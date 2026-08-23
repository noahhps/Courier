# unified-llm

One conversation history, one memory, identical from every device. Inference
runs on your own hardware; the phone and laptop are thin clients that hold no
durable state.

Phases 1–3 of the architecture are built: the pipe, persistence, and the PWA.
Retrieval, memory curation, and the cloud-fallback UI are not.

```
phone / laptop (PWA)  ────── HTTP on the LAN ──────▶  PC :8080  ──▶  Ollama 127.0.0.1:11434
                                                    │
                                                 SQLite (WAL, FTS5)
```

---

## Running it on the PC

The short way, once a model is pulled:

```bash
./run.sh
```

That checks the virtualenv and installs the server if it is missing, installs
and builds the client, reports whether Ollama is up and whether the configured
model is actually pulled, refuses to start if something already holds the port,
and then serves everything on :8080. `./run.sh --dev` additionally runs Vite on
:5173 with hot reload, and stops both on Ctrl+C. `--no-build`, `--port` and
`--model` are there too; `--help` lists them.

It deliberately does not start Ollama, which is a system service with its own
lifecycle -- guessing at that is how you end up running two of them.

The same steps by hand:


Do this on the machine with the GPU. Everything below assumes Windows with the
RTX 5070 Ti, but the commands are the same on macOS and Linux.

**1. Pull a model.** A 14B-class dense model at Q4 fits 16 GB with room for a
32K KV cache:

```bash
ollama pull qwen3:14b
```

**2. Build the client.** The UI is a React app; the server serves the build
output, not the source:

```bash
cd client && npm install && npm run build
```

**3. Install and start the server:**

```bash
python -m venv .venv && .venv/Scripts/pip install -e ./server
.venv/Scripts/python -m app
```

It prints the URL and the access token. The token is written to `data/token`
and reused across restarts, so you enter it on each device once.

If `client/dist` is missing the server still starts and says so — the API
works, there is just no UI at the root until you run the build.

**4. Keep Ollama private.** It must never be reachable from the network — the
API server is the only thing that talks to it. Confirm it is bound to loopback:

```bash
curl http://127.0.0.1:11434/api/tags   # works
```

Set `OLLAMA_HOST=127.0.0.1` if anything has changed it. Also set
`OLLAMA_KEEP_ALIVE=-1` so the model stays resident and you don't pay a cold
load on the first message of the day.

---

## Reaching it from the iPhone

Both devices have to be on the same network, and the server has to be listening
on an address the phone can dial.

**1. Find the PC's address on the LAN.** `ipconfig` on Windows, `ip addr` on
Linux — the `192.168.x.y` or `10.x.y.z` one. Give it a DHCP reservation in the
router if you don't want it moving.

**2. Bind to that address** rather than loopback:

```bash
BIND_HOST=192.168.1.50 .venv/Scripts/python -m app
```

`0.0.0.0` works too and listens on every interface, which is one fewer thing to
edit when the address changes and one more network you may not have meant to
serve — on a laptop that follows you around, it is whatever wifi you joined.

**3. Open it on the phone** at `http://192.168.1.50:8080` and enter the token
once.

**4. Install it to the home screen.** Safari → Share → *Add to Home Screen*. It
launches standalone, without browser chrome, and the service worker caches the
shell so it opens instantly. Conversation data is never cached — the server is
the source of truth, so a wiped phone loses nothing.

**5. Understand what is holding the door.** The bearer token is the only thing
in front of the API now: anything that can reach port 8080 gets as many guesses
as it likes. On a home LAN that is your own devices and whatever else is on the
wifi. Raise `TOKEN_LENGTH` if that set is larger than you'd like, and add a
firewall rule if only some machines should reach the port.

> **This is plain HTTP.** Requests cross the LAN in the clear, including the
> token. That is usually fine on a network you control and is not fine on one
> you don't — never expose this port to the internet as it stands. For a real
> certificate (and the padlock), put a reverse proxy such as
> Caddy in front of it and let that terminate TLS.

---

## Attachments

Files can be attached to a message. What happens to one depends on the only
thing that matters — what a model can do with it:

| | | |
|---|---|---|
| **Images** | PNG, JPEG, GIF, WebP | Sent as images, to a model that can see |
| **Documents** | PDF, `.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods`, `.odp` | Read for their text at upload |
| **Text and code** | `.txt`, `.md`, `.csv`, `.json`, `.py`, and the rest | Pasted into the prompt as-is |

Anything else is refused in the composer with a reason, rather than accepted
and quietly ignored later.

