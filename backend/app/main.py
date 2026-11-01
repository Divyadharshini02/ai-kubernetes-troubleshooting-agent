from fastapi import FastAPI

from app import __version__
from app.api import health

app = FastAPI(title="AI Kubernetes Troubleshooting Agent", version=__version__)
app.include_router(health.router, prefix="/api")
