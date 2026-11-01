# AI Kubernetes Troubleshooting Agent

Evidence-based Kubernetes incident investigation and recovery assistant. The full requirements are in [docs/MASTER_PROJECT_REQUIREMENTS.md](docs/MASTER_PROJECT_REQUIREMENTS.md).

## Current status

| Milestone | Status |
|---|---|
| M0: Environment and foundation | Done |
| [M1: Local Kubernetes connectivity](docs/M1_LOCAL_KUBERNETES.md) | Done |
| M2 to M10 | Not started |

**What works today:** a FastAPI backend that reads real nodes and pods from a local kind cluster, and a React page that shows backend health.

**What does not exist yet:** the application does not investigate or diagnose anything. Evidence collection, diagnostics, AI, Jenkins and monitoring are planned for later milestones.

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
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

| URL | Purpose |
|---|---|
| <http://127.0.0.1:8000/api/health> | Backend health |
| <http://127.0.0.1:8000/api/cluster/health> | Live nodes and pods from the current kubeconfig context |
| <http://127.0.0.1:8000/docs> | Interactive API docs |

## Run the frontend

In a second PowerShell window, from the repository root:

```powershell
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>. The page shows the live result from the backend, or an error if the backend is not running.

## Run the checks

```powershell
# From backend/: tests that need no cluster
.\.venv\Scripts\python.exe -m pytest

# From backend/: tests against the real kind cluster
.\.venv\Scripts\python.exe -m pytest -m cluster

# From frontend/
npm run lint
npm run build
```
