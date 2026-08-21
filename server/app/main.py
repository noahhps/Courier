"""One process: API, static client, SQLite, provider routing."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import build_router
from .auth import make_auth_dependency
from .config import Settings, load_settings
from .db import Database
from .orchestrator import Orchestrator
from .providers import ProviderRouter
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
    orchestrator = Orchestrator(settings, store, providers)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await providers.aclose()
        db.close()

    app = FastAPI(title="unified-llm", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db

    auth = make_auth_dependency(settings)
    app.include_router(build_router(store, orchestrator, providers, auth), prefix="/api")

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
