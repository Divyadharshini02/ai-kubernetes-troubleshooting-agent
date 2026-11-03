"""OpenRouter chat completions client, using urllib3 with bounded time and size."""

import json
import logging
import queue
import threading
import time

import certifi
import urllib3
from urllib3.exceptions import HTTPError, NewConnectionError
from urllib3.exceptions import TimeoutError as Urllib3Timeout

from app.agent.budget import BUDGET
from app.agent.provider import ProviderError
from app.core.config import Settings, is_free_model

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 64 * 1024
# Reasoning models spend part of this budget thinking before they write the answer. 4,000 was
# found too small in real use: the root cause analysis was cut off before its JSON was complete.
MAX_OUTPUT_TOKENS = 16000
# Less thinking, and the thinking itself left out of the response, so it cannot use up the
# response size limit. Providers that do not support this are not used, because of require_parameters.
REASONING = {"effort": "low", "exclude": True}
_CHUNK_BYTES = 8192


def _error_for_status(status: int) -> ProviderError:
    if status == 401:
        return ProviderError("unauthorized", stop_all=True)
    if status == 402:
        return ProviderError("insufficient_credits", stop_all=True)
    if status == 403:
        return ProviderError("provider_refused")
    if status == 404:
        return ProviderError("model_not_found", stop_all=True)
    if status == 429:
        return ProviderError("rate_limited", stop_all=True)
    if status == 408 or status >= 500:
        return ProviderError("provider_error", retryable=True)
    return ProviderError("bad_request")


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, settings: Settings):
        # Checked here too, so no caller can reach a paid model by skipping a configuration check.
        if not is_free_model(settings.openrouter_model):
            raise ProviderError("model_not_free", stop_all=True)
        self.model = settings.openrouter_model
        self._url = f"{settings.openrouter_base_url}/chat/completions"
        self._api_key = settings.openrouter_api_key.get_secret_value()
        self._timeout = settings.llm_timeout_seconds
        self._per_minute = settings.llm_max_requests_per_minute
        self._per_day = settings.llm_max_requests_per_day
        self._http = urllib3.PoolManager(cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(), retries=False)

    def complete(self, system: str, user: str, schema: dict, deadline: float, schema_name: str = "diagnosis_explanation") -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("time_budget_exhausted", stop_all=True)
        if not BUDGET.try_acquire(self._per_minute, self._per_day):
            logger.warning("Local LLM request limit reached, so no request was sent")
            raise ProviderError("budget_exhausted", stop_all=True)
        timeout = min(self._timeout, remaining)
        body = {
            # One model only. A list of models would let OpenRouter fall back to another, possibly paid, one.
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            "provider": {
                # Only route to providers that support the schema above.
                "require_parameters": True,
                # Refuse any provider that would charge, in dollars per million tokens.
                "max_price": {"prompt": 0, "completion": 0},
            },
            # Asks OpenRouter to report the cost, so a charge would be detected rather than assumed away.
            "usage": {"include": True},
            "reasoning": REASONING,
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "stream": False,
        }
        raw, status = self._post(body, timeout)
        if status != 200:
            logger.warning("LLM provider returned HTTP %s", status)
            raise _error_for_status(status)
        return _extract_content(raw)

    def _post(self, body: dict, timeout: float) -> tuple[bytes, int]:
        """Send one request, returning within the time limit whatever the server does.

        Socket timeouts only limit the gap between bytes, at every stage, and a name lookup
        has none. So the request runs on its own thread, and the wait for it is limited.
        """
        results = queue.SimpleQueue()

        def attempt() -> None:
            try:
                results.put(self._send(body, timeout))
            except Exception as exc:
                results.put(exc)

        threading.Thread(target=attempt, name="llm-request", daemon=True).start()
        try:
            outcome = results.get(timeout=timeout)
        except queue.Empty:
            logger.warning("LLM request exceeded its time limit")
            raise ProviderError("timeout", retryable=True) from None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def _send(self, body: dict, timeout: float) -> tuple[bytes, int]:
        started = time.monotonic()
        try:
            response = self._http.request(
                "POST",
                self._url,
                body=json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    # Compressed data could stall or grow without the limits below seeing it.
                    "Accept-Encoding": "identity",
                },
                timeout=urllib3.Timeout(connect=min(CONNECT_TIMEOUT_SECONDS, timeout), read=timeout),
                # A redirect could carry the request, and its key, somewhere unexpected.
                redirect=False,
                retries=False,
                preload_content=False,
            )
            try:
                return _read_bounded(response, started + timeout), response.status
            finally:
                # Closed rather than reused, because a stopped read leaves unread data behind.
                response.close()
        # NewConnectionError is a subclass of the timeout error, so it must come first.
        except NewConnectionError as exc:
            raise self._network_error(exc) from exc
        except Urllib3Timeout as exc:
            logger.warning("LLM request timed out: %s", type(exc).__name__)
            raise ProviderError("timeout", retryable=True) from exc
        except HTTPError as exc:
            raise self._network_error(exc) from exc

    @staticmethod
    def _network_error(exc: Exception) -> ProviderError:
        logger.warning("LLM request failed: %s", type(exc).__name__)
        return ProviderError("network_error", retryable=True)


