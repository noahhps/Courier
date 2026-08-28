# Memory

The README promises "one conversation history, one memory". This is the
memory: what it stores, what it costs, and the decisions that are easy to
undo by accident.

Read `extending.md` first for the map of the server. This feature enters at
all three of its altitudes at once.

---

## 1. Three things, not one

| | What it is | Where it lives | Costs |
|---|---|---|---|
| **The window** | The last N turns, verbatim | `orchestrator.build_window` | The bulk of every prompt |
| **Recall** | Search over everything ever said | `chunks`, `chunks_fts`, `search_history` | Nothing until called |
| **Facts** | A short curated list the model always sees | `memory_facts` | ~1 line per fact, every turn |

They are not alternatives. The window is what the model is *reading*, recall
is what it can *look up*, facts are what it *knows without asking*. A person
has all three.

The distinction that matters in practice: **facts are paid for on every turn
of every conversation forever, recall is paid for only when the model decides
to search.** That asymmetry is why facts are capped hard and recall is not.

---

## 2. Recall

### What happens to a message

```
turn ends
  api._remember_quietly          (asyncio task, off the response path)
    indexer.catch_up(session_id)
      messages_needing_chunks    -> chunking.split()  -> chunks
      chunks_pending_embedding   -> local.embed()     -> chunks.embedding
    curator.run(...)             (every 5th user turn; §3)
```

The same `catch_up()` runs at boot with no session id, over everything. It is
idempotent and driven entirely by what is in the tables — "has no chunks", "has
no embedding" — so there is no completion flag to get wrong, and an interrupted
backfill simply resumes.

### What is deliberately never indexed

- **`role = 'tool'`** — skill output. Migration 1's FTS trigger already made
  this call; indexing it means recall retrieves the results of previous
  recalls, which degrades slowly enough to look like a model problem.
- **`role = 'system'`** — the preamble, which matches everything.
- **Empty content** — an assistant row between `add_message` and the
  `_persist` at the end of a turn. Skipped rather than chunked-as-nothing, so
  the next pass picks it up once there is an answer in it.

### The two retrievers

`chunks_fts` (BM25) and cosine over `chunks.embedding`. Both return chunk ids,
which is the point of adding `chunks_fts` in migration 6 rather than reusing
`messages_fts`: the vector side only ever returns chunks, and two rankings
cannot be combined unless they name the same things. It also reaches
**attachment text**, which has no message row and so was never in
`messages_fts` at all — the "what did the tenancy agreement actually say" case.

`messages_fts` is untouched and still current. It is the right index for
browsing whole conversations, which wants messages rather than fragments.

They are fused by **reciprocal rank fusion** — combining positions, not scores,
because BM25 (negative, unbounded) and cosine (−1..1) share no scale. An id
near the top of both lists beats one that tops only a single list.

### The similarity floor, which is the part worth understanding

`MEMORY_MIN_SIMILARITY` (default **0.35**) is not a tuning nicety. Without it:

> Nearest-neighbour search always returns neighbours. Ask about something the
> history does not cover and it hands back the least-unrelated passages in the
> corpus, confidently ranked. Fusion then discards the scores, so nothing
> downstream can tell a good match from the best of a bad lot — and
> `search_history` reports them to the model as "the best passages from the
> user's history".

That is a confident wrong answer with a citation. The floor is what makes
"Nothing in the history matches" reachable.

**It is encoder-dependent and worth checking once against your own.** Ask
something the history definitely does not cover and look at what comes back.
If anything does, raise it. Too high is the safe direction: recall falls back
to keywords rather than inventing relevance.

### Embeddings

`nomic-embed-text` by default, over Ollama's `/api/embed`, pulled separately:

```bash
ollama pull nomic-embed-text
```

Three rules, all of which follow from `chunks.model` existing:

1. **Always `router.local`, never `router.resolve()`.** The cloud provider
   raises `ProviderError("no embedding model on the cloud fallback path")` by
   design; routing around it would fill the table with vectors from a
   different encoder.
2. **Never mix encoders in one search.** `embedded_chunks` filters on `model`.
   Changing `EMBED_MODEL` does not migrate anything — it makes existing rows
   unfindable until they are re-embedded.
3. **Failure leaves rows pending, and that is fine.** No embedding model
   means `embedding IS NULL`, `idx_chunks_pending` finds them next pass, and
   search runs on keywords alone. A worse answer, not a broken one.

The nomic family is trained with task prefixes and gets them here:
`search_document: ` on stored passages, `search_query: ` on the query. Without
them both sides land in the same region of the space and the ranking flattens.
Applied by model name, because prefixing a model not trained on them is worse
than not.

---

## 3. Facts

### Where they come from

**Told** — the `remember` skill, at confidence 1.0. Exact, because the user
said it in so many words. `forget` is its pair, so "forget what I said about
the plumber" can be answered in the turn it is asked rather than by directing
someone to a settings page.

**Inferred** — `memory.facts.Curator`, every fifth user turn
(`MEMORY_EXTRACT_EVERY`, 0 to disable). Guardrails, each guarding a specific
failure:

