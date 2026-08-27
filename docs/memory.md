# Implementing memory

The README promises "one conversation history, one memory". The history is
real. The memory is not — not yet. This is how to build it.

Read `extending.md` first if you have not: it explains the three altitudes a
feature enters at, and memory enters at all three.

---

## 1. What "memory" means here

Three different things get called memory, they fail in different ways, and
conflating them is how you end up with a system that forgets what you told it
yesterday while confidently reciting a joke from last March.

| | What it is | Where it lives | State |
|---|---|---|---|
| **The window** | The last N turns, verbatim | `orchestrator.build_window` | Built. Trims from the head. |
| **Recall** | Search over everything ever said | `chunks`, `messages_fts` | Schema built, code missing |
| **Facts** | A short curated list the model always sees | nothing yet | Not started |

They are not alternatives. The window is what the model is *reading*; recall is
what it can *look up*; facts are what it *knows without asking*. A person has
all three and so should this.

The order to build them in is the order of that table, and it is not
negotiable: recall is testable on its own, and facts are worth very little
until there is a corpus to draw them from.

---

## 2. What is already waiting for you

More than you would expect. Previous phases left the schema in place
deliberately, so this is mostly writing code to meet a database that is already
shaped for it.

**`PRAGMA user_version` is 5.** Five migrations have run. Your next migration
is number **6** — append it, never edit 1–5.

**`chunks` exists and is empty** (migration 4). Read its comment in `db.py`
before you write a line; it already made several decisions for you:

- A chunk is a *paragraph-sized piece* of a message or an attachment's
  extracted text. Not a whole message — "a long answer averages out to nothing
  in particular".
- `CHECK ((message_id IS NULL) <> (attachment_id IS NULL))` — exactly one
  source, so deletes cannot leak.
- `embedding` is a raw float32 BLOB, `NULL` until embedded. `dims` and `model`
  are stored *per row* because vectors from different encoders are not
  comparable.
- `idx_chunks_pending` is a partial index over `WHERE embedding IS NULL`. It
  exists for one query: "what still needs embedding?" That query is the
  backfill *and* the steady state — see §3.4.

**`messages_fts` exists and is populated back to the first message ever sent**
(migration 1). Its triggers keep it current, and the insert trigger deliberately
skips `role = 'tool'`. Respect that judgement everywhere: tool output is the
assistant's scratch paper, and indexing it means your search index fills up
with the results of previous searches.

**`OllamaProvider.embed()` exists and has never been called.** It posts to
`/api/embed` with `self.embed_model`, default `nomic-embed-text`.

**`AnthropicProvider.embed()` raises on purpose.** There is no cloud embedding
path. Everything in §3 must call `router.local.embed(...)` directly rather than
going through `router.resolve()`, or one Ollama outage silently poisons your
vector space with vectors from a different model. See the gotcha in §3.4.

**`build_system_prompt()` says where facts go.** Its docstring: facts are
appended *after* the static preamble, "precisely so that editing memory
invalidates as little of the cached prefix as possible."

**The Memory page is drawn and unwired.** `components/Memory.jsx` renders
`FACTS`, `FADING`, `CORPORA`, `FACT_FILTERS` and `MEMORY_SETTINGS` out of
`lib/placeholder.js`, behind an `.unbacked` banner that says so on screen. That
file is your checklist: when nothing in `Memory.jsx` imports from it any more,
the page is done.

---

## 3. Phase 4 — recall

One skill the model can call: *look through everything I have ever said.*

### 3.1 Cut the scope first

Build it in this order and stop after any step with something that works:

1. **Keyword only.** `messages_fts` is populated *right now*. A skill that runs
   one `MATCH` query is about fifty lines and is immediately useful.
2. **Chunks, unembedded.** Fill the table, get the write path right.
3. **Embeddings.** Backfill, then steady state.
4. **Fusion.** Combine the two rankings.

Step 1 alone answers "when did I say anything about the boiler?" — which, on a
personal assistant, is most of what recall is for.

### 3.2 The chunker — `memory/chunking.py`

Pure functions. No database, no HTTP, no `async`. This is the part you can test
in a REPL and the part where mistakes are cheapest to find.

