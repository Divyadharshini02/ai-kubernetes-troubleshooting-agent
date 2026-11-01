"""Failure and empty-result behaviour that every list collector must share."""

import socket

import pytest
from kubernetes import client

from app.collectors.base import CollectorContext
from app.collectors.events import collect_events
from app.collectors.nodes import collect_nodes
from app.collectors.pods import collect_pods
from app.collectors.serviceaccounts import collect_service_accounts
from app.collectors.services import collect_endpoint_slices, collect_services
from app.collectors.storage import collect_persistent_volume_claims, collect_storage_classes
from app.collectors.workloads import collect_deployments, collect_replica_sets
from stub_kube import NAMESPACE, SECRET_VALUE, list_of, make_pod, metadata

BASE = f"/api/v1/namespaces/{NAMESPACE}"
APPS = f"/apis/apps/v1/namespaces/{NAMESPACE}"
DISCOVERY = f"/apis/discovery.k8s.io/v1/namespaces/{NAMESPACE}"

LIST_COLLECTORS = [
    pytest.param(collect_pods, f"{BASE}/pods", "PodList", "Pod", True, id="pods"),
    pytest.param(collect_events, f"{BASE}/events", "EventList", "Event", True, id="events"),
    pytest.param(collect_deployments, f"{APPS}/deployments", "DeploymentList", "Deployment", True, id="deployments"),
    pytest.param(collect_replica_sets, f"{APPS}/replicasets", "ReplicaSetList", "ReplicaSet", True, id="replicasets"),
    pytest.param(collect_services, f"{BASE}/services", "ServiceList", "Service", True, id="services"),
    pytest.param(collect_endpoint_slices, f"{DISCOVERY}/endpointslices", "EndpointSliceList", "EndpointSlice", True, id="endpointslices"),
    pytest.param(collect_nodes, "/api/v1/nodes", "NodeList", "Node", False, id="nodes"),
    pytest.param(collect_persistent_volume_claims, f"{BASE}/persistentvolumeclaims", "PersistentVolumeClaimList", "PersistentVolumeClaim", True, id="pvcs"),
    pytest.param(collect_storage_classes, "/apis/storage.k8s.io/v1/storageclasses", "StorageClassList", "StorageClass", False, id="storageclasses"),
    pytest.param(collect_service_accounts, f"{BASE}/serviceaccounts", "ServiceAccountList", "ServiceAccount", True, id="serviceaccounts"),
]


def assert_has_identity(record, kind, namespaced):
    assert record.evidence_id
    assert record.resource_kind == kind
    assert record.namespace == (NAMESPACE if namespaced else None)
    assert record.collected_at.tzinfo is not None
    assert record.source.startswith("GET /")


@pytest.mark.parametrize(("collect", "path", "list_kind", "kind", "namespaced"), LIST_COLLECTORS)
def test_empty_list_is_reported_as_empty(ctx, kube, collect, path, list_kind, kind, namespaced):
    kube.routes[path] = (200, list_of(list_kind, []))

    records = collect(ctx)

    assert len(records) == 1
    assert records[0].status == "empty"
    assert records[0].error is None
    assert_has_identity(records[0], kind, namespaced)


@pytest.mark.parametrize(
    ("http_status", "status", "code"),
    [
        (401, "forbidden", "access_denied"),
        (403, "forbidden", "access_denied"),
        (404, "not_found", "resource_not_found"),
        (500, "failed", "api_error"),
        (503, "failed", "api_error"),
    ],
)
@pytest.mark.parametrize(("collect", "path", "list_kind", "kind", "namespaced"), LIST_COLLECTORS)
def test_api_errors_become_explicit_records(
    ctx, kube, collect, path, list_kind, kind, namespaced, http_status, status, code
):
    kube.routes[path] = (http_status, {"kind": "Status", "message": f"leak {SECRET_VALUE}"})

    records = collect(ctx)

    assert len(records) == 1
    assert records[0].status == status
    assert records[0].error.code == code
    assert records[0].data == {}
    assert SECRET_VALUE not in records[0].model_dump_json()
    assert_has_identity(records[0], kind, namespaced)


@pytest.mark.parametrize(("collect", "path", "list_kind", "kind", "namespaced"), LIST_COLLECTORS)
def test_malformed_response_is_a_failure(ctx, kube, collect, path, list_kind, kind, namespaced):
    kube.routes[path] = (200, "this is not json")

    records = collect(ctx)

    assert [record.status for record in records] == ["failed"]
    assert records[0].error.code == "unexpected_response"


@pytest.mark.parametrize(("collect", "path", "list_kind", "kind", "namespaced"), LIST_COLLECTORS)
def test_unreachable_api_is_a_failure(collect, path, list_kind, kind, namespaced):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    configuration = client.Configuration(host=f"http://127.0.0.1:{closed_port}")
    configuration.retries = False
    with client.ApiClient(configuration) as api_client:
        ctx = CollectorContext(api_client=api_client, namespace=NAMESPACE, deadline=float("inf"))

        records = collect(ctx)

    assert [record.status for record in records] == ["failed"]
    assert records[0].error.code == "api_unreachable"


def test_truncated_list_keeps_data_and_says_so(ctx, kube):
    kube.routes[f"{BASE}/pods"] = (200, list_of("PodList", [make_pod()], continue_token="more"))

    records = collect_pods(ctx)

    limit = [record for record in records if record.evidence_id == "pods:limit"]
    assert len(limit) == 1
    assert limit[0].status == "skipped"
    assert limit[0].error.code == "list_truncated"
    assert any(record.evidence_id == f"pod:{NAMESPACE}/web" for record in records)


def test_list_requests_are_bounded(ctx, kube):
    kube.routes[f"{BASE}/services"] = (
        200,
        list_of("ServiceList", [{"metadata": metadata("s"), "spec": {}}]),
    )

    collect_services(ctx)

    method, _, query = kube.requests[0]
    assert method == "GET"
    assert query["limit"] == ["500"]
