"""Free models only, at zero cost, within a local request budget. No test contacts the real provider."""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.agent import explainer
from app.agent.budget import BUDGET, RequestBudget
from app.agent.openrouter import OpenRouterProvider
from app.agent.provider import ProviderError
from app.core import config
from app.core.config import is_free_model, load_settings
from app.main import app
from conftest import TEST_API_KEY
from llm_factory import COMPLETIONS_PATH, completion, valid_content
from stub_kube import NAMESPACE, container_status, list_of, make_pod, namespace_routes, pod_log_path
from test_cluster_health import VERSION

ROOT = Path(__file__).resolve().parents[2]
client = TestClient(app)
URL = f"/api/namespaces/{NAMESPACE}/diagnosis"
PAID_TWIN = "nvidia/nemotron-3.5-super-vl"


@pytest.fixture
def cluster(kube, stub_kubeconfig):
    """A stub cluster with one crashing pod, so there is one diagnosis to explain."""
    stub_kubeconfig(kube.url)
    pod = make_pod(
        "web",
        statuses=[container_status("app", ready=False, restarts=4, state={"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}})],
    )
    kube.routes.update(namespace_routes())
    kube.routes["/version"] = (200, VERSION)
    kube.routes["/api/v1/pods"] = (200, list_of("PodList", [pod]))
    kube.routes[f"/api/v1/namespaces/{NAMESPACE}/pods"] = (200, list_of("PodList", [pod]))
    kube.routes[pod_log_path("web")] = (200, b"2026-01-01T00:00:00Z fatal\n")
    return kube


def explain():
    response = client.get(URL, params={"explain": "true"})
    assert response.status_code == 200
    return response.json()["llm"]


def sent_bodies(llm):
    return [json.loads(body) for body in llm.bodies]


# Which model IDs count as free.


@pytest.mark.parametrize(
    ("model", "free"),
    [
        ("nvidia/nemotron-3.5-super-vl:free", True),
        ("test/model:free", True),
        (PAID_TWIN, False),
        ("openrouter/free", False),
        ("openrouter/auto", False),
        (":free", False),
        ("vendor/model:FREE", False),
        ("vendor/model:free:extended", False),
        ("vendor/model:free ", False),
        ("", False),
    ],
)
def test_only_an_explicit_free_suffix_counts_as_free(model, free):
    assert is_free_model(model) is free


# A paid model is refused before any request.


@pytest.mark.parametrize("model", [PAID_TWIN, "openrouter/auto", "openrouter/free"])
def test_a_paid_or_routed_model_is_refused_without_any_request(cluster, llm, monkeypatch, model):
    monkeypatch.setenv("OPENROUTER_MODEL", model)
    llm.routes[COMPLETIONS_PATH] = (200, completion(valid_content()))

    report = explain()

    assert llm.requests == []
    assert report["used"] is False
    assert report["reason"] == "model_not_free"
    assert "free" in report["message"]


def test_the_provider_itself_refuses_a_paid_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", PAID_TWIN)

    with pytest.raises(ProviderError) as raised:
        OpenRouterProvider(load_settings())

    assert raised.value.code == "model_not_free"
    assert raised.value.stop_all is True


# What is sent.


def test_the_request_asks_for_one_free_model_at_zero_price(cluster, llm):
    llm.routes[COMPLETIONS_PATH] = (200, completion(valid_content()))

    assert explain()["used"] is True

    [body] = sent_bodies(llm)
    assert body["model"] == "test/model:free"
    assert "models" not in body
    assert "route" not in body
    assert body["provider"] == {"require_parameters": True, "max_price": {"prompt": 0, "completion": 0}}
    assert body["usage"] == {"include": True}
    # Reasoning models think within max_tokens, so thinking is kept short and out of the response.
    assert body["reasoning"] == {"effort": "low", "exclude": True}
    assert body["max_tokens"] == 16000


def test_the_key_goes_only_in_the_authorization_header(cluster, llm):
    llm.routes[COMPLETIONS_PATH] = (200, completion(valid_content()))

    explain()

    assert llm.headers[0]["Authorization"] == f"Bearer {TEST_API_KEY}"
    assert TEST_API_KEY.encode() not in llm.bodies[0]


# What comes back.


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(completion(valid_content(), model=PAID_TWIN), id="paid-model"),
        pytest.param(completion(valid_content(), model="openrouter/auto"), id="routed-model"),
        pytest.param({**completion(valid_content()), "usage": {"cost": 0.0002}}, id="charged"),
        pytest.param({key: value for key, value in completion(valid_content()).items() if key != "model"}, id="model-missing"),
        pytest.param(completion(valid_content(), model=None), id="model-null"),
    ],
)
def test_an_answer_not_confirmed_as_free_is_never_used_or_retried(cluster, llm, monkeypatch, caplog, response):
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    llm.routes[COMPLETIONS_PATH] = (200, response)

    with caplog.at_level(logging.DEBUG):
        report = explain()

    assert len(llm.requests) == 1
    assert report["used"] is False
    assert report["reason"] == "non_free_model"
    assert report["explanations"][0]["content"] is None
    assert "keeps stopping" not in json.dumps(report)
    assert PAID_TWIN not in caplog.text


@pytest.mark.parametrize("usage", [{"cost": 0}, {"cost": 0.0}, {}, None, {"cost": "unknown"}])
def test_a_free_answer_reporting_no_charge_is_used(cluster, llm, usage):
    response = completion(valid_content())
    if usage is not None:
        response["usage"] = usage
    llm.routes[COMPLETIONS_PATH] = (200, response)

    assert explain()["used"] is True


# The local request budget.


def test_requests_stop_at_the_per_minute_limit(cluster, llm, monkeypatch):
    monkeypatch.setenv("LLM_MAX_REQUESTS_PER_MINUTE", "1")
    llm.routes[COMPLETIONS_PATH] = (200, completion(valid_content()))

    first = explain()
    second = explain()

    assert first["used"] is True
    assert second["reason"] == "budget_exhausted"
    assert len(llm.requests) == 1


def test_a_request_refused_by_the_budget_is_not_counted_as_sent(cluster, llm, monkeypatch):
    from prometheus_client import REGISTRY

    def sent():
        return REGISTRY.get_sample_value("troubleshooting_llm_request_duration_seconds_count") or 0

    monkeypatch.setenv("LLM_MAX_REQUESTS_PER_MINUTE", "1")
    llm.routes[COMPLETIONS_PATH] = (200, completion(valid_content()))
    before = sent()

    explain()
    explain()

    # One request reached the provider, so the metric used to check real calls must say one.
    assert len(llm.requests) == 1
    assert sent() == before + 1
    assert (REGISTRY.get_sample_value("troubleshooting_llm_request_errors_total", {"reason": "budget_exhausted"}) or 0) == 0


def test_retries_count_against_the_budget(cluster, llm, monkeypatch):
    monkeypatch.setenv("LLM_MAX_REQUESTS_PER_MINUTE", "2")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setattr(explainer, "RETRY_DELAY_SECONDS", 0)
    llm.routes[COMPLETIONS_PATH] = (500, {"error": {"code": 500, "message": "busy"}})

    report = explain()

    assert len(llm.requests) == 2
    assert report["reason"] == "budget_exhausted"


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


def test_the_minute_window_slides():
    clock = Clock(1_000_000.0)
    budget = RequestBudget(clock)

    assert [budget.try_acquire(2, 100) for _ in range(3)] == [True, True, False]
    clock.now += 59.9
    assert budget.try_acquire(2, 100) is False
    clock.now += 0.2
    assert budget.try_acquire(2, 100) is True


def test_the_daily_limit_resets_at_midnight_utc():
    just_before = datetime(2026, 11, 3, 23, 59, 0, tzinfo=timezone.utc).timestamp()
    clock = Clock(just_before)
    budget = RequestBudget(clock)

    assert [budget.try_acquire(20, 2) for _ in range(3)] == [True, True, False]
    clock.now += 61
    assert budget.try_acquire(20, 2) is True


def test_the_budget_is_safe_under_concurrent_use():
    budget = RequestBudget()
    results = []

    def take():
        results.append(budget.try_acquire(5, 100))

    threads = [threading.Thread(target=take) for _ in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 5


def test_a_refused_request_is_not_counted():
    budget = RequestBudget(Clock(time.time()))

    assert budget.try_acquire(1, 2) is True
    assert budget.try_acquire(1, 2) is False
    assert budget.try_acquire(1, 2) is False
    budget._clock.now += 61
    assert budget.try_acquire(1, 2) is True
    assert budget.try_acquire(5, 2) is False


def test_the_shared_budget_starts_unused_in_each_test():
    assert BUDGET.try_acquire(1, 1) is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_MAX_REQUESTS_PER_MINUTE", "0"),
        ("LLM_MAX_REQUESTS_PER_MINUTE", "21"),
        ("LLM_MAX_REQUESTS_PER_DAY", "0"),
        ("LLM_MAX_REQUESTS_PER_DAY", "1001"),
        ("LLM_MAX_REQUESTS_PER_DAY", "lots"),
    ],
)
def test_budget_settings_outside_their_range_are_invalid(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    assert load_settings() is None


@pytest.mark.parametrize(("value", "valid"), [("120", True), ("120.5", False), ("60", True)])
def test_the_timeout_allows_a_slow_free_model_but_stays_bounded(monkeypatch, value, valid):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", value)

    assert (load_settings() is not None) is valid


def test_budget_defaults_stay_below_the_free_tier(monkeypatch):
    for name in ("LLM_MAX_REQUESTS_PER_MINUTE", "LLM_MAX_REQUESTS_PER_DAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)

    settings = load_settings()

    assert settings.llm_max_requests_per_minute == 10
    assert settings.llm_max_requests_per_day == 40


# The key as a Docker Compose secret file.


def test_the_key_can_come_from_a_secret_file(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    (tmp_path / "openrouter_api_key").write_text("key-from-a-secret-file\n")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)

    settings = load_settings()

    assert settings.openrouter_api_key.get_secret_value() == "key-from-a-secret-file"
    assert "key-from-a-secret-file" not in repr(settings)


def test_an_environment_variable_still_takes_priority_over_the_secret_file(monkeypatch, tmp_path):
    (tmp_path / "openrouter_api_key").write_text("key-from-a-secret-file")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "key-from-the-environment")

    assert load_settings().openrouter_api_key.get_secret_value() == "key-from-the-environment"


def test_no_secret_directory_is_normal(monkeypatch, tmp_path, recwarn):
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "does-not-exist")

    assert load_settings() is not None
    assert not [warning for warning in recwarn if "does not exist" in str(warning.message)]


