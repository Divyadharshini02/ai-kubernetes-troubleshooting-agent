"""HTTP metrics: each request counted once, by route template, without disturbing streaming."""

import asyncio
import http.client
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.api import investigations as api
from app.main import app
from app.observability import metrics
from app.observability.middleware import EVENT_STREAM_ROUTE, MetricsMiddleware
from app.storage import investigations as storage

client = TestClient(app, raise_server_exceptions=False)
REQUESTS = "troubleshooting_http_requests_total"
DURATION_COUNT = "troubleshooting_http_request_duration_seconds_count"
DURATION_SUM = "troubleshooting_http_request_duration_seconds_sum"
HEALTH = "/api/health"
DETAIL_ROUTE = "/api/investigations/{investigation_id}"
HARNESS_ROUTE = "/harness/{name}"


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "history.db")


def count(route, status, method="GET"):
    return REGISTRY.get_sample_value(REQUESTS, {"method": method, "route": route, "status": str(status)}) or 0


def timed(route, method="GET"):
    return REGISTRY.get_sample_value(DURATION_COUNT, {"method": method, "route": route}) or 0


def scrape():
    return client.get("/metrics").text


# The endpoint.


def test_metrics_endpoint_uses_the_prometheus_format():
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "troubleshooting_investigations_total" in response.text


def test_scrapes_are_not_counted_and_the_endpoint_is_not_in_the_api_docs():
    client.get("/metrics")
    text = client.get("/metrics").text

    assert 'route="/metrics"' not in text
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]


def test_metrics_stay_outside_the_api_prefix_that_nginx_proxies():
    assert client.get("/api/metrics").status_code == 404


# Labels.


def test_a_request_is_counted_once_by_its_template_and_status():
    before = count(HEALTH, 200), timed(HEALTH)

    assert client.get(HEALTH).status_code == 200

    assert (count(HEALTH, 200), timed(HEALTH)) == (before[0] + 1, before[1] + 1)


def test_an_identifier_in_a_path_never_becomes_a_label():
    identifier = uuid.uuid4().hex
    before = count(DETAIL_ROUTE, 404)

    assert client.get(f"/api/investigations/{identifier}").status_code == 404

    assert count(DETAIL_ROUTE, 404) == before + 1
    assert identifier not in scrape()


def test_a_rejected_namespace_never_becomes_a_label():
    route = "/api/namespaces/{namespace}/diagnosis"
    before = count(route, 422)

    assert client.get("/api/namespaces/NOT-VALID-NAME/diagnosis").status_code == 422

    assert count(route, 422) == before + 1
    assert "NOT-VALID-NAME" not in scrape()


def test_random_paths_cannot_create_new_series():
    client.get("/seed-the-unmatched-series")
    before = len(scrape().splitlines())

    for _ in range(1000):
        assert client.get(f"/{uuid.uuid4().hex}").status_code == 404

    assert len(scrape().splitlines()) == before


def test_an_unknown_method_cannot_create_a_new_series():
    client.request("BREW", "/coffee")
    text = scrape()

    assert "BREW" not in text
    assert count("unmatched", 404, "OTHER") >= 1


def test_every_route_with_a_path_parameter_is_labelled_as_a_template():
    for path in ("/api/namespaces/some-name/evidence", "/api/investigations/some-id/events"):
        client.get(path)

    text = scrape()

    assert "some-name" not in text
    assert "some-id" not in text
    assert 'route="/api/namespaces/{namespace}/evidence"' in text
    assert f'route="{EVENT_STREAM_ROUTE}"' in text


# Timing and background work.


def test_post_timing_stops_before_the_background_investigation(monkeypatch):
    route = "/api/investigations"
    finished = []

    def slow_investigation(*_args):
        time.sleep(0.6)
        finished.append(True)

    monkeypatch.setattr(api, "run_investigation", slow_investigation)
    sum_before = REGISTRY.get_sample_value(DURATION_SUM, {"method": "POST", "route": route}) or 0
    count_before = count(route, 202, "POST")

    assert client.post(route, json={"namespace": "some-namespace"}).status_code == 202

    assert finished == [True]
    assert count(route, 202, "POST") == count_before + 1
    assert REGISTRY.get_sample_value(DURATION_SUM, {"method": "POST", "route": route}) - sum_before < 0.5


# The event stream, through the real application.


