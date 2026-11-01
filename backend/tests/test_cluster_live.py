"""Tests against the real kind cluster. Run with: pytest -m cluster

These fail rather than skip when the cluster is down, so a missing cluster can
never look like a pass. kubectl is the independent source of expected values.
"""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.cluster

client = TestClient(app)


def kubectl_get(*args):
    result = subprocess.run(
        ["kubectl", "get", *args, "-o", "json"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture
def health():
    response = client.get("/api/cluster/health")
    assert response.status_code == 200, response.text
    return response.json()


def test_context_matches_kubectl(health):
    result = subprocess.run(
        ["kubectl", "config", "current-context"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    assert health["status"] == "connected"
    assert health["context"] == result.stdout.strip()
    assert health["truncated"] is False


def test_nodes_match_kubectl(health):
    expected = {
        node["metadata"]["name"]: node["status"]["nodeInfo"]["kubeletVersion"]
        for node in kubectl_get("nodes")["items"]
    }

    assert expected
    assert {n["name"]: n["kubelet_version"] for n in health["nodes"]} == expected
    assert health["server_version"] in expected.values()


def test_pods_match_kubectl(health):
    expected = {
        (pod["metadata"]["namespace"], pod["metadata"]["name"]): pod["status"]["phase"]
        for pod in kubectl_get("pods", "--all-namespaces")["items"]
    }

    assert expected
    assert {(p["namespace"], p["name"]): p["phase"] for p in health["pods"]} == expected


def test_sample_workload_is_reported_healthy(health):
    sample_pods = [p for p in health["pods"] if p["namespace"] == "sample-app"]

    assert len(sample_pods) == 1
    assert sample_pods[0]["name"].startswith("sample-web-")
    assert sample_pods[0]["phase"] == "Running"
    assert sample_pods[0]["ready_containers"] == sample_pods[0]["total_containers"] == 1