# Docker Compose delivery.


@pytest.fixture(scope="module")
def llm_compose():
    return yaml.safe_load((ROOT / "docker-compose.llm.yml").read_text(encoding="utf-8"))


def test_the_default_application_needs_no_key(llm_compose):
    default = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert "secrets" not in default["services"]["backend"]
    assert "OPENROUTER_API_KEY" not in json.dumps(default)


def test_the_llm_override_delivers_the_key_only_as_a_secret_file_outside_the_repository(llm_compose):
    backend = llm_compose["services"]["backend"]
    text = json.dumps(llm_compose)

    assert backend["secrets"] == ["openrouter_api_key"]
    assert "OPENROUTER_API_KEY" not in text
    assert llm_compose["secrets"]["openrouter_api_key"]["file"].startswith("${USERPROFILE")
    assert ".troubleshooting-agent-llm" in llm_compose["secrets"]["openrouter_api_key"]["file"]
    assert backend["environment"]["LLM_ENABLED"] == "${LLM_ENABLED:-false}"


def test_the_key_and_env_files_never_enter_the_image():
    ignored = (ROOT / "backend" / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "*" in ignored
    assert "**/.env*" in ignored
    assert not any(line.startswith("!") and "secret" in line for line in ignored)


def test_the_example_settings_use_a_free_model():
    example = dict(
        line.split("=", 1) for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#")
    )

    assert is_free_model(example["OPENROUTER_MODEL"])
    assert example["LLM_ENABLED"] == "false"
    assert int(example["LLM_MAX_REQUESTS_PER_DAY"]) <= 50
