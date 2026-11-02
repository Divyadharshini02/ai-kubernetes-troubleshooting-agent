"""The RBAC manifest, the credential rules and the kubeconfig script. No cluster is needed."""

import importlib.util
import json
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from app.kubernetes import cluster
from app.main import app
from app.storage import investigations as storage
from lab_harness import ADMIN_KUBECONFIG, LAB_CONTEXT, LabSafetyError, admin_kubeconfig, current_context, run_kubectl

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "k8s" / "rbac" / "troubleshooting-agent.yaml"
client = TestClient(app)

# (API group, resource): verbs. Written from the calls in app/collectors and app/kubernetes.
REQUIRED = {
    ("", "events"): {"list"},
    ("", "nodes"): {"list"},
    ("", "persistentvolumeclaims"): {"list"},
    ("", "pods"): {"list"},
    ("", "serviceaccounts"): {"list"},
    ("", "services"): {"list"},
    ("", "namespaces"): {"get"},
    ("", "pods/log"): {"get"},
    ("apps", "deployments"): {"list"},
    ("apps", "replicasets"): {"list"},
    ("discovery.k8s.io", "endpointslices"): {"list"},
    ("storage.k8s.io", "storageclasses"): {"list"},
}


def documents():
    return {document["kind"]: document for document in yaml.safe_load_all(MANIFEST.read_text())}


def granted():
    rules = {}
    for rule in documents()["ClusterRole"]["rules"]:
        for group in rule["apiGroups"]:
            for resource in rule["resources"]:
                rules.setdefault((group, resource), set()).update(rule["verbs"])
    return rules


# The manifest.


def test_manifest_holds_only_the_expected_kinds():
    assert set(documents()) == {"Namespace", "ServiceAccount", "ClusterRole", "ClusterRoleBinding"}


def test_grants_exactly_what_the_code_uses():
    assert granted() == REQUIRED


def test_only_read_verbs_and_no_wildcards():
    for rule in documents()["ClusterRole"]["rules"]:
        assert set(rule) == {"apiGroups", "resources", "verbs"}
        assert set(rule["verbs"]) <= {"get", "list"}
        assert "*" not in [*rule["apiGroups"], *rule["resources"], *rule["verbs"]]


@pytest.mark.parametrize(
    "forbidden",
    ["secrets", "configmaps", "pods/exec", "pods/attach", "pods/portforward", "pods/ephemeralcontainers", "serviceaccounts/token", "nodes/proxy", "clusterroles", "clusterrolebindings"],
)
def test_sensitive_resources_are_not_granted(forbidden):
    assert forbidden not in {resource for _, resource in granted()}


def test_binding_connects_only_our_service_account_to_our_role():
    binding = documents()["ClusterRoleBinding"]

    assert binding["roleRef"]["kind"] == "ClusterRole"
    assert binding["roleRef"]["name"] == documents()["ClusterRole"]["metadata"]["name"]
    assert binding["roleRef"]["name"] != "cluster-admin"
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "troubleshooting-agent", "namespace": "troubleshooting-agent"}]


def test_service_account_is_dedicated_and_mounts_no_token():
    account = documents()["ServiceAccount"]

    assert account["metadata"]["name"] != "default"
    assert account["automountServiceAccountToken"] is False


def test_no_credential_is_stored_in_the_manifest():
    assert "Secret" not in documents()
    assert "token:" not in MANIFEST.read_text()


# No silent administrator fallback.


@pytest.fixture
def no_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.delenv(cluster.ALLOW_DEFAULT_KUBECONFIG, raising=False)
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "history.db")


@pytest.fixture
def real_loader_must_not_run(monkeypatch):
    def fail(*_args, **_kwargs):
        pytest.fail("The kubeconfig must not be read")

    monkeypatch.setattr(cluster.config, "load_kube_config", fail)


@pytest.mark.parametrize("path", ["/api/cluster/health", "/api/namespaces/sample-app/evidence", "/api/namespaces/sample-app/diagnosis"])
def test_missing_credentials_fail_with_a_clear_error_and_read_nothing(no_credentials, real_loader_must_not_run, path):
    response = client.get(path)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "kubeconfig_unavailable"
    assert "docs/M7_RBAC_SECURITY.md" in response.json()["detail"]["message"]


def test_missing_credentials_are_a_saved_investigation_failure(no_credentials, real_loader_must_not_run):
    body = client.post("/api/investigations", json={"namespace": "sample-app"}).json()

    saved = client.get(f"/api/investigations/{body['id']}").json()
    assert saved["status"] == "failed"
    assert saved["error"]["code"] == "kubeconfig_unavailable"


