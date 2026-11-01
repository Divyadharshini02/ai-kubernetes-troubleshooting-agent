from app.collectors.base import COLLECTION_ERRORS, CollectorContext, describe_failure, utc_now
from app.kubernetes import cluster
from app.models.evidence import EvidenceRecord


def collect_namespace(ctx: CollectorContext) -> EvidenceRecord:
    fields = {
        "evidence_id": f"namespace:{ctx.namespace}",
        "category": "namespace",
        "resource_kind": "Namespace",
        "resource_name": ctx.namespace,
        "source": f"GET /api/v1/namespaces/{ctx.namespace}",
    }
    try:
        namespace = ctx.core.read_namespace(
            ctx.namespace, _request_timeout=cluster.REQUEST_TIMEOUT_SECONDS
        )
    except COLLECTION_ERRORS as exc:
        status, error = describe_failure(exc)
        return EvidenceRecord(collected_at=utc_now(), status=status, error=error, **fields)
    return EvidenceRecord(
        collected_at=utc_now(),
        data={
            "phase": namespace.status.phase if namespace.status else None,
            "labels": namespace.metadata.labels or {},
        },
        **fields,
    )
