"""The full lifecycle against the real kind cluster. Run with: pytest -m cluster

Each scenario goes from healthy, to a real fault, to a diagnosis, to the fix, to a fresh
investigation. Every fault is first confirmed directly with kubectl, independently of the
agent. EXPECTED is written by hand and is never available to production code.
"""

import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import investigations as storage
from failure_scenarios import FAULTS, HEALTHY, NAMESPACE, OWNER_LABEL, OWNER_VALUE, apply_scenario, failure_demo, remove_scenarios
from lab_harness import run_kubectl

pytestmark = pytest.mark.cluster

client = TestClient(app)
URL = "/api/investigations"
DIAGNOSIS = f"/api/namespaces/{NAMESPACE}/diagnosis"
WAIT_SECONDS = 180
SERVICE_NOT_READY = ("service_selector", "Service demo-web matches pods, but none are ready", "high", "not a selector mismatch")

# manifest: (scenario, title text, strength, cause text, evidence categories), then any second diagnosis.
EXPECTED = {
    "crash-loop.yaml": (
        ("crash_loop_backoff", "Container web is restarting repeatedly", "medium", "missing required configuration", {"container", "logs"}),
        SERVICE_NOT_READY,
    ),
    "image-pull.yaml": (
        ("image_pull_backoff", "Container web cannot pull its image", "high", "unable to reach the registry", {"container", "event"}),
        SERVICE_NOT_READY,
    ),
    "pending-pod.yaml": (
        ("pending_pod", "cannot be scheduled", "high", "node selector", {"pod", "event"}),
        SERVICE_NOT_READY,
    ),
    "readiness-probe.yaml": (
        ("probe_failure", "Readiness probe of container web is failing", "high", "HTTP 404", {"container", "event"}),
        SERVICE_NOT_READY,
    ),
    "service-selector.yaml": (
        ("service_selector", "Service demo-web selector matches no pods", "high", "typo", {"service"}),
    ),
}


def wait_until(check, what):
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        value = check()
        if value:
            return value
        assert time.monotonic() < deadline, f"Timed out waiting for {what}"
        time.sleep(2)


def kubectl_json(*args):
    return json.loads(run_kubectl("get", *args, "-n", NAMESPACE, "-o", "json"))


def demo_pods():
    return kubectl_json("pods", "-l", "app=demo-web")["items"]


def sample_app_pods():
    pods = json.loads(run_kubectl("get", "pods", "-n", "sample-app", "-o", "json"))["items"]
    return {
        pod["metadata"]["uid"]: sum(status["restartCount"] for status in pod["status"]["containerStatuses"])
        for pod in pods
    }


def readiness_failures():
    """Readiness failures recorded for the current demo-web pod."""
    pod = demo_pods()[0]["metadata"]["name"]
    events = kubectl_json("events", "--field-selector", f"involvedObject.name={pod},reason=Unhealthy")["items"]
    return sum(event.get("count") or 1 for event in events)


def wait_for_rollout():
    run_kubectl("rollout", "status", "deployment/demo-web", "-n", NAMESPACE, "--timeout=120s")


def restore_healthy():
    apply_scenario(HEALTHY)
    wait_for_rollout()


# What each fault looks like to kubectl, without any of the application's code.


def _container_status():
    pods = demo_pods()
    statuses = pods[0]["status"].get("containerStatuses") if len(pods) == 1 else None
    return statuses[0] if statuses else None


def crash_observed():
    status = _container_status()
    if not status or status["restartCount"] < 2:
        return False
    return status.get("lastState", {}).get("terminated", {}).get("exitCode") == 3


def image_pull_observed():
    status = _container_status()
    return bool(status) and status["state"].get("waiting", {}).get("reason") in ("ErrImagePull", "ImagePullBackOff")


def pending_observed():
    pods = demo_pods()
    if len(pods) != 1 or pods[0]["status"]["phase"] != "Pending":
        return False
    return any(c["type"] == "PodScheduled" and c["status"] == "False" for c in pods[0]["status"].get("conditions", []))


def readiness_observed():
    status = _container_status()
    if not status or not status["state"].get("running") or status["ready"]:
        return False
    events = kubectl_json("events.v1.events.k8s.io", "--field-selector", "reason=Unhealthy")["items"]
    return any("statuscode: 404" in (event.get("note") or "") for event in events)


def selector_observed():
    status = _container_status()
    if not status or not status["ready"]:
        return False
    slices = kubectl_json("endpointslices", "-l", "kubernetes.io/service-name=demo-web")["items"]
    return not any(endpoint.get("conditions", {}).get("ready") for item in slices for endpoint in item.get("endpoints") or [])


OBSERVED = {
    "crash-loop.yaml": crash_observed,
    "image-pull.yaml": image_pull_observed,
    "pending-pod.yaml": pending_observed,
    "readiness-probe.yaml": readiness_observed,
    "service-selector.yaml": selector_observed,
}


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "history.db")


@pytest.fixture(scope="module")
def demo():
    with failure_demo():
        wait_for_rollout()
        yield


@pytest.fixture(scope="module")
def sample_app_before():
    return sample_app_pods()


def investigate():
    """A fresh investigation, saved in history like one started from the dashboard."""
    response = client.post(URL, json={"namespace": NAMESPACE})
    assert response.status_code == 202, response.text
    return client.get(f"{URL}/{response.json()['id']}").json()