@pytest.mark.parametrize("variant", ["config", f".{cluster.os.sep}config", "other{sep}config"])
def test_kubeconfig_naming_the_default_file_is_refused(no_credentials, real_loader_must_not_run, monkeypatch, tmp_path, variant):
    home = tmp_path / "home"
    (home / ".kube").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(home / ".kube")
    monkeypatch.setenv("KUBECONFIG", variant.format(sep=cluster.os.pathsep))

    response = client.get("/api/cluster/health")

    assert response.status_code == 503
    assert "administrator" in response.json()["detail"]["message"]


@pytest.mark.parametrize("value", ["", "false", "1", "yes", "on", "truthy"])
def test_only_the_exact_setting_allows_the_default_file(no_credentials, real_loader_must_not_run, monkeypatch, value):
    monkeypatch.setenv(cluster.ALLOW_DEFAULT_KUBECONFIG, value)

    assert client.get("/api/cluster/health").status_code == 503


def test_explicit_setting_allows_the_default_file(no_credentials, monkeypatch):
    calls = []

    def load(**kwargs):
        calls.append(kwargs["config_file"])

    monkeypatch.setenv(cluster.ALLOW_DEFAULT_KUBECONFIG, "true")
    monkeypatch.setattr(cluster.config, "load_kube_config", load)
    monkeypatch.setattr(cluster.config, "list_kube_config_contexts", lambda **_: ([], {"name": "dev"}))

    configuration, context = cluster.load_configuration()

    assert calls == [None]
    assert context == "dev"
    assert configuration is not None


def test_startup_warns_only_when_the_default_file_is_allowed(no_credentials, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass
    assert cluster.ALLOW_DEFAULT_KUBECONFIG not in caplog.text

    monkeypatch.setenv(cluster.ALLOW_DEFAULT_KUBECONFIG, "true")
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass
    assert cluster.ALLOW_DEFAULT_KUBECONFIG in caplog.text


def test_invalid_restricted_kubeconfig_fails_safely_without_quoting_it(no_credentials, monkeypatch, tmp_path):
    damaged = tmp_path / "restricted.kubeconfig"
    damaged.write_text("token: secret-looking-value\nnot valid: [")
    monkeypatch.setenv("KUBECONFIG", str(damaged))

    response = client.get("/api/cluster/health")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "kubeconfig_unavailable"
    assert "secret-looking-value" not in response.text


# The test harnesses' identity.


def test_harness_refuses_to_write_without_an_explicit_admin_kubeconfig(monkeypatch):
    monkeypatch.delenv(ADMIN_KUBECONFIG, raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("kubectl must not run"))

    with pytest.raises(LabSafetyError):
        run_kubectl("delete", "namespace", "failure-demo")
    with pytest.raises(LabSafetyError):
        current_context()


def test_harness_refuses_the_backends_kubeconfig(monkeypatch, tmp_path):
    same = tmp_path / "same"
    monkeypatch.setenv(ADMIN_KUBECONFIG, str(same))
    monkeypatch.setenv("KUBECONFIG", str(same))

    with pytest.raises(LabSafetyError):
        admin_kubeconfig()


def test_harness_commands_name_the_admin_kubeconfig_and_context(monkeypatch):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="")

    monkeypatch.setenv(ADMIN_KUBECONFIG, "admin-file")
    monkeypatch.setenv("KUBECONFIG", "restricted-file")
    monkeypatch.setattr(subprocess, "run", fake_run)

    run_kubectl("apply", "-f", "scenario.yaml")
    current_context()

    assert calls[0][:5] == ["kubectl", "--kubeconfig", "admin-file", "--context", LAB_CONTEXT]
    assert calls[1][:3] == ["kubectl", "--kubeconfig", "admin-file"]
    assert not any("restricted-file" in part for call in calls for part in call)


# The kubeconfig script.

