"""Prometheus metrics. Every label value comes from a fixed list, so series cannot grow without limit.

Namespaces, pod names, investigation IDs and log text are never labels. Recording can never raise,
because a fault in monitoring must not change the result of an investigation.
"""

import logging
from collections.abc import Callable
from typing import get_args

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, disable_created_metrics, generate_latest

from app.models.diagnosis import DiagnosisStatus, DiagnosticReport, ReportOutcome, Scenario
from app.models.evidence import INCOMPLETE_STATUSES
from app.models.explanation import ExplanationReason
from app.models.investigation import VerificationStatus

logger = logging.getLogger(__name__)

# Each counter would otherwise add a second series holding its creation time.
disable_created_metrics()

PREFIX = "troubleshooting"
OTHER = "other"
NONE = "none"

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

FAILURE_CODES = (
    "kubeconfig_unavailable",
    "cluster_unreachable",
    "cluster_access_denied",
    "cluster_api_error",
    "namespace_not_found",
    "unexpected_error",
)
EVIDENCE_CATEGORIES = (
    "namespace",
    "pod",
    "container",
    "container_last_state",
    "logs",
    "event",
    "deployment",
    "replicaset",
    "service",
    "endpointslice",
    "node",
    "pvc",
    "storageclass",
    "serviceaccount",
)
OUTCOMES = get_args(ReportOutcome)
SCENARIOS = get_args(Scenario)
DIAGNOSIS_STATUSES = get_args(DiagnosisStatus)
VERIFICATION_STATUSES = get_args(VerificationStatus)
LLM_REASONS = get_args(ExplanationReason)
FAILED_EVIDENCE_STATUSES = tuple(sorted(INCOMPLETE_STATUSES))

