"""Run every evidence collector for one namespace. Performs read requests only."""

import logging
import time
from collections import Counter

from kubernetes import client

from app.collectors.base import CollectorContext, utc_now
from app.collectors.events import collect_events
from app.collectors.logs import collect_logs
from app.collectors.namespaces import collect_namespace
from app.collectors.nodes import collect_nodes
from app.collectors.pods import collect_pods
from app.collectors.serviceaccounts import collect_service_accounts
from app.collectors.services import collect_endpoint_slices, collect_services
from app.collectors.storage import collect_persistent_volume_claims, collect_storage_classes
from app.collectors.workloads import collect_deployments, collect_replica_sets
from app.kubernetes.cluster import ClusterAccessError, load_configuration
from app.models.evidence import INCOMPLETE_STATUSES, EvidenceBundle, EvidenceError, EvidenceRecord

logger = logging.getLogger(__name__)

COLLECTION_DEADLINE_SECONDS = 45


class NamespaceNotFoundError(Exception):
    pass


def _collector_outcome(
    collector: str, category: str, code: str, message: str, status: str
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"{collector}:collector",
        category=category,
        resource_kind="Collector",
        collected_at=utc_now(),
        source=f"collector:{collector}",
        status=status,
        error=EvidenceError(code=code, message=message),
    )


def collect_namespace_evidence(namespace: str) -> EvidenceBundle:
    """Collect evidence for a namespace.

    Raises ClusterAccessError when the cluster cannot be used at all and
    NamespaceNotFoundError when the namespace does not exist. Any later failure
    is recorded in the bundle, which is then marked partial.
    """
    started_at = utc_now()
    configuration, context = load_configuration()

    with client.ApiClient(configuration) as api_client:
        ctx = CollectorContext(
            api_client=api_client,
            namespace=namespace,
            deadline=time.monotonic() + COLLECTION_DEADLINE_SECONDS,
        )
        namespace_record = collect_namespace(ctx)
        if namespace_record.status == "not_found":
            raise NamespaceNotFoundError(namespace)
        if namespace_record.error and namespace_record.error.code == "api_unreachable":
            raise ClusterAccessError(
                "cluster_unreachable",
                "The Kubernetes API could not be reached.",
            )

        records = [namespace_record]
        # The category matches the records each collector produces, so a failed
        # collector marks that kind of evidence as unavailable.
        collectors = [
            ("pods", "pod", collect_pods),
            ("logs", "logs", lambda current: collect_logs(current, records)),
            ("events", "event", collect_events),
            ("deployments", "deployment", collect_deployments),
            ("replicasets", "replicaset", collect_replica_sets),
            ("services", "service", collect_services),
            ("endpointslices", "endpointslice", collect_endpoint_slices),
            ("nodes", "node", collect_nodes),
            ("pvcs", "pvc", collect_persistent_volume_claims),
            ("storageclasses", "storageclass", collect_storage_classes),
            ("serviceaccounts", "serviceaccount", collect_service_accounts),
        ]
        for name, category, collector in collectors:
            if ctx.expired():
                records.append(
                    _collector_outcome(
                        name,
                        category,
                        "deadline_exceeded",
                        "The collection time limit was reached before this collector ran.",
                        "skipped",
                    )
                )
                continue
            try:
                records.extend(collector(ctx))
            # One collector's defect must not discard evidence from the others.
            except Exception as exc:
                logger.exception("Collector %s failed: %s", name, type(exc).__name__)
                records.append(
                    _collector_outcome(
                        name,
                        category,
                        "collector_error",
                        "This collector failed unexpectedly.",
                        "failed",
                    )
                )

    counts = Counter(record.status for record in records)
    return EvidenceBundle(
        namespace=namespace,
        context=context,
        started_at=started_at,
        completed_at=utc_now(),
        completeness="partial" if INCOMPLETE_STATUSES & set(counts) else "complete",
        status_counts=dict(counts),
        records=records,
    )