```python
"""Splitting something long into pieces worth embedding.

Paragraph-first, because a paragraph is already the unit a person wrote in and
a model reads in. Merged up to a target size, because a one-line paragraph
embeds to noise; overlapped, because the sentence that answers the question is
as likely to sit on a boundary as anywhere else.
"""

TARGET_CHARS = 1200   # ~300 tokens; comfortably inside any encoder's window
OVERLAP_CHARS = 150
MIN_CHARS = 40        # below this there is nothing to match on: "ok, thanks"

_PAGE = re.compile(r"^\[page (\d+)\]$", re.MULTILINE)


@dataclass(frozen=True)
class Piece:
    ordinal: int
    content: str
    page: int | None = None


def split(text: str) -> list[Piece]:
    ...
```

Three rules the code has to get right, each of which cost something to learn:

- **Carry the page number.** `extract.py` emits `[page N]` on its own line
  ahead of each page of a PDF. That marker is the only landmark a citation can
  use, and migration 4 gave `chunks` a `page` column for it. Parse it, attach
  it to every piece until the next marker, and strip it from the stored
  content — a chunk that begins `[page 4]` embeds four percent worse and reads
  badly when quoted back.
- **Never emit an empty or whitespace-only piece.** `content TEXT NOT NULL`
  will take `""` happily and it will match nothing forever.
- **Overlap by characters, not by "one paragraph back".** A single 4000-character
  paragraph is common — a pasted error log, a model's long answer — and a
  paragraph-based overlap degenerates to no overlap exactly there.

Test it with the ugliest thing you own: a PDF extraction with page markers, a
message that is one 5000-character line, a message that is three words.

### 3.3 The rows — `store.py`

Plain SQL against a table that already exists. Follow the file's conventions:
`_new_id("chk")`, `_now()` in milliseconds, a frozen dataclass with `to_dict()`.

```python
def add_chunks(self, pieces, *, message_id=None, attachment_id=None) -> int
def chunks_pending_embedding(self, limit: int = 256) -> list[StoredChunk]
def set_embedding(self, chunk_id: str, vector: bytes, *, dims: int, model: str) -> None
def all_embedded(self, model: str) -> list[StoredChunk]     # id, content, embedding
def messages_without_chunks(self, session_id: str | None = None) -> list[StoredMessage]
def search_messages_fts(self, query: str, limit: int = 20) -> list[sqlite3.Row]
```

Two notes on specific ones.

`chunks_pending_embedding` is the query `idx_chunks_pending` was built for.
Write it so the index is actually used — `WHERE embedding IS NULL`, matching
the partial index's predicate exactly.

`search_messages_fts` is a join back to the real table, because the FTS index
is external-content (`content='messages'`) and holds no columns of its own:

```sql
SELECT m.id, m.session_id, m.role, m.content, m.created_at,
       bm25(messages_fts) AS score
  FROM messages_fts f
  JOIN messages m ON m.rowid = f.rowid
 WHERE messages_fts MATCH ?
 ORDER BY score            -- bm25 is negative; lower is better
 LIMIT ?
```

**FTS5 `MATCH` takes a query language, not a sentence.** A user question
containing an apostrophe, a hyphen or the bare word `AND` raises
`sqlite3.OperationalError` and takes the turn with it. Quote every term:
`" ".join(f'"{t}"' for t in re.findall(r"\w+", query))`, and return an empty
list rather than raising when that leaves nothing.

### 3.4 Embedding — `memory/embedding.py`

```python
def pack(vector: Sequence[float]) -> bytes:
    """float32, little-endian, normalised.

    Normalised on the way in so cosine similarity is a plain dot product on the
    way out -- the alternative is dividing by two norms inside the hot loop,
    several thousand times per search, for a number that never changes.
    """
    array = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(array)
    return (array / norm if norm else array).tobytes()
```

**Add `numpy>=1.26` to `server/pyproject.toml`.** Migration 4's comment already
assumes it: "cosine against all of them is one numpy matmul in single-digit
milliseconds. sqlite-vec earns its place somewhere past a hundred thousand, and
not before." A few thousand chunks at 768 dims is about 9 MB held in memory and
a matmul you cannot feel. Do not reach for `sqlite-vec`; you do not have the
rows for it.

