import json
import time
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.models.investigation import InvestigationDetail, InvestigationRequest, InvestigationSummary
from app.services.investigations import run_investigation, start_investigation
from app.storage import investigations as storage

router = APIRouter(prefix="/investigations", tags=["investigations"])

POLL_SECONDS = 0.25
KEEP_ALIVE_SECONDS = 15
# Well above the time limits on collection and the LLM. A client that sees this reads the saved status.
STREAM_LIMIT_SECONDS = 300
_NOT_FOUND = {"code": "investigation_not_found", "message": "No investigation has this ID."}


@router.post("", response_model=InvestigationSummary, status_code=202)
def create_investigation(request: InvestigationRequest, background_tasks: BackgroundTasks) -> InvestigationSummary:
    """Start an investigation of one namespace. Follow it with the events route."""
    investigation = start_investigation(request.namespace, request.explain)
    background_tasks.add_task(run_investigation, investigation.id, request.namespace, request.explain)
    return investigation


@router.get("", response_model=list[InvestigationSummary])
def list_investigations(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[InvestigationSummary]:
    """Saved investigations, newest first."""
    return storage.list_summaries(limit)


@router.get("/{investigation_id}", response_model=InvestigationDetail)
def get_investigation(investigation_id: str) -> InvestigationDetail:
    investigation = storage.get_detail(investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return investigation


def _progress_stream(investigation_id: str, after: int) -> Iterator[str]:
    stream_until = time.monotonic() + STREAM_LIMIT_SECONDS
    next_keep_alive = time.monotonic() + KEEP_ALIVE_SECONDS
    yield "retry: 2000\n\n"
    while True:
        status, events = storage.read_progress(investigation_id, after)
        for event in events:
            # A browser sends the last id back when it reconnects, so nothing repeats or is lost.
            yield f"id: {event.sequence}\nevent: progress\ndata: {event.model_dump_json()}\n\n"
            after = event.sequence
        if status != "running":
            yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
            return
        if time.monotonic() > stream_until:
            yield f"event: stream_limit\ndata: {json.dumps({'status': status})}\n\n"
            return
        if time.monotonic() > next_keep_alive:
            yield ": keep-alive\n\n"
            next_keep_alive = time.monotonic() + KEEP_ALIVE_SECONDS
        time.sleep(POLL_SECONDS)


@router.get("/{investigation_id}/events")
def stream_investigation_events(
    investigation_id: str, last_event_id: Annotated[str | None, Header()] = None
) -> StreamingResponse:
    """Server-Sent Events for one investigation. Saved events are replayed, then new ones follow."""
    if storage.get_summary(investigation_id) is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    after = int(last_event_id) if last_event_id and last_event_id.isdecimal() else 0
    return StreamingResponse(
        _progress_stream(investigation_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
