import pytest

from app.diagnostics.rules.pending import evaluate
from evidence_factory import (
    OLD,
    claim,
    container,
    event,
    index_of,
    node,
    pod,
    statements,
    storage_class,
    unavailable,
    unscheduled,
)

REAL_SELECTOR = "0/1 nodes are available: 1 node(s) didn't match Pod's node affinity/selector. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling."
REAL_CPU = "0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling."
REAL_ANTI = "0/1 nodes are available: 1 node(s) didn't match pod anti-affinity rules. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod."
REAL_CLAIM = "0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found"
REAL_TAINT = "0/1 nodes are available: 1 node(s) had untolerated taint(s). preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling."


def diagnose(*records):
    return evaluate(index_of(*records))


def pending(message=REAL_SELECTOR, reason="Unschedulable", **fields):
    return pod(phase="Pending", node=None, conditions=unscheduled(reason, message), **fields)


# Positive matches, using messages captured from a real cluster.


def test_scheduler_message_is_a_confirmed_fact():
    result = diagnose(pending(node_selector={"disk": "ssd"}), node())[0]

    assert result.status == "problem_detected"
    assert result.scenario == "pending_pod"
    assert result.affected_resource.kind == "Pod"
    assert any("didn't match" in (item.excerpt or "") for item in result.confirmed_facts)


def test_node_selector_is_compared_with_real_node_labels():
    result = diagnose(pending(node_selector={"disk": "ssd"}), node(labels={"disk": "hdd"}))[0]

    assert result.evidence_strength == "high"
    assert "'disk': 'ssd'" in statements(result.confirmed_facts)


def test_insufficient_cpu_larger_than_any_node_is_high_strength():
    result = diagnose(
        pending(REAL_CPU),
        container(requests={"cpu": "1000"}, reported=False),
        node(cpu="4"),
    )[0]

    assert result.evidence_strength == "high"
    assert "more cpu than any collected node" in statements(result.possible_causes)
    assert "1000" in statements(result.confirmed_facts)


def test_insufficient_memory_is_compared_using_real_quantities():
    result = diagnose(
        pending("0/1 nodes are available: 1 Insufficient memory."),
        container(requests={"memory": "64Gi"}, reported=False),
        node(memory="8Gi"),
    )[0]

    assert result.evidence_strength == "high"
    assert "more memory" in statements(result.possible_causes)


def test_request_that_fits_a_node_stays_medium():
    # Other pods may be using the capacity, and that is not collected.
    result = diagnose(pending(REAL_CPU), container(requests={"cpu": "1"}, reported=False), node(cpu="4"))[0]

    assert result.evidence_strength == "medium"
    assert "enough free cpu" in statements(result.possible_causes)


def test_requests_across_app_containers_are_totalled():
    result = diagnose(
        pending(REAL_CPU),
        container(name="one", requests={"cpu": "3"}, reported=False),
        container(name="two", requests={"cpu": "3"}, reported=False),
        node(cpu="4"),
    )[0]

    assert result.evidence_strength == "high"


def test_untolerated_taint_is_verified_on_nodes():
    result = diagnose(
        pending(REAL_TAINT),
        node(taints=[{"key": "dedicated", "value": "db", "effect": "NoSchedule"}]),
    )[0]

    assert result.evidence_strength == "high"
    assert "dedicated" in statements(result.confirmed_facts)


def test_anti_affinity_is_verified_on_the_pod():
    result = diagnose(pending(REAL_ANTI, affinity={"podAntiAffinity": {"required": []}}), node())[0]

    assert result.evidence_strength == "high"
    assert "anti-affinity" in statements(result.possible_causes)


def test_pod_affinity_is_not_confused_with_anti_affinity():
    result = diagnose(
        pending("0/1 nodes are available: 1 node(s) didn't match pod affinity rules.", affinity={"podAffinity": {}}),
        node(),
    )[0]

    assert "Pod affinity" in statements(result.possible_causes)
    assert "anti-affinity" not in statements(result.possible_causes)


def test_missing_storage_class_is_verified():
    result = diagnose(
        pending(REAL_CLAIM, claims=["data"]),
        claim(storage_class="no-such-class"),
        storage_class("standard"),
    )[0]

    assert result.evidence_strength == "high"
    assert "no-such-class" in statements(result.confirmed_facts)
    assert "does not exist" in statements(result.possible_causes)


def test_missing_claim_is_verified():
    result = diagnose(pending('persistentvolumeclaim "data" not found', claims=["data"]))[0]

    assert result.evidence_strength == "high"
    assert "needs to be created" in statements(result.possible_causes)


def test_claim_event_is_included():
    result = diagnose(
        pending(REAL_CLAIM, claims=["data"]),
        claim(storage_class="standard"),
        storage_class("standard"),
        event("ProvisioningFailed", "provisioner error", name="data", kind="PersistentVolumeClaim"),
    )[0]

    assert any("provisioner error" in (item.excerpt or "") for item in result.confirmed_facts)
    assert "not bound" in statements(result.possible_causes)


def test_cordoned_nodes_are_verified():
    result = diagnose(pending("0/1 nodes are available: 1 node(s) were unschedulable."), node(unschedulable=True))[0]

    assert result.evidence_strength == "high"
    assert "node-1" in statements(result.confirmed_facts)


def test_scheduling_gate_is_recognised():
    result = diagnose(pending("Scheduling is blocked", reason="SchedulingGated"))[0]

    assert "scheduling gates" in statements(result.possible_causes)


