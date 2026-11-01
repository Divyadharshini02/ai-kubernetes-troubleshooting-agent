from app.collectors.base import CollectorContext, collect_list, conditions_summary, utc_now
from app.collectors.redaction import Redactor
from app.models.evidence import EvidenceRecord


def _node_record(node, source: str) -> EvidenceRecord:
    redactor = Redactor()
    status, spec = node.status, node.spec
    node_info = status.node_info if status else None
    data = {
        "unschedulable": bool(spec.unschedulable) if spec else False,
        "taints": [
            {"key": taint.key, "value": taint.value, "effect": taint.effect}
            for taint in (spec.taints if spec else None) or []
        ],
        "labels": node.metadata.labels or {},
        "capacity": (status.capacity or {}) if status else {},
        "allocatable": (status.allocatable or {}) if status else {},
        "conditions": conditions_summary(status.conditions if status else None, redactor),
        "kubelet_version": node_info.kubelet_version if node_info else None,
    }
    return EvidenceRecord(
        evidence_id=f"node:{node.metadata.name}",
        category="node",
        resource_kind="Node",
        resource_name=node.metadata.name,
        collected_at=utc_now(),
        source=source,
        redactions=redactor.count,
        data=data,
    )


def collect_nodes(ctx: CollectorContext) -> list[EvidenceRecord]:
    source = "GET /api/v1/nodes"
    return collect_list(
        ctx,
        collector="nodes",
        category="node",
        kind="Node",
        source=source,
        namespace=None,
        fetch=lambda **kwargs: ctx.core.list_node(**kwargs),
        to_records=lambda node: [_node_record(node, source)],
    )
