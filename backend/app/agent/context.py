"""Builds the bounded evidence package for one investigation's root cause analysis.

Everything from the cluster is untrusted data. It goes into one JSON document as string values,
redacted again and cut to fixed sizes. The most relevant records go first, and whole records are
left out, never cut mid-text, when the package would be too large. The package says how many were
left out, so the model can state that as a limitation instead of assuming it saw everything.
"""

import json
from dataclasses import dataclass

from app.agent.prompt import _bound, _record_input
from app.models.diagnosis import DiagnosticReport
from app.models.evidence import INCOMPLETE_STATUSES, EvidenceBundle, EvidenceRecord

MAX_PACKAGE_CHARS = 16000
_MAX_FINDINGS = 6
_MAX_FINDING_ITEMS = 5


@dataclass(frozen=True)
class EvidencePackage:
    message: str
    evidence_ids: list[str]
    evidence_omitted: int


def _container_is_unhealthy(record: EvidenceRecord) -> bool:
    data = record.data
    state = data.get("state") or {}
    return data.get("ready") is False or bool(data.get("restart_count")) or bool(state and state.get("kind") != "running")


def _priority(record: EvidenceRecord, cited: set[str]) -> int:
    """Lower is sent first: what the rules relied on, then failures and signs of trouble."""
    if record.evidence_id in cited:
        return 0
    if record.status in INCOMPLETE_STATUSES:
        return 1
    if record.category == "logs" or (record.category == "event" and record.data.get("type") == "Warning"):
        return 2
    if record.category in ("container", "container_last_state") and _container_is_unhealthy(record):
        return 2
    if record.category in ("pod", "container", "container_last_state", "deployment", "replicaset", "service", "endpointslice", "event"):
        return 3
    return 4


def _finding(diagnosis) -> dict:
    return {
        "diagnosis_id": _bound(diagnosis.diagnosis_id),
        "scenario": diagnosis.scenario,
        "title": _bound(diagnosis.title),
        "status": diagnosis.status,
        "evidence_strength": diagnosis.evidence_strength,
        # Identifiers are built by the collectors from Kubernetes names, which allow no free text.
        "evidence_ids": list(dict.fromkeys(reference.evidence_id for reference in diagnosis.evidence_references))[: _MAX_FINDING_ITEMS * 2],
        "confirmed_facts": [_bound(fact.statement) for fact in diagnosis.confirmed_facts][:_MAX_FINDING_ITEMS],
        "possible_causes": [_bound(cause.statement) for cause in diagnosis.possible_causes][:_MAX_FINDING_ITEMS],
        "missing_evidence": [_bound(item) for item in diagnosis.missing_evidence][:_MAX_FINDING_ITEMS],
        "suggested_actions": [_bound(item.action) for item in diagnosis.recommendations][:_MAX_FINDING_ITEMS],
    }


def build_package(report: DiagnosticReport, bundle: EvidenceBundle, max_chars: int = MAX_PACKAGE_CHARS) -> EvidencePackage:
    """Serialize the investigation within a size limit. The same input always gives the same output."""
    cited = {reference.evidence_id for diagnosis in report.diagnoses for reference in diagnosis.evidence_references}
    ordered = sorted(enumerate(bundle.records), key=lambda item: (_priority(item[1], cited), item[0]))
    document = {
        "namespace": _bound(report.namespace),
        "outcome": report.outcome,
        "evidence_completeness": report.evidence_completeness,
        "rule_findings": [_finding(diagnosis) for diagnosis in report.diagnoses[:_MAX_FINDINGS]],
        "rule_findings_omitted": max(0, len(report.diagnoses) - _MAX_FINDINGS),
        "rules_not_fully_evaluated": [result.rule for result in report.rule_results if result.status != "evaluated"],
        "evidence": [],
        "evidence_omitted": 0,
    }
    base_size = len(json.dumps(document, ensure_ascii=False))
    size = base_size
    for _, record in ordered:
        item = _record_input(record)
        item_size = len(json.dumps(item, ensure_ascii=False)) + 2
        if size + item_size > max_chars:
            document["evidence_omitted"] += 1
            continue
        document["evidence"].append(item)
        size += item_size
    message = json.dumps(document, ensure_ascii=False)
    # The counter's own digits can add a few characters, so the result is checked as a whole.
    while len(message) > max_chars and document["evidence"]:
        document["evidence"].pop()
        document["evidence_omitted"] += 1
        message = json.dumps(document, ensure_ascii=False)
    sent = [item["evidence_id"] for item in document["evidence"]]
    # A citation may name a record that was sent, or one a rule finding lists. Both are real records.
    listed = [evidence_id for finding in document["rule_findings"] for evidence_id in finding["evidence_ids"]]
    return EvidencePackage(
        message=message,
        evidence_ids=list(dict.fromkeys([*sent, *listed])),
        evidence_omitted=document["evidence_omitted"],
    )
