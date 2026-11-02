"""Records each HTTP request exactly once.

This is plain ASGI. BaseHTTPMiddleware can interfere with streaming responses, and the event stream
depends on them. Background tasks run inside the same call, so timing stops at the last response byte.
"""

import asyncio
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.metrics import record_http_request

METRICS_PATH = "/metrics"
UNMATCHED_ROUTE = "unmatched"
EVENT_STREAM_ROUTE = "/api/investigations/{investigation_id}/events"
API_PREFIX = "/api"
# The client left before any response started. A common convention, since nothing was actually sent.
CLIENT_CLOSED_STATUS = 499


def route_label(scope: Scope) -> str:
    """The route's template, never the requested path, which can contain anything."""
    template = getattr(scope.get("route"), "path", None)
    if template is None:
        return UNMATCHED_ROUTE
    # An included router's template leaves out the prefix it was included with.
    inside_api = scope["path"].startswith(f"{API_PREFIX}/") and not template.startswith(f"{API_PREFIX}/")
    return f"{API_PREFIX}{template}" if inside_api else template


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == METRICS_PATH:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = None
        recorded = False

        def record(final_status: int) -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            route = route_label(scope)
            record_http_request(
                scope["method"], route, final_status, time.perf_counter() - started, timed=route != EVENT_STREAM_ROUTE
            )

        async def send_and_record(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                record(status)

        try:
            await self.app(scope, receive, send_and_record)
        except asyncio.CancelledError:
            if status is None:
                status = CLIENT_CLOSED_STATUS
            raise
        finally:
            # Reached without a recording when a stream's client disconnects or the application fails.
            record(500 if status is None else status)
