from typing import Annotated

from fastapi import APIRouter, HTTPException, Path

from app.api.cluster import STATUS_BY_CODE
from app.kubernetes.cluster import ClusterAccessError
from app.models.evidence import EvidenceBundle
from app.services.evidence import NamespaceNotFoundError, collect_namespace_evidence

router = APIRouter(tags=["evidence"])

NamespaceName = Annotated[str, Path(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", max_length=63)]


@router.get("/namespaces/{namespace}/evidence", response_model=EvidenceBundle)
def get_namespace_evidence(namespace: NamespaceName) -> EvidenceBundle:
    """Collect live evidence for one namespace.

    This reports evidence only. It does not diagnose anything or change the cluster.
    """
    return collect_or_http_error(namespace)


def collect_or_http_error(namespace: str) -> EvidenceBundle:
    try:
        return collect_namespace_evidence(namespace)
    except NamespaceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "namespace_not_found",
                "message": "The Kubernetes API reports that this namespace does not exist.",
            },
        ) from exc
    except ClusterAccessError as exc:
        raise HTTPException(
            status_code=STATUS_BY_CODE[exc.code],
            detail={"code": exc.code, "message": exc.message},
        ) from exc
