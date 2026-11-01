from app.collectors.base import CollectorContext, collect_list, conditions_summary, utc_now
from app.collectors.redaction import Redactor
from app.models.evidence import EvidenceRecord

DEFAULT_CLASS_ANNOTATION = "storageclass.kubernetes.io/is-default-class"


def _claim_record(claim, source: str) -> EvidenceRecord:
    redactor = Redactor()
    status, spec = claim.status, claim.spec
    data = {
        "phase": status.phase if status else None,
        "storage_class": spec.storage_class_name,
        "volume_name": spec.volume_name,
        "access_modes": spec.access_modes or [],
        "requested": (spec.resources.requests or {}) if spec.resources else {},
        "conditions": conditions_summary(status.conditions if status else None, redactor),
    }
    return EvidenceRecord(
        evidence_id=f"pvc:{claim.metadata.namespace}/{claim.metadata.name}",
        category="pvc",
        resource_kind="PersistentVolumeClaim",
        resource_name=claim.metadata.name,
        namespace=claim.metadata.namespace,
        collected_at=utc_now(),
        source=source,
        redactions=redactor.count,
        data=data,
    )


def _storage_class_record(storage_class, source: str) -> EvidenceRecord:
    annotations = storage_class.metadata.annotations or {}
    return EvidenceRecord(
        evidence_id=f"storageclass:{storage_class.metadata.name}",
        category="storageclass",
        resource_kind="StorageClass",
        resource_name=storage_class.metadata.name,
        collected_at=utc_now(),
        source=source,
        data={
            "provisioner": storage_class.provisioner,
            "volume_binding_mode": storage_class.volume_binding_mode,
            "is_default": annotations.get(DEFAULT_CLASS_ANNOTATION) == "true",
        },
    )


def collect_persistent_volume_claims(ctx: CollectorContext) -> list[EvidenceRecord]:
    source = f"GET /api/v1/namespaces/{ctx.namespace}/persistentvolumeclaims"
    return collect_list(
        ctx,
        collector="pvcs",
        category="pvc",
        kind="PersistentVolumeClaim",
        source=source,
        namespace=ctx.namespace,
        fetch=lambda **kwargs: ctx.core.list_namespaced_persistent_volume_claim(
            ctx.namespace, **kwargs
        ),
        to_records=lambda claim: [_claim_record(claim, source)],
    )


def collect_storage_classes(ctx: CollectorContext) -> list[EvidenceRecord]:
    source = "GET /apis/storage.k8s.io/v1/storageclasses"
    return collect_list(
        ctx,
        collector="storageclasses",
        category="storageclass",
        kind="StorageClass",
        source=source,
        namespace=None,
        fetch=lambda **kwargs: ctx.storage.list_storage_class(**kwargs),
        to_records=lambda storage_class: [_storage_class_record(storage_class, source)],
    )
