"""Scenario 3: pods the scheduler cannot place.

The PodScheduled condition is current state and is what decides this rule. The scheduler
may emit FailedScheduling only once, so an old event for a pod that is still Pending is
normal. Each blocker named by the scheduler is then checked against collected
configuration where possible, which is what separates high from medium strength.
"""

import re
from dataclasses import dataclass, field

from kubernetes.utils.quantity import parse_quantity

from app.diagnostics.common import Rule, cause, count_of, describe_pod_step, fact, make_diagnosis, pod_resource
from app.diagnostics.index import EvidenceIndex
from app.models.diagnosis import Diagnosis, Fact, PossibleCause, Recommendation
from app.models.evidence import EvidenceRecord

SCENARIO = "pending_pod"
BLOCKING_EFFECTS = {"NoSchedule", "NoExecute"}


@dataclass
class Finding:
    causes: list[PossibleCause] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)
    # True when collected configuration independently agrees with the scheduler.
    corroborated: bool = False


def _node_selector(index, pod) -> Finding:
    selector = pod.data["node_selector"]
    nodes = index.nodes()
    statement = "The pod's node selector or affinity may not match any node."
    if not selector and not pod.data["affinity"]:
        return Finding(
            causes=[cause(statement, pod)],
            conflicting=["The scheduler reports an affinity or selector mismatch, but the pod sets neither."],
        )
    finding = Finding(causes=[cause(statement, pod)])
    if selector and nodes:
        unmatched = {
            key: value
            for key, value in selector.items()
            if not any(node.data["labels"].get(key) == value for node in nodes)
        }
        if unmatched:
            finding.corroborated = True
            finding.facts.append(
                fact(
                    f"No collected node has these labels required by the pod: {unmatched}.",
                    pod,
                    *nodes,
                )
            )
    return finding


def _requested(index, pod_name: str, resource: str):
    total = 0
    found = False
    for container in index.containers_of(pod_name):
        if container.data["container_type"] != "app":
            continue
        value = container.data["resources"]["requests"].get(resource)
        if value is not None:
            total += parse_quantity(value)
            found = True
    return total if found else None


def _insufficient(index, pod, resource: str) -> Finding:
    requested = _requested(index, pod.resource_name, resource)
    nodes = index.nodes()
    finding = Finding(causes=[cause(f"Nodes may not have enough free {resource} for this pod.", pod)])
    if requested is None:
        finding.conflicting.append(
            f"The scheduler reports insufficient {resource}, but no {resource} request was found on the pod."
        )
        return finding
    allocatable = [
        parse_quantity(node.data["allocatable"][resource])
        for node in nodes
        if resource in node.data["allocatable"]
    ]
    if allocatable:
        finding.facts.append(
            fact(
                f"The pod requests {requested} {resource}. The largest node can allocate {max(allocatable)}.",
                pod,
                *nodes,
            )
        )
        if requested > max(allocatable):
            finding.corroborated = True
            finding.causes = [
                cause(f"The pod requests more {resource} than any collected node can provide.", pod, *nodes)
            ]
    return finding


def _tolerates(taint: dict, toleration: dict) -> bool:
    if toleration.get("effect") and toleration["effect"] != taint["effect"]:
        return False
    if toleration.get("operator") == "Exists":
        return not toleration.get("key") or toleration["key"] == taint["key"]
    return toleration.get("key") == taint["key"] and toleration.get("value") == taint["value"]


def _taints(index, pod) -> Finding:
    finding = Finding(causes=[cause("Node taints may be repelling this pod.", pod)])
    nodes = index.nodes()
    if not nodes:
        return finding
    tolerations = pod.data["tolerations"]
    blocking = {}
    for node in nodes:
        for taint in node.data["taints"]:
            if taint["effect"] in BLOCKING_EFFECTS and not any(_tolerates(taint, t) for t in tolerations):
                blocking.setdefault(node.resource_name, []).append(taint["key"])
    if blocking:
        finding.corroborated = len(blocking) == len(nodes)
        finding.facts.append(fact(f"Taints the pod does not tolerate, by node: {blocking}.", pod, *nodes))
    else:
        finding.conflicting.append(
            "The scheduler reports untolerated taints, but the collected nodes show none now."
        )
    return finding


