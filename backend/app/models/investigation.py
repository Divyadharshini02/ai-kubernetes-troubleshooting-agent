from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.diagnosis import DiagnosticReport, ReportOutcome

InvestigationStatus = Literal["running", "completed", "failed"]

# Recovery verification arrives in Milestone 6, so nothing can be verified yet.
VerificationStatus = Literal["not_verified"]

# Each stage is recorded only after the backend has actually reached it.
ProgressStage = Literal[
    "created",
    "connectivity_checked",
    "evidence_collected",
    "rules_evaluated",
    "ai_requested",
    "ai_finished",
    "report_saved",
    "completed",
    "failed",
]


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str = Field(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", max_length=63)
    # Off unless asked for, because an explanation sends evidence to a third party.
    explain: bool = False


class InvestigationError(BaseModel):
    code: str
    message: str


class ProgressEvent(BaseModel):
    sequence: int
    stage: ProgressStage
    message: str
    occurred_at: datetime


class InvestigationSummary(BaseModel):
    id: str
    namespace: str
    status: InvestigationStatus
    explain_requested: bool
    verification_status: VerificationStatus
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: float | None
    outcome: ReportOutcome | None
    summary: str | None
    diagnosis_count: int | None
    # The LLM report's status and reason. Both are empty when no explanation was requested.
    ai_status: str | None
    ai_reason: str | None
    error: InvestigationError | None


class InvestigationDetail(InvestigationSummary):
    events: list[ProgressEvent]
    report: DiagnosticReport | None
