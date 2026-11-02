# Milestone 7: Kubernetes RBAC and Security Hardening

**Goal:** run the backend with a dedicated read-only identity, enforced by the cluster, instead of an administrator's credentials.

**Acceptance criterion:** tests pass and unauthorized write operations are denied.

**Status:** completed.

## The design in short

```text
Backend  ->  KUBECONFIG  ->  ~/.kube/troubleshooting-agent.kubeconfig
                             ServiceAccount token, valid for 8 hours
                             Can get and list what the collectors use. Nothing else.

Humans and test harnesses  ->  ~/.kube/config  ->  administrator
```

Before this milestone, the code only sent read requests, but nothing enforced that. Now the Kubernetes API server refuses anything else, whatever the application tries to do.

| Part | File |
|---|---|
| ServiceAccount, ClusterRole, ClusterRoleBinding | [k8s/rbac/troubleshooting-agent.yaml](../k8s/rbac/troubleshooting-agent.yaml) |
| Creates and renews the restricted kubeconfig | [scripts/new_readonly_kubeconfig.py](../scripts/new_readonly_kubeconfig.py) |
| Chooses the kubeconfig, with no silent fallback | [app/kubernetes/cluster.py](../backend/app/kubernetes/cluster.py) |

## Set up

Run these once, from the repository root, as the administrator:

```powershell
kubectl --context kind-troubleshooting-lab apply -f k8s/rbac/troubleshooting-agent.yaml
backend\.venv\Scripts\python.exe scripts\new_readonly_kubeconfig.py
```

The script writes `%USERPROFILE%\.kube\troubleshooting-agent.kubeconfig`. Then start the backend from `backend/`:

