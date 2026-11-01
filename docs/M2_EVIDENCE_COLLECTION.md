# Milestone 2: Kubernetes Evidence Collection

**Goal:** collect real, attributable evidence from the cluster for the five supported troubleshooting scenarios.

**Acceptance criterion:** evidence includes resource identity, timestamps, source and collection status.

**Status:** complete. This milestone reports evidence only. It does not diagnose anything, call an LLM or change the cluster. Diagnosis is added by [Milestone 3](M3_DIAGNOSTICS.md).

## The endpoint

`GET /api/namespaces/{namespace}/evidence` collects evidence for one namespace, plus the cluster-wide nodes and StorageClasses that affect it.

| Situation | HTTP status | Result |
|---|---|---|
| Collection ran | 200 | An evidence bundle, with `completeness` of `complete` or `partial` |
| Namespace does not exist | 404 | `namespace_not_found` |
| Invalid namespace name | 422 | Rejected before any cluster call |
| Kubeconfig missing or invalid | 503 | `kubeconfig_unavailable` |
| API server unreachable, or the TLS connection failed | 503 | `cluster_unreachable` |

A bundle is `partial` if any record has the status `forbidden`, `failed` or `skipped`.

## Evidence records

Every record contains:

| Field | Meaning |
|---|---|
| `evidence_id` | Identifier that is unique within the bundle, such as `container:demo/web/app` |
| `category`, `resource_kind`, `resource_name`, `namespace` | What the evidence is about |
| `collected_at` | When the application collected it |
| `source` | The API request it came from |
| `temporality` | `current` or `historical` (see below) |
| `observed_at` | When a historical record last happened, if the cluster reported it |
| `status` | The collection outcome (see below) |
| `truncated`, `redactions` | Whether a limit cut the data, and how many secrets were masked |
| `data` | The structured observation |
| `error` | A safe code and message when collection did not succeed |

### Current and historical evidence

| `temporality` | Records | Meaning |
|---|---|---|
| `current` | Pods, containers, current logs, Deployments, ReplicaSets, Services, EndpointSlices, nodes, claims, StorageClasses, ServiceAccounts | State when collected |
| `historical` | Events, last container termination, previous logs | Something that already happened and may no longer be true |

A container's current state and its last termination are separate records. Events include `age_seconds`, so an old warning can be told apart from a recent one.

### Collection status

| `status` | Meaning |
|---|---|
| `collected` | Data was read |
| `empty` | The request worked and found nothing |
| `not_found` | The API says the resource does not exist |
| `not_available` | The API has no such data, such as previous logs that were already removed |
| `forbidden` | The API denied access |
| `failed` | The request failed or the response was unusable |
| `skipped` | A collection limit was reached |

An empty result and a failed request always produce different records.

## Evidence for each scenario

| Scenario | Evidence collected |
|---|---|
| CrashLoopBackOff | Container state, restart count, last termination with exit code and reason, current and previous logs, events, environment variable names and references, probes, resource limits |
| ImagePullBackOff | Image registry, repository, tag and digest, pull policy, waiting reason, pull events, pull secret names on the pod and ServiceAccount |
| Pending pods | `PodScheduled` condition, scheduler events, node readiness, capacity and taints, resource requests, node selector, affinity, tolerations, claim phase, StorageClasses |
| Service selector mismatch | Service selector, ports and target ports, pod labels, EndpointSlice addresses and readiness, Deployment and ReplicaSet status |
| Probe failures | Readiness, liveness and startup probe configuration, container state, readiness, restart count, probe events |

This milestone collects the evidence. [Milestone 3](M3_DIAGNOSTICS.md) decides which scenario applies.

## Safety

**Read-only:** collectors send only GET requests. A test runs a full collection against a stub API and asserts every request was a GET.

**Secrets:**

- The Secrets API is never called. A test asserts this.
- Fields are copied from an allowlist instead of dumping whole objects.
- Literal environment variable values, container commands and arguments, probe header values and annotations are never collected. The one exception is the Deployment revision annotation, a plain number.
- Secret references report the Secret name and key only.
- Logs, event messages, status messages and probe commands pass through pattern-based redaction. This is best effort, so it can miss a secret in an unusual format.

**Limits:**

