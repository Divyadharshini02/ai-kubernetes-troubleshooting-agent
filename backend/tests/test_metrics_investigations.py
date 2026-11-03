"""Investigation and LLM metrics, through real collectors, a stub cluster and a temporary database.

Each result must be counted exactly once, and only after it was saved. SQLite is the source of truth.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.agent import analysis, explainer
from app.main import app
from app.observability import metrics
from app.services import investigations as service
from app.storage import investigations as storage
from llm_factory import COMPLETIONS_PATH, completion, valid_rca
from stub_kube import NAMESPACE, SECRET_VALUE, container_status, list_of, make_pod, namespace_routes, pod_log_path
from test_cluster_health import VERSION

client = TestClient(app)
URL = "/api/investigations"
PODS = f"/api/v1/namespaces/{NAMESPACE}/pods"
INVESTIGATIONS = "troubleshooting_investigations_total"
FAILURES = "troubleshooting_investigation_failures_total"
DURATION_COUNT = "troubleshooting_investigation_duration_seconds_count"
DIAGNOSES = "troubleshooting_diagnoses_total"
RECOVERY = "troubleshooting_recovery_verifications_total"
EVIDENCE = "troubleshooting_evidence_collector_failures_total"
LLM_COUNT = "troubleshooting_llm_request_duration_seconds_count"
LLM_ERRORS = "troubleshooting_llm_request_errors_total"
LLM_EXPLANATIONS = "troubleshooting_llm_explanations_total"
AI_ANALYSES = "troubleshooting_ai_analyses_total"


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    path = tmp_path / "data" / "history.db"
    monkeypatch.setattr(storage, "DATABASE_PATH", path)
    return path


@pytest.fixture
def cluster(kube, stub_kubeconfig):
    """A stub cluster with one crashing pod, which logs a password."""
    stub_kubeconfig(kube.url)
    pod = make_pod(
        "web",
        statuses=[container_status("app", ready=False, restarts=4, state={"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}})],
    )
    kube.routes.update(namespace_routes())
    kube.routes["/version"] = (200, VERSION)
    kube.routes["/api/v1/pods"] = (200, list_of("PodList", [pod]))
    kube.routes[PODS] = (200, list_of("PodList", [pod]))
    kube.routes[pod_log_path("web")] = (200, f"2026-01-01T00:00:00Z fatal password={SECRET_VALUE}\n".encode())
    return kube


def fix(kube):
    pod = make_pod("web-fixed", statuses=[container_status("app")])
    kube.routes[PODS] = (200, list_of("PodList", [pod]))
    kube.routes[pod_log_path("web-fixed")] = (200, b"2026-01-01T00:00:00Z started\n")


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0


class Counts:
    """Everything an investigation records, so a test can also prove what did not change."""

    def __init__(self):
        self.before = self.read()

    @staticmethod
    def read():
        return {
            (family_sample.name, tuple(sorted(family_sample.labels.items()))): family_sample.value
            for family in REGISTRY.collect()
            for family_sample in family.samples
            if family_sample.name.startswith(
                (
                    "troubleshooting_investigation",
                    "troubleshooting_diagnoses",
                    "troubleshooting_recovery",
                    "troubleshooting_evidence",
                    "troubleshooting_llm",
                    "troubleshooting_ai",
                )
            )
            and "_bucket" not in family_sample.name
            and "_sum" not in family_sample.name
        }

    def changes(self):
        after = self.read()
        return {key: after[key] - self.before.get(key, 0) for key in after if after[key] != self.before.get(key, 0)}


def key(name, **labels):
    return name, tuple(sorted(labels.items()))


def investigate(namespace=NAMESPACE, **fields):
    response = client.post(URL, json={"namespace": namespace, **fields})
    assert response.status_code == 202, response.text
    return client.get(f"{URL}/{response.json()['id']}").json()


# Exactly once.


def test_a_completed_investigation_changes_exactly_the_expected_series(cluster):
    counts = Counts()

    investigation = investigate()

    assert investigation["status"] == "completed"
    assert counts.changes() == {
        key(INVESTIGATIONS, status="completed", outcome="problems_found"): 1,
        key(DURATION_COUNT, status="completed"): 1,
        key(DIAGNOSES, scenario="crash_loop_backoff", status="problem_detected"): 1,
        key(RECOVERY, status="not_verified"): 1,
    }


def test_a_missing_namespace_is_one_failure_and_no_completion(cluster):
    cluster.routes[f"/api/v1/namespaces/{NAMESPACE}"] = (404, {"kind": "Status"})
    counts = Counts()

    investigation = investigate()

    assert investigation["error"]["code"] == "namespace_not_found"
    assert counts.changes() == {
        key(INVESTIGATIONS, status="failed", outcome="none"): 1,
        key(FAILURES, code="namespace_not_found"): 1,
        key(DURATION_COUNT, status="failed"): 1,
    }


def test_missing_credentials_are_one_failure_with_their_code(monkeypatch, tmp_path):
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "missing"))
    counts = Counts()

    investigation = investigate()

    assert investigation["error"]["code"] == "kubeconfig_unavailable"
    assert counts.changes() == {
        key(INVESTIGATIONS, status="failed", outcome="none"): 1,
        key(FAILURES, code="kubeconfig_unavailable"): 1,
        key(DURATION_COUNT, status="failed"): 1,
    }


def test_rejected_credentials_are_one_failure_with_their_code(cluster):
    cluster.routes["/version"] = (401, {"kind": "Status", "code": 401})
    counts = Counts()

    investigation = investigate()

    assert investigation["error"]["code"] == "cluster_access_denied"
    assert counts.changes()[key(FAILURES, code="cluster_access_denied")] == 1


# Saving comes first.


def test_a_report_that_cannot_be_saved_is_never_counted_as_completed(cluster, monkeypatch):
    def cannot_save(*_args):
        raise storage.StorageError("OperationalError")

    monkeypatch.setattr(storage, "complete_investigation", cannot_save)
    counts = Counts()

    investigation = investigate()

    # The failure itself could be saved, so it is one failed investigation and nothing else.
    assert investigation["status"] == "failed"
    assert investigation["error"]["code"] == "unexpected_error"
    assert counts.changes() == {
        key(INVESTIGATIONS, status="failed", outcome="none"): 1,
        key(FAILURES, code="unexpected_error"): 1,
        key(DURATION_COUNT, status="failed"): 1,
    }


def test_nothing_is_counted_when_neither_the_result_nor_the_failure_can_be_saved(cluster, monkeypatch):
    def cannot_save(*_args):
        raise storage.StorageError("OperationalError")

    monkeypatch.setattr(storage, "complete_investigation", cannot_save)
    monkeypatch.setattr(storage, "fail_investigation", cannot_save)
    counts = Counts()

    investigation = investigate()

    assert investigation["status"] == "running"
    assert counts.changes() == {}


def test_a_failure_marked_at_restart_is_not_counted_as_a_new_result(cluster):
    storage.create_investigation("cut-off", NAMESPACE, False, "Created.")
    counts = Counts()

    with TestClient(app) as restarted:
        assert restarted.get(f"{URL}/cut-off").json()["error"]["code"] == "interrupted"

    assert counts.changes() == {}


# A fault in monitoring.


def test_a_metrics_fault_cannot_turn_a_saved_report_into_a_failure(cluster, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("metrics are broken")

    monkeypatch.setattr(metrics.INVESTIGATIONS, "labels", broken)

    investigation = investigate()

    assert investigation["status"] == "completed"
    assert investigation["error"] is None


def test_a_metrics_fault_cannot_change_a_saved_failure(monkeypatch, tmp_path):
    def broken(*_args, **_kwargs):
        raise RuntimeError("metrics are broken")

    monkeypatch.setattr(metrics.INVESTIGATIONS, "labels", broken)
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "missing"))

    investigation = investigate()

    assert investigation["status"] == "failed"
    assert investigation["error"]["code"] == "kubeconfig_unavailable"


# Recovery and evidence.


def test_recovery_status_is_recorded_from_the_saved_comparison(cluster):
    investigate()
    counts = Counts()

    investigation = investigate()

    assert investigation["verification_status"] == "still_failing"
    assert counts.changes()[key(RECOVERY, status="still_failing")] == 1
    assert key(RECOVERY, status="not_verified") not in counts.changes()


def test_recovery_is_recorded_when_a_problem_is_gone(cluster):
    investigate()
    fix(cluster)
    counts = Counts()

    investigation = investigate()

    assert investigation["verification_status"] == "recovered"
    changes = counts.changes()
    assert changes[key(RECOVERY, status="recovered")] == 1
    assert changes[key(INVESTIGATIONS, status="completed", outcome="no_problems_found")] == 1


def test_evidence_that_could_not_be_collected_is_counted_by_category(cluster):
    path = f"/api/v1/namespaces/{NAMESPACE}/services"
    cluster.routes[path] = (403, {"kind": "Status", "code": 403, "reason": "Forbidden"})
    counts = Counts()

    investigation = investigate()

    assert investigation["status"] == "completed"
    assert counts.changes()[key(EVIDENCE, category="service", status="forbidden")] == 1


# Agreement with SQLite.


def test_counts_agree_with_the_saved_history(cluster, database):
    counts = Counts()

    investigate()
    investigate()
    fix(cluster)
    investigate()
    assert investigate("no-such-namespace-at-all")["status"] == "failed"

    changes = counts.changes()
    with sqlite3.connect(database) as connection:
        saved = dict(
            connection.execute("SELECT outcome, COUNT(*) FROM investigations WHERE status = 'completed' GROUP BY outcome").fetchall()
        )
        failed = dict(
            connection.execute("SELECT error_code, COUNT(*) FROM investigations WHERE status = 'failed' GROUP BY error_code").fetchall()
        )
        verification = dict(
            connection.execute(
                "SELECT verification_status, COUNT(*) FROM investigations WHERE status = 'completed' GROUP BY verification_status"
            ).fetchall()
        )
        diagnoses = connection.execute("SELECT SUM(diagnosis_count) FROM investigations").fetchone()[0]

    assert sum(saved.values()) == 3
    for outcome, number in saved.items():
        assert changes[key(INVESTIGATIONS, status="completed", outcome=outcome)] == number
    for code, number in failed.items():
        assert changes[key(FAILURES, code=code)] == number
    assert changes[key(INVESTIGATIONS, status="failed", outcome="none")] == sum(failed.values())
    for status, number in verification.items():
        assert changes[key(RECOVERY, status=status)] == number
    assert sum(value for (name, _), value in changes.items() if name == DIAGNOSES) == diagnoses


# Privacy.


def test_metrics_contain_no_names_identifiers_or_secrets(cluster):
    investigation = investigate()
    text = client.get("/metrics").text

    assert SECRET_VALUE not in text
    assert investigation["id"] not in text
    assert f'"{NAMESPACE}"' not in text
    assert '"web"' not in text
    assert "password" not in text
    assert 'namespace="' not in text
    assert 'pod="' not in text


# The optional LLM. Everything here uses the local stand-in provider.


def test_a_disabled_llm_is_one_fallback_and_no_provider_attempt(cluster):
    counts = Counts()

    investigation = investigate(explain=True)

    assert investigation["ai_reason"] == "disabled"
    changes = counts.changes()
    assert changes[key(AI_ANALYSES, status="unavailable", reason="disabled")] == 1
    assert key(LLM_COUNT) not in changes


def test_no_explanation_request_records_nothing_about_the_llm(cluster):
    counts = Counts()

    investigate()

    assert not [name for (name, _labels) in counts.changes() if name.startswith("troubleshooting_llm")]


def test_a_generated_analysis_is_one_attempt_and_no_fallback(cluster, llm):
    cited = [r["evidence_id"] for r in investigate()["report"]["diagnoses"][0]["evidence_references"]]
    llm.routes[COMPLETIONS_PATH] = (200, completion(valid_rca(cited)))
    counts = Counts()

    investigation = investigate(explain=True)

    assert investigation["ai_status"] == "generated"
    changes = counts.changes()
    assert changes[key(LLM_COUNT)] == 1
    assert changes[key(AI_ANALYSES, status="generated", reason="none")] == 1
    assert not [name for (name, _labels) in changes if name in (LLM_ERRORS, LLM_EXPLANATIONS)]


def test_a_failed_analysis_is_one_attempt_and_one_fallback(cluster, llm, monkeypatch):
    # Retries are allowed in the settings, but an analysis makes one request and then falls back.
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setattr(explainer, "RETRY_DELAY_SECONDS", 0)
    llm.routes[COMPLETIONS_PATH] = (500, {"error": {"code": 500, "message": "provider detail"}})
    counts = Counts()

    investigation = investigate(explain=True)

    assert investigation["ai_reason"] == "provider_error"
    assert len(llm.requests) == 1
    changes = counts.changes()
    assert changes[key(LLM_COUNT)] == 1
    assert changes[key(LLM_ERRORS, reason="provider_error")] == 1
    assert changes[key(AI_ANALYSES, status="unavailable", reason="provider_error")] == 1
    # The investigation itself still completed, and only once.
    assert changes[key(INVESTIGATIONS, status="completed", outcome="problems_found")] == 1


def test_a_rejected_answer_is_a_fallback_but_not_a_failed_request(cluster, llm):
    llm.routes[COMPLETIONS_PATH] = (200, completion("this is not the expected JSON"))
    counts = Counts()

    investigation = investigate(explain=True)

    assert investigation["ai_status"] == "unavailable"
    changes = counts.changes()
    assert changes[key(LLM_COUNT)] == 1
    assert not [name for (name, _labels) in changes if name == LLM_ERRORS]
    assert changes[key(AI_ANALYSES, status="unavailable", reason=investigation["ai_reason"])] == 1


def test_the_diagnosis_endpoint_counts_its_explanation_once_and_no_investigation(cluster):
    counts = Counts()

    response = client.get(f"/api/namespaces/{NAMESPACE}/diagnosis", params={"explain": "true"})

    assert response.status_code == 200
    assert counts.changes() == {key(LLM_EXPLANATIONS, status="unavailable", reason="disabled"): 1}


def test_an_unexpected_analysis_fault_is_one_fallback(cluster, monkeypatch):
    def broken(*_args):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(analysis, "_analyze", broken)
    counts = Counts()

    investigation = investigate(explain=True)

    assert investigation["status"] == "completed"
    assert counts.changes()[key(AI_ANALYSES, status="unavailable", reason="unexpected_error")] == 1


def test_run_investigation_itself_records_once(cluster):
    summary = service.start_investigation(NAMESPACE, False)
    before = sample(INVESTIGATIONS, status="completed", outcome="problems_found")

    service.run_investigation(summary.id, NAMESPACE, False)

    assert sample(INVESTIGATIONS, status="completed", outcome="problems_found") == before + 1
