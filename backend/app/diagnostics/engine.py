"""Run every diagnostic rule over one evidence bundle. Reads evidence only."""

import logging

from app.diagnostics.common import Rule, count_of
from app.diagnostics.index import EvidenceIndex
from app.diagnostics.rules import crash_loop, image_pull, pending, probes, service_selector, unclassified
from app.models.diagnosis import (
    Diagnosis,
    DiagnosticReport,
    ReportOutcome,
    ResourcesExamined,
    RuleResult,
)
from app.models.evidence import EvidenceBundle

logger = logging.getLogger(__name__)

RULES: tuple[Rule, ...] = (
    crash_loop.RULE,
    image_pull.RULE,
    pending.RULE,
    service_selector.RULE,
    probes.RULE,
)


def _run_rule(rule: Rule, index: EvidenceIndex) -> tuple[RuleResult, list[Diagnosis]]:
    unavailable = [category for category in rule.required if not index.has(category)]
    if unavailable:
        return (
            RuleResult(
                rule=rule.name,
                status="insufficient_evidence",
                diagnoses=0,
                missing_evidence=[f"Required evidence is unavailable: {c}." for c in unavailable],
            ),
            [],
        )
    limited = [
        category
        for category in (*rule.required, *rule.supporting)
        if index.availability(category) != "available"
    ]
    try:
        diagnoses = rule.evaluate(index)
    # A defect in one rule must not hide findings from the others.
    except Exception as exc:
        logger.exception("Diagnostic rule %s failed: %s", rule.name, type(exc).__name__)
        return (
            RuleResult(
                rule=rule.name,
                status="error",
                diagnoses=0,
                missing_evidence=["This rule failed unexpectedly, so it reports nothing."],
            ),
            [],
        )
    return (
        RuleResult(
            rule=rule.name,
            status="partial" if limited else "evaluated",
            diagnoses=len(diagnoses),
            missing_evidence=[f"Supporting evidence is incomplete: {c}." for c in limited],
        ),
        diagnoses,
    )


def _run_unclassified(index, diagnoses) -> tuple[RuleResult, list[Diagnosis]]:
    if not index.has("pod"):
        return (
            RuleResult(
                rule=unclassified.RULE_NAME,
                status="insufficient_evidence",
                diagnoses=0,
                missing_evidence=["Required evidence is unavailable: pod."],
            ),
            [],
        )
    found = unclassified.evaluate(index, diagnoses)
    return RuleResult(rule=unclassified.RULE_NAME, status="evaluated", diagnoses=len(found), missing_evidence=[]), found


def _outcome(diagnoses: list[Diagnosis], rule_results: list[RuleResult]) -> ReportOutcome:
    if any(diagnosis.status == "problem_detected" for diagnosis in diagnoses):
        return "problems_found"
    if diagnoses or any(result.status != "evaluated" for result in rule_results):
        return "insufficient_evidence"
    return "no_problems_found"


def _summary(outcome: ReportOutcome, diagnoses, examined: ResourcesExamined) -> str:
    scope = (
        f"Examined {count_of(examined.pods, 'pod')}, {count_of(examined.containers, 'container')} "
        f"and {count_of(examined.services, 'service')}."
    )
    if outcome == "problems_found":
        confirmed = sum(1 for diagnosis in diagnoses if diagnosis.status == "problem_detected")
        return f"{count_of(confirmed, 'problem')} detected. {scope}"
    if outcome == "insufficient_evidence":
        return f"The available evidence does not support a conclusion. {scope}"
    return f"No supported problem was detected in the collected evidence. {scope}"


def diagnose(bundle: EvidenceBundle) -> DiagnosticReport:
    """Evaluate a bundle. The same bundle always produces the same diagnoses."""
    index = EvidenceIndex(bundle)
    rule_results, diagnoses = [], []
    for rule in RULES:
        result, found = _run_rule(rule, index)
        rule_results.append(result)
        diagnoses.extend(found)
    try:
        result, found = _run_unclassified(index, diagnoses)
    except Exception as exc:
        logger.exception("Diagnostic rule unclassified failed: %s", type(exc).__name__)
        result, found = (
            RuleResult(
                rule=unclassified.RULE_NAME,
                status="error",
                diagnoses=0,
                missing_evidence=["This rule failed unexpectedly, so it reports nothing."],
            ),
            [],
        )
    rule_results.append(result)
    diagnoses.extend(found)

    examined = ResourcesExamined(
        pods=len(index.pods()),
        containers=len(index.containers()),
        services=len(index.services()),
    )
    outcome = _outcome(diagnoses, rule_results)
    return DiagnosticReport(
        namespace=bundle.namespace,
        context=bundle.context,
        outcome=outcome,
        summary=_summary(outcome, diagnoses, examined),
        evidence_completeness=bundle.completeness,
        evidence_collected_at=bundle.completed_at,
        resources_examined=examined,
        diagnoses=diagnoses,
        rule_results=rule_results,
    )
