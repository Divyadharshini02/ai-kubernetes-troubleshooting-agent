"""The monitoring files: real queries only, the metrics they use exist, and nothing new is exposed."""

import json
import re
from pathlib import Path

import pytest
import yaml
from prometheus_client import REGISTRY

from app.main import app  # noqa: F401  (imported so that every metric is registered)

ROOT = Path(__file__).resolve().parents[2]
MONITORING = ROOT / "monitoring"
DASHBOARD = MONITORING / "grafana" / "dashboards" / "troubleshooting-agent.json"
FORBIDDEN_LABELS = ("namespace", "pod", "investigation_id", "user", "log")
ALERTS = {"BackendDown", "BackendErrorsHigh", "ClusterAccessFailing"}


def load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dashboard():
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def compose():
    return load_yaml(ROOT / "docker-compose.yml")


@pytest.fixture(scope="module")
def rules():
    return load_yaml(MONITORING / "prometheus" / "alerts.yml")["groups"][0]["rules"]


def targets_of(dashboard):
    return [target for panel in dashboard["panels"] for target in panel["targets"]]


def known_metric_names():
    # From the definitions, since a labelled metric has no samples until it is first used.
    suffixes = ("_total", "_bucket", "_count", "_sum")
    return {f"{family.name}{suffix}" for family in REGISTRY.collect() for suffix in suffixes}


def used_metric_names(text):
    return set(re.findall(r"troubleshooting_[a-z_]+", text))


# Prometheus.


def test_prometheus_scrapes_the_backend_over_the_compose_network():
    config = load_yaml(MONITORING / "prometheus" / "prometheus.yml")
    jobs = {job["job_name"]: job for job in config["scrape_configs"]}

    assert jobs["backend"]["static_configs"][0]["targets"] == ["backend:8000"]
    assert jobs["backend"].get("metrics_path", "/metrics") == "/metrics"
    assert config["global"]["scrape_interval"] == "15s"
    assert "alerting" not in config


def test_exactly_the_agreed_alert_rules_exist(rules):
    assert {rule["alert"] for rule in rules} == ALERTS
    assert all(rule["annotations"]["summary"] and rule["annotations"]["description"] for rule in rules)


def test_alerts_only_use_metrics_that_exist(rules):
    used = set().union(*(used_metric_names(rule["expr"]) for rule in rules))

    assert used
    assert used <= known_metric_names()


def test_a_mistyped_namespace_cannot_raise_the_cluster_alert(rules):
    expression = next(rule["expr"] for rule in rules if rule["alert"] == "ClusterAccessFailing")

    assert "namespace_not_found" not in expression
    assert "cluster_access_denied" in expression


def test_alert_series_exist_at_zero_so_that_a_first_burst_can_be_seen(rules):
    # Found live: a series first seen at 6 looks like no increase. Labelled HTTP series start that way.
    expressions = " ".join(rule["expr"] for rule in rules)

    assert "troubleshooting_http_requests_total" not in expressions
    assert REGISTRY.get_sample_value("troubleshooting_http_server_errors_total") is not None
    assert REGISTRY.get_sample_value("troubleshooting_investigation_failures_total", {"code": "cluster_access_denied"}) is not None


def test_every_alert_has_a_rule_test():
    tests = (MONITORING / "prometheus" / "alerts.test.yml").read_text(encoding="utf-8")

    for name in ALERTS:
        assert f"alertname: {name}" in tests


# Grafana.


def test_one_dashboard_with_the_agreed_panels(dashboard):
    assert list(DASHBOARD.parent.glob("*.json")) == [DASHBOARD]
    assert len(dashboard["panels"]) == 12
    assert dashboard["uid"] == "troubleshooting-agent"
    assert dashboard["editable"] is False


def test_every_panel_queries_the_provisioned_prometheus(dashboard):
    uid = load_yaml(MONITORING / "grafana" / "provisioning" / "datasources" / "prometheus.yml")["datasources"][0]["uid"]

    for panel in dashboard["panels"]:
        assert panel["targets"], panel["title"]
        assert panel["datasource"] == {"type": "prometheus", "uid": uid}
        for target in panel["targets"]:
            assert target["datasource"]["uid"] == uid
            assert target["expr"].strip()


def test_no_panel_shows_a_hardcoded_value(dashboard):
    for panel in dashboard["panels"]:
        assert panel["type"] in {"stat", "timeseries", "bargauge"}
        assert "content" not in panel["options"]
        assert "testdata" not in json.dumps(panel).lower()
        for target in panel["targets"]:
            assert "vector(" not in target["expr"]
            assert re.search(r"[a-z_]+[{\[(]", target["expr"])
            assert "rawSql" not in target


def test_dashboard_only_uses_metrics_that_exist(dashboard):
    used = set().union(*(used_metric_names(target["expr"]) for target in targets_of(dashboard)))

    assert len(used) >= 8
    assert used <= known_metric_names()


