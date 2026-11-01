from fastapi.testclient import TestClient

from app import __version__
from app.main import app

client = TestClient(app)


def test_health_reports_service_identity_and_version():
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-kubernetes-troubleshooting-agent",
        "version": __version__,
    }


def test_unknown_route_returns_404():
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
