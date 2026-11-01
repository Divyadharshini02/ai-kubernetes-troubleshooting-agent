from app.collectors.storage import collect_persistent_volume_claims, collect_storage_classes
from stub_kube import NAMESPACE, SECRET_VALUE, list_of, metadata

CLAIMS = f"/api/v1/namespaces/{NAMESPACE}/persistentvolumeclaims"
CLASSES = "/apis/storage.k8s.io/v1/storageclasses"


def test_pending_claim_reports_binding_evidence(ctx, kube):
    claim = {
        "metadata": metadata("data"),
        "spec": {
            "storageClassName": "fast",
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "1Gi"}},
        },
        "status": {"phase": "Pending"},
    }
    kube.routes[CLAIMS] = (200, list_of("PersistentVolumeClaimList", [claim]))

    record = collect_persistent_volume_claims(ctx)[0]

    assert record.evidence_id == f"pvc:{NAMESPACE}/data"
    assert record.temporality == "current"
    assert record.data["phase"] == "Pending"
    assert record.data["storage_class"] == "fast"
    assert record.data["volume_name"] is None
    assert record.data["access_modes"] == ["ReadWriteOnce"]
    assert record.data["requested"] == {"storage": "1Gi"}


def test_claim_with_no_status_or_class_is_not_guessed(ctx, kube):
    claim = {"metadata": metadata("data"), "spec": {}}
    kube.routes[CLAIMS] = (200, list_of("PersistentVolumeClaimList", [claim]))

    data = collect_persistent_volume_claims(ctx)[0].data

    assert data["phase"] is None
    assert data["storage_class"] is None
    assert data["requested"] == {}


def test_storage_class_reports_provisioner_and_default(ctx, kube):
    default = {
        "metadata": {
            "name": "standard",
            "annotations": {
                "storageclass.kubernetes.io/is-default-class": "true",
                "note": SECRET_VALUE,
            },
        },
        "provisioner": "rancher.io/local-path",
        "volumeBindingMode": "WaitForFirstConsumer",
    }
    other = {"metadata": {"name": "slow"}, "provisioner": "example.com/slow"}
    kube.routes[CLASSES] = (200, list_of("StorageClassList", [default, other]))

    records = collect_storage_classes(ctx)

    assert records[0].evidence_id == "storageclass:standard"
    assert records[0].namespace is None
    assert records[0].data == {
        "provisioner": "rancher.io/local-path",
        "volume_binding_mode": "WaitForFirstConsumer",
        "is_default": True,
    }
    assert records[1].data["is_default"] is False
    assert SECRET_VALUE not in records[0].model_dump_json()
