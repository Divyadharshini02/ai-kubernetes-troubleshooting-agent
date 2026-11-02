"""Runs one investigation, recording each stage only after it has actually happened.

The diagnosis comes from the same evidence collection and rules as the diagnosis endpoint.
Nothing here changes the cluster, and an LLM explanation cannot change the diagnosis.
"""

import logging
import uuid

from app.agent.explainer import explain_report
from app.diagnostics.engine import diagnose
from app.kubernetes.cluster import ClusterAccessError, read_cluster_health
from app.models.investigation import InvestigationSummary
from app.services.evidence import NamespaceNotFoundError, collect_namespace_evidence
from app.storage import investigations as storage

logger = logging.getLogger(__name__)


def start_investigation(namespace: str, explain: bool) -> InvestigationSummary:
    return storage.create_investigation(uuid.uuid4().hex, namespace, explain, f"Investigation of {namespace} created.")


def _investigate(investigation_id: str, namespace: str, explain: bool) -> None:
    health = read_cluster_health()
    storage.add_event(investigation_id, "connectivity_checked", f"Connected to context {health.context}.")

    bundle = collect_namespace_evidence(namespace)
    storage.add_event(
        investigation_id,
        "evidence_collected",
        f"Collected {len(bundle.records)} evidence records. Completeness: {bundle.completeness}.",
    )

    report = diagnose(bundle)
    storage.add_event(
        investigation_id,
        "rules_evaluated",
        f"Ran {len(report.rule_results)} diagnostic rules, which produced {len(report.diagnoses)} diagnoses.",
    )

    if explain:
        storage.add_event(investigation_id, "ai_requested", "An AI explanation was requested.")
        report.llm = explain_report(report, bundle)
        storage.add_event(investigation_id, "ai_finished", f"AI explanation status: {report.llm.status}.")

    report.evidence = bundle
    storage.complete_investigation(investigation_id, report)


def run_investigation(investigation_id: str, namespace: str, explain: bool) -> None:
    """Run to a final saved status. This runs after the response, so it must not raise."""
    try:
        try:
            _investigate(investigation_id, namespace, explain)
        except NamespaceNotFoundError:
            storage.fail_investigation(
                investigation_id, "namespace_not_found", "The Kubernetes API reports that this namespace does not exist."
            )
        except ClusterAccessError as exc:
            storage.fail_investigation(investigation_id, exc.code, exc.message)
        except Exception as exc:
            # The type only: exception text can quote cluster data.
            logger.warning("Investigation failed unexpectedly: %s", type(exc).__name__)
            storage.fail_investigation(
                investigation_id, "unexpected_error", "The investigation failed unexpectedly. Check the backend log."
            )
    except storage.StorageError as exc:
        # It stays marked running until the next backend start marks it as interrupted.
        logger.warning("Could not save the investigation's failure: %s", exc)
