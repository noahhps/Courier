"""Session and message persistence.

The client holds no durable state, so everything the UI can show has to be
reconstructible from here.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass

from .db import Database
from .situation import Situation


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


# An inferred fact nobody has pinned, used, or seen recently stops being worth
# a place in every prompt. Thirty days is long enough that a fact about a
# months-long situation survives a quiet fortnight.
FADE_AFTER_MS = 30 * 24 * 60 * 60 * 1000


@dataclass
class StoredChunk:
    """A retrievable piece of a message or of an attachment's text."""

    id: str
    message_id: str | None
    attachment_id: str | None
    ordinal: int
    page: int | None
    content: str
    created_at: int
    embedding: bytes | None = None
    dims: int | None = None
    model: str | None = None


@dataclass
class StoredFact:
    id: str
    text: str
    category: str | None
    source: str  # told | inferred
    confidence: float
    pinned: int
    status: str  # active | pending
    message_id: str | None
    used_count: int
    last_used_at: int | None
    created_at: int
    updated_at: int

    def fading(self, *, now: int) -> bool:
        """Whether this is on its way out.

        Derived rather than stored: a fact becomes stale by the passage of
        time, and a status column would need a scheduler to keep it honest.
        Anything told, pinned, or actually used stays put.
        """
        if self.pinned or self.source == "told" or self.used_count >= 3:
            return False
        return (self.last_used_at or self.created_at) < now - FADE_AFTER_MS

    def to_dict(self, *, now: int | None = None) -> dict:
        now = now if now is not None else _now()
        return {
            "id": self.id,
            "text": self.text,
            "category": self.category,
            "source": self.source,
            "confidence": self.confidence,
            "pinned": bool(self.pinned),
            "status": self.status,
            "used_count": self.used_count,
            "last_used_at": self.last_used_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            # Computed here so the page and the prompt agree on what is fading
            # without each re-deriving the rule.
            "fading": self.fading(now=now),
        }


@dataclass
class StoredEvent:
    id: str
    title: str
    starts_at: str
    ends_at: str | None
    all_day: int
    notes: str | None
    tz: str | None
    session_id: str | None
    created_at: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            # int in SQLite, bool everywhere it is read.
            "all_day": bool(self.all_day),
            "notes": self.notes,
            "tz": self.tz,
            "session_id": self.session_id,
            "created_at": self.created_at,
        }


