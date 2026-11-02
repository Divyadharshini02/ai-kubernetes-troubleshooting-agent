"""SQLite storage for investigations. Each call opens its own connection, so any thread can call it."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.collectors.base import utc_now
from app.models.diagnosis import DiagnosticReport
from app.models.investigation import InvestigationDetail, InvestigationError, InvestigationSummary, ProgressEvent

DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "investigations.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    explain_requested INTEGER NOT NULL,
    verification_status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_seconds REAL,
    outcome TEXT,
    summary TEXT,
    diagnosis_count INTEGER,
    ai_status TEXT,
    ai_reason TEXT,
    error_code TEXT,
    error_message TEXT,
    report_json TEXT
);
CREATE INDEX IF NOT EXISTS investigations_by_start ON investigations (started_at);
CREATE TABLE IF NOT EXISTS progress_events (
    investigation_id TEXT NOT NULL REFERENCES investigations (id),
    sequence INTEGER NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    PRIMARY KEY (investigation_id, sequence)
);
"""

# Everything except the report, which can be large and is only needed for one investigation.
_SUMMARY_COLUMNS = (
    "id, namespace, status, explain_requested, verification_status, started_at, completed_at, "
    "duration_seconds, outcome, summary, diagnosis_count, ai_status, ai_reason, error_code, error_message"
)

INTERRUPTED_MESSAGE = "The backend stopped before this investigation finished."


class StorageError(Exception):
    """The database could not be used. It carries no database text."""


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """Open the database, and commit on success or roll back on any failure."""
    connection = None
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(DATABASE_PATH, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_SCHEMA)
        with connection:
            yield connection
    except (sqlite3.Error, OSError) as exc:
        raise StorageError(type(exc).__name__) from exc
    finally:
        if connection:
            connection.close()


def _add_event(connection: sqlite3.Connection, investigation_id: str, stage: str, message: str) -> None:
    connection.execute(
        "INSERT INTO progress_events (investigation_id, sequence, stage, message, occurred_at) "
        "SELECT ?, COALESCE(MAX(sequence), 0) + 1, ?, ?, ? FROM progress_events WHERE investigation_id = ?",
        (investigation_id, stage, message, utc_now().isoformat(), investigation_id),
    )


def _duration(connection: sqlite3.Connection, investigation_id: str, finished_at: datetime) -> float:
    row = connection.execute("SELECT started_at FROM investigations WHERE id = ?", (investigation_id,)).fetchone()
    return (finished_at - datetime.fromisoformat(row["started_at"])).total_seconds()


def _summary_fields(row: sqlite3.Row) -> dict:
    fields = {key: row[key] for key in row.keys() if key not in ("error_code", "error_message", "report_json")}
    fields["error"] = InvestigationError(code=row["error_code"], message=row["error_message"]) if row["error_code"] else None
    return fields


def create_investigation(investigation_id: str, namespace: str, explain: bool, message: str) -> InvestigationSummary:
    with _connection() as connection:
        connection.execute(
            "INSERT INTO investigations (id, namespace, status, explain_requested, verification_status, started_at) "
            "VALUES (?, ?, 'running', ?, 'not_verified', ?)",
            (investigation_id, namespace, explain, utc_now().isoformat()),
        )
        _add_event(connection, investigation_id, "created", message)
    return get_summary(investigation_id)


def add_event(investigation_id: str, stage: str, message: str) -> None:
    with _connection() as connection:
        _add_event(connection, investigation_id, stage, message)


def previous_outcome(investigation_id: str, namespace: str) -> str | None:
    """The outcome of the latest earlier conclusive investigation of this namespace.

    Failed, interrupted and inconclusive investigations show nothing about whether a problem
    was present, so they are skipped. Looking too soon after a fix does not lose the comparison.
    """
    with _connection() as connection:
        row = connection.execute(
            "SELECT outcome FROM investigations WHERE namespace = ? AND status = 'completed' "
            "AND outcome != 'insufficient_evidence' "
            "AND started_at < (SELECT started_at FROM investigations WHERE id = ?) "
            "ORDER BY started_at DESC LIMIT 1",
            (namespace, investigation_id),
        ).fetchone()
    return row["outcome"] if row else None