def test_dashboard_never_groups_by_a_forbidden_label(dashboard):
    for target in targets_of(dashboard):
        for label in FORBIDDEN_LABELS:
            assert not re.search(rf"\b{label}\s*[=!~,)]", target["expr"]), target["expr"]


def test_traffic_panels_leave_out_docker_health_checks(dashboard):
    traffic = next(panel for panel in dashboard["panels"] if panel["title"] == "Requests and 5xx rate")

    assert 'route!="/api/health"' in traffic["targets"][0]["expr"]


def test_dashboard_loader_points_at_the_mounted_folder(compose):
    provider = load_yaml(MONITORING / "grafana" / "provisioning" / "dashboards" / "dashboards.yml")["providers"][0]

    assert provider["allowUiUpdates"] is False
    assert any(volume.endswith(f":{provider['options']['path']}:ro") for volume in compose["services"]["grafana"]["volumes"])


# Compose: separate by default, private, limited and pinned.


def test_monitoring_starts_only_with_its_profile(compose):
    assert compose["services"]["prometheus"]["profiles"] == ["monitoring"]
    assert compose["services"]["grafana"]["profiles"] == ["monitoring"]
    assert "profiles" not in compose["services"]["backend"]
    assert "profiles" not in compose["services"]["frontend"]


@pytest.mark.parametrize("service, published", [("prometheus", "127.0.0.1:9091:9090"), ("grafana", "127.0.0.1:3001:3000")])
def test_monitoring_ports_are_loopback_only(compose, service, published):
    assert compose["services"][service]["ports"] == [published]


@pytest.mark.parametrize("service", ["prometheus", "grafana"])
def test_monitoring_has_no_route_to_the_cluster_docker_or_credentials(compose, service):
    definition = compose["services"][service]
    text = json.dumps(definition)

    assert definition["networks"] == ["app"]
    assert "docker.sock" not in text
    assert ".kube" not in text
    assert "KUBECONFIG" not in text
    assert "privileged" not in definition


@pytest.mark.parametrize("service", ["prometheus", "grafana"])
def test_monitoring_is_limited_and_pinned(compose, service):
    definition = compose["services"][service]

    assert definition["mem_limit"]
    assert definition["cpus"]
    assert re.search(r"@sha256:[0-9a-f]{64}$", definition["image"])
    assert definition["cap_drop"] == ["ALL"]
    assert definition["security_opt"] == ["no-new-privileges:true"]


def test_prometheus_keeps_a_bounded_amount_of_data(compose):
    command = compose["services"]["prometheus"]["command"]

    assert "--storage.tsdb.retention.time=7d" in command
    assert "--storage.tsdb.retention.size=256MB" in command
    assert not [option for option in command if "enable-lifecycle" in option or "enable-admin-api" in option]


def test_grafana_login_comes_from_a_secret_file_outside_the_repository(compose):
    grafana = compose["services"]["grafana"]
    environment = grafana["environment"]

    assert environment["GF_SECURITY_ADMIN_PASSWORD__FILE"] == "/run/secrets/grafana_admin_password"
    assert "GF_SECURITY_ADMIN_PASSWORD" not in environment
    assert environment["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert environment["GF_USERS_ALLOW_SIGN_UP"] == "false"
    assert compose["secrets"]["grafana_admin_password"]["file"].startswith("${USERPROFILE")
    assert ".troubleshooting-monitoring" in compose["secrets"]["grafana_admin_password"]["file"]


def test_the_backend_stays_unpublished_and_monitoring_did_not_change_the_application(compose):
    backend = compose["services"]["backend"]
    frontend = compose["services"]["frontend"]

    assert "ports" not in backend
    assert backend["networks"] == ["app", "kind"]
    assert backend["mem_limit"] == "384m"
    assert frontend["ports"] == ["127.0.0.1:8088:8080"]


def test_nginx_still_proxies_only_the_api():
    config = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")

    assert "metrics" not in config
    proxied = re.findall(r"location\s+([^\s{]+)\s*\{[^}]*proxy_pass", config)
    assert proxied == ["/api/"]


# Jenkins.


def test_jenkins_validates_monitoring_and_keeps_every_earlier_gate():
    jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")

    for stage in (
        "Backend lint",
        "Backend tests",
        "Frontend lint",
        "Frontend build",
        "Validate manifests",
        "Validate monitoring",
        "Build images",
        "Smoke test",
        "Trivy",
    ):
        assert f"stage('{stage}')" in jenkinsfile
    assert "promtool" in jenkinsfile
    assert "check config" in jenkinsfile
    assert "test rules" in jenkinsfile
    assert re.search(r"PROMETHEUS_IMAGE = 'prom/prometheus:[^']+@sha256:[0-9a-f]{64}'", jenkinsfile)


def test_smoke_test_checks_metrics_and_that_nginx_does_not_serve_them():
    smoke = (ROOT / "ci" / "smoke-test.sh").read_text(encoding="utf-8")

    assert "/metrics" in smoke
    assert "troubleshooting_http_requests_total" in smoke
