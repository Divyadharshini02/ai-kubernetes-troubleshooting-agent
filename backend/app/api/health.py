from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Report that the API process is serving requests.

    This does not check Kubernetes or any other dependency.
    """
    return HealthResponse(
        status="ok",
        service="ai-kubernetes-troubleshooting-agent",
        version=__version__,
    )
