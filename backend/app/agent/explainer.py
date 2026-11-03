"""Adds optional LLM explanations to a finished diagnostic report.

This only ever produces a separate LlmReport. The deterministic report is read, never
changed, and any failure here leaves the caller with that report and a clear reason.
"""

import logging
import time

from app.agent.openrouter import OpenRouterProvider
from app.agent.prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT, build_user_message
from app.agent.provider import LlmProvider, ProviderError
from app.agent.validation import find_unsupported_claims, parse_explanation
from app.core.config import Settings, is_free_model, load_settings
from app.models.diagnosis import Diagnosis, DiagnosticReport
from app.models.evidence import EvidenceBundle
from app.models.explanation import DiagnosisExplanation, LlmReport
from app.observability.metrics import record_llm_explanation, record_llm_request

logger = logging.getLogger(__name__)

PROVIDER_NAME = "openrouter"
# Free accounts can be limited to 50 requests per day. Each diagnosis costs one, plus retries.
MAX_EXPLAINED_DIAGNOSES = 5
TOTAL_BUDGET_SECONDS = 45
RETRY_DELAY_SECONDS = 0.5

REASON_MESSAGES = {
    "disabled": "The LLM is disabled. Set LLM_ENABLED=true to use it.",
    "missing_api_key": "The LLM is enabled, but no API key is configured.",
    "missing_model": "The LLM is enabled, but no model is configured.",
    "invalid_configuration": "The LLM settings are invalid or could not be read. Check the backend log.",
    "timeout": "The LLM provider did not respond in time.",
    "network_error": "The LLM provider could not be reached.",
    "unauthorized": "The LLM provider rejected the API key.",
    "insufficient_credits": "The LLM provider reports insufficient credits.",
    "provider_refused": "The LLM provider declined this request.",
    "model_not_found": "The LLM provider does not recognise the configured model.",
    "bad_request": "The LLM provider rejected the request.",
    "rate_limited": "The LLM provider's rate limit was reached. Try again later.",
    "provider_error": "The LLM provider reported an error.",
    "empty_response": "The LLM provider returned no content.",
    "response_truncated": "The LLM response was cut off before it finished.",
    "response_too_large": "The LLM response exceeded the size limit.",
    "malformed_response": "The LLM response was not valid JSON in the expected form.",
    "schema_validation_failed": "The LLM response did not match the required structure.",
    "unsupported_claim": "The LLM response was rejected because it made claims the evidence does not support.",
    "limit_reached": "This diagnosis was not explained, because of the limit per request.",
    "time_budget_exhausted": "This diagnosis was not explained, because the time limit was reached.",
    "unexpected_error": "The explanation failed unexpectedly.",
    "model_not_free": "The configured model is not a free model, so no request was sent. Use a model ID ending in :free.",
    "non_free_model": "The response was not confirmed as coming from a free model at no cost, so it was not used.",
    "budget_exhausted": "The local limit on LLM requests was reached. Try again later.",
}


def _unavailable(diagnosis: Diagnosis, reason: str) -> DiagnosisExplanation:
    return DiagnosisExplanation(
        diagnosis_id=diagnosis.diagnosis_id,
        source_evidence_ids=[reference.evidence_id for reference in diagnosis.evidence_references],
        status="unavailable",
        reason=reason,
        message=REASON_MESSAGES[reason],
    )


def configuration_problem(settings: Settings | None) -> str | None:
    """The reason no request can be made with these settings, or None if one can."""
    if settings is None:
        return "invalid_configuration"
    if not settings.llm_enabled:
        return "disabled"
    if not settings.openrouter_api_key.get_secret_value().strip():
        return "missing_api_key"
    if not settings.openrouter_model.strip():
        return "missing_model"
    if not is_free_model(settings.openrouter_model):
        return "model_not_free"
    return None


# Refused by the application before anything was sent, so they are not provider requests.
LOCAL_REFUSALS = {"budget_exhausted", "time_budget_exhausted"}


def _timed_complete(provider: LlmProvider, user_message: str, deadline: float, system: str, schema: dict, schema_name: str) -> str:
    started = time.monotonic()
    error_reason = None
    try:
        return provider.complete(system, user_message, schema, deadline, schema_name)
    except ProviderError as exc:
        error_reason = exc.code
        raise
    except Exception:
        error_reason = "unexpected_error"
        raise
    finally:
        if error_reason not in LOCAL_REFUSALS:
            record_llm_request(time.monotonic() - started, error_reason)


