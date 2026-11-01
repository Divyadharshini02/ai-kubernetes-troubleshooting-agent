from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

# collected: data was read. empty: the request worked and found nothing.
# not_found: the resource does not exist. not_available: the API has no such data,
# such as previous logs for a container that never restarted.
# forbidden, failed: the request did not succeed. skipped: a collection limit was hit.
CollectionStatus = Literal[
    "collected", "empty", "not_found", "not_available", "forbidden", "failed", "skipped"
]

# current: state at collection time. historical: something that already happened,
# which may no longer be true.
Temporality = Literal["current", "historical"]

INCOMPLETE_STATUSES = {"forbidden", "failed", "skipped"}


class EvidenceError(BaseModel):
    code: str
    message: str


class EvidenceRecord(BaseModel):
    evidence_id: str
    category: str
    resource_kind: str
    resource_name: str | None = None
    namespace: str | None = None
    collected_at: datetime
    source: str
    temporality: Temporality = "current"
    # When a historical record last happened, if the cluster reported it.
    observed_at: datetime | None = None
    status: CollectionStatus = "collected"
    truncated: bool = False
    redactions: int = 0
    data: dict[str, Any] = {}
    error: EvidenceError | None = None


class EvidenceBundle(BaseModel):
    namespace: str
    context: str
    started_at: datetime
    completed_at: datetime
    # partial: at least one record could not be fully collected.
    completeness: Literal["complete", "partial"]
    status_counts: dict[str, int]
    records: list[EvidenceRecord]