@pytest.mark.parametrize(
    "message",
    ["volume node affinity conflict", "1 Too many pods", "1 node(s) didn't have free ports"],
)
def test_blockers_that_cannot_be_verified_stay_medium(message):
    result = diagnose(pending(f"0/1 nodes are available: {message}."), node())[0]

    assert result.evidence_strength == "medium"
    assert result.possible_causes


# Negative matches.


def test_running_pod_with_a_failed_scheduling_event_is_not_flagged():
    assert diagnose(pod(), container(), event("FailedScheduling", REAL_CPU, age=OLD, container_name=None)) == []


def test_running_pod_with_a_leftover_unscheduled_condition_is_not_flagged():
    assert diagnose(pod(phase="Running", node=None, conditions=unscheduled())) == []


def test_scheduled_pending_pod_is_not_a_scheduling_problem():
    # A scheduled pod pending on image pull or start-up belongs to other rules.
    assert diagnose(pod(phase="Pending", ready=False)) == []


def test_pod_being_deleted_is_ignored():
    assert diagnose(pending(deleting=True)) == []


# Missing or incomplete evidence.


def test_pending_without_any_condition_is_insufficient():
    result = diagnose(pod(phase="Pending", node=None, conditions=[]))[0]

    assert result.status == "insufficient_evidence"
    assert result.evidence_strength == "insufficient"
    assert any("no PodScheduled" in item for item in result.missing_evidence)


def test_unknown_scheduler_message_is_low_strength_without_a_cause():
    result = diagnose(pending("0/1 nodes are available: 1 something new happened."), node())[0]

    assert result.status == "problem_detected"
    assert result.evidence_strength == "low"
    assert result.possible_causes == []
    assert any("did not match a known blocker" in item for item in result.missing_evidence)


def test_unavailable_nodes_keep_the_scheduler_message_at_medium():
    result = diagnose(pending(node_selector={"disk": "ssd"}), unavailable("node"))[0]

    assert result.evidence_strength == "medium"
    assert any("Nodes could not be collected" in item for item in result.missing_evidence)


def test_unavailable_events_do_not_hide_the_current_condition():
    result = diagnose(pending(node_selector={"disk": "ssd"}), node(), unavailable("event"))[0]

    assert result.status == "problem_detected"
    assert any("Events could not be collected" in item for item in result.missing_evidence)


def test_unavailable_claims_do_not_invent_a_missing_claim():
    result = diagnose(pending(REAL_CLAIM, claims=["data"]), unavailable("pvc"))[0]

    assert "needs to be created" not in statements(result.possible_causes)
    assert any("Volume claims could not be collected" in item for item in result.missing_evidence)


def test_unavailable_storage_classes_do_not_invent_a_missing_class():
    result = diagnose(pending(REAL_CLAIM, claims=["data"]), claim(storage_class="fast"), unavailable("storageclass"))[0]

    assert "does not exist" not in statements(result.possible_causes)


# Conflicting or stale evidence.


def test_old_failed_scheduling_event_on_a_still_pending_pod_is_used():
    # Observed on a real cluster: the scheduler may emit this only once.
    result = diagnose(
        pending(node_selector={"disk": "ssd"}),
        node(),
        event("FailedScheduling", REAL_SELECTOR, age=OLD, container_name=None),
    )[0]

    assert result.status == "problem_detected"
    assert "FailedScheduling" in statements(result.confirmed_facts)


def test_assigned_node_with_unscheduled_condition_is_a_conflict():
    pod_record = pod(phase="Pending", node="node-1", conditions=unscheduled())

    result = diagnose(pod_record)[0]

    assert result.status == "insufficient_evidence"
    assert any("assigned node" in item for item in result.conflicting_evidence)


def test_selector_message_with_no_selector_on_the_pod_is_a_conflict():
    result = diagnose(pending(), node())[0]

    assert any("sets neither" in item for item in result.conflicting_evidence)
    assert result.evidence_strength == "medium"


def test_taint_message_with_no_taints_now_is_a_conflict():
    result = diagnose(pending(REAL_TAINT), node())[0]

    assert any("show none now" in item for item in result.conflicting_evidence)
    assert result.evidence_strength == "medium"


def test_tolerated_taint_is_not_counted():
    result = diagnose(
        pending(REAL_TAINT, tolerations=[{"key": "dedicated", "operator": "Exists", "effect": "NoSchedule"}]),
        node(taints=[{"key": "dedicated", "value": "db", "effect": "NoSchedule"}]),
    )[0]

    assert any("show none now" in item for item in result.conflicting_evidence)


def test_insufficient_message_with_no_request_is_a_conflict():
    result = diagnose(pending(REAL_CPU), container(reported=False), node())[0]

    assert any("no cpu request" in item for item in result.conflicting_evidence)


def test_claim_message_with_no_claims_is_a_conflict():
    result = diagnose(pending(REAL_CLAIM))[0]

    assert any("lists no claims" in item for item in result.conflicting_evidence)


# Multiple simultaneous issues.


def test_several_blockers_in_one_message_are_all_reported():
    message = "0/3 nodes are available: 1 Insufficient cpu, 1 node(s) didn't match Pod's node affinity/selector, 1 node(s) had untolerated taint(s)."

    result = diagnose(
        pending(message, node_selector={"disk": "ssd"}),
        container(requests={"cpu": "1"}, reported=False),
        node(taints=[{"key": "dedicated", "value": "db", "effect": "NoSchedule"}]),
    )[0]

    text = statements(result.possible_causes)
    assert "enough free cpu" in text
    assert "node selector" in text
    assert "taints" in text


def test_several_pending_pods_each_get_a_diagnosis():
    results = diagnose(pending(name="one"), pending(name="two"), node())

    assert [result.affected_resource.name for result in results] == ["one", "two"]
