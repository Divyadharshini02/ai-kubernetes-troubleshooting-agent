"""Diagnostics against real failures in the kind cluster. Run with: pytest -m cluster

EXPECTED is written independently of the rules and is never available to production
code. The rules only ever see what the collectors read from the cluster.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from lab_harness import LAB_NAMESPACE as LAB
from lab_harness import evidence_lab

pytestmark = pytest.mark.cluster

client = TestClient(app)
WAIT_SECONDS = 240

# (scenario, resource name prefix): (status, strength, text required in a possible cause)
EXPECTED = {
    ("crash_loop_backoff", "crash-demo-"): ("problem_detected", "medium", "exited with code 3"),
    ("crash_loop_backoff", "oom-demo"): ("problem_detected", "high", "exceeded its memory limit"),
    ("crash_loop_backoff", "liveness-demo"): ("problem_detected", "high", "Liveness probe failures"),
    ("image_pull_backoff", "image-demo"): ("problem_detected", "high", "unable to reach the registry"),
    ("pending_pod", "pending-selector-demo"): ("problem_detected", "high", "node selector"),
    ("pending_pod", "pending-cpu-demo"): ("problem_detected", "high", "more cpu than any collected node"),
    ("pending_pod", "pending-claim-demo"): ("problem_detected", "high", "StorageClass that does not exist"),
    ("pending_pod", "anti-affinity-demo-"): ("problem_detected", "high", "anti-affinity"),
    ("service_selector", "mismatch-demo"): ("problem_detected", "high", "typo"),
    ("service_selector", "not-ready-demo"): ("problem_detected", "high", "not a selector mismatch"),
    ("probe_failure", "readiness-demo"): ("problem_detected", "high", "HTTP 404"),
    ("probe_failure", "liveness-demo"): ("problem_detected", "high", "HTTP 404"),
    ("unclassified", "config-error-demo"): ("insufficient_evidence", "insufficient", None),
}


def fetch(**params):
    response = client.get(f"/api/namespaces/{LAB}/diagnosis", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def find(report, scenario, prefix):
    return [
        diagnosis
        for diagnosis in report["diagnoses"]
        if diagnosis["scenario"] == scenario and diagnosis["affected_resource"]["name"].startswith(prefix)
    ]


def _settled(report):
    for (scenario, prefix), (status, _, _) in EXPECTED.items():
        found = find(report, scenario, prefix)
        if not found or found[0]["status"] != status:
            return False
    return True


@pytest.fixture(scope="module")
def lab():
    with evidence_lab():
        deadline = time.monotonic() + WAIT_SECONDS
        report = fetch(include_evidence=True)
        while not _settled(report):
            missing = [key for key in EXPECTED if not find(report, *key)]
            assert time.monotonic() < deadline, f"not diagnosed in time: {missing}"
            time.sleep(5)
            report = fetch(include_evidence=True)
        yield report


@pytest.mark.parametrize(("key", "expected"), EXPECTED.items(), ids=lambda v: "/".join(v) if isinstance(v[0], str) and len(v) == 2 else "")
def test_real_failure_is_diagnosed_as_expected(lab, key, expected):
    status, strength, cause_text = expected

    found = find(lab, *key)

    assert len(found) == 1, found
    diagnosis = found[0]
    assert diagnosis["status"] == status
    assert diagnosis["evidence_strength"] == strength, diagnosis["possible_causes"]
    if cause_text:
        causes = " | ".join(cause["statement"] for cause in diagnosis["possible_causes"])
        assert cause_text in causes, causes
    else:
        assert diagnosis["possible_causes"] == []


def test_nothing_unexpected_is_diagnosed(lab):
    allowed = {key[1] for key in EXPECTED}
    for diagnosis in lab["diagnoses"]:
        name = diagnosis["affected_resource"]["name"]
        assert any(name.startswith(prefix) for prefix in allowed), diagnosis["diagnosis_id"]
        assert any(
            scenario == diagnosis["scenario"] and name.startswith(prefix)
            for scenario, prefix in EXPECTED
        ), diagnosis["diagnosis_id"]


def test_real_outcome_and_rule_coverage(lab):
    assert lab["outcome"] == "problems_found"
    assert lab["evidence_completeness"] == "complete"
    assert {result["status"] for result in lab["rule_results"]} == {"evaluated"}


def test_healthy_replica_of_a_partly_scheduled_deployment_is_not_flagged(lab):
    pods = [r for r in lab["evidence"]["records"] if r["category"] == "pod" and r["resource_name"].startswith("anti-affinity-demo-")]
    diagnosed = {d["affected_resource"]["name"] for d in find(lab, "pending_pod", "anti-affinity-demo-")}

    assert len(pods) == 2
    assert len(diagnosed) == 1
    healthy = [pod for pod in pods if pod["resource_name"] not in diagnosed]
    assert healthy[0]["data"]["phase"] == "Running"


def test_every_citation_points_at_real_collected_evidence(lab):
    collected = {record["evidence_id"]: record for record in lab["evidence"]["records"]}

    for diagnosis in lab["diagnoses"]:
        assert diagnosis["evidence_references"]
        for reference in diagnosis["evidence_references"]:
            record = collected[reference["evidence_id"]]
            assert reference["temporality"] == record["temporality"]
            assert reference["source"] == record["source"]
            assert reference["collected_at"]


def test_secrets_in_the_lab_do_not_reach_the_report(lab):
    text = str(lab)

    assert "lab-log-secret" not in text
    assert "lab-literal-secret" not in text


def test_empty_output_is_not_reported_as_unreadable_logs(lab):
    # Regression from a real run: oom-demo wrote nothing, and its logs were readable.
    diagnosis = find(lab, "crash_loop_backoff", "oom-demo")[0]

    assert not any("could be read" in item for item in diagnosis["missing_evidence"])


def test_probe_side_effects_are_not_blamed_on_the_application(lab):
    # Regression from a real run: nginx logged the failing probe's request as an error.
    diagnosis = find(lab, "crash_loop_backoff", "liveness-demo")[0]

    causes = " | ".join(cause["statement"] for cause in diagnosis["possible_causes"])
    assert "startup or runtime error" not in causes


def test_healthy_namespace_reports_no_problems():
    response = client.get("/api/namespaces/sample-app/diagnosis")

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "no_problems_found", body["diagnoses"]
    assert body["diagnoses"] == []
    assert body["resources_examined"]["pods"] == 1
    assert body["resources_examined"]["services"] == 1


def test_missing_namespace_returns_404():
    response = client.get("/api/namespaces/no-such-namespace/diagnosis")

    assert response.status_code == 404