- **Reads only the user's messages.** The model's speculation about you, fed
  back as something it knows about you, is how a memory system develops
  opinions nobody expressed.
- **Three per pass, minimum confidence 0.4.** Below 0.7 the page shows "Looks
  right?".
- **Every malformed reply is `[]`.** Fenced JSON, narrated JSON, prose, a bare
  object, an unparseable confidence — `facts.parse` is total, because this
  runs unattended after a turn that already succeeded.
- **"Ask before saving anything" (default on)** stores them `pending`, so
  nothing inferred reaches a prompt until a person agrees.

### What they cost

They are appended **after** the static preamble, never inside it: the preamble
is the cacheable prefix, so appending confines the churn to the tail.

Ordered pinned-first then **`created_at`** — never `updated_at`, which would
reshuffle the list every time a fact is reinforced and throw the cached prefix
away for a change nobody made.

Capped at `MEMORY_MAX_FACTS` (40) facts of `MEMORY_FACT_CHARS` (200) each.
Roughly 2k tokens at the ceiling, on the same budget the conversation is
competing for. Raise it and `build_window` responds by trimming turns — so the
symptom of too much memory is *"it forgot what I said two messages ago"*.

### Fading

Derived, not stored, and needing no scheduler: an **inferred**, **unpinned**
fact with fewer than 3 uses that has not been touched in 30 days stops being
injected and shows greyed on the page with *Keep it*. Computed identically in
SQL (`active_facts`) and in Python (`StoredFact.fading`) so the prompt and the
page agree.

### Deleting a conversation does not delete its facts

Every other foreign key in this schema cascades — dropping a session takes its
messages, attachments and chunks, because those *are* the conversation. A fact
is not. "Prefers the short answer first" was learned during some conversation
but is not about it, and tidying the sidebar in September must not silently
change how the assistant writes in October.

`memory_facts.message_id` is therefore `ON DELETE SET NULL`. Provenance is
lost; the fact stays.

---

## 4. Operating it

| Variable | Default | Notes |
|---|---|---|
| `EMBED_MODEL` | `nomic-embed-text` | Pull separately. Changing it orphans existing vectors. |
| `MEMORY_MIN_SIMILARITY` | `0.35` | Encoder-dependent. See §2. |
| `MEMORY_MAX_FACTS` | `40` | Facts in the system prompt. |
| `MEMORY_FACT_CHARS` | `200` | Per fact. |
| `MEMORY_EXTRACT_EVERY` | `5` | User turns between curation passes; 0 disables. |

The three switches on the Memory page live in `app_settings`, not the
environment: they are user data, and `MEMORY_DEFAULTS` in `memory/__init__.py`
says what "unset" means. *Share across projects* is stored and currently inert
— there are no projects yet.

**Endpoints** — `GET /api/memory` (facts, settings, corpora, index size),
`POST /api/memory`, `PATCH|DELETE /api/memory/{id}`,
`PATCH /api/memory/settings`, `POST /api/memory/forget-all` (requires
`confirm: true`), `GET /api/memory/export`, `POST /api/memory/reindex`.

> `/memory/settings` is declared **before** `/memory/{fact_id}`. A path
> parameter matches anything, and with the routes the other way round PATCHing
> a switch returns "no such fact". This was a real bug, found by calling it.

**Checking it without a browser:**

```bash
# The pure parts. No fixtures, no database, no event loop.
cd server && pytest

# What the model will actually be told it knows.
DB_PATH=data/chat.db .venv/bin/python -c "
from app.main import app; print(app.state.orchestrator.build_system_prompt()[0])"

# Backfill against a copy, to see what months of history actually costs.
cp data/chat.db /tmp/backfill.db
DB_PATH=/tmp/backfill.db .venv/bin/python -c "
import asyncio; from app.main import app
print(asyncio.run(app.state.indexer.catch_up()))"
```

Use `from app.main import app`, not `create_app()`: `main.py` constructs one at
import time, so calling the factory again builds a second app and opens a
second connection to the same file.

Search is worth reading directly rather than through a chat — the model will
paraphrase a bad result into a plausible sentence and you will learn nothing.
If the model answers without calling the skill at all, that is a description
problem in `skills/recall.py`, not a ranking problem.

---

## 5. Things that will bite

- **Never edit a migration that has run.** The database is at 6. Append.
- **The similarity floor is the difference between "I don't know" and a
  confident fabrication.** Verify it against your own encoder.
- **`build_system_prompt()` is called once per turn and returns a tuple.** It
  used to be called twice inside `build_window` — free as an attribute read,
  two queries now, and able to disagree with itself between the two calls.
- **Facts compete with the conversation for the window.** Every fact injected
  is a turn trimmed.
- **Nothing here leaves the machine.** Embeddings are generated locally,
  vectors live in the same SQLite file as the messages, and `VACUUM INTO`
  still backs up everything in one file. Do not let a convenient hosted
  embedding API undo the entire point of the project.
