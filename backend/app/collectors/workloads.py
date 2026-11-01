from app.collectors.base import (
    CollectorContext,
    collect_list,
    conditions_summary,
    owner_references,
    utc_now,
)
from app.collectors.redaction import Redactor
from app.models.evidence import EvidenceRecord

REVISION_ANNOTATION = "deployment.kubernetes.io/revision"


def _selector(label_selector) -> dict:
    return {
        "match_labels": label_selector.match_labels or {},
        "match_expressions": [
            {"key": item.key, "operator": item.operator, "values": item.values or []}
            for item in label_selector.match_expressions or []
        ],
    }


def _deployment_record(deployment, source: str) -> EvidenceRecord:
    redactor = Redactor()
    status = deployment.status
    data = {
        "desired_replicas": deployment.spec.replicas,
        "ready_replicas": status.ready_replicas or 0,
        "available_replicas": status.available_replicas or 0,
        "updated_replicas": status.updated_replicas or 0,
        "unavailable_replicas": status.unavailable_replicas or 0,
        "generation": deployment.metadata.generation,
        "observed_generation": status.observed_generation,
        "selector": _selector(deployment.spec.selector),
        "strategy": deployment.spec.strategy.type if deployment.spec.strategy else None,
        "conditions": conditions_summary(status.conditions, redactor),
    }
    return EvidenceRecord(
        evidence_id=f"deployment:{deployment.metadata.namespace}/{deployment.metadata.name}",
        category="deployment",
        resource_kind="Deployment",
        resource_name=deployment.metadata.name,
        namespace=deployment.metadata.namespace,
        collected_at=utc_now(),
        source=source,
        redactions=redactor.count,
        data=data,
    )


def _replica_set_record(replica_set, source: str) -> EvidenceRecord:
    status = replica_set.status
    annotations = replica_set.metadata.annotations or {}
    return EvidenceRecord(
        evidence_id=f"replicaset:{replica_set.metadata.namespace}/{replica_set.metadata.name}",
        category="replicaset",
        resource_kind="ReplicaSet",
        resource_name=replica_set.metadata.name,
        namespace=replica_set.metadata.namespace,
        collected_at=utc_now(),
        source=source,
        data={
            "desired_replicas": replica_set.spec.replicas,
            "ready_replicas": status.ready_replicas or 0,
            "available_replicas": status.available_replicas or 0,
            "revision": annotations.get(REVISION_ANNOTATION),
            "selector": _selector(replica_set.spec.selector),
            "owner_references": owner_references(replica_set.metadata),
        },
    )


def collect_deployments(ctx: CollectorContext) -> list[EvidenceRecord]:
    source = f"GET /apis/apps/v1/namespaces/{ctx.namespace}/deployments"
    return collect_list(
        ctx,
        collector="deployments",
        category="deployment",
        kind="Deployment",
        source=source,
        namespace=ctx.namespace,
        fetch=lambda **kwargs: ctx.apps.list_namespaced_deployment(ctx.namespace, **kwargs),
        to_records=lambda deployment: [_deployment_record(deployment, source)],
    )


def collect_replica_sets(ctx: CollectorContext) -> list[EvidenceRecord]:
    source = f"GET /apis/apps/v1/namespaces/{ctx.namespace}/replicasets"
    return collect_list(
        ctx,
        collector="replicasets",
        category="replicaset",
        kind="ReplicaSet",
        source=source,
        namespace=ctx.namespace,
        fetch=lambda **kwargs: ctx.apps.list_namespaced_replica_set(ctx.namespace, **kwargs),
        to_records=lambda replica_set: [_replica_set_record(replica_set, source)],
    )
