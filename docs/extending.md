# Extending the server

Written for putting attachments and thinking back, and for adding things that
were never there. It assumes you are writing the Python yourself.

The removal took out code, not data. You are rarely starting from a blank
schema here — more often you are writing code to meet one that is already
waiting for it.

`PRAGMA user_version` is **6**. Migrations 1–4 were in place before the code
that used them; 5 (reasoning and skills on a message) and 6 (memory) have since
landed with their features.

---

## 1. The map

One turn, end to end:

```
POST /api/chat
  auth.py            bearer token, or 401
  api.py             ChatRequest validates the body
  orchestrator.py    store the user message
                     build the window (system prompt + recent turns that fit)
  providers/         stream tokens from ollama
  orchestrator.py    yield SSE frames, persist the answer
  api.py             StreamingResponse back to the browser
```

Eight files, each with one job:

| File | Owns |
|---|---|
| `main.py` | Wiring. Builds `Database → Store → ProviderRouter → Orchestrator` and mounts the router. Nothing else. |
| `config.py` | Settings, from environment variables, with working defaults. |
| `auth.py` | The bearer-token dependency. |
| `api.py` | HTTP shapes only — what a request must look like, what comes back. |
| `orchestrator.py` | The turn. What the model sees and in what order. |
| `store.py` | Rows in, rows out. Plain SQL. |
| `db.py` | Schema and migrations. |
| `providers/` | The seam. Everything above it talks `Message` and `Chunk` and never learns which backend answered. |

**A feature enters at one of three altitudes.** Deciding which one first will
save you from touching files that did not need to change.

1. **Request-shaped** — a new endpoint, a new field. `api.py` alone.
2. **Changes what the model sees** — `orchestrator.py`, and `providers/` if the
   backend needs a new kind of input.
3. **Has to be remembered** — a migration in `db.py`, methods in `store.py`,
   then one of the above.

---

## 2. Warm-up: put `think` back

Do this one first. It is a single boolean travelling the full length of the
system, so you meet every seam with nothing else in the way. Half a day.

**The path, in order:**

1. **`config.py`** — add `ollama_think: bool`. There is `_env` and `_env_int`
   but no `_env_bool`; write it. Decide what `"0"`, `"false"` and `"no"` mean
   and be consistent.
2. **`api.py`** — add `think: bool | None = None` to `ChatRequest`. Three
   states, not two: on, off, and *unset* meaning "use the configured default".
3. **`orchestrator.run_turn`** — accept `think` as a keyword-only argument
   (after the `*`) and pass it down.
4. **`providers/base.py`** — add it to the `ModelProvider` protocol's `stream`
   signature. Do this before the implementations, not after: the protocol is
   the contract, and writing it first is what stops the two providers drifting.
5. **`providers/ollama.py`** — `payload["think"] = think`, and read
   `message.get("thinking")` off each streamed event alongside `content`.
6. **`providers/anthropic.py`** — it will not take the same shape. Decide
   whether to map it or ignore it, and write the reason in a comment.
7. **`orchestrator.py`** — a new SSE frame: `yield _sse("thinking", {...})`.
   Reasoning is *not* persisted; it exists only for the live stream.
8. **`api.py`** — expose the default on `/status` so the client can start its
   toggle in the right position.

**Gotcha you already paid for once:** on the local runner, reasoning and vision
are mutually exclusive — a turn carrying an image goes blind if thinking is on.
When you get to attachments, force `think=False` on any turn with an image.

**Python you will meet:** keyword-only parameters, `Protocol`, async generators
(a function with both `async for` and `yield`), dataclass fields with
`default_factory`.

---

## 3. Attachments

Bigger, because it involves bytes. Build it bottom-up — each layer testable
before the one above it exists.

**Do not write a migration.** Migrations 2 and 3 already created the
`attachments` table and its `text` column. Those 5 orphaned rows are real test
data: your first `list_attachments` has something to return before you have
written a single upload path.

**Order:**

1. **`store.py`** — `add_attachment`, `list_attachments`, `get_attachment`, and
   a `StoredAttachment` dataclass. Pure SQL against a table that already exists.
   Test it in a REPL against a copy of the database.
2. **`attachments.py`** (new) — validation and decoding. Keep it *pure*: bytes
   in, bytes out, no database, no HTTP. Limits, allowed types, base64 decoding,
   a readable error for each refusal. Pure functions are the easiest thing in
   Python to test, and this is where the fiddly rules live.
3. **`api.py`** — an `AttachmentIn` model (`name`, `mime`, `data` as base64),
   the field on `ChatRequest`, and a `GET /attachments/{id}` route returning
   raw bytes. Base64 in JSON rather than multipart, so you need no new
   dependency and no new content type.
4. **`orchestrator.py`** — attachments become part of the window. Images ride
   along as images; documents arrive as text with a header naming the file.
   This is also where the token budget has to account for them.
5. **`providers/`** — an `Image` type, `Message.images`, and Ollama's `images`
   array on each message.

**Cut scope on the first pass.** Start with **PNG and JPEG only**. Skip
`extract.py` entirely — it is 200 lines of zipfile and XML archaeology for
.docx/.xlsx/.pptx, and it teaches you almost nothing about Python that the rest
of this project does not teach better. Add PDF later with `pypdf`, which is
twenty lines. Add the Office formats only if you ever actually need them.

**Dependencies to put back in `server/pyproject.toml`:** `pillow` first, for
normalising formats the runner rejects. `pypdf` only when you reach PDFs.

The old implementation is in
`.ui-revert/20260820-161938-backend-files-thinking/`. Read it *after* you have
tried the layer yourself — it is worth more as a check on your design than as
something to copy.

---

## 4. Adding something new

