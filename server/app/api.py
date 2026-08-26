"""HTTP surface. Small on purpose -- one user, one client, few endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .attachments import AttachmentError
from .attachments import decode as decode_attachments
from .config import Settings, ThinkingLevel
from .orchestrator import Orchestrator
from .providers import ProviderRouter
from .store import Store
from .skills.registry import Registry

# Proxies love to buffer text/event-stream into uselessness.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class AttachmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    mime: str = Field(default="", max_length=200)
    data: str  # base64, without the data: URL prefix


class ChatRequest(BaseModel):
    # Empty is allowed only alongside a file: dropping in a screenshot with no
    # sentence is a real way to ask a question, but an empty POST is not.
    message: str = Field(default="", max_length=200_000)
    session_id: str | None = None
    attachments: list[AttachmentIn] = Field(default_factory=list)
    # "local" | "cloud" | None (auto). Never switch silently -- the client
    # asks for a specific provider or accepts whatever the router picks.
    provider: str | None = None
    # gpt-oss accepts a reasoning effort, rather than a true/false switch.
    # None leaves the server's OLLAMA_THINK default in charge.
    think: ThinkingLevel | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "ChatRequest":
        if not self.message.strip() and not self.attachments:
            raise ValueError("a message needs text, a file, or both")
        return self


class SkillToggle(BaseModel):
    enabled: bool


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


def build_router(
    store: Store,
    orchestrator: Orchestrator,
    providers: ProviderRouter,
    auth,
    registry: Registry,
    settings: Settings,
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
        # Names and sizes only. The bytes are fetched per file, by id, so
        # opening a conversation full of screenshots stays one small response.
        attached = store.attachments_for_session(session_id)
        return {
            "session": session,
            "messages": [
                {
                    **m.to_dict(),
                    "attachments": [a.to_dict() for a in attached.get(m.id, ())],
                }
                for m in store.list_messages(session_id)
            ],
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
        # Decoded before the session is touched: a refused file should leave no
        # trace, and the composer needs the reason back as a plain 400 rather
        # than as an error frame inside a stream it has already started.
        try:
            attached = decode_attachments(body.attachments)
        except AttachmentError as exc:
            raise HTTPException(400, str(exc)) from exc

        session_id = body.session_id
        if session_id:
            if not store.get_session(session_id):
                raise HTTPException(404, "no such session")
        else:
            session_id = store.create_session()["id"]

        async def frames():
            yield f'event: session\ndata: {{"session_id": "{session_id}"}}\n\n'
            async for frame in orchestrator.run_turn(
                session_id,
                body.message,
                attached=attached,
                prefer=body.provider,
                think=body.think,
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

    # -- attachments ------------------------------------------------------

    @router.get("/attachments/{attachment_id}")
    def get_attachment(attachment_id: str) -> Response:
        attachment = store.get_attachment(attachment_id)
        if not attachment:
            raise HTTPException(404, "no such attachment")
        return Response(
            content=attachment.data,
            media_type=attachment.mime or "application/octet-stream",
            headers={
                # Immutable: an attachment's bytes never change, and its id is
                # never reused. `private` because this router is authenticated
                # and the bytes are the user's.
                "Cache-Control": "private, max-age=31536000, immutable",
                # Belt and braces against a crafted upload being served back as
                # something the browser would run.
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "inline",
            },
        )

    # -- skills -----------------------------------------------------------

    @router.get("/skills")
    def list_skills() -> dict:
        # Name and description only. `use` is code, not something to serialise,
        # and the description is the part a caller needs in order to choose.
        return {
            "skills": [
                {
                    "name": name,
                    "description": skill.description,
                    "enabled": skill.enabled,
                }
                for name, skill in registry.all()
            ]
        }

    @router.patch("/skills/{name}")
    def set_skill_enabled(name: str, body: SkillToggle) -> dict:
        if not registry.set_enabled(name, body.enabled):
            raise HTTPException(404, f"no skill named {name!r}")
        return {"name": name, "enabled": body.enabled}

    # -- documents --------------------------------------------------------

    @router.get("/documents/{name}")
    def get_document(name: str) -> FileResponse:
        """Serve something the assistant wrote.

        The name is resolved and then checked to be inside the documents
        directory. `Path.name` alone would be enough for the shapes FastAPI
        lets through, but a containment check is the assertion that actually
        expresses the rule, and it survives someone widening the route later.
        """
        directory = settings.documents_dir.resolve()
        candidate = (directory / Path(name).name).resolve()
        if candidate.parent != directory or not candidate.is_file():
            raise HTTPException(404, "no such document")
        return FileResponse(
            candidate,
            filename=candidate.name,
            headers={"Cache-Control": "private, no-store"},
        )

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
