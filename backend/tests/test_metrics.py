"""Metric definitions: fixed labels, series that exist from the start, and recording that cannot raise."""

import re
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from app.observability import metrics

APP = Path(metrics.__file__).resolve().parents[1]
ALLOWED_LABELS = {"method", "route", "status", "outcome", "code", "scenario", "category", "reason", "le"}
FORBIDDEN_LABELS = {"namespace", "pod", "pod_name", "investigation_id", "id", "user", "message", "log", "model", "path"}
REQUIRED_METRICS = {
    "troubleshooting_investigations_total",
    "troubleshooting_investigation_failures_total",
    "troubleshooting_investigation_duration_seconds_count",
    "troubleshooting_diagnoses_total",
    "troubleshooting_recovery_verifications_total",
    "troubleshooting_evidence_collector_failures_total",
    "troubleshooting_llm_request_duration_seconds_count",
    "troubleshooting_llm_request_errors_total",
    "troubleshooting_llm_explanations_total",
}


def project_samples():
    return [
        sample
        for family in REGISTRY.collect()
        for sample in family.samples
        if sample.name.startswith(f"{metrics.PREFIX}_")
    ]


def value(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0


def test_all_required_metrics_exist_before_any_activity():
    assert REQUIRED_METRICS <= {sample.name for sample in project_samples()}


def test_only_fixed_labels_are_used():
    used = {label for sample in project_samples() for label in sample.labels}

    assert used <= ALLOWED_LABELS
    assert not used & FORBIDDEN_LABELS


def test_counters_have_no_creation_time_series():
    assert not [sample.name for sample in project_samples() if sample.name.endswith("_created")]


def test_known_series_start_so_that_a_first_increase_can_be_seen():
    assert REGISTRY.get_sample_value(
        "troubleshooting_investigations_total", {"status": "completed", "outcome": "no_problems_found"}
    ) is not None
    assert REGISTRY.get_sample_value("troubleshooting_investigation_failures_total", {"code": "namespace_not_found"}) is not None
    assert REGISTRY.get_sample_value("troubleshooting_recovery_verifications_total", {"status": "recovered"}) is not None
    assert REGISTRY.get_sample_value(
        "troubleshooting_diagnoses_total", {"scenario": "probe_failure", "status": "problem_detected"}
    ) is not None
    assert REGISTRY.get_sample_value("troubleshooting_llm_explanations_total", {"status": "unavailable", "reason": "disabled"}) is not None


def test_total_series_stay_small():
    assert len(project_samples()) < 1000


def test_every_failure_code_the_application_raises_is_listed():
    source = (APP / "kubernetes" / "cluster.py").read_text(encoding="utf-8")
    raised = set(re.findall(r'ClusterAccessError\(\s*"([a-z_]+)"', source))

    assert raised
    assert raised <= set(metrics.FAILURE_CODES)
    assert {"namespace_not_found", "unexpected_error"} <= set(metrics.FAILURE_CODES)


def test_every_evidence_category_the_collectors_use_is_listed():
    found = set()
    for path in (APP / "collectors").glob("*.py"):
        found |= set(re.findall(r'category="([a-z_]+)"', path.read_text(encoding="utf-8")))
    orchestrator = (APP / "services" / "evidence.py").read_text(encoding="utf-8")
    found |= set(re.findall(r'\("[a-z]+", "([a-z_]+)", ', orchestrator))

    assert len(found) >= 10
    assert found <= set(metrics.EVIDENCE_CATEGORIES)


def test_an_unknown_failure_code_cannot_create_a_new_series():
    before = value("troubleshooting_investigation_failures_total", code=metrics.OTHER)

    metrics.record_investigation_failed("value-from-somewhere-unexpected", 1)

    assert value("troubleshooting_investigation_failures_total", code=metrics.OTHER) == before + 1
    assert not [s for s in project_samples() if s.labels.get("code") == "value-from-somewhere-unexpected"]


def test_an_unknown_llm_reason_cannot_create_a_new_series():
    metrics.record_llm_request(0.1, "provider said something strange")

    assert not [s for s in project_samples() if s.labels.get("reason") == "provider said something strange"]


def test_an_unusual_http_method_cannot_create_a_new_series():
    before = value("troubleshooting_http_requests_total", method="OTHER", route="unmatched", status="405")

    metrics.record_http_request("MADE-UP", "unmatched", 405, 0.01)

    assert value("troubleshooting_http_requests_total", method="OTHER", route="unmatched", status="405") == before + 1


def test_the_server_error_counter_exists_before_any_request_and_counts_only_5xx():
    errors = "troubleshooting_http_server_errors_total"
    before = REGISTRY.get_sample_value(errors)

    assert before is not None
    metrics.record_http_request("GET", "unmatched", 404, 0.01)
    metrics.record_http_request("GET", "unmatched", 499, 0.01)
    assert REGISTRY.get_sample_value(errors) == before
    metrics.record_http_request("GET", "unmatched", 502, 0.01)
    metrics.record_http_request("GET", "unmatched", 503, 0.01)
    assert REGISTRY.get_sample_value(errors) == before + 2


@pytest.mark.parametrize(
    "call",
    [
        lambda: metrics.record_http_request("GET", "unmatched", 200, 0.01),
        lambda: metrics.record_investigation_failed("unexpected_error", 1),
        lambda: metrics.record_llm_request(0.1, "timeout"),
        lambda: metrics.record_llm_explanation("generated", None),
    ],
)
def test_recording_never_raises(call, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("metrics are broken")

    for metric in (metrics.HTTP_REQUESTS, metrics.INVESTIGATIONS, metrics.LLM_DURATION, metrics.LLM_EXPLANATIONS):
        monkeypatch.setattr(metric, "labels", broken)
    monkeypatch.setattr(metrics.LLM_DURATION, "observe", broken)

    call()


def test_a_recording_fault_logs_only_the_error_type(monkeypatch, caplog):
    def broken(*_args, **_kwargs):
        raise RuntimeError("detail that must not be logged")

    monkeypatch.setattr(metrics.INVESTIGATIONS, "labels", broken)

    metrics.record_investigation_failed("unexpected_error", 1)

    assert "RuntimeError" in caplog.text
    assert "detail that must not be logged" not in caplog.text
