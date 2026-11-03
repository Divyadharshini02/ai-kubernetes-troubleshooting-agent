"""Chat messages for an investigation, in the same SQLite database. Only redacted text is ever stored.

The table is created by the shared schema, which only adds what is missing, so an existing database
keeps all its investigations. A question and its reply are saved together, or not at all.
"""

import sqlite3

from app.collectors.base import utc_now
from app.models.chat import ChatAnswer, ChatExchange, ChatMessage
from app.storage.investigations import _connection

# Enough for a long troubleshooting session, while keeping each investigation's history bounded.
MAX_MESSAGES = 200


def _message(row: sqlite3.Row) -> ChatMessage:
    answer = ChatAnswer.model_validate_json(row["answer_json"]) if row["answer_json"] else None
    return ChatMessage(
        sequence=row["sequence"],
        role=row["role"],
        kind=row["kind"],
        text=row["text"],
        answer=answer,
        reason=row["reason"],
        created_at=row["created_at"],
    )


def list_messages(investigation_id: str) -> list[ChatMessage]:
    """All messages, oldest first."""
    with _connection() as connection:
        rows = connection.execute(
            "SELECT * FROM chat_messages WHERE investigation_id = ? ORDER BY sequence LIMIT ?", (investigation_id, MAX_MESSAGES)
        ).fetchall()
    return [_message(row) for row in rows]


def recent_messages(investigation_id: str, count: int) -> list[ChatMessage]:
    """The latest messages, oldest first."""
    with _connection() as connection:
        rows = connection.execute(
            "SELECT * FROM chat_messages WHERE investigation_id = ? ORDER BY sequence DESC LIMIT ?", (investigation_id, count)
        ).fetchall()
    return [_message(row) for row in reversed(rows)]


def count_messages(investigation_id: str) -> int:
    with _connection() as connection:
        return connection.execute("SELECT COUNT(*) FROM chat_messages WHERE investigation_id = ?", (investigation_id,)).fetchone()[0]


def _insert(connection: sqlite3.Connection, investigation_id: str, role: str, kind: str, text: str, answer, reason) -> None:
    connection.execute(
        "INSERT INTO chat_messages (investigation_id, sequence, role, kind, text, answer_json, reason, created_at) "
        "SELECT ?, COALESCE(MAX(sequence), 0) + 1, ?, ?, ?, ?, ?, ? FROM chat_messages WHERE investigation_id = ?",
        (
            investigation_id,
            role,
            kind,
            text,
            answer.model_dump_json() if answer else None,
            reason,
            utc_now().isoformat(),
            investigation_id,
        ),
    )


def add_exchange(
    investigation_id: str, question: str, kind: str, reply: str, answer: ChatAnswer | None = None, reason: str | None = None
) -> ChatExchange:
    """Save a question and its reply in one transaction."""
    with _connection() as connection:
        _insert(connection, investigation_id, "user", "question", question, None, None)
        _insert(connection, investigation_id, "assistant", kind, reply, answer, reason)
        rows = connection.execute(
            "SELECT * FROM chat_messages WHERE investigation_id = ? ORDER BY sequence DESC LIMIT 2", (investigation_id,)
        ).fetchall()
    reply_row, question_row = rows
    return ChatExchange(question=_message(question_row), reply=_message(reply_row))