Files can be dropped anywhere on the window — the composer is what lights up,
since that is where they land — or picked with the paperclip.

Attached bytes live in SQLite beside the messages, so `VACUUM INTO` still
copies everything in one file and deleting a conversation takes its files with
it. Images are re-encoded to PNG on the way in when the local runner cannot
read the original — it rejects WebP — and transparency is flattened onto white,
without which a transparent screenshot arrives as a black rectangle. Documents
are extracted once, at upload, so a PDF with no text layer is refused while
you are still looking at the composer rather than two turns later.

Only the four most recent images travel with a request; older ones remain in
the transcript as `[earlier image: name]`. Resending every picture in a long
conversation is expensive, and a small vision model handed six of them answers
about the wrong one.

> **Vision needs a model that actually has it.** Some models advertise the
> `vision` capability without shipping a projector, accept the image, and then
> describe something that was never there. If answers about pictures are
> confidently wrong, check `ollama show <model>` for a projector before
> suspecting anything else.

## Configuration

All environment variables, all optional.

| Variable | Default | Notes |
|---|---|---|
| `BIND_HOST` | `127.0.0.1` | Used verbatim. A LAN address (or `0.0.0.0`) lets other devices in. |
| `BIND_PORT` | `8080` | |
| `AUTH_TOKEN` | generated | Written to `data/token` on first run. |
| `TOKEN_LENGTH` | `14` | Characters, from an alphabet with no `0/O/1/l/I`. |
| `DB_PATH` | `data/chat.db` | |
| `CLIENT_DIR` | `client/dist` | The built UI. Missing means API-only. |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | |
| `OLLAMA_MODEL` | `qwen3:14b` | |
| `OLLAMA_THINK` | `medium` | Default gpt-oss reasoning effort: `low`, `medium`, or `high`. |
| `CONTEXT_TOKENS` | `32768` | |
| `REPLY_TOKENS` | `2048` | Headroom reserved for the answer. |
| `SYSTEM_PREAMBLE` | see `config.py` | Kept static — it is the cacheable prefix. |
| `ANTHROPIC_API_KEY` | unset | Enables cloud fallback. `pip install -e "./server[cloud]"`. |

### gpt-oss reasoning effort and `OLLAMA_THINK`

gpt-oss accepts `low`, `medium`, or `high` for its reasoning effort. The
composer's slider selects that level for each message; `OLLAMA_THINK` provides
the server default for API callers that do not send one. Higher levels take
longer and use more tokens. Reasoning arrives on Ollama's separate `thinking`
channel, so it can be shown live without being stored with the answer.

---

## Backups

Section 7's answer to SQLite corruption. `VACUUM INTO` produces a consistent
copy of a live database:

```python
from app.db import Database
from pathlib import Path
Database(Path("data/chat.db")).backup_to(Path("backups/chat-2026-08-13.db"))
```

Schedule it nightly with Task Scheduler. How that copy gets off the machine
without defeating the point of local-only is still an open decision (§10).

---

## Layout

```
server/app/
  config.py        env only; token persistence
  db.py            WAL, user_version migrations, VACUUM INTO
  store.py         sessions and messages
  orchestrator.py  §6 request lifecycle
  api.py           HTTP surface
  providers/
    base.py        ModelProvider protocol — the seam, in place from day one
    ollama.py      default
    anthropic.py   cloud fallback, lazily imported
    router.py      local first, cached health, explicit override
client/            React, built by Vite; no CDN, no runtime dependencies
  src/
    App.jsx        auth phases, drawer, wiring
    hooks/         useChat (the turn), useSessions (the list)
    lib/api.js     bearer token, SSE-over-fetch
    lib/markdown.js  the renderer -- escapes before it emits a single tag
    components/    gate, top bar, drawer, message list, composer
  public/          copied verbatim: service worker, manifest, icons
  dist/            the build output; this is what the server serves
```

## Working on the UI

```bash
cd client && npm run dev
```

Vite serves the UI on :5173 and proxies `/api` to the server on :8080, so the
dev UI talks to the real thing — real token, real streaming, real history.
Run the Python server alongside it as usual. `npm run build` when you're done;
the server only ever reads `dist/`.

## Not built yet

Phase 4 (hybrid FTS5 + `sqlite-vec` retrieval behind a tool), Phase 5
(`memory_facts`, the curation pass, the cap, an editing page), Phase 6 (model
picker and per-session override). The FTS5 index and its triggers already run,
so search will cover history back to the first message.
