import pytest

from app.diagnostics.rules.probes import evaluate
from evidence_factory import (
    OLD,
    container,
    event,
    index_of,
    pod,
    probe,
    statements,
    terminated,
    unavailable,
    waiting,
)

READINESS_404 = "Readiness probe failed: HTTP probe failed with statuscode: 404"
READINESS_REFUSED = 'Readiness probe failed: Get "http://10.0.0.1:80/": dial tcp 10.0.0.1:80: connect: connection refused'
LIVENESS_404 = "Liveness probe failed: HTTP probe failed with statuscode: 404"
KILLING = "Container app failed liveness probe, will be restarted"


def diagnose(*records):
    return evaluate(index_of(*records))


def not_ready(**fields):
    return container(ready=False, readiness=probe(), **fields)


# Readiness.


def test_readiness_failure_is_detected_and_named():
    results = diagnose(pod(ready=False), not_ready(), event("Unhealthy", READINESS_404, count=24))

    assert len(results) == 1
    result = results[0]
    assert result.status == "problem_detected"
    assert result.scenario == "probe_failure"
    assert result.title == "Readiness probe of container app is failing"
    assert result.diagnosis_id.startswith("probe_failure:readiness:")
    assert result.evidence_strength == "high"
    assert "24 readiness probe failures" in statements(result.confirmed_facts)
    assert "HTTP GET /health on port 8080" in statements(result.confirmed_facts)
    assert "timeout 1s" in statements(result.confirmed_facts)


def test_latest_failure_message_decides_the_cause():
    # Observed on a real cluster: connection refused during start-up, then 404 for good.
    result = diagnose(
        pod(ready=False),
        not_ready(),
        event("Unhealthy", READINESS_REFUSED, age=300),
        event("Unhealthy", READINESS_404, age=30, count=24),
    )[0]

    text = statements(result.possible_causes)
    assert "HTTP 404" in text
    assert "Nothing was listening" not in text


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (READINESS_404, "probe path may be wrong"),
        (READINESS_REFUSED, "Nothing was listening"),
        ("Readiness probe failed: HTTP probe failed with statuscode: 503", "server error"),
        ("Readiness probe failed: HTTP probe failed with statuscode: 401", "unsuccessful HTTP status"),
        ("Readiness probe failed: context deadline exceeded", "too slowly"),
    ],
)
def test_failure_messages_suggest_causes(message, expected):
    result = diagnose(pod(ready=False), not_ready(), event("Unhealthy", message))[0]

    assert expected in statements(result.possible_causes)
    assert result.evidence_strength == "high"


def test_unrecognised_failure_message_is_medium_without_a_cause():
    result = diagnose(pod(ready=False), not_ready(), event("Unhealthy", "Readiness probe failed: something new"))[0]

    assert result.evidence_strength == "medium"
    assert result.possible_causes == []


def test_probe_port_is_compared_with_declared_ports():
    result = diagnose(
        pod(ready=False),
        not_ready(ports=[{"name": "http", "container_port": 80, "protocol": "TCP"}]),
        event("Unhealthy", READINESS_REFUSED),
    )[0]

    assert "uses port 8080" in statements(result.confirmed_facts)
    assert "may not match the container's ports" in statements(result.possible_causes)


def test_named_probe_port_that_is_not_declared_is_reported():
    named = probe({"type": "http_get", "path": "/", "port": "web", "scheme": "HTTP", "header_names": []})
    result = diagnose(
        pod(ready=False),
        container(ready=False, readiness=named),
        event("Unhealthy", READINESS_REFUSED),
    )[0]

    assert "port name web" in statements(result.confirmed_facts)


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        ({"type": "tcp_socket", "port": 80}, "TCP check on port 80"),
        ({"type": "grpc", "port": 90, "service": None}, "gRPC check on port 90"),
        ({"type": "exec", "command": ["check"]}, "a command run in the container"),
    ],
)
def test_other_probe_types_are_described(handler, expected):
    result = diagnose(
        pod(ready=False),
        container(ready=False, readiness=probe(handler)),
        event("Unhealthy", "Readiness probe failed: nope"),
    )[0]

    assert expected in statements(result.confirmed_facts)


def test_omitted_initial_delay_is_shown_as_the_kubernetes_default():
    # Regression: real output printed "delay Nones".
    config = probe()
    config["initial_delay_seconds"] = None

    result = diagnose(pod(ready=False), container(ready=False, readiness=config), event("Unhealthy", READINESS_404))[0]

    assert "delay 0s" in statements(result.confirmed_facts)
    assert "None" not in statements(result.confirmed_facts)


def test_single_failure_uses_singular_wording():
    result = diagnose(pod(ready=False), not_ready(restarts=1), event("Unhealthy", READINESS_404))[0]

    text = statements(result.confirmed_facts)
    assert "1 readiness probe failure." in text
    assert "1 restart." in text


# Liveness and startup are kept distinct from readiness.


def test_liveness_failure_with_restart_is_detected_and_named():
    results = diagnose(
        pod(),
        container(restarts=5, liveness=probe()),
        event("Unhealthy", LIVENESS_404, count=5),
        event("Killing", KILLING, event_type="Normal", count=5),
    )

    assert len(results) == 1
    assert results[0].title == "Liveness probe of container app is failing"
    assert results[0].diagnosis_id.startswith("probe_failure:liveness:")
    assert "killed the container" in statements(results[0].confirmed_facts)


