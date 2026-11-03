import json
import os
import time

import pytest
from kubernetes import client

from app.agent.budget import BUDGET
from app.collectors.base import CollectorContext
from app.kubernetes.cluster import default_kubeconfig_allowed
from lab_harness import ADMIN_KUBECONFIG
from stub_kube import NAMESPACE, SECRET_VALUE, StubKube

TEST_API_KEY = "test-only-openrouter-key-value"


def pytest_runtest_setup(item):
    """Fail, not skip, so that missing credentials can never look like a pass."""
    if item.get_closest_marker("cluster") is None:
        return
    if not os.environ.get(ADMIN_KUBECONFIG):
        pytest.fail(f"Cluster tests need {ADMIN_KUBECONFIG}. See docs/M7_RBAC_SECURITY.md.", pytrace=False)
    if not os.environ.get("KUBECONFIG") and not default_kubeconfig_allowed():
        pytest.fail("Cluster tests need KUBECONFIG for the backend. See docs/M7_RBAC_SECURITY.md.", pytrace=False)


@pytest.fixture(autouse=True)
def no_real_llm_calls(monkeypatch):
    """Keep every test away from the real provider, even with a developer's .env present.

    Environment variables take priority over .env, and this address accepts no connections.
    """
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_MODEL", "")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://127.0.0.1:9/api/v1")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")
    # Each test starts with an unused local request budget, at the highest allowed rate.
    monkeypatch.setenv("LLM_MAX_REQUESTS_PER_MINUTE", "20")
    BUDGET.reset()


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


@pytest.fixture
def llm(monkeypatch):
    """A local stand-in for OpenRouter, with the LLM enabled and pointed at it."""
    stub = StubKube()
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("OPENROUTER_MODEL", "test/model:free")
    monkeypatch.setenv("OPENROUTER_BASE_URL", f"{stub.url}/api/v1")
    yield stub
    stub.stop()