HTTP_REQUESTS = Counter(
    f"{PREFIX}_http_requests",
    "Finished HTTP requests, except scrapes of the metrics endpoint.",
    ["method", "route", "status"],
)
# Route and status series only appear when first used, so increase() would miss their first errors.
# This one starts at 0, which is what lets an alert see the first burst of errors.
HTTP_SERVER_ERRORS = Counter(
    f"{PREFIX}_http_server_errors",
    "Finished HTTP requests with a 5xx status.",
)
HTTP_DURATION = Histogram(
    f"{PREFIX}_http_request_duration_seconds",
    "Time until the last response byte was sent. Event streams are left out, since they stay open on purpose.",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
INVESTIGATIONS = Counter(
    f"{PREFIX}_investigations",
    "Investigations whose final result was saved. The outcome is none for failed ones.",
    ["status", "outcome"],
)
INVESTIGATION_FAILURES = Counter(
    f"{PREFIX}_investigation_failures",
    "Investigations saved as failed while running, by failure code.",
    ["code"],
)
INVESTIGATION_DURATION = Histogram(
    f"{PREFIX}_investigation_duration_seconds",
    "Time an investigation ran for, until its final result was saved.",
    ["status"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)
DIAGNOSES = Counter(
    f"{PREFIX}_diagnoses",
    "Diagnoses in saved investigation reports.",
    ["scenario", "status"],
)
RECOVERY_VERIFICATIONS = Counter(
    f"{PREFIX}_recovery_verifications",
    "Recovery comparisons in saved investigation reports.",
    ["status"],
)
EVIDENCE_FAILURES = Counter(
    f"{PREFIX}_evidence_collector_failures",
    "Evidence records in saved reports that could not be fully collected.",
    ["category", "status"],
)
LLM_DURATION = Histogram(
    f"{PREFIX}_llm_request_duration_seconds",
    "One attempt to call the LLM provider. Retries are separate attempts.",
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 45),
)
LLM_ERRORS = Counter(
    f"{PREFIX}_llm_request_errors",
    "Attempts to call the LLM provider that failed.",
    ["reason"],
)
LLM_EXPLANATIONS = Counter(
    f"{PREFIX}_llm_explanations",
    "Requests for LLM explanations. Unavailable means the user received only the deterministic result.",
    ["status", "reason"],
)
CHAT_STATUSES = ("answered", "unavailable", "off_topic", "clarify", "policy", "budget_exhausted")
CHAT_MESSAGES = Counter(
    f"{PREFIX}_chat_messages",
    "Chat messages handled. off_topic, clarify and policy were answered by the topic guard, without the LLM.",
    ["status"],
)
AI_ANALYSES = Counter(
    f"{PREFIX}_ai_analyses",
    "AI root cause analyses of investigations. Unavailable means the user received only the deterministic result.",
    ["status", "reason"],
)


def _initialize_series() -> None:
    """Create known series at 0. Prometheus cannot see the first increase of a series starting at 1."""
    for outcome in OUTCOMES:
        INVESTIGATIONS.labels("completed", outcome)
    INVESTIGATIONS.labels("failed", NONE)
    for code in FAILURE_CODES:
        INVESTIGATION_FAILURES.labels(code)
    for status in ("completed", "failed"):
        INVESTIGATION_DURATION.labels(status)
    for scenario in SCENARIOS:
        for status in DIAGNOSIS_STATUSES:
            DIAGNOSES.labels(scenario, status)
    for status in VERIFICATION_STATUSES:
        RECOVERY_VERIFICATIONS.labels(status)
    for category in EVIDENCE_CATEGORIES:
        for status in FAILED_EVIDENCE_STATUSES:
            EVIDENCE_FAILURES.labels(category, status)
    for reason in LLM_REASONS:
        LLM_ERRORS.labels(reason)
        LLM_EXPLANATIONS.labels("unavailable", reason)
        LLM_EXPLANATIONS.labels("partial", reason)
    LLM_EXPLANATIONS.labels("generated", NONE)
    LLM_EXPLANATIONS.labels("not_needed", NONE)
    for reason in LLM_REASONS:
        AI_ANALYSES.labels("unavailable", reason)
    AI_ANALYSES.labels("generated", NONE)
    AI_ANALYSES.labels("not_needed", NONE)
    for status in CHAT_STATUSES:
        CHAT_MESSAGES.labels(status)


_initialize_series()


def _bounded(value: str | None, allowed: tuple[str, ...], fallback: str = OTHER) -> str:
    return value if value in allowed else fallback


def _safely(record: Callable[..., None]) -> Callable[..., None]:
    def wrapper(*args, **kwargs) -> None:
        try:
            record(*args, **kwargs)
        except Exception as exc:
            logger.warning("Could not record a metric: %s", type(exc).__name__)

    return wrapper


@_safely
def record_http_request(method: str, route: str, status: int, seconds: float, *, timed: bool = True) -> None:
    method_label = method if method in HTTP_METHODS else "OTHER"
    HTTP_REQUESTS.labels(method_label, route, str(status)).inc()
    if status >= 500:
        HTTP_SERVER_ERRORS.inc()
    if timed:
        HTTP_DURATION.labels(method_label, route).observe(seconds)


@_safely
def record_investigation_completed(report: DiagnosticReport, verification_status: str, seconds: float) -> None:
    """Call once, after the report has been saved."""
    INVESTIGATIONS.labels("completed", _bounded(report.outcome, OUTCOMES)).inc()
    INVESTIGATION_DURATION.labels("completed").observe(seconds)
    RECOVERY_VERIFICATIONS.labels(_bounded(verification_status, VERIFICATION_STATUSES)).inc()
    for diagnosis in report.diagnoses:
        DIAGNOSES.labels(_bounded(diagnosis.scenario, SCENARIOS), _bounded(diagnosis.status, DIAGNOSIS_STATUSES)).inc()
    if report.evidence:
        for record in report.evidence.records:
            if record.status in INCOMPLETE_STATUSES:
                EVIDENCE_FAILURES.labels(_bounded(record.category, EVIDENCE_CATEGORIES), record.status).inc()


@_safely
def record_investigation_failed(code: str, seconds: float) -> None:
    """Call once, after the failure has been saved."""
    INVESTIGATIONS.labels("failed", NONE).inc()
    INVESTIGATION_FAILURES.labels(_bounded(code, FAILURE_CODES)).inc()
    INVESTIGATION_DURATION.labels("failed").observe(seconds)


@_safely
def record_llm_request(seconds: float, error_reason: str | None) -> None:
    LLM_DURATION.observe(seconds)
    if error_reason is not None:
        LLM_ERRORS.labels(_bounded(error_reason, LLM_REASONS)).inc()


@_safely
def record_llm_explanation(status: str, reason: str | None) -> None:
    LLM_EXPLANATIONS.labels(status, _bounded(reason, LLM_REASONS, NONE)).inc()


@_safely
def record_chat_message(status: str) -> None:
    CHAT_MESSAGES.labels(_bounded(status, CHAT_STATUSES)).inc()


@_safely
def record_ai_analysis(status: str, reason: str | None) -> None:
    AI_ANALYSES.labels(_bounded(status, ("generated", "unavailable", "not_needed")), _bounded(reason, LLM_REASONS, NONE)).inc()


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
