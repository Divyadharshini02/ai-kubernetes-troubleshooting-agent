from app.diagnostics.rules.service_selector import evaluate
from evidence_factory import (
    container,
    deployment,
    endpoint_slice,
    index_of,
    pod,
    service,
    statements,
    unavailable,
)


def diagnose(*records):
    return evaluate(index_of(*records))


def named_port(target):
    return [{"name": "http", "port": 80, "target_port": target, "protocol": "TCP", "node_port": None}]


# Positive matches.


def test_typo_in_selector_is_high_strength_and_shows_near_match():
    result = diagnose(
        service(selector={"app": "wbe"}),
        pod(labels={"app": "web"}),
        endpoint_slice(),
    )[0]

    assert result.status == "problem_detected"
    assert result.scenario == "service_selector"
    assert result.title == "Service web selector matches no pods"
    assert result.evidence_strength == "high"
    assert result.affected_resource.kind == "Service"
    assert "'app': 'web'" in statements(result.confirmed_facts)
    assert "typo" in statements(result.possible_causes)


def test_every_selector_label_must_match():
    result = diagnose(
        service(selector={"app": "web", "tier": "front"}),
        pod(labels={"app": "web"}),
        endpoint_slice(),
    )[0]

    assert "matches no pods" in result.title


def test_no_related_pods_is_medium_with_the_alternative_stated():
    result = diagnose(service(selector={"app": "web"}), pod(labels={"other": "thing"}), endpoint_slice())[0]

    assert result.evidence_strength == "medium"
    assert "typo" not in statements(result.possible_causes)
    assert "not be deployed" in statements(result.possible_causes)


def test_namespace_with_no_pods_is_not_called_a_typo():
    result = diagnose(service(), endpoint_slice())[0]

    assert result.status == "problem_detected"
    assert "not be deployed" in statements(result.possible_causes)
    assert "typo" not in statements(result.possible_causes)


def test_named_target_port_that_no_pod_declares_is_detected():
    result = diagnose(
        service(ports=named_port("metrics")),
        pod(labels={"app": "web"}),
        container(ports=[{"name": "http", "container_port": 80, "protocol": "TCP"}]),
        endpoint_slice(ready=["web"]),
    )[0]

    assert result.status == "problem_detected"
    assert "port name" in result.title
    assert "metrics" in statements(result.confirmed_facts)


# It must not assume every unavailable Service has a selector mismatch.


def test_matching_pods_that_are_not_ready_are_not_called_a_mismatch():
    result = diagnose(
        service(),
        pod(labels={"app": "web"}, ready=False),
        container(ready=False),
        endpoint_slice(not_ready=["web"]),
    )[0]

    assert result.title == "Service web matches pods, but none are ready"
    assert "not a selector mismatch" in statements(result.possible_causes)
    assert "typo" not in statements(result.possible_causes)


def test_workload_scaled_to_zero_is_not_called_a_mismatch():
    result = diagnose(service(), deployment(replicas=0), endpoint_slice())[0]

    assert "with no pods" in result.title
    assert "scaled to zero" in statements(result.possible_causes)
    assert "typo" not in statements(result.possible_causes)


# Negative matches.


def test_working_service_is_not_flagged():
    assert diagnose(service(), pod(labels={"app": "web"}), container(), endpoint_slice(ready=["web"])) == []


def test_partly_ready_service_is_not_flagged():
    results = diagnose(
        service(),
        pod("one", labels={"app": "web"}),
        pod("two", labels={"app": "web"}, ready=False),
        endpoint_slice(ready=["one"], not_ready=["two"]),
    )

    assert results == []


def test_service_without_selector_is_not_flagged():
    assert diagnose(service(selector={})) == []


def test_external_name_service_is_not_flagged():
    assert diagnose(service(service_type="ExternalName")) == []


def test_numeric_target_port_need_not_be_declared():
    # Kubernetes does not require containerPort to be declared for traffic to work.
    results = diagnose(
        service(ports=named_port(9999)),
        pod(labels={"app": "web"}),
        container(ports=[{"name": "http", "container_port": 80, "protocol": "TCP"}]),
        endpoint_slice(ready=["web"]),
    )

    assert results == []


def test_named_target_port_that_a_pod_declares_is_not_flagged():
    results = diagnose(
        service(ports=named_port("http")),
        pod(labels={"app": "web"}),
        container(ports=[{"name": "http", "container_port": 80, "protocol": "TCP"}]),
        endpoint_slice(ready=["web"]),
    )

    assert results == []


def test_completed_pods_are_not_eligible_matches():
    result = diagnose(
        service(),
        pod(labels={"app": "web"}, phase="Succeeded", ready=False),
        endpoint_slice(),
    )[0]

    assert "matches no pods" in result.title


def test_terminating_pods_are_not_eligible_matches():
    result = diagnose(service(), pod(labels={"app": "web"}, deleting=True), endpoint_slice())[0]

    assert "matches no pods" in result.title


# Missing or incomplete evidence.


def test_truncated_pod_list_cannot_prove_no_match():
    result = diagnose(service(), pod(labels={"other": "x"}), unavailable("pod", "skipped", "limit"), endpoint_slice())[0]

    assert result.status == "insufficient_evidence"
    assert any("truncated" in item for item in result.missing_evidence)


def test_unavailable_endpoint_slices_lower_strength_and_are_reported():
    result = diagnose(service(selector={"app": "wbe"}), pod(labels={"app": "web"}), unavailable("endpointslice"))[0]

    assert result.evidence_strength == "low"
    assert any("EndpointSlices could not be collected" in item for item in result.missing_evidence)


def test_matching_pods_with_unavailable_slices_are_not_flagged():
    assert diagnose(service(), pod(labels={"app": "web"}, ready=False), unavailable("endpointslice")) == []


def test_service_with_no_slice_at_all_says_so():
    result = diagnose(service(selector={"app": "wbe"}), pod(labels={"app": "web"}))[0]

    assert "No EndpointSlice exists" in statements(result.confirmed_facts)


# Conflicting or stale evidence.


def test_ready_endpoints_with_no_matching_pod_is_a_conflict():
    result = diagnose(service(), pod(labels={"other": "x"}), endpoint_slice(ready=["gone"]))[0]

    assert result.status == "insufficient_evidence"
    assert result.evidence_strength == "insufficient"
    assert any("no collected pod matches" in item for item in result.conflicting_evidence)


def test_ready_pods_with_no_ready_endpoints_is_a_conflict():
    result = diagnose(service(), pod(labels={"app": "web"}), endpoint_slice(not_ready=["web"]))[0]

    assert result.status == "insufficient_evidence"
    assert any("no endpoint is ready" in item for item in result.conflicting_evidence)


# Multiple simultaneous issues.


def test_each_service_is_judged_independently():
    results = diagnose(
        service("broken", selector={"app": "nope"}),
        service("working"),
        service("unready", selector={"app": "slow"}),
        pod("web", labels={"app": "web"}),
        pod("slow", labels={"app": "slow"}, ready=False),
        endpoint_slice("working", ready=["web"]),
    )

    assert [result.affected_resource.name for result in results] == ["broken", "unready"]
