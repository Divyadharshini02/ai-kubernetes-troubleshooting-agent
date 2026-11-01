"""Helpers shared by the diagnostic rules."""

from dataclasses import dataclass
from typing import Callable

from app.diagnostics.index import EvidenceIndex
from app.models.diagnosis import (
    AffectedResource,
    Diagnosis,
    DiagnosisStatus,
    EvidenceStrength,
    Fact,
    PossibleCause,
    Recommendation,
    Scenario,
)
from app.models.evidence import EvidenceRecord

EXCERPT_LIMIT = 300


@dataclass(frozen=True)
class Rule:
    name: str
    evaluate: Callable[[EvidenceIndex], list[Diagnosis]]
    # Without these the rule cannot run at all.
    required: tuple[str, ...]
    # Without these the rule still runs, but "no problem" cannot be concluded from it.
    supporting: tuple[str, ...] = ()


def quote(text: str | None) -> str | None:
    """Bound text taken from logs or the API before it enters a diagnosis."""
    if not text:
        return None
    single_line = " ".join(text.split())
    if len(single_line) <= EXCERPT_LIMIT:
        return single_line
    return single_line[:EXCERPT_LIMIT] + "..."


def count_of(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _unique_ids(records) -> list[str]:
    return list(dict.fromkeys(record.evidence_id for record in records))


def fact(statement: str, *records: EvidenceRecord, excerpt: str | None = None, observed_at=None) -> Fact:
    return Fact(
        statement=statement,
        evidence_ids=_unique_ids(records),
        observed_at=observed_at,
        excerpt=quote(excerpt),
    )


def cause(statement: str, *records: EvidenceRecord) -> PossibleCause:
    return PossibleCause(statement=statement, evidence_ids=_unique_ids(records))


def describe_pod_step(index: EvidenceIndex, pod: str, *records: EvidenceRecord) -> Recommendation:
    return Recommendation(
        action=f"Inspect pod {pod} directly.",
        reason="This shows the full status and recent events beyond what was collected.",
        risk="None. These commands only read.",
        expected_impact="No change to the cluster.",
        how_to_apply=[f"kubectl describe pod {pod} -n {index.namespace}"],
        how_to_verify=[f"kubectl get pod {pod} -n {index.namespace}"],
        evidence_ids=_unique_ids(records),
    )


def make_diagnosis(
    index: EvidenceIndex,
    *,
    diagnosis_id: str,
    scenario: Scenario,
    title: str,
    status: DiagnosisStatus,
    strength: EvidenceStrength,
    resource: AffectedResource,
    explanation: str,
    facts: list[Fact],
    causes: list[PossibleCause] | None = None,
    missing: list[str] | None = None,
    conflicting: list[str] | None = None,
    recommendations: list[Recommendation] | None = None,
) -> Diagnosis:
    causes = causes or []
    recommendations = recommendations or []
    cited = dict.fromkeys(
        evidence_id
        for item in [*facts, *causes, *recommendations]
        for evidence_id in item.evidence_ids
    )
    return Diagnosis(
        diagnosis_id=diagnosis_id,
        scenario=scenario,
        title=title,
        status=status,
        evidence_strength=strength,
        affected_resource=resource,
        explanation=explanation,
        confirmed_facts=facts,
        possible_causes=causes,
        missing_evidence=missing or [],
        conflicting_evidence=conflicting or [],
        recommendations=recommendations,
        evidence_references=[index.reference(evidence_id) for evidence_id in cited],
    )


def pod_resource(pod: EvidenceRecord, container: str | None = None) -> AffectedResource:
    return AffectedResource(
        kind="Pod", name=pod.resource_name, namespace=pod.namespace, container=container
    )
