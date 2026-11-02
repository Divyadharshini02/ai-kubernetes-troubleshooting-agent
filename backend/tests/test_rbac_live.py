"""What the backend's identity can and cannot do, in the real cluster. Run with: pytest -m cluster

Nothing is destroyed to prove a denial. Permissions are asked about with `kubectl auth can-i`,
and writes attempted with the real token use a server-side dry run, which changes nothing.
"""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import investigations as storage
from lab_harness import LAB_CONTEXT, admin_kubeconfig, run_kubectl

pytestmark = pytest.mark.cluster

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "k8s" / "rbac" / "troubleshooting-agent.yaml"
NAMESPACE = "troubleshooting-agent"
IDENTITY = f"system:serviceaccount:{NAMESPACE}:troubleshooting-agent"
UNBOUND = "troubleshooting-agent-unbound"
client = TestClient(app)

spec = importlib.util.spec_from_file_location("new_readonly_kubeconfig", REPO / "scripts" / "new_readonly_kubeconfig.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

ALLOWED = [
    ("list", "pods", "--all-namespaces"),
    ("list", "pods", "-n", "sample-app"),
    ("get", "pods", "--subresource=log", "-n", "sample-app"),
    ("list", "events", "-n", "sample-app"),
    ("list", "deployments.apps", "-n", "sample-app"),
    ("list", "replicasets.apps", "-n", "sample-app"),
    ("list", "services", "-n", "sample-app"),
    ("list", "endpointslices.discovery.k8s.io", "-n", "sample-app"),
    ("list", "persistentvolumeclaims", "-n", "sample-app"),
    ("list", "serviceaccounts", "-n", "sample-app"),
    ("get", "namespaces/sample-app"),
    ("list", "nodes"),
    ("list", "storageclasses.storage.k8s.io"),
]

DENIED = [
    # Secrets and configuration.
    ("get", "secrets", "-n", "sample-app"),
    ("list", "secrets", "--all-namespaces"),
    ("watch", "secrets", "--all-namespaces"),
    ("list", "configmaps", "-n", "sample-app"),
    # Reaching inside a workload.
    ("create", "pods", "--subresource=exec", "-n", "sample-app"),
    ("create", "pods", "--subresource=attach", "-n", "sample-app"),
    ("create", "pods", "--subresource=portforward", "-n", "sample-app"),
    ("update", "pods", "--subresource=ephemeralcontainers", "-n", "sample-app"),
    ("get", "nodes", "--subresource=proxy"),
    # Writes.
    ("create", "pods", "-n", "sample-app"),
    ("delete", "pods", "-n", "sample-app"),
    ("update", "pods", "-n", "sample-app"),
    ("patch", "pods", "-n", "sample-app"),
    ("create", "deployments.apps", "-n", "sample-app"),
    ("update", "deployments.apps", "-n", "sample-app"),
    ("patch", "deployments.apps", "-n", "sample-app"),
    ("delete", "deployments.apps", "-n", "sample-app"),
    ("patch", "deployments.apps", "--subresource=scale", "-n", "sample-app"),
    ("create", "services", "-n", "sample-app"),
    ("delete", "services", "-n", "sample-app"),
    ("create", "events", "-n", "sample-app"),
    ("delete", "persistentvolumeclaims", "-n", "sample-app"),
    ("create", "namespaces"),
    ("delete", "namespaces/sample-app"),
    ("delete", "namespaces/failure-demo"),
    ("patch", "nodes"),
    ("delete", "nodes"),
    # Gaining more access.
    ("create", "serviceaccounts", "--subresource=token", "-n", NAMESPACE),
    ("impersonate", "users"),
    ("impersonate", "serviceaccounts"),
    ("bind", "clusterroles.rbac.authorization.k8s.io"),
    ("escalate", "clusterroles.rbac.authorization.k8s.io"),
    ("create", "clusterrolebindings.rbac.authorization.k8s.io"),
    ("create", "rolebindings.rbac.authorization.k8s.io", "-n", "sample-app"),
    # Beyond what the code uses, even though these only read.
    ("watch", "pods", "--all-namespaces"),
    ("get", "pods", "-n", "sample-app"),
    ("list", "namespaces"),
]


def can_i(*args):
    """Ask the API server, as the administrator impersonating the backend's identity."""
    result = subprocess.run(
        ["kubectl", "--kubeconfig", admin_kubeconfig(), "--context", LAB_CONTEXT, "auth", "can-i", *args, f"--as={IDENTITY}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout.strip()


@pytest.fixture(scope="module")
def rbac():
    run_kubectl("apply", "-f", str(MANIFEST))


@pytest.fixture(scope="module")
def restricted_file(rbac, tmp_path_factory):
    output = tmp_path_factory.mktemp("restricted") / "backend.kubeconfig"
    script.create_kubeconfig(output, Path(admin_kubeconfig()))
    return output


@pytest.fixture
def restricted(restricted_file, tmp_path, monkeypatch):
    """The backend runs as the restricted identity, with a temporary database."""
    monkeypatch.setenv("KUBECONFIG", str(restricted_file))
    monkeypatch.delenv("KUBERNETES_ALLOW_DEFAULT_KUBECONFIG", raising=False)
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "history.db")
    return restricted_file


def as_backend(kubeconfig, *args):
    return subprocess.run(["kubectl", "--kubeconfig", str(kubeconfig), *args], capture_output=True, text=True, timeout=60)


def investigate(namespace):
    response = client.post("/api/investigations", json={"namespace": namespace})
    assert response.status_code == 202, response.text
    return client.get(f"/api/investigations/{response.json()['id']}").json()


# What it may do.


@pytest.mark.parametrize("check", ALLOWED, ids=" ".join)
def test_required_read_is_allowed(rbac, check):
    assert can_i(*check) == "yes"


@pytest.mark.parametrize("check", DENIED, ids=" ".join)
def test_everything_else_is_denied(rbac, check):
    assert can_i(*check) == "no"


def test_cluster_grants_exactly_what_the_manifest_lists(rbac):
    """Read back from the cluster, so a later edit to the live role would be noticed."""
    role = json.loads(run_kubectl("get", "clusterrole", "troubleshooting-agent-reader", "-o", "json"))
    binding = json.loads(run_kubectl("get", "clusterrolebinding", "troubleshooting-agent-reader", "-o", "json"))

    rules = {
        (group, resource): set(rule["verbs"])
        for rule in role["rules"]
        for group in rule["apiGroups"]
        for resource in rule["resources"]
    }
    assert all(rule["verbs"] and set(rule["verbs"]) <= {"get", "list"} for rule in role["rules"])
    assert len(rules) == 12
    assert binding["roleRef"]["name"] == "troubleshooting-agent-reader"
    assert [subject["name"] for subject in binding["subjects"]] == ["troubleshooting-agent"]


def test_no_long_lived_token_secret_exists(rbac):
    secrets = json.loads(run_kubectl("get", "secrets", "-n", NAMESPACE, "-o", "json"))["items"]

    assert secrets == []


# The real token against the real API server.


@pytest.mark.parametrize(
    "command",
    [
        ("delete", "deployment", "sample-web", "-n", "sample-app", "--dry-run=server"),
        ("delete", "namespace", "sample-app", "--dry-run=server"),
        ("create", "deployment", "denied", "--image=none", "-n", "sample-app", "--dry-run=server"),
        ("scale", "deployment", "sample-web", "--replicas=0", "-n", "sample-app", "--dry-run=server"),
        ("get", "secrets", "-n", "sample-app"),
        ("get", "configmaps", "-n", "sample-app"),
        ("create", "token", "default", "-n", "sample-app"),
    ],
    ids=" ".join,
)
def test_real_token_is_denied_by_the_api_server(restricted, command):
    result = as_backend(restricted, *command)

    assert result.returncode != 0
    assert "Forbidden" in result.stderr or "forbidden" in result.stderr


def test_denied_writes_left_sample_app_unchanged(restricted):
    deployment = json.loads(run_kubectl("get", "deployment", "sample-web", "-n", "sample-app", "-o", "json"))

    assert deployment["spec"]["replicas"] == 1


def test_real_token_can_read_what_the_backend_needs(restricted):
    result = as_backend(restricted, "get", "pods", "--all-namespaces", "-o", "name")

    assert result.returncode == 0
    assert "pod/sample-web-" in result.stdout


def test_restricted_file_holds_no_administrator_credentials(restricted):
    saved = json.loads(restricted.read_text())

    assert set(saved["users"][0]["user"]) == {"token"}
    assert set(saved["clusters"][0]["cluster"]) == {"server", "certificate-authority-data"}


# The application with restricted credentials.


def test_dashboard_data_works_with_restricted_credentials(restricted):
    response = client.get("/api/cluster/health")

    assert response.status_code == 200
    assert response.json()["context"] == LAB_CONTEXT
    assert response.json()["nodes"]
    assert any(pod["namespace"] == "kube-system" for pod in response.json()["pods"])


def test_full_investigation_works_with_restricted_credentials(restricted):
    investigation = investigate("sample-app")

    assert investigation["status"] == "completed"
    assert investigation["outcome"] == "no_problems_found"
    records = investigation["report"]["evidence"]["records"]
    assert investigation["report"]["evidence_completeness"] == "complete"
    assert not [record["evidence_id"] for record in records if record["status"] in ("forbidden", "failed")]
    assert {"pod", "event", "service", "node", "storageclass", "serviceaccount", "deployment"} <= {r["category"] for r in records}


def test_missing_namespace_is_still_reported_as_missing(restricted):
    assert client.get("/api/namespaces/no-such-namespace/diagnosis").status_code == 404
    assert investigate("no-such-namespace")["error"]["code"] == "namespace_not_found"


def test_token_never_reaches_responses_or_history(restricted, tmp_path):
    token = json.loads(restricted.read_text())["users"][0]["user"]["token"]

    investigation = investigate("sample-app")
    health = client.get("/api/cluster/health")

    assert token not in json.dumps(investigation)
    assert token not in health.text
    assert token.encode() not in (tmp_path / "history.db").read_bytes()


# Credentials that do not work.


def with_token(restricted_file, tmp_path, token):
    document = json.loads(restricted_file.read_text())
    document["users"][0]["user"]["token"] = token
    path = tmp_path / "other.kubeconfig"
    path.write_text(json.dumps(document))
    return path


def test_invalid_token_fails_safely(restricted, tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", str(with_token(restricted, tmp_path, "not-a-real-token")))

    response = client.get("/api/cluster/health")
    investigation = investigate("sample-app")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "cluster_access_denied"
    assert "not-a-real-token" not in response.text
    assert investigation["status"] == "failed"
    assert investigation["error"]["code"] == "cluster_access_denied"


def test_service_account_without_permissions_is_denied_by_real_rbac(restricted, tmp_path, monkeypatch):
    run_kubectl("create", "serviceaccount", UNBOUND, "-n", NAMESPACE)
    try:
        token = run_kubectl("create", "token", UNBOUND, "-n", NAMESPACE, "--duration=10m").strip()
        monkeypatch.setenv("KUBECONFIG", str(with_token(restricted, tmp_path, token)))

        assert client.get("/api/cluster/health").json()["detail"]["code"] == "cluster_access_denied"
        assert investigate("sample-app")["error"]["code"] == "cluster_access_denied"
    finally:
        run_kubectl("delete", "serviceaccount", UNBOUND, "-n", NAMESPACE, "--ignore-not-found")


def test_forbidden_collectors_are_reported_with_real_rbac_not_called_healthy(restricted, tmp_path, monkeypatch):
    # The diagnosis route has no connectivity check, so each collector meets real RBAC itself.
    run_kubectl("create", "serviceaccount", UNBOUND, "-n", NAMESPACE)
    try:
        token = run_kubectl("create", "token", UNBOUND, "-n", NAMESPACE, "--duration=10m").strip()
        monkeypatch.setenv("KUBECONFIG", str(with_token(restricted, tmp_path, token)))

        report = client.get("/api/namespaces/sample-app/diagnosis")

        assert report.status_code == 200
        assert report.json()["outcome"] == "insufficient_evidence"
        assert report.json()["evidence_completeness"] == "partial"
        assert token not in report.text
    finally:
        run_kubectl("delete", "serviceaccount", UNBOUND, "-n", NAMESPACE, "--ignore-not-found")


def test_explicit_development_setting_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", admin_kubeconfig())
    monkeypatch.setenv("KUBERNETES_ALLOW_DEFAULT_KUBECONFIG", "true")
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "history.db")

    assert client.get("/api/cluster/health").status_code == 200
