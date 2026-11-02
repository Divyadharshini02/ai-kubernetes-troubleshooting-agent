"""Metrics from real investigations against the real kind cluster, read only. Run with: pytest -m cluster"""

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.main import app
from app.storage import investigations as storage

pytestmark = pytest.mark.cluster

client = TestClient(app)
URL = "/api/investigations"
INVESTIGATIONS = "troubleshooting_investigations_total"
FAILURES = "troubleshooting_investigation_failures_total"


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "history.db")


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0


def investigate(namespace):
    response = client.post(URL, json={"namespace": namespace})
    assert response.status_code == 202, response.text
    return client.get(f"{URL}/{response.json()['id']}").json()


def test_a_real_healthy_investigation_is_counted_once_without_naming_anything():
    before = sample(INVESTIGATIONS, status="completed", outcome="no_problems_found")

    investigation = investigate("sample-app")

    assert investigation["outcome"] == "no_problems_found"
    assert sample(INVESTIGATIONS, status="completed", outcome="no_problems_found") == before + 1
    text = client.get("/metrics").text
    assert "sample-app" not in text
    assert "sample-web" not in text
    assert investigation["id"] not in text


def test_a_real_missing_namespace_is_counted_once_as_a_failure():
    before = sample(INVESTIGATIONS, status="failed", outcome="none"), sample(FAILURES, code="namespace_not_found")

    investigation = investigate("no-such-namespace")

    assert investigation["status"] == "failed"
    assert sample(INVESTIGATIONS, status="failed", outcome="none") == before[0] + 1
    assert sample(FAILURES, code="namespace_not_found") == before[1] + 1