**Configuration.** Add `embed_model: str` to `Settings` (`EMBED_MODEL`, default
`nomic-embed-text`). Then notice the gap: `OllamaProvider.__init__` accepts
`embed_model` as a keyword argument, and `ProviderRouter` never passes it. Thread
it through `router.py` or the setting will be read and ignored, which is worse
than not having it.

The model has to be pulled separately — `ollama pull nomic-embed-text` — and
`run.sh` currently only checks for the chat model. A missing embedding model
comes back from Ollama as a 404 on the first backfill batch and nowhere else.

**Three rules about the encoder, all of which follow from `chunks.model`:**

1. **Always `router.local.embed(...)`, never `router.resolve()`.** The cloud
   provider raises `ProviderError("no embedding model on the cloud fallback
   path")` by design.
2. **Never mix models in one search.** Filter on `model = ?` in
   `all_embedded`. Changing `EMBED_MODEL` does not migrate anything — it makes
   every existing row unsearchable until it is re-embedded, which is exactly
   what migration 4's comment warned about.
3. **Failure leaves rows pending, and that is fine.** If Ollama is down, catch
   it, log it, leave `embedding` NULL. `idx_chunks_pending` means the next run
   picks them up, and until then search degrades to keyword-only rather than
   breaking.

### 3.5 One indexer, used for both backfill and steady state

The temptation is to write two things: a one-off backfill script, and a hook on
the write path. Write one thing instead.

```python
class Indexer:
    """Bring the chunk table level with the message table.

    Idempotent and interruptible, which is what lets the same call serve as the
    one-off backfill over five months of history and as the after-every-turn
    top-up. There is no "have I run this yet" flag to get wrong -- the answer
    is always in the tables.
    """

    async def catch_up(self, session_id: str | None = None) -> int:
        ...  # chunk what has no chunks, then embed what has no embedding
```

Call it from `api.py`, next to titling, following that precedent exactly:

```python
asyncio.create_task(_title_quietly(session_id))
asyncio.create_task(_remember_quietly(session_id))
```

with the same `try/except Exception` around it and the same reasoning: memory
is off the response path, and a failure to index must never break a turn that
already succeeded.

**What not to index.** Skip `role = 'tool'` — the FTS trigger already made this
decision and disagreeing with it creates a feedback loop where recall retrieves
its own previous output. Skip `role = 'system'`. Skip empty content, which is
what an assistant row looks like between `add_message` and `_persist`.

**That last one is a real race.** `run_turn` inserts the assistant message
empty and fills it in at the end. Indexing a session while a turn is in flight
will find a blank row; indexing it after `update_message` will find the answer.
Because `catch_up` is driven by "has no chunks", the empty row is skipped and
picked up on the next pass — provided you skip on empty content rather than
writing zero chunks and considering it done.

### 3.6 Search — `memory/search.py`

Two retrievers, one ranking.

```python
def cosine_top_k(query_vector: np.ndarray, rows, k: int) -> list[tuple[str, float]]:
    matrix = np.frombuffer(b"".join(r.embedding for r in rows), dtype=np.float32)
    matrix = matrix.reshape(len(rows), -1)
    scores = matrix @ query_vector      # both sides normalised: this is cosine
    top = np.argpartition(-scores, min(k, len(rows) - 1))[:k]
    return sorted(((rows[i].id, float(scores[i])) for i in top),
                  key=lambda pair: -pair[1])
```

Fusing the two is the only genuinely interesting decision here, and it is a
decision about *keys*, not about scores.

**The problem:** the vector side returns chunk ids. `messages_fts` returns
message ids. BM25 scores and cosine scores are not on a comparable scale, so
you cannot add them, and the two sides do not even rank the same kind of thing.

**The fix, in two parts.**

