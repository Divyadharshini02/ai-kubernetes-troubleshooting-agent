"""Application settings, read from environment variables, the repository's .env file and secret files.

Environment variables take priority over .env, which takes priority over a secret file. The API
key is held as a SecretStr, so it cannot appear in logs or error messages through normal formatting.
"""

import logging
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
# Docker Compose mounts secret files here. Each file is named after the setting it holds.
SECRETS_DIR = Path("/run/secrets")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
# OpenRouter marks zero-cost variants with this suffix. The same name without it can be a paid model.
FREE_MODEL_SUFFIX = ":free"


def is_free_model(model: str) -> bool:
    return model.endswith(FREE_MODEL_SUFFIX) and len(model) > len(FREE_MODEL_SUFFIX)


class Settings(BaseSettings):
    # utf-8-sig also accepts the byte order mark Windows PowerShell can write.
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8-sig", extra="ignore")

    llm_enabled: bool = False
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = Field(default="", pattern=r"^[A-Za-z0-9._\-/:]*$", max_length=200)
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Up to 120: a free reasoning model was measured taking more than 40 seconds for a root cause analysis.
    llm_timeout_seconds: float = Field(default=20, gt=0, le=120)
    llm_max_retries: int = Field(default=1, ge=0, le=3)
    # Local limits, below OpenRouter's free tier, so the quota cannot be used up by accident.
    llm_max_requests_per_minute: int = Field(default=10, ge=1, le=20)
    llm_max_requests_per_day: int = Field(default=40, ge=1, le=1000)

    @field_validator("openrouter_base_url")
    @classmethod
    def _require_safe_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.username or parsed.password or not parsed.hostname:
            raise ValueError("must be a plain URL without credentials")
        # The API key must never travel unencrypted to another machine.
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS):
            raise ValueError("must use https")
        return value.rstrip("/")


def load_settings() -> Settings | None:
    """Read settings, returning None if they are invalid or unreadable.

    Settings are optional, so nothing here may ever stop a request. A .env file saved as
    UTF-16, which Windows PowerShell's > operator can do, fails while it is being decoded.
    """
    try:
        return Settings(_secrets_dir=SECRETS_DIR if SECRETS_DIR.is_dir() else None)
    except ValidationError as exc:
        # Field names only: a rejected value could be a credential.
        fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors(include_input=False)]
        logger.warning("Invalid LLM settings in: %s", fields)
        return None
    except Exception as exc:
        # The type only: decoding errors quote the bytes of the file, which holds the key.
        logger.warning("Could not read LLM settings: %s", type(exc).__name__)
        return None
