"""Pods and containers: current state, last termination and configuration.

Fields are copied from an allowlist. Environment variable values, container
commands and annotations are deliberately never read, because they can hold secrets.
"""

from app.collectors.base import (
    CollectorContext,
    collect_list,
    conditions_summary,
    owner_references,
    utc_now,
)
from app.collectors.redaction import Redactor
from app.models.evidence import EvidenceRecord


def parse_image_reference(image: str | None) -> dict:
    """Split an image reference exactly as written, without assuming defaults."""
    if not image:
        return {"reference": image, "registry": None, "repository": None, "tag": None, "digest": None}
    name, _, digest = image.partition("@")
    head, slash, rest = name.partition("/")
    has_registry = bool(slash) and ("." in head or ":" in head or head == "localhost")
    registry = head if has_registry else None
    path = rest if has_registry else name
    repository, colon, tag = path.rpartition(":") if ":" in path else (path, "", "")
    return {
        "reference": image,
        "registry": registry,
        "repository": repository,
        "tag": tag if colon else None,
        "digest": digest or None,
    }


def _state_summary(state, redactor: Redactor) -> dict | None:
    if state is None:
        return None
    if state.running:
        return {"kind": "running", "started_at": state.running.started_at}
    if state.waiting:
        return {
            "kind": "waiting",
            "reason": state.waiting.reason,
            "message": redactor.text(state.waiting.message),
        }
    if state.terminated:
        return {
            "kind": "terminated",
            "reason": state.terminated.reason,
            "message": redactor.text(state.terminated.message),
            "exit_code": state.terminated.exit_code,
            "signal": state.terminated.signal,
            "started_at": state.terminated.started_at,
            "finished_at": state.terminated.finished_at,
        }
    return None


def _probe_summary(probe, redactor: Redactor) -> dict | None:
    if probe is None:
        return None
    summary = {
        "initial_delay_seconds": probe.initial_delay_seconds,
        "period_seconds": probe.period_seconds,
        "timeout_seconds": probe.timeout_seconds,
        "success_threshold": probe.success_threshold,
        "failure_threshold": probe.failure_threshold,
    }
    if probe.http_get:
        headers = probe.http_get.http_headers or []
        summary["handler"] = {
            "type": "http_get",
            "path": probe.http_get.path,
            "port": probe.http_get.port,
            "scheme": probe.http_get.scheme,
            # Header values can carry credentials.
            "header_names": [header.name for header in headers],
        }
    elif probe.tcp_socket:
        summary["handler"] = {"type": "tcp_socket", "port": probe.tcp_socket.port}
    elif probe.grpc:
        summary["handler"] = {
            "type": "grpc",
            "port": probe.grpc.port,
            "service": probe.grpc.service,
        }
    elif probe.var_exec:
        summary["handler"] = {
            "type": "exec",
            "command": [redactor.text(part) for part in probe.var_exec.command or []],
        }
    return summary


def _env_summary(container) -> dict:
    variables = []
    for env in container.env or []:
        source = env.value_from
        entry = {"name": env.name, "source": "literal"}
        if source is None:
            pass
        elif source.secret_key_ref:
            entry.update(
                source="secret",
                secret_name=source.secret_key_ref.name,
                key=source.secret_key_ref.key,
                optional=source.secret_key_ref.optional,
            )
        elif source.config_map_key_ref:
            entry.update(
                source="config_map",
                config_map_name=source.config_map_key_ref.name,
                key=source.config_map_key_ref.key,
                optional=source.config_map_key_ref.optional,
            )
        elif source.field_ref:
            entry.update(source="field", field_path=source.field_ref.field_path)
        elif source.resource_field_ref:
            entry.update(source="resource", resource=source.resource_field_ref.resource)
        else:
            entry["source"] = "other"
        variables.append(entry)

    references = []
    for env_from in container.env_from or []:
        if env_from.secret_ref:
            references.append({"kind": "Secret", "name": env_from.secret_ref.name})
        elif env_from.config_map_ref:
            references.append({"kind": "ConfigMap", "name": env_from.config_map_ref.name})
    return {"variables": variables, "from": references}