*Use one key.* Add an FTS index over `chunks` in migration 6, alongside the
facts table:

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  content, content='chunks', content_rowid='rowid'
);
-- plus ai/ad/au triggers, copied from migration 1's shape
```

Both retrievers then return chunk ids. It also buys you the thing `messages_fts`
structurally cannot do: **keyword search over attachment text**, since a chunk
of a PDF has no message row and so was never in `messages_fts` at all. That is
precisely the "what did the tenancy agreement actually say" case. It costs a
third copy of the text, which at this scale is megabytes.

Leave `messages_fts` exactly as it is. It is the right index for a future
"browse my conversations" screen — the green callout at the bottom of
`Memory.jsx` — which wants whole messages, not fragments.

*Use rank, not score.* Reciprocal rank fusion, which needs no calibration
between the two scales:

```python
K = 60   # damping; the standard value, and the results are not sensitive to it
fused[chunk_id] += 1 / (K + rank)
```

Take the top 5–8 after fusion.

### 3.7 The skill

`memory/skills/recall.py`, or `skills/recall.py` — one `Skill` subclass, same
shape as `Clock`, registered in `main.py`.

```python
class Recall(Skill):
    def __init__(self, store, indexer, router):
        super().__init__(
            name="search_history",
            description=(
                "Search everything the user has ever said in this and previous "
                "conversations, and the text of every document they have "
                "attached. Use it for anything about the user's own past: what "
                "they told you before, what a document said, when something "
                "happened. Prefer it over recalling from the current "
                "conversation alone."
            ),
            parameters={"type": "object", "properties": {
                "query": {"type": "string",
                          "description": "What to look for, in the user's own words."}},
                "required": ["query"]},
        )
```

Four things about the result string, all of which follow from how the
orchestrator treats it:

- **It is truncated at `MAX_RESULT_CHARS = 4000`, from the tail.** Emit the best
  match first. Five results at ~600 characters is the right budget; ten is not.
- **Date every hit.** `messages.created_at` is milliseconds; render as
  `DD-MM-YYYY`, which is what the system preamble tells the model to write.
- **Say when nothing matched, in a sentence.** "Nothing in the history matches
  'boiler'." A model handed an empty string will assume the tool is broken and
  either retry it or answer from imagination.
- **Never raise.** `_run_skill` catches everything, but its fallback message is
  a stack-trace summary. A skill that knows what went wrong should say so
  itself.

Register it unconditionally — unlike `WebSearch`, it needs no key, and an empty
history is a valid answer rather than a broken tool.

One system-preamble note: it already says *"anything about the present moment,
or about the user's own files and history, is worth looking up rather than
guessing"*. That sentence was written for this skill. Do not add another
naming it — the `tools` array is how the model learns the name, and duplicating
it costs tokens on every turn of every conversation forever.

---

## 4. Phase 5 — facts

Recall is a filing cabinet the model opens on request. Facts are the handful of
things it should never have to open a cabinet for: where you live, how you like
answers written, what you are in the middle of.

The design is already drawn, so read `Memory.jsx` and `placeholder.js` before
designing anything — they encode decisions worth keeping. A fact is **told** or
**inferred**; an inferred one carries a confidence and can be flagged for
confirmation; a fact can be **pinned** ("Keep always"); a fact can be **fading**,
shown greyed with "I'll let this fade once the job's done" and a *Keep it*
button.

### 4.1 Migration 6

```sql
CREATE TABLE memory_facts (
  id            TEXT PRIMARY KEY,
  text          TEXT NOT NULL,
  category      TEXT,                                  -- 'Home', 'How I answer', ...
  source        TEXT NOT NULL,                         -- told | inferred
  confidence    REAL NOT NULL DEFAULT 1.0,             -- 1.0 for told
  pinned        INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'active',        -- active | pending | fading
  message_id    TEXT REFERENCES messages(id) ON DELETE SET NULL,
  used_count    INTEGER NOT NULL DEFAULT 0,
  last_used_at  INTEGER,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);