def complete_investigation(investigation_id: str, report: DiagnosticReport, verification_status: str) -> None:
    """Save the report and its final events together, so a reader never sees only some of them."""
    finished_at = utc_now()
    with _connection() as connection:
        connection.execute(
            "UPDATE investigations SET status = 'completed', completed_at = ?, duration_seconds = ?, "
            "verification_status = ?, outcome = ?, "
            "summary = ?, diagnosis_count = ?, ai_status = ?, ai_reason = ?, report_json = ? WHERE id = ?",
            (
                finished_at.isoformat(),
                _duration(connection, investigation_id, finished_at),
                verification_status,
                report.outcome,
                report.summary,
                len(report.diagnoses),
                report.llm.status if report.llm else None,
                report.llm.reason if report.llm else None,
                report.model_dump_json(),
                investigation_id,
            ),
        )
        _add_event(connection, investigation_id, "report_saved", "The report was saved.")
        _add_event(connection, investigation_id, "completed", "The investigation completed.")


def fail_investigation(investigation_id: str, code: str, message: str) -> None:
    finished_at = utc_now()
    with _connection() as connection:
        connection.execute(
            "UPDATE investigations SET status = 'failed', completed_at = ?, duration_seconds = ?, "
            "error_code = ?, error_message = ? WHERE id = ?",
            (finished_at.isoformat(), _duration(connection, investigation_id, finished_at), code, message, investigation_id),
        )
        _add_event(connection, investigation_id, "failed", message)


def mark_interrupted() -> int:
    """Fail anything still marked running. Called at startup, when nothing can really be running."""
    with _connection() as connection:
        running = [row["id"] for row in connection.execute("SELECT id FROM investigations WHERE status = 'running'")]
        finished_at = utc_now().isoformat()
        for investigation_id in running:
            # No duration: the time the backend stopped is unknown.
            connection.execute(
                "UPDATE investigations SET status = 'failed', completed_at = ?, error_code = 'interrupted', "
                "error_message = ? WHERE id = ?",
                (finished_at, INTERRUPTED_MESSAGE, investigation_id),
            )
            _add_event(connection, investigation_id, "failed", INTERRUPTED_MESSAGE)
    return len(running)


def get_summary(investigation_id: str) -> InvestigationSummary | None:
    with _connection() as connection:
        row = connection.execute(f"SELECT {_SUMMARY_COLUMNS} FROM investigations WHERE id = ?", (investigation_id,)).fetchone()
    return InvestigationSummary(**_summary_fields(row)) if row else None


def list_summaries(limit: int) -> list[InvestigationSummary]:
    with _connection() as connection:
        rows = connection.execute(
            f"SELECT {_SUMMARY_COLUMNS} FROM investigations ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [InvestigationSummary(**_summary_fields(row)) for row in rows]


def _events_after(connection: sqlite3.Connection, investigation_id: str, after: int) -> list[ProgressEvent]:
    rows = connection.execute(
        "SELECT sequence, stage, message, occurred_at FROM progress_events "
        "WHERE investigation_id = ? AND sequence > ? ORDER BY sequence",
        (investigation_id, after),
    ).fetchall()
    return [ProgressEvent(**dict(row)) for row in rows]


def get_detail(investigation_id: str) -> InvestigationDetail | None:
    with _connection() as connection:
        row = connection.execute("SELECT * FROM investigations WHERE id = ?", (investigation_id,)).fetchone()
        if row is None:
            return None
        events = _events_after(connection, investigation_id, 0)
    report = DiagnosticReport.model_validate_json(row["report_json"]) if row["report_json"] else None
    return InvestigationDetail(**_summary_fields(row), events=events, report=report)


def read_progress(investigation_id: str, after: int) -> tuple[str, list[ProgressEvent]]:
    """Return the status and newer events.

    The status is read first. Final events are written with the final status, so a finished
    status guarantees that the events read next include the last one.
    """
    with _connection() as connection:
        status = connection.execute("SELECT status FROM investigations WHERE id = ?", (investigation_id,)).fetchone()["status"]
        return status, _events_after(connection, investigation_id, after)