def _container_records(ctx, pod, container, status, container_type, source) -> list[EvidenceRecord]:
    namespace, pod_name = pod.metadata.namespace, pod.metadata.name
    target = f"{namespace}/{pod_name}/{container.name}"
    redactor = Redactor()
    resources = container.resources
    data = {
        "pod": pod_name,
        "container": container.name,
        "container_type": container_type,
        # False means the kubelet has not reported this container yet.
        "status_reported": status is not None,
        "image": parse_image_reference(container.image),
        "image_id": status.image_id if status else None,
        "image_pull_policy": container.image_pull_policy,
        "ready": status.ready if status else None,
        "started": status.started if status else None,
        "restart_count": status.restart_count if status else None,
        "state": _state_summary(status.state, redactor) if status else None,
        "has_last_termination": bool(status and status.last_state and status.last_state.terminated),
        "probes": {
            "readiness": _probe_summary(container.readiness_probe, redactor),
            "liveness": _probe_summary(container.liveness_probe, redactor),
            "startup": _probe_summary(container.startup_probe, redactor),
        },
        "resources": {
            "requests": (resources.requests or {}) if resources else {},
            "limits": (resources.limits or {}) if resources else {},
        },
        "ports": [
            {"name": port.name, "container_port": port.container_port, "protocol": port.protocol}
            for port in container.ports or []
        ],
        "env": _env_summary(container),
    }
    records = [
        EvidenceRecord(
            evidence_id=f"container:{target}",
            category="container",
            resource_kind="Container",
            resource_name=container.name,
            namespace=namespace,
            collected_at=utc_now(),
            source=source,
            redactions=redactor.count,
            data=data,
        )
    ]
    if data["has_last_termination"]:
        last_redactor = Redactor()
        last = _state_summary(status.last_state, last_redactor)
        records.append(
            EvidenceRecord(
                evidence_id=f"container-last-state:{target}",
                category="container_last_state",
                resource_kind="Container",
                resource_name=container.name,
                namespace=namespace,
                collected_at=utc_now(),
                source=source,
                temporality="historical",
                observed_at=last["finished_at"],
                redactions=last_redactor.count,
                data={"pod": pod_name, "container": container.name, "last_state": last},
            )
        )
    return records


def _pod_records(ctx, pod, source) -> list[EvidenceRecord]:
    spec, status, metadata = pod.spec, pod.status, pod.metadata
    redactor = Redactor()
    record = EvidenceRecord(
        evidence_id=f"pod:{metadata.namespace}/{metadata.name}",
        category="pod",
        resource_kind="Pod",
        resource_name=metadata.name,
        namespace=metadata.namespace,
        collected_at=utc_now(),
        source=source,
        data={
            "phase": status.phase if status else None,
            "reason": status.reason if status else None,
            "message": redactor.text(status.message) if status else None,
            "start_time": status.start_time if status else None,
            "conditions": conditions_summary(status.conditions if status else None, redactor),
            "labels": metadata.labels or {},
            "owner_references": owner_references(metadata),
            "deletion_timestamp": metadata.deletion_timestamp,
            "node_name": spec.node_name,
            "node_selector": spec.node_selector or {},
            "affinity": ctx.api_client.sanitize_for_serialization(spec.affinity),
            "tolerations": ctx.api_client.sanitize_for_serialization(spec.tolerations or []),
            "restart_policy": spec.restart_policy,
            "service_account": spec.service_account_name,
            # Names only: the Secret contents are never read.
            "image_pull_secrets": [ref.name for ref in spec.image_pull_secrets or []],
            "persistent_volume_claims": [
                volume.persistent_volume_claim.claim_name
                for volume in spec.volumes or []
                if volume.persistent_volume_claim
            ],
        },
    )
    record.redactions = redactor.count

    records = [record]
    for container_type, containers, statuses in (
        ("init", spec.init_containers, status.init_container_statuses if status else None),
        ("app", spec.containers, status.container_statuses if status else None),
    ):
        by_name = {item.name: item for item in statuses or []}
        for container in containers or []:
            records.extend(
                _container_records(
                    ctx, pod, container, by_name.get(container.name), container_type, source
                )
            )
    return records


def collect_pods(ctx: CollectorContext) -> list[EvidenceRecord]:
    source = f"GET /api/v1/namespaces/{ctx.namespace}/pods"
    return collect_list(
        ctx,
        collector="pods",
        category="pod",
        kind="Pod",
        source=source,
        namespace=ctx.namespace,
        fetch=lambda **kwargs: ctx.core.list_namespaced_pod(ctx.namespace, **kwargs),
        to_records=lambda pod: _pod_records(ctx, pod, source),
    )
