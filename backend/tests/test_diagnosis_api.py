"""The diagnosis endpoint through the real collectors and client, against a stub API."""

import socket

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import evidence as evidence_service
from stub_kube import (
    NAMESPACE,
    SECRET_VALUE,
    container_status,
    list_of,
    make_pod,
    namespace_routes,
    pod_log_path,
)

client = TestClient(app)
URL = f"/api/namespaces/{NAMESPACE}/diagnosis"
PODS = f"/api/v1/namespaces/{NAMESPACE}/pods"


@pytest.fixture
def api(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())
    return kube


def crashing_pod():
    return make_pod(
        statuses=[
            container_status(
                "app",
                ready=False,
                restarts=4,
                state={"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}},
            )
        ]
    )


def test_empty_namespace_reports_no_problems(api):
    response = client.get(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["namespace"] == NAMESPACE
    assert body["context"] == "stub-context"
    assert body["outcome"] == "no_problems_found"
    assert body["evidence_completeness"] == "complete"
    assert body["diagnoses"] == []
    assert body["evidence"] is None
    assert {result["status"] for result in body["rule_results"]} == {"evaluated"}
    assert {result["rule"] for result in body["rule_results"]} == {
        "crash_loop",
        "image_pull",
        "pending_pod",
        "service_selector",
        "probe_failure",
        "unclassified",
    }


def test_real_collected_evidence_reaches_the_rules(api):
    api.routes[PODS] = (200, list_of("PodList", [crashing_pod()]))
    api.routes[pod_log_path("web")] = (200, b"2026-01-01T00:00:00Z fatal: startup failed\n")

    body = client.get(URL).json()

    assert body["outcome"] == "problems_found"
    diagnosis = body["diagnoses"][0]
    assert diagnosis["scenario"] == "crash_loop_backoff"
    assert diagnosis["affected_resource"] == {
        "kind": "Pod",
        "name": "web",
        "namespace": NAMESPACE,
        "container": "app",
    }
    assert any("startup failed" in (fact["excerpt"] or "") for fact in diagnosis["confirmed_facts"])
    reference = diagnosis["evidence_references"][0]
    assert reference["evidence_id"] == f"container:{NAMESPACE}/web/app"
    assert reference["collected_at"]
    assert reference["source"].startswith("GET /")


def test_include_evidence_returns_every_cited_record(api):
    api.routes[PODS] = (200, list_of("PodList", [crashing_pod()]))
    api.routes[pod_log_path("web")] = (200, b"2026-01-01T00:00:00Z fatal\n")

    body = client.get(URL, params={"include_evidence": True}).json()

    collected = {record["evidence_id"] for record in body["evidence"]["records"]}
    cited = {
        reference["evidence_id"]
        for diagnosis in body["diagnoses"]
        for reference in diagnosis["evidence_references"]
    }
    assert cited
    assert cited <= collected


def test_forbidden_pods_give_an_insufficient_outcome_not_healthy(api):
    api.routes[PODS] = (403, {"kind": "Status", "message": f"leak {SECRET_VALUE}"})

    response = client.get(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "insufficient_evidence"
    assert body["evidence_completeness"] == "partial"
    assert body["diagnoses"] == []
    assert SECRET_VALUE not in response.text


def test_failed_collector_makes_rules_partial_not_healthy(api, monkeypatch):
    # A collector defect must mark its evidence unavailable, so "no problems" is impossible.
    def broken(_ctx):
        raise RuntimeError(f"defect {SECRET_VALUE}")

    monkeypatch.setattr(evidence_service, "collect_events", broken)

    response = client.get(URL)

    body = response.json()
    assert body["outcome"] == "insufficient_evidence"
    statuses = {result["rule"]: result["status"] for result in body["rule_results"]}
    assert statuses["probe_failure"] == "partial"
    assert SECRET_VALUE not in response.text


def test_time_limit_gives_an_insufficient_outcome(api, monkeypatch):
    monkeypatch.setattr(evidence_service, "COLLECTION_DEADLINE_SECONDS", -1)

    body = client.get(URL).json()

    assert body["outcome"] == "insufficient_evidence"


def test_missing_namespace_returns_404(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes[f"/api/v1/namespaces/{NAMESPACE}"] = (404, {"kind": "Status"})

    response = client.get(URL)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "namespace_not_found"


@pytest.mark.parametrize("name", ["UPPER", "has_underscore", "-leading", "a" * 64])
def test_invalid_namespace_names_are_rejected_without_a_cluster_call(kube, stub_kubeconfig, name):
    stub_kubeconfig(kube.url)

    response = client.get(f"/api/namespaces/{name}/diagnosis")

    assert response.status_code == 422
    assert kube.requests == []


def test_missing_kubeconfig_returns_503(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "missing"))

    response = client.get(URL)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "kubeconfig_unavailable"


def test_unreachable_cluster_returns_503(stub_kubeconfig):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    stub_kubeconfig(f"http://127.0.0.1:{closed_port}")

    response = client.get(URL)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cluster_unreachable"


def test_diagnosis_sends_only_read_requests_and_never_touches_secrets(api):
    api.routes[PODS] = (200, list_of("PodList", [crashing_pod()]))
    api.routes[pod_log_path("web")] = (200, b"2026-01-01T00:00:00Z fatal\n")

    assert client.get(URL).status_code == 200

    assert api.requests
    assert {method for method, _, _ in api.requests} == {"GET"}
    assert not any("secret" in path.lower() for _, path, _ in api.requests)


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_endpoint_rejects_write_methods(kube, stub_kubeconfig, method):
    stub_kubeconfig(kube.url)

    response = getattr(client, method)(URL)

    assert response.status_code == 405
    assert kube.requests == []
