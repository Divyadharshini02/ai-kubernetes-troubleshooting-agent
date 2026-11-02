"""Recovery status, the scenario manifests and the cleanup guards. No cluster is needed."""

import json
import os
import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.models.diagnosis import DiagnosticReport, ResourcesExamined
from app.services.investigations import recovery_status
from app.storage import investigations as storage
from failure_scenarios import (
    FAULTS,
    HEALTHY,
    MANUAL_CLEANUP,
    NAMESPACE,
    OWNER_LABEL,
    OWNER_VALUE,
    SCENARIOS,
    apply_scenario,
    failure_demo,
    remove_scenarios,
    require_lab_context,
)
from lab_harness import LAB_CONTEXT, LabCleanupError, LabSafetyError
from stub_kube import NAMESPACE as STUB_NAMESPACE
from stub_kube import container_status, list_of, make_pod, namespace_routes, pod_log_path
from test_cluster_health import VERSION

client = TestClient(app)
URL = "/api/investigations"
PODS = f"/api/v1/namespaces/{STUB_NAMESPACE}/pods"


def report(outcome, completeness="complete", pods=1):
    return DiagnosticReport(
        namespace=STUB_NAMESPACE,
        context="stub",
        outcome=outcome,
        summary="",
        evidence_completeness=completeness,
        evidence_collected_at="2026-01-01T00:00:00Z",
        resources_examined=ResourcesExamined(pods=pods, containers=pods, services=0),
        diagnoses=[],
        rule_results=[],
    )


@pytest.mark.parametrize(
    ("previous", "outcome", "completeness", "expected"),
    [
        # Only an earlier confirmed problem can be recovered from.
        ("problems_found", "no_problems_found", "complete", "recovered"),
        ("problems_found", "problems_found", "complete", "still_failing"),
        # Confirmed problems count even if other evidence is missing.
        ("problems_found", "problems_found", "partial", "still_failing"),
        # Inconclusive results prove nothing.
        ("problems_found", "insufficient_evidence", "complete", "not_verified"),
        ("problems_found", "insufficient_evidence", "partial", "not_verified"),
        ("problems_found", "no_problems_found", "partial", "not_verified"),
        # No earlier problem to compare with.
        (None, "no_problems_found", "complete", "not_verified"),
        (None, "problems_found", "complete", "not_verified"),
        ("no_problems_found", "no_problems_found", "complete", "not_verified"),
        ("no_problems_found", "problems_found", "complete", "not_verified"),
        ("insufficient_evidence", "no_problems_found", "complete", "not_verified"),
        ("insufficient_evidence", "problems_found", "complete", "not_verified"),
    ],
)
def test_recovery_status(previous, outcome, completeness, expected):
    assert recovery_status(previous, report(outcome, completeness)) == expected


def test_no_pods_is_not_recovery():
    assert recovery_status("problems_found", report("no_problems_found", pods=0)) == "not_verified"


# Through the API, the real rules and saved history.


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "history.db")


@pytest.fixture
def cluster(kube, stub_kubeconfig):
    """A stub cluster that starts healthy, with an empty namespace."""
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())
    kube.routes["/version"] = (200, VERSION)
    kube.routes["/api/v1/pods"] = (200, list_of("PodList", []))
    return kube