def test_liveness_failure_is_reported_while_the_container_is_waiting():
    result = diagnose(
        pod(ready=False),
        container(ready=False, restarts=5, state=waiting("CrashLoopBackOff"), liveness=probe()),
        event("Unhealthy", LIVENESS_404),
    )[0]

    assert result.status == "problem_detected"


def test_startup_failure_is_detected_and_named():
    results = diagnose(
        pod(ready=False),
        container(ready=False, started=False, startup=probe()),
        event("Unhealthy", "Startup probe failed: connect: connection refused"),
    )

    assert len(results) == 1
    assert results[0].title == "Startup probe of container app is failing"
    assert results[0].diagnosis_id.startswith("probe_failure:startup:")


def test_failure_type_comes_from_the_event_not_the_configuration():
    results = diagnose(
        pod(ready=False),
        container(ready=False, readiness=probe(), liveness=probe()),
        event("Unhealthy", READINESS_404),
    )

    assert [result.title for result in results] == ["Readiness probe of container app is failing"]


# Negative matches.


def test_healthy_container_with_probes_is_not_flagged():
    assert diagnose(pod(), container(readiness=probe(), liveness=probe(), startup=probe())) == []


def test_single_failure_during_start_up_is_not_flagged():
    # Observed on a real cluster and normal: an application is not listening yet.
    assert diagnose(pod(), container(readiness=probe()), event("Unhealthy", READINESS_REFUSED)) == []


def test_single_liveness_failure_below_the_threshold_is_not_flagged():
    assert diagnose(pod(), container(liveness=probe()), event("Unhealthy", LIVENESS_404)) == []


def test_not_ready_container_without_a_readiness_probe_is_not_flagged():
    assert diagnose(pod(ready=False), container(ready=False)) == []


def test_crashed_container_is_not_a_readiness_failure():
    assert diagnose(pod(ready=False), container(ready=False, readiness=probe(), state=terminated(1))) == []


def test_started_container_with_old_startup_failures_is_not_flagged():
    assert diagnose(pod(), container(startup=probe()), event("Unhealthy", "Startup probe failed: x")) == []


def test_other_unhealthy_events_are_ignored():
    assert diagnose(pod(), container(liveness=probe()), event("Unhealthy", "Something else failed", count=9)) == []


def test_unreported_container_is_skipped():
    assert diagnose(pod(phase="Pending", node=None), container(reported=False, readiness=probe())) == []


def test_event_for_another_container_is_not_counted():
    result = diagnose(
        pod(ready=False),
        container(name="one", ready=False, readiness=probe()),
        event("Unhealthy", READINESS_404, container_name="two"),
    )[0]

    assert result.status == "insufficient_evidence"


# Missing or incomplete evidence.


def test_not_ready_without_a_failure_event_is_insufficient():
    result = diagnose(pod(ready=False), not_ready())[0]

    assert result.status == "insufficient_evidence"
    assert result.evidence_strength == "insufficient"
    assert any("No recent readiness probe failure" in item for item in result.missing_evidence)


def test_unavailable_events_are_insufficient_and_say_why():
    result = diagnose(pod(ready=False), not_ready(), unavailable("event"))[0]

    assert result.status == "insufficient_evidence"
    assert any("Events could not be collected" in item for item in result.missing_evidence)


# Conflicting or stale evidence.


def test_old_failures_on_a_healthy_container_are_not_flagged():
    assert (
        diagnose(
            pod(),
            container(readiness=probe(), liveness=probe()),
            event("Unhealthy", READINESS_404, age=OLD, count=50),
            event("Unhealthy", LIVENESS_404, age=OLD, count=50),
            event("Killing", KILLING, age=OLD),
        )
        == []
    )


def test_old_failures_on_a_not_ready_container_are_insufficient_and_flagged():
    result = diagnose(pod(ready=False), not_ready(), event("Unhealthy", READINESS_404, age=OLD))[0]

    assert result.status == "insufficient_evidence"
    assert any("old readiness failure" in item for item in result.conflicting_evidence)


def test_ready_now_after_repeated_recent_failures_is_a_conflict():
    result = diagnose(pod(), container(readiness=probe()), event("Unhealthy", READINESS_404, count=5))[0]

    assert result.status == "insufficient_evidence"
    assert any("ready now" in item for item in result.conflicting_evidence)


def test_liveness_events_with_no_probe_configured_is_a_conflict():
    result = diagnose(pod(), container(restarts=2), event("Unhealthy", LIVENESS_404))[0]

    assert result.status == "insufficient_evidence"
    assert any("no liveness probe is configured" in item for item in result.conflicting_evidence)


# Multiple simultaneous issues.


def test_readiness_and_liveness_failing_together_are_both_reported():
    results = diagnose(
        pod(ready=False),
        container(ready=False, restarts=2, readiness=probe(), liveness=probe()),
        event("Unhealthy", READINESS_404),
        event("Unhealthy", LIVENESS_404),
        event("Killing", KILLING),
    )

    assert {result.title.split()[0] for result in results} == {"Readiness", "Liveness"}


def test_each_container_is_judged_separately():
    results = diagnose(
        pod(ready=False),
        container(name="one", ready=False, readiness=probe()),
        container(name="two", ready=False, readiness=probe()),
        event("Unhealthy", READINESS_404, container_name="one"),
    )

    by_container = {result.affected_resource.container: result.status for result in results}
    assert by_container == {"one": "problem_detected", "two": "insufficient_evidence"}
