"""One process: API, static client, SQLite, provider routing."""

from __future__ import annotations

import asyncio
import os
import signal
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import build_router
from .auth import make_auth_dependency
from .config import Settings, load_settings, write_secret
from .db import Database
from .mcp import MCPManager
from .memory.facts import Curator
from .memory.indexer import Indexer
from .orchestrator import Orchestrator
from .providers import OAuthFlows, ProviderError, ProviderRouter, model_setting_key
from .skills.calendar import AddEvent, FindEvents, ListEvents, UpdateEvent
from .device.mac_calendar import available as device_calendar_available
from .device.mac_photos import available as device_photos_available
from .skills.device_calendar import AddDeviceEvent, FindDeviceEvents, ListDeviceEvents
from .skills.device_photos import AddToAlbum, CreateAlbum, ListAlbums, ListPhotos
from .skills.files import ListDirectory, ReadFile, SearchFiles
from .skills.clock import Clock
from .skills.recall import Recall
from .skills.registry import Registry
from .skills.remember import Forget, Remember
from .skills.websearch import WebSearch
from .store import Store


class ShellStatic(StaticFiles):
    """Cache policy for the built client.

    The bundler stamps a content hash into every filename under `assets/`, so
    those are immutable: a new build asks for a new URL, and the old one can
    sit in the phone's cache forever.

    Everything else keeps its name across deploys and gets `no-cache` -- not
    "don't cache" but "revalidate before reusing". Without it the browser
    applies heuristic freshness to the entry document, and a deployed change
    can sit behind a stale copy for hours with no way to force it from the
    phone. The files are local and tiny; a conditional request costs nothing.
    """

    def file_response(self, full_path, *args, **kwargs) -> FileResponse:
        response = super().file_response(full_path, *args, **kwargs)
        immutable = Path(full_path).parent.name == "assets"
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if immutable else "no-cache"
        )
        return response


def _signin_page(heading: str, detail: str, *, ok: bool) -> HTMLResponse:
    """The one page this server renders that is not the client.

    Deliberately plain: it is on screen for a second or two, in a tab the
    reader is about to close, and it has no stylesheet to fetch because a
    browser that has never loaded this app before is a browser with nothing
    cached. Both strings are escaped -- the detail can be text OpenRouter
    wrote.
    """
    from html import escape

    tint = "#2f7d4f" if ok else "#a2452f"
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(heading)}</title>
<body style="margin:0;display:grid;place-items:center;min-height:100vh;
             background:#f6f5f3;color:#14171d;
             font:400 15px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif">
  <main style="max-width:30rem;padding:2rem;text-align:center">
    <h1 style="font-size:1.15rem;margin:0 0 .5rem;color:{tint}">{escape(heading)}</h1>
    <p style="margin:0;color:#5c6270">{escape(detail)}</p>
  </main>
  <script>
    // Only closes a tab this app opened itself, which is the one this is.
    // A tab the browser opened some other way simply stays put.
    if (window.opener) setTimeout(() => window.close(), 1200);
  </script>