| Limit | Value |
|---|---|
| Items per list request | 500 |
| Lines per log stream | 200 |
| Bytes per log stream | 32 KiB |
| Log streams per collection | 20 |
| API request timeouts | 3 seconds to connect, 10 seconds to read |
| New work stops after | 45 seconds |

Reaching a limit produces a `skipped` record or sets `truncated`.

**Logs are untrusted:** log text is stored as plain data. Nothing in this milestone acts on it.

## Which logs are collected

Logs are only read for containers that restarted, are waiting, failed, or are running but not ready. Healthy containers are not read, and the `logs:selection` record reports how many containers were seen and selected.

### Kubelet messages returned as logs

Testing on Kubernetes 1.37 found that the log endpoint can answer **HTTP 200** with a message instead of logs:

```text
unable to retrieve container logs for containerd://...
```

`kubectl logs --previous` receives the same message. It happens after the kubelet removes a dead container, even when `lastState` still refers to it. In the observed crash loop only the latest attempt was kept, so the crash output was in the current stream and the previous stream had nothing to return.

Logs are requested with timestamps, so a body that does not start with one is not application output. It is recorded as `not_available`, with the text in `data.api_message` and never in `data.text`.

Previous logs being unavailable is normal for a crash loop. It is not evidence of anything, and crash output may be in either stream.

## Run the tests

From `backend/`:

```powershell
# No cluster needed: collectors run through the real client against a local stub API
.\.venv\Scripts\python.exe -m pytest

# Needs the kind cluster and the sample workload
.\.venv\Scripts\python.exe -m pytest -m cluster
```

The cluster tests create real failures from [backend/tests/fixtures/evidence-lab.yaml](../backend/tests/fixtures/evidence-lab.yaml) in a throwaway `evidence-lab` namespace. [backend/tests/lab_harness.py](../backend/tests/lab_harness.py) creates and removes it using kubectl. The application itself only reads.

### Test lab cleanup

| Event | What happens to the lab |
|---|---|
| Tests pass or fail | Removed, and removal is verified |
| `kubectl apply` fails partway | Removed |
| Ctrl+C | Removed |
| Process killed, power loss | Cannot be removed at that moment; the next cluster test run removes it first |
| Removal fails | The test run reports an error with the command to run |

Two guards run before the harness writes anything:

- The current kubeconfig context must be `kind-troubleshooting-lab`.
- An existing `evidence-lab` namespace is only removed if it has the label `app.kubernetes.io/managed-by=evidence-lab-tests`.

To remove a leftover lab by hand:

```powershell
kubectl --context kind-troubleshooting-lab delete namespace evidence-lab
```

## Acceptance evidence

Results from 2026-11-01:

| Check | Result |
|---|---|
| Tests without a cluster | 247 passed |
| Tests against the real cluster | 18 passed |
| Deliberate defects introduced in a scratch copy | 15 of 15 caught by the tests |
| Evidence from real failures over HTTP | HTTP 200, 59 records, `complete`, about 0.5 seconds |
| Record statuses in that bundle | 57 `collected`, 2 `not_available` |
| Lab secrets in the response | Absent: the log secret and the literal environment value |
| Healthy `sample-app` namespace | `complete`, no logs selected |
| Missing namespace, invalid name | HTTP 404, HTTP 422 |
| Write calls and Secret API calls in `backend/app` | 0 and 0 |

## Limitations

- **Admin credentials:** the backend still uses kind's default kubeconfig. The code only reads, but nothing in the cluster enforces that until the read-only ServiceAccount in Milestone 7. `forbidden` handling has only been tested against a stub API, not real RBAC.
- **No live application checks:** probes are reported from configuration and events. The application is not called directly, because that needs pod exec or the API proxy.
- **No direct registry check:** registry access evidence comes from image pull events.
- **Node requests are not totalled:** pods in other namespaces are not read, so per-node usage is not calculated. The scheduler's events report insufficient resources.
- **PersistentVolumes are not read:** binding evidence comes from claims, StorageClasses and events.
- **Namespace events only:** events recorded on nodes in the `default` namespace are not collected for other namespaces.
- **Raw facts only:** collectors report selectors and labels without comparing them. [Milestone 3](M3_DIAGNOSTICS.md) does the comparing.
- **Event retention:** Kubernetes deletes old events, one hour by default, so a missing event does not prove nothing happened.
