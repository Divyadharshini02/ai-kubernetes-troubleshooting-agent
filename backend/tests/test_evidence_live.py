"""Evidence collection against real failures in the kind cluster. Run with: pytest -m cluster

The test harness uses kubectl to create and delete a throwaway namespace. The
application itself only reads. Assertions check collected facts, not diagnoses.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from lab_harness import LAB_NAMESPACE as LAB
from lab_harness import evidence_lab

pytestmark = pytest.mark.cluster

client = TestClient(app)
WAIT_SECONDS = 180


def fetch():
    response = client.get(f"/api/namespaces/{LAB}/evidence")
    assert response.status_code == 200, response.text
    return response.json()


def index(bundle):
    return {record["evidence_id"]: record for record in bundle["records"]}


def event_reasons(records, pod):
    return {
        record["data"]["reason"]
        for record in records.values()
        if record["category"] == "event" and record["data"]["involved_object"]["name"] == pod
    }


def failures_are_visible(records):
    crash_pods = [
        r for r in records.values()
        if r["category"] == "container" and r["data"]["pod"].startswith("crash-demo-")
    ]
    liveness = records.get(f"container:{LAB}/liveness-demo/app")
    image = records.get(f"container:{LAB}/image-demo/app")
    return (
        crash_pods
        and crash_pods[0]["data"]["restart_count"]
        and liveness
        and liveness["data"]["restart_count"]
        and image
        and (image["data"]["state"] or {}).get("reason") == "ImagePullBackOff"
        and "Unhealthy" in event_reasons(records, "readiness-demo")
        and "FailedScheduling" in event_reasons(records, "pending-selector-demo")
        and any(
            r["category"] == "endpointslice" and r["data"]["service"] == "mismatch-demo"
            for r in records.values()
        )
    )


@pytest.fixture(scope="module")
def lab():
    with evidence_lab():
        deadline = time.monotonic() + WAIT_SECONDS
        bundle = fetch()
        while not failures_are_visible(index(bundle)):
            assert time.monotonic() < deadline, "failures did not appear in time"
            time.sleep(5)
            bundle = fetch()
        yield bundle


def container(records, prefix):
    return next(
        r for r in records.values()
        if r["category"] == "container" and r["data"]["pod"].startswith(prefix)
    )


def test_bundle_is_complete_with_identity_on_every_record(lab):
    assert lab["completeness"] == "complete", lab["status_counts"]
    ids = [record["evidence_id"] for record in lab["records"]]
    assert len(ids) == len(set(ids))
    for record in lab["records"]:
        assert record["collected_at"]
        assert record["source"]
        assert record["resource_kind"]
        assert record["status"] in ("collected", "empty", "not_available")
    categories = {record["category"] for record in lab["records"]}
    assert {
        "namespace", "pod", "container", "container_last_state", "logs", "event", "deployment",
        "replicaset", "service", "endpointslice", "node", "pvc", "storageclass", "serviceaccount",
    } <= categories


def test_crash_loop_evidence(lab):
    records = index(lab)
    crash = container(records, "crash-demo-")
    pod = crash["data"]["pod"]

    assert crash["temporality"] == "current"
    assert crash["data"]["restart_count"] >= 1
    assert crash["data"]["ready"] is False

    last = records[f"container-last-state:{LAB}/{pod}/app"]
    assert last["temporality"] == "historical"
    assert last["observed_at"]
    assert last["data"]["last_state"]["exit_code"] == 3
    assert last["data"]["last_state"]["reason"] == "Error"

    # The kubelet keeps few dead containers, so depending on timing the crash output
    # is in either stream. It must be in at least one, and the other must be honest.
    current_logs = records[f"logs:{LAB}/{pod}/app:current"]
    previous_logs = records[f"logs:{LAB}/{pod}/app:previous"]
    assert current_logs["temporality"] == "current"
    assert previous_logs["temporality"] == "historical"
    streams = [log for log in (current_logs, previous_logs) if log["status"] == "collected"]
    assert streams, (current_logs, previous_logs)
    assert any("startup failed" in log["data"]["text"] for log in streams)
    for log in streams:
        assert "password=[REDACTED]" in log["data"]["text"]
        assert log["redactions"] >= 1
        assert "unable to retrieve container logs" not in log["data"]["text"]
    for log in (current_logs, previous_logs):
        if log["status"] != "collected":
            assert log["status"] == "not_available"
            assert "text" not in log["data"]

    assert "BackOff" in event_reasons(records, pod)
    assert crash["data"]["resources"]["limits"]["memory"] == "32Mi"
    assert any(
        r["category"] == "deployment" and r["resource_name"] == "crash-demo"
        and r["data"]["ready_replicas"] == 0
        for r in records.values()
    )
    assert any(
        r["category"] == "replicaset"
        and {"kind": "Deployment", "name": "crash-demo"} in r["data"]["owner_references"]
        for r in records.values()
    )


def test_no_secret_value_or_literal_env_value_is_exposed(lab):
    records = index(lab)
    text = str(lab)

    assert "lab-log-secret" not in text
    assert "lab-literal-secret" not in text
    variables = container(records, "crash-demo-")["data"]["env"]["variables"]
    assert {"name": "LITERAL_SETTING", "source": "literal"} in variables
    assert {
        "name": "FROM_SECRET",
        "source": "secret",
        "secret_name": "not-created",
        "key": "password",
        "optional": True,
    } in variables


def test_image_pull_evidence(lab):
    records = index(lab)
    image = records[f"container:{LAB}/image-demo/app"]

    assert image["data"]["state"]["reason"] == "ImagePullBackOff"
    assert image["data"]["image"]["registry"] == "registry.invalid"
    assert image["data"]["image"]["repository"] == "demo/app"
    assert image["data"]["image"]["tag"] == "missing"
    assert image["data"]["restart_count"] == 0
    assert {"Failed", "BackOff"} & event_reasons(records, "image-demo")
    assert records[f"logs:{LAB}/image-demo/app:current"]["status"] == "not_available"
    assert records[f"serviceaccount:{LAB}/default"]["data"] == {"image_pull_secrets": []}


def test_pending_evidence(lab):
    records = index(lab)
    pod = records[f"pod:{LAB}/pending-selector-demo"]

    assert pod["data"]["phase"] == "Pending"
    assert pod["data"]["node_name"] is None
    assert pod["data"]["node_selector"] == {"evidence-lab/no-such-node": "true"}
    scheduled = next(c for c in pod["data"]["conditions"] if c["type"] == "PodScheduled")
    assert scheduled["status"] == "False"
    assert scheduled["reason"] == "Unschedulable"
    assert "FailedScheduling" in event_reasons(records, "pending-selector-demo")

    node = next(r for r in records.values() if r["category"] == "node")
    assert node["data"]["allocatable"]["cpu"]

    claim = records[f"pvc:{LAB}/pending-claim"]
    assert claim["data"]["phase"] == "Pending"
    assert claim["data"]["storage_class"] == "no-such-class"
    assert records[f"pod:{LAB}/pending-claim-demo"]["data"]["persistent_volume_claims"] == ["pending-claim"]
    classes = {r["resource_name"] for r in records.values() if r["category"] == "storageclass"}
    assert "no-such-class" not in classes
    assert "standard" in classes


def test_service_with_no_matching_pods_evidence(lab):
    records = index(lab)
    service = records[f"service:{LAB}/mismatch-demo"]
    endpoint_slice = next(
        r for r in records.values()
        if r["category"] == "endpointslice" and r["data"]["service"] == "mismatch-demo"
    )

    assert service["data"]["selector"] == {"app": "no-such-app"}
    assert service["data"]["ports"][0]["port"] == 80
    assert endpoint_slice["status"] == "collected"
    assert endpoint_slice["data"]["endpoints"] == []


def test_readiness_and_liveness_evidence_are_distinct(lab):
    records = index(lab)
    readiness = records[f"container:{LAB}/readiness-demo/app"]
    liveness = records[f"container:{LAB}/liveness-demo/app"]

    assert readiness["data"]["state"]["kind"] == "running"
    assert readiness["data"]["ready"] is False
    assert readiness["data"]["restart_count"] == 0
    handler = readiness["data"]["probes"]["readiness"]["handler"]
    assert handler["path"] == "/no-such-path"
    assert handler["port"] == 80
    assert readiness["data"]["probes"]["liveness"] is None

    assert liveness["data"]["restart_count"] >= 1
    assert liveness["data"]["probes"]["liveness"]["failure_threshold"] == 1
    assert liveness["data"]["probes"]["readiness"] is None
    assert "Unhealthy" in event_reasons(records, "readiness-demo")
    assert "Unhealthy" in event_reasons(records, "liveness-demo")


def test_events_are_historical_with_times(lab):
    events = [r for r in lab["records"] if r["category"] == "event"]

    assert events
    for event in events:
        assert event["temporality"] == "historical"
        assert event["observed_at"]
        assert event["data"]["age_seconds"] is not None


def test_healthy_namespace_reports_no_problem_logs():
    response = client.get("/api/namespaces/sample-app/evidence")

    assert response.status_code == 200
    records = index(response.json())
    assert response.json()["completeness"] == "complete"
    assert records["logs:selection"]["status"] == "empty"
    assert not [r for r in records.values() if r["category"] == "container_last_state"]
    pod = next(r for r in records.values() if r["category"] == "pod")
    assert pod["data"]["phase"] == "Running"


def test_missing_namespace_returns_404():
    response = client.get("/api/namespaces/no-such-namespace/evidence")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "namespace_not_found"
