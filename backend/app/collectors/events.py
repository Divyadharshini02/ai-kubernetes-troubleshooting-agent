"""Kubernetes events. An event records something that happened, not current state."""

from app.collectors.base import CollectorContext, collect_list, utc_now
from app.collectors.redaction import Redactor
from app.models.evidence import EvidenceRecord


def _last_seen(event):
    series = event.series.last_observed_time if event.series else None
    return (
        series
        or event.last_timestamp
        or event.event_time
        or event.first_timestamp
        or event.metadata.creation_timestamp
    )


def _event_record(event, namespace: str, source: str) -> EvidenceRecord:
    redactor = Redactor()
    involved = event.involved_object
    collected_at = utc_now()
    last_seen = _last_seen(event)
    count = event.series.count if event.series else event.count
    reporter = event.reporting_component or (event.source.component if event.source else None)
    return EvidenceRecord(
        evidence_id=f"event:{namespace}/{event.metadata.uid or event.metadata.name}",
        category="event",
        resource_kind="Event",
        resource_name=event.metadata.name,
        namespace=namespace,
        collected_at=collected_at,
        source=source,
        temporality="historical",
        observed_at=last_seen,
        data={
            "type": event.type,
            "reason": event.reason,
            "message": redactor.text(event.message),
            "count": count,
            "first_seen": event.first_timestamp or event.event_time,
            "last_seen": last_seen,
            "age_seconds": int((collected_at - last_seen).total_seconds()) if last_seen else None,
            "reporter": reporter,
            "involved_object": {
                "kind": involved.kind,
                "name": involved.name,
                "namespace": involved.namespace,
                "field_path": involved.field_path,
            },
        },
        redactions=redactor.count,
    )


def collect_events(ctx: CollectorContext) -> list[EvidenceRecord]:
    source = f"GET /api/v1/namespaces/{ctx.namespace}/events"
    return collect_list(
        ctx,
        collector="events",
        category="event",
        kind="Event",
        source=source,
        namespace=ctx.namespace,
        fetch=lambda **kwargs: ctx.core.list_namespaced_event(ctx.namespace, **kwargs),
        to_records=lambda event: [_event_record(event, ctx.namespace, source)],
    )
