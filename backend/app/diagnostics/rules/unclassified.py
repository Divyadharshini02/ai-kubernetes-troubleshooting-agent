"""Catch unhealthy pods that none of the five supported rules explained.

Without this, a pod failing in an unsupported way could produce "no problems found". It
is reported honestly as insufficient evidence, never forced into a supported scenario.
"""

from app.diagnostics.common import describe_pod_step, fact, make_diagnosis, pod_resource
from app.diagnostics.index import EvidenceIndex
from app.models.diagnosis import Diagnosis
from app.models.evidence import EvidenceRecord

SCENARIO = "unclassified"
RULE_NAME = "unclassified"


def _unhealthy_containers(index, pod_name: str) -> list[EvidenceRecord]:
    unhealthy = []
    for container in index.containers_of(pod_name):
        data = container.data
        state = data["state"] or {}
        finished = state.get("kind") == "terminated" and state.get("exit_code") == 0
        if data["container_type"] == "init":
            if state.get("kind") in ("waiting", "terminated") and not finished:
                unhealthy.append(container)
        elif data["ready"] is not True and not finished:
            unhealthy.append(container)
    return unhealthy


def evaluate(index: EvidenceIndex, diagnoses: list[Diagnosis]) -> list[Diagnosis]:
    explained = {d.affected_resource.name for d in diagnoses if d.affected_resource.kind == "Pod"}
    results = []
    for pod in index.pods():
        name = pod.resource_name
        phase = pod.data["phase"]
        if name in explained or phase == "Succeeded":
            continue
        unhealthy = _unhealthy_containers(index, name)
        if phase == "Running" and not unhealthy:
            continue

        facts = [fact(f"Pod {name} has phase {phase}.", pod, excerpt=pod.data["message"])]
        for container in unhealthy:
            state = container.data["state"] or {}
            facts.append(
                fact(
                    f"Container {container.data['container']} is not healthy: "
                    f"state {state.get('kind', 'not reported')}, reason {state.get('reason', 'not reported')}.",
                    container,
                    excerpt=state.get("message"),
                )
            )
        for event in [e for e in index.events_for("Pod", name) if e.data["type"] == "Warning"][:1]:
            facts.append(
                fact(
                    f"The latest warning event has reason {event.data['reason']}.",
                    event,
                    excerpt=event.data["message"],
                    observed_at=event.observed_at,
                )
            )
        results.append(
            make_diagnosis(
                index,
                diagnosis_id=f"{SCENARIO}:{pod.namespace}/{name}",
                scenario=SCENARIO,
                title=f"Pod {name} is unhealthy in a way no supported rule explains",
                status="insufficient_evidence",
                strength="insufficient",
                resource=pod_resource(pod),
                explanation=(
                    f"Pod {name} is not healthy, but its evidence does not match any of the five "
                    "supported scenarios, so no cause is claimed. It may also still be starting."
                ),
                facts=facts,
                missing=["This condition is outside the rules implemented in this release."],
                recommendations=[describe_pod_step(index, name, pod)],
            )
        )
    return results
