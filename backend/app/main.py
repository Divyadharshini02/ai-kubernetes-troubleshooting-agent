import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api import cluster, diagnosis, evidence, health, investigations
from app.kubernetes.cluster import ALLOW_DEFAULT_KUBECONFIG, default_kubeconfig_allowed
from app.storage.investigations import StorageError, mark_interrupted

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if default_kubeconfig_allowed():
        logger.warning("%s is on, so the backend may be using administrator credentials", ALLOW_DEFAULT_KUBECONFIG)
    try:
        interrupted = mark_interrupted()
        if interrupted:
            logger.warning("Marked %s interrupted investigations as failed", interrupted)
    # History is optional for everything else, so this must not stop the backend.
    except StorageError as exc:
        logger.warning("Could not read saved investigations: %s", exc)
    yield


app = FastAPI(title="AI Kubernetes Troubleshooting Agent", version=__version__, lifespan=lifespan)
app.include_router(health.router, prefix="/api")
app.include_router(cluster.router, prefix="/api")
app.include_router(evidence.router, prefix="/api")
app.include_router(diagnosis.router, prefix="/api")
app.include_router(investigations.router, prefix="/api")


@app.exception_handler(StorageError)
def storage_error_response(_request: Request, _exc: StorageError) -> JSONResponse:
    detail = {"code": "storage_unavailable", "message": "The investigation history could not be read or saved."}
    return JSONResponse(status_code=503, content={"detail": detail})
