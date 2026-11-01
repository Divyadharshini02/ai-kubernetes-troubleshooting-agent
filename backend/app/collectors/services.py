from app.collectors.base import CollectorContext, collect_list, utc_now
from app.models.evidence import EvidenceRecord

SERVICE_NAME_LABEL = "kubernetes.io/service-name"


def _service_record(service, source: str) -> EvidenceRecord:
    spec = service.spec
    return EvidenceRecord(
        evidence_id=f"service:{service.metadata.namespace}/{service.metadata.name}",
        category="service",
        resource_kind="Service",
        resource_name=service.metadata.name,
        namespace=service.metadata.namespace,
        collected_at=utc_now(),
        source=source,
        data={
            "type": spec.type,
            "cluster_ip": spec.cluster_ip,
            # An empty selector means endpoints are managed outside this Service.
            "selector": spec.selector or {},
            "ports": [
                {
                    "name": port.name,
                    "port": port.port,
                    "target_port": port.target_port,
                    "protocol": port.protocol,
                    "node_port": port.node_port,
                }
                for port in spec.ports or []
            ],
        },
    )


def _endpoint_slice_record(endpoint_slice, source: str) -> EvidenceRecord:
    labels = endpoint_slice.metadata.labels or {}
    endpoints = []
    # The API returns null endpoints for a Service that matches no pods.
    for endpoint in endpoint_slice.endpoints or []:
        conditions = endpoint.conditions
        target = endpoint.target_ref
        endpoints.append(
            {
                "addresses": endpoint.addresses,
                "ready": conditions.ready if conditions else None,
                "serving": conditions.serving if conditions else None,
                "terminating": conditions.terminating if conditions else None,
                "node_name": endpoint.node_name,
                "target": {"kind": target.kind, "name": target.name} if target else None,
            }
        )
    return EvidenceRecord(
        evidence_id=f"endpointslice:{endpoint_slice.metadata.namespace}/{endpoint_slice.metadata.name}",
        category="endpointslice",
        resource_kind="EndpointSlice",
        resource_name=endpoint_slice.metadata.name,
        namespace=endpoint_slice.metadata.namespace,
        collected_at=utc_now(),
        source=source,
        data={
            "service": labels.get(SERVICE_NAME_LABEL),
            "address_type": endpoint_slice.address_type,
            "endpoints": endpoints,
            "ports": [
                {"name": port.name, "port": port.port, "protocol": port.protocol}
                for port in endpoint_slice.ports or []
            ],
        },
    )


def collect_services(ctx: CollectorContext) -> list[EvidenceRecord]:
    source = f"GET /api/v1/namespaces/{ctx.namespace}/services"
    return collect_list(
        ctx,
        collector="services",
        category="service",
        kind="Service",
        source=source,
        namespace=ctx.namespace,
        fetch=lambda **kwargs: ctx.core.list_namespaced_service(ctx.namespace, **kwargs),
        to_records=lambda service: [_service_record(service, source)],
    )


def collect_endpoint_slices(ctx: CollectorContext) -> list[EvidenceRecord]:
    source = f"GET /apis/discovery.k8s.io/v1/namespaces/{ctx.namespace}/endpointslices"
    return collect_list(
        ctx,
        collector="endpointslices",
        category="endpointslice",
        kind="EndpointSlice",
        source=source,
        namespace=ctx.namespace,
        fetch=lambda **kwargs: ctx.discovery.list_namespaced_endpoint_slice(ctx.namespace, **kwargs),
        to_records=lambda endpoint_slice: [_endpoint_slice_record(endpoint_slice, source)],
    )