CREATE INDEX idx_facts_active ON memory_facts(status, pinned DESC, updated_at DESC);
CREATE UNIQUE INDEX idx_facts_text ON memory_facts(text);
```

Every column here is answering something the drawn page asks for. Three are
worth arguing about:

**`ON DELETE SET NULL`, not `CASCADE`.** Everywhere else in this schema, deletes
cascade — dropping a session takes its messages, attachments and chunks with
it. A fact is the exception on purpose. "Prefers the short answer first" was
learned during some conversation, but it is not *about* that conversation, and
deleting a chat from July should not silently change how the assistant writes
in September. The provenance link goes null; the fact stays. If you want the
other behaviour, make it an explicit action on the Memory page ("forget
everything I learned here"), not a side effect of tidying the sidebar.

**`UNIQUE` on `text`.** The extraction pass in §4.3 will propose "Lives in
Leeds" on four separate occasions. Let SQLite refuse the duplicate — an
`INSERT ... ON CONFLICT(text) DO UPDATE SET updated_at = ...` is a fact being
*reinforced*, which is exactly the right semantics and costs one clause.

**`status = 'pending'`** is what the *"Ask before saving anything"* toggle
produces: extracted, stored, not yet used in a prompt, waiting for a tap on the
page. Without it that toggle has nowhere to put its output.

And a small key/value table, because two features now want one:

```sql
CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

`MEMORY_SETTINGS` needs somewhere to live across restarts. So, eventually, does
`Registry.set_enabled`, whose comment currently reads: *"Persisting this means a
table, and that is a decision about user data rather than about code that ships
with the server."* This is that table, and skills can move into it later without
another migration.

### 4.2 Injecting facts into the prompt

This is a four-line change in `build_system_prompt` and one performance trap.

```python
def build_system_prompt(self) -> str:
    facts = self.store.active_facts(limit=MAX_FACTS)
    if not facts:
        return self.settings.system_preamble
    lines = "\n".join(f"- {f.text}" for f in facts)
    return (
        f"{self.settings.system_preamble}\n\n"
        f"What you know about the user:\n{lines}"
    )
```

**The trap:** `build_window` calls `build_system_prompt()` twice — once to
charge it against the budget, once to build the system message. Today that is
an attribute read. After this change it is two queries per turn, and the two
calls can disagree if a fact is edited between them, which charges the window
for a prompt it did not send. Compute it once at the top of `build_window` and
pass it down.

**Order the facts stably.** Pinned first, then `created_at` ascending — *not*
`updated_at`, which reorders the list every time a fact is reinforced and
throws away the cached prefix for no gain. The whole reason facts come after
the preamble is to keep the churn confined to the tail.

**Cap it, hard.** `MAX_FACTS = 40`, and truncate individual facts to ~200
characters. This text is prepended to every request forever, on the same budget
the conversation is competing for. A memory system that quietly eats a third of
a 32K window is a regression dressed as a feature — and `build_window` will
respond by trimming turns, so the visible symptom is "it forgot what I said two
messages ago" and the cause is memory.

**Increment `used_count` / `last_used_at`** when facts go into a prompt — that
is what "12 answers" under a fact on the drawn page is reporting. Do it in a
single batched `UPDATE ... WHERE id IN (...)`, off the response path, not one
statement per fact per turn.

### 4.3 The curation pass

Where facts come from. Two sources; build them in this order.

**Told, first.** A `remember` skill: the model calls it when the user says
"remember that…" or states something plainly durable. This is a hundred lines,
it is exact, `source = 'told'`, `confidence = 1.0`, and it is the half of the
feature people actually notice. Give it a sibling `forget(query)` so the
assistant can act on "forget what I said about the plumber" in the turn where
it is asked, rather than directing the user to a settings page.

**Inferred, second.** After a turn, off the response path, ask the model to
propose facts from the last exchange:

```
Read the exchange below. List anything durably true about the user that
would still be worth knowing in a month. Facts about the user, not about
the topic. Nothing that is already in the list of known facts. Reply with
a JSON array of {"text", "category", "confidence"}, at most three items,
or [] if there is nothing.
```

Rules that keep this from becoming the worst part of the system:

- **Run it on a cadence, not every turn.** Every fifth user message, or on
  session close. It is a whole extra generation; doing it per turn doubles the
  work the GPU does and delays nothing visible, right up until it delays
  everything.
- **Never extract from the assistant's own text.** The model's speculation
  about you, fed back in as something it knows about you, is how a memory
  system develops opinions nobody expressed.
