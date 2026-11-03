"""Validates model output. Nothing from the model is trusted until it passes every check.

Pydantic checks the shape. The checks below compare the text with the exact input the model
was given. They catch invented values and forbidden claims, but they cannot prove every
sentence is true, so generated text stays labelled as generated.
"""

import json
import logging
import re

from pydantic import ValidationError

from app.agent.provider import ProviderError
from app.collectors.redaction import Redactor
from app.models.analysis import RcaContent
from app.models.chat import ChatAnswer
from app.models.diagnosis import Diagnosis
from app.models.explanation import LlmExplanationContent

logger = logging.getLogger(__name__)

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


# Root cause analysis. Its fields name a confidence level and a root cause, so the explanation
# checks for those words do not apply. Percentages and claims of certainty still do.
_PERCENTAGE = re.compile(r"\d\s*(?:%|percent\b)|\bprobability\b", re.IGNORECASE)
_CERTAINTY = re.compile(r"\b(?:definitely|certainly|guaranteed|without (?:a )?doubt)\b", re.IGNORECASE)
# Changes a person may choose to make by hand, on top of reading. Nothing here is ever run.
_REMEDIATION_KUBECTL = _READ_ONLY_KUBECTL | {"set", "patch", "apply", "rollout", "scale", "edit", "label", "annotate"}
# Dangerous wherever they appear: forced or cluster-wide changes, kube-system, and cluster-scoped or
# sensitive resources. Dangerous verbs such as delete, drain, exec or replace are refused by position
# instead, because they are not in _REMEDIATION_KUBECTL. Matching them anywhere wrongly refused a real
# JSON patch, whose body says "op": "replace".
_FORBIDDEN_REMEDIATION = re.compile(
    r"--force\b|--grace-period|(?:^|\s)(?:-A|--all-namespaces)\b|\bkube-system\b"
    r"|\b(?:nodes?|namespaces?|clusterroles?|clusterrolebindings?|roles?|rolebindings?|serviceaccounts?"
    r"|certificatesigningrequests?|persistentvolumes?|pv)\b(?![\w-])",
    re.IGNORECASE,
)
_NAMESPACE_FLAG = re.compile(r"(?:^|\s)(?:-n|--namespace)(?:\s+|=)(\S+)")
# A value the person must fill in, such as <image-tag>. Its > is not shell redirection.
_PLACEHOLDER = re.compile(r"<[A-Za-z0-9_.:/-]{1,60}>")


_KUBECTL_VERBS = (_REMEDIATION_KUBECTL | {"create", "delete", "drain", "cordon", "uncordon", "taint", "exec", "attach", "cp", "port-forward",
                                          "proxy", "replace", "run", "expose", "autoscale", "debug", "auth", "certificate", "config"})


def rejected_command_verbs(commands: list[str], namespace: str) -> list[str]:
    """Each refused command's kubectl verb and rule, for the log, such as patch:unsafe_argument.

    Only known verbs and fixed rule names are logged, never command text, which is untrusted.
    """
    refused = set()
    for command in commands:
        rule = _remediation_refusal(command, namespace)
        if rule is None:
            continue
        words = [word for word in "".join(_KUBECTL_CALL.findall(command)[:1]).lower().split() if not word.startswith("-")]
        refused.add(f"{words[0] if words and words[0] in _KUBECTL_VERBS else 'other'}:{rule}")
    return sorted(refused)


def _log_schema_errors(what: str, exc: ValidationError) -> None:
    # Field paths and error types only, such as reasoning:too_long. Values are untrusted and never logged.
    problems = sorted({".".join(str(part) for part in error["loc"] if not isinstance(part, int)) + ":" + error["type"] for error in exc.errors(include_input=False)})
    logger.warning("%s did not match the schema: %s", what, problems[:10])


def parse_rca(raw: str) -> RcaContent:
    """Parse exact JSON into the only accepted shape. Nothing is repaired or guessed."""
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ProviderError("malformed_response") from exc
    try:
        return RcaContent.model_validate(document)
    except ValidationError as exc:
        _log_schema_errors("AI root cause analysis", exc)
        raise ProviderError("schema_validation_failed") from exc


def _cited_ids(content: RcaContent) -> list[str]:
    return [
        *content.root_cause.evidence_ids,
        *(item.evidence_id for item in content.supporting_evidence),
        *(evidence_id for item in content.alternative_causes for evidence_id in item.evidence_ids),
        *(evidence_id for item in content.remediation for evidence_id in item.evidence_ids),
    ]


def _rca_prose(content: RcaContent) -> list[str]:
    """Every generated text except remediation commands, which have their own rules."""
    return [
        content.summary,
        content.root_cause.statement,
        *content.reasoning,
        *(item.observation for item in content.supporting_evidence),
        *content.limitations,
        *(item.statement for item in content.alternative_causes),
        *(text for item in content.remediation for text in (item.action, item.why)),
        *content.verification_steps,
    ]