def live_diagnoses():
    """Current diagnoses, for waiting. These are not saved, so they cannot affect recovery."""
    return client.get(DIAGNOSIS).json()


def wait_for_outcome(outcome):
    wait_until(lambda: live_diagnoses()["outcome"] == outcome, f"outcome {outcome}")


def wait_for_diagnoses(expected):
    def found():
        confirmed = {d["scenario"] for d in live_diagnoses()["diagnoses"] if d["status"] == "problem_detected"}
        return {item[0] for item in expected} <= confirmed

    wait_until(found, f"diagnoses {[item[0] for item in expected]}")


def assert_diagnosis(diagnoses, scenario, title, strength, cause, categories=frozenset()):
    matching = [d for d in diagnoses if d["scenario"] == scenario and title in d["title"]]
    assert len(matching) == 1, [(d["scenario"], d["title"]) for d in diagnoses]
    diagnosis = matching[0]
    assert diagnosis["status"] == "problem_detected"
    assert diagnosis["evidence_strength"] == strength, diagnosis["possible_causes"]
    causes = " | ".join(item["statement"] for item in diagnosis["possible_causes"])
    assert cause in causes, causes
    assert categories <= {reference["category"] for reference in diagnosis["evidence_references"]}


def test_healthy_baseline_detects_no_supported_problem(demo, sample_app_before):
    # Regression: a normal start used to record enough failures to make the pod look unstable.
    # Those count for 10 minutes, far longer than this waits, so waiting cannot hide them.
    assert readiness_failures() < 3
    wait_for_outcome("no_problems_found")

    investigation = investigate()

    assert investigation["status"] == "completed"
    assert investigation["outcome"] == "no_problems_found"
    assert investigation["report"]["diagnoses"] == []
    assert investigation["report"]["evidence_completeness"] == "complete"
    assert investigation["verification_status"] == "not_verified"


@pytest.mark.parametrize("manifest", FAULTS)
def test_failure_is_diagnosed_and_recovery_is_verified(demo, sample_app_before, manifest):
    restore_healthy()
    wait_for_outcome("no_problems_found")
    assert readiness_failures() < 3
    before = investigate()
    assert before["outcome"] == "no_problems_found"

    apply_scenario(manifest)
    try:
        wait_until(OBSERVED[manifest], f"kubectl to show the fault from {manifest}")
        wait_for_diagnoses(EXPECTED[manifest])

        failing = investigate()
        assert failing["status"] == "completed"
        assert failing["outcome"] == "problems_found"
        assert failing["verification_status"] == "not_verified"
        assert len(failing["report"]["diagnoses"]) == len(EXPECTED[manifest])
        for expected in EXPECTED[manifest]:
            assert_diagnosis(failing["report"]["diagnoses"], *expected)
        cited = {r["evidence_id"] for d in failing["report"]["diagnoses"] for r in d["evidence_references"]}
        assert cited <= {record["evidence_id"] for record in failing["report"]["evidence"]["records"]}

        assert investigate()["verification_status"] == "still_failing"
    finally:
        restore_healthy()

    wait_for_outcome("no_problems_found")
    recovered = investigate()
    assert recovered["outcome"] == "no_problems_found"
    assert recovered["verification_status"] == "recovered"
    assert recovered["report"]["diagnoses"] == []


def test_every_manifest_is_accepted_by_the_real_api_server(demo):
    for name in (HEALTHY, *FAULTS):
        apply_scenario(name, run_kubectl, "--dry-run=server")


def test_invalid_manifest_is_rejected_without_changing_anything(demo, tmp_path):
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(f"apiVersion: v1\nkind: Service\nmetadata:\n  name: invalid\n  namespace: {NAMESPACE}\nspec:\n  ports: not-a-list\n")

    with pytest.raises(subprocess.CalledProcessError):
        run_kubectl("apply", "-f", str(invalid))

    assert run_kubectl("get", "service", "invalid", "-n", NAMESPACE, "--ignore-not-found") == ""


def test_deleting_the_failing_workload_is_not_called_recovered(demo):
    apply_scenario("image-pull.yaml")
    wait_until(image_pull_observed, "kubectl to show the image pull fault")
    wait_for_diagnoses(EXPECTED["image-pull.yaml"])
    assert investigate()["outcome"] == "problems_found"

    run_kubectl("delete", "deployment", "demo-web", "-n", NAMESPACE, "--wait=true", "--timeout=60s")
    run_kubectl("delete", "service", "demo-web", "-n", NAMESPACE, "--wait=true", "--timeout=60s")
    wait_until(lambda: not demo_pods(), "the pods to be removed")

    latest = investigate()
    assert latest["outcome"] == "no_problems_found"
    assert latest["verification_status"] == "not_verified"
    restore_healthy()


def test_cleanup_removes_only_failure_demo(demo, sample_app_before):
    namespaces_before = set(run_kubectl("get", "namespaces", "-o", "name").split())
    labels = json.loads(run_kubectl("get", "namespace", NAMESPACE, "-o", "json"))["metadata"]["labels"]
    assert labels[OWNER_LABEL] == OWNER_VALUE

    remove_scenarios()

    assert set(run_kubectl("get", "namespaces", "-o", "name").split()) == namespaces_before - {f"namespace/{NAMESPACE}"}
    assert sample_app_pods() == sample_app_before