def test_a_finished_stream_is_counted_once_and_not_timed():
    investigation = storage.create_investigation(uuid.uuid4().hex, "some-namespace", False, "Created.")
    storage.fail_investigation(investigation.id, "unexpected_error", "Stopped.")
    before = count(EVENT_STREAM_ROUTE, 200), timed(EVENT_STREAM_ROUTE)

    response = client.get(f"/api/investigations/{investigation.id}/events")

    assert "event: end" in response.text
    assert (count(EVENT_STREAM_ROUTE, 200), timed(EVENT_STREAM_ROUTE)) == (before[0] + 1, before[1])


def test_a_client_disconnecting_from_a_real_stream_is_counted_once(monkeypatch):
    monkeypatch.setattr(api, "POLL_SECONDS", 0.02)
    investigation = storage.create_investigation(uuid.uuid4().hex, "some-namespace", False, "Created.")
    before = count(EVENT_STREAM_ROUTE, 200)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", f"/api/investigations/{investigation.id}/events")
        response = connection.getresponse()
        assert response.status == 200
        # Bytes arriving while the investigation is still running show that streaming still works.
        assert response.read1(1024)
        assert count(EVENT_STREAM_ROUTE, 200) == before
        connection.close()

        deadline = time.monotonic() + 10
        while count(EVENT_STREAM_ROUTE, 200) == before and time.monotonic() < deadline:
            time.sleep(0.02)
        assert count(EVENT_STREAM_ROUTE, 200) == before + 1
        time.sleep(0.2)
        assert count(EVENT_STREAM_ROUTE, 200) == before + 1
    finally:
        storage.fail_investigation(investigation.id, "unexpected_error", "Stopped.")
        server.should_exit = True
        thread.join(10)


# Exact behaviour, with a minimal application standing in for FastAPI.


def run_middleware(application, name):
    """Run one request and return its counted statuses, with the error it raised if any."""
    scope = {"type": "http", "method": "GET", "path": f"/harness/{name}", "route": SimpleNamespace(path=HARNESS_ROUTE)}
    sent = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    raised = None
    try:
        asyncio.run(MetricsMiddleware(application)(scope, receive, send))
    except BaseException as exc:
        raised = type(exc)
    return sent, raised


def harness_count(status):
    return count(HARNESS_ROUTE, status)


def test_a_normal_response_followed_by_background_work_is_counted_once():
    before = harness_count(200)

    async def application(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})
        assert harness_count(200) == before + 1

    sent, raised = run_middleware(application, "normal")

    assert raised is None
    assert len(sent) == 2
    assert harness_count(200) == before + 1


def test_a_stream_ending_without_its_final_message_is_counted_once():
    before = harness_count(206)

    async def application(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 206, "headers": []})
        await send({"type": "http.response.body", "body": b"part", "more_body": True})

    run_middleware(application, "disconnected")

    assert harness_count(206) == before + 1


def test_cancellation_during_a_stream_keeps_its_status_and_is_counted_once():
    before = harness_count(207)

    async def application(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 207, "headers": []})
        await send({"type": "http.response.body", "body": b"part", "more_body": True})
        raise asyncio.CancelledError

    _sent, raised = run_middleware(application, "cancelled")

    assert raised is asyncio.CancelledError
    assert harness_count(207) == before + 1


def test_cancellation_before_any_response_is_not_reported_as_a_server_error():
    before = harness_count(499), harness_count(500)

    async def application(_scope, _receive, _send):
        raise asyncio.CancelledError

    run_middleware(application, "gone")

    assert (harness_count(499), harness_count(500)) == (before[0] + 1, before[1])


def test_an_application_error_is_a_500_counted_once_and_still_raised():
    before = harness_count(500)

    async def application(_scope, _receive, _send):
        raise ValueError

    _sent, raised = run_middleware(application, "broken")

    assert raised is ValueError
    assert harness_count(500) == before + 1


def test_a_response_is_passed_on_unchanged_when_recording_fails(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError

    monkeypatch.setattr(metrics.HTTP_REQUESTS, "labels", broken)

    response = client.get(HEALTH)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_non_http_traffic_passes_through_untouched():
    seen = []

    async def application(scope, _receive, _send):
        seen.append(scope["type"])

    asyncio.run(MetricsMiddleware(application)({"type": "lifespan"}, None, None))

    assert seen == ["lifespan"]
