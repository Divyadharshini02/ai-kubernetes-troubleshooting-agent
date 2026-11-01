"""Scenario 1: containers that keep crashing or restarting.

Real clusters rarely stay in one tidy state. A crash-looping container was observed as
waiting/CrashLoopBackOff, as terminated/Error, and as running and ready just after a
restart. The rule therefore relies on restarts and how recently they happened, not on
one reason string.
"""

import re

from app.diagnostics.common import Rule, cause, count_of, describe_pod_step, fact, make_diagnosis, pod_resource
from app.diagnostics.index import EvidenceIndex
from app.models.diagnosis import Diagnosis, Recommendation
from app.models.evidence import EvidenceRecord

SCENARIO = "crash_loop_backoff"
REPEATED_RESTARTS = 2

_LOG_PATTERNS = [
    (
        re.compile(
            r"(?i)\b(missing|not set|undefined|required|invalid)\b.{0,40}\b(env|environment|config|configuration|variable|setting)"
            r"|\b(env|environment|config|configuration|variable|setting)\b.{0,40}\b(missing|not set|undefined|required|not found)"
        ),
        "The application may be missing required configuration.",
    ),
    (
        re.compile(
            r"(?i)connection refused|could not connect|failed to connect|no such host"
            r"|name or service not known|econnrefused|connection timed out"
        ),
        "The application may be failing to reach a dependency.",
    ),
    (
        re.compile(r"(?i)permission denied|access denied|read-only file system"),
        "The application may lack permission to access something it needs.",
    ),
]
_GENERIC_ERROR = re.compile(r"(?i)\b(error|fatal|panic|exception|traceback|failed)\b")

_EXIT_CODE_CAUSES = {
    126: "Exit code 126 usually means the container command could not be executed.",
    127: "Exit code 127 usually means the container command was not found.",
    137: "Exit code 137 means the process received SIGKILL, from the kernel or the kubelet.",
    143: "Exit code 143 means the process received SIGTERM and shut down.",
}


def _backoff_events(index: EvidenceIndex, pod: str, container: str) -> list[EvidenceRecord]:
    return [
        event
        for event in index.events_for("Pod", pod, container)
        if event.data["reason"] == "BackOff"
        and "restarting failed container" in (event.data["message"] or "")
        and index.is_recent(event)
    ]


def _liveness_events(index: EvidenceIndex, pod: str, container: str) -> list[EvidenceRecord]:
    return [
        event
        for event in index.events_for("Pod", pod, container)
        if index.is_recent(event)
        and (
            (event.data["reason"] == "Unhealthy" and "Liveness probe failed" in (event.data["message"] or ""))
            or (event.data["reason"] == "Killing" and "failed liveness probe" in (event.data["message"] or ""))
        )
    ]


def _latest_termination(container: EvidenceRecord, last: EvidenceRecord | None) -> tuple[dict | None, EvidenceRecord | None]:
    state = container.data["state"] or {}
    if state.get("kind") == "terminated":
        return state, container
    if last:
        return last.data["last_state"], last
    return None, None


def _classify(index, container: EvidenceRecord, last: EvidenceRecord | None, backoff: list) -> str | None:
    """Return active, uncertain, or None when there is no current sign of a problem."""
    data = container.data
    state = data["state"] or {}
    restarts = data["restart_count"] or 0
    if state.get("kind") == "waiting" and state.get("reason") == "CrashLoopBackOff":
        return "active"
    if state.get("kind") == "terminated" and restarts >= 1 and (state.get("exit_code") != 0 or backoff):
        return "active"
    recent_activity = bool(backoff) or (last is not None and index.is_recent(last))
    if restarts >= REPEATED_RESTARTS and recent_activity:
        return "active"
    if restarts >= 1 and recent_activity:
        return "uncertain"
    return None


