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
