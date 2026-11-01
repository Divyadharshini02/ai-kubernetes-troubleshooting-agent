"""Shared plumbing for the read-only evidence collectors."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from kubernetes import client
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError

from app.kubernetes import cluster
from app.models.evidence import CollectionStatus, EvidenceError, EvidenceRecord

logger = logging.getLogger(__name__)

LIST_LIMIT = cluster.LIST_LIMIT

# The failures a collector records as evidence instead of raising.
COLLECTION_ERRORS = (ApiException, HTTPError, ValueError)


@dataclass
class CollectorContext:
    api_client: client.ApiClient
    namespace: str
    # time.monotonic() value after which no new API work should start.
    deadline: float

    @property
    def core(self) -> client.CoreV1Api:
        return client.CoreV1Api(self.api_client)

    @property
    def apps(self) -> client.AppsV1Api:
        return client.AppsV1Api(self.api_client)

    @property
    def discovery(self) -> client.DiscoveryV1Api:
        return client.DiscoveryV1Api(self.api_client)

    @property
    def storage(self) -> client.StorageV1Api:
        return client.StorageV1Api(self.api_client)

    def expired(self) -> bool:
        return time.monotonic() >= self.deadline


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def describe_failure(exc: Exception) -> tuple[CollectionStatus, EvidenceError]:
    """Map a client failure to a status and safe message, never raw exception text."""
    if isinstance(exc, ApiException):
        if cluster.is_tls_failure(exc):
            logger.warning("Could not establish a secure connection during collection")
            return "failed", EvidenceError(
                code="api_unreachable",
                message="A secure connection to the Kubernetes API could not be established.",
            )
        if not exc.status:
            logger.warning("Kubernetes API response could not be processed during collection")
            return "failed", EvidenceError(
                code="unexpected_response",
                message="The Kubernetes API returned an unexpected response.",
            )
        logger.warning("Kubernetes API returned status %s during collection", exc.status)
        if exc.status in (401, 403):
            return "forbidden", EvidenceError(
                code="access_denied",
                message="The Kubernetes API denied access to this resource.",
            )
        if exc.status == 404:
            return "not_found", EvidenceError(
                code="resource_not_found",
                message="The Kubernetes API reports that this resource does not exist.",
            )
        return "failed", EvidenceError(
            code="api_error",
            message=f"The Kubernetes API returned an error (HTTP {exc.status}).",
        )
    logger.warning("Evidence collection failed: %s", type(exc).__name__)
    if isinstance(exc, HTTPError):
        return "failed", EvidenceError(
            code="api_unreachable",
            message="The Kubernetes API could not be reached.",
        )
    return "failed", EvidenceError(
        code="unexpected_response",
        message="The Kubernetes API returned an unexpected response.",
    )


def collect_list(
    ctx: CollectorContext,
    *,
    collector: str,
    category: str,
    kind: str,
    source: str,
    namespace: str | None,
    fetch: Callable,
    to_records: Callable[[object], Iterable[EvidenceRecord]],
) -> list[EvidenceRecord]:
    """List one resource type, always returning explicit records.

    A failed request or an empty list produces a record saying so, because an
    empty result must never look the same as a failed one.
    """

    def outcome(suffix: str, status: CollectionStatus, **fields) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=f"{collector}:{suffix}",
            category=category,
            resource_kind=kind,
            namespace=namespace,
            collected_at=utc_now(),
            source=source,
            status=status,
            **fields,
        )

    try:
        result = fetch(limit=LIST_LIMIT, _request_timeout=cluster.REQUEST_TIMEOUT_SECONDS)
    except COLLECTION_ERRORS as exc:
        status, error = describe_failure(exc)
        return [outcome("request", status, error=error)]

    if not result.items:
        return [outcome("none", "empty")]

    records = [record for item in result.items for record in to_records(item)]
    if result.metadata and result.metadata.var_continue:
        records.append(
            outcome(
                "limit",
                "skipped",
                data={"limit": LIST_LIMIT},
                error=EvidenceError(
                    code="list_truncated",
                    message=f"More resources exist than the {LIST_LIMIT} collected.",
                ),
            )
        )
    return records


def conditions_summary(conditions, redactor) -> list[dict]:
    return [
        {
            "type": condition.type,
            "status": condition.status,
            "reason": condition.reason,
            "message": redactor.text(condition.message),
            "last_transition_time": getattr(condition, "last_transition_time", None),
        }
        for condition in conditions or []
    ]


def owner_references(metadata) -> list[dict]:
    return [
        {"kind": owner.kind, "name": owner.name}
        for owner in metadata.owner_references or []
    ]