def _log_causes(logs: list[EvidenceRecord], explained: bool):
    """Find log lines that suggest a cause. Log text is matched as data only.

    A generic error line is only a clue when nothing better is known. Once the cause is
    explained, such a line may be a side effect: a failing probe's own request gets logged.
    """
    facts, causes = [], []
    seen = set()
    for record in logs:
        lines = record.data["text"].splitlines()
        for pattern, statement in _LOG_PATTERNS:
            if statement in seen:
                continue
            match = next((line for line in reversed(lines) if pattern.search(line)), None)
            if match:
                seen.add(statement)
                facts.append(fact("A log line matches a known failure pattern.", record, excerpt=match))
                causes.append(cause(statement, record))
    if not causes and not explained:
        for record in logs:
            match = next(
                (line for line in reversed(record.data["text"].splitlines()) if _GENERIC_ERROR.search(line)),
                None,
            )
            if match:
                facts.append(fact("A log line reports an error.", record, excerpt=match))
                causes.append(cause("The application may be reporting a startup or runtime error.", record))
                break
    return facts, causes


def _recommendations(index, pod, name, container, logs, is_oom) -> list[Recommendation]:
    ids = [container.evidence_id, *[log.evidence_id for log in logs]]
    steps = [
        Recommendation(
            action=f"Read the logs of container {name}, including the previous attempt.",
            reason="Application output is the most direct evidence of why a process exited.",
            risk="None. These commands only read.",
            expected_impact="No change to the cluster.",
            how_to_apply=[
                f"kubectl logs {pod} -n {index.namespace} -c {name}",
                f"kubectl logs {pod} -n {index.namespace} -c {name} --previous",
            ],
            how_to_verify=[f"kubectl get pod {pod} -n {index.namespace}"],
            evidence_ids=ids,
        )
    ]
    if is_oom:
        steps.append(
            Recommendation(
                action=f"Review the memory limit of container {name} and the application's memory use.",
                reason="The container was terminated with reason OOMKilled.",
                risk="A higher limit uses more node memory. A memory leak would still recur.",
                expected_impact="The container stops being killed if the limit was too low.",
                how_to_apply=["Change resources.limits.memory in the workload manifest, then apply it yourself."],
                how_to_verify=[
                    f"kubectl get pod {pod} -n {index.namespace} --watch",
                    "Run this diagnosis again and confirm the restart count stops rising.",
                ],
                evidence_ids=[container.evidence_id],
            )
        )
    return steps


