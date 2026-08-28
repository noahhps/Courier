"""HTTP surface. Small on purpose -- one user, one client, few endpoints."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from . import attachments as files
from .attachments import AttachmentError
from .attachments import decode as decode_attachments
from .config import Settings, ThinkingLevel
from .memory import MEMORY_DEFAULTS
from .memory.facts import Curator
from .memory.indexer import Indexer
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


class FactIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    category: str | None = Field(default=None, max_length=80)
    pinned: bool = False


class FactPatch(BaseModel):
    """Every field optional -- this backs Edit, Keep always, and Looks right?,
    and each of those touches exactly one of them."""

    text: str | None = Field(default=None, min_length=1, max_length=2000)
    category: str | None = Field(default=None, max_length=80)
    pinned: bool | None = None
    status: str | None = Field(default=None, pattern="^(active|pending)$")


class MemorySettingsIn(BaseModel):
    # A subset is allowed: the page sends the one switch that moved.
    between_chats: bool | None = None
    confirm: bool | None = None
    share: bool | None = None


class ForgetAll(BaseModel):
    # Named rather than a bare POST, so nothing forgets everything by
    # accident -- a stray request to this path should do nothing at all.
    confirm: bool = False


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


def build_router(
    store: Store,
    orchestrator: Orchestrator,
    providers: ProviderRouter,
    auth,
    registry: Registry,
    settings: Settings,
    indexer: Indexer,
    curator: Curator,
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
            # Both off the response path, and both deliberately after the
            # stream has finished rather than inside it: the answer is already
            # on its way to the phone, and neither of these should delay it.
            asyncio.create_task(_title_quietly(session_id))
            asyncio.create_task(_remember_quietly(session_id))

        return StreamingResponse(
            frames(), media_type="text/event-stream", headers=SSE_HEADERS
        )

    async def _title_quietly(session_id: str) -> None:
        try:
            await orchestrator.ensure_title(session_id)
        except Exception as exc:  # a missing title must never break a turn
            print(f"[title] {session_id}: {exc}")

    async def _remember_quietly(session_id: str) -> None:
        """Index the turn that just finished, then curate on a cadence.

        Wrapped like titling and for the same reason: this runs after a turn
        that already succeeded, and a failure here must never be able to
        retract an answer the user has already read.

        Indexing first. The curation pass may not run this turn, but the
        chunks should exist either way -- and if the switch is off, neither
        happens and the history simply stays unindexed.
        """
        settings_now = store.get_settings(MEMORY_DEFAULTS)
        if not settings_now["memory.between_chats"]:
            return
        try:
            await indexer.catch_up(session_id)
        except Exception as exc:
            print(f"[memory] indexing {session_id}: {exc}")
        try:
            await curator.run(session_id, confirm=settings_now["memory.confirm"])
        except Exception as exc:
            print(f"[memory] curating {session_id}: {exc}")

    # -- attachments ------------------------------------------------------

    @router.get("/attachments/{attachment_id}")
    def get_attachment(attachment_id: str) -> Response:
        """The bytes of one uploaded file.

        Only images are served as themselves. Everything else goes back as an
        opaque download, whatever it claimed to be on the way in.

        The reason is that this origin holds the bearer token in
        `localStorage`. A .html file is a legitimate thing to attach and ask
        about, it was stored with `mime: text/html`, and serving it back with
        that type and `Content-Disposition: inline` renders it *on this
        origin* -- so a script inside it reads the token and can then drive
        the whole API. `X-Content-Type-Options: nosniff` does not help: it
        stops the browser guessing a different type, and here the declared
        type was already the dangerous one.

        The UI is unaffected. It fetches attachments with `fetch()` and wraps
        them in an object URL, and neither the type nor the disposition
        changes what that produces.
        """
        attachment = store.get_attachment(attachment_id)
        if not attachment:
            raise HTTPException(404, "no such attachment")

        inline = attachment.kind == "image" and attachment.mime in files.STORABLE_MIMES
        disposition = "inline" if inline else "attachment"
        # The filename came from the user and reaches a header here, so it is
        # percent-encoded rather than quoted -- a quote or a newline in it
        # would otherwise end the header and start another.
        disposition += f"; filename*=UTF-8''{quote(attachment.name, safe='')}"

        return Response(
            content=attachment.data,
            media_type=attachment.mime if inline else "application/octet-stream",
            headers={
                # Immutable: an attachment's bytes never change, and its id is
                # never reused. `private` because this router is authenticated
                # and the bytes are the user's.
                "Cache-Control": "private, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": disposition,
                # Nothing served from here should ever run script, frame
                # anything, or reach the network, whatever it turns out to be.
                "Content-Security-Policy": "default-src 'none'; sandbox",
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

    # -- memory -----------------------------------------------------------

    def _settings_out() -> dict:
        stored = store.get_settings(MEMORY_DEFAULTS)
        # Stripped of the "memory." prefix on the way out: the namespace is an
        # implementation detail of a table shared with other features.
        return {key.split(".", 1)[1]: value for key, value in stored.items()}

    @router.get("/memory")
    def get_memory() -> dict:
        """Everything the memory page renders, in one call.

        Raw storage shapes -- `source`, `created_at`, `used_count`. The server
        never emits "4 Jul" or "12 answers": those are a rendering decision,
        they are locale-dependent, and the export below wants the numbers.
        """
        return {
            "facts": [fact.to_dict() for fact in store.list_facts()],
            "settings": _settings_out(),
            "corpora": store.attachment_summary(),
            # What recall can actually see, so "searchable once retrieval
            # lands" can become a number rather than a promise.
            "index": {**store.chunk_counts(), "model": indexer.model},
        }

    @router.post("/memory")
    def add_fact(body: FactIn) -> dict:
        fact = store.add_fact(
            body.text[: settings.memory_fact_chars],
            source="told",
            category=body.category,
            pinned=body.pinned,
        )
        if fact is None:
            raise HTTPException(409, "That is already remembered, word for word.")
        return fact.to_dict()

    @router.delete("/memory/{fact_id}")
    def forget_fact(fact_id: str) -> dict:
        if not store.delete_fact(fact_id):
            raise HTTPException(404, "There is no such fact -- it may already be gone.")
        return {"ok": True}

    @router.post("/memory/forget-all")
    def forget_everything(body: ForgetAll) -> dict:
        if not body.confirm:
            raise HTTPException(
                400, "Forgetting everything needs confirm: true in the request."
            )
        return {"forgotten": store.delete_all_facts()}

    @router.patch("/memory/settings")
    def set_memory_settings(body: MemorySettingsIn) -> dict:
        store.set_settings(
            {
                f"memory.{name}": value
                for name, value in body.model_dump(exclude_none=True).items()
            }
        )
        return _settings_out()

    @router.get("/memory/export")
    def export_memory() -> Response:
        """Everything remembered, as a file. Facts only.

        The conversation history is not in here: it is far larger, it is
        already backed up by copying the database, and a download button that
        silently produced months of transcripts would be a surprise.
        """
        payload = {
            "exported_at": int(time.time() * 1000),
            "settings": _settings_out(),
            "facts": [fact.to_dict() for fact in store.list_facts()],
        }
        return Response(
            content=json.dumps(payload, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="memory.json"',
                "Cache-Control": "private, no-store",
            },
        )

    @router.post("/memory/reindex")
    async def reindex() -> dict:
        """Chunk and embed everything not yet covered.

        The same call the turn loop makes, over the whole history rather than
        one session. It exists as a button because the first run after this
        feature lands has months to get through, and the alternative is asking
        someone to open a Python shell.
        """
        try:
            return await indexer.catch_up()
        except Exception as exc:
            raise HTTPException(500, f"Indexing failed: {exc}") from exc

    # Declared after every literal path under /memory. A path parameter
    # matches anything, so "/memory/settings" reaching this route first is
    # how PATCHing a switch turns into "no such fact".
    @router.patch("/memory/{fact_id}")
    def edit_fact(fact_id: str, body: FactPatch) -> dict:
        if not store.update_fact(
            fact_id,
            text=body.text[: settings.memory_fact_chars] if body.text else None,
            category=body.category,
            pinned=body.pinned,
            status=body.status,
        ):
            raise HTTPException(404, "There is no such fact -- it may already be gone.")
        return {"ok": True}

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
