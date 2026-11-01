"""Regression tests for the client's status 0 errors.

The client raises ApiException with status 0 for TLS failures and for unsupported
content types. Neither may ever be reported as "HTTP 0".

The TLS tests use real handshake failures: they speak HTTPS to a plain HTTP server.
"""

import pytest
from fastapi.testclient import TestClient
from kubernetes import client as kubernetes_client

from app.collectors.base import CollectorContext
from app.collectors.nodes import collect_nodes
from app.main import app
from stub_kube import NAMESPACE, SECRET_VALUE

client = TestClient(app)
CLUSTER_URL = "/api/cluster/health"
EVIDENCE_URL = f"/api/namespaces/{NAMESPACE}/evidence"


def https_url(kube):
    return kube.url.replace("http://", "https://")


@pytest.mark.parametrize("url", [CLUSTER_URL, EVIDENCE_URL])
def test_tls_failure_is_a_connection_error_on_both_endpoints(kube, stub_kubeconfig, url):
    stub_kubeconfig(https_url(kube))

    response = client.get(url)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cluster_unreachable"
    assert "HTTP 0" not in response.text
    assert SECRET_VALUE not in response.text


def test_tls_failure_detail_is_not_exposed(kube, stub_kubeconfig):
    stub_kubeconfig(https_url(kube))

    body = client.get(CLUSTER_URL).text

    assert "SSL" not in body
    assert "certificate" not in body.lower()
    assert "127.0.0.1" not in body


def test_tls_failure_logs_do_not_expose_detail(kube, stub_kubeconfig, caplog):
    stub_kubeconfig(https_url(kube))

    client.get(CLUSTER_URL)

    assert SECRET_VALUE not in caplog.text
    assert "HTTP 0" not in caplog.text
    assert "status 0" not in caplog.text


def test_unsupported_content_type_is_not_called_a_connection_error(kube, stub_kubeconfig):
    stub_kubeconfig(kube.url)
    kube.routes["/version"] = (200, "<html>a proxy page</html>", "application/xml")

    response = client.get(CLUSTER_URL)

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "cluster_api_error"
    assert "HTTP 0" not in response.text


def test_collector_records_tls_failure_as_unreachable(kube):
    configuration = kubernetes_client.Configuration(host=https_url(kube))
    configuration.retries = False
    with kubernetes_client.ApiClient(configuration) as api_client:
        ctx = CollectorContext(api_client=api_client, namespace=NAMESPACE, deadline=float("inf"))

        records = collect_nodes(ctx)

    assert [record.status for record in records] == ["failed"]
    assert records[0].error.code == "api_unreachable"
    assert "SSL" not in records[0].model_dump_json()


def test_collector_records_unsupported_content_type_as_unexpected(ctx, kube):
    kube.routes["/api/v1/nodes"] = (200, "<html>a proxy page</html>", "application/xml")

    records = collect_nodes(ctx)

    assert [record.status for record in records] == ["failed"]
    assert records[0].error.code == "unexpected_response"
    assert "HTTP 0" not in records[0].model_dump_json()