def _read_bounded(response, read_until: float) -> bytes:
    """Read a response, enforcing a size limit and a total time limit.

    read1 returns as soon as any data arrives, while read waits for a full chunk, so these
    checks also run for a server sending one byte at a time. Nothing is decompressed, so
    the size limit applies to what was actually received.
    """
    received = bytearray()
    while True:
        chunk = response.read1(_CHUNK_BYTES, decode_content=False)
        if not chunk:
            return bytes(received)
        received.extend(chunk)
        if len(received) > MAX_RESPONSE_BYTES:
            raise ProviderError("response_too_large")
        if time.monotonic() > read_until:
            raise ProviderError("timeout", retryable=True)


def _extract_content(raw: bytes) -> str:
    """Find the model's text. OpenRouter can report errors inside an HTTP 200 response."""
    if not raw.strip():
        raise ProviderError("empty_response", retryable=True)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ProviderError("malformed_response") from exc
    if not isinstance(payload, dict):
        raise ProviderError("malformed_response")
    _raise_for_embedded_error(payload.get("error"))

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("empty_response", retryable=True)
    choice = choices[0]
    _raise_for_embedded_error(choice.get("error"))

    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        raise ProviderError("response_truncated")
    if finish_reason == "content_filter":
        raise ProviderError("provider_refused")
    if finish_reason == "error":
        raise ProviderError("provider_error", retryable=True)

    message = choice.get("message")
    if not isinstance(message, dict):
        raise ProviderError("malformed_response")
    if message.get("refusal"):
        raise ProviderError("provider_refused")
    # No tools are offered, so a tool call means the response cannot be trusted.
    if message.get("tool_calls") or message.get("function_call"):
        raise ProviderError("malformed_response")
    content = message.get("content")
    if content is None or content == "":
        raise ProviderError("empty_response", retryable=True)
    if not isinstance(content, str):
        raise ProviderError("malformed_response")
    _require_free_response(payload)
    return content


def _require_free_response(payload: dict) -> None:
    """Use an answer only if the provider says it came from a free model, at no cost.

    The request already asks for this. Checking the response as well means a paid answer is
    never used, even if routing ever behaved differently. A missing model is not trusted either.
    """
    model = payload.get("model")
    usage = payload.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    charged = isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0
    if not isinstance(model, str) or not is_free_model(model) or charged:
        # No model name or amount: the response is untrusted, and this only needs to say what was refused.
        logger.warning("LLM response rejected: not confirmed as a free model at zero cost")
        raise ProviderError("non_free_model", stop_all=True)


def _raise_for_embedded_error(error) -> None:
    if not error:
        return
    code = error.get("code") if isinstance(error, dict) else None
    logger.warning("LLM provider reported an error in the response body: %s", code)
    raise _error_for_status(code if isinstance(code, int) else 500)
