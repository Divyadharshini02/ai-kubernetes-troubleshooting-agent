# AI Kubernetes Troubleshooting Agent

Evidence-based Kubernetes incident investigation and recovery assistant. The full requirements are in [docs/MASTER_PROJECT_REQUIREMENTS.md](docs/MASTER_PROJECT_REQUIREMENTS.md).

## Current status

**Milestone 0 (foundation) only.** A FastAPI backend with a health endpoint and a React page that displays it.

The application does not investigate clusters yet. Kubernetes connectivity, evidence collection, diagnostics, AI, Jenkins and monitoring are planned for later milestones.

## Prerequisites

- Python 3.13
- Node.js 22.12 or newer
- Git

Environment findings for this project's development machine are in [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md).

## Run the backend

From the repository root, in PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Check it at <http://127.0.0.1:8000/api/health>. Interactive API docs are at <http://127.0.0.1:8000/docs>.

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
# From backend/
.\.venv\Scripts\python.exe -m pytest

# From frontend/
npm run lint
npm run build
```
