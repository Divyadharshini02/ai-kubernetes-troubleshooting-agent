from app.collectors.base import CollectorContext, collect_list, utc_now
from app.models.evidence import EvidenceRecord


def _service_account_record(account, source: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"serviceaccount:{account.metadata.namespace}/{account.metadata.name}",
        category="serviceaccount",
        resource_kind="ServiceAccount",
        resource_name=account.metadata.name,
        namespace=account.metadata.namespace,
        collected_at=utc_now(),
        source=source,
        # Names only: registry credentials are never read.
        data={"image_pull_secrets": [ref.name for ref in account.image_pull_secrets or []]},
    )


def collect_service_accounts(ctx: CollectorContext) -> list[EvidenceRecord]:
    source = f"GET /api/v1/namespaces/{ctx.namespace}/serviceaccounts"
    return collect_list(
        ctx,
        collector="serviceaccounts",
        category="serviceaccount",
        kind="ServiceAccount",
        source=source,
        namespace=ctx.namespace,
        fetch=lambda **kwargs: ctx.core.list_namespaced_service_account(ctx.namespace, **kwargs),
        to_records=lambda account: [_service_account_record(account, source)],
    )
