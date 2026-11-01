"""Validates model output. Nothing from the model is trusted until it passes every check.

Pydantic checks the shape. The checks below compare the text with the exact input the model
was given. They catch invented values and forbidden claims, but they cannot prove every
sentence is true, so generated text stays labelled as generated.
"""

import json
import re

from pydantic import ValidationError

from app.agent.provider import ProviderError
from app.collectors.redaction import Redactor
from app.models.diagnosis import Diagnosis
from app.models.explanation import LlmExplanationContent

_EVIDENCE_ID = re.compile(
    r"\b(?:container-last-state|container|pod|logs|event|service|endpointslice|node|pvc|deployment"
    r"|replicaset|storageclass|serviceaccount|namespace):[A-Za-z0-9][\w./:-]*"
)
_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2})?")
_NUMBER = re.compile(r"(?<![\w.\-/:])\d+(?:\.\d+)?[A-Za-z]*(?![\w\-:/])")
_STEP_NUMBER = re.compile(r"^\s*\d+[.)]\s+")
_URL = re.compile(r"https?://[^\s\"'<>)]+", re.IGNORECASE)
_RESOURCE_NAME = re.compile(
    r"\b(?i:pods?|containers?|nodes?|services?|deployments?|replicasets?|namespaces?|secrets?|configmaps?"
    r"|persistentvolumeclaims?|pvcs?|statefulsets?|daemonsets?|jobs?)[\s/]+[`'\"]?([a-z0-9][a-z0-9.\-]*[a-z0-9])",
)
_QUOTED_NAME = re.compile(r"[`'\"]([a-z0-9]+(?:[.\-][a-z0-9]+)+)[`'\"]")
# One kubectl call: its arguments, up to the end of the line, a pipe, a backtick or another call.
_KUBECTL_CALL = re.compile(r"\b(?:kubectl(?:\.exe)?|oc|k)\s+((?:(?!\bkubectl\b)[^\n`|])*)", re.IGNORECASE)
_READ_ONLY_KUBECTL = {"get", "describe", "logs", "top", "explain", "events", "version", "api-resources"}
_KUBECTL_FLAGS_WITH_VALUES = {"-n", "--namespace", "--context"}
# Other identities and credentials, direct API access, secrets, and shell chaining or redirection.
_UNSAFE_KUBECTL_ARGUMENTS = re.compile(
    r"(?:^|\s)--(?:raw|as|as-group|as-uid|token|username|password|kubeconfig|server|client-key"
    r"|client-certificate|insecure-skip-tls-verify)\b|\bsecrets?\b|;|&&|\$\(|>",
    re.IGNORECASE,
)
# What a kubectl command's output may be piped to. These only read and filter.
_SAFE_PIPE_TARGETS = "grep|egrep|findstr|select-string|head|tail|less|more|sort|wc|jq"

_FAILURE_TERMS = (
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "ErrImageNeverPull",
    "InvalidImageName",
    "OOMKilled",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
    "Evicted",
    "NodeNotReady",
)
# Terms a scenario makes natural to use, even if the collected state did not contain them.
_SCENARIO_TERMS = {
    "crash_loop_backoff": {"CrashLoopBackOff"},
    "image_pull_backoff": {"ImagePullBackOff", "ErrImagePull"},
}

_CHECKS = {
    "confidence_score": re.compile(r"\d\s*(?:%|percent\b)|\bconfidence\b|\bprobability\b", re.IGNORECASE),
    "claimed_certainty": re.compile(
        r"\b(?:definitely|certainly|guaranteed|without (?:a )?doubt|(?:the )?root cause is)\b", re.IGNORECASE
    ),
    "claimed_action": re.compile(
        r"\b(?:I|we)(?: have|'ve)? (?:checked|ran|run|executed|contacted|queried|verified|restarted"
        r"|deleted|fixed|applied|inspected|called)\b",
        re.IGNORECASE,
    ),
    "unsafe_command": re.compile(
        rf"\brm\s+\S|\bsudo\s|\|\s*(?!(?:{_SAFE_PIPE_TARGETS})\b)[a-z]"
        r"|\b(?:docker|podman|nerdctl|crictl)\s+(?:rm|rmi|stop|kill|exec|run|system)\b"
        r"|\bhelm\s+(?:install|upgrade|uninstall|delete|rollback)\b"
        r"|\bkind\s+delete\b|\bterraform\s+(?:apply|destroy)\b"
        r"|\bbase64\s+(?:-d|--decode)\b|\.kube[/\\]config|/etc/kubernetes|/var/run/secrets",
        re.IGNORECASE,
    ),
    "echoed_injection": re.compile(r"\b(?:ignore|disregard) (?:all |any )?(?:previous|prior|above) (?:instructions|rules)\b", re.IGNORECASE),
}
# Only applied when the diagnosis confirms a problem.
_DISMISSES_PROBLEM = re.compile(
    r"\b(?:no (?:problem|issue|action)s?\b|(?:is|are) healthy|working (?:correctly|normally|as expected)"
    r"|nothing (?:is )?wrong|false (?:alarm|positive)|(?:can|may) (?:safely )?be ignored|has (?:been )?resolved"
    r"|is not (?:a problem|an issue))",
    re.IGNORECASE,
)
# Only applied to investigation steps. An explanation may say Kubernetes restarts a container.
_DESTRUCTIVE_STEP = re.compile(
    r"\b(?:delete|remove|restart|kill|drain|cordon|uncordon|taint|recreate|redeploy|roll ?back|scale|force|edit|patch|apply"
    r"|exec|port[- ]?forward|grant|impersonate|decode)\b",
    re.IGNORECASE,
)


