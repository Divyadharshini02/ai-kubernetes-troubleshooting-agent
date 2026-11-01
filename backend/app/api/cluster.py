from fastapi import APIRouter, HTTPException

from app.kubernetes.cluster import ClusterAccessError, read_cluster_health
from app.models.cluster import ClusterHealth

router = APIRouter(prefix="/cluster", tags=["cluster"])

# The API answered with an error: 502. No usable connection was made: 503.
STATUS_BY_CODE = {
    "kubeconfig_unavailable": 503,
    "cluster_unreachable": 503,
    "cluster_access_denied": 502,
    "cluster_api_error": 502,
}


@router.get("/health", response_model=ClusterHealth)
def get_cluster_health() -> ClusterHealth:
    """Return live node and pod information from the current kubeconfig context.

    This reports what the cluster says. It does not diagnose workloads.
    """
    try:
        return read_cluster_health()
    except ClusterAccessError as exc:
        raise HTTPException(
            status_code=STATUS_BY_CODE[exc.code],
            detail={"code": exc.code, "message": exc.message},
        ) from exc
