"""The investigation workflow against the real kind cluster. Run with: pytest -m cluster"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import investigations as storage

pytestmark = pytest.mark.cluster

client = TestClient(app)
URL = "/api/investigations"


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "history.db")


def investigate(namespace):
    response = client.post(URL, json={"namespace": namespace})
    assert response.status_code == 202, response.text
    return client.get(f"{URL}/{response.json()['id']}").json()


def test_healthy_namespace_is_investigated_and_saved_with_real_evidence():
    investigation = investigate("sample-app")

    assert investigation["status"] == "completed"
    assert investigation["outcome"] == "no_problems_found"
    assert investigation["report"]["context"] == "kind-troubleshooting-lab"
    assert any(record["category"] == "pod" and record["status"] == "collected" for record in investigation["report"]["evidence"]["records"])
    assert client.get(URL).json()[0]["id"] == investigation["id"]


def test_missing_namespace_is_a_saved_failure():
    investigation = investigate("no-such-namespace")

    assert investigation["status"] == "failed"
    assert investigation["error"]["code"] == "namespace_not_found"
    assert [event["stage"] for event in investigation["events"]] == ["created", "connectivity_checked", "failed"]
