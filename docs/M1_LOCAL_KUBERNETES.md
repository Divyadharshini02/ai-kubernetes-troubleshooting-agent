# Milestone 1: Local Kubernetes Connectivity

**Goal:** run a real local Kubernetes cluster and have the backend return real information from it.

**Acceptance criterion:** the backend returns real Kubernetes resources.

**Status:** all checks below passed on 2026-11-01. No troubleshooting or diagnosis exists yet.

## What was built

| Item | Location |
|---|---|
| Cluster definition, pinned by image digest | [k8s/kind-config.yaml](../k8s/kind-config.yaml) |
| Healthy sample workload: namespace, Deployment and Service | [k8s/base/sample-workload.yaml](../k8s/base/sample-workload.yaml) |
| Cluster access and error handling | [backend/app/kubernetes/cluster.py](../backend/app/kubernetes/cluster.py) |
| Response schema | [backend/app/models/cluster.py](../backend/app/models/cluster.py) |
| `GET /api/cluster/health` | [backend/app/api/cluster.py](../backend/app/api/cluster.py) |
| Tests without a cluster | [backend/tests/test_cluster_health.py](../backend/tests/test_cluster_health.py) |
| Tests against the real cluster | [backend/tests/test_cluster_live.py](../backend/tests/test_cluster_live.py) |

## Set up the cluster

These steps assume cgroup v2 is active. See [ENVIRONMENT.md](ENVIRONMENT.md) for the WSL setting and how to verify it.

```powershell
winget install --id Kubernetes.kind --version 0.33.0 --exact
# Open a new terminal so kind is on PATH, then from the repository root:
kind create cluster --config k8s/kind-config.yaml --retain --wait 5m
kubectl apply -f k8s/base/sample-workload.yaml
kubectl rollout status deployment/sample-web -n sample-app --timeout=180s
```

`--retain` keeps a failed node so its logs can be read. Creation took 213 seconds here, including the image download.

## The endpoint

`GET /api/cluster/health` reads the cluster selected by the current kubeconfig context, or by the `KUBECONFIG` environment variable if it is set.

It reports what the Kubernetes API returned: server version, nodes and pods. It does not decide whether a workload is healthy, because diagnosis belongs to later milestones.

| Situation | HTTP status | `detail.code` |
|---|---|---|
| Connected | 200 | Not applicable |
| Kubeconfig missing or invalid | 503 | `kubeconfig_unavailable` |
| API server unreachable, timed out, or the TLS connection failed | 503 | `cluster_unreachable` |
| API rejected the credentials or permissions | 502 | `cluster_access_denied` |
| API returned an error or an unexpected response | 502 | `cluster_api_error` |

Other behaviour:

- **No partial success:** if any request fails, the whole response is an error.
- **Unknown stays unknown:** a node with no `Ready` condition reports `ready: null`, not `false`.
- **Bounded:** each list returns at most 500 items, and `truncated` is `true` if more exist.
- **Safe errors:** responses and logs never include raw exception text, which can contain request details.

## Run the tests

From `backend/`:

```powershell
# No cluster needed
.\.venv\Scripts\python.exe -m pytest

# Needs the real cluster and the sample workload; fails if the cluster is unavailable
.\.venv\Scripts\python.exe -m pytest -m cluster
```

The first command uses real failures where it can: a missing file, a damaged file, a closed port and a silent socket. A local stub HTTP server stands in for the Kubernetes API only where a test needs a specific response. The second command uses no test doubles and compares the endpoint against `kubectl`.

## Acceptance evidence

| Check | Result |
|---|---|
| Node `Ready`, all system pods `Running` with 0 restarts | Passed |
| Sample workload serves HTTP through its Service | Passed: returned the nginx welcome page |
| Tests without a cluster | 23 passed |
| Tests against the real cluster | 4 passed |
| Real-cluster tests without a usable kubeconfig | 4 errors, exit code 1, none skipped |
| Endpoint follows real changes | Sample pods went 1, 2, 1 when scaled with kubectl; total pods went 10, 11, 10 |
| Endpoint through the frontend proxy | HTTP 200 |
| Missing kubeconfig over real HTTP | HTTP 503 `kubeconfig_unavailable` |
| Unreachable API server over real HTTP | HTTP 503 `cluster_unreachable`, about 2 seconds |
| Frontend page in headless Chrome | Backend status displays `ok` |
| Other projects' containers, images, volumes and networks | Unchanged; only kind's own additions are new |

## Limitations

- **Admin credentials:** the backend uses kind's default kubeconfig, which can do anything in the cluster (`kubectl auth can-i '*' '*'` returns `yes`). The code only sends read requests, but nothing in the cluster enforces that yet. The dedicated read-only ServiceAccount is required by Milestone 7.
- **No UI yet:** the React page shows backend health only. It does not call the cluster endpoint.
- **Single page of results:** lists are not paginated past the 500-item limit.

See [ENVIRONMENT.md](ENVIRONMENT.md) for environment limitations.
