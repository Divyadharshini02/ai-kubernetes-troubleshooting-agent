from app.collectors.workloads import collect_deployments, collect_replica_sets
from stub_kube import NAMESPACE, SECRET_VALUE, list_of, metadata

APPS = f"/apis/apps/v1/namespaces/{NAMESPACE}"
TEMPLATE = {
    "metadata": {"labels": {"app": "web"}},
    "spec": {
        "containers": [
            {"name": "app", "image": "i", "env": [{"name": "PLAIN", "value": SECRET_VALUE}]}
        ]
    },
}
SELECTOR = {"matchLabels": {"app": "web"}}


def test_deployment_reports_replicas_and_conditions(ctx, kube):
    deployment = {
        "metadata": metadata("web", generation=3),
        "spec": {
            "replicas": 3,
            "selector": SELECTOR,
            "template": TEMPLATE,
            "strategy": {"type": "RollingUpdate"},
        },
        "status": {
            "replicas": 3,
            "readyReplicas": 1,
            "availableReplicas": 1,
            "updatedReplicas": 3,
            "unavailableReplicas": 2,
            "observedGeneration": 3,
            "conditions": [
                {
                    "type": "Available",
                    "status": "False",
                    "reason": "MinimumReplicasUnavailable",
                    "message": f"token={SECRET_VALUE}",
                }
            ],
        },
    }
    kube.routes[f"{APPS}/deployments"] = (200, list_of("DeploymentList", [deployment]))

    record = collect_deployments(ctx)[0]

    assert record.evidence_id == f"deployment:{NAMESPACE}/web"
    assert record.temporality == "current"
    assert record.data["desired_replicas"] == 3
    assert record.data["ready_replicas"] == 1
    assert record.data["unavailable_replicas"] == 2
    assert record.data["generation"] == 3
    assert record.data["observed_generation"] == 3
    assert record.data["strategy"] == "RollingUpdate"
    assert record.data["selector"]["match_labels"] == {"app": "web"}
    assert record.data["conditions"][0]["reason"] == "MinimumReplicasUnavailable"
    assert record.redactions == 1
    assert SECRET_VALUE not in record.model_dump_json()


def test_deployment_with_no_status_counts_is_zero_not_missing(ctx, kube):
    deployment = {
        "metadata": metadata("web"),
        "spec": {"replicas": 1, "selector": SELECTOR, "template": TEMPLATE},
        "status": {},
    }
    kube.routes[f"{APPS}/deployments"] = (200, list_of("DeploymentList", [deployment]))

    data = collect_deployments(ctx)[0].data

    assert data["ready_replicas"] == 0
    assert data["available_replicas"] == 0
    assert data["conditions"] == []


def test_selector_expressions_are_collected(ctx, kube):
    selector = {"matchExpressions": [{"key": "tier", "operator": "In", "values": ["a", "b"]}]}
    deployment = {
        "metadata": metadata("web"),
        "spec": {"selector": selector, "template": TEMPLATE},
        "status": {},
    }
    kube.routes[f"{APPS}/deployments"] = (200, list_of("DeploymentList", [deployment]))

    data = collect_deployments(ctx)[0].data

    assert data["selector"] == {
        "match_labels": {},
        "match_expressions": [{"key": "tier", "operator": "In", "values": ["a", "b"]}],
    }


def test_replica_set_reports_owner_and_revision_only(ctx, kube):
    replica_set = {
        "metadata": metadata(
            "web-abc",
            annotations={
                "deployment.kubernetes.io/revision": "4",
                "note": SECRET_VALUE,
            },
            ownerReferences=[
                {"apiVersion": "apps/v1", "kind": "Deployment", "name": "web", "uid": "u"}
            ],
        ),
        "spec": {"replicas": 2, "selector": SELECTOR, "template": TEMPLATE},
        "status": {"replicas": 2, "readyReplicas": 1},
    }
    kube.routes[f"{APPS}/replicasets"] = (200, list_of("ReplicaSetList", [replica_set]))

    record = collect_replica_sets(ctx)[0]

    assert record.evidence_id == f"replicaset:{NAMESPACE}/web-abc"
    assert record.data["desired_replicas"] == 2
    assert record.data["ready_replicas"] == 1
    assert record.data["available_replicas"] == 0
    assert record.data["revision"] == "4"
    assert record.data["owner_references"] == [{"kind": "Deployment", "name": "web"}]
    assert SECRET_VALUE not in record.model_dump_json()


def test_standalone_replica_set_has_no_owner_or_revision(ctx, kube):
    replica_set = {
        "metadata": metadata("solo"),
        "spec": {"selector": SELECTOR},
        "status": {"replicas": 0},
    }
    kube.routes[f"{APPS}/replicasets"] = (200, list_of("ReplicaSetList", [replica_set]))

    data = collect_replica_sets(ctx)[0].data

    assert data["owner_references"] == []
    assert data["revision"] is None
