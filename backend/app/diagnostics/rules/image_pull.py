"""Scenario 2: containers that cannot start because their image cannot be pulled."""

import re

from app.diagnostics.common import Rule, cause, count_of, describe_pod_step, fact, make_diagnosis, pod_resource
from app.diagnostics.index import EvidenceIndex
from app.models.diagnosis import Diagnosis, Recommendation
from app.models.evidence import EvidenceRecord

SCENARIO = "image_pull_backoff"
PULL_FAILURE_REASONS = {"ImagePullBackOff", "ErrImagePull", "InvalidImageName", "ErrImageNeverPull"}

# (pattern, statement, unambiguous). Registries often answer "denied" for an image that
# does not exist as well as for missing credentials, so that signal stays ambiguous.
_ERROR_CLASSES = [
    (
        re.compile(r"(?i)toomanyrequests|rate limit"),
        "The registry may be rate limiting image pulls.",
        True,
    ),
    (
        re.compile(r"(?i)pull access denied|repository does not exist|may require authorization"),
        "The repository may not exist, or pulling it may require credentials.",
        False,
    ),
    (
        re.compile(r"(?i)unauthorized|authentication required|insufficient_scope|forbidden"),
        "The registry may be rejecting the credentials, or none were provided.",
        True,
    ),
    (
        re.compile(r"(?i)manifest unknown|manifest for .* not found|: not found"),
        "The image name or tag may not exist in the registry.",
        True,
    ),
    (
        re.compile(r"(?i)no such host|i/o timeout|connection refused|network is unreachable|tls handshake"),
        "The node may be unable to reach the registry, or the registry name may be wrong.",
        True,
    ),
    (
        re.compile(r"(?i)invalid reference format|couldn't parse image"),
        "The image reference may be malformed.",
        True,
    ),
    (
        re.compile(r"(?i)imagepullpolicy is set to never|neverpull"),
        "The image pull policy forbids pulling and the image is not on the node.",
        True,
    ),
]


def _pull_events(index: EvidenceIndex, pod: str, container: str) -> list[EvidenceRecord]:
    return [
        event
        for event in index.events_for("Pod", pod, container)
        if event.data["reason"] == "Failed" and (event.data["message"] or "").startswith("Failed to pull")
    ]


def _classify_errors(texts: list[tuple[str, EvidenceRecord]]):
    causes, all_clear = [], True
    seen = set()
    for pattern, statement, unambiguous in _ERROR_CLASSES:
        for text, record in texts:
            if statement not in seen and pattern.search(text):
                seen.add(statement)
                causes.append(cause(statement, record))
                all_clear = all_clear and unambiguous
    return causes, all_clear


def _pull_secret_facts(index, pod, container):
    pod_secrets = pod.data["image_pull_secrets"]
    account_name = pod.data["service_account"] or "default"
    account = index.service_account(account_name)
    facts = [
        fact(
            f"The pod lists these image pull secrets: {', '.join(pod_secrets) or 'none'}.",
            pod,
        )
    ]
    missing = []
    if account:
        names = account.data["image_pull_secrets"]
        facts.append(
            fact(
                f"ServiceAccount {account_name} lists these image pull secrets: {', '.join(names) or 'none'}.",
                account,
            )
        )
    elif not index.has("serviceaccount"):
        missing.append("ServiceAccounts could not be collected, so inherited pull secrets are unknown.")
    return facts, missing


def _diagnose_container(index: EvidenceIndex, container: EvidenceRecord) -> Diagnosis | None:
    data = container.data
    state = data["state"] or {}
    if state.get("kind") != "waiting" or state.get("reason") not in PULL_FAILURE_REASONS:
        return None

    pod_name, name = data["pod"], data["container"]
    pod = index.pod(pod_name)
    image = data["image"]
    facts = [
        fact(
            f"Container {name} is currently waiting with reason {state['reason']}.",
            container,
            excerpt=state.get("message"),
        ),
        fact(
            f"It uses image {image['reference']} "
            f"(registry: {image['registry'] or 'not specified'}, tag: {image['tag'] or 'not specified'}, "
            f"digest: {image['digest'] or 'not specified'}, pull policy: {data['image_pull_policy']}).",
            container,
        ),
    ]
    secret_facts, missing = _pull_secret_facts(index, pod, container)
    facts.extend(secret_facts)

    events = _pull_events(index, pod_name, name)
    recent = [event for event in events if index.is_recent(event)]
    stale = [event for event in events if not index.is_recent(event)]
    for event in recent[:1]:
        facts.append(
            fact(
                f"The kubelet reported this pull failure {count_of(event.data['count'] or 1, 'time')}.",
                event,
                excerpt=event.data["message"],
                observed_at=event.observed_at,
            )
        )

    # The current state message and recent events describe the failure happening now.
    # Older events might describe an image reference that has since been changed.
    texts = [(state.get("message") or "", container)]
    texts.extend((event.data["message"] or "", event) for event in recent)
    causes, unambiguous = _classify_errors(texts)
    if state["reason"] == "InvalidImageName" and not causes:
        causes.append(cause("The image reference may be malformed.", container))

    conflicting = []
    if stale and not recent:
        conflicting.append(
            "The only detailed pull errors are from old events, which may describe an earlier image."
        )
    if not index.has("event"):
        missing.append("Events could not be collected, so the registry's error message may be unavailable.")
    elif not events and not causes:
        missing.append("No event or status message explains why the pull failed.")

    if causes and unambiguous:
        strength = "high"
    elif causes:
        strength = "medium"
    else:
        strength = "low"

    steps = [
        Recommendation(
            action=f"Confirm that image {image['reference']} exists and is reachable.",
            reason="The container cannot start until this exact image can be pulled.",
            risk="None for the check. Changing the image later changes what is deployed.",
            expected_impact="The container starts if the corrected image can be pulled.",
            how_to_apply=[
                f"docker manifest inspect {image['reference']}",
                "If the reference is wrong, correct it in the workload manifest and apply it yourself.",
            ],
            how_to_verify=[
                f"kubectl get pod {pod_name} -n {index.namespace} --watch",
                "Run this diagnosis again.",
            ],
            evidence_ids=[container.evidence_id],
        ),
        describe_pod_step(index, pod_name, pod),
    ]
    return make_diagnosis(
        index,
        diagnosis_id=f"{SCENARIO}:{container.namespace}/{pod_name}/{name}",
        scenario=SCENARIO,
        title=f"Container {name} cannot pull its image",
        status="problem_detected",
        strength=strength,
        resource=pod_resource(pod, name),
        explanation=(
            f"Container {name} in pod {pod_name} cannot start because Kubernetes cannot pull "
            f"image {image['reference']}. Possible causes below are inferences from the error text."
        ),
        facts=facts,
        causes=causes,
        missing=missing,
        conflicting=conflicting,
        recommendations=steps,
    )


def evaluate(index: EvidenceIndex) -> list[Diagnosis]:
    results = [_diagnose_container(index, container) for container in index.containers()]
    return [result for result in results if result]


RULE = Rule(name="image_pull", evaluate=evaluate, required=("pod",), supporting=("event", "serviceaccount"))