def _remediation_refusal(command: str, namespace: str) -> str | None:
    """Why a suggested command is refused, as a fixed rule name, or None if it is allowed."""
    command = _PLACEHOLDER.sub("PLACEHOLDER", command)
    calls = _KUBECTL_CALL.findall(command)
    # One kubectl call per command, so nothing can be chained or hidden after it.
    if len(calls) != 1 or not re.match(r"^\s*kubectl(?:\.exe)?\s", command, re.IGNORECASE):
        return "not_one_kubectl_call"
    arguments = calls[0]
    if _UNSAFE_KUBECTL_ARGUMENTS.search(arguments):
        return "unsafe_argument"
    if _FORBIDDEN_REMEDIATION.search(command):
        return "forbidden_action"
    if any(value.strip("'\"") != namespace for value in _NAMESPACE_FLAG.findall(arguments)):
        return "other_namespace"
    words = arguments.lower().split()
    while words and words[0].startswith("-"):
        flag = words.pop(0)
        if flag in _KUBECTL_FLAGS_WITH_VALUES and words:
            words.pop(0)
    return None if words and words[0] in _REMEDIATION_KUBECTL else "verb_not_allowed"


def _remediation_command_is_allowed(command: str, namespace: str) -> bool:
    return _remediation_refusal(command, namespace) is None


def _grounding_failures(
    prose: list[str],
    commands: list[str],
    cited: list[str],
    read_only_steps: list[str],
    known_message: str,
    citable_ids: set[str],
    namespace: str,
    problem_confirmed: bool,
    about_this_cluster: bool = True,
) -> list[str]:
    """The checks shared by every generated answer, compared with the exact input the model was given.

    about_this_cluster is False for general technical guidance. Such an answer may use ordinary
    examples and values, but it still may not cite unknown evidence, claim actions, suggest unsafe
    commands, contradict a confirmed problem or contain secrets.
    """
    prose = [_STEP_NUMBER.sub("", text) for text in prose]
    combined = "\n".join([*prose, *commands])
    known = known_message.lower()
    failed = [name for name in ("claimed_action", "unsafe_command", "echoed_injection") if _CHECKS[name].search(combined)]

    if _PERCENTAGE.search(combined):
        failed.append("confidence_score")
    if _CERTAINTY.search(combined):
        failed.append("claimed_certainty")
    if problem_confirmed and _DISMISSES_PROBLEM.search(combined):
        failed.append("contradicts_diagnosis")
    if any(evidence_id not in citable_ids for evidence_id in cited):
        failed.append("unknown_evidence_id")
    if any(token.rstrip(".,;:") not in known_message for token in _EVIDENCE_ID.findall(combined)):
        failed.append("evidence_identifier")
    if about_this_cluster:
        if _timestamps(combined) - _timestamps(known_message):
            failed.append("invented_timestamp")
        if _numbers(combined) - _numbers(known_message):
            failed.append("invented_value")
        if any(url.rstrip(".,").lower() not in known for url in _URL.findall(combined)):
            failed.append("invented_url")
        names = {name for name in [*_RESOURCE_NAME.findall(combined), *_QUOTED_NAME.findall(combined)] if "-" in name or any(c.isdigit() for c in name)}
        if any(name not in known for name in names):
            failed.append("invented_resource")
        allowed_terms = {term for scenario, terms in _SCENARIO_TERMS.items() if scenario in known_message for term in terms}
        if any(term in combined and term not in known_message and term not in allowed_terms for term in _FAILURE_TERMS):
            failed.append("unsupported_failure_type")
    # Outside remediation commands, kubectl may only read.
    if not all(_kubectl_is_read_only(arguments) for arguments in _KUBECTL_CALL.findall("\n".join(prose))):
        failed.append("unsafe_command")
    if not all(_remediation_command_is_allowed(command, namespace) for command in commands):
        failed.append("unsafe_remediation")
    if any(_DESTRUCTIVE_STEP.search(step) for step in read_only_steps):
        failed.append("destructive_step")

    redactor = Redactor()
    redactor.text(combined)
    if redactor.count:
        failed.append("sensitive_content")
    return sorted(set(failed))


def find_unsupported_rca_claims(
    content: RcaContent, package_message: str, citable_ids: set[str], namespace: str, problem_confirmed: bool
) -> list[str]:
    """Return the names of the checks this analysis fails. An empty list means it passed.

    Everything is compared with the exact package the model was given. These checks catch
    invented values and forbidden claims. They cannot prove every sentence is true.
    """
    return _grounding_failures(
        _rca_prose(content),
        [command for item in content.remediation for command in item.commands],
        _cited_ids(content),
        content.verification_steps,
        package_message,
        citable_ids,
        namespace,
        problem_confirmed,
    )


def parse_chat_answer(raw: str) -> ChatAnswer:
    """Parse exact JSON into the only accepted shape. Nothing is repaired or guessed."""
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ProviderError("malformed_response") from exc
    try:
        return ChatAnswer.model_validate(document)
    except ValidationError as exc:
        _log_schema_errors("Chat answer", exc)
        raise ProviderError("schema_validation_failed") from exc


def find_unsupported_chat_claims(
    answer: ChatAnswer, context_message: str, citable_ids: set[str], namespace: str, problem_confirmed: bool
) -> list[str]:
    """Return the names of the checks this chat answer fails, compared with the exact context sent."""
    return _grounding_failures(
        [answer.answer, *answer.limitations, *answer.follow_up_questions, *(t for i in answer.remediation for t in (i.action, i.why))],
        [command for item in answer.remediation for command in item.commands],
        [*answer.evidence_ids, *(evidence_id for item in answer.remediation for evidence_id in item.evidence_ids)],
        [],
        context_message,
        citable_ids,
        namespace,
        problem_confirmed,
        about_this_cluster=answer.scope == "investigation",
    )