```powershell
$env:KUBECONFIG = "$env:USERPROFILE\.kube\troubleshooting-agent.kubeconfig"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Setting `KUBECONFIG` in a window also changes what `kubectl` does in that window. To run `kubectl` as the administrator, use another window.

## Renew the token

The token lasts 8 hours. When it expires, the dashboard reports that the API rejected the credentials, and investigations are saved as failed with `cluster_access_denied`. To renew it:

```powershell
backend\.venv\Scripts\python.exe scripts\new_readonly_kubeconfig.py
```

No backend restart is needed, because the kubeconfig is read on every request. If renewal fails, the existing file is left as it was. The backend in Docker uses its own copy, created with `--container`, as described in [Milestone 8](M8_DOCKER_JENKINS_TRIVY.md#run-the-application).

## What the script guarantees

- **Validates first:** the context and the ServiceAccount and binding must exist, or it stops before requesting a token.
- **Copies no administrator credentials:** only the server address and public CA are taken from the administrator's kubeconfig.
- **Never prints the token:** error messages are fixed text, never command output.
- **Stays outside the repository:** it refuses to write inside the repository or over an administrator kubeconfig. Git also ignores `*.kubeconfig`.
- **Proves the result:** before saving, it uses the new token to confirm it can list pods and cannot read Secrets or delete pods.

Kubernetes issues the token on request, and no long-lived token Secret is created. The file is only as private as your Windows user profile, so treat it like a password until it expires.

## Credentials are never chosen silently

The backend used to fall back to the default kubeconfig, which on this machine is an administrator's. That no longer happens.

| Situation | Result |
|---|---|
| `KUBECONFIG` points at a working restricted kubeconfig | Works |
| `KUBECONFIG` not set | HTTP 503 `kubeconfig_unavailable`, with a message saying what to do |
| `KUBECONFIG` names the default `~/.kube/config` | The same, refused |
| File missing or damaged | HTTP 503 `kubeconfig_unavailable`, and its contents are never quoted |
| Token expired or invalid | HTTP 502 `cluster_access_denied` |
| ServiceAccount has no permissions | HTTP 502 `cluster_access_denied` from cluster health and investigations |

`GET /api/health` and saved history keep working in all of these. Investigations started in these states are saved as failed, with the reason.

### Local development without the restricted identity

For local development only, you can explicitly allow the default kubeconfig:

```powershell
$env:KUBERNETES_ALLOW_DEFAULT_KUBECONFIG = "true"
```

Only the exact value `true` works. The backend logs a warning at startup when this is on. Do not use it for demonstrations of this milestone.

## Why these permissions

Every permission comes from a call in `app/collectors` or `app/kubernetes`.

| Resource | Verb | Why |
|---|---|---|
| `pods` | `list` | Collecting pods in a namespace, and the cluster overview across all namespaces |
| `pods/log` | `get` | Logs of unhealthy containers |
| `events` | `list` | Probe failures, scheduling failures, image pull errors |
| `deployments`, `replicasets` | `list` | Rollout and ownership evidence |
| `services`, `endpointslices` | `list` | Service selector diagnosis |
| `persistentvolumeclaims` | `list` | Pending pods waiting on storage |
| `serviceaccounts` | `list` | Image pull secret names, for image pull diagnosis |
| `namespaces` | `get` | Telling a missing namespace from an existing one |
| `nodes`, `storageclasses` | `list` | Scheduling diagnosis |

`get /version` needs nothing extra, because Kubernetes allows it for every authenticated user by default.

**Denied, because nothing grants it:** every write, Secrets, ConfigMaps, `pods/exec`, `pods/attach`, `pods/portforward`, `nodes/proxy`, creating tokens, impersonation, changing RBAC, and even `watch` and `get` on pods, which the code does not use. RBAC has no "deny" rule: anything not granted is refused, so keeping this list short is the protection. The tests prove these are refused.

## Why cluster-wide and not namespace-scoped

A `Role` and `RoleBinding` would be stronger isolation, but they would not work here:

- **Nodes and StorageClasses belong to no namespace.** A `Role` lives inside one namespace, so it cannot grant them. Only a `ClusterRole` with a `ClusterRoleBinding` can.
- **Denying them is not an option.** The scheduling rule needs them. Without them, evidence is partial, so results become `insufficient_evidence`, and a healthy namespace could never show `no_problems_found` or `recovered`.
- **The cluster overview lists pods in all namespaces**, and every investigation's connectivity check uses the same call.
- **Investigating any namespace is the core feature**, and `failure-demo` and `evidence-lab` are created and deleted repeatedly, which would remove their bindings each time.

So this design enforces **"cannot change anything"**, not "cannot see other namespaces".

## Three identities, two credentials

| Identity | Credential | Can do |
|---|---|---|
| Backend | The restricted kubeconfig | Read, as listed above |
| Human administrator | `~/.kube/config` | Anything |
| Test harnesses | The administrator's, named in `TEST_ADMIN_KUBECONFIG` | Writes for scenarios and labs |

There is no separate identity for test harnesses, on purpose. Creating namespaces cannot be limited by name in RBAC, and writing to namespaces that come and go would need cluster-wide write access. That would be a powerful standing credential, which is worse than a tool a person runs. Instead, [lab_harness.py](../backend/tests/lab_harness.py) refuses to run without `TEST_ADMIN_KUBECONFIG`, refuses if it is the same file as the backend's `KUBECONFIG`, and keeps its existing context and ownership label checks.

## Security limitations

- **Reads everywhere:** the backend can read pod specs and logs in every namespace, including `kube-system`.
- **`list` returns whole objects:** pod specs can include environment values set directly in a manifest. The collectors strip these, but the identity can technically read them.
- **Logs can hold secrets:** an application can write a secret to its logs. Redaction is best effort.
- **The application cannot tell which identity a file holds:** it refuses the default file, but a copy of an administrator's kubeconfig at another path would work. Nothing checks this.
- **The explicit development setting removes this protection**, so it logs a warning.
- **A token cannot be revoked individually:** it lasts until it expires, or until the ServiceAccount is deleted.
- **No login:** the API has no authentication and is for local use only. Anyone who can reach it can use the read access.
- **Local only:** this has been tested on the kind cluster, not a production cluster.

## Clean up

To remove everything this milestone created in the cluster:

```powershell
kubectl --context kind-troubleshooting-lab delete -f k8s/rbac/troubleshooting-agent.yaml
Remove-Item "$env:USERPROFILE\.kube\troubleshooting-agent.kubeconfig"
```

The backend then reports that its credentials do not work. Deleting the ServiceAccount also invalidates any token it issued.

## Interview walkthrough

Run these from the repository root, in a window where `KUBECONFIG` is not set.

1. **Show the problem.** The administrator can do anything:
   ```powershell
   kubectl auth can-i '*' '*' --all-namespaces
   ```
2. **Show the identity:**
   ```powershell
   $restricted = "$env:USERPROFILE\.kube\troubleshooting-agent.kubeconfig"
   kubectl --kubeconfig $restricted auth whoami
   ```
3. **Prove the limits:**
   ```powershell
   kubectl --kubeconfig $restricted auth can-i list pods --all-namespaces   # yes
   kubectl --kubeconfig $restricted auth can-i delete pods -n sample-app    # no
   kubectl --kubeconfig $restricted auth can-i list secrets -n sample-app   # no
   kubectl --kubeconfig $restricted auth can-i create pods/exec -n sample-app  # no
   ```
4. **Show a real denial**, which changes nothing because it is a server-side dry run:
   ```powershell
   kubectl --kubeconfig $restricted delete deployment sample-web -n sample-app --dry-run=server
   ```
   This must return `Forbidden`.
5. **Show the application works anyway:** run the [Milestone 6 demonstration](M6_FAILURE_SCENARIOS.md#interview-walkthrough) in the dashboard.
6. **Show failing safely:** start a backend without `KUBECONFIG`. The dashboard explains what is missing, and no administrator credentials are used.

**Questions you may be asked:**

- **"Why not `cluster-admin`, since it is only local?"** The application only needs to read. If it had a bug, or a prompt injection influenced it, the API server would still refuse any change.
- **"Why cluster-wide?"** Nodes and StorageClasses cannot be granted any other way, and the tool investigates any namespace. It enforces "cannot change anything", and I would narrow it further for a production tool that only serves certain namespaces.
- **"Why not the built-in `view` role?"** It grants resources this application never reads. A custom role lists only what the code uses, and a test checks that they match.
- **"Why an 8-hour token?"** A leaked token stops working by itself. Long-lived token Secrets do not expire.
- **"What is still weak?"** Read access is broad, and there is no login on the API.

## Run the tests

From `backend/`:

```powershell
# No cluster needed: the manifest, credential rules and script
.\.venv\Scripts\python.exe -m pytest tests\test_rbac_manifest.py

