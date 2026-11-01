import logging

import pytest

from app.core import config
from app.core.config import load_settings


def test_defaults_keep_the_llm_off(monkeypatch):
    for name in ("LLM_ENABLED", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL", "LLM_TIMEOUT_SECONDS", "LLM_MAX_RETRIES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)

    settings = load_settings()

    assert settings.llm_enabled is False
    assert settings.openrouter_api_key.get_secret_value() == ""
    assert settings.openrouter_model == ""
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_timeout_seconds == 20
    assert settings.llm_max_retries == 1


def test_values_are_read_from_a_dotenv_file_with_a_windows_byte_order_mark(monkeypatch, tmp_path):
    for name in ("LLM_ENABLED", "OPENROUTER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"\xef\xbb\xbfLLM_ENABLED=true\nOPENROUTER_MODEL=from/dotenv:free\n")
    monkeypatch.setitem(config.Settings.model_config, "env_file", env_file)

    settings = load_settings()

    assert settings.llm_enabled is True
    assert settings.openrouter_model == "from/dotenv:free"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("OPENROUTER_API_KEY=key-inside-the-file\n".encode("utf-16"), id="utf-16"),
        pytest.param(b"OPENROUTER_API_KEY=key-inside-the-file\n\xff\xfe\xfa\n", id="invalid-bytes"),
    ],
)
def test_unreadable_dotenv_never_crashes_and_never_logs_its_contents(monkeypatch, tmp_path, caplog, content):
    env_file = tmp_path / ".env"
    env_file.write_bytes(content)
    monkeypatch.setitem(config.Settings.model_config, "env_file", env_file)

    with caplog.at_level(logging.DEBUG):
        settings = load_settings()

    assert settings is None
    assert "UnicodeDecodeError" in caplog.text
    assert "key-inside-the-file" not in caplog.text
    # Decoding errors quote raw bytes, so the exception text itself must not be logged.
    assert "codec can't decode" not in caplog.text


def test_environment_variable_overrides_dotenv(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_MODEL=from/dotenv:free\n")
    monkeypatch.setitem(config.Settings.model_config, "env_file", env_file)
    monkeypatch.setenv("OPENROUTER_MODEL", "from/environment:free")

    assert load_settings().openrouter_model == "from/environment:free"


def test_api_key_is_hidden_when_settings_are_formatted(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "real-looking-secret")

    settings = load_settings()

    assert "real-looking-secret" not in repr(settings)
    assert "real-looking-secret" not in str(settings)
    assert "real-looking-secret" not in settings.model_dump_json()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_ENABLED", "not-a-boolean"),
        ("LLM_TIMEOUT_SECONDS", "0"),
        ("LLM_TIMEOUT_SECONDS", "-1"),
        ("LLM_TIMEOUT_SECONDS", "9999"),
        ("LLM_TIMEOUT_SECONDS", "soon"),
        ("LLM_MAX_RETRIES", "-1"),
        ("LLM_MAX_RETRIES", "100"),
        ("OPENROUTER_MODEL", "bad model name; rm -rf"),
        ("OPENROUTER_BASE_URL", "http://openrouter.ai/api/v1"),
        ("OPENROUTER_BASE_URL", "ftp://openrouter.ai"),
        ("OPENROUTER_BASE_URL", "https://user:pass@openrouter.ai/api/v1"),
        ("OPENROUTER_BASE_URL", "not a url"),
    ],
)
def test_invalid_settings_never_crash(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    assert load_settings() is None


def test_invalid_settings_log_names_but_not_values(monkeypatch, caplog):
    monkeypatch.setenv("OPENROUTER_MODEL", "secret-looking value!")

    with caplog.at_level(logging.DEBUG):
        load_settings()

    assert "openrouter_model" in caplog.text
    assert "secret-looking" not in caplog.text


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/v1", "http://localhost:8000/v1", "https://example.com/v1"])
def test_https_or_local_base_urls_are_accepted(monkeypatch, url):
    monkeypatch.setenv("OPENROUTER_BASE_URL", url)

    assert load_settings().openrouter_base_url == url


def test_trailing_slash_is_removed(monkeypatch):
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.com/v1/")

    assert load_settings().openrouter_base_url == "https://example.com/v1"