def _claims(index, pod) -> Finding:
    finding = Finding()
    names = pod.data["persistent_volume_claims"]
    if not names:
        finding.causes.append(cause("A volume claim needed by this pod may not be bound.", pod))
        finding.conflicting.append("The scheduler reports a volume problem, but the pod lists no claims.")
        return finding
    for name in names:
        claim = index.claim(name)
        if claim is None:
            if index.has("pvc"):
                finding.corroborated = True
                finding.facts.append(fact(f"Volume claim {name} used by the pod does not exist.", pod))
                finding.causes.append(cause(f"Volume claim {name} needs to be created.", pod))
            continue
        finding.facts.append(fact(f"Volume claim {name} has phase {claim.data['phase']}.", claim))
        if claim.data["phase"] == "Bound":
            continue
        class_name = claim.data["storage_class"]
        if class_name and index.has("storageclass") and index.storage_class(class_name) is None:
            finding.corroborated = True
            finding.facts.append(fact(f"StorageClass {class_name} requested by claim {name} does not exist.", claim))
            finding.causes.append(cause(f"Claim {name} requests a StorageClass that does not exist.", claim))
        else:
            finding.causes.append(cause(f"Volume claim {name} is not bound.", claim))
        for event in index.events_for("PersistentVolumeClaim", name)[:1]:
            finding.facts.append(
                fact(
                    f"An event for claim {name} reports {event.data['reason']}.",
                    event,
                    excerpt=event.data["message"],
                    observed_at=event.observed_at,
                )
            )
    return finding


def _anti_affinity(index, pod) -> Finding:
    finding = Finding(causes=[cause("Pod anti-affinity rules may prevent placement on the available nodes.", pod)])
    if (pod.data["affinity"] or {}).get("podAntiAffinity"):
        finding.corroborated = True
        finding.facts.append(fact("The pod defines pod anti-affinity rules.", pod))
    return finding


def _pod_affinity(index, pod) -> Finding:
    finding = Finding(causes=[cause("Pod affinity rules may not be satisfied by any node.", pod)])
    if (pod.data["affinity"] or {}).get("podAffinity"):
        finding.corroborated = True
        finding.facts.append(fact("The pod defines pod affinity rules.", pod))
    return finding


def _unschedulable_nodes(index, pod) -> Finding:
    finding = Finding(causes=[cause("Available nodes may be marked unschedulable.", pod)])
    cordoned = [node for node in index.nodes() if node.data["unschedulable"]]
    if cordoned:
        finding.corroborated = True
        names = ", ".join(node.resource_name for node in cordoned)
        finding.facts.append(fact(f"These nodes are marked unschedulable: {names}.", *cordoned))
    return finding


def _no_nodes(index, pod) -> Finding:
    finding = Finding(causes=[cause("The cluster may have no nodes available to this pod.", pod)])
    if index.has("node") and not index.nodes():
        finding.corroborated = True
        finding.facts.append(fact("No nodes were found in the cluster.", pod))
    return finding


def _uncorroborated(statement: str):
    def check(index, pod) -> Finding:
        return Finding(causes=[cause(statement, pod)])

    return check


_BLOCKERS = [
    (re.compile(r"didn't match pod anti-affinity", re.I), _anti_affinity),
    (re.compile(r"didn't match pod affinity", re.I), _pod_affinity),
    (re.compile(r"didn't match Pod's node affinity/selector|node affinity", re.I), _node_selector),
    (re.compile(r"Insufficient cpu", re.I), lambda index, pod: _insufficient(index, pod, "cpu")),
    (re.compile(r"Insufficient memory", re.I), lambda index, pod: _insufficient(index, pod, "memory")),
    (re.compile(r"Insufficient ephemeral-storage", re.I), lambda index, pod: _insufficient(index, pod, "ephemeral-storage")),
    (re.compile(r"untolerated taint", re.I), _taints),
    (re.compile(r"unbound immediate PersistentVolumeClaims|persistentvolumeclaim .* not found", re.I), _claims),
    (re.compile(r"were unschedulable", re.I), _unschedulable_nodes),
    (re.compile(r"no nodes available|0/0 nodes", re.I), _no_nodes),
    (
        re.compile(r"volume node affinity conflict|didn't find available persistent volumes", re.I),
        _uncorroborated("Available persistent volumes may not fit this pod's node or request."),
    ),
    (
        re.compile(r"too many pods", re.I),
        _uncorroborated("Nodes may have reached their limit on the number of pods."),
    ),
    (
        re.compile(r"didn't have free ports", re.I),
        _uncorroborated("A host port needed by this pod may already be in use on the nodes."),
    ),
]


def _condition(pod) -> dict | None:
    return next((c for c in pod.data["conditions"] if c["type"] == "PodScheduled"), None)


