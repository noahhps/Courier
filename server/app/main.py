"""One process: API, static client, SQLite, provider routing."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import build_router
from .auth import make_auth_dependency
from .config import Settings, load_settings
from .db import Database
from .mcp import MCPManager
from .memory.facts import Curator
from .memory.indexer import Indexer
from .orchestrator import Orchestrator
from .providers import ProviderRouter
from .skills.calendar import AddEvent, FindEvents, ListEvents, UpdateEvent
from .skills.clock import Clock
from .skills.document import DocumentWriter
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    store = Store(db)
    providers = ProviderRouter(settings)
    # Built fresh each boot: skills are code that ships with the server, so
    # there is nothing to load and nothing to persist. Built *before* the
    # orchestrator, which needs it to tell the model what it can call.
    indexer = Indexer(settings, store, providers)
    curator = Curator(settings, store, providers)
    registry = Registry()
    registry.register(Clock())
    registry.register(AddEvent(store))
    registry.register(UpdateEvent(store))
    registry.register(ListEvents(store))
    registry.register(FindEvents(store))
    registry.register(DocumentWriter(settings.documents_dir))
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
        yield
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
    app.state.settings = settings
    app.state.db = db
    # Hung here so the one-liners in docs/memory.md can reach them without
    # constructing a second app.
    app.state.store = store
    app.state.orchestrator = orchestrator
    app.state.indexer = indexer
    app.state.mcp_manager = mcp_manager

    auth = make_auth_dependency(settings)
    app.include_router(
        build_router(
            store, orchestrator, providers, auth, registry,
            settings, indexer, curator, mcp_manager,
        ),
        prefix="/api",
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