spec = importlib.util.spec_from_file_location("new_readonly_kubeconfig", REPO / "scripts" / "new_readonly_kubeconfig.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

TOKEN = "fake-service-account-token-value"
ADMIN_KEY = "fake-admin-client-key-data"
ADMIN_CERT = "fake-admin-client-certificate-data"


class FakeKubectl:
    def __init__(self, fail=(), can=None, token=TOKEN):
        self.fail = fail
        self.can = {"pods": "yes", "secrets": "no", "delete": "no", **(can or {})}
        self.token = token
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        for part in self.fail:
            if part in args:
                return SimpleNamespace(returncode=1, stdout="", stderr=f"error mentioning {TOKEN}")
        return SimpleNamespace(returncode=0, stdout=self._stdout(args), stderr="")

    def _stdout(self, args):
        if "get-contexts" in args:
            return f"other\n{LAB_CONTEXT}\n"
        if "view" in args:
            return json.dumps(
                {
                    "clusters": [{"cluster": {"server": "https://127.0.0.1:1234", "certificate-authority-data": "public-ca"}}],
                    "users": [{"user": {"client-key-data": ADMIN_KEY, "client-certificate-data": ADMIN_CERT}}],
                }
            )
        if "token" in args:
            return json.dumps({"status": {"token": self.token, "expirationTimestamp": "2026-11-02T20:00:00Z"}})
        if "can-i" in args:
            check = "delete" if "delete" in args else "secrets" if "secrets" in args else "pods"
            return self.can[check] + "\n"
        return "found\n"


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "out" / "restricted.kubeconfig", tmp_path / "admin"


def test_writes_only_public_cluster_data_and_the_token(paths):
    output, admin = paths

    expires_at = script.create_kubeconfig(output, admin, FakeKubectl())

    saved = json.loads(output.read_text())
    assert expires_at == "2026-11-02T20:00:00Z"
    assert saved["users"] == [{"name": "troubleshooting-agent", "user": {"token": TOKEN}}]
    assert saved["clusters"][0]["cluster"] == {"server": "https://127.0.0.1:1234", "certificate-authority-data": "public-ca"}
    assert saved["current-context"] == LAB_CONTEXT
    assert ADMIN_KEY not in output.read_text()
    assert ADMIN_CERT not in output.read_text()


def test_requests_an_8_hour_token_for_the_service_account_using_the_admin_file(paths):
    output, admin = paths
    kubectl = FakeKubectl()

    script.create_kubeconfig(output, admin, kubectl)

    token_call = next(call for call in kubectl.calls if "token" in call)
    assert "--duration=8h" in token_call
    assert token_call[:4] == ("--kubeconfig", str(admin), "--context", LAB_CONTEXT)
    assert ("create", "token", "troubleshooting-agent", "-n", "troubleshooting-agent") == token_call[4:9]


def test_the_token_is_never_printed(paths, monkeypatch, capsys):
    output, admin = paths
    real = script.create_kubeconfig
    monkeypatch.setattr(script, "create_kubeconfig", lambda o, a: real(o, a, FakeKubectl()))

    assert script.main(["--output", str(output), "--admin-kubeconfig", str(admin)]) == 0

    printed = capsys.readouterr()
    assert TOKEN not in printed.out + printed.err
    assert str(output) in printed.out


@pytest.mark.parametrize(
    ("fail", "message"),
    [
        (("get-contexts",), "Context"),
        (("serviceaccount",), "ServiceAccount"),
        (("clusterrolebinding",), "ClusterRoleBinding"),
        (("view",), "address"),
        (("token",), "token"),
    ],
)
def test_a_failed_check_stops_writes_nothing_and_never_quotes_output(paths, monkeypatch, capsys, fail, message):
    output, admin = paths
    real = script.create_kubeconfig
    monkeypatch.setattr(script, "create_kubeconfig", lambda o, a: real(o, a, FakeKubectl(fail=fail)))

    assert script.main(["--output", str(output), "--admin-kubeconfig", str(admin)]) == 1

    printed = capsys.readouterr()
    assert message in printed.err
    assert TOKEN not in printed.err
    assert not output.exists()


def test_wrong_context_requests_no_token(paths):
    output, admin = paths
    kubectl = FakeKubectl()
    original = kubectl._stdout
    kubectl._stdout = lambda args: "only-another-cluster\n" if "get-contexts" in args else original(args)

    with pytest.raises(script.ScriptError):
        script.create_kubeconfig(output, admin, kubectl)

    assert not any("token" in call for call in kubectl.calls)


@pytest.mark.parametrize("can", [{"secrets": "yes"}, {"delete": "yes"}, {"pods": "no"}])
def test_token_with_the_wrong_access_is_not_saved_and_leaves_no_file(paths, can):
    output, admin = paths

    with pytest.raises(script.ScriptError):
        script.create_kubeconfig(output, admin, FakeKubectl(can=can))

    assert list(output.parent.iterdir()) == []


def test_failed_renewal_keeps_the_working_file(paths):
    output, admin = paths
    script.create_kubeconfig(output, admin, FakeKubectl())
    working = output.read_text()

    with pytest.raises(script.ScriptError):
        script.create_kubeconfig(output, admin, FakeKubectl(can={"secrets": "yes"}, token="another-token"))

    assert output.read_text() == working


def test_renewal_replaces_the_file_and_leaves_nothing_else(paths):
    output, admin = paths
    script.create_kubeconfig(output, admin, FakeKubectl())

    script.create_kubeconfig(output, admin, FakeKubectl(token="renewed-token"))

    assert "renewed-token" in output.read_text()
    assert [path.name for path in output.parent.iterdir()] == [output.name]


@pytest.mark.parametrize("target", ["inside.kubeconfig", "backend/data/inside.kubeconfig"])
def test_refuses_to_write_inside_the_repository(tmp_path, target):
    kubectl = FakeKubectl()

    with pytest.raises(script.ScriptError):
        script.create_kubeconfig(REPO / target, tmp_path / "admin", kubectl)

    assert kubectl.calls == []
    assert not (REPO / target).exists()


def test_refuses_to_overwrite_the_admin_kubeconfig(tmp_path):
    kubectl = FakeKubectl()
    admin = tmp_path / "admin"
    admin.write_text("admin credentials")

    with pytest.raises(script.ScriptError):
        script.create_kubeconfig(admin, admin, kubectl)

    assert admin.read_text() == "admin credentials"
    assert kubectl.calls == []


def test_default_mode_keeps_the_address_this_machine_can_reach(paths):
    output, admin = paths

    script.create_kubeconfig(output, admin, FakeKubectl())

    assert json.loads(output.read_text())["clusters"][0]["cluster"]["server"] == "https://127.0.0.1:1234"


def test_container_mode_saves_the_internal_address_but_verifies_through_the_reachable_one(paths):
    output, admin = paths
    verified_through = []
    kubectl = FakeKubectl()

    def run(*args):
        if "can-i" in args:
            verified_through.append(json.loads(Path(args[1]).read_text())["clusters"][0]["cluster"]["server"])
        return kubectl(*args)

    script.create_kubeconfig(output, admin, run, saved_server=script.CONTAINER_SERVER)

    saved = json.loads(output.read_text())
    assert set(verified_through) == {"https://127.0.0.1:1234"}
    assert saved["clusters"][0]["cluster"] == {"server": script.CONTAINER_SERVER, "certificate-authority-data": "public-ca"}
    assert saved["users"] == [{"name": "troubleshooting-agent", "user": {"token": TOKEN}}]
    assert ADMIN_KEY not in output.read_text()
    assert [path.name for path in output.parent.iterdir()] == [output.name]


def test_container_mode_keeps_every_other_guard(paths):
    output, admin = paths

    with pytest.raises(script.ScriptError):
        script.create_kubeconfig(output, admin, FakeKubectl(can={"secrets": "yes"}), saved_server=script.CONTAINER_SERVER)

    assert list(output.parent.iterdir()) == []


def test_container_mode_refuses_the_folder_of_the_admin_kubeconfig(tmp_path):
    # That folder is mounted whole, which would hand the administrator's file to the container.
    kubectl = FakeKubectl()
    admin = tmp_path / "kube" / "config"
    admin.parent.mkdir()

    with pytest.raises(script.ScriptError):
        script.create_kubeconfig(admin.parent / "kubeconfig", admin, kubectl, saved_server=script.CONTAINER_SERVER)

    assert kubectl.calls == []
    assert list(admin.parent.iterdir()) == []


def test_container_mode_refuses_the_repository(tmp_path):
    with pytest.raises(script.ScriptError):
        script.create_kubeconfig(REPO / "kubeconfig", tmp_path / "admin", FakeKubectl(), saved_server=script.CONTAINER_SERVER)


@pytest.mark.parametrize(
    ("flags", "expected_server"),
    [([], None), (["--container"], script.CONTAINER_SERVER)],
)
def test_the_command_line_selects_the_mode(paths, monkeypatch, capsys, flags, expected_server):
    output, admin = paths
    real = script.create_kubeconfig
    monkeypatch.setattr(script, "create_kubeconfig", lambda o, a, **options: real(o, a, FakeKubectl(), **options))

    assert script.main(["--output", str(output), "--admin-kubeconfig", str(admin), *flags]) == 0

    server = json.loads(output.read_text())["clusters"][0]["cluster"]["server"]
    assert server == (expected_server or "https://127.0.0.1:1234")
    assert TOKEN not in capsys.readouterr().out


def test_container_default_output_is_alone_in_its_folder_and_outside_the_repository():
    assert script.DEFAULT_CONTAINER_OUTPUT.parent != script.DEFAULT_ADMIN.parent
    assert script.DEFAULT_CONTAINER_OUTPUT.parent != script.DEFAULT_OUTPUT.parent
    assert not script.DEFAULT_CONTAINER_OUTPUT.resolve().is_relative_to(REPO)


def test_missing_kubectl_is_a_clean_error(paths, monkeypatch, capsys):
    output, admin = paths

    def no_kubectl(*_args):
        raise FileNotFoundError("kubectl")

    monkeypatch.setattr(script, "create_kubeconfig", lambda _output, _admin: no_kubectl())

    assert script.main(["--output", str(output), "--admin-kubeconfig", str(admin)]) == 1
    assert "kubectl was not found" in capsys.readouterr().err


def test_default_output_is_outside_the_repository_and_git_ignores_the_extension():
    assert not script.DEFAULT_OUTPUT.resolve().is_relative_to(REPO)
    assert script.DEFAULT_OUTPUT.suffix == ".kubeconfig"
    assert "*.kubeconfig" in (REPO / ".gitignore").read_text()