def _steps(index, pod) -> list[Recommendation]:
    name = pod.resource_name
    return [
        Recommendation(
            action=f"Compare what pod {name} requires with what the nodes offer.",
            reason="The scheduler can only place a pod on a node meeting all its requirements.",
            risk="None for the check. Changing requirements or nodes affects where workloads run.",
            expected_impact="The pod is scheduled once one node meets every requirement.",
            how_to_apply=[
                f"kubectl describe pod {name} -n {index.namespace}",
                "kubectl describe nodes",
                "Change the workload's requirements or the nodes yourself if needed.",
            ],
            how_to_verify=[
                f"kubectl get pod {name} -n {index.namespace} -o wide --watch",
                "Run this diagnosis again.",
            ],
            evidence_ids=[pod.evidence_id],
        )
    ]


def _diagnose_pod(index: EvidenceIndex, pod: EvidenceRecord) -> Diagnosis | None:
    if pod.data["phase"] != "Pending":
        return None
    condition = _condition(pod)
    node = pod.data["node_name"]
    name = pod.resource_name
    identifier = f"{SCENARIO}:{pod.namespace}/{name}"

    if condition is None and not node:
        return make_diagnosis(
            index,
            diagnosis_id=identifier,
            scenario=SCENARIO,
            title=f"Pod {name} is Pending with no scheduling status",
            status="insufficient_evidence",
            strength="insufficient",
            resource=pod_resource(pod),
            explanation=(
                f"Pod {name} is Pending and has no node, but the scheduler has reported nothing. "
                "It may simply be very new. Collect evidence again shortly."
            ),
            facts=[fact(f"Pod {name} has phase Pending and no assigned node.", pod)],
            missing=["The pod has no PodScheduled condition."],
            recommendations=[describe_pod_step(index, name, pod)],
        )
    if condition is None or condition["status"] != "False":
        return None
    if node:
        return make_diagnosis(
            index,
            diagnosis_id=identifier,
            scenario=SCENARIO,
            title=f"Pod {name} has conflicting scheduling status",
            status="insufficient_evidence",
            strength="insufficient",
            resource=pod_resource(pod),
            explanation="The pod is assigned to a node, but its condition says it is not scheduled.",
            facts=[fact(f"Pod {name} is assigned to node {node} with PodScheduled=False.", pod)],
            conflicting=["PodScheduled is False, but the pod has an assigned node."],
            recommendations=[describe_pod_step(index, name, pod)],
        )

    message = condition["message"] or ""
    facts = [
        fact(
            f"Pod {name} has phase Pending with PodScheduled=False, reason {condition['reason']}.",
            pod,
            excerpt=message,
            observed_at=condition["last_transition_time"],
        )
    ]
    events = [e for e in index.events_for("Pod", name) if e.data["reason"] == "FailedScheduling"]
    for event in events[:1]:
        facts.append(
            fact(
                f"The scheduler reported FailedScheduling {count_of(event.data['count'] or 1, 'time')}.",
                event,
                excerpt=event.data["message"],
                observed_at=event.observed_at,
            )
        )

    causes, conflicting = [], []
    corroborated = False
    text = " ".join([message, *[e.data["message"] or "" for e in events[:1]]])
    for pattern, check in _BLOCKERS:
        if pattern.search(text):
            finding = check(index, pod)
            causes.extend(finding.causes)
            facts.extend(finding.facts)
            conflicting.extend(finding.conflicting)
            corroborated = corroborated or finding.corroborated
    if condition["reason"] == "SchedulingGated":
        causes.append(cause("The pod has scheduling gates that must be removed first.", pod))
        corroborated = True

    missing = [
        description
        for category, description in (
            ("node", "Nodes could not be collected, so node labels, taints and capacity were not checked."),
            ("pvc", "Volume claims could not be collected."),
            ("storageclass", "StorageClasses could not be collected."),
            ("event", "Events could not be collected."),
        )
        if not index.has(category)
    ]
    if corroborated and causes:
        strength = "high"
    elif causes:
        strength = "medium"
    else:
        strength = "low"
        missing.append("The scheduler's message did not match a known blocker.")

    return make_diagnosis(
        index,
        diagnosis_id=identifier,
        scenario=SCENARIO,
        title=f"Pod {name} cannot be scheduled",
        status="problem_detected",
        strength=strength,
        resource=pod_resource(pod),
        explanation=(
            f"The scheduler cannot place pod {name} on any node. The scheduler's message is "
            "confirmed. Possible causes below are inferred from that message and collected configuration."
        ),
        facts=facts,
        causes=causes,
        missing=missing,
        conflicting=conflicting,
        recommendations=_steps(index, pod),
    )


def evaluate(index: EvidenceIndex) -> list[Diagnosis]:
    results = [_diagnose_pod(index, pod) for pod in index.pods()]
    return [result for result in results if result]


RULE = Rule(
    name="pending_pod",
    evaluate=evaluate,
    required=("pod",),
    supporting=("event", "node", "pvc", "storageclass"),
)
