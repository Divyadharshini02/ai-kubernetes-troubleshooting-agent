"""Scenario 5: readiness, liveness and startup probe failures, kept distinct.

Only recent failure events count as current. A single failure while a container starts is
normal, so a container that is ready now is only flagged after repeated recent failures.
"""

import re

from app.diagnostics.common import Rule, cause, count_of, fact, make_diagnosis, pod_resource
from app.diagnostics.index import EvidenceIndex
from app.models.diagnosis import Diagnosis, Recommendation
from app.models.evidence import EvidenceRecord

SCENARIO = "probe_failure"
REPEATED_FAILURES = 3

_PROBE_MESSAGES = {
    "readiness": "Readiness probe failed",
    "liveness": "Liveness probe failed",
    "startup": "Startup probe failed",
}

_FAILURE_CLASSES = [
    (
        re.compile(r"statuscode: 404"),
        "The probe path may be wrong, because the application answered HTTP 404.",
    ),
    (
        re.compile(r"statuscode: 5\d\d"),
        "The application answered the probe with a server error.",
    ),
    (
        re.compile(r"statuscode: \d+"),
        "The application answered the probe with an unsuccessful HTTP status.",
    ),
    (
        re.compile(r"(?i)connection refused"),
        "Nothing was listening on the probe port. The port may be wrong, or the application may not have started.",
    ),
    (
        re.compile(r"(?i)deadline exceeded|timeout|timed out"),
        "The application may be responding too slowly for the probe's timeout.",
    ),
]


def _failure_events(index, pod, container, probe) -> list[EvidenceRecord]:
    prefix = _PROBE_MESSAGES[probe]
    return [
        event
        for event in index.events_for("Pod", pod, container)
        if event.data["reason"] == "Unhealthy" and (event.data["message"] or "").startswith(prefix)
    ]


def _describe(config: dict) -> str:
    handler = config.get("handler") or {}
    # Kubernetes omits an initial delay of 0, its documented default.
    timing = (
        f"delay {config.get('initial_delay_seconds') or 0}s, period {config.get('period_seconds')}s, "
        f"timeout {config.get('timeout_seconds')}s, failure threshold {config.get('failure_threshold')}"
    )
    kind = handler.get("type")
    if kind == "http_get":
        return f"HTTP GET {handler.get('path')} on port {handler.get('port')} ({timing})"
    if kind == "tcp_socket":
        return f"TCP check on port {handler.get('port')} ({timing})"
    if kind == "grpc":
        return f"gRPC check on port {handler.get('port')} ({timing})"
    if kind == "exec":
        return f"a command run in the container ({timing})"
    return f"an unrecognised handler ({timing})"


def _port_fact(container: EvidenceRecord, config: dict):
    """Check a probe port against declared ports. Undeclared numeric ports are legal."""
    port = (config.get("handler") or {}).get("port")
    if port is None:
        return None
    declared = container.data["ports"]
    if isinstance(port, str):
        if port not in {item["name"] for item in declared}:
            return fact(f"The probe uses port name {port}, which the container does not declare.", container)
        return None
    numbers = {item["container_port"] for item in declared}
    if declared and port not in numbers:
        return fact(f"The probe uses port {port}. The container declares ports {sorted(numbers)}.", container)
    return None


def _steps(index, pod, name, probe, container) -> list[Recommendation]:
    return [
        Recommendation(
            action=f"Test what the {probe} probe of container {name} checks.",
            reason="This separates a wrong probe configuration from an unhealthy application.",
            risk=(
                "None for the check. Changing a probe changes when Kubernetes restarts "
                "the container or sends it traffic."
            ),
            expected_impact="The failures stop once the probe and the application agree.",
            how_to_apply=[
                f"kubectl describe pod {pod} -n {index.namespace}",
                f"kubectl port-forward pod/{pod} -n {index.namespace} <local-port>:<probe-port>",
                "Correct the probe or the application in your manifests and apply them yourself.",
            ],
            how_to_verify=[
                f"kubectl get pod {pod} -n {index.namespace} --watch",
                "Run this diagnosis again.",
            ],
            evidence_ids=[container.evidence_id],
        )
    ]


def _build(index, container, probe, status, strength, title, explanation, facts, causes=None, missing=None, conflicting=None):
    pod_name, name = container.data["pod"], container.data["container"]
    return make_diagnosis(
        index,
        diagnosis_id=f"{SCENARIO}:{probe}:{container.namespace}/{pod_name}/{name}",
        scenario=SCENARIO,
        title=title,
        status=status,
        strength=strength,
        resource=pod_resource(index.pod(pod_name), name),
        explanation=explanation,
        facts=facts,
        causes=causes,
        missing=missing,
        conflicting=conflicting,
        recommendations=_steps(index, pod_name, name, probe, container),
    )


