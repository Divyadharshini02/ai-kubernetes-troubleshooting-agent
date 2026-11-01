"""The provider client against real local sockets that send slowly, stall, or never finish.

Regression: read() waited for a full chunk, so the time limit was only checked between chunks,
and a server sending one byte at a time held a request with a 1 second limit for as long as it
kept sending. No test here contacts OpenRouter.
"""

import gzip
import json
import time

import pytest

from app.agent.openrouter import OpenRouterProvider
from app.agent.prompt import OUTPUT_SCHEMA
from app.agent.provider import ProviderError
from app.core.config import load_settings
from conftest import TEST_API_KEY
from llm_factory import HTTP_200, ScriptedProvider, completion, valid_content

TIME_LIMIT = 0.5
# Well above TIME_LIMIT to allow for a busy machine, and well below how long the servers keep sending.
ON_TIME = 3
SENDS_FOR_LONGER_THAN_THE_TEST = 1000000
FIXED_LENGTH = HTTP_200 + f"Content-Length: {SENDS_FOR_LONGER_THAN_THE_TEST}\r\n\r\n".encode()
CHUNKED = HTTP_200 + b"Transfer-Encoding: chunked\r\n\r\n"
COMPRESSED = HTTP_200 + f"Content-Encoding: gzip\r\nContent-Length: {SENDS_FOR_LONGER_THAN_THE_TEST}\r\n\r\n".encode()


@pytest.fixture
def scripted(monkeypatch):
    servers = []

    def start(first=b"", drip=b""):
        server = ScriptedProvider(first, drip)
        servers.append(server)
        monkeypatch.setenv("OPENROUTER_API_KEY", TEST_API_KEY)
        monkeypatch.setenv("OPENROUTER_MODEL", "test/model:free")
        monkeypatch.setenv("OPENROUTER_BASE_URL", server.url)
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", str(TIME_LIMIT))
        return server

    yield start
    for server in servers:
        server.stop()


def complete(budget=45):
    provider = OpenRouterProvider(load_settings())
    return provider.complete("system", "user", OUTPUT_SCHEMA, time.monotonic() + budget)


def timed_failure(budget=45):
    started = time.monotonic()
    with pytest.raises(ProviderError) as error:
        complete(budget)
    return error.value, time.monotonic() - started


@pytest.mark.parametrize(
    ("first", "drip"),
    [
        pytest.param(FIXED_LENGTH, b" ", id="fixed-length"),
        pytest.param(CHUNKED, b"1\r\n \r\n", id="chunked"),
        pytest.param(COMPRESSED, b"\x1f", id="compressed"),
    ],
)
def test_slow_body_stops_on_time_and_the_connection_is_closed(scripted, first, drip):
    server = scripted(first, drip)

    error, elapsed = timed_failure()

    assert error.code == "timeout"
    assert error.retryable is True
    assert elapsed < ON_TIME
    # The read itself stopped, rather than carrying on unseen after the caller gave up.
    assert server.disconnected.wait(ON_TIME)


@pytest.mark.parametrize(
    ("first", "drip"),
    [
        pytest.param(b"", b"", id="no-reply-at-all"),
        pytest.param(FIXED_LENGTH, b"", id="fixed-length-stalls-after-headers"),
        pytest.param(FIXED_LENGTH + b"{", b"", id="fixed-length-stalls-in-body"),
        pytest.param(CHUNKED, b"", id="chunked-stalls-after-headers"),
        pytest.param(CHUNKED + b"1\r\n{\r\n", b"", id="chunked-stalls-in-body"),
        pytest.param(b"HTTP/1.1 200 OK\r\nX-Slow: ", b"x", id="slow-headers"),
    ],
)
def test_stalled_or_slow_replies_stop_on_time(scripted, first, drip):
    scripted(first, drip)

    error, elapsed = timed_failure()

    assert error.code == "timeout"
    assert elapsed < ON_TIME


def test_remaining_budget_limits_the_attempt_when_shorter_than_the_timeout(scripted, monkeypatch):
    scripted(FIXED_LENGTH, b" ")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "30")

    error, elapsed = timed_failure(budget=TIME_LIMIT)

    assert error.code == "timeout"
    assert elapsed < ON_TIME


def test_oversized_reply_closes_the_connection(scripted):
    server = scripted(FIXED_LENGTH + b"x" * 70000, b" ")

    error, _ = timed_failure()

    assert error.code == "response_too_large"
    assert server.disconnected.wait(ON_TIME)


def as_chunks(body):
    pieces = [body[index : index + 50] for index in range(0, len(body), 50)]
    return b"".join(b"%x\r\n%s\r\n" % (len(piece), piece) for piece in pieces) + b"0\r\n\r\n"


def complete_body():
    return json.dumps(completion(valid_content())).encode()


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(HTTP_200 + f"Content-Length: {len(complete_body())}\r\n\r\n".encode() + complete_body(), id="fixed-length"),
        pytest.param(CHUNKED + as_chunks(complete_body()), id="chunked"),
    ],
)
def test_complete_replies_are_read_in_full(scripted, reply):
    scripted(reply)

    assert json.loads(complete()) == valid_content()


def test_compressed_reply_is_not_decompressed(scripted):
    body = gzip.compress(complete_body())
    scripted(HTTP_200 + f"Content-Encoding: gzip\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)

    error, _ = timed_failure()

    assert error.code == "malformed_response"
