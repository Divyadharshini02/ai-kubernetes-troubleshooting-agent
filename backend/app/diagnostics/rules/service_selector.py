"""Scenario 4: Services whose selector does not lead to usable endpoints.

Not every Service without endpoints has a selector mismatch, so this rule first compares
the real selector with real pod labels. A mismatch is only reported when no eligible pod
matches. Matching pods that are not ready is reported as a different problem.
"""

from app.diagnostics.common import Rule, cause, count_of, fact, make_diagnosis
from app.diagnostics.index import EvidenceIndex
from app.models.diagnosis import AffectedResource, Diagnosis, Recommendation
from app.models.evidence import EvidenceRecord

SCENARIO = "service_selector"
TERMINAL_PHASES = {"Succeeded", "Failed"}
NEAR_MATCH_LIMIT = 3


def _matches(selector: dict, labels: dict) -> bool:
    return all(labels.get(key) == value for key, value in selector.items())


def _eligible_pods(index: EvidenceIndex) -> list[EvidenceRecord]:
    return [pod for pod in index.pods() if pod.data["phase"] not in TERMINAL_PHASES]


def _pod_is_ready(pod: EvidenceRecord) -> bool:
    return any(c["type"] == "Ready" and c["status"] == "True" for c in pod.data["conditions"])


