import json
import time

import pytest
from kubernetes import client

from app.collectors.base import CollectorContext
from stub_kube import NAMESPACE, SECRET_VALUE, StubKube


@pytest.fixture
def kube():
    stub = StubKube()
    yield stub
    stub.stop()


@pytest.fixture
def ctx(kube):
    configuration = client.Configuration(host=kube.url)
    configuration.retries = False
    with client.ApiClient(configuration) as api_client:
        yield CollectorContext(
            api_client=api_client,
            namespace=NAMESPACE,
            deadline=time.monotonic() + 60,
        )


@pytest.fixture
def stub_kubeconfig(tmp_path, monkeypatch):
    """Point the backend at a temporary kubeconfig for the given API server URL."""

    def _use(server_url):
        path = tmp_path / "kubeconfig"
        path.write_text(
            json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Config",
                    "current-context": "stub-context",
                    "clusters": [{"name": "c", "cluster": {"server": server_url}}],
                    "users": [{"name": "u", "user": {"token": SECRET_VALUE}}],
                    "contexts": [{"name": "stub-context", "context": {"cluster": "c", "user": "u"}}],
                }
            )
        )
        monkeypatch.setenv("KUBECONFIG", str(path))

    return _use