def _confirmed_failure(index, container, probe, config, recent, extra_facts=None) -> Diagnosis:
    name = container.data["container"]
    latest = recent[0]
    total = sum(event.data["count"] or 1 for event in recent)
    facts = [
        fact(f"The {probe} probe of container {name} is {_describe(config)}.", container),
        fact(
            f"Recent events report {count_of(total, f'{probe} probe failure')}.",
            *recent[:2],
            excerpt=latest.data["message"],
            observed_at=latest.observed_at,
        ),
        *(extra_facts or []),
    ]
    causes = []
    for pattern, statement in _FAILURE_CLASSES:
        if pattern.search(latest.data["message"] or ""):
            causes.append(cause(statement, latest))
            break
    port_fact = _port_fact(container, config)
    if port_fact:
        facts.append(port_fact)
        causes.append(cause("The probe port may not match the container's ports.", container))
    return _build(
        index,
        container,
        probe,
        "problem_detected",
        "high" if causes else "medium",
        f"{probe.capitalize()} probe of container {name} is failing",
        f"Kubernetes reports that the {probe} probe of container {name} is failing now. "
        "Possible causes below are inferred from the latest failure message.",
        facts,
        causes=causes,
    )


def _check_readiness(index, container) -> Diagnosis | None:
    data = container.data
    config = data["probes"]["readiness"]
    if not config or (data["state"] or {}).get("kind") != "running":
        return None
    name = data["container"]
    events = _failure_events(index, data["pod"], name, "readiness")
    recent = [event for event in events if index.is_recent(event)]
    stale = [event for event in events if not index.is_recent(event)]

    if data["ready"] is True:
        total = sum(event.data["count"] or 1 for event in recent)
        if total < REPEATED_FAILURES:
            return None
        return _build(
            index,
            container,
            "readiness",
            "insufficient_evidence",
            "insufficient",
            f"Readiness of container {name} may be unstable",
            "The container is ready now, but recently failed its readiness probe repeatedly.",
            [
                fact(f"Container {name} reports ready.", container),
                fact(
                    f"Recent events report {count_of(total, 'readiness probe failure')}.",
                    *recent[:2],
                    observed_at=recent[0].observed_at,
                ),
            ],
            conflicting=["The container is ready now, but recent events report repeated readiness failures."],
        )

    if recent:
        return _confirmed_failure(
            index,
            container,
            "readiness",
            config,
            recent,
            [fact(f"Container {name} is running but not ready, with {count_of(data['restart_count'], 'restart')}.", container)],
        )

    missing = (
        ["Events could not be collected, so probe failures cannot be confirmed."]
        if not index.has("event")
        else ["No recent readiness probe failure event was found."]
    )
    return _build(
        index,
        container,
        "readiness",
        "insufficient_evidence",
        "insufficient",
        f"Container {name} is not ready, but no probe failure is confirmed",
        "The container is not ready and has a readiness probe, but no recent event shows the probe failing. "
        "It may still be starting.",
        [
            fact(f"Container {name} is running but not ready.", container),
            fact(f"Its readiness probe is {_describe(config)}.", container),
        ],
        missing=missing,
        conflicting=["Only old readiness failure events were found."] if stale else None,
    )


def _check_liveness(index, container) -> Diagnosis | None:
    data = container.data
    pod_name, name = data["pod"], data["container"]
    config = data["probes"]["liveness"]
    recent = [e for e in _failure_events(index, pod_name, name, "liveness") if index.is_recent(e)]
    if not recent:
        return None
    if not config:
        return _build(
            index,
            container,
            "liveness",
            "insufficient_evidence",
            "insufficient",
            f"Liveness failures reported for container {name}, which has no liveness probe",
            "Recent events report liveness failures, but the current configuration has no such probe. "
            "The probe may have just been removed.",
            [fact("Recent events report liveness probe failures.", *recent[:2], observed_at=recent[0].observed_at)],
            conflicting=["Events report liveness failures, but no liveness probe is configured now."],
        )
    killing = [
        event
        for event in index.events_for("Pod", pod_name, name)
        if event.data["reason"] == "Killing"
        and "failed liveness probe" in (event.data["message"] or "")
        and index.is_recent(event)
    ]
    restarts = data["restart_count"] or 0
    total = sum(event.data["count"] or 1 for event in recent)
    if not killing and restarts == 0 and total < REPEATED_FAILURES:
        return None
    extra = [fact(f"Container {name} has restarted {count_of(restarts, 'time')}.", container)]
    if killing:
        extra.append(
            fact(
                f"The kubelet killed the container for failing this probe {count_of(killing[0].data['count'] or 1, 'time')}.",
                killing[0],
                observed_at=killing[0].observed_at,
            )
        )
    return _confirmed_failure(index, container, "liveness", config, recent, extra)


def _check_startup(index, container) -> Diagnosis | None:
    data = container.data
    config = data["probes"]["startup"]
    if not config or data["started"] is not False:
        return None
    recent = [
        e for e in _failure_events(index, data["pod"], data["container"], "startup") if index.is_recent(e)
    ]
    if not recent:
        return None
    return _confirmed_failure(
        index,
        container,
        "startup",
        config,
        recent,
        [fact(f"Container {data['container']} has not finished starting.", container)],
    )


def evaluate(index: EvidenceIndex) -> list[Diagnosis]:
    results = []
    for container in index.containers():
        if not container.data["status_reported"]:
            continue
        for check in (_check_startup, _check_liveness, _check_readiness):
            diagnosis = check(index, container)
            if diagnosis:
                results.append(diagnosis)
    return results


RULE = Rule(name="probe_failure", evaluate=evaluate, required=("pod",), supporting=("event",))
