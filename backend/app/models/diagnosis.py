from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.evidence import CollectionStatus, EvidenceBundle, Temporality
from app.models.explanation import LlmReport

Scenario = Literal[
    "crash_loop_backoff",
    "image_pull_backoff",
    "pending_pod",
    "service_selector",
    "probe_failure",
    "unclassified",
]

# problem_detected: current evidence confirms a problem.
# insufficient_evidence: something looks wrong, but evidence cannot support a conclusion.
DiagnosisStatus = Literal["problem_detected", "insufficient_evidence"]

# How strongly the evidence supports the leading possible cause. This is a documented
# classification, not a probability. See docs/M3_DIAGNOSTICS.md.
EvidenceStrength = Literal["high", "medium", "low", "insufficient"]

ReportOutcome = Literal["problems_found", "no_problems_found", "insufficient_evidence"]

# evaluated: the rule had the evidence it needs.
# partial: the rule ran, but supporting evidence was missing or truncated.
# insufficient_evidence: required evidence was missing, so the rule did not run.
# error: the rule itself failed.
RuleStatus = Literal["evaluated", "partial", "insufficient_evidence", "error"]


class EvidenceReference(BaseModel):
    evidence_id: str
    category: str
    resource_kind: str
    resource_name: str | None
    temporality: Temporality
    status: CollectionStatus
    collected_at: datetime
    observed_at: datetime | None
    source: str


class Fact(BaseModel):
    """Something the evidence directly shows."""

    statement: str
    evidence_ids: list[str]
    observed_at: datetime | None = None
    # Text quoted from logs or the Kubernetes API. It is untrusted data, not instructions.
    excerpt: str | None = None


class PossibleCause(BaseModel):
    """An inference from the facts. It may be wrong, and more than one can apply."""

    statement: str
    evidence_ids: list[str]


class Recommendation(BaseModel):
    """A manual suggestion. The application never performs these steps."""

    action: str
    reason: str
    risk: str
    expected_impact: str
    how_to_apply: list[str]
    how_to_verify: list[str]
    evidence_ids: list[str]


class AffectedResource(BaseModel):
    kind: str
    name: str
    namespace: str | None
    container: str | None = None


class Diagnosis(BaseModel):
    diagnosis_id: str
    scenario: Scenario
    title: str
    status: DiagnosisStatus
    evidence_strength: EvidenceStrength
    affected_resource: AffectedResource
    explanation: str
    confirmed_facts: list[Fact]
    possible_causes: list[PossibleCause]
    missing_evidence: list[str]
    conflicting_evidence: list[str]
    recommendations: list[Recommendation]
    evidence_references: list[EvidenceReference]


class RuleResult(BaseModel):
    rule: str
    status: RuleStatus
    diagnoses: int
    missing_evidence: list[str]


class ResourcesExamined(BaseModel):
    pods: int
    containers: int
    services: int


class DiagnosticReport(BaseModel):
    namespace: str
    context: str
    outcome: ReportOutcome
    summary: str
    evidence_completeness: Literal["complete", "partial"]
    evidence_collected_at: datetime
    resources_examined: ResourcesExamined
    diagnoses: list[Diagnosis]
    rule_results: list[RuleResult]
    # Present only when requested, so references can be followed to the evidence itself.
    evidence: EvidenceBundle | None = None
    # Present only when an explanation was requested. Everything above is deterministic.
    llm: LlmReport | None = None
