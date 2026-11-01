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
URL = f"/api/namespaces/{NAMESPACE}/evidence"
PODS = f"/api/v1/namespaces/{NAMESPACE}/pods"


def test_existing_empty_namespace_is_complete_and_explicit(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())

    response = client.get(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["namespace"] == NAMESPACE
    assert body["context"] == "stub-context"
    assert body["completeness"] == "complete"
    assert body["started_at"] <= body["completed_at"]
    assert body["status_counts"] == {"collected": 1, "empty": 11}
    ids = [record["evidence_id"] for record in body["records"]]
    assert ids[0] == f"namespace:{NAMESPACE}"
    assert "pods:none" in ids
    assert "events:none" in ids
    assert "nodes:none" in ids


def test_every_record_has_identity_time_source_and_status(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())
    kube.routes[PODS] = (200, list_of("PodList", [make_pod(statuses=[container_status("app")])]))

    records = client.get(URL).json()["records"]

    for record in records:
        assert record["evidence_id"]
        assert record["resource_kind"]
        assert record["collected_at"]
        assert record["source"]
        assert record["status"]
        assert record["temporality"] in ("current", "historical")
    ids = [record["evidence_id"] for record in records]
    assert len(ids) == len(set(ids))


def test_logs_are_collected_from_pod_records(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())
    pod = make_pod(statuses=[container_status("app", ready=False)])
    kube.routes[PODS] = (200, list_of("PodList", [pod]))
    kube.routes[pod_log_path("web")] = (200, b"2026-01-01T00:00:00Z a log line\n")

    records = {r["evidence_id"]: r for r in client.get(URL).json()["records"]}

    assert "a log line" in records[f"logs:{NAMESPACE}/web/app:current"]["data"]["text"]


def test_failed_collector_makes_bundle_partial_and_keeps_the_rest(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())
    kube.routes[PODS] = (403, {"kind": "Status", "message": f"leak {SECRET_VALUE}"})

    response = client.get(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["completeness"] == "partial"
    assert body["status_counts"]["forbidden"] == 1
    records = {r["evidence_id"]: r for r in body["records"]}
    assert records["pods:request"]["status"] == "forbidden"
    assert records["nodes:none"]["status"] == "empty"
    assert SECRET_VALUE not in response.text


def test_forbidden_namespace_read_still_collects_the_rest(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())
    kube.routes[f"/api/v1/namespaces/{NAMESPACE}"] = (403, {"kind": "Status"})

    body = client.get(URL).json()

    assert body["completeness"] == "partial"
    assert body["records"][0]["status"] == "forbidden"
    assert len(body["records"]) == 12


def test_collector_defect_is_isolated(kube, stub_kubeconfig, monkeypatch):
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())

    def broken(_ctx):
        raise RuntimeError(f"defect {SECRET_VALUE}")

    monkeypatch.setattr(evidence_service, "collect_events", broken)

    response = client.get(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["completeness"] == "partial"
    records = {r["evidence_id"]: r for r in body["records"]}
    assert records["events:collector"]["status"] == "failed"
    assert records["events:collector"]["error"]["code"] == "collector_error"
    assert records["nodes:none"]["status"] == "empty"
    assert SECRET_VALUE not in response.text


def test_time_limit_stops_new_work_and_says_so(kube, stub_kubeconfig, monkeypatch):
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())
    monkeypatch.setattr(evidence_service, "COLLECTION_DEADLINE_SECONDS", -1)

    body = client.get(URL).json()

    assert body["completeness"] == "partial"
    assert body["status_counts"] == {"collected": 1, "skipped": 11}
    assert body["records"][1]["error"]["code"] == "deadline_exceeded"


def test_missing_namespace_returns_404(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes[f"/api/v1/namespaces/{NAMESPACE}"] = (404, {"kind": "Status"})

    response = client.get(URL)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "namespace_not_found"
    assert len(kube.requests) == 1


@pytest.mark.parametrize(
    "name",
    ["UPPER", "has_underscore", "-leading", "trailing-", "a" * 64, "a.b"],
)
def test_invalid_namespace_names_are_rejected_without_a_cluster_call(kube, stub_kubeconfig, name):
    stub_kubeconfig(kube.url)

    response = client.get(f"/api/namespaces/{name}/evidence")

    assert response.status_code == 422
    assert kube.requests == []


def test_missing_kubeconfig_returns_503(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "missing"))

    response = client.get(URL)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "kubeconfig_unavailable"


def test_unreachable_cluster_returns_503_and_stops_quickly(stub_kubeconfig):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    stub_kubeconfig(f"http://127.0.0.1:{closed_port}")

    response = client.get(URL)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cluster_unreachable"
    assert SECRET_VALUE not in response.text


def test_collection_is_read_only_and_never_touches_secrets(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())
    pod = make_pod(statuses=[container_status("app", ready=False, restarts=1)])
    kube.routes[PODS] = (200, list_of("PodList", [pod]))
    kube.routes[pod_log_path("web")] = (200, b"2026-01-01T00:00:00Z x\n")

    assert client.get(URL).status_code == 200

    assert kube.requests
    assert {method for method, _, _ in kube.requests} == {"GET"}
    assert not any("secret" in path.lower() for _, path, _ in kube.requests)


def test_kubeconfig_token_never_appears_in_a_bundle(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes.update(namespace_routes())

    assert SECRET_VALUE not in client.get(URL).text
