"""Session and message persistence.

The client holds no durable state, so everything the UI can show has to be
reconstructible from here.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from .db import Database


def _now() -> int:
    return int(time.time() * 1000)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


@dataclass
class StoredMessage:
    id: str
    session_id: str
    role: str
    content: str
    tokens: int | None
    model: str | None
    provider: str | None
    created_at: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "tokens": self.tokens,
            "model": self.model,
            "provider": self.provider,
            "created_at": self.created_at,
        }


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- sessions ---------------------------------------------------------

    def create_session(self, title: str | None = None) -> dict:
        session_id = _new_id("ses")
        now = _now()
        self.db.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        return {"id": session_id, "title": title, "created_at": now, "updated_at": now}

    def list_sessions(self, limit: int = 200) -> list[dict]:
        rows = self.db.query(
            """
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count
            FROM sessions s
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in rows]

    def get_session(self, session_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return dict(row) if row else None

    def rename_session(self, session_id: str, title: str) -> None:
        self.db.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )

    def delete_session(self, session_id: str) -> None:
        # ON DELETE CASCADE removes the messages; the FTS triggers follow.
        self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def touch_session(self, session_id: str) -> None:
        self.db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id)
        )

    # -- messages ---------------------------------------------------------

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        tokens: int | None = None,
        model: str | None = None,
        provider: str | None = None,
        message_id: str | None = None,
    ) -> StoredMessage:
        message = StoredMessage(
            id=message_id or _new_id("msg"),
            session_id=session_id,
            role=role,
            content=content,
            tokens=tokens,
            model=model,
            provider=provider,
            created_at=_now(),
        )
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO messages
                    (id, session_id, role, content, tokens, model, provider, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.tokens,
                    message.model,
                    message.provider,
                    message.created_at,
                ),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (message.created_at, session_id),
            )
        return message

    def update_message(
        self,
        message_id: str,
        content: str,
        *,
        tokens: int | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> None:
        self.db.execute(
            """
            UPDATE messages
               SET content = ?,
                   tokens = COALESCE(?, tokens),
                   model = COALESCE(?, model),
                   provider = COALESCE(?, provider)
             WHERE id = ?
            """,
            (content, tokens, model, provider, message_id),
        )

    def list_messages(self, session_id: str) -> list[StoredMessage]:
        rows = self.db.query(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, rowid",
            (session_id,),
        )
        return [StoredMessage(**dict(row)) for row in rows]

    def delete_message(self, message_id: str) -> None:
        self.db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
