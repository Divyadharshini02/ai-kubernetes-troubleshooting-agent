"""Builds evidence records for rule unit tests, shaped exactly as the M2 collectors shape them.

This is test data only. Production diagnostics only ever receive live collected evidence.
"""

from datetime import datetime, timedelta, timezone

from app.diagnostics.engine import diagnose
from app.diagnostics.index import EvidenceIndex
from app.models.evidence import EvidenceBundle, EvidenceError, EvidenceRecord

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
NS = "demo"
OLD = 86400
RECENT = 30


def ago(seconds):
    return NOW - timedelta(seconds=seconds)


def record(evidence_id, category, kind, name, data, **fields):
    return EvidenceRecord(
        evidence_id=evidence_id,
        category=category,
        resource_kind=kind,
        resource_name=name,
        namespace=fields.pop("namespace", NS),
        collected_at=NOW,
        source=f"GET /test/{category}",
        data=data,
        **fields,
    )


def pod(
    name="web",
    phase="Running",
    node="node-1",
    labels=None,
    conditions=None,
    ready=True,
    node_selector=None,
    affinity=None,
    tolerations=None,
    claims=None,
    pull_secrets=None,
    deleting=False,
    message=None,
):
    if conditions is None:
        conditions = [_condition("Ready", "True" if ready else "False")]
        if node:
            conditions.append(_condition("PodScheduled", "True"))
    return record(
        f"pod:{NS}/{name}",
        "pod",
        "Pod",
        name,
        {
            "phase": phase,
            "reason": None,
            "message": message,
            "start_time": ago(3600),
            "conditions": conditions,
            "labels": labels if labels is not None else {},
            "owner_references": [],
            "deletion_timestamp": ago(1) if deleting else None,
            "node_name": node,
            "node_selector": node_selector or {},
            "affinity": affinity,
            "tolerations": tolerations or [],
            "restart_policy": "Always",
            "service_account": "default",
            "image_pull_secrets": pull_secrets or [],
            "persistent_volume_claims": claims or [],
        },
    )


def _condition(kind, status, reason=None, message=None):
    return {
        "type": kind,
        "status": status,
        "reason": reason,
        "message": message,
        "last_transition_time": ago(60),
    }


def unscheduled(reason="Unschedulable", message="0/1 nodes are available"):
    return [_condition("PodScheduled", "False", reason, message)]


def running():
    return {"kind": "running", "started_at": ago(3600)}


def waiting(reason, message=None):
    return {"kind": "waiting", "reason": reason, "message": message}


def terminated(exit_code=1, reason="Error", finished=RECENT):
    return {
        "kind": "terminated",
        "reason": reason,
        "message": None,
        "exit_code": exit_code,
        "signal": None,
        "started_at": ago(finished + 5),
        "finished_at": ago(finished),
    }


def probe(handler=None, **timing):
    return {
        "initial_delay_seconds": timing.get("initial_delay", 0),
        "period_seconds": timing.get("period", 10),
        "timeout_seconds": timing.get("timeout", 1),
        "success_threshold": 1,
        "failure_threshold": timing.get("failure_threshold", 3),
        "handler": handler or {"type": "http_get", "path": "/health", "port": 8080, "scheme": "HTTP", "header_names": []},
    }


def container(
    pod_name="web",
    name="app",
    state=None,
    ready=True,
    restarts=0,
    started=True,
    readiness=None,
    liveness=None,
    startup=None,
    limits=None,
    requests=None,
    ports=None,
    image="registry.example/app:1",
    container_type="app",
    reported=True,
    has_last_termination=False,
):
    parts = {"reference": image, "registry": None, "repository": image, "tag": None, "digest": None}
    if image.startswith("registry.example/"):
        parts.update(registry="registry.example", repository="app", tag="1")
    return record(
        f"container:{NS}/{pod_name}/{name}",
        "container",
        "Container",
        name,
        {
            "pod": pod_name,
            "container": name,
            "container_type": container_type,
            "status_reported": reported,
            "image": parts,
            "image_id": None,
            "image_pull_policy": "IfNotPresent",
            "ready": ready if reported else None,
            "started": started if reported else None,
            "restart_count": restarts if reported else None,
            "state": (state or running()) if reported else None,
            "has_last_termination": has_last_termination,
            "probes": {"readiness": readiness, "liveness": liveness, "startup": startup},
            "resources": {"requests": requests or {}, "limits": limits or {}},
            "ports": ports or [],
            "env": {"variables": [], "from": []},
        },
    )


def last_state(exit_code=1, reason="Error", finished=RECENT, pod_name="web", name="app"):
    state = terminated(exit_code, reason, finished)
    return record(
        f"container-last-state:{NS}/{pod_name}/{name}",
        "container_last_state",
        "Container",
        name,
        {"pod": pod_name, "container": name, "last_state": state},
        temporality="historical",
        observed_at=state["finished_at"],
    )


def logs(pod_name="web", name="app", stream="current", text="2026-01-01T00:00:00Z ok\n", status="collected"):
    data = {"pod": pod_name, "container": name, "previous": stream == "previous"}
    if status == "collected":
        data["text"] = text
    return record(
        f"logs:{NS}/{pod_name}/{name}:{stream}",
        "logs",
        "Container",
        name,
        data,
        status=status,
        temporality="historical" if stream == "previous" else "current",
    )


