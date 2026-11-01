import pytest

from app.diagnostics.rules.image_pull import PULL_FAILURE_REASONS, evaluate
from evidence_factory import (
    OLD,
    container,
    event,
    index_of,
    pod,
    service_account,
    statements,
    unavailable,
    waiting,
)

REAL_DNS_ERROR = (
    'Failed to pull image "registry.invalid/demo/app:missing": failed to resolve reference: '
    "dial tcp: lookup registry.invalid on 192.168.65.254:53: no such host"
)


def diagnose(*records):
    return evaluate(index_of(*records))


def pulling(reason="ImagePullBackOff", message=None, **fields):
    return container(ready=False, started=False, state=waiting(reason, message), **fields)


@pytest.mark.parametrize("reason", sorted(PULL_FAILURE_REASONS))
def test_every_pull_failure_reason_is_detected(reason):
    results = diagnose(pod(phase="Pending", ready=False), pulling(reason))

    assert len(results) == 1
    assert results[0].status == "problem_detected"
    assert results[0].scenario == "image_pull_backoff"
    assert results[0].affected_resource.container == "app"


def test_facts_report_the_exact_image_and_pull_settings():
    result = diagnose(
        pod(phase="Pending", ready=False, pull_secrets=["registry-login"]),
        pulling(),
        service_account(pull_secrets=["account-login"]),
    )[0]

    text = statements(result.confirmed_facts)
    assert "registry.example/app:1" in text
    assert "registry: registry.example" in text
    assert "tag: 1" in text
    assert "registry-login" in text
    assert "account-login" in text


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (REAL_DNS_ERROR, "unable to reach the registry"),
        ("manifest unknown", "may not exist in the registry"),
        ('failed to resolve: "app:1": not found', "may not exist in the registry"),
        ("401 unauthorized: authentication required", "rejecting the credentials"),
        ("toomanyrequests: rate limit exceeded", "rate limiting"),
        ("invalid reference format", "malformed"),
    ],
)
def test_clear_errors_give_high_strength(message, expected):
    result = diagnose(pod(phase="Pending", ready=False), pulling(message=message))[0]

    assert expected in statements(result.possible_causes)
    assert result.evidence_strength == "high"


def test_ambiguous_denial_stays_medium_and_names_both_possibilities():
    result = diagnose(
        pod(phase="Pending", ready=False),
        pulling(message="pull access denied, repository does not exist or may require authorization"),
    )[0]

    assert result.evidence_strength == "medium"
    text = statements(result.possible_causes)
    assert "may not exist" in text
    assert "require credentials" in text


def test_recent_event_supplies_the_cause_when_status_has_no_detail():
    result = diagnose(
        pod(phase="Pending", ready=False),
        pulling(message='Back-off pulling image "x"'),
        event("Failed", REAL_DNS_ERROR, count=3),
    )[0]

    assert "unable to reach the registry" in statements(result.possible_causes)
    assert "3 times" in statements(result.confirmed_facts)


def test_invalid_image_name_reason_implies_a_malformed_reference():
    result = diagnose(pod(phase="Pending", ready=False), pulling("InvalidImageName"))[0]

    assert "malformed" in statements(result.possible_causes)


# Negative matches.


@pytest.mark.parametrize(
    "state",
    [
        None,
        waiting("ContainerCreating"),
        waiting("CrashLoopBackOff"),
        waiting("CreateContainerConfigError", "secret not found"),
    ],
)
def test_other_states_are_not_flagged(state):
    assert diagnose(pod(ready=False), container(ready=False, state=state)) == []


def test_recovered_container_with_old_pull_failure_events_is_not_flagged():
    assert diagnose(pod(), container(), event("Failed", REAL_DNS_ERROR, age=OLD)) == []


def test_other_failed_events_do_not_become_causes():
    result = diagnose(
        pod(phase="Pending", ready=False),
        pulling(message="back-off"),
        event("Failed", "Error: secret not found no such host"),
    )[0]

    assert result.possible_causes == []


# Missing or incomplete evidence.


def test_no_error_detail_is_low_strength_and_says_so():
    result = diagnose(pod(phase="Pending", ready=False), pulling(message="Back-off pulling image"))[0]

    assert result.status == "problem_detected"
    assert result.evidence_strength == "low"
    assert result.possible_causes == []
    assert any("explains why" in item for item in result.missing_evidence)


def test_unavailable_events_are_reported_as_missing():
    result = diagnose(pod(phase="Pending", ready=False), pulling(), unavailable("event"))[0]

    assert any("Events could not be collected" in item for item in result.missing_evidence)


def test_unavailable_service_accounts_are_reported_as_missing():
    result = diagnose(pod(phase="Pending", ready=False), pulling(), unavailable("serviceaccount"))[0]

    assert any("inherited pull secrets" in item for item in result.missing_evidence)


# Conflicting or stale evidence.


def test_old_event_is_not_used_as_a_cause_and_is_flagged():
    result = diagnose(
        pod(phase="Pending", ready=False),
        pulling(message="Back-off pulling image"),
        event("Failed", REAL_DNS_ERROR, age=OLD),
    )[0]

    assert result.possible_causes == []
    assert result.evidence_strength == "low"
    assert any("old events" in item for item in result.conflicting_evidence)


# Multiple simultaneous issues.


def test_each_failing_container_gets_its_own_diagnosis():
    results = diagnose(
        pod(phase="Pending", ready=False),
        pulling(name="one", message="manifest unknown"),
        pulling(name="two", message="no such host"),
    )

    assert [result.affected_resource.container for result in results] == ["one", "two"]
    assert "may not exist" in statements(results[0].possible_causes)
    assert "reach the registry" in statements(results[1].possible_causes)


def test_several_error_types_are_all_reported():
    result = diagnose(
        pod(phase="Pending", ready=False),
        pulling(message="no such host"),
        event("Failed", "Failed to pull image: toomanyrequests"),
    )[0]

    text = statements(result.possible_causes)
    assert "rate limiting" in text
    assert "reach the registry" in text
