"""Chat about one saved investigation. Generated answers are labelled, and checked before they are kept."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.analysis import EvidenceId, RemediationStep, ShortText
from app.models.explanation import ExplanationReason

MAX_MESSAGE_CHARS = 2000

# answer: generated, and passed every check. off_topic, clarify, policy: the topic guard replied,
# with no request to the model. unavailable: no checked answer, so the deterministic result is shown.
MessageKind = Literal["question", "answer", "off_topic", "clarify", "policy", "unavailable"]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS)]


class ChatAnswer(BaseModel):
    """The only shape accepted from the model."""

    model_config = ConfigDict(extra="forbid", strict=True)

    # The prompt asks for about 1,200 characters. A real model wrote more than 1,500, so this leaves room.
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2500)]
    # investigation: about this investigation's evidence. general: technical guidance, not about this cluster.
    scope: Literal["investigation", "general"]
    evidence_ids: Annotated[list[EvidenceId], Field(max_length=8)]
    remediation: Annotated[list[RemediationStep], Field(max_length=3)]
    limitations: Annotated[list[ShortText], Field(max_length=4)]
    follow_up_questions: Annotated[list[ShortText], Field(max_length=3)]


class ChatMessage(BaseModel):
    sequence: int
    role: Literal["user", "assistant"]
    kind: MessageKind
    # The user's message after redaction, or the assistant's reply text.
    text: str
    answer: ChatAnswer | None = None
    reason: ExplanationReason | None = None
    created_at: datetime


class ChatExchange(BaseModel):
    question: ChatMessage
    reply: ChatMessage
