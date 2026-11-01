import pytest

from app.diagnostics.rules.crash_loop import evaluate
from evidence_factory import (
    OLD,
    container,
    event,
    index_of,
    last_state,
    logs,
    pod,
    statements,
    terminated,
    unavailable,
    waiting,
)

BACKOFF = "Back-off restarting failed container app in pod web"


def diagnose(*records):
    return evaluate(index_of(*records))


# Positive matches, including every state a real crash loop was observed in.


def test_waiting_crash_loop_back_off_is_detected():
    results = diagnose(
        pod(ready=False),
        container(ready=False, restarts=3, state=waiting("CrashLoopBackOff", "back-off")),
        last_state(exit_code=2),
    )

    assert len(results) == 1
    assert results[0].status == "problem_detected"
    assert results[0].scenario == "crash_loop_backoff"
    assert results[0].affected_resource.container == "app"
    assert "exit code 2" in statements(results[0].confirmed_facts)


def test_terminated_with_error_after_restart_is_detected():
    results = diagnose(pod(ready=False), container(ready=False, restarts=4, state=terminated(3)), last_state(3))

    assert results[0].status == "problem_detected"
    assert "exit code 3" in statements(results[0].confirmed_facts)


def test_current_and_previous_terminations_are_both_reported():
    result = diagnose(pod(ready=False), container(ready=False, restarts=4, state=terminated(3)), last_state(5))[0]

    text = statements(result.confirmed_facts)
    assert "latest termination had exit code 3" in text
    assert "before that had exit code 5" in text


def test_running_and_ready_just_after_repeated_restarts_is_still_detected():
    # Observed on a real cluster: restarts look healthy for a moment.
    results = diagnose(pod(), container(ready=True, restarts=4), last_state(137, "OOMKilled"))

    assert results[0].status == "problem_detected"


def test_terminated_with_success_is_detected_when_back_off_is_recent():
    results = diagnose(
        pod(ready=False),
        container(ready=False, restarts=3, state=terminated(0, "Completed")),
        event("BackOff", BACKOFF),
    )

    assert results[0].status == "problem_detected"
    assert "restart policy" in statements(results[0].possible_causes)


def test_init_container_crash_loop_is_detected():
    results = diagnose(
        pod(ready=False),
        container(name="setup", container_type="init", ready=False, restarts=3, state=waiting("CrashLoopBackOff")),
    )

    assert results[0].affected_resource.container == "setup"


# Possible causes and evidence strength.


def test_cited_evidence_is_not_repeated():
    # Regression: a cause citing the same record twice was seen in real output.
    result = diagnose(pod(ready=False), container(ready=False, restarts=3, state=terminated(137, "OOMKilled")))[0]

    for item in [*result.confirmed_facts, *result.possible_causes]:
        assert len(item.evidence_ids) == len(set(item.evidence_ids))


def test_oom_kill_is_high_strength_and_reports_the_limit():
    result = diagnose(pod(), container(restarts=3, limits={"memory": "16Mi"}), last_state(137, "OOMKilled"))[0]

    assert result.evidence_strength == "high"
    assert "memory limit" in statements(result.possible_causes)
    assert "16Mi" in statements(result.confirmed_facts)


def test_liveness_failures_are_a_cause_with_high_strength():
    result = diagnose(
        pod(),
        container(restarts=3),
        last_state(0, "Completed"),
        event("Unhealthy", "Liveness probe failed: HTTP probe failed with statuscode: 404"),
        event("Killing", "Container app failed liveness probe, will be restarted", event_type="Normal"),
    )[0]

    assert result.evidence_strength == "high"
    assert "Liveness probe failures" in statements(result.possible_causes)


def test_error_line_caused_by_a_failing_probe_is_not_blamed_on_the_application():
    # Regression: nginx logged the failing probe's own request as an error.
    result = diagnose(
        pod(),
        container(restarts=3),
        last_state(0, "Completed"),
        logs(text="2026-01-01T00:00:00Z open() failed (2: No such file or directory)\n"),
        event("Unhealthy", "Liveness probe failed: HTTP probe failed with statuscode: 404"),
    )[0]

    assert "startup or runtime error" not in statements(result.possible_causes)


def test_error_line_after_an_oom_kill_is_not_blamed_on_the_application():
    result = diagnose(
        pod(),
        container(restarts=3),
        last_state(137, "OOMKilled"),
        logs(text="2026-01-01T00:00:00Z something failed\n"),
    )[0]

    assert "startup or runtime error" not in statements(result.possible_causes)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("config value DB_URL is missing", "required configuration"),
        ("required environment variable", "required configuration"),
        ("dial tcp: connection refused", "reach a dependency"),
        ("lookup db: no such host", "reach a dependency"),
        ("open /data: permission denied", "lack permission"),
        ("fatal: startup failed", "startup or runtime error"),
    ],
)
def test_log_patterns_suggest_causes(line, expected):
    result = diagnose(
        pod(ready=False),
        container(ready=False, restarts=3, state=terminated(1)),
        logs(text=f"2026-01-01T00:00:00Z {line}\n"),
    )[0]

    assert expected in statements(result.possible_causes)
    assert result.evidence_strength == "medium"
    assert any(line in (item.excerpt or "") for item in result.confirmed_facts)


