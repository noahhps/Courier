"""HTTP surface. Small on purpose -- one user, one client, few endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import ThinkingLevel
from .orchestrator import Orchestrator
from .providers import ProviderRouter
from .store import Store

# Proxies love to buffer text/event-stream into uselessness.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200_000)
    session_id: str | None = None
    # "local" | "cloud" | None (auto). Never switch silently -- the client
    # asks for a specific provider or accepts whatever the router picks.
    provider: str | None = None
    # gpt-oss accepts a reasoning effort, rather than a true/false switch.
    # None leaves the server's OLLAMA_THINK default in charge.
    think: ThinkingLevel | None = None


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


def build_router(
    store: Store, orchestrator: Orchestrator, providers: ProviderRouter, auth
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth)])

    # -- sessions ---------------------------------------------------------

    @router.get("/sessions")
    def list_sessions() -> dict:
        return {"sessions": store.list_sessions()}

    @router.post("/sessions")
    def create_session() -> dict:
        return store.create_session()

    @router.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        session = store.get_session(session_id)
        if not session:
            raise HTTPException(404, "no such session")
        return {
            "session": session,
            "messages": [m.to_dict() for m in store.list_messages(session_id)],
        }

    @router.patch("/sessions/{session_id}")
    def rename_session(session_id: str, body: RenameRequest) -> dict:
        if not store.get_session(session_id):
            raise HTTPException(404, "no such session")
        store.rename_session(session_id, body.title)
        return {"ok": True}

    @router.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> dict:
        store.delete_session(session_id)
        return {"ok": True}

    # -- chat -------------------------------------------------------------

    @router.post("/chat")
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        session_id = body.session_id
        if session_id:
            if not store.get_session(session_id):
                raise HTTPException(404, "no such session")
        else:
            session_id = store.create_session()["id"]

        async def frames():
            yield f'event: session\ndata: {{"session_id": "{session_id}"}}\n\n'
            async for frame in orchestrator.run_turn(
                session_id, body.message, prefer=body.provider, think=body.think
            ):
                if await request.is_disconnected():
                    break
                yield frame
            # Titling is off the response path but shouldn't outlive the process.
            asyncio.create_task(_title_quietly(session_id))

        return StreamingResponse(
            frames(), media_type="text/event-stream", headers=SSE_HEADERS
        )

    async def _title_quietly(session_id: str) -> None:
        try:
            await orchestrator.ensure_title(session_id)
        except Exception as exc:  # a missing title must never break a turn
            print(f"[title] {session_id}: {exc}")

    # -- status -----------------------------------------------------------

    @router.get("/status")
    async def status() -> dict:
        local_ok = await providers.local.health()
        cloud_ok = await providers.cloud.health()
        return {
            "local": {
                "healthy": local_ok,
                "model": providers.local.model,
                "url": providers.local.base_url,
            },
            "cloud": {"healthy": cloud_ok, "model": providers.cloud.model},
            "serving": "local" if local_ok else ("cloud" if cloud_ok else "none"),
            # ADD THIS NEW KEY:
            "default_provider": "local", 
        }
    return router
