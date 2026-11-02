# AI Kubernetes Troubleshooting Agent

Evidence-based Kubernetes incident investigation and recovery assistant. The full requirements are in [docs/MASTER_PROJECT_REQUIREMENTS.md](docs/MASTER_PROJECT_REQUIREMENTS.md).

## Current status

| Milestone | Status |
|---|---|
| M0: Environment and foundation | Done |
| [M1: Local Kubernetes connectivity](docs/M1_LOCAL_KUBERNETES.md) | Done |
| [M2: Evidence collection](docs/M2_EVIDENCE_COLLECTION.md) | Done |
| [M3: Deterministic diagnostics](docs/M3_DIAGNOSTICS.md) | Done |
| [M4: Optional LLM integration](docs/M4_LLM_INTEGRATION.md) | Accepted with documented limitations, not yet run against the real provider |
| [M5: React dashboard](docs/M5_REACT_DASHBOARD.md) | Done |
| [M6: Failure scenarios and recovery verification](docs/M6_FAILURE_SCENARIOS.md) | Done |
| [M7: Kubernetes RBAC and security](docs/M7_RBAC_SECURITY.md) | Done |
| [M8: Docker, Jenkins CI/CD and Trivy](docs/M8_DOCKER_JENKINS_TRIVY.md) | M8 completed |
| M9 to M10 | Not started |

**What works today:** a React dashboard investigates a namespace in a real local kind cluster. It shows live progress, a diagnosis of five failure scenarios from deterministic rules, the read-only evidence behind it, and saved history that survives restarts. Reproducible failures in [k8s/scenarios](k8s/scenarios) demonstrate the full lifecycle, and a fresh investigation after a manual fix shows whether earlier problems are still detected. An optional LLM can explain those diagnoses in clearer language, and the project works fully without it.

The backend uses a dedicated read-only ServiceAccount, so the cluster itself refuses any change. The application suggests fixes but never applies them.

The application also runs in Docker, and a local Jenkins pipeline tests, builds and scans it with Trivy. Nothing is published.

**What does not exist yet:** monitoring is planned for a later milestone.

## Prerequisites

- Python 3.13
- Node.js 22.12 or newer
- Git
- Docker Desktop with cgroup v2, and kind 0.33.0 (only for the cluster features)

Environment findings for this project's development machine are in [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md).

## Set up the cluster

Follow [docs/M1_LOCAL_KUBERNETES.md](docs/M1_LOCAL_KUBERNETES.md#set-up-the-cluster). The backend and frontend still start without a cluster; the cluster endpoint then returns HTTP 503.

## Run the backend

From the repository root, in PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:KUBECONFIG = "$env:USERPROFILE\.kube\troubleshooting-agent.kubeconfig"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

`KUBECONFIG` must point at the restricted kubeconfig. Create it once, and renew it every 8 hours, by following [docs/M7_RBAC_SECURITY.md](docs/M7_RBAC_SECURITY.md#set-up). Without it, the backend still starts, but its cluster features explain what is missing. It never falls back to administrator credentials unless you [explicitly allow that](docs/M7_RBAC_SECURITY.md#local-development-without-the-restricted-identity).

| URL | Purpose |
|---|---|
| <http://127.0.0.1:8000/api/health> | Backend health |
| <http://127.0.0.1:8000/api/cluster/health> | Live nodes and pods from the current kubeconfig context |
| <http://127.0.0.1:8000/api/namespaces/sample-app/evidence> | Live evidence for one namespace |
| <http://127.0.0.1:8000/api/namespaces/sample-app/diagnosis> | Deterministic diagnosis of one namespace |
| <http://127.0.0.1:8000/api/namespaces/sample-app/diagnosis?explain=true> | The same, with an optional LLM explanation |
| <http://127.0.0.1:8000/api/investigations> | Saved investigations |
| <http://127.0.0.1:8000/docs> | Interactive API docs |

Saved investigations are kept in `backend/data/investigations.db`, which Git ignores.

No API key is needed. To turn on the optional LLM explanations, follow [docs/M4_LLM_INTEGRATION.md](docs/M4_LLM_INTEGRATION.md#set-up).

## Run the frontend

In a second PowerShell window, from the repository root:

```powershell
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>, enter a namespace such as `sample-app`, and click **Investigate Cluster**. The page shows real results from the backend, or an error if the backend or cluster is not available. See [docs/M5_REACT_DASHBOARD.md](docs/M5_REACT_DASHBOARD.md).

## Run it in Docker

To run the application in containers, at <http://localhost:8088>, or the Jenkins pipeline, at <http://localhost:8090>, follow [docs/M8_DOCKER_JENKINS_TRIVY.md](docs/M8_DOCKER_JENKINS_TRIVY.md).

## Run the checks

```powershell
# From backend/: lint, and tests that need no cluster
.\.venv\Scripts\ruff.exe check app tests ..\scripts
.\.venv\Scripts\python.exe -m pytest

# From backend/: tests against the real kind cluster, with the backend restricted
$env:TEST_ADMIN_KUBECONFIG = "$env:USERPROFILE\.kube\config"
$env:KUBECONFIG = "$env:USERPROFILE\.kube\troubleshooting-agent.kubeconfig"
.\.venv\Scripts\python.exe -m pytest -m cluster

# From frontend/
npm run lint
npm run build
```