@pytest.mark.parametrize(
    ("code", "expected"),
    [(126, "could not be executed"), (127, "not found"), (137, "SIGKILL"), (143, "SIGTERM")],
)
def test_known_exit_codes_explain_themselves(code, expected):
    result = diagnose(pod(ready=False), container(ready=False, restarts=3, state=terminated(code)))[0]

    assert expected in statements(result.possible_causes)
    assert result.evidence_strength == "medium"


def test_unexplained_exit_code_is_low_strength_and_says_so():
    result = diagnose(pod(ready=False), container(ready=False, restarts=3, state=terminated(3)))[0]

    assert result.evidence_strength == "low"
    assert "does not identify why" in statements(result.possible_causes)


# Negative matches.


def test_healthy_container_is_not_flagged():
    assert diagnose(pod(), container()) == []


def test_old_restarts_on_a_stable_container_are_not_flagged():
    assert diagnose(pod(), container(restarts=9), last_state(finished=OLD)) == []


def test_old_back_off_event_is_not_current_evidence():
    assert diagnose(pod(), container(restarts=9), last_state(finished=OLD), event("BackOff", BACKOFF, age=OLD)) == []


def test_completed_job_style_container_is_not_flagged():
    assert diagnose(pod(phase="Succeeded", ready=False), container(ready=False, state=terminated(0, "Completed"))) == []


def test_failure_without_a_restart_is_not_called_a_crash_loop():
    assert diagnose(pod(phase="Failed", ready=False), container(ready=False, state=terminated(1))) == []


def test_unstarted_container_is_not_flagged():
    assert diagnose(pod(phase="Pending", node=None), container(reported=False)) == []


def test_pod_being_deleted_is_ignored():
    assert diagnose(pod(deleting=True, ready=False), container(ready=False, restarts=5, state=terminated(1))) == []


# Missing or incomplete evidence.


def test_no_termination_details_is_reported_as_missing():
    result = diagnose(pod(ready=False), container(ready=False, restarts=3, state=waiting("CrashLoopBackOff")))[0]

    assert result.status == "problem_detected"
    assert result.evidence_strength == "low"
    assert any("termination details" in item for item in result.missing_evidence)


def test_unreadable_logs_are_reported_as_missing():
    result = diagnose(
        pod(ready=False),
        container(ready=False, restarts=3, state=terminated(1)),
        logs(status="not_available"),
        logs(stream="previous", status="not_available"),
    )[0]

    assert any("No log output could be read" in item for item in result.missing_evidence)


def test_empty_logs_are_a_fact_not_missing_evidence():
    # Regression: a container that wrote nothing was reported as having unreadable logs.
    result = diagnose(
        pod(),
        container(restarts=3),
        last_state(137, "OOMKilled"),
        logs(status="empty"),
        logs(stream="previous", status="not_available"),
    )[0]

    assert result.missing_evidence == []
    assert "wrote no log output" in statements(result.confirmed_facts)


def test_previous_logs_being_unavailable_is_not_held_against_the_diagnosis():
    result = diagnose(
        pod(ready=False),
        container(ready=False, restarts=3, state=terminated(3)),
        logs(text="2026-01-01T00:00:00Z startup failed\n"),
        logs(stream="previous", status="not_available"),
    )[0]

    assert result.missing_evidence == []
    assert "startup failed" in " ".join(item.excerpt or "" for item in result.confirmed_facts)


def test_unavailable_events_are_reported_as_missing():
    result = diagnose(
        pod(ready=False),
        container(ready=False, restarts=3, state=terminated(1)),
        unavailable("event"),
    )[0]

    assert any("Events could not be collected" in item for item in result.missing_evidence)


# Conflicting or stale evidence.


def test_single_recent_restart_is_insufficient_not_a_crash_loop():
    result = diagnose(pod(), container(restarts=1), last_state())[0]

    assert result.status == "insufficient_evidence"
    assert result.evidence_strength == "insufficient"
    assert any("recent restart" in item for item in result.conflicting_evidence)


def test_recent_back_off_with_running_container_is_insufficient():
    result = diagnose(pod(), container(restarts=1), last_state(finished=OLD), event("BackOff", BACKOFF))[0]

    assert result.status == "insufficient_evidence"


def test_old_liveness_events_do_not_become_a_cause():
    result = diagnose(
        pod(ready=False),
        container(ready=False, restarts=3, state=terminated(1)),
        event("Unhealthy", "Liveness probe failed: HTTP 500", age=OLD),
    )[0]

    assert "Liveness" not in statements(result.possible_causes)


# Multiple simultaneous issues.


def test_each_crashing_container_gets_its_own_diagnosis():
    results = diagnose(
        pod(ready=False),
        container(name="one", ready=False, restarts=3, state=terminated(1)),
        container(name="two", ready=False, restarts=3, state=terminated(2)),
    )

    assert [result.affected_resource.container for result in results] == ["one", "two"]


def test_several_causes_can_be_reported_together():
    result = diagnose(
        pod(),
        container(restarts=3),
        last_state(137, "OOMKilled"),
        event("Unhealthy", "Liveness probe failed: timeout"),
        logs(text="2026-01-01T00:00:00Z connection refused\n"),
    )[0]

    text = statements(result.possible_causes)
    assert "memory limit" in text
    assert "Liveness" in text
    assert "dependency" in text
