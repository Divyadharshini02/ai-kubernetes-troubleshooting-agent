from datetime import datetime, timedelta, timezone

from app.collectors.events import collect_events
from stub_kube import NAMESPACE, SECRET_VALUE, list_of, metadata

EVENTS = f"/api/v1/namespaces/{NAMESPACE}/events"


def make_event(name="e1", **fields):
    return {
        "metadata": metadata(name),
        "involvedObject": {"kind": "Pod", "name": "web", "namespace": NAMESPACE},
        "type": "Warning",
        "reason": "BackOff",
        "message": "Back-off restarting failed container",
        **fields,
    }


def collect(ctx, kube, *events):
    kube.routes[EVENTS] = (200, list_of("EventList", list(events)))
    return collect_events(ctx)


def test_event_is_recorded_as_historical(ctx, kube):
    record = collect(
        ctx,
        kube,
        make_event(
            count=7,
            firstTimestamp="2026-01-01T00:00:00Z",
            lastTimestamp="2026-01-01T00:10:00Z",
            source={"component": "kubelet"},
        ),
    )[0]

    assert record.temporality == "historical"
    assert record.status == "collected"
    assert record.evidence_id == f"event:{NAMESPACE}/uid-e1"
    assert record.observed_at == datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)
    assert record.data["type"] == "Warning"
    assert record.data["reason"] == "BackOff"
    assert record.data["count"] == 7
    assert record.data["reporter"] == "kubelet"
    assert record.data["first_seen"] == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert record.data["involved_object"] == {
        "kind": "Pod",
        "name": "web",
        "namespace": NAMESPACE,
        "field_path": None,
    }


def test_age_lets_old_events_be_told_from_recent_ones(ctx, kube):
    recent = datetime.now(timezone.utc) - timedelta(seconds=30)
    old = datetime.now(timezone.utc) - timedelta(days=2)
    records = collect(
        ctx,
        kube,
        make_event("recent", lastTimestamp=recent.isoformat()),
        make_event("old", lastTimestamp=old.isoformat()),
    )

    ages = {record.resource_name: record.data["age_seconds"] for record in records}
    assert 25 <= ages["recent"] <= 60
    assert ages["old"] > 172000


def test_series_event_uses_series_time_and_count(ctx, kube):
    record = collect(
        ctx,
        kube,
        make_event(
            eventTime="2026-01-01T00:00:00.000000Z",
            reportingComponent="default-scheduler",
            series={"count": 12, "lastObservedTime": "2026-01-01T00:30:00.000000Z"},
        ),
    )[0]

    assert record.data["count"] == 12
    assert record.data["reporter"] == "default-scheduler"
    assert record.observed_at == datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
    assert record.data["first_seen"] == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_event_with_only_creation_time(ctx, kube):
    event = make_event()
    event["metadata"]["creationTimestamp"] = "2026-01-01T00:05:00Z"

    record = collect(ctx, kube, event)[0]

    assert record.observed_at == datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)


def test_event_with_no_time_is_not_given_a_guessed_time(ctx, kube):
    record = collect(ctx, kube, make_event())[0]

    assert record.observed_at is None
    assert record.data["age_seconds"] is None


def test_event_messages_are_redacted(ctx, kube):
    record = collect(ctx, kube, make_event(message=f"failed with token={SECRET_VALUE}"))[0]

    assert record.redactions == 1
    assert SECRET_VALUE not in record.model_dump_json()


def test_event_without_uid_still_has_an_identifier(ctx, kube):
    event = make_event()
    del event["metadata"]["uid"]

    record = collect(ctx, kube, event)[0]

    assert record.evidence_id == f"event:{NAMESPACE}/e1"


def test_event_field_path_identifies_the_container(ctx, kube):
    event = make_event(
        involvedObject={
            "kind": "Pod",
            "name": "web",
            "namespace": NAMESPACE,
            "fieldPath": "spec.containers{app}",
        },
    )

    record = collect(ctx, kube, event)[0]

    assert record.data["involved_object"]["field_path"] == "spec.containers{app}"
