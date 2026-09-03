"""SQLite access. One file, WAL mode, one writer.

A single connection guarded by a lock. Calls are made straight from async
handlers rather than pushed through a thread pool: for one user against a local
file, every statement here is microseconds, and the indirection would buy
nothing but stack frames.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Each entry is applied in order and bumps PRAGMA user_version. Never edit a
# migration that has shipped -- append a new one.
MIGRATIONS: list[str] = [
    # 1 -- sessions, messages, and the keyword index over everything ever said.
    """
    CREATE TABLE sessions (
      id           TEXT PRIMARY KEY,
      title        TEXT,
      created_at   INTEGER NOT NULL,
      updated_at   INTEGER NOT NULL
    );

    CREATE TABLE messages (
      id           TEXT PRIMARY KEY,
      session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      role         TEXT NOT NULL,          -- user | assistant | system | tool
      content      TEXT NOT NULL,
      tokens       INTEGER,
      model        TEXT,                   -- which model produced this turn
      provider     TEXT,                   -- and via which provider
      created_at   INTEGER NOT NULL
    );

    CREATE INDEX idx_messages_session ON messages(session_id, created_at);

    -- Episodic recall lives in phase 4, but the index is free to maintain from
    -- day one and this way it covers the earliest history too.
    CREATE VIRTUAL TABLE messages_fts USING fts5(
      content,
      content='messages',
      content_rowid='rowid'
    );

    CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
      INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
    END;
    
    DROP TRIGGER messages_ai;
    CREATE TRIGGER messages_ai AFTER INSERT ON messages
    WHEN new.role <> 'tool' BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
    END;

    CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
      INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
    END;

    CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
      INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
      INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
    END;
    """,
    # 2 -- files attached to a message.
    #
    # The bytes live in the database rather than beside it: `VACUUM INTO` then
    # still produces one file containing everything, which is the whole backup
    # story from section 7. A directory of loose blobs would have to be backed
    # up separately, and would drift from the rows that point at it.
    #
    # Deletes cascade twice over -- session to message to attachment -- so
    # dropping a conversation takes its files with it.
    """
    CREATE TABLE attachments (
      id           TEXT PRIMARY KEY,
      message_id   TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
      kind         TEXT NOT NULL,          -- image | text
      name         TEXT NOT NULL,
      mime         TEXT NOT NULL,
      size         INTEGER NOT NULL,       -- bytes, before base64
      data         BLOB NOT NULL,
      created_at   INTEGER NOT NULL
    );

    CREATE INDEX idx_attachments_message ON attachments(message_id);
    """,
    # 3 -- the readable form of a document.
    #
    # A PDF or .docx reaches the model as text, and pulling that text out is
    # slow enough that doing it on every turn of a long conversation would be
    # felt. Extracted once at upload, stored beside the original bytes: the
    # file can still be downloaded as it was, and the prompt is assembled from
    # a column read.
    #
    # NULL for images and for plain text files, which are already their own
    # readable form.
    """
    ALTER TABLE attachments ADD COLUMN text TEXT;
    """,
    # 4 -- retrievable chunks, and their embeddings.
    #
    # A chunk is a paragraph-sized piece of something already stored here: a
    # message, or the extracted text of a document. Embedding whole messages
    # matches badly -- a long answer averages out to nothing in particular --
    # so retrieval is built on pieces rather than on rows.
    #
    # The vector is a raw float32 BLOB. At this scale that is enough: a few
    # thousand chunks is a couple of megabytes, and cosine against all of them
    # is one numpy matmul in single-digit milliseconds. sqlite-vec earns its
    # place somewhere past a hundred thousand, and not before.
    #
    # `model` and `dims` are stored per row because vectors from different
    # encoders are not comparable. Changing the embedding model means finding
    # every row that disagrees and re-embedding it, and that is only possible
    # if each row remembers what made it.
    """
    CREATE TABLE chunks (
      id             TEXT PRIMARY KEY,
      message_id     TEXT REFERENCES messages(id) ON DELETE CASCADE,
      attachment_id  TEXT REFERENCES attachments(id) ON DELETE CASCADE,
      ordinal        INTEGER NOT NULL,   -- position within its source
      page           INTEGER,            -- the [page N] a PDF chunk came from
      content        TEXT NOT NULL,
      embedding      BLOB,               -- float32; NULL until embedded
      dims           INTEGER,
      model          TEXT,               -- the encoder that produced it
      created_at     INTEGER NOT NULL,

      -- Exactly one source. A chunk belongs to a message or to an attachment,
      -- never to both and never to neither -- otherwise deletes leak.
      CHECK ((message_id IS NULL) <> (attachment_id IS NULL))
    );

    CREATE INDEX idx_chunks_message ON chunks(message_id);
    CREATE INDEX idx_chunks_attachment ON chunks(attachment_id);

    -- The backfill and the write path both ask "what still needs embedding?",
    -- and a partial index keeps that answer cheap once most rows are done.
    CREATE INDEX idx_chunks_pending ON chunks(id) WHERE embedding IS NULL;
    """,
    # 5 -- the working that produced an answer.
    #
    # Reasoning and tool calls used to be live-only: streamed to the client and
    # then dropped, so reopening a conversation gave back the reply with no
    # record of how it was reached. That is fine for a chat and wrong for a
    # thing that runs skills -- "which file did it read?" is exactly the
    # question you ask a day later.
    #
    # `reasoning` is the raw thinking stream, stored as it arrived. `skills` is
    # a JSON array of {name, arguments, result}, not a table: it is always read
    # whole, always with its message, and never queried across messages, so a
    # column keeps the read to the one row the client already fetches.
    #
    # Both NULL on every existing row, and on any turn that used neither.
    """
    ALTER TABLE messages ADD COLUMN reasoning TEXT;
    ALTER TABLE messages ADD COLUMN skills TEXT;
    """,
    # 6 -- memory: what is remembered on purpose, and the keyword half of
    # what can be looked up.
    #
    # `memory_facts` is the short curated list the model sees on every turn,
    # as distinct from the history it can search. A fact is a sentence, it
    # came from somewhere, and it can be wrong -- hence `source`, `confidence`
    # and a `status` that lets one be held back until a human agrees with it.
    #
    # `message_id` is provenance, and it is the one foreign key in this schema
    # that does NOT cascade. Everywhere else deleting a session takes its
    # contents with it, which is right for messages, attachments and chunks:
    # they *are* the conversation. A fact is not. "Prefers the short answer
    # first" was learned during some conversation but is not about it, and
    # tidying the sidebar in September must not silently change how the
    # assistant writes in October. The link goes null; the fact stays.
    """
    CREATE TABLE memory_facts (
      id            TEXT PRIMARY KEY,
      text          TEXT NOT NULL,
      category      TEXT,
      source        TEXT NOT NULL,                       -- told | inferred
      confidence    REAL NOT NULL DEFAULT 1.0,           -- 1.0 when told
      pinned        INTEGER NOT NULL DEFAULT 0,
      status        TEXT NOT NULL DEFAULT 'active',      -- active | pending
      message_id    TEXT REFERENCES messages(id) ON DELETE SET NULL,
      used_count    INTEGER NOT NULL DEFAULT 0,
      last_used_at  INTEGER,
      created_at    INTEGER NOT NULL,
      updated_at    INTEGER NOT NULL
    );

    -- The one query that runs on every single turn: active facts, pinned
    -- first, in a stable order.
    CREATE INDEX idx_facts_active ON memory_facts(status, pinned DESC, created_at);

    -- The curation pass will propose "Lives in Leeds" on four separate
    -- occasions. Let SQLite refuse the duplicate: an ON CONFLICT that bumps
    -- updated_at turns the second sighting into a fact being reinforced,
    -- which is the correct reading of it and costs one clause.
    CREATE UNIQUE INDEX idx_facts_text ON memory_facts(text);

    -- Small key/value store for preferences that are user data rather than
    -- deployment configuration -- the three switches on the Memory page, and
    -- eventually the per-skill on/off that `Registry.set_enabled` currently
    -- forgets on every restart.
    CREATE TABLE app_settings (
      key    TEXT PRIMARY KEY,
      value  TEXT NOT NULL
    );

    -- Keyword search over chunks, mirroring messages_fts over messages.
    --
    -- Both halves of retrieval have to return the same kind of key or their
    -- rankings cannot be combined, and the vector half only ever returns
    -- chunks. This also reaches something messages_fts structurally cannot:
    -- the text of an attachment, which has no message row and so has never
    -- been in that index at all.
    --
    -- messages_fts stays exactly as it is. It is the right index for browsing
    -- whole conversations, which wants messages rather than fragments.
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
      content,
      content='chunks',
      content_rowid='rowid'
    );

    CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
      INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
    END;

    CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
      INSERT INTO chunks_fts(chunks_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
    END;

    -- Only the text matters to the index, and the column that actually
    -- changes after insert is `embedding`. Rewriting the FTS row on every
    -- embedding write would be one delete and one insert per chunk during a
    -- backfill, for no change in what is indexed.
    CREATE TRIGGER chunks_au AFTER UPDATE OF content ON chunks BEGIN
      INSERT INTO chunks_fts(chunks_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
      INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
    END;
    """,
    # 7 -- the calendar.
    #
    # Times are stored as an ISO-8601 local string, not an epoch: an event is
    # "Tuesday at 9" to the person who made it, and that is still 9am after a
    # daylight-saving change or a flight. Storing an instant and rendering it
    # back would silently move a standing meeting by an hour twice a year.
    # `tz` records where it was written so a display can say so if it ever
    # needs to; nothing computes with it yet.
    #
    # `all_day` rather than a null time, so a date-only event is a deliberate
    # kind rather than a missing field the reader has to interpret.
    """
    CREATE TABLE calendar_events (
      id          TEXT PRIMARY KEY,
      title       TEXT NOT NULL,
      starts_at   TEXT NOT NULL,        -- 'YYYY-MM-DDTHH:MM' local
      ends_at     TEXT,                 -- NULL for a point in time
      all_day     INTEGER NOT NULL DEFAULT 0,
      notes       TEXT,
      tz          TEXT,
      -- Which conversation asked for it, when a skill made it. NULL when a
      -- person added it by hand. ON DELETE SET NULL: deleting a chat must not
      -- take next week's dentist appointment with it.
      session_id  TEXT REFERENCES sessions(id) ON DELETE SET NULL,
      created_at  INTEGER NOT NULL
    );

    -- Every read is a date range, so the index is on the sort key.
    CREATE INDEX idx_events_start ON calendar_events(starts_at);
    """,
    # 8 -- projects: a folder holding conversations.
    #
    # ON DELETE SET NULL, not CASCADE. A project is a way of grouping chats,
    # not a container that owns them -- deleting the folder should file its
    # conversations back under "no project", never destroy months of them.
    # Deleting conversations is what the Settings page is for, where it is
    # gated behind typing the word.
    """
    CREATE TABLE projects (
      id          TEXT PRIMARY KEY,
      name        TEXT NOT NULL,
      created_at  INTEGER NOT NULL,
      updated_at  INTEGER NOT NULL
    );

    ALTER TABLE sessions ADD COLUMN project_id TEXT
      REFERENCES projects(id) ON DELETE SET NULL;

    CREATE INDEX idx_sessions_project ON sessions(project_id, updated_at);
    """,
    # 9 -- database-backed MCP servers.
    #
    # Allows MCP server configurations (stdio, sse, http) to live in SQLite
    # alongside messages and memory, enabling user-configured skills without
    # writing Python or restarting the server.
    """
    CREATE TABLE mcp_servers (
      id           TEXT PRIMARY KEY,
      name         TEXT NOT NULL UNIQUE,
      transport    TEXT NOT NULL,          -- stdio | sse | http | websocket
      command      TEXT,                   -- for stdio
      args         TEXT,                   -- JSON array of strings
      env          TEXT,                   -- JSON object of key-value pairs
      cwd          TEXT,
      url          TEXT,                   -- for sse | http | websocket
      headers      TEXT,                   -- JSON object
      auto_approve TEXT,                   -- JSON array of tool names
      enabled      INTEGER NOT NULL DEFAULT 1,
      description  TEXT,
      created_at   INTEGER NOT NULL,
      updated_at   INTEGER NOT NULL
    );

    CREATE INDEX idx_mcp_servers_enabled ON mcp_servers(enabled);
    """,
    # 10 -- where and when the user was when a conversation started.
    #
    # Recorded per session rather than globally: the answer is a property of
    # the device that opened the conversation, and the whole point is that the
    # phone in the kitchen and the server in the cupboard disagree. Nullable
    # throughout -- an API client that reports nothing is a normal caller, not
    # a broken one, and every session that existed before this migration has
    # nothing to say.
    """
    ALTER TABLE sessions ADD COLUMN tz TEXT;          -- IANA name, e.g. Europe/London
    ALTER TABLE sessions ADD COLUMN locale TEXT;      -- BCP-47 tag, e.g. en-GB
    ALTER TABLE sessions ADD COLUMN utc_offset INTEGER;  -- minutes east of UTC
    ALTER TABLE sessions ADD COLUMN region TEXT;      -- display name, e.g. United Kingdom
    """,
    # 11 -- logos for MCP servers.
    #
    # `homepage` is where to look for one: a preset declares it, a custom
    # server infers it from its endpoint, and a stdio server that names neither
    # simply has no logo to find.
    #
    # Icons are cached by *source* rather than per server, so two servers on
    # one domain share a row and a re-added server is instant. A key is either
    # "site:<domain>" for something fetched, or "server:<id>" for one the
    # reader uploaded -- the second wins, which is what makes an upload an
    # override rather than a race with the fetcher.
    """
    ALTER TABLE mcp_servers ADD COLUMN homepage TEXT;

    CREATE TABLE mcp_icons (
      key        TEXT PRIMARY KEY,   -- site:<domain> | server:<id>
      mime       TEXT NOT NULL,
      data       BLOB NOT NULL,
      source     TEXT,               -- the URL it came from, or 'upload'
      fetched_at INTEGER NOT NULL
    );
    """,
    # 12 -- accents: the colour a conversation or a folder is dressed in.
    #
    # One nullable TEXT column on each, holding the JSON the client sends --
    # a mode and, depending on it, a preset name or a hue. NULL means "not
    # decided here", which is what makes the three scopes stack: a chat falls
    # through to its project, and a project falls through to the app-wide
    # accent in `app_settings`.
    #
    # The colour itself is not stored, only the intent behind it. A hue and a
    # mode survive a change to how the palette is derived; forty resolved hex
    # values would freeze today's arithmetic into every row and have to be
    # migrated the first time a tint is adjusted.
    """
    ALTER TABLE sessions ADD COLUMN theme TEXT;
    ALTER TABLE projects ADD COLUMN theme TEXT;
    """,
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._migrate()

    def _configure(self) -> None:
        cur = self._conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
                # BEGIN/COMMIT go inside the script: executescript commits any
                # transaction opened outside it, so wrapping from Python here
                # would leave each migration half-applied on failure. SQLite
                # DDL and user_version are both transactional.
                self._conn.executescript(
                    f"BEGIN;\n{script}\nPRAGMA user_version={index};\nCOMMIT;"
                )
                print(f"[db] applied migration {index}")

    # -- query helpers ----------------------------------------------------

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def backup_to(self, destination: Path) -> Path:
        """Consistent copy of a live database. `VACUUM INTO` per section 7."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        with self._lock:
            self._conn.execute("VACUUM INTO ?", (str(destination),))
        return destination

    def close(self) -> None:
        with self._lock:
            self._conn.close()
