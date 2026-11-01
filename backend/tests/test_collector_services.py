from app.collectors.services import collect_endpoint_slices, collect_services
from stub_kube import NAMESPACE, list_of, metadata

SERVICES = f"/api/v1/namespaces/{NAMESPACE}/services"
SLICES = f"/apis/discovery.k8s.io/v1/namespaces/{NAMESPACE}/endpointslices"


def test_service_reports_selector_and_ports(ctx, kube):
    service = {
        "metadata": metadata("web"),
        "spec": {
            "type": "ClusterIP",
            "clusterIP": "10.0.0.1",
            "selector": {"app": "web"},
            "ports": [
                {"name": "http", "port": 80, "targetPort": "http", "protocol": "TCP"},
                {"name": "metrics", "port": 9100, "targetPort": 9100, "protocol": "TCP"},
            ],
        },
    }
    kube.routes[SERVICES] = (200, list_of("ServiceList", [service]))

    record = collect_services(ctx)[0]

    assert record.evidence_id == f"service:{NAMESPACE}/web"
    assert record.temporality == "current"
    assert record.data["selector"] == {"app": "web"}
    assert record.data["cluster_ip"] == "10.0.0.1"
    assert record.data["ports"] == [
        {"name": "http", "port": 80, "target_port": "http", "protocol": "TCP", "node_port": None},
        {"name": "metrics", "port": 9100, "target_port": 9100, "protocol": "TCP", "node_port": None},
    ]


def test_service_without_selector_is_reported_as_empty_selector(ctx, kube):
    service = {"metadata": metadata("external"), "spec": {"ports": [{"port": 443}]}}
    kube.routes[SERVICES] = (200, list_of("ServiceList", [service]))

    assert collect_services(ctx)[0].data["selector"] == {}


def test_endpoint_slice_reports_addresses_and_readiness(ctx, kube):
    endpoint_slice = {
        "metadata": metadata("web-1", labels={"kubernetes.io/service-name": "web"}),
        "addressType": "IPv4",
        "endpoints": [
            {
                "addresses": ["10.1.0.5"],
                "conditions": {"ready": False, "serving": True, "terminating": False},
                "nodeName": "node-1",
                "targetRef": {"kind": "Pod", "name": "web-abc"},
            }
        ],
        "ports": [{"name": "http", "port": 80, "protocol": "TCP"}],
    }
    kube.routes[SLICES] = (200, list_of("EndpointSliceList", [endpoint_slice]))

    record = collect_endpoint_slices(ctx)[0]

    assert record.evidence_id == f"endpointslice:{NAMESPACE}/web-1"
    assert record.data["service"] == "web"
    assert record.data["address_type"] == "IPv4"
    assert record.data["endpoints"] == [
        {
            "addresses": ["10.1.0.5"],
            "ready": False,
            "serving": True,
            "terminating": False,
            "node_name": "node-1",
            "target": {"kind": "Pod", "name": "web-abc"},
        }
    ]
    assert record.data["ports"] == [{"name": "http", "port": 80, "protocol": "TCP"}]


def test_null_endpoints_from_a_service_with_no_matches(ctx, kube):
    endpoint_slice = {
        "metadata": metadata("orphan-1", labels={"kubernetes.io/service-name": "orphan"}),
        "addressType": "IPv4",
        "endpoints": None,
        "ports": None,
    }
    kube.routes[SLICES] = (200, list_of("EndpointSliceList", [endpoint_slice]))

    record = collect_endpoint_slices(ctx)[0]

    assert record.status == "collected"
    assert record.data["service"] == "orphan"
    assert record.data["endpoints"] == []
    assert record.data["ports"] == []


def test_endpoint_without_conditions_stays_unknown(ctx, kube):
    endpoint_slice = {
        "metadata": metadata("web-1"),
        "addressType": "IPv4",
        "endpoints": [{"addresses": ["10.1.0.5"]}],
    }
    kube.routes[SLICES] = (200, list_of("EndpointSliceList", [endpoint_slice]))

    data = collect_endpoint_slices(ctx)[0].data

    assert data["service"] is None
    assert data["endpoints"][0]["ready"] is None
    assert data["endpoints"][0]["target"] is None
