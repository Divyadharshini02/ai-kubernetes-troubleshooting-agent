from app.collectors.serviceaccounts import collect_service_accounts
from stub_kube import NAMESPACE, list_of, metadata

ACCOUNTS = f"/api/v1/namespaces/{NAMESPACE}/serviceaccounts"


def test_service_account_reports_pull_secret_names_only(ctx, kube):
    account = {
        "metadata": metadata("default"),
        "imagePullSecrets": [{"name": "registry-login"}],
        "secrets": [{"name": "token-secret"}],
    }
    kube.routes[ACCOUNTS] = (200, list_of("ServiceAccountList", [account]))

    record = collect_service_accounts(ctx)[0]

    assert record.evidence_id == f"serviceaccount:{NAMESPACE}/default"
    assert record.temporality == "current"
    assert record.data == {"image_pull_secrets": ["registry-login"]}


def test_service_account_without_pull_secrets(ctx, kube):
    kube.routes[ACCOUNTS] = (200, list_of("ServiceAccountList", [{"metadata": metadata("default")}]))

    assert collect_service_accounts(ctx)[0].data == {"image_pull_secrets": []}
