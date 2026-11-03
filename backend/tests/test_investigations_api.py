"""The investigation workflow, through real collectors, a stub cluster and a temporary database.

Background work finishes before TestClient returns, so these tests read final results directly.
"""

import json
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.agent import explainer
from app.api import investigations as api
from app.main import app
from app.services import investigations as service
from app.storage import investigations as storage
from conftest import TEST_API_KEY
from llm_factory import COMPLETIONS_PATH, completion, valid_content, valid_rca
from stub_kube import NAMESPACE, SECRET_VALUE, container_status, list_of, make_pod, namespace_routes, pod_log_path
from test_cluster_health import VERSION

client = TestClient(app)
URL = "/api/investigations"
PODS = f"/api/v1/namespaces/{NAMESPACE}/pods"
STAGES_WITHOUT_AI = ["created", "connectivity_checked", "evidence_collected", "rules_evaluated", "report_saved", "completed"]


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    path = tmp_path / "data" / "history.db"
    monkeypatch.setattr(storage, "DATABASE_PATH", path)
    return path


@pytest.fixture
def cluster(kube, stub_kubeconfig):
    """A stub cluster with one crashing pod, which sets a literal environment value and logs a password."""
    stub_kubeconfig(kube.url)
    pod = make_pod(
        "web",
        spec={"containers": [{"name": "app", "image": "registry.example/app:1", "env": [{"name": "SETTING", "value": "literal-env-value"}]}]},
        statuses=[container_status("app", ready=False, restarts=4, state={"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}})],
    )
    kube.routes.update(namespace_routes())
    kube.routes["/version"] = (200, VERSION)
    kube.routes["/api/v1/pods"] = (200, list_of("PodList", [pod]))
    kube.routes[PODS] = (200, list_of("PodList", [pod]))
    kube.routes[pod_log_path("web")] = (200, f"2026-01-01T00:00:00Z fatal password={SECRET_VALUE}\n".encode())
    return kube


def investigate(namespace=NAMESPACE, **fields):
    response = client.post(URL, json={"namespace": namespace, **fields})
    assert response.status_code == 202, response.text
    detail = client.get(f"{URL}/{response.json()['id']}")
    assert detail.status_code == 200
    return detail.json()


def parse_stream(text):
    """Split a Server-Sent Events body into (event name, id, data) messages, skipping comments."""
    messages = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if not line.startswith((":", "retry")))
        if fields:
            messages.append((fields["event"], fields.get("id"), json.loads(fields["data"])))
    return messages


def assert_failed(investigation, code):
    assert investigation["status"] == "failed"
    assert investigation["error"]["code"] == code
    assert investigation["report"] is None
    assert investigation["events"][-1]["stage"] == "failed"
    assert investigation["completed_at"]


# A completed investigation.


def test_investigation_saves_the_same_diagnosis_as_the_endpoint_and_its_evidence(cluster):
    expected = client.get(f"/api/namespaces/{NAMESPACE}/diagnosis").json()

    investigation = investigate()

    assert investigation["status"] == "completed"
    assert investigation["outcome"] == "problems_found"
    assert investigation["diagnosis_count"] == 1
    assert investigation["verification_status"] == "not_verified"
    assert investigation["duration_seconds"] >= 0
    report = investigation["report"]
    assert [d["diagnosis_id"] for d in report["diagnoses"]] == [d["diagnosis_id"] for d in expected["diagnoses"]]
    assert report["diagnoses"][0]["title"] == expected["diagnoses"][0]["title"]
    assert any(record["category"] == "logs" for record in report["evidence"]["records"])


def test_post_returns_at_once_with_a_running_record(cluster):
    body = client.post(URL, json={"namespace": NAMESPACE}).json()

    assert body["status"] == "running"
    assert body["completed_at"] is None


def test_no_explanation_means_no_llm_call(cluster, llm):
    investigation = investigate()

    assert llm.requests == []
    assert investigation["explain_requested"] is False
    assert investigation["ai_status"] is None
    assert investigation["report"]["llm"] is None


def test_diagnosis_endpoint_creates_no_record(cluster):
    assert client.get(f"/api/namespaces/{NAMESPACE}/diagnosis").status_code == 200

    assert client.get(URL).json() == []


# Progress.


def test_progress_events_arrive_in_order_and_then_the_stream_ends(cluster):
    investigation = investigate()

    response = client.get(f"{URL}/{investigation['id']}/events")

    assert response.headers["content-type"].startswith("text/event-stream")
    messages = parse_stream(response.text)
    progress = [message for message in messages if message[0] == "progress"]
    assert [data["stage"] for _, _, data in progress] == STAGES_WITHOUT_AI
    assert [int(event_id) for _, event_id, _ in progress] == list(range(1, len(STAGES_WITHOUT_AI) + 1))
    assert messages[-1] == ("end", None, {"status": "completed"})


def test_reconnecting_resumes_after_the_last_event_seen(cluster):
    investigation = investigate()

    response = client.get(f"{URL}/{investigation['id']}/events", headers={"Last-Event-ID": "4"})

    stages = [data["stage"] for name, _, data in parse_stream(response.text) if name == "progress"]
    assert stages == STAGES_WITHOUT_AI[4:]


def test_unusable_last_event_id_replays_from_the_start(cluster):
    investigation = investigate()

    response = client.get(f"{URL}/{investigation['id']}/events", headers={"Last-Event-ID": "not-a-number"})

    assert len([m for m in parse_stream(response.text) if m[0] == "progress"]) == len(STAGES_WITHOUT_AI)


def test_stream_delivers_events_as_a_running_investigation_records_them(monkeypatch):
    monkeypatch.setattr(api, "POLL_SECONDS", 0.01)
    storage.create_investigation("live", NAMESPACE, False, "Created.")

    def finish_later():
        time.sleep(0.1)
        storage.add_event("live", "connectivity_checked", "Connected.")
        time.sleep(0.1)
        storage.fail_investigation("live", "cluster_unreachable", "Gone.")

    worker = threading.Thread(target=finish_later)
    worker.start()
    response = client.get(f"{URL}/live/events")
    worker.join()

    messages = parse_stream(response.text)
    assert [data["stage"] for name, _, data in messages if name == "progress"] == ["created", "connectivity_checked", "failed"]
    assert messages[-1] == ("end", None, {"status": "failed"})


def test_stream_stops_at_its_limit_rather_than_following_forever(monkeypatch):
    monkeypatch.setattr(api, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(api, "STREAM_LIMIT_SECONDS", 0.05)
    storage.create_investigation("stuck", NAMESPACE, False, "Created.")

    messages = parse_stream(client.get(f"{URL}/stuck/events").text)

    assert messages[-1] == ("stream_limit", None, {"status": "running"})


# Requests that cannot start, and failures that are reported honestly.


@pytest.mark.parametrize("namespace", ["UPPER", "-bad", "bad-", "a" * 64, "../etc", "", "a_b"])
def test_invalid_namespace_is_rejected_without_a_record(kube, stub_kubeconfig, namespace):
    stub_kubeconfig(kube.url)

    response = client.post(URL, json={"namespace": namespace})

    assert response.status_code == 422
    assert client.get(URL).json() == []
    assert kube.requests == []


def test_unknown_field_is_rejected_without_a_record():
    assert client.post(URL, json={"namespace": NAMESPACE, "id": "chosen"}).status_code == 422
    assert client.get(URL).json() == []


def test_missing_namespace_is_a_saved_failure(cluster):
    cluster.routes[f"/api/v1/namespaces/{NAMESPACE}"] = (404, {"kind": "Status"})

    assert_failed(investigate(), "namespace_not_found")


def test_missing_kubeconfig_is_a_saved_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "missing"))

    assert_failed(investigate(), "kubeconfig_unavailable")


def test_unreachable_cluster_is_a_saved_failure(stub_kubeconfig):
    # This port accepts no connections: see the no_real_llm_calls fixture.
    stub_kubeconfig("http://127.0.0.1:9")

    assert_failed(investigate(), "cluster_unreachable")


def test_unexpected_defect_is_a_saved_failure_without_its_text(cluster, monkeypatch, caplog):
    def broken(_bundle):
        raise RuntimeError(f"defect {SECRET_VALUE}")

    monkeypatch.setattr(service, "diagnose", broken)

    with caplog.at_level(logging.DEBUG):
        investigation = investigate()

    assert_failed(investigation, "unexpected_error")
    assert SECRET_VALUE not in json.dumps(investigation)
    assert SECRET_VALUE not in caplog.text


@pytest.mark.parametrize("path", ["", "/events"])
def test_unknown_investigation_returns_404(path):
    response = client.get(f"{URL}/no-such-id{path}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "investigation_not_found"


def test_unusable_database_returns_503_and_the_diagnosis_still_works(cluster, monkeypatch, tmp_path):
    # A directory cannot be opened as a database.
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path)

    response = client.post(URL, json={"namespace": NAMESPACE})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert client.get(f"/api/namespaces/{NAMESPACE}/diagnosis").status_code == 200


# History across backend restarts.


def test_history_survives_a_restart_newest_first(cluster):
    first = investigate()
    second = investigate()

    with TestClient(app) as restarted:
        history = restarted.get(URL).json()
        detail = restarted.get(f"{URL}/{first['id']}").json()

    assert [item["id"] for item in history] == [second["id"], first["id"]]
    assert "report" not in history[0]
    assert detail["status"] == "completed"
    assert detail["report"]["diagnoses"] == first["report"]["diagnoses"]
    assert detail["events"] == first["events"]


def test_restart_marks_only_running_investigations_as_interrupted(cluster):
    finished = investigate()
    storage.create_investigation("cut-off", NAMESPACE, False, "Created.")

    with TestClient(app) as restarted:
        interrupted = restarted.get(f"{URL}/cut-off").json()
        unchanged = restarted.get(f"{URL}/{finished['id']}").json()
        stream = restarted.get(f"{URL}/cut-off/events")

    assert_failed(interrupted, "interrupted")
    assert interrupted["duration_seconds"] is None
    assert unchanged["status"] == "completed"
    assert parse_stream(stream.text)[-1] == ("end", None, {"status": "failed"})


def test_startup_survives_an_unusable_database(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path)

    with TestClient(app) as started:
        assert started.get("/api/health").status_code == 200


# The optional LLM.


def test_llm_failure_keeps_the_deterministic_diagnosis(cluster, llm, monkeypatch):
    monkeypatch.setattr(explainer, "RETRY_DELAY_SECONDS", 0)
    llm.routes[COMPLETIONS_PATH] = (500, {"error": {"code": 500, "message": "provider detail"}})

    investigation = investigate(explain=True)

    assert investigation["status"] == "completed"
    assert investigation["outcome"] == "problems_found"
    assert investigation["ai_status"] == "unavailable"
    assert investigation["ai_reason"] == "provider_error"
    assert investigation["report"]["diagnoses"]
    assert "provider detail" not in json.dumps(investigation)
    assert [event["stage"] for event in investigation["events"]] == [
        *STAGES_WITHOUT_AI[:4],
        "ai_requested",
        "ai_finished",
        *STAGES_WITHOUT_AI[4:],
    ]


def test_ai_analysis_is_saved_apart_from_an_identical_diagnosis(cluster, llm):
    without = investigate()
    cited = [reference["evidence_id"] for reference in without["report"]["diagnoses"][0]["evidence_references"]]
    llm.routes[COMPLETIONS_PATH] = (200, completion(valid_rca(cited)))

    explained = investigate(explain=True)

    assert explained["ai_status"] == "generated"
    assert explained["report"]["ai_analysis"]["status"] == "generated"
    # One analysis per investigation replaces the per-diagnosis explanations.
    assert explained["report"]["llm"] is None
    assert len(llm.requests) == 1
    assert [d["title"] for d in explained["report"]["diagnoses"]] == [d["title"] for d in without["report"]["diagnoses"]]


# Secrets.


def test_secrets_never_reach_the_database(cluster, llm, database):
    llm.routes[COMPLETIONS_PATH] = (200, completion(valid_content()))

    investigation = investigate(explain=True)

    assert investigation["status"] == "completed"
    saved = database.read_bytes()
    assert investigation["id"].encode() in saved
    # The kubeconfig token and the password in the logs share this value.
    assert SECRET_VALUE.encode() not in saved
    assert TEST_API_KEY.encode() not in saved
    assert b"literal-env-value" not in saved
    assert SECRET_VALUE not in client.get(f"{URL}/{investigation['id']}/events").text