# Real cluster. The backend is restricted, and test harnesses write as the administrator
$env:TEST_ADMIN_KUBECONFIG = "$env:USERPROFILE\.kube\config"
$env:KUBECONFIG = "$env:USERPROFILE\.kube\troubleshooting-agent.kubeconfig"
.\.venv\Scripts\python.exe -m pytest tests\test_rbac_live.py -m cluster
.\.venv\Scripts\python.exe -m pytest -m cluster
```

Cluster tests fail with a clear message, instead of skipping, if either setting is missing. Nothing is destroyed to prove a denial: permissions are checked with `kubectl auth can-i`, and writes attempted with the real token use a server-side dry run.

## Final verification results

Recorded on 2026-11-02, on the local kind cluster, with no real provider calls.

| Check | Result |
|---|---|
| Non-cluster suite, with no credentials set | 952 passed, 126 deselected |
| Real-cluster suite, with the backend restricted | 126 passed, 952 deselected |
| Live permission tests, included above | 70 passed: 13 allowed, 37 denied, 7 real API denials, and the rest application and failure tests |
| `npm run lint` | No problems |
| `npm run build` | Succeeded |
| Token in the database or backend log | Not found |

**Browser run:** real headless Chrome, driven by a script, against the real backend.

| Scenario | Result |
|---|---|
| Backend started with no credentials | The dashboard and a saved failed investigation both explained what was missing, and `/api/health` kept working |
| Full failure and recovery demonstration, restricted | Not Verified, Not Verified, Still Failing, Recovered, with no page errors |

The first two attempts at the restricted demonstration stopped at the healthy baseline. A normal start was recording enough readiness probe failures to make the pod look unstable, which had nothing to do with RBAC. It was fixed in the Milestone 6 manifests, as described in [why the readiness probe waits](M6_FAILURE_SCENARIOS.md#why-the-readiness-probe-waits), and the same flow then passed.
