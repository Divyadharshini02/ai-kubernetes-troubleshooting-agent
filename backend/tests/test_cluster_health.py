"""Cluster endpoint tests that need no real cluster.

Connection failures are real: a missing file, a damaged file, a closed port and
a silent socket. A local stub HTTP server stands in for the Kubernetes API only
where a test needs a specific API response.
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.kubernetes import cluster
from app.main import app

client = TestClient(app)
SECRET_TOKEN = "test-only-secret-token-value"

VERSION = {
    "buildDate": "2026-01-01T00:00:00Z",
    "compiler": "gc",
    "gitCommit": "abc",
    "gitTreeState": "clean",
    "gitVersion": "v9.9.9-stub",
    "goVersion": "go1",
    "major": "9",
    "minor": "9",
    "platform": "linux/amd64",
}


def make_node(name, conditions):
    return {
        "metadata": {"name": name},
        "status": {
            "conditions": conditions,
            "nodeInfo": {
                "architecture": "amd64",
                "bootID": "b",
                "containerRuntimeVersion": "c",
                "kernelVersion": "k",
                "kubeProxyVersion": "p",
                "kubeletVersion": "v9.9.9-stub",
                "machineID": "m",
                "operatingSystem": "linux",
                "osImage": "o",
                "systemUUID": "s",
            },
        },
    }


def make_container_status(name, ready, restarts):
    return {
        "name": name,
        "image": "i",
        "imageID": "i",
        "ready": ready,
        "restartCount": restarts,
    }


def node_list(items, continue_token=None):
    metadata = {"continue": continue_token} if continue_token else {}
    return {"kind": "NodeList", "apiVersion": "v1", "metadata": metadata, "items": items}


def pod_list(items, continue_token=None):
    metadata = {"continue": continue_token} if continue_token else {}
    return {"kind": "PodList", "apiVersion": "v1", "metadata": metadata, "items": items}


READY_NODE = make_node("node-ready", [{"type": "Ready", "status": "True"}])
RUNNING_POD = {
    "metadata": {"namespace": "ns-a", "name": "pod-a"},
    "spec": {"nodeName": "node-ready", "containers": [{"name": "one"}, {"name": "two"}]},
    "status": {
        "phase": "Running",
        "containerStatuses": [
            make_container_status("one", True, 2),
            make_container_status("two", False, 3),
        ],
    },
}


def healthy_routes():
    return {
        "/version": (200, VERSION),
        "/api/v1/nodes": (200, node_list([READY_NODE])),
        "/api/v1/pods": (200, pod_list([RUNNING_POD])),
    }


@pytest.fixture
def stub_api():
    """Start a local HTTP server that answers from a route table and records requests."""
    state = {"routes": healthy_routes(), "methods": []}

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            state["methods"].append(self.command)
            path = urlparse(self.path).path.rstrip("/")
            status, body = state["routes"].get(path, (404, {"kind": "Status"}))
            payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def use_kubeconfig(tmp_path, monkeypatch):
    """Point the backend at a temporary kubeconfig for the given API server URL."""

    def _use(server_url):
        path = tmp_path / "kubeconfig"
        path.write_text(
            json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Config",
                    "current-context": "test-context",
                    "clusters": [{"name": "c", "cluster": {"server": server_url}}],
                    "users": [{"name": "u", "user": {"token": SECRET_TOKEN}}],
                    "contexts": [{"name": "test-context", "context": {"cluster": "c", "user": "u"}}],
                }
            )
        )
        monkeypatch.setenv("KUBECONFIG", str(path))

    return _use


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert SECRET_TOKEN not in response.text


def test_reports_exactly_what_the_api_returned(stub_api, use_kubeconfig):
    use_kubeconfig(stub_api["url"])

    response = client.get("/api/cluster/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "connected"
    assert body["context"] == "test-context"
    assert body["server_version"] == "v9.9.9-stub"
    assert body["truncated"] is False
    assert body["collected_at"]
    assert body["nodes"] == [
        {"name": "node-ready", "ready": True, "kubelet_version": "v9.9.9-stub"}
    ]
    assert body["pods"] == [
        {
            "namespace": "ns-a",
            "name": "pod-a",
            "phase": "Running",
            "node": "node-ready",
            "ready_containers": 1,
            "total_containers": 2,
            "restarts": 5,
        }
    ]


@pytest.mark.parametrize(
    ("conditions", "expected"),
    [
        ([{"type": "Ready", "status": "True"}], True),
        ([{"type": "Ready", "status": "False"}], False),
        ([{"type": "Ready", "status": "Unknown"}], None),
        ([{"type": "MemoryPressure", "status": "False"}], None),
    ],
)
def test_node_readiness_is_not_guessed(stub_api, use_kubeconfig, conditions, expected):
    use_kubeconfig(stub_api["url"])
    stub_api["routes"]["/api/v1/nodes"] = (200, node_list([make_node("n", conditions)]))

    response = client.get("/api/cluster/health")

    assert response.status_code == 200
    assert response.json()["nodes"][0]["ready"] is expected


def test_pending_pod_without_container_statuses(stub_api, use_kubeconfig):
    use_kubeconfig(stub_api["url"])
    pending = {
        "metadata": {"namespace": "ns", "name": "waiting"},
        "spec": {"containers": [{"name": "one"}]},
        "status": {"phase": "Pending"},
    }
    stub_api["routes"]["/api/v1/pods"] = (200, pod_list([pending]))

    response = client.get("/api/cluster/health")

    assert response.status_code == 200
    assert response.json()["pods"] == [
        {
            "namespace": "ns",
            "name": "waiting",
            "phase": "Pending",
            "node": None,
            "ready_containers": 0,
            "total_containers": 1,
            "restarts": 0,
        }
    ]


def test_empty_cluster_is_reported_as_empty(stub_api, use_kubeconfig):
    use_kubeconfig(stub_api["url"])
    stub_api["routes"]["/api/v1/nodes"] = (200, node_list([]))
    stub_api["routes"]["/api/v1/pods"] = (200, pod_list([]))

    response = client.get("/api/cluster/health")

    assert response.status_code == 200
    assert response.json()["nodes"] == []
    assert response.json()["pods"] == []


def test_partial_results_are_flagged_as_truncated(stub_api, use_kubeconfig):
    use_kubeconfig(stub_api["url"])
    stub_api["routes"]["/api/v1/pods"] = (200, pod_list([RUNNING_POD], continue_token="more"))

    response = client.get("/api/cluster/health")

    assert response.status_code == 200
    assert response.json()["truncated"] is True


def test_endpoint_only_sends_get_requests(stub_api, use_kubeconfig):
    use_kubeconfig(stub_api["url"])

    assert client.get("/api/cluster/health").status_code == 200

    assert stub_api["methods"]
    assert set(stub_api["methods"]) == {"GET"}


def test_missing_kubeconfig_returns_503(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "does-not-exist"))

    response = client.get("/api/cluster/health")

    assert_error(response, 503, "kubeconfig_unavailable")


@pytest.mark.parametrize(
    "content",
    ["{ this is not valid: [", "just a string", "apiVersion: v1\nkind: Config\n"],
)
def test_invalid_kubeconfig_returns_503(tmp_path, monkeypatch, content):
    path = tmp_path / "kubeconfig"
    path.write_text(content)
    monkeypatch.setenv("KUBECONFIG", str(path))

    response = client.get("/api/cluster/health")

    assert_error(response, 503, "kubeconfig_unavailable")


def test_closed_port_returns_503(use_kubeconfig):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    use_kubeconfig(f"http://127.0.0.1:{closed_port}")

    response = client.get("/api/cluster/health")

    assert_error(response, 503, "cluster_unreachable")


def test_silent_server_times_out_with_503(use_kubeconfig, monkeypatch):
    monkeypatch.setattr(cluster, "REQUEST_TIMEOUT_SECONDS", (1, 1))
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        use_kubeconfig(f"http://127.0.0.1:{silent.getsockname()[1]}")

        response = client.get("/api/cluster/health")

    assert_error(response, 503, "cluster_unreachable")


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_credentials_return_502(stub_api, use_kubeconfig, status):
    use_kubeconfig(stub_api["url"])
    stub_api["routes"]["/api/v1/nodes"] = (status, {"kind": "Status", "code": status})

    response = client.get("/api/cluster/health")

    assert_error(response, 502, "cluster_access_denied")


def test_failed_pod_listing_never_returns_partial_success(stub_api, use_kubeconfig):
    use_kubeconfig(stub_api["url"])
    stub_api["routes"]["/api/v1/pods"] = (
        500,
        {"kind": "Status", "message": f"leaked {SECRET_TOKEN}"},
    )

    response = client.get("/api/cluster/health")

    assert_error(response, 502, "cluster_api_error")
    assert "nodes" not in response.json()


@pytest.mark.parametrize(
    "body",
    ["this is not json", {"kind": "NodeList", "items": [{"metadata": {"name": "n"}, "spec": 5}]}],
)
def test_unexpected_response_returns_502(stub_api, use_kubeconfig, body):
    use_kubeconfig(stub_api["url"])
    stub_api["routes"]["/api/v1/nodes"] = (200, body)

    response = client.get("/api/cluster/health")

    assert_error(response, 502, "cluster_api_error")


def test_failure_logs_do_not_contain_secrets(stub_api, use_kubeconfig, caplog):
    use_kubeconfig(stub_api["url"])
    stub_api["routes"]["/api/v1/nodes"] = (500, {"message": f"leaked {SECRET_TOKEN}"})

    client.get("/api/cluster/health")

    assert SECRET_TOKEN not in caplog.text