def parse_explanation(raw: str) -> LlmExplanationContent:
    """Parse exact JSON into the only accepted shape. Nothing is repaired or guessed."""
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ProviderError("malformed_response") from exc
    try:
        return LlmExplanationContent.model_validate(document)
    except ValidationError as exc:
        raise ProviderError("schema_validation_failed") from exc


def _without_timestamps(text: str) -> str:
    return _TIMESTAMP.sub(" ", re.sub(r"\b\d{4}-\d{2}-\d{2}[T ][\d:.]+(?:Z|[+-]\d{2}:\d{2})?", " ", text))


def _numbers(text: str) -> set[str]:
    return {match.lower() for match in _NUMBER.findall(_without_timestamps(text))}


def _timestamps(text: str) -> set[str]:
    return {match.replace(" ", "T") for match in _TIMESTAMP.findall(text)}


def _kubectl_is_read_only(arguments: str) -> bool:
    if _UNSAFE_KUBECTL_ARGUMENTS.search(arguments):
        return False
    words = arguments.lower().split()
    while words and words[0].startswith("-"):
        flag = words.pop(0)
        if flag in _KUBECTL_FLAGS_WITH_VALUES and words:
            words.pop(0)
    # Anything not recognised as a safe verb is refused, including a flag whose value was not skipped.
    return bool(words) and words[0].rstrip(".,:)\"'") in _READ_ONLY_KUBECTL


def _all_text(content: LlmExplanationContent) -> list[str]:
    return [
        content.explanation,
        content.impact,
        *content.evidence_summary,
        *content.investigation_steps,
        *content.limitations,
    ]


def find_unsupported_claims(content: LlmExplanationContent, user_message: str, diagnosis: Diagnosis) -> list[str]:
    """Return the names of the checks this output fails. An empty list means it passed."""
    texts = [_STEP_NUMBER.sub("", text) for text in _all_text(content)]
    combined = "\n".join(texts)
    known = user_message.lower()
    failed = [name for name, pattern in _CHECKS.items() if pattern.search(combined)]

    if diagnosis.status == "problem_detected" and _DISMISSES_PROBLEM.search(combined):
        failed.append("contradicts_diagnosis")
    if any(token.rstrip(".,;:") not in user_message for token in _EVIDENCE_ID.findall(combined)):
        failed.append("evidence_identifier")
    if _timestamps(combined) - _timestamps(user_message):
        failed.append("invented_timestamp")
    if _numbers(combined) - _numbers(user_message):
        failed.append("invented_value")
    if any(url.rstrip(".,").lower() not in known for url in _URL.findall(combined)):
        failed.append("invented_url")
    names = {name for name in [*_RESOURCE_NAME.findall(combined), *_QUOTED_NAME.findall(combined)] if "-" in name or any(c.isdigit() for c in name)}
    if any(name not in known for name in names):
        failed.append("invented_resource")

    allowed_terms = _SCENARIO_TERMS.get(diagnosis.scenario, set())
    if any(term in combined and term not in user_message and term not in allowed_terms for term in _FAILURE_TERMS):
        failed.append("unsupported_failure_type")
    if not all(_kubectl_is_read_only(arguments) for arguments in _KUBECTL_CALL.findall(combined)):
        failed.append("unsafe_command")
    if any(_DESTRUCTIVE_STEP.search(step) for step in content.investigation_steps):
        failed.append("destructive_step")

    redactor = Redactor()
    redactor.text(combined)
    if redactor.count:
        failed.append("sensitive_content")
    return sorted(set(failed))