@dataclass
class StoredMCPServer:
    id: str
    name: str
    transport: str  # stdio | sse | http | websocket
    command: str | None = None
    args: str | None = None  # JSON array
    env: str | None = None  # JSON object
    cwd: str | None = None
    url: str | None = None
    headers: str | None = None  # JSON object
    auto_approve: str | None = None  # JSON array
    enabled: int = 1
    description: str | None = None
    created_at: int = 0
    updated_at: int = 0
    homepage: str | None = None  # where to look for this service's logo

    def parsed_args(self) -> list[str]:
        if not self.args:
            return []
        try:
            val = json.loads(self.args)
            return [str(x) for x in val] if isinstance(val, list) else []
        except Exception:
            return []

    def parsed_env(self) -> dict[str, str]:
        if not self.env:
            return {}
        try:
            val = json.loads(self.env)
            return {str(k): str(v) for k, v in val.items()} if isinstance(val, dict) else {}
        except Exception:
            return {}

    def parsed_headers(self) -> dict[str, str]:
        if not self.headers:
            return {}
        try:
            val = json.loads(self.headers)
            return {str(k): str(v) for k, v in val.items()} if isinstance(val, dict) else {}
        except Exception:
            return {}

    def parsed_auto_approve(self) -> list[str]:
        if not self.auto_approve:
            return []
        try:
            val = json.loads(self.auto_approve)
            return [str(x) for x in val] if isinstance(val, list) else []
        except Exception:
            return []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": self.parsed_args(),
            "env": self.parsed_env(),
            "cwd": self.cwd,
            "url": self.url,
            "headers": self.parsed_headers(),
            "auto_approve": self.parsed_auto_approve(),
            "enabled": bool(self.enabled),
            "description": self.description,
            "homepage": self.homepage,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- sessions ---------------------------------------------------------

    def create_session(
        self, title: str | None = None, situation: Situation | None = None
    ) -> dict:
        session_id = _new_id("ses")
        now = _now()
        where = (situation or Situation()).to_row()
        self.db.execute(
            """
            INSERT INTO sessions
                   (id, title, created_at, updated_at, tz, locale, utc_offset, region)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                title,
                now,
                now,
                where["tz"],
                where["locale"],
                where["utc_offset"],
                where["region"],
            ),
        )
        return {
            "id": session_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "theme": None,
            **where,
        }

    def session_situation(self, session_id: str) -> Situation:
        """Where and when this conversation started. Empty if it never said."""
        row = self.db.query_one(
            "SELECT tz, locale, utc_offset, region FROM sessions WHERE id = ?",
            (session_id,),
        )
        return Situation.from_row(dict(row) if row else None)

    def set_session_situation(self, session_id: str, situation: Situation) -> bool:
        """Record it, but only the first time. False if it was already known.

        First write wins on purpose. The client sends this with every message
        so that a session created by an older build still gets filled in, but
        a conversation whose stated time and place changed underneath it would
        contradict what the model was told on turn one -- and would invalidate
        the prompt cache on every turn it changed.
        """
        if not situation.known:
            return False
        current = self.session_situation(session_id)
        if current.known:
            return False
        where = situation.to_row()
        self.db.execute(
            "UPDATE sessions SET tz = ?, locale = ?, utc_offset = ?, region = ? "
            "WHERE id = ?",
            (
                where["tz"],
                where["locale"],
                where["utc_offset"],
                where["region"],
                session_id,
            ),
        )
        return True

    def list_sessions(self, limit: int = 200) -> list[dict]:
        rows = self.db.query(
            """
            SELECT s.id, s.title, s.created_at, s.updated_at, s.project_id, s.theme,
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

    # -- chunks -----------------------------------------------------------

    def add_chunks(
        self,
        pieces,
        *,
        message_id: str | None = None,
        attachment_id: str | None = None,
    ) -> int:
        """Store the pieces one message or attachment was split into.

        One transaction, because a half-chunked message is indistinguishable
        from an unchunked one to `messages_needing_chunks` -- it would be
        picked up again and chunked a second time, leaving duplicates that
        both match every search.
        """
        now = _now()
        rows = [
            (
                _new_id("chk"),
                message_id,
                attachment_id,
                piece.ordinal,
                piece.page,
                piece.content,
                now,
            )
            for piece in pieces
        ]
        if not rows:
            return 0
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO chunks
                    (id, message_id, attachment_id, ordinal, page, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def chunks_pending_embedding(self, limit: int = 128) -> list[StoredChunk]:
        """What still needs a vector. The query `idx_chunks_pending` is for.

        The WHERE clause matches that partial index's predicate exactly; write
        it any other way and SQLite falls back to a full scan of a table that
        is mostly embedded rows.
        """
        rows = self.db.query(
            "SELECT * FROM chunks WHERE embedding IS NULL ORDER BY created_at LIMIT ?",
            (limit,),
        )
        return [StoredChunk(**dict(row)) for row in rows]

    def set_embeddings(self, updates: list[tuple[str, bytes, int, str]]) -> None:
        """Attach vectors to chunks. `updates` is (id, blob, dims, model)."""
        if not updates:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                "UPDATE chunks SET embedding = ?, dims = ?, model = ? WHERE id = ?",
                [(blob, dims, model, cid) for cid, blob, dims, model in updates],
            )

    def embedded_chunks(self, model: str) -> list[StoredChunk]:
        """Every vector that came from one encoder.

        Filtered on `model` because vectors from different encoders are not
        comparable -- mixing them does not raise, it just returns nonsense
        rankings, which is far harder to notice.
        """
        rows = self.db.query(
            """
            SELECT id, message_id, attachment_id, ordinal, page, content,
                   created_at, embedding, dims, model
              FROM chunks
             WHERE embedding IS NOT NULL AND model = ?
             ORDER BY rowid
            """,
            (model,),
        )
        return [StoredChunk(**dict(row)) for row in rows]

    def search_chunks_fts(self, query: str, limit: int = 20) -> list[tuple[str, float]]:
        """Keyword hits over chunks, best first, as (chunk_id, bm25).

        Returns [] rather than raising on a query with nothing searchable in
        it: an empty result is something the caller can report, while an
        exception here would take down a turn over a punctuation mark.
        """
        match = _fts_query(query)
        if not match:
            return []
        rows = self.db.query(
            """
            SELECT c.id AS id, bm25(chunks_fts) AS score
              FROM chunks_fts f
              JOIN chunks c ON c.rowid = f.rowid
             WHERE chunks_fts MATCH ?
             ORDER BY score
             LIMIT ?
            """,
            (match, limit),
        )
        return [(row["id"], row["score"]) for row in rows]

    def chunks_by_id(self, ids: list[str]) -> dict[str, StoredChunk]:
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self.db.query(
            f"""
            SELECT id, message_id, attachment_id, ordinal, page, content,
                   created_at, NULL AS embedding, dims, model
              FROM chunks WHERE id IN ({marks})
            """,
            tuple(ids),
        )
        return {row["id"]: StoredChunk(**dict(row)) for row in rows}

    def chunk_sources(self, ids: list[str]) -> dict[str, dict]:
        """Where each chunk came from, for citing it back.

        One query rather than one per hit: a search returns a handful of
        chunks from a handful of conversations, and the alternative is a
        round trip per result purely to learn a date.
        """
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self.db.query(
            f"""
            SELECT c.id AS id,
                   m.role AS role, m.created_at AS message_at,
                   s.id AS session_id, s.title AS session_title,
                   a.name AS attachment_name, a.created_at AS attachment_at
              FROM chunks c
              LEFT JOIN messages m ON m.id = c.message_id
              LEFT JOIN sessions s ON s.id = m.session_id
              LEFT JOIN attachments a ON a.id = c.attachment_id
             WHERE c.id IN ({marks})
            """,
            tuple(ids),
        )
        return {row["id"]: dict(row) for row in rows}

    def messages_needing_chunks(
        self, session_id: str | None = None, limit: int = 500
    ) -> list[StoredMessage]:
        """Messages that have never been chunked.

        Three exclusions, each of which would otherwise poison retrieval:

        `role = 'tool'` is skill output -- the same judgement migration 1's FTS
        trigger already made. Indexing it means recall retrieves the results of
        previous recalls.

        `role = 'system'` is the preamble, which matches everything and means
        nothing.

        Empty content is an assistant row between `add_message` and the
        `_persist` at the end of the turn. Skipping it here rather than writing
        zero chunks is what lets the next pass pick it up once it has an
        answer in it.
        """
        rows = self.db.query(
            """
            SELECT m.* FROM messages m
             WHERE m.role IN ('user', 'assistant')
               AND TRIM(m.content) <> ''
               AND (?1 IS NULL OR m.session_id = ?1)
               AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.message_id = m.id)
             ORDER BY m.created_at
             LIMIT ?2
            """,
            (session_id, limit),
        )
        return [StoredMessage(**dict(row)) for row in rows]

    def attachments_needing_chunks(self, limit: int = 200) -> list[StoredAttachment]:
        """Readable attachments that have never been chunked.

        Images are excluded: there is no text to index, and their filename is
        already carried into the transcript by the window builder.
        """
        columns = ", ".join(f"a.{c}" for c in _META_COLUMNS.split(", "))
        rows = self.db.query(
            f"""
            SELECT {columns}, a.data, a.text FROM attachments a
             WHERE a.kind IN ('text', 'document')
               AND NOT EXISTS (
                     SELECT 1 FROM chunks c WHERE c.attachment_id = a.id)
             ORDER BY a.created_at
             LIMIT ?
            """,
            (limit,),
        )
        return [StoredAttachment(**dict(row)) for row in rows]

    def chunk_counts(self) -> dict:
        row = self.db.query_one(
            """
            SELECT COUNT(*) AS total,
                   SUM(embedding IS NOT NULL) AS embedded
              FROM chunks
            """
        )
        return {"total": row["total"] or 0, "embedded": row["embedded"] or 0}

    # -- facts ------------------------------------------------------------

    def add_fact(
        self,
        text: str,
        *,
        source: str = "told",
        category: str | None = None,
        confidence: float = 1.0,
        pinned: bool = False,
        status: str = "active",
        message_id: str | None = None,
    ) -> StoredFact | None:
        """Remember one thing. None when it was already known.

        The unique index on `text` does the deduplicating, and the conflict
        clause turns a repeat sighting into what it actually is -- a fact being
        reinforced, not a new one. A fact already confirmed stays confirmed: a
        second, unconfirmed sighting must never quietly downgrade it to
        pending, or the curation pass could undo a decision the user made.
        """
        now = _now()
        fact = StoredFact(
            id=_new_id("fct"),
            text=text.strip(),
            category=category,
            source=source,
            confidence=confidence,
            pinned=int(pinned),
            status=status,
            message_id=message_id,
            used_count=0,
            last_used_at=None,
            created_at=now,
            updated_at=now,
        )
        if not fact.text:
            return None
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO memory_facts
                    (id, text, category, source, confidence, pinned, status,
                     message_id, used_count, last_used_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
                ON CONFLICT(text) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    confidence = MAX(confidence, excluded.confidence),
                    status = CASE WHEN status = 'active' THEN 'active'
                                  ELSE excluded.status END
                """,
                (
                    fact.id,
                    fact.text,
                    fact.category,
                    fact.source,
                    fact.confidence,
                    fact.pinned,
                    fact.status,
                    fact.message_id,
                    fact.created_at,
                    fact.updated_at,
                ),
            )
            # rowcount is 1 for both branches, so the id is what tells them
            # apart: an update left the original row, with its original id.
            existing = conn.execute(
                "SELECT id FROM memory_facts WHERE text = ?", (fact.text,)
            ).fetchone()
        return fact if existing and existing["id"] == fact.id else None

    def list_facts(self, *, status: str | None = None) -> list[StoredFact]:
        sql = "SELECT * FROM memory_facts"
        params: tuple = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY pinned DESC, created_at DESC"
        return [StoredFact(**dict(row)) for row in self.db.query(sql, params)]

    def active_facts(self, limit: int = 40) -> list[StoredFact]:
        """What goes into the system prompt, in a stable order.

        Ordered by `created_at`, never `updated_at`: reinforcing a fact would
        otherwise reshuffle the list and invalidate the cached prefix for a
        change nobody made. Pinned first, because if the list is ever truncated
        the pinned ones are what must survive.

        Fading facts are excluded by the same rule `StoredFact.fading` reports
        to the page, expressed in SQL so the cap applies after the filter
        rather than before it.
        """
        cutoff = _now() - FADE_AFTER_MS
        rows = self.db.query(
            """
            SELECT * FROM memory_facts
             WHERE status = 'active'
               AND (pinned = 1 OR source = 'told' OR used_count >= 3
                    OR COALESCE(last_used_at, created_at) >= ?)
             ORDER BY pinned DESC, created_at
             LIMIT ?
            """,
            (cutoff, limit),
        )
        return [StoredFact(**dict(row)) for row in rows]

    def mark_facts_used(self, ids: list[str]) -> None:
        """One batched update per turn, not one per fact."""
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        self.db.execute(
            f"""
            UPDATE memory_facts
               SET used_count = used_count + 1, last_used_at = ?
             WHERE id IN ({marks})
            """,
            (_now(), *ids),
        )

    def update_fact(
        self,
        fact_id: str,
        *,
        text: str | None = None,
        category: str | None = None,
        pinned: bool | None = None,
        status: str | None = None,
    ) -> bool:
        if not self.db.query_one("SELECT 1 FROM memory_facts WHERE id = ?", (fact_id,)):
            return False
        self.db.execute(
            """
            UPDATE memory_facts
               SET text = COALESCE(?, text),
                   category = COALESCE(?, category),
                   pinned = COALESCE(?, pinned),
                   status = COALESCE(?, status),
                   updated_at = ?
             WHERE id = ?
            """,
            (
                text.strip() if text else None,
                category,
                None if pinned is None else int(pinned),
                status,
                _now(),
                fact_id,
            ),
        )
        return True

    def delete_fact(self, fact_id: str) -> bool:
        row = self.db.query_one("SELECT 1 FROM memory_facts WHERE id = ?", (fact_id,))
        if not row:
            return False
        # Actually deleted, not tombstoned. The page is titled "What I
        # remember" and a row still sitting there marked forgotten would make
        # that a lie.
        self.db.execute("DELETE FROM memory_facts WHERE id = ?", (fact_id,))
        return True

    def delete_all_facts(self) -> int:
        count = self.db.query_one("SELECT COUNT(*) AS n FROM memory_facts")["n"]
        self.db.execute("DELETE FROM memory_facts")
        return count

    def find_facts(self, needle: str, limit: int = 10) -> list[StoredFact]:
        """Substring match, for `forget` naming a fact in the user's words.

        Deliberately not FTS: this runs over a few dozen rows the user wrote
        themselves, and LIKE needs no index, no tokeniser and no escaping of
        their apostrophes.
        """
        rows = self.db.query(
            "SELECT * FROM memory_facts WHERE text LIKE ? ORDER BY pinned DESC LIMIT ?",
            (f"%{needle.strip()}%", limit),
        )
        return [StoredFact(**dict(row)) for row in rows]

    # -- settings ---------------------------------------------------------

    def get_settings(self, defaults: dict[str, bool]) -> dict[str, bool]:
        stored = {
            row["key"]: row["value"]
            for row in self.db.query("SELECT key, value FROM app_settings")
        }
        return {key: stored.get(key, "1" if default else "0") == "1"
                for key, default in defaults.items()}

    def set_settings(self, values: dict[str, bool]) -> None:
        if not values:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                [(key, "1" if on else "0") for key, on in values.items()],
            )

    # The same table read as text rather than as a switch. `app_settings` was
    # built for the three booleans on the Memory page, and every one of those
    # still round-trips through "1"/"0" -- but a stored accent is a small JSON
    # object, and coercing that to a bool on the way out would lose it.
    #
    # Two accessors rather than a `kind` argument on the existing pair: a
    # caller always knows which of the two it wants, and a mistaken read is
    # then a missing method rather than a value that quietly comes back False.

    def get_text_setting(self, key: str) -> str | None:
        row = self.db.query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_text_setting(self, key: str, value: str | None) -> None:
        """Store a string; None deletes the row, which restores the default."""
        if value is None:
            self.db.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            return
        self.db.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    # -- documents summary ------------------------------------------------


    def attachment_summary(self) -> list[dict]:
        """The Documents column of the memory page.

        A summary, not a new concept: the drawn card wants a count and a few
        names per kind, and that is a GROUP BY over a table that already
        exists rather than a collections feature nobody asked for.
        """
        rows = self.db.query(
            """
            SELECT a.kind AS kind,
                   COUNT(*) AS count,
                   MAX(a.created_at) AS newest,
                   SUM(a.id IN (SELECT DISTINCT attachment_id FROM chunks
                                 WHERE attachment_id IS NOT NULL)) AS indexed
              FROM attachments a
             GROUP BY a.kind
             ORDER BY count DESC
            """
        )
        summary = []
        for row in rows:
            names = self.db.query(
                "SELECT name FROM attachments WHERE kind = ? ORDER BY created_at DESC LIMIT 3",
                (row["kind"],),
            )
            summary.append(
                {
                    "kind": row["kind"],
                    "count": row["count"],
                    "newest": row["newest"],
                    "indexed": row["indexed"] or 0,
                    "names": [n["name"] for n in names],
                }
            )
        return summary

    # -- calendar ---------------------------------------------------------

    def add_event(
        self,
        title: str,
        starts_at: str,
        *,
        ends_at: str | None = None,
        all_day: bool = False,
        notes: str | None = None,
        tz: str | None = None,
        session_id: str | None = None,
    ) -> StoredEvent:
        event = StoredEvent(
            id=_new_id("evt"),
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            all_day=1 if all_day else 0,
            notes=notes,
            tz=tz,
            session_id=session_id,
            created_at=_now(),
        )
        self.db.execute(
            """
            INSERT INTO calendar_events
                (id, title, starts_at, ends_at, all_day, notes, tz, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id, event.title, event.starts_at, event.ends_at,
                event.all_day, event.notes, event.tz, event.session_id,
                event.created_at,
            ),
        )
        return event

    def update_event(
        self,
        event_id: str,
        *,
        title: str,
        starts_at: str,
        ends_at: str | None,
        all_day: bool,
        notes: str | None,
    ) -> StoredEvent | None:
        """Replace an event's mutable fields. None if there is no such event.

        Every field is required rather than optional-and-merged, which is the
        opposite of `update_message` above and deliberate: telling "leave this
        alone" apart from "clear this" needs a sentinel, and a calendar edit
        genuinely has to be able to clear an end time or a note. The callers
        already hold the row -- they read it to find it -- so they state the
        whole new event and this writes it.

        `created_at`, `session_id` and `tz` are absent for the same reason:
        the first two are provenance, which editing an event does not change.
        """
        if self.get_event(event_id) is None:
            return None
        self.db.execute(
            """
            UPDATE calendar_events
               SET title = ?, starts_at = ?, ends_at = ?, all_day = ?, notes = ?
             WHERE id = ?
            """,
            (title, starts_at, ends_at, 1 if all_day else 0, notes, event_id),
        )
        return self.get_event(event_id)

    def list_events(
        self, *, since: str | None = None, until: str | None = None, limit: int = 500
    ) -> list[StoredEvent]:
        """Events in a half-open range, soonest first.

        The bounds are compared as strings, which works because the format is
        fixed-width ISO-8601 -- lexicographic order is chronological order for
        'YYYY-MM-DDTHH:MM' and nothing else has to parse a date to filter.
        `until` is exclusive so a month query is [first, first-of-next) with no
        arithmetic about how long the month is.
        """
        clauses, params = [], []
        if since:
            clauses.append("starts_at >= ?")
            params.append(since)
        if until:
            clauses.append("starts_at < ?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM calendar_events {where} ORDER BY starts_at, rowid LIMIT ?",
            (*params, limit),
        )
        return [StoredEvent(**dict(row)) for row in rows]

    def search_events(self, needle: str, limit: int = 50) -> list[StoredEvent]:
        """Substring match on title and notes.

        Not FTS: the calendar is small enough that a LIKE scan is instant, and
        a second FTS table would have to be kept in step by triggers for a
        table that will never hold thousands of rows.
        """
        like = f"%{needle}%"
        rows = self.db.query(
            """
            SELECT * FROM calendar_events
             WHERE title LIKE ? OR COALESCE(notes, '') LIKE ?
             ORDER BY starts_at, rowid LIMIT ?
            """,
            (like, like, limit),
        )
        return [StoredEvent(**dict(row)) for row in rows]

    def get_event(self, event_id: str) -> StoredEvent | None:
        row = self.db.query_one("SELECT * FROM calendar_events WHERE id = ?", (event_id,))
        return StoredEvent(**dict(row)) if row else None

    def delete_event(self, event_id: str) -> None:
        self.db.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))

    # -- projects ---------------------------------------------------------

    def create_project(self, name: str) -> dict:
        project_id = _new_id("prj")
        now = _now()
        self.db.execute(
            "INSERT INTO projects (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (project_id, name, now, now),
        )
        return {
            "id": project_id,
            "name": name,
            "created_at": now,
            "updated_at": now,
            "theme": None,
        }

    def list_projects(self) -> list[dict]:
        rows = self.db.query(
            """
            SELECT p.id, p.name, p.created_at, p.updated_at, p.theme,
                   (SELECT COUNT(*) FROM sessions s WHERE s.project_id = p.id)
                       AS session_count
            FROM projects p
            ORDER BY p.name COLLATE NOCASE
            """
        )
        return [dict(row) for row in rows]

    def get_project(self, project_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return dict(row) if row else None

    def rename_project(self, project_id: str, name: str) -> None:
        self.db.execute(
            "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
            (name, _now(), project_id),
        )

    def delete_project(self, project_id: str) -> None:
        # The conversations survive -- the foreign key is ON DELETE SET NULL,
        # so they reappear as unfiled rather than vanishing with the folder.
        self.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def set_session_project(self, session_id: str, project_id: str | None) -> None:
        """File a conversation, or unfile it with None."""
        self.db.execute(
            "UPDATE sessions SET project_id = ? WHERE id = ?", (project_id, session_id)
        )

    # -- accents ----------------------------------------------------------
    #
    # `theme` is the JSON the client sent, stored verbatim. None clears it,
    # which is not the same as an accent that says "off": cleared means this
    # scope has no opinion and the one above it decides, while off is a
    # decision to wear no colour at all.
    #
    # Neither writer touches `updated_at`. Recolouring a conversation is not a
    # change to the conversation, and bumping the timestamp would jump it to
    # the top of a list ordered by when it was last actually said something.

    def set_session_theme(self, session_id: str, theme: str | None) -> None:
        self.db.execute(
            "UPDATE sessions SET theme = ? WHERE id = ?", (theme, session_id)
        )

    def set_project_theme(self, project_id: str, theme: str | None) -> None:
        self.db.execute(
            "UPDATE projects SET theme = ? WHERE id = ?", (theme, project_id)
        )

    # -- MCP servers ------------------------------------------------------

    def list_mcp_servers(self, *, enabled_only: bool = False) -> list[StoredMCPServer]:
        where = "WHERE enabled = 1" if enabled_only else ""
        rows = self.db.query(
            f"SELECT * FROM mcp_servers {where} ORDER BY name COLLATE NOCASE"
        )
        return [StoredMCPServer(**dict(row)) for row in rows]

    def get_mcp_server(self, server_id: str) -> StoredMCPServer | None:
        row = self.db.query_one("SELECT * FROM mcp_servers WHERE id = ?", (server_id,))
        return StoredMCPServer(**dict(row)) if row else None

    def get_mcp_server_by_name(self, name: str) -> StoredMCPServer | None:
        row = self.db.query_one("SELECT * FROM mcp_servers WHERE name = ?", (name.strip(),))
        return StoredMCPServer(**dict(row)) if row else None

    def add_mcp_server(
        self,
        name: str,
        transport: str,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        auto_approve: list[str] | None = None,
        enabled: bool = True,
        description: str | None = None,
        homepage: str | None = None,
    ) -> StoredMCPServer:
        server_id = _new_id("mcp")
        now = _now()
        args_json = json.dumps(args) if args is not None else None
        env_json = json.dumps(env) if env is not None else None
        headers_json = json.dumps(headers) if headers is not None else None
        auto_approve_json = json.dumps(auto_approve) if auto_approve is not None else None
        enabled_int = 1 if enabled else 0

        self.db.execute(
            """
            INSERT INTO mcp_servers (
                id, name, transport, command, args, env, cwd, url,
                headers, auto_approve, enabled, description, created_at, updated_at,
                homepage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                server_id,
                name.strip(),
                transport.strip().lower(),
                command.strip() if command else None,
                args_json,
                env_json,
                cwd.strip() if cwd else None,
                url.strip() if url else None,
                headers_json,
                auto_approve_json,
                enabled_int,
                description.strip() if description else None,
                now,
                now,
                homepage.strip() if homepage else None,
            ),
        )
        return StoredMCPServer(
            id=server_id,
            name=name.strip(),
            transport=transport.strip().lower(),
            command=command.strip() if command else None,
            args=args_json,
            env=env_json,
            cwd=cwd.strip() if cwd else None,
            url=url.strip() if url else None,
            headers=headers_json,
            auto_approve=auto_approve_json,
            enabled=enabled_int,
            description=description.strip() if description else None,
            created_at=now,
            updated_at=now,
            homepage=homepage.strip() if homepage else None,
        )

    def update_mcp_server(
        self,
        server_id: str,
        *,
        name: str | None = None,
        transport: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        auto_approve: list[str] | None = None,
        enabled: bool | None = None,
        description: str | None = None,
    ) -> StoredMCPServer | None:
        current = self.get_mcp_server(server_id)
        if not current:
            return None

        now = _now()
        new_name = name.strip() if name is not None else current.name
        new_transport = transport.strip().lower() if transport is not None else current.transport
        new_command = command.strip() if command is not None else current.command
        new_args = json.dumps(args) if args is not None else current.args
        new_env = json.dumps(env) if env is not None else current.env
        new_cwd = cwd.strip() if cwd is not None else current.cwd
        new_url = url.strip() if url is not None else current.url
        new_headers = json.dumps(headers) if headers is not None else current.headers
        new_auto_approve = (
            json.dumps(auto_approve) if auto_approve is not None else current.auto_approve
        )
        new_enabled = (1 if enabled else 0) if enabled is not None else current.enabled
        new_description = description.strip() if description is not None else current.description

        self.db.execute(
            """
            UPDATE mcp_servers
               SET name = ?, transport = ?, command = ?, args = ?, env = ?,
                   cwd = ?, url = ?, headers = ?, auto_approve = ?, enabled = ?,
                   description = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                new_name,
                new_transport,
                new_command,
                new_args,
                new_env,
                new_cwd,
                new_url,
                new_headers,
                new_auto_approve,
                new_enabled,
                new_description,
                now,
                server_id,
            ),
        )
        return self.get_mcp_server(server_id)

    # -- MCP icons --------------------------------------------------------
    #
    # Keyed by where the artwork came from, not by which server is wearing it:
    # "site:<domain>" for a fetched logo, "server:<id>" for an uploaded one.
    # Two servers pointing at the same service share one row, and deleting a
    # server does not take the logo away from its sibling.

    def get_mcp_icon(self, key: str) -> dict | None:
        row = self.db.query_one(
            "SELECT key, mime, data, source, fetched_at FROM mcp_icons WHERE key = ?",
            (key,),
        )
        return dict(row) if row else None

    def put_mcp_icon(self, key: str, *, mime: str, data: bytes, source: str) -> None:
        self.db.execute(
            """
            INSERT INTO mcp_icons (key, mime, data, source, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                mime = excluded.mime,
                data = excluded.data,
                source = excluded.source,
                fetched_at = excluded.fetched_at
            """,
            (key, mime, data, source, _now()),
        )

    def delete_mcp_icon(self, key: str) -> bool:
        if not self.get_mcp_icon(key):
            return False
        self.db.execute("DELETE FROM mcp_icons WHERE key = ?", (key,))
        return True

    def delete_mcp_server(self, server_id: str) -> bool:
        if not self.get_mcp_server(server_id):
            return False
        # The uploaded override goes with it; the site cache does not, because
        # another server may be wearing the same logo.
        self.delete_mcp_icon(f"server:{server_id}")
        self.db.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        return True

    def set_mcp_server_enabled(self, server_id: str, enabled: bool) -> bool:
        if not self.get_mcp_server(server_id):
            return False
        self.db.execute(
            "UPDATE mcp_servers SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, _now(), server_id),
        )
        return True

def _fts_query(text: str) -> str:
    """A user's sentence as an FTS5 MATCH expression.

    FTS5 MATCH takes a query language, not a search box. An apostrophe raises
    "syntax error near", a hyphen makes the tail look like a column name
    ("no such column: let"), and a bare AND or NEAR is read as an operator.
    Quoting every extracted word neutralises all three at once.

    Returns "" when there is nothing searchable left, which the caller reports
    as no results rather than as a failure.
    """
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    return " OR ".join(f'"{word}"' for word in words if len(word) > 1)