_event_counter = iter(range(1, 100000))


def event(
    reason,
    message,
    name="web",
    kind="Pod",
    age=RECENT,
    event_type="Warning",
    count=1,
    container_name="app",
):
    identifier = f"event:{NS}/uid-{next(_event_counter)}"
    field_path = f"spec.containers{{{container_name}}}" if kind == "Pod" and container_name else None
    return record(
        identifier,
        "event",
        "Event",
        identifier.rsplit("/", maxsplit=1)[-1],
        {
            "type": event_type,
            "reason": reason,
            "message": message,
            "count": count,
            "first_seen": ago(age),
            "last_seen": ago(age),
            "age_seconds": age,
            "reporter": "kubelet",
            "involved_object": {"kind": kind, "name": name, "namespace": NS, "field_path": field_path},
        },
        temporality="historical",
        observed_at=ago(age),
    )


def service(name="web", selector=None, ports=None, service_type="ClusterIP"):
    return record(
        f"service:{NS}/{name}",
        "service",
        "Service",
        name,
        {
            "type": service_type,
            "cluster_ip": "10.0.0.1",
            "selector": selector if selector is not None else {"app": "web"},
            "ports": ports or [{"name": None, "port": 80, "target_port": 80, "protocol": "TCP", "node_port": None}],
        },
    )


def endpoint_slice(service_name="web", ready=(), not_ready=()):
    endpoints = [_endpoint(pod_name, True) for pod_name in ready]
    endpoints += [_endpoint(pod_name, False) for pod_name in not_ready]
    return record(
        f"endpointslice:{NS}/{service_name}-1",
        "endpointslice",
        "EndpointSlice",
        f"{service_name}-1",
        {"service": service_name, "address_type": "IPv4", "endpoints": endpoints, "ports": []},
    )


def _endpoint(pod_name, ready):
    return {
        "addresses": ["10.1.0.1"],
        "ready": ready,
        "serving": ready,
        "terminating": False,
        "node_name": "node-1",
        "target": {"kind": "Pod", "name": pod_name},
    }


def node(name="node-1", labels=None, taints=None, cpu="4", memory="8Gi", unschedulable=False):
    return record(
        f"node:{name}",
        "node",
        "Node",
        name,
        {
            "unschedulable": unschedulable,
            "taints": taints or [],
            "labels": labels or {},
            "capacity": {"cpu": cpu, "memory": memory},
            "allocatable": {"cpu": cpu, "memory": memory},
            "conditions": [],
            "kubelet_version": "v1.37.0",
        },
        namespace=None,
    )


def claim(name="data", phase="Pending", storage_class="fast"):
    return record(
        f"pvc:{NS}/{name}",
        "pvc",
        "PersistentVolumeClaim",
        name,
        {
            "phase": phase,
            "storage_class": storage_class,
            "volume_name": None,
            "access_modes": ["ReadWriteOnce"],
            "requested": {"storage": "1Gi"},
            "conditions": [],
        },
    )


def storage_class(name="standard"):
    return record(
        f"storageclass:{name}",
        "storageclass",
        "StorageClass",
        name,
        {"provisioner": "example", "volume_binding_mode": "Immediate", "is_default": True},
        namespace=None,
    )


def service_account(name="default", pull_secrets=None):
    return record(
        f"serviceaccount:{NS}/{name}",
        "serviceaccount",
        "ServiceAccount",
        name,
        {"image_pull_secrets": pull_secrets or []},
    )


def deployment(name="web", replicas=1, labels=None):
    return record(
        f"deployment:{NS}/{name}",
        "deployment",
        "Deployment",
        name,
        {
            "desired_replicas": replicas,
            "ready_replicas": replicas,
            "available_replicas": replicas,
            "updated_replicas": replicas,
            "unavailable_replicas": 0,
            "generation": 1,
            "observed_generation": 1,
            "selector": {"match_labels": labels or {"app": name}, "match_expressions": []},
            "strategy": "RollingUpdate",
            "conditions": [],
        },
    )


def unavailable(category, status="forbidden", suffix="request"):
    """A collection outcome, as collect_list records it when a list request fails."""
    return record(
        f"{category}s:{suffix}",
        category,
        category.capitalize(),
        None,
        {},
        status=status,
        error=EvidenceError(code="test", message="test"),
    )


def bundle(*records):
    statuses = [item.status for item in records]
    counts = {status: statuses.count(status) for status in set(statuses)}
    incomplete = {"forbidden", "failed", "skipped"} & set(statuses)
    return EvidenceBundle(
        namespace=NS,
        context="test-context",
        started_at=NOW,
        completed_at=NOW,
        completeness="partial" if incomplete else "complete",
        status_counts=counts,
        records=list(records),
    )


def index_of(*records):
    return EvidenceIndex(bundle(*records))


def report_of(*records):
    return diagnose(bundle(*records))


def statements(items):
    return " | ".join(item.statement for item in items)