- **`confidence < 0.7` gets `check = true` on the page** — the drawn "Looks
  right?" affordance. Below 0.4, discard rather than store.
- **A malformed JSON reply is a no-op.** Log it, drop it, move on. This runs
  unattended and must never be a source of exceptions.
- **Respect the toggle.** *"Ask before saving anything"* on means `status =
  'pending'`. *"Remember between chats"* off means the pass does not run and
  §4.2 injects nothing — which must actually be checked in both places, not
  just hidden in the UI.

**Fading** is the cheapest possible decay and does not need a scheduler: an
unpinned, `source = 'inferred'` fact with `last_used_at` older than 30 days and
`used_count` under 3 renders as fading and stops being injected. Compute it in
the query. *Keep it* sets `pinned = 1`. If you find yourself writing a
background job for this, you have overbuilt it.

### 4.4 The endpoints

`api.py`, in the style of the existing `/skills` block — Pydantic model, one
route each, `HTTPException` with a full sentence on failure.

| Route | Notes |
|---|---|
| `GET /api/memory` | `{facts, settings, corpora}` — one call, one page render |
| `POST /api/memory` | Manual add. `source = 'told'`. |
| `PATCH /api/memory/{id}` | `text`, `pinned`, `status`. Covers *Edit* and *Keep it*. |
| `DELETE /api/memory/{id}` | *Forget*. Actually delete — a tombstone would be a lie on a page titled "What I remember". |
| `PATCH /api/memory/settings` | The three toggles |
| `POST /api/memory/forget-all` | Requires an explicit confirmation field in the body |
| `GET /api/memory/export` | *Download all*. JSON, `Content-Disposition: attachment`. |

`corpora` is the Documents column, and it is a `GROUP BY` over `attachments`
rather than a new concept: a count, a most-recent date, and the names. Do not
build a document-collections feature to fill it; the drawn card wants a
summary, and the summary is a query you can already write.

### 4.5 The client

Last, always. Never guess at the server's shape from the client side.

1. **`lib/api.js`** — the methods above, on the object `createApi` returns.
2. **`hooks/useMemory.js`** — copy `useSkills.js` wholesale. It already has the
   three things this needs: `loading` starting `true` so the page says
   "checking" rather than flashing "nothing here", the `live` flag for
   StrictMode's double mount, and the optimistic-update-with-rollback pattern
   that the *Forget* button wants.
3. **`components/Memory.jsx`** — swap the `placeholder` import for the hook and
   delete the `.unbacked` banner and the `unbacked-body` class.
4. **`lib/placeholder.js`** — delete `FACTS`, `FADING`, `CORPORA`,
   `FACT_FILTERS` and `MEMORY_SETTINGS`. That file's own comment says it is the
   checklist; leaving dead exports in it defeats the point.

**Map display shapes in the hook, not on the server.** `Memory.jsx` wants
`{from, when, used, check}`; the table holds `{source, created_at, used_count,
confidence}`. The server should never emit the string `"4 Jul"` or
`"12 answers"` — those are a rendering decision, they are locale-dependent, and
the export in §4.4 wants the raw numbers. One `toFact()` in the hook, and the
API stays honest.

`FACT_FILTERS` becomes `SELECT DISTINCT category`, with *All* and *Fading*
prepended — the filters are data now, not a constant.

**Nothing on this page needs an SSE frame.** Add one only if you want a fact
appearing live mid-turn, and think hard first: a memory that visibly writes
itself down while you are still typing is unsettling in a way the mock does not
convey.

---

## 5. Checking it without the browser

Same throwaway database as `extending.md` §6, so experiments never touch
`data/chat.db`:

```bash
DB_PATH=/tmp/scratch.db AUTH_TOKEN=devtoken BIND_PORT=8091 .venv/Scripts/python -m app
```

A fresh `DB_PATH` runs all six migrations from scratch, which is how you check
that migration 6 actually applies.

**The chunker, with no server at all** — the whole reason it is pure:

```bash
.venv/Scripts/python -c "
from app.memory.chunking import split
print([(p.ordinal, p.page, p.content[:60]) for p in split(open('README.md').read())])"
```