def break_it(kube):
    pod = make_pod(
        "web",
        statuses=[container_status("app", ready=False, restarts=4, state={"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}})],
    )
    kube.routes[PODS] = (200, list_of("PodList", [pod]))
    kube.routes[pod_log_path("web")] = (200, b"2026-01-01T00:00:00Z fatal\n")


def fix_it(kube):
    pod = make_pod("web-fixed", statuses=[container_status("app")])
    kube.routes[PODS] = (200, list_of("PodList", [pod]))
    kube.routes[pod_log_path("web-fixed")] = (200, b"2026-01-01T00:00:00Z started\n")


def investigate(namespace=STUB_NAMESPACE):
    body = client.post(URL, json={"namespace": namespace}).json()
    return client.get(f"{URL}/{body['id']}").json()


def test_failure_then_fix_reports_each_transition(cluster):
    assert investigate()["verification_status"] == "not_verified"

    break_it(cluster)
    first_failure = investigate()
    assert first_failure["outcome"] == "problems_found"
    assert first_failure["verification_status"] == "not_verified"
    assert investigate()["verification_status"] == "still_failing"

    fix_it(cluster)
    recovered = investigate()
    assert recovered["outcome"] == "no_problems_found"
    assert recovered["verification_status"] == "recovered"

    # Recovery has already been reported, so there is nothing further to compare.
    assert investigate()["verification_status"] == "not_verified"


def test_deleting_the_failing_workload_is_not_recovery(cluster):
    break_it(cluster)
    investigate()
    cluster.routes[PODS] = (200, list_of("PodList", []))

    latest = investigate()

    assert latest["outcome"] == "no_problems_found"
    assert latest["verification_status"] == "not_verified"


def test_recovery_status_is_saved_and_listed(cluster):
    break_it(cluster)
    investigate()
    fix_it(cluster)
    recovered = investigate()

    with TestClient(app) as restarted:
        history = restarted.get(URL).json()
        detail = restarted.get(f"{URL}/{recovered['id']}").json()

    assert [item["verification_status"] for item in history] == ["recovered", "not_verified"]
    assert detail["verification_status"] == "recovered"


def test_recovery_does_not_change_the_saved_diagnosis(cluster):
    break_it(cluster)
    first = investigate()
    second = investigate()

    def content(investigation):
        return [
            (d["diagnosis_id"], d["scenario"], d["title"], d["status"], d["evidence_strength"], d["possible_causes"])
            for d in investigation["report"]["diagnoses"]
        ]

    assert second["verification_status"] == "still_failing"
    assert content(second) == content(first)
    assert second["outcome"] == first["outcome"]


def test_partial_evidence_is_not_called_recovered(cluster):
    break_it(cluster)
    investigate()
    fix_it(cluster)
    cluster.routes[f"/api/v1/namespaces/{STUB_NAMESPACE}/events"] = (500, {"kind": "Status", "code": 500})

    latest = investigate()

    assert latest["report"]["evidence_completeness"] == "partial"
    assert latest["outcome"] == "insufficient_evidence"
    assert latest["verification_status"] == "not_verified"


def test_inconclusive_investigation_is_not_compared_with(cluster):
    # Looking too soon after a fix must not lose the comparison with the real failure.
    break_it(cluster)
    investigate()
    fix_it(cluster)
    events = f"/api/v1/namespaces/{STUB_NAMESPACE}/events"
    working = cluster.routes[events]
    cluster.routes[events] = (500, {"kind": "Status", "code": 500})
    assert investigate()["verification_status"] == "not_verified"
    cluster.routes[events] = working

    assert investigate()["verification_status"] == "recovered"


def test_failed_investigation_is_not_verified_and_is_not_compared_with(cluster, tmp_path, monkeypatch):
    break_it(cluster)
    investigate()

    working_kubeconfig = os.environ["KUBECONFIG"]
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "missing"))
    failed = investigate()
    assert failed["status"] == "failed"
    assert failed["verification_status"] == "not_verified"
    monkeypatch.setenv("KUBECONFIG", working_kubeconfig)

    fix_it(cluster)
    assert investigate()["verification_status"] == "recovered"


def test_interrupted_investigation_is_not_compared_with(cluster):
    break_it(cluster)
    investigate()
    storage.create_investigation("cut-off", STUB_NAMESPACE, False, "Created.")
    storage.mark_interrupted()
    assert client.get(f"{URL}/cut-off").json()["verification_status"] == "not_verified"

    fix_it(cluster)
    assert investigate()["verification_status"] == "recovered"


def test_another_namespace_is_not_compared_with(cluster):
    storage.create_investigation("elsewhere", "other-namespace", False, "Created.")
    storage.complete_investigation("elsewhere", report("problems_found"), "not_verified")

    assert investigate()["verification_status"] == "not_verified"


def test_existing_not_verified_history_can_still_be_read(cluster):
    storage.create_investigation("older", STUB_NAMESPACE, False, "Created.")
    storage.fail_investigation("older", "cluster_unreachable", "Gone.")

    assert client.get(URL).json()[0]["verification_status"] == "not_verified"


# The scenario manifests.


def documents(name):
    return [document for document in yaml.safe_load_all((SCENARIOS / name).read_text()) if document]


def test_every_manifest_is_known_and_none_is_extra():
    assert {path.name for path in SCENARIOS.iterdir()} == {HEALTHY, *FAULTS}


@pytest.mark.parametrize("name", [HEALTHY, *FAULTS])
def test_manifests_stay_in_their_namespace_and_carry_the_label(name):
    for document in documents(name):
        metadata = document["metadata"]
        assert document["kind"] in {"Namespace", "Deployment", "Service"}
        assert metadata["labels"][OWNER_LABEL] == OWNER_VALUE
        assert (metadata["name"] if document["kind"] == "Namespace" else metadata["namespace"]) == NAMESPACE


def containers(document):
    return document["spec"]["template"]["spec"]["containers"]


@pytest.mark.parametrize("name", [HEALTHY, *FAULTS])
def test_deployments_set_limits_and_have_no_liveness_probe(name):
    for document in documents(name):
        if document["kind"] == "Deployment":
            assert document["spec"]["replicas"] == 1
            assert document["spec"]["strategy"]["type"] == "Recreate"
            for container in containers(document):
                assert container["resources"]["requests"]
                assert container["resources"]["limits"]
                assert "livenessProbe" not in container


def test_only_the_image_fault_uses_an_image_not_pinned_by_digest():
    for name in (HEALTHY, *FAULTS):
        for document in documents(name):
            if document["kind"] == "Deployment":
                image = containers(document)[0]["image"]
                if name == "image-pull.yaml":
                    assert image.startswith("registry.invalid/")
                else:
                    assert "@sha256:" in image


def test_faults_differ_from_the_healthy_baseline_only_in_the_intended_way():
    healthy = {document["kind"]: document for document in documents(HEALTHY)}
    selector = documents("service-selector.yaml")[0]
    assert selector["spec"]["selector"] != healthy["Service"]["spec"]["selector"]
    assert selector["metadata"] == healthy["Service"]["metadata"]
    assert selector["spec"]["ports"] == healthy["Service"]["spec"]["ports"]

    template = healthy["Deployment"]["spec"]["template"]["spec"]
    pending = documents("pending-pod.yaml")[0]["spec"]["template"]["spec"]
    assert pending["nodeSelector"]
    assert pending["containers"] == template["containers"]
    probe = containers(documents("readiness-probe.yaml")[0])[0]["readinessProbe"]
    assert probe["httpGet"]["path"] == "/no-such-path"
    crash = containers(documents("crash-loop.yaml")[0])[0]
    assert "exit 3" in crash["command"][-1]


# The guards around the cluster.


class FakeKubectl:
    def __init__(self, namespace=None, delete_works=True, apply_error=None):
        self.namespace = namespace
        self.delete_works = delete_works
        self.apply_error = apply_error
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "get":
            return json.dumps(self.namespace) if self.namespace else ""
        if args[0] == "apply" and self.apply_error:
            raise self.apply_error
        if args[0] == "delete" and self.delete_works:
            self.namespace = None
        return ""

    def writes(self):
        return [call for call in self.calls if call[0] != "get"]


OURS = {"metadata": {"labels": {OWNER_LABEL: OWNER_VALUE}}}
NOT_OURS = {"metadata": {"labels": {OWNER_LABEL: "someone-else"}}}
UNLABELLED = {"metadata": {}}


def in_lab():
    return LAB_CONTEXT


def test_wrong_context_changes_nothing():
    kubectl = FakeKubectl()

    with pytest.raises(LabSafetyError):
        with failure_demo(kubectl, lambda: "kind-another-cluster"):
            pytest.fail("Must not get here")

    assert kubectl.calls == []


def test_context_check_accepts_only_the_lab():
    require_lab_context(in_lab)
    with pytest.raises(LabSafetyError):
        require_lab_context(lambda: "production")


@pytest.mark.parametrize("namespace", [NOT_OURS, UNLABELLED])
def test_namespace_we_did_not_create_is_never_touched(namespace):
    kubectl = FakeKubectl(namespace)

    with pytest.raises(LabSafetyError):
        with failure_demo(kubectl, in_lab):
            pytest.fail("Must not get here")
    with pytest.raises(LabSafetyError):
        remove_scenarios(kubectl)

    assert kubectl.writes() == []


def test_cleanup_deletes_only_this_namespace():
    kubectl = FakeKubectl(OURS)

    remove_scenarios(kubectl)

    deletes = [call for call in kubectl.writes() if call[0] == "delete"]
    assert len(deletes) == 1
    assert deletes[0][:3] == ("delete", "namespace", NAMESPACE)


def test_cleanup_of_a_missing_namespace_is_safe():
    remove_scenarios(FakeKubectl())


def test_cleanup_that_leaves_the_namespace_raises_with_instructions():
    with pytest.raises(LabCleanupError) as error:
        remove_scenarios(FakeKubectl(OURS, delete_works=False))

    assert MANUAL_CLEANUP in str(error.value)


def test_failed_apply_still_cleans_up():
    failure = subprocess.CalledProcessError(1, "kubectl")
    kubectl = FakeKubectl(OURS, apply_error=failure)

    with pytest.raises(subprocess.CalledProcessError):
        with failure_demo(kubectl, in_lab):
            pytest.fail("Must not get here")

    assert kubectl.calls[-2][0] == "delete"


def test_a_failure_inside_the_block_still_cleans_up():
    kubectl = FakeKubectl()

    with pytest.raises(RuntimeError):
        with failure_demo(kubectl, in_lab):
            kubectl.namespace = OURS
            raise RuntimeError("test failed")

    assert any(call[0] == "delete" for call in kubectl.calls)


@pytest.mark.parametrize("name", ["missing.yaml", "../base/sample-workload.yaml", "..\\base\\sample-workload.yaml", "", "."])
def test_only_named_manifests_can_be_applied(name):
    kubectl = FakeKubectl()

    with pytest.raises(ValueError):
        apply_scenario(name, kubectl)

    assert kubectl.calls == []


def test_missing_kubectl_fails_safely(monkeypatch):
    def no_kubectl(*_args, **_kwargs):
        raise FileNotFoundError("kubectl")

    monkeypatch.setattr(subprocess, "run", no_kubectl)

    with pytest.raises(FileNotFoundError):
        with failure_demo():
            pytest.fail("Must not get here")
