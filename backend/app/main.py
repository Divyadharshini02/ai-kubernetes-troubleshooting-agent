from fastapi import FastAPI

from app import __version__
from app.api import cluster, health

app = FastAPI(title="AI Kubernetes Troubleshooting Agent", version=__version__)
app.include_router(health.router, prefix="/api")
app.include_router(cluster.router, prefix="/api")