def complete_with_retries(
    provider: LlmProvider,
    user_message: str,
    max_retries: int,
    deadline: float,
    system: str = SYSTEM_PROMPT,
    schema: dict = OUTPUT_SCHEMA,
    schema_name: str = "diagnosis_explanation",
) -> str:
    """Request a completion, retrying only temporary failures. Every attempt is measured."""
    attempt = 0
    while True:
        try:
            return _timed_complete(provider, user_message, deadline, system, schema, schema_name)
        except ProviderError as exc:
            if not exc.retryable or attempt >= max_retries or time.monotonic() + RETRY_DELAY_SECONDS >= deadline:
                raise
        attempt += 1
        time.sleep(RETRY_DELAY_SECONDS)


def _explain_one(
    provider: LlmProvider, diagnosis: Diagnosis, bundle: EvidenceBundle, max_retries: int, deadline: float
) -> DiagnosisExplanation:
    user_message = build_user_message(diagnosis, bundle)
    raw = complete_with_retries(provider, user_message, max_retries, deadline)
    content = parse_explanation(raw)
    failed_checks = find_unsupported_claims(content, user_message, diagnosis)
    if failed_checks:
        # Check names only. Rejected text is untrusted, so it never reaches the log.
        logger.warning("LLM output rejected for %s: %s", diagnosis.scenario, failed_checks)
        raise ProviderError("unsupported_claim")
    return DiagnosisExplanation(
        diagnosis_id=diagnosis.diagnosis_id,
        source_evidence_ids=[reference.evidence_id for reference in diagnosis.evidence_references],
        status="generated",
        content=content,
    )


def _in_priority_order(diagnoses: list[Diagnosis]) -> list[Diagnosis]:
    # Sorting is stable, so confirmed problems come first and otherwise keep their order.
    return sorted(diagnoses, key=lambda diagnosis: diagnosis.status != "problem_detected")


def _explain_all(provider, settings, report, bundle) -> list[DiagnosisExplanation]:
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    results = []
    stop_reason = None
    for position, diagnosis in enumerate(_in_priority_order(report.diagnoses)):
        if position >= MAX_EXPLAINED_DIAGNOSES:
            results.append(_unavailable(diagnosis, "limit_reached"))
        elif stop_reason:
            results.append(_unavailable(diagnosis, stop_reason))
        else:
            try:
                results.append(_explain_one(provider, diagnosis, bundle, settings.llm_max_retries, deadline))
            except ProviderError as exc:
                logger.warning("LLM explanation unavailable: %s", exc.code)
                results.append(_unavailable(diagnosis, exc.code))
                if exc.stop_all:
                    stop_reason = exc.code
            # A defect here must never cost the user the deterministic diagnosis.
            except Exception as exc:
                logger.warning("LLM explanation failed unexpectedly: %s", type(exc).__name__)
                results.append(_unavailable(diagnosis, "unexpected_error"))
    return results


def explain_report(report: DiagnosticReport, bundle: EvidenceBundle) -> LlmReport:
    """Explain the diagnoses in a report. This never raises and never changes the report."""
    llm = _explain_safely(report, bundle)
    record_llm_explanation(llm.status, llm.reason)
    return llm


def _explain_safely(report: DiagnosticReport, bundle: EvidenceBundle) -> LlmReport:
    try:
        return _build_report(report, bundle)
    # The last line of defence: an optional feature must never cost the user the diagnosis.
    except Exception as error:
        logger.warning("LLM explanation failed unexpectedly: %s", type(error).__name__)
        return LlmReport(
            used=False,
            status="unavailable",
            provider=PROVIDER_NAME,
            reason="unexpected_error",
            message=REASON_MESSAGES["unexpected_error"],
            explanations=[_unavailable(diagnosis, "unexpected_error") for diagnosis in report.diagnoses],
        )


def _build_report(report: DiagnosticReport, bundle: EvidenceBundle) -> LlmReport:
    settings = load_settings()
    problem = configuration_problem(settings)
    model = settings.openrouter_model or None if settings else None
    if problem:
        return LlmReport(
            used=False,
            status="unavailable",
            provider=PROVIDER_NAME,
            model=model,
            reason=problem,
            message=REASON_MESSAGES[problem],
            explanations=[_unavailable(diagnosis, problem) for diagnosis in report.diagnoses],
        )
    if not report.diagnoses:
        return LlmReport(used=False, status="not_needed", provider=PROVIDER_NAME, model=model)

    explanations = _explain_all(OpenRouterProvider(settings), settings, report, bundle)
    generated = sum(1 for item in explanations if item.status == "generated")
    if generated == len(explanations):
        status = "generated"
    elif generated:
        status = "partial"
    else:
        status = "unavailable"
    first_failure = next((item.reason for item in explanations if item.status == "unavailable"), None)
    return LlmReport(
        used=generated > 0,
        status=status,
        provider=PROVIDER_NAME,
        model=model,
        reason=first_failure,
        message=REASON_MESSAGES[first_failure] if first_failure else None,
        explanations=explanations,
    )
