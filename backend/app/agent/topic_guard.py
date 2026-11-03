"""A deterministic check of what a chat message is about, made before any request to the model.

It accepts Kubernetes and DevOps questions and anything about the current investigation, refuses
clearly unrelated requests and attempts to change the assistant's rules, and asks for clarification
otherwise. It works when the LLM is unavailable, and a refused message uses no request or budget.
It matches words, so it can be wrong either way: when unsure it asks, and the model's own rules and
the output checks still apply to everything it lets through.
"""

import re
from typing import Literal

Decision = Literal["allowed", "off_topic", "clarify", "policy"]

OFF_TOPIC_REPLY = (
    "I can help with Kubernetes troubleshooting and DevOps-related technical questions. "
    "Please ask me something related to those topics."
)
CLARIFY_REPLY = (
    "Could you say a little more about what you need? I can help with this investigation, "
    "or with a Kubernetes or DevOps question."
)
POLICY_REPLY = (
    "I can't change how I work, or reveal my instructions, configuration or credentials. "
    "Ask me about this investigation, or a Kubernetes or DevOps topic."
)

_WORD = re.compile(r"[a-z0-9][a-z0-9_./:-]*")

TECHNICAL_TERMS = frozenset(
    """
    kubernetes k8s kubectl kubelet kind minikube helm kustomize pod pods container containers containerd
    docker dockerfile compose image images registry deployment deployments replicaset replicasets statefulset
    daemonset job cronjob service services ingress namespace namespaces node nodes cluster clusters manifest
    manifests yaml json label labels selector selectors annotation endpoint endpoints endpointslice probe probes
    readiness liveness startup crashloopbackoff imagepullbackoff errimagepull pending evicted oomkilled
    restart restarts restarting rollout rollback replica replicas scale scaling hpa autoscaling scheduler
    scheduling taint toleration affinity resources cpu memory limit limits request requests quota pvc pv
    volume volumes storage storageclass configmap secret secrets serviceaccount rbac role rolebinding
    clusterrole permission permissions log logs event events exit oom crash crashing crashed
    dns network networking port ports tcp udp http https tls ssl certificate certificates proxy loadbalancer
    linux bash shell systemd process processes kernel cgroup cgroups git github gitlab branch commit merge
    jenkins jenkinsfile pipeline pipelines ci cd ci/cd build builds artifact terraform ansible iac aws azure
    gcp cloud ec2 eks s3 iam vpc prometheus grafana promql metric metrics alert alerts alertmanager
    monitoring observability tracing trivy vulnerability vulnerabilities cve scan devops devsecops sre
    incident outage latency availability uptime python fastapi api apis backend frontend nginx sqlite database
    timeout timeouts error errors exception failure failures failing failed debug debugging troubleshoot
    troubleshooting deploy deploying deployed release version upgrade
    """.split()
)
# Words that point at the investigation on screen, so short follow-ups like "Why?" or "How do I fix it?" pass.
INVESTIGATION_TERMS = frozenset(
    """
    issue issues problem problems fix fixes fixed cause causes root diagnosis diagnoses evidence finding
    findings recommendation recommendations remediation verify verification recovered recovery healthy
    unhealthy broken wrong why investigation analysis confidence risk risky safe command commands step steps
    explain explanation mean means meaning happened happening
    """.split()
)
OFF_TOPIC_TERMS = frozenset(
    """
    lunch dinner breakfast food restaurant restaurants recipe recipes cook cooking movie movies film films
    music song songs singer weather joke jokes poem poems story stories sport sports football cricket
    soccer basketball dating girlfriend boyfriend horoscope astrology celebrity celebrities shopping
    fashion travel holiday vacation hotel hotels flight flights lottery gambling betting
    """.split()
)
_POLICY = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,40}\b(?:instructions?|rules?|polic(?:y|ies)|prompt|guard)"
    r"|\b(?:system|developer|hidden)\s+(?:prompt|message|instructions?)"
    r"|\b(?:jailbreak|dan mode|developer mode)\b"
    r"|\b(?:reveal|show|print|tell|give|leak|share)\b.{0,40}\b(?:api[ _-]?key|password|credentials?|token|secrets?)\b"
    r"|\bopenrouter\b.{0,20}\bkey\b",
    re.IGNORECASE | re.DOTALL,
)


def classify(message: str, investigation_words: set[str] = frozenset()) -> Decision:
    """Decide what to do with a message, from its words alone. investigation_words are names from this investigation."""
    if _POLICY.search(message):
        return "policy"
    words = set(_WORD.findall(message.lower()))
    # Hyphenated or dotted terms such as ci/cd or crash-loop also count by their parts.
    words |= {part for word in words for part in re.split(r"[./:_-]", word) if part}
    technical = bool(words & TECHNICAL_TERMS) or bool(words & investigation_words)
    if words & OFF_TOPIC_TERMS:
        # A mix such as "a joke about Kubernetes" is not clearly technical, so it is checked with the user.
        return "clarify" if technical else "off_topic"
    if technical or words & INVESTIGATION_TERMS:
        return "allowed"
    return "clarify"