def _diagnose_container(index: EvidenceIndex, container: EvidenceRecord) -> Diagnosis | None:
    data = container.data
    pod_name, name = data["pod"], data["container"]
    pod = index.pod(pod_name)
    last = index.last_state(pod_name, name)
    backoff = _backoff_events(index, pod_name, name)
    verdict = _classify(index, container, last, backoff)
    if verdict is None:
        return None

    state = data["state"] or {}
    restarts = data["restart_count"] or 0
    state_text = f"{state.get('kind')}/{state.get('reason')}" if state.get("reason") else str(state.get("kind"))
    facts = [fact(f"Container {name} is currently {state_text} and has restarted {count_of(restarts, 'time')}.", container)]
    causes, missing = [], []

    termination, termination_record = _latest_termination(container, last)
    is_oom = False
    if termination:
        facts.append(
            fact(
                f"The latest termination had exit code {termination.get('exit_code')} "
                f"and reason {termination.get('reason')}.",
                termination_record,
                observed_at=termination.get("finished_at"),
            )
        )
        is_oom = termination.get("reason") == "OOMKilled"
        if last and termination_record is not last:
            previous = last.data["last_state"]
            facts.append(
                fact(
                    f"The termination before that had exit code {previous.get('exit_code')} "
                    f"and reason {previous.get('reason')}.",
                    last,
                    observed_at=previous.get("finished_at"),
                )
            )
    else:
        missing.append("The cluster reported no termination details for this container.")
    for event in backoff[:1]:
        facts.append(
            fact(
                f"The kubelet reported restart back-off {count_of(event.data['count'] or 1, 'time')}.",
                event,
                observed_at=event.observed_at,
            )
        )

    strength = "low"
    limit = data["resources"]["limits"].get("memory")
    if is_oom:
        facts.append(fact(f"The container's memory limit is {limit or 'not set'}.", container))
        causes.append(cause("The container exceeded its memory limit.", termination_record, container))
        strength = "high"

    liveness = _liveness_events(index, pod_name, name)
    if liveness:
        facts.append(
            fact(
                "Recent events show liveness probe failures for this container.",
                *liveness[:2],
                observed_at=liveness[0].observed_at,
            )
        )
        causes.append(cause("Liveness probe failures may be causing the restarts.", *liveness[:2]))
        strength = "high"

    if termination and not is_oom:
        exit_cause = _EXIT_CODE_CAUSES.get(termination.get("exit_code"))
        if exit_cause:
            causes.append(cause(exit_cause, termination_record))
            strength = "medium" if strength == "low" else strength
        elif termination.get("exit_code") == 0 and not liveness:
            causes.append(
                cause("The process exited successfully, but the restart policy runs it again.", termination_record)
            )
        elif termination.get("exit_code") != 0:
            causes.append(
                cause(
                    f"The application exited with code {termination.get('exit_code')}. "
                    "This alone does not identify why.",
                    termination_record,
                )
            )

    streams = [index.logs(pod_name, name, "current"), index.logs(pod_name, name, "previous")]
    streams = [record for record in streams if record is not None]
    with_text = [record for record in streams if record.status == "collected"]
    log_facts, log_causes = _log_causes(with_text, explained=is_oom or bool(liveness))
    facts.extend(log_facts)
    causes.extend(log_causes)
    if log_causes and strength == "low":
        strength = "medium"
    empty = [record for record in streams if record.status == "empty"]
    if not with_text and empty:
        facts.append(fact("The container wrote no log output.", *empty))
    elif not with_text:
        missing.append("No log output could be read for this container.")
    if not index.has("event"):
        missing.append("Events could not be collected, so probe failures and back-off cannot be checked.")

    steps = _recommendations(index, pod_name, name, container, with_text, is_oom)
    steps.append(describe_pod_step(index, pod_name, pod))

    if verdict == "uncertain":
        return make_diagnosis(
            index,
            diagnosis_id=f"{SCENARIO}:{container.namespace}/{pod_name}/{name}",
            scenario=SCENARIO,
            title=f"Container {name} restarted recently",
            status="insufficient_evidence",
            strength="insufficient",
            resource=pod_resource(pod, name),
            explanation=(
                f"Container {name} restarted recently, but one restart does not establish "
                "a crash loop. Collect evidence again to see whether the count keeps rising."
            ),
            facts=facts,
            causes=causes,
            missing=missing,
            conflicting=(
                ["The container is running now, but evidence shows a recent restart."]
                if state.get("kind") == "running"
                else []
            ),
            recommendations=steps,
        )
    return make_diagnosis(
        index,
        diagnosis_id=f"{SCENARIO}:{container.namespace}/{pod_name}/{name}",
        scenario=SCENARIO,
        title=f"Container {name} is restarting repeatedly",
        status="problem_detected",
        strength=strength,
        resource=pod_resource(pod, name),
        explanation=(
            f"Container {name} in pod {pod_name} has restarted {count_of(restarts, 'time')} and is still "
            "crashing or restarting. Possible causes below are inferences from the evidence."
        ),
        facts=facts,
        causes=causes,
        missing=missing,
        recommendations=steps,
    )


def evaluate(index: EvidenceIndex) -> list[Diagnosis]:
    results = [_diagnose_container(index, container) for container in index.containers()]
    return [result for result in results if result]


RULE = Rule(name="crash_loop", evaluate=evaluate, required=("pod",), supporting=("event", "logs"))
