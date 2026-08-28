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
