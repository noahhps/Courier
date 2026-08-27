"""Session and message persistence.

The client holds no durable state, so everything the UI can show has to be
reconstructible from here.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from .db import Database


def _now() -> int:
    return int(time.time() * 1000)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


# Everything about an attachment except the bytes.
_META_COLUMNS = "id, message_id, kind, name, mime, size, created_at"


@dataclass
class StoredAttachment:
    id: str
    message_id: str
    kind: str  # image | text | document
    name: str
    mime: str
    size: int
    created_at: int
    # Both left out of the listing queries: a conversation's worth of image
    # bytes is megabytes and the UI only needs them one at a time, by id, while
    # the extracted text is for the model rather than the screen.
    data: bytes | None = None
    text: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "kind": self.kind,
            "name": self.name,
            "mime": self.mime,
            "size": self.size,
            "created_at": self.created_at,
        }


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
    # The working, when there was any. `skills` is JSON on the way in and out
    # of SQLite; `to_dict` is what the client sees, so it decodes there.
    reasoning: str | None = None
    skills: str | None = None

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
            "reasoning": self.reasoning,
            # Decoded here rather than in the client: the column is an
            # implementation detail of this table, and every caller wants the
            # list. A row written before migration 5, or by a turn that called
            # nothing, has no JSON to decode.
            "skills": json.loads(self.skills) if self.skills else [],
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
        reasoning: str | None = None,
        skills: list[dict] | None = None,
    ) -> None:
        self.db.execute(
            """
            UPDATE messages
               SET content = ?,
                   tokens = COALESCE(?, tokens),
                   model = COALESCE(?, model),
                   provider = COALESCE(?, provider),
                   reasoning = COALESCE(?, reasoning),
                   skills = COALESCE(?, skills)
             WHERE id = ?
            """,
            (
                content,
                tokens,
                model,
                provider,
                # Empty is stored as NULL rather than as "" or "[]": COALESCE
                # then leaves whatever was already there, so a retry that
                # produced no reasoning cannot erase the first attempt's.
                reasoning or None,
                json.dumps(skills) if skills else None,
                message_id,
            ),
        )

    def list_messages(self, session_id: str) -> list[StoredMessage]:
        rows = self.db.query(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, rowid",
            (session_id,),
        )
        return [StoredMessage(**dict(row)) for row in rows]

    def delete_message(self, message_id: str) -> None:
        self.db.execute("DELETE FROM messages WHERE id = ?", (message_id,))

    # -- attachments ------------------------------------------------------

    def add_attachment(
        self,
        message_id: str,
        *,
        kind: str,
        name: str,
        mime: str,
        data: bytes,
        text: str | None = None,
    ) -> StoredAttachment:
        attachment = StoredAttachment(
            id=_new_id("att"),
            message_id=message_id,
            kind=kind,
            name=name,
            mime=mime,
            size=len(data),
            created_at=_now(),
            text=text,
        )
        self.db.execute(
            """
            INSERT INTO attachments
                (id, message_id, kind, name, mime, size, data, text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attachment.id,
                attachment.message_id,
                attachment.kind,
                attachment.name,
                attachment.mime,
                attachment.size,
                data,
                attachment.text,
                attachment.created_at,
            ),
        )
        return attachment

    def get_attachment(self, attachment_id: str) -> StoredAttachment | None:
        """One file, bytes included. This is what serves an image to the UI."""
        row = self.db.query_one(
            f"SELECT {_META_COLUMNS}, data, text FROM attachments WHERE id = ?",
            (attachment_id,),
        )
        return StoredAttachment(**dict(row)) if row else None

    def attachments_for_session(
        self, session_id: str, *, with_data: bool = False
    ) -> dict[str, list[StoredAttachment]]:
        """Every file in a conversation, keyed by the message it belongs to.

        `with_data` is the difference between describing the files to the UI and
        handing the bytes to a model: the first happens on every session open,
        the second only while assembling a prompt.
        """
        columns = ", ".join(f"a.{c}" for c in _META_COLUMNS.split(", "))
        rows = self.db.query(
            f"""
            SELECT {columns}{', a.data, a.text' if with_data else ''}
              FROM attachments a
              JOIN messages m ON m.id = a.message_id
             WHERE m.session_id = ?
             ORDER BY a.created_at
            """,
            (session_id,),
        )
        grouped: dict[str, list[StoredAttachment]] = {}
        for row in rows:
            attachment = StoredAttachment(**dict(row))
            grouped.setdefault(attachment.message_id, []).append(attachment)
        return grouped
