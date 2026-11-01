"""Read-only lookups over one evidence bundle, shared by every diagnostic rule."""

from typing import Literal

from app.models.diagnosis import EvidenceReference
from app.models.evidence import EvidenceBundle, EvidenceRecord

# A container that crashes keeps retrying with a back-off of at most five minutes, so
# anything older than two back-off periods no longer describes an active problem.
RECENT_SECONDS = 600

Availability = Literal["available", "partial", "unavailable"]


class EvidenceIndex:
    def __init__(self, bundle: EvidenceBundle):
        self.bundle = bundle
        self.namespace = bundle.namespace
        self.now = bundle.completed_at
        self._by_id = {record.evidence_id: record for record in bundle.records}

    def availability(self, category: str) -> Availability:
        """Say whether one kind of evidence can be relied on.

        Collection outcomes have no resource name, which separates them from a failure
        to read one specific resource, such as a single log stream.
        """
        outcomes = [
            record.status
            for record in self.bundle.records
            if record.category == category and record.resource_name is None
        ]
        if any(status in ("forbidden", "failed", "not_found") for status in outcomes):
            return "unavailable"
        if "skipped" in outcomes:
            return "partial"
        return "available"

    def has(self, category: str) -> bool:
        return self.availability(category) != "unavailable"

    def _collected(self, category: str) -> list[EvidenceRecord]:
        return [
            record
            for record in self.bundle.records
            if record.category == category
            and record.status == "collected"
            and record.resource_name is not None
        ]

    def pods(self) -> list[EvidenceRecord]:
        """Pods not being deleted. A pod shutting down is expected to look unhealthy."""
        return [pod for pod in self._collected("pod") if not pod.data["deletion_timestamp"]]

    def pod(self, name: str) -> EvidenceRecord | None:
        return self._by_id.get(f"pod:{self.namespace}/{name}")

    def containers(self) -> list[EvidenceRecord]:
        active = {pod.resource_name for pod in self.pods()}
        return [c for c in self._collected("container") if c.data["pod"] in active]

    def containers_of(self, pod_name: str) -> list[EvidenceRecord]:
        return [c for c in self._collected("container") if c.data["pod"] == pod_name]

    def last_state(self, pod_name: str, container: str) -> EvidenceRecord | None:
        return self._by_id.get(f"container-last-state:{self.namespace}/{pod_name}/{container}")

    def logs(self, pod_name: str, container: str, stream: str) -> EvidenceRecord | None:
        return self._by_id.get(f"logs:{self.namespace}/{pod_name}/{container}:{stream}")

    def events_for(self, kind: str, name: str, container: str | None = None) -> list[EvidenceRecord]:
        """Events about one resource, newest first."""
        events = []
        for event in self._collected("event"):
            involved = event.data["involved_object"]
            if involved["kind"] != kind or involved["name"] != name:
                continue
            field_path = involved["field_path"]
            if container and field_path and f"{{{container}}}" not in field_path:
                continue
            events.append(event)
        return sorted(events, key=self._event_sort_key, reverse=True)

    def _event_sort_key(self, event: EvidenceRecord) -> tuple:
        # Events with no time sort last, and the identifier keeps the order stable.
        return (event.observed_at is not None, event.observed_at or self.now, event.evidence_id)

    def is_recent(self, record: EvidenceRecord) -> bool:
        return self.is_recent_time(record.observed_at)

    def is_recent_time(self, moment) -> bool:
        if moment is None:
            return False
        return (self.now - moment).total_seconds() <= RECENT_SECONDS

    def services(self) -> list[EvidenceRecord]:
        return self._collected("service")

    def endpoint_slices_for(self, service: str) -> list[EvidenceRecord]:
        return [s for s in self._collected("endpointslice") if s.data["service"] == service]

    def deployments(self) -> list[EvidenceRecord]:
        return self._collected("deployment")

    def nodes(self) -> list[EvidenceRecord]:
        return self._collected("node")

    def claim(self, name: str) -> EvidenceRecord | None:
        return self._by_id.get(f"pvc:{self.namespace}/{name}")

    def storage_class(self, name: str) -> EvidenceRecord | None:
        return self._by_id.get(f"storageclass:{name}")

    def service_account(self, name: str) -> EvidenceRecord | None:
        return self._by_id.get(f"serviceaccount:{self.namespace}/{name}")

    def reference(self, evidence_id: str) -> EvidenceReference:
        """Describe a cited record. An unknown identifier is a defect, so this raises."""
        record = self._by_id[evidence_id]
        return EvidenceReference(
            evidence_id=record.evidence_id,
            category=record.category,
            resource_kind=record.resource_kind,
            resource_name=record.resource_name,
            temporality=record.temporality,
            status=record.status,
            collected_at=record.collected_at,
            observed_at=record.observed_at,
            source=record.source,
        )
