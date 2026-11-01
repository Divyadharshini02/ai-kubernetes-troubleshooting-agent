from app.collectors.nodes import collect_nodes
from stub_kube import SECRET_VALUE, list_of

NODES = "/api/v1/nodes"


def test_node_reports_scheduling_evidence(ctx, kube):
    node = {
        "metadata": {"name": "node-1", "labels": {"disk": "ssd"}, "annotations": {"note": SECRET_VALUE}},
        "spec": {
            "unschedulable": True,
            "taints": [{"key": "dedicated", "value": "db", "effect": "NoSchedule"}],
        },
        "status": {
            "capacity": {"cpu": "4", "memory": "8Gi"},
            "allocatable": {"cpu": "3", "memory": "7Gi"},
            "conditions": [{"type": "Ready", "status": "True", "reason": "KubeletReady", "message": "ok"}],
        },
    }
    kube.routes[NODES] = (200, list_of("NodeList", [node]))

    record = collect_nodes(ctx)[0]

    assert record.evidence_id == "node:node-1"
    assert record.namespace is None
    assert record.temporality == "current"
    assert record.data["unschedulable"] is True
    assert record.data["taints"] == [{"key": "dedicated", "value": "db", "effect": "NoSchedule"}]
    assert record.data["labels"] == {"disk": "ssd"}
    assert record.data["capacity"] == {"cpu": "4", "memory": "8Gi"}
    assert record.data["allocatable"] == {"cpu": "3", "memory": "7Gi"}
    assert record.data["conditions"][0]["type"] == "Ready"
    assert SECRET_VALUE not in record.model_dump_json()


def test_node_with_little_data_does_not_invent_any(ctx, kube):
    kube.routes[NODES] = (200, list_of("NodeList", [{"metadata": {"name": "bare"}}]))

    data = collect_nodes(ctx)[0].data

    assert data["unschedulable"] is False
    assert data["taints"] == []
    assert data["capacity"] == {}
    assert data["conditions"] == []
    assert data["kubelet_version"] is None