**The backfill, against a copy of the real database** — this is where you find
out whether five months of history chunks into three thousand rows or thirty
thousand. `create_app` currently hangs only `settings` and `db` off
`app.state`; hang the indexer and the orchestrator there too, and both of these
one-liners work without a running server:

```bash
cp data/chat.db /tmp/backfill.db
DB_PATH=/tmp/backfill.db .venv/Scripts/python -c "
import asyncio; from app.main import create_app
asyncio.run(create_app().state.indexer.catch_up())"
```

**Search, before any model is involved.** Ranking bugs are invisible through a
chat: the model paraphrases a bad result into a plausible sentence and you
learn nothing. Print the top 8 with their scores and read them yourself.

**The skill, through the real loop:**

```bash
curl -N -H "Authorization: Bearer devtoken" -H "Content-Type: application/json" \
  -d '{"message":"what did I say about the boiler?"}' \
  http://127.0.0.1:8091/api/chat
```

You want `event: tool_call` naming `search_history`, then `event: tool_result`,
then deltas. If the model answers without the tool call, that is a description
problem in §3.7, not a retrieval problem — fix the sentence, not the ranking.

**Confirm the prompt is what you think it is.** Facts silently not being
injected looks exactly like facts being injected and ignored:

```bash
.venv/Scripts/python -c "
from app.main import create_app
print(create_app().state.orchestrator.build_system_prompt())"
```

**Start the tests here.** There are still none anywhere in the project, and
this feature is the best argument for them the repo has produced: `chunking`,
`embedding.pack` and the RRF fusion are pure functions with fiddly edge cases,
no fixtures, no database and no event loop. The first test is five lines.

---

## 6. The order, and where to stop

| | Ships | Worth it? |
|---|---|---|
| 1 | Keyword-only `search_history` over `messages_fts` | Yes. Half a day, immediately useful. |
| 2 | Chunking + write path + backfill | Yes. Nothing else works without it. |
| 3 | Embeddings + cosine + RRF | Yes, once you have a corpus to test against. |
| 4 | `remember` / `forget` skills, facts in the prompt | Yes. The visible half of "one memory". |
| 5 | The Memory page, wired | Yes. Memory you cannot inspect is memory you cannot trust. |
| 6 | Automatic extraction | Only after 1–5 have been *lived with*. |
| 7 | Compaction of the middle of a long window | Later, and separately. |

Six is last for a reason: a system that saves things about you unprompted is
the one part of this that can be actively unpleasant when it is wrong, and you
cannot tune it before you know what the corpus looks like.

Seven is on this list only because `build_window`'s docstring promises it —
"real compaction (summarise the middle, keep head and tail)" — and the README
files it under phase 5. It is a different feature with a different failure mode.
Do not let it ride along.

---

## 7. What to watch out for

Collected from the sections above, because these are the ones that will
actually cost you an evening.

- **Migrations 1–5 have run.** Anything you edit in them will never be applied,
  and you will debug a table that does not match its own source code.
- **Never index `role = 'tool'`.** Recall retrieving its own previous output is
  a loop that degrades slowly enough to look like a model problem.
- **Embeddings only ever come from `router.local`.** The cloud provider raises,
  and mixing encoders in one table is unrecoverable without a full re-embed.
- **FTS5 `MATCH` is a query language.** Unquoted user text raises
  `OperationalError` on an apostrophe.
- **`build_system_prompt()` is called twice per turn** and is about to become a
  query.
- **The facts block competes with the conversation for the same window.** Every
  fact you inject is a turn you trim.
- **Assistant rows are empty between insert and persist.** Skip on empty
  content, or you will index blanks and never revisit them.
- **The skill result is truncated from the tail at 4000 characters.** Best match
  first.
- **Deleting a session takes its chunks (cascade) but not its facts (set
  null).** That asymmetry is deliberate; write it in a comment where the next
  person will find it.
- **Nothing here leaves the machine.** Embeddings are generated locally,
  vectors live in the same SQLite file the messages do, and `VACUUM INTO` keeps
  backing all of it up in one file. That property is the entire point of the
  project — do not let a convenient hosted embedding API undo it.