</body>""",
        status_code=200 if ok else 400,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    store = Store(db)
    providers = ProviderRouter(settings)
    # A model chosen in the picker outlives the process it was chosen in. The
    # environment still sets the starting point; this is what someone actually
    # picked, so it wins over the default and loses to nothing.
    #
    # Applied without checking that the model still exists: Ollama may not have
    # it pulled this boot and OpenRouter may have retired it, and either way
    # the honest failure is the one that names the model at the moment it is
    # used, not a silent reversion to something else on startup.
    for provider_id in providers.by_id:
        saved = store.get_text_setting(model_setting_key(provider_id))
        if saved:
            try:
                providers.set_model(provider_id, saved)
            except (KeyError, ValueError):
                pass
    # Sign-ins in flight. In memory and per-process, because a half-finished
    # one is worth nothing after a restart -- see providers/openrouter_oauth.py.
    oauth = OAuthFlows()
    # Built fresh each boot: skills are code that ships with the server, so
    # there is nothing to load and nothing to persist. Built *before* the
    # orchestrator, which needs it to tell the model what it can call.
    indexer = Indexer(settings, store, providers)
    curator = Curator(settings, store, providers)
    registry = Registry()
    registry.register(Clock())
    # The device's own files. Read-only, and only inside settings.device_roots
    # -- `Registry.enabled` hides them entirely when no root is configured, so
    # an unshared machine never offers the model a folder it cannot open.
    registry.register(ListDirectory(settings))
    registry.register(ReadFile(settings))
    registry.register(SearchFiles(settings))
    # The calendar, from whichever source this machine actually has.
    #
    # One or the other, never both: they answer to the same three names, and a
    # model offered two `add_event`s would be choosing between a private table
    # and the user's real calendar without being told which is which. Where
    # EventKit exists the real calendar wins, because a calendar only Courier
    # can see is one it will be confidently wrong about.
    #
    # The private table stays as the fallback for Windows and Linux, and its
    # rows are migrated by `tools/migrate_calendar.py` before it goes.
    if device_calendar_available():
        registry.register(ListDeviceEvents())
        registry.register(FindDeviceEvents())
        registry.register(AddDeviceEvent())
    else:
        registry.register(AddEvent(store))
        registry.register(UpdateEvent(store))
        registry.register(ListEvents(store))
        registry.register(FindEvents(store))

    # The photo library. No private fallback exists or should: there is no
    # useful sense in which Courier could keep its own photos.
    if device_photos_available():
        registry.register(ListPhotos())
        registry.register(ListAlbums())
        registry.register(CreateAlbum())
        registry.register(AddToAlbum())
    # Registered unconditionally, unlike web search: these need no key, and an
    # empty history is a valid answer rather than a broken tool.
    registry.register(Recall(indexer))
    registry.register(Remember(store, max_chars=settings.memory_fact_chars))
    registry.register(Forget(store))
    # Registered only when configured. An unconfigured search that announced
    # itself and then refused would be the same failure as a system prompt
    # promising a tool the request never declares: the model spends the turn
    # reaching for something that was never there.
    web_search = WebSearch(settings.search_api_key, endpoint=settings.search_endpoint)
    registry.register(web_search)
    
    # Register skill creator to help with skill management

    # Database-backed MCP manager: dynamically loads tools from mcp_servers table
    mcp_manager = MCPManager(store, registry)
    orchestrator = Orchestrator(settings, store, providers, registry)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One catch-up at boot, in the background. A server that has just
        # gained retrieval has every previous conversation to index, and the
        # alternative is a first search that finds nothing and gives no reason.
        # It is a task rather than an await because the port should open now,
        # not after several thousand chunks have been embedded.
        indexing = asyncio.create_task(_index_quietly())
        mcp_sync = asyncio.create_task(_sync_mcp_quietly())
        orphan_watch = asyncio.create_task(_exit_with_parent())
        yield
        orphan_watch.cancel()
        mcp_sync.cancel()
        await mcp_manager.aclose()
        indexing.cancel()
        await providers.aclose()
        db.close()

    async def _index_quietly() -> None:
        try:
            done = await indexer.catch_up()
            if done["chunked"] or done["embedded"]:
                print(f"[memory] indexed {done['chunked']} new chunk(s), "
                      f"embedded {done['embedded']}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never keep the server from starting
            print(f"[memory] startup indexing: {exc}")

    async def _exit_with_parent() -> None:
        """Shut down when whatever launched us is gone.

        Only when asked. A server started from a shell must keep running when
        that shell closes -- that is what `nohup` and every background launch
        depend on -- so this does nothing unless the supervisor that spawned it
        opts in by setting COURIER_EXIT_WITH_PARENT.

        The desktop shell sets it. Without this, force-quitting the app leaves
        the server holding the port: the shell's own exit handler never runs on
        SIGKILL, and the next launch then adopts a server the reader believes
        they closed. That is the confusing half of the orphan problem, and it
        is worse than the leaked memory.

        Detection is by reparenting rather than by signal, because that is the
        one thing SIGKILL cannot dodge: when the parent dies the kernel hands
        its children to init, and getppid() changes to 1.
        """
        if os.environ.get("COURIER_EXIT_WITH_PARENT") != "1":
            return
        started_under = os.getppid()
        while True:
            await asyncio.sleep(2)
            current = os.getppid()
            if current != started_under:
                print(f"[server] supervisor {started_under} exited -- shutting down")
                # SIGTERM to ourselves rather than os._exit: uvicorn has a
                # handler for it, so connections close and the lifespan
                # shutdown above still runs.
                os.kill(os.getpid(), signal.SIGTERM)
                return

    async def _sync_mcp_quietly() -> None:
        try:
            res = await mcp_manager.sync_all()
            if res["synced"]:
                print(f"[mcp] connected to {res['synced']} MCP server(s)")
            if res["failed"]:
                print(f"[mcp] failed to connect to {res['failed']} MCP server(s): {res['errors']}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[mcp] startup sync: {exc}")

    app = FastAPI(title="unified-llm", lifespan=lifespan)

    # The desktop shell is a different origin from this server.
    #
    # In a browser the client is served by this process, so `/api` is
    # same-origin and none of this applies. The Tauri build loads the same
    # bundle from a custom protocol instead, which makes every call
    # cross-origin -- and the bearer header makes each one a preflight. With
    # no CORS middleware the browser rejects them before the request is ever
    # sent, which surfaces in the UI as an unreachable server rather than as
    # the policy decision it is.
    #
    # Named origins rather than "*": the token is the whole perimeter, so a
    # wildcard would let any page the reader visits make authenticated calls
    # to a LAN-exposed server if it ever learned the token. Both spellings are
    # listed because macOS serves the shell from tauri://localhost and Windows
    # from http://tauri.localhost -- the Windows client should not need a
    # server change to work.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["tauri://localhost", "http://tauri.localhost"],
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        # Cookies are not how this authenticates, and allowing them would mean
        # the browser attaching ambient credentials to these requests.
        allow_credentials=False,
    )
    app.state.settings = settings
    app.state.db = db
    # Hung here so the one-liners in docs/memory.md can reach them without
    # constructing a second app.
    app.state.store = store
    app.state.orchestrator = orchestrator
    app.state.indexer = indexer
    app.state.mcp_manager = mcp_manager
    app.state.providers = providers
    app.state.openrouter_oauth = oauth

    auth = make_auth_dependency(settings)
    app.include_router(
        build_router(
            store, orchestrator, providers, auth, registry,
            settings, indexer, curator, mcp_manager, oauth,
        ),
        prefix="/api",
    )

    @app.get("/openrouter/callback/{state}")
    async def openrouter_callback(state: str, code: str = "", error: str = "") -> HTMLResponse:
        """Where OpenRouter sends the browser back to, holding a code.

        Unauthenticated, and it has to be: the redirect comes from
        openrouter.ai and carries none of this app's headers. What stands in
        for the token is `state` -- 256 bits, minted by an authenticated
        request, single-use and expiring in fifteen minutes. Someone who cannot
        guess it cannot spend a code here, and someone who can already had the
        token.

        The reply is a page, not JSON, because a person is looking at it. The
        client is not told anything by this route; it learns the outcome by
        polling the sign-in status, which is what it was already doing while
        this tab was open.
        """
        if error:
            return _signin_page("Sign-in cancelled", error, ok=False)
        try:
            key = await oauth.complete(state, code)
        except ProviderError as exc:
            return _signin_page("That didn't work", str(exc), ok=False)

        providers.openrouter.set_api_key(key)
        write_secret(settings.openrouter_key_path, key)
        # Held only until it is written down. The flow record stays for the
        # client's next poll; the key in it does not need to.
        flow = oauth.get(state)
        if flow is not None:
            flow.key = ""
        return _signin_page(
            "OpenRouter connected",
            "You can close this tab and go back to Courier.",
            ok=True,
        )

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        # Unauthenticated on purpose: it reveals nothing and makes it possible
        # to tell "server down" from "token wrong" from a browser.
        return JSONResponse({"ok": True})

    if settings.client_dir.is_dir():
        # The service worker must be served from the root to claim the whole
        # scope, so it gets its own route rather than living under /static.
        @app.get("/sw.js")
        def service_worker() -> FileResponse:
            return FileResponse(
                settings.client_dir / "sw.js",
                media_type="application/javascript",
                headers={"Cache-Control": "no-cache"},
            )

        app.mount(
            "/",
            ShellStatic(directory=settings.client_dir, html=True),
            name="client",
        )
    else:
        # The API still works; only the UI is missing. Say so, because the
        # symptom otherwise is a bare 404 at the root with no explanation.
        print(
            f"[client] {settings.client_dir} not found -- API only. "
            f"Build the UI with: npm install && npm run build (in client/)"
        )

    return app


app = create_app()