The `chunks` table is sitting there with zero rows and a partial index named
`idx_chunks_pending` waiting for a backfill. Searchable history is the obvious
next feature and the schema is already designed for it.

**The template, whatever the feature:**

1. **Pick the altitude** (section 1). Write down which files you expect to
   touch before you open any of them.
2. **Schema first, if it must survive a restart.** Append to `MIGRATIONS` in
   `db.py`. Never edit one that has run — your database is at 4, so anything
   you change in 1–4 will simply never be applied, and you will debug a table
   that does not match its own source code.
3. **`store.py` next.** Get the rows moving before anything above cares.
4. **Then a module of pure functions** for the actual logic — chunking,
   scoring, formatting. No I/O. This is the part you can test in a REPL and the
   part where mistakes are cheapest to find.
5. **Then wire it in**, to `orchestrator.py` if it changes what the model sees,
   or to `api.py` if it is a new endpoint.
6. **An SSE frame** only if the UI has to see it *while* the turn runs. Frames
   are already `meta`, `delta`, `done`, `error` — adding one is one `_sse(...)`
   call and one branch in `useChat.js`.
7. **The client last.** Never guess at the server's shape from the client side.

History search was the worked example of this template, and it is now built:
migration 6, `store.py`, the pure modules in `app/memory/`, then one skill and
one page. **[`memory.md`](memory.md)** documents what it does and why.

Its first two steps are still the ones to copy: schema, then rows, then a
module of pure functions you can test in a REPL before anything above it
exists.

---

## 5. Wiring the frontend back up

The two composer buttons are still there and still styled — only their handlers
are gone. `.ui-revert/20260820-171125-frontend-files-thinking/` has the working
versions of everything below.

| File | What to put back |
|---|---|
| `components/Composer.jsx` | The hidden `<input type="file">`, `stage`/`unstage`, the window drag-and-drop effect, and `onSend(text, files, thinkingOn)` |
| `hooks/useChat.js` | `send(text, files, think)`, the `thinking` SSE branch, `reasoning` in the frame buffer |
| `lib/api.js` | `attachments` and `think` on the chat body; the `attachment(id)` fetch |
| `components/Message.jsx` | The reasoning and attachment branches |
| `components/MessageList.jsx` | Pass `thinking`, `reasoning`, `attachments` through |
| `Attachments.jsx`, `Reasoning.jsx`, `lib/files.js` | Restore whole — they need no edits |

**The field names are the contract.** Those three restored files expect
`{name, mime, data, size, kind}` on an attachment and a `thinking` event
carrying `{text}`. Name things differently on the server and you will be editing
them after all — so decide the names when you write `AttachmentIn`, not later.

Every CSS rule survived: `.staged`, `.lightbox`, `.reasoning`, `.file-chip`,
and the `data-dropping` state on the composer. You will not have to restyle
anything.

---

## 6. Testing without the browser

**Run against a throwaway database** so experiments never touch `data/chat.db`:

```bash
DB_PATH=/tmp/scratch.db AUTH_TOKEN=devtoken BIND_PORT=8091 .venv/Scripts/python -m app
```

Both are read by `config.py` already. A fresh `DB_PATH` runs every migration
from scratch, which is also how you check that a migration you just wrote
actually applies.

**Hit the endpoint directly:**

```bash
curl -N -H "Authorization: Bearer devtoken" -H "Content-Type: application/json" -d "{\"message\":\"say pong\"}" http://127.0.0.1:8091/api/chat
```

You should see `event: session`, `event: meta`, a run of `event: delta`, then
`event: done`. When you add a frame, this is where you confirm it before
touching a line of React.

**Check the app still constructs** without starting a server:

```bash
.venv/Scripts/python -c "from app.main import create_app; create_app()"
```

**Tests start in `server/tests/`.** `pip install -e "./server[dev]"`, then
`pytest` from `server/`. There are 28, all over the pure functions in
`app/memory/` — chunking, ranking, FTS escaping, and the fact parser — because
those need no fixtures, no database and no event loop. Everything else is still
checked by hand; add to this rather than starting again.

---

## 7. Python in this repo, by example

You already own working examples of most of what you need. When a concept is
unclear, read the file rather than a tutorial — it is the same idea in a context
you understand.

| Concept | Where it already lives |
|---|---|
| Async generators | `orchestrator.run_turn` — `yield` inside `async def` |
| `async for` over one | `orchestrator._stream_with_recovery` |
| Context managers | `db.transaction` (`@contextmanager`), `main.lifespan` |
| Structural typing | `providers/base.ModelProvider` — a `Protocol`, not a base class |
| Frozen dataclasses | `config.Settings`, `providers/base.Message` |
| `field(default_factory=...)` | `config.Settings` — why a plain default breaks for mutables |
| Keyword-only arguments | `store.add_message` — everything after the `*` |
| Exception hierarchies | `ProviderError` → `ContextOverflow`, caught separately |
| `try/except/finally` for cleanup | `orchestrator.run_turn` — persists even on disconnect |
| Pydantic vs dataclass | `api.ChatRequest` validates *untrusted* input; `providers/base` describes trusted internal shapes |
| `from __future__ import annotations` | Every file — makes `str \| None` work on older runtimes and defers annotation evaluation |

---

## 8. House rules

- **Never edit a migration that has run.** Append.
- **Nothing above `providers/` learns which backend answered.** If orchestration
  starts checking `provider.name == "ollama"`, the abstraction has sprung a leak
  and the fix belongs in the provider.
- **Errors a person will read get a full sentence**, raised as `HTTPException`
  or `ProviderError`. The client renders them verbatim.
- **Comments say why, not what.** The code says what.
- **Snapshot before you delete.** `.ui-revert/` exists because that rule was
  broken once and three icons did not come back.
