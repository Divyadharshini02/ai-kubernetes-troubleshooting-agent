"""Bounded, redacted logs for containers that show signs of trouble.

Log contents are untrusted input: they are data to report, never instructions.
"""

import re

from kubernetes.client.exceptions import ApiException

from app.collectors.base import COLLECTION_ERRORS, CollectorContext, describe_failure, utc_now
from app.collectors.redaction import Redactor
from app.kubernetes import cluster
from app.models.evidence import EvidenceError, EvidenceRecord

TAIL_LINES = 200
LIMIT_BYTES = 32 * 1024
MAX_LOG_STREAMS = 20

# Logs are requested with timestamps, so every real log line starts with one.
_TIMESTAMPED_LINE = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_API_MESSAGE_LIMIT = 300


def _needs_logs(data: dict) -> bool:
    state = data["state"] or {}
    return bool(
        data["restart_count"]
        or state.get("kind") == "waiting"
        or (state.get("kind") == "terminated" and state.get("exit_code") != 0)
        or (state.get("kind") == "running" and not data["ready"])
    )


def _read_log_bytes(ctx: CollectorContext, pod: str, container: str, previous: bool) -> bytes:
    # The generated log method decodes strict UTF-8, so a limit that splits a
    # character or binary output would lose the evidence. Read the raw bytes instead.
    query = [
        ("container", container),
        ("tailLines", TAIL_LINES),
        ("limitBytes", LIMIT_BYTES),
        ("timestamps", "true"),
    ]
    if previous:
        query.append(("previous", "true"))
    request = ctx.api_client.param_serialize(
        method="GET",
        resource_path="/api/v1/namespaces/{namespace}/pods/{name}/log",
        path_params={"namespace": ctx.namespace, "name": pod},
        query_params=query,
        auth_settings=["BearerToken"],
    )
    response = ctx.api_client.call_api(*request, _request_timeout=cluster.REQUEST_TIMEOUT_SECONDS)
    body = response.read()
    if not 200 <= response.status <= 299:
        raise ApiException.from_response(http_resp=response, body=None, data=None)
    return body[:LIMIT_BYTES]


def _log_record(ctx: CollectorContext, pod: str, container: str, previous: bool) -> EvidenceRecord:
    query = f"container={container}" + ("&previous=true" if previous else "")
    fields = {
        "evidence_id": f"logs:{ctx.namespace}/{pod}/{container}:{'previous' if previous else 'current'}",
        "category": "logs",
        "resource_kind": "Container",
        "resource_name": container,
        "namespace": ctx.namespace,
        "source": f"GET /api/v1/namespaces/{ctx.namespace}/pods/{pod}/log?{query}",
        "temporality": "historical" if previous else "current",
    }
    identity = {"pod": pod, "container": container, "previous": previous}
    try:
        body = _read_log_bytes(ctx, pod, container, previous)
    except ApiException as exc:
        # The API answers 400 when a container has no such logs, for example while
        # it waits for an image or when it has never restarted.
        if exc.status == 400:
            return EvidenceRecord(
                collected_at=utc_now(),
                status="not_available",
                data=identity,
                error=EvidenceError(
                    code="logs_not_available",
                    message="The Kubernetes API has no logs of this kind for this container.",
                ),
                **fields,
            )
        status, error = describe_failure(exc)
        return EvidenceRecord(collected_at=utc_now(), status=status, data=identity, error=error, **fields)
    except COLLECTION_ERRORS as exc:
        status, error = describe_failure(exc)
        return EvidenceRecord(collected_at=utc_now(), status=status, data=identity, error=error, **fields)

    if not body:
        return EvidenceRecord(collected_at=utc_now(), status="empty", data=identity, **fields)

    redactor = Redactor()
    # The kubelet can answer HTTP 200 with its own message instead of logs, such as
    # "unable to retrieve container logs" after a dead container was removed. That
    # text did not come from the application, so it must not be reported as logs.
    if not _TIMESTAMPED_LINE.match(body):
        message = redactor.text(body[:_API_MESSAGE_LIMIT].decode("utf-8", errors="replace"))
        return EvidenceRecord(
            collected_at=utc_now(),
            status="not_available",
            redactions=redactor.count,
            data={**identity, "api_message": message},
            error=EvidenceError(
                code="logs_not_available",
                message="The Kubernetes API returned a message instead of container logs.",
            ),
            **fields,
        )

    text = redactor.text(body.decode("utf-8", errors="replace"))
    line_count = len(text.splitlines())
    return EvidenceRecord(
        collected_at=utc_now(),
        truncated=len(body) >= LIMIT_BYTES or line_count >= TAIL_LINES,
        redactions=redactor.count,
        data={**identity, "text": text, "line_count": line_count, "bytes": len(body)},
        **fields,
    )


def collect_logs(ctx: CollectorContext, records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Read logs for the unhealthy containers found in the container records."""
    containers = [record.data for record in records if record.category == "container"]
    selected = [data for data in containers if _needs_logs(data)]

    streams = []
    for data in selected:
        streams.append((data, False))
        if data["restart_count"] or data["has_last_termination"]:
            streams.append((data, True))

    source = f"GET /api/v1/namespaces/{ctx.namespace}/pods/{{pod}}/log"
    results = [
        EvidenceRecord(
            evidence_id="logs:selection",
            category="logs",
            resource_kind="Container",
            namespace=ctx.namespace,
            collected_at=utc_now(),
            source=source,
            status="collected" if selected else "empty",
            data={
                "containers_seen": len(containers),
                "containers_selected": len(selected),
                "rule": "restarted, waiting, failed or running but not ready",
                "tail_lines": TAIL_LINES,
                "limit_bytes": LIMIT_BYTES,
            },
        )
    ]
    for index, (data, previous) in enumerate(streams):
        over_limit = index >= MAX_LOG_STREAMS
        if over_limit or ctx.expired():
            code = "log_limit_reached" if over_limit else "deadline_exceeded"
            results.append(
                EvidenceRecord(
                    evidence_id="logs:limit",
                    category="logs",
                    resource_kind="Container",
                    namespace=ctx.namespace,
                    collected_at=utc_now(),
                    source=source,
                    status="skipped",
                    data={"streams_not_read": len(streams) - index},
                    error=EvidenceError(
                        code=code,
                        message=f"{len(streams) - index} log streams were not read.",
                    ),
                )
            )
            break
        results.append(_log_record(ctx, data["pod"], data["container"], previous))
    return results