def _near_matches(selector: dict, pods: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Pods sharing a label key or value with the selector, the likely intended targets."""
    def score(pod):
        labels = pod.data["labels"]
        return sum(key in labels for key in selector) + sum(v in labels.values() for v in selector.values())

    scored = [(score(pod), pod.resource_name, pod) for pod in pods]
    ranked = sorted((item for item in scored if item[0] > 0), key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[:NEAR_MATCH_LIMIT]]


def _endpoint_counts(slices: list[EvidenceRecord]) -> tuple[int, int]:
    endpoints = [endpoint for item in slices for endpoint in item.data["endpoints"]]
    return len(endpoints), sum(1 for endpoint in endpoints if endpoint["ready"] is True)


def _service_resource(service: EvidenceRecord) -> AffectedResource:
    return AffectedResource(kind="Service", name=service.resource_name, namespace=service.namespace)


def _steps(index: EvidenceIndex, service: EvidenceRecord) -> list[Recommendation]:
    name, namespace = service.resource_name, index.namespace
    return [
        Recommendation(
            action=f"Compare the selector of Service {name} with the labels of the intended pods.",
            reason="A Service only sends traffic to ready pods carrying all of its selector labels.",
            risk="None for the check. Changing a selector or labels redirects live traffic.",
            expected_impact="The Service gains endpoints once selector and labels agree and pods are ready.",
            how_to_apply=[
                f"kubectl get service {name} -n {namespace} -o yaml",
                f"kubectl get pods -n {namespace} --show-labels",
                "Correct the selector or the pod template labels in your manifests and apply them yourself.",
            ],
            how_to_verify=[
                f"kubectl get endpointslices -n {namespace} -l kubernetes.io/service-name={name}",
                "Run this diagnosis again.",
            ],
            evidence_ids=[service.evidence_id],
        )
    ]


def _no_matching_pods(index, service, selector, pods, slices, total, ready) -> Diagnosis:
    name = service.resource_name
    identifier = f"{SCENARIO}:{service.namespace}/{name}"
    facts = [fact(f"Service {name} selects pods with labels {selector}.", service)]
    missing = []

    if index.availability("pod") == "partial":
        return make_diagnosis(
            index,
            diagnosis_id=identifier,
            scenario=SCENARIO,
            title=f"Cannot confirm whether Service {name} matches any pod",
            status="insufficient_evidence",
            strength="insufficient",
            resource=_service_resource(service),
            explanation="No collected pod matches, but the pod list was truncated, so a match may exist.",
            facts=facts,
            missing=["The pod list was truncated by the collection limit."],
            recommendations=_steps(index, service),
        )
    facts.append(fact(f"None of the eligible pods in the namespace ({len(pods)}) match that selector.", service))

    if ready:
        return make_diagnosis(
            index,
            diagnosis_id=identifier,
            scenario=SCENARIO,
            title=f"Service {name} has endpoints but no matching pods",
            status="insufficient_evidence",
            strength="insufficient",
            resource=_service_resource(service),
            explanation="The evidence conflicts, so no conclusion is drawn. Collect evidence again.",
            facts=[*facts, fact(f"Its EndpointSlices list {count_of(ready, 'ready endpoint')}.", *slices)],
            conflicting=["EndpointSlices list ready endpoints, but no collected pod matches the selector."],
            recommendations=_steps(index, service),
        )

    if slices:
        facts.append(fact(f"Its EndpointSlices list {count_of(total, 'endpoint')}, {ready} ready.", *slices))
    elif not index.has("endpointslice"):
        missing.append("EndpointSlices could not be collected, so endpoints were not confirmed.")
    else:
        facts.append(fact(f"No EndpointSlice exists for Service {name}.", service))

    scaled_down = [
        deployment
        for deployment in index.deployments()
        if deployment.data["desired_replicas"] == 0
        and _matches(selector, deployment.data["selector"]["match_labels"])
    ]
    near = _near_matches(selector, pods)
    causes = []
    if scaled_down:
        names = ", ".join(d.resource_name for d in scaled_down)
        facts.append(fact(f"Deployments scaled to zero with matching labels: {names}.", *scaled_down))
        causes.append(cause("The intended workload may be scaled to zero, not mismatched.", *scaled_down))
        return make_diagnosis(
            index,
            diagnosis_id=identifier,
            scenario=SCENARIO,
            title=f"Service {name} selects a workload with no pods",
            status="problem_detected",
            strength="medium",
            resource=_service_resource(service),
            explanation=f"Service {name} has no endpoints because the workload it selects has no replicas.",
            facts=facts,
            causes=causes,
            missing=missing,
            recommendations=_steps(index, service),
        )
    for pod in near:
        facts.append(fact(f"Pod {pod.resource_name} has labels {pod.data['labels']}.", pod))
    if near:
        causes.append(cause("The selector may contain a typo, or the pod labels may have changed.", service, *near))
    causes.append(cause("The intended workload may not be deployed in this namespace.", service))

    if not index.has("endpointslice"):
        strength = "low"
    elif near:
        strength = "high"
    else:
        strength = "medium"
    return make_diagnosis(
        index,
        diagnosis_id=identifier,
        scenario=SCENARIO,
        title=f"Service {name} selector matches no pods",
        status="problem_detected",
        strength=strength,
        resource=_service_resource(service),
        explanation=f"Service {name} cannot route traffic because no eligible pod carries all of its selector labels.",
        facts=facts,
        causes=causes,
        missing=missing,
        recommendations=_steps(index, service),
    )


def _missing_named_ports(index, service, matching) -> list[str]:
    """Named target ports no matching pod declares. Numeric ports need no declaration."""
    missing = []
    for port in service.data["ports"]:
        target = port["target_port"]
        if not isinstance(target, str):
            continue
        declared = {
            declared_port["name"]
            for pod in matching
            for container in index.containers_of(pod.resource_name)
            for declared_port in container.data["ports"]
        }
        if target not in declared:
            missing.append(target)
    return missing


def _diagnose_service(index: EvidenceIndex, service: EvidenceRecord) -> Diagnosis | None:
    selector = service.data["selector"]
    # With no selector, endpoints are managed elsewhere. ExternalName has no endpoints.
    if not selector or service.data["type"] == "ExternalName":
        return None

    name = service.resource_name
    identifier = f"{SCENARIO}:{service.namespace}/{name}"
    pods = _eligible_pods(index)
    matching = [pod for pod in pods if _matches(selector, pod.data["labels"])]
    slices = index.endpoint_slices_for(name)
    total, ready = _endpoint_counts(slices)

    if not matching:
        return _no_matching_pods(index, service, selector, pods, slices, total, ready)

    base_facts = [
        fact(f"Service {name} selects pods with labels {selector}.", service),
        fact(f"Pods matching that selector: {len(matching)}.", service, *matching),
    ]
    missing_ports = _missing_named_ports(index, service, matching)
    if missing_ports:
        return make_diagnosis(
            index,
            diagnosis_id=identifier,
            scenario=SCENARIO,
            title=f"Service {name} targets a port name its pods do not declare",
            status="problem_detected",
            strength="high",
            resource=_service_resource(service),
            explanation="The selector matches, but traffic cannot be routed to a port name the pods do not declare.",
            facts=[*base_facts, fact(f"No matching pod declares these target ports: {missing_ports}.", service, *matching)],
            causes=[cause("The Service targetPort name may not match the container's port name.", service, *matching)],
            recommendations=_steps(index, service),
        )

    if not index.has("endpointslice") or ready:
        return None

    ready_pods = [pod for pod in matching if _pod_is_ready(pod)]
    facts = [*base_facts]
    if slices:
        facts.append(fact(f"Its EndpointSlices list {count_of(total, 'endpoint')}, 0 ready.", *slices))
    else:
        facts.append(fact(f"No EndpointSlice exists for Service {name}.", service))

    if ready_pods:
        return make_diagnosis(
            index,
            diagnosis_id=identifier,
            scenario=SCENARIO,
            title=f"Service {name} has ready pods but no ready endpoints",
            status="insufficient_evidence",
            strength="insufficient",
            resource=_service_resource(service),
            explanation="The evidence conflicts. Endpoints may simply be updating, so collect evidence again.",
            facts=[*facts, fact(f"Matching pods reporting Ready: {len(ready_pods)}.", *ready_pods)],
            conflicting=["Matching pods report Ready, but no endpoint is ready."],
            recommendations=_steps(index, service),
        )

    return make_diagnosis(
        index,
        diagnosis_id=identifier,
        scenario=SCENARIO,
        title=f"Service {name} matches pods, but none are ready",
        status="problem_detected",
        strength="high",
        resource=_service_resource(service),
        explanation=(
            f"The selector of Service {name} is working: it matches pods. "
            "It has no usable endpoints because those pods are not ready."
        ),
        facts=[*facts, fact("None of the matching pods report Ready.", *matching)],
        causes=[cause("The matching pods are not ready, so this is not a selector mismatch.", *matching)],
        recommendations=_steps(index, service),
    )


def evaluate(index: EvidenceIndex) -> list[Diagnosis]:
    results = [_diagnose_service(index, service) for service in index.services()]
    return [result for result in results if result]


RULE = Rule(
    name="service_selector",
    evaluate=evaluate,
    required=("service", "pod"),
    supporting=("endpointslice", "deployment"),
)
