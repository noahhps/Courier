"""HTTP surface. Small on purpose -- one user, one client, few endpoints."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .attachments import AttachmentError
from .attachments import decode as decode_attachments
from .config import Settings, ThinkingLevel, write_search_key
from .thinking import control_for
from .memory import MEMORY_DEFAULTS
from .memory.facts import Curator
from .memory.indexer import Indexer
from .orchestrator import Orchestrator
from .providers import ProviderRouter
from .mcp import icons as mcp_icons
from .mcp.settings import MCP_DEFAULTS
from .situation import Situation
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


class ClientContext(BaseModel):
    """What the browser knows about itself, and nothing more.

    Coarse by construction: an IANA zone, a language tag, a UTC offset and a
    country name. No coordinates, no permission prompt, no outbound lookup --
    the browser already holds all four, and they are enough to say when it is
    and roughly where. Every one is re-validated in `situation`; these limits
    only keep an absurd payload from reaching that far.
    """

    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=35)
    # Minutes east of UTC -- the negation of Date.getTimezoneOffset().
    utc_offset: int | None = Field(default=None, ge=-840, le=840)
    region: str | None = Field(default=None, max_length=80)


class SessionIn(BaseModel):
    client: ClientContext | None = None


class ChatRequest(BaseModel):
    # Empty is allowed only alongside a file: dropping in a screenshot with no
    # sentence is a real way to ask a question, but an empty POST is not.
    message: str = Field(default="", max_length=200_000)
    session_id: str | None = None
    attachments: list[AttachmentIn] = Field(default_factory=list)
    # Sent with every message, recorded only on the first one. A conversation
    # that starts from the phone should say so even when the client opened it
    # implicitly by posting here without a session_id.
    client: ClientContext | None = None
    # "local" | "cloud" | None (auto). Never switch silently -- the client
    # asks for a specific provider or accepts whatever the router picks.
    provider: str | None = None
    # Three shapes, because the families disagree: an effort word for gpt-oss,
    # a switch for deepseek and qwen, a token budget for Claude. Which one is
    # valid is a property of the live model, and /status says which -- so this
    # accepts any of them. None leaves the configured default in charge.
    #
    # bool before ThinkingLevel matters: Pydantic tries a union in order, and
    # `str` would happily swallow a JSON `true`.
    think: bool | ThinkingLevel | int | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "ChatRequest":
        if not self.message.strip() and not self.attachments:
            raise ValueError("a message needs text, a file, or both")
        return self


class SkillToggle(BaseModel):
    enabled: bool


class SkillKey(BaseModel):
    # Empty clears it. Not `min_length=1`, because "remove my key" is a thing
    # someone will want and a separate endpoint for it would be ceremony.
    key: str = Field(default="", max_length=400)


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


class BulkDelete(BaseModel):
    """Delete several conversations at once.

    `confirm` is the same guard `ForgetAll` uses: a stray POST to this path
    with a list of ids and nothing else does nothing. The typed word lives in
    the UI, where the person is -- a server that demanded a magic string would
    be checking that the client is polite, not that the request is intended.
    """

    ids: list[str] = Field(min_length=1, max_length=500)
    confirm: bool = False


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    # Validated by shape here and by meaning in the skill: the client sends
    # what its own date picker produced, so a bad string is a bug rather than
    # a model guessing.
    starts_at: str = Field(min_length=10, max_length=16)
    ends_at: str | None = Field(default=None, max_length=16)
    all_day: bool = False
    notes: str | None = Field(default=None, max_length=2000)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class SessionProject(BaseModel):
    # None files the conversation back under no project. Explicitly nullable
    # rather than an absent field, so "unfile this" is a thing the client can
    # say rather than something it has to express by omission.
    project_id: str | None = None


class MCPServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    transport: str = Field(default="stdio", pattern="^(stdio|sse|http|streamable-http)$")
    command: str | None = Field(default=None, max_length=500)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=1000)
    headers: dict[str, str] = Field(default_factory=dict)
    auto_approve: list[str] = Field(default_factory=list, alias="autoApprove")
    enabled: bool = True
    description: str | None = Field(default=None, max_length=1000)
    # Where to look for this service's logo. Inferred from `url` when absent,
    # which is why a custom HTTP server needs to say nothing to get an icon.
    homepage: str | None = Field(default=None, max_length=253)


class MCPServerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    transport: str | None = Field(default=None, pattern="^(stdio|sse|http|streamable-http)$")
    command: str | None = Field(default=None, max_length=500)
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=1000)
    headers: dict[str, str] | None = None
    auto_approve: list[str] | None = Field(default=None, alias="autoApprove")
    enabled: bool | None = None
    description: str | None = Field(default=None, max_length=1000)
    homepage: str | None = Field(default=None, max_length=253)


class MCPIconIn(BaseModel):
    """A logo the reader supplied themselves, base64 as everything else here is."""

    data: str = Field(min_length=1, max_length=700_000)  # ~512KB decoded
    mime: str | None = Field(default=None, max_length=100)


class MCPSettingsIn(BaseModel):
    fetch_icons: bool | None = None


class MCPImportIn(BaseModel):
    """The `mcpServers` block every MCP install page hands out, pasted verbatim.

    Accepts either the whole config file or just the inner mapping, because
    both are what people actually copy.
    """

    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class MCPPresetIn(BaseModel):
    preset: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, max_length=100)
    # What the preset's ${PLACEHOLDER}s should be filled in with. `env` is the
    # older spelling of the same thing and still works; both are merged, and a
    # value lands wherever the preset put the placeholder -- env, args, or a
    # header. Previously only `env` existed, so a preset whose secret lived in
    # an Authorization header had no way to receive one.
    values: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)


def build_router(
    store: Store,
    orchestrator: Orchestrator,
    providers: ProviderRouter,
    auth,
    registry: Registry,
    settings: Settings,
    indexer: Indexer,
    curator: Curator,
    mcp_manager: Any = None,
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth)])

    # -- sessions ---------------------------------------------------------

    @router.get("/sessions")
    def list_sessions() -> dict:
        return {"sessions": store.list_sessions()}

    @router.post("/sessions")
    def create_session(body: SessionIn | None = None) -> dict:
        # Body-less POSTs still work: an older client, or curl, is a caller
        # that simply has nothing to report about where it is.
        client = body.client if body else None
        return store.create_session(
            situation=Situation.from_client(client.model_dump() if client else None)
        )

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

    @router.post("/sessions/delete")
    def delete_sessions(body: BulkDelete) -> dict:
        if not body.confirm:
            raise HTTPException(400, "bulk delete needs confirm: true")
        # Counted against what was actually there, so the caller learns that an
        # id it asked about had already gone rather than being told it deleted
        # something it did not.
        deleted = 0
        for session_id in body.ids:
            if store.get_session(session_id):
                store.delete_session(session_id)
                deleted += 1
        return {"deleted": deleted, "requested": len(body.ids)}

    # -- projects ---------------------------------------------------------

    @router.get("/projects")
    def list_projects() -> dict:
        return {"projects": store.list_projects()}

    @router.post("/projects")
    def create_project(body: ProjectIn) -> dict:
        return store.create_project(body.name.strip())

    @router.patch("/projects/{project_id}")
    def rename_project(project_id: str, body: ProjectIn) -> dict:
        if not store.get_project(project_id):
            raise HTTPException(404, "no such project")
        store.rename_project(project_id, body.name.strip())
        return {"ok": True}

    @router.delete("/projects/{project_id}")
    def delete_project(project_id: str) -> dict:
        if not store.get_project(project_id):
            raise HTTPException(404, "no such project")
        store.delete_project(project_id)
        # Said out loud in the response, because "delete" on a folder full of
        # conversations is the kind of thing a caller should not have to read
        # the schema to be sure about.
        return {"ok": True, "conversations": "kept, now unfiled"}

    @router.put("/sessions/{session_id}/project")
    def set_session_project(session_id: str, body: SessionProject) -> dict:
        if not store.get_session(session_id):
            raise HTTPException(404, "no such session")
        if body.project_id and not store.get_project(body.project_id):
            raise HTTPException(404, "no such project")
        store.set_session_project(session_id, body.project_id)
        return {"ok": True, "project_id": body.project_id}

    # -- calendar ---------------------------------------------------------

    @router.get("/events")
    def list_events(since: str | None = None, until: str | None = None) -> dict:
        return {
            "events": [e.to_dict() for e in store.list_events(since=since, until=until)]
        }

    @router.post("/events")
    def create_event(body: EventIn) -> dict:
        if body.ends_at and body.ends_at <= body.starts_at:
            raise HTTPException(400, "the end has to come after the start")
        event = store.add_event(
            body.title.strip(),
            body.starts_at,
            ends_at=body.ends_at,
            all_day=body.all_day or "T" not in body.starts_at,
            notes=(body.notes or "").strip() or None,
        )
        return event.to_dict()

    @router.delete("/events/{event_id}")
    def delete_event(event_id: str) -> dict:
        if not store.get_event(event_id):
            raise HTTPException(404, "no such event")
        store.delete_event(event_id)
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

        situation = Situation.from_client(
            body.client.model_dump() if body.client else None
        )

        session_id = body.session_id
        if session_id:
            if not store.get_session(session_id):
                raise HTTPException(404, "no such session")
            # A no-op once the session knows where it started, which is the
            # usual case by the second message.
            store.set_session_situation(session_id, situation)
        else:
            session_id = store.create_session(situation=situation)["id"]

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
                    # Whether it *could* run, and what it wants if not. The key
                    # itself is never sent back -- only whether one is set.
                    "available": skill.available,
                    "requires": skill.requires,
                    "configurable": hasattr(skill, "set_api_key"),
                    # Which MCP server this came from, or None for a skill that
                    # ships with the server. Sent as its own field because the
                    # alternative is the client parsing it back out of
                    # `requires` ("MCP server 'gmail'"), which makes a sentence
                    # written for a person into a wire format.
                    "server": getattr(skill, "server_name", None),
                }
                for name, skill in registry.all()
            ]
        }

    @router.patch("/skills/{name}")
    def set_skill_enabled(name: str, body: SkillToggle) -> dict:
        skill = registry.get(name)
        if skill is None:
            raise HTTPException(404, f"no skill named {name!r}")
        # Refused rather than silently accepted: a switch that flips on and
        # then does nothing is worse than one that says why it will not.
        if body.enabled and not skill.available:
            raise HTTPException(
                400, f"{name} needs {skill.requires or 'configuration'} first"
            )
        registry.set_enabled(name, body.enabled)
        return {"name": name, "enabled": body.enabled}

    @router.put("/skills/{name}/key")
    async def set_skill_key(name: str, body: SkillKey) -> dict:
        skill = registry.get(name)
        if skill is None:
            raise HTTPException(404, f"no skill named {name!r}")
        if not hasattr(skill, "set_api_key"):
            raise HTTPException(400, f"{name} takes no key")

        key = body.key.strip()
        previous = skill.api_key
        skill.set_api_key(key)

        # Checked against the real service before it is written down. A key
        # that is wrong by one character otherwise fails on the first search,
        # a turn later, as an error the model reports rather than the page.
        if key:
            try:
                probe = await skill.use(query="test", count=1)
            except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
                skill.set_api_key(previous)
                raise HTTPException(400, f"could not reach the search API: {exc}") from exc
            # Allow-list, not a deny-list. Matching on failure phrases meant
            # guessing which ones the service produces, and Brave answers a
            # malformed key with 422 rather than 401 -- so "rejected" never
            # appeared and a junk key was saved as valid. Anything that is not
            # recognisably an answer is treated as a failure instead.
            #
            # A rate limit is the exception: the key is fine, it is the quota
            # that is not, and refusing to save it would be wrong.
            lowered = probe.strip().lower()
            rate_limited = "rate limit" in lowered
            if lowered.startswith(("the search", "no query")) and not rate_limited:
                skill.set_api_key(previous)
                raise HTTPException(400, probe.strip())

        write_search_key(settings.search_key_path, key)
        # Clearing the key leaves the switch on but the skill unavailable, so
        # it is turned off here rather than left in a state the page would have
        # to explain.
        if not key:
            registry.set_enabled(name, False)
        return {"name": name, "configured": bool(key), "available": skill.available}


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
    # -- MCP servers ------------------------------------------------------

    # An MCP server is recognised by its logo far faster than by its name, so
    # each one is given the service's own -- fetched once from the service's
    # site, cached, and overridable by upload. `icons.py` explains why this is
    # the only place Courier fetches a URL a reader typed, and what it refuses.

    def _icon_keys(server) -> tuple[str, str | None]:
        """(uploaded key, site key). The upload wins where it exists."""
        domain = mcp_icons.domain_for(homepage=server.homepage, url=server.url)
        return f"server:{server.id}", (f"site:{domain}" if domain else None)

    def _stored_icon(server) -> dict | None:
        uploaded, site = _icon_keys(server)
        return store.get_mcp_icon(uploaded) or (store.get_mcp_icon(site) if site else None)

    async def _fetch_icon_quietly(server_id: str) -> None:
        """Go and get a logo, after the response has already gone out.

        Never raises and never blocks the caller: adding a server should not
        wait on somebody else's website, and should not fail because of it.
        """
        try:
            if not store.get_settings(MCP_DEFAULTS)["mcp.fetch_icons"]:
                return
            server = store.get_mcp_server(server_id)
            if not server:
                return
            _, site = _icon_keys(server)
            if not site or store.get_mcp_icon(site):
                return  # nothing to look up, or another server already did
            found = await mcp_icons.fetch_icon(site.removeprefix("site:"))
            if found:
                store.put_mcp_icon(
                    site, mime=found.mime, data=found.data, source=found.source
                )
        except Exception:
            pass

    def _with_status(server, *, tools: list[str] | None = None, error: str | None = None) -> dict:
        data = server.to_dict()
        data["connected"] = mcp_manager.is_server_connected(server.name) if mcp_manager else False
        data["tools"] = tools if tools is not None else (
            mcp_manager.tools_for(server.name) if mcp_manager else []
        )
        data["error"] = error if error is not None else (
            mcp_manager.last_error(server.name) if mcp_manager else None
        )
        # A flag, not the bytes: the endpoint is authenticated, so the client
        # fetches it with the bearer header and wraps it in an object URL.
        data["has_icon"] = _stored_icon(server) is not None
        return data

    @router.get("/mcp/presets")
    def list_mcp_presets() -> dict:
        from .mcp.presets import list_presets

        presets = []
        for preset in list_presets():
            domain = mcp_icons.domain_for(
                homepage=preset.get("homepage"), url=preset.get("url")
            )
            presets.append({**preset, "has_icon": bool(domain)})
        return {"presets": presets}

    @router.get("/mcp/presets/{preset_id}/icon")
    async def get_mcp_preset_icon(preset_id: str) -> Response:
        """The logo for a preset that has not been installed yet.

        The gallery is where a logo earns its keep most -- picking one of
        fourteen services from a wall of identical glyphs is the hard read --
        so presets get the same treatment, cached under the same site key.
        """
        from .mcp.presets import get_preset

        preset = get_preset(preset_id)
        if not preset:
            raise HTTPException(404, f"no preset named {preset_id!r}")
        domain = mcp_icons.domain_for(homepage=preset.get("homepage"), url=preset.get("url"))
        if not domain:
            raise HTTPException(404, "this preset has no public website")

        key = f"site:{domain}"
        icon = store.get_mcp_icon(key)
        if not icon:
            if not store.get_settings(MCP_DEFAULTS)["mcp.fetch_icons"]:
                raise HTTPException(404, "logo fetching is switched off")
            found = await mcp_icons.fetch_icon(domain)
            if not found:
                raise HTTPException(404, "that site offers no icon")
            store.put_mcp_icon(key, mime=found.mime, data=found.data, source=found.source)
            icon = store.get_mcp_icon(key)

        return Response(
            content=icon["data"],
            media_type=icon["mime"],
            headers={"Cache-Control": "private, max-age=86400"},
        )

    @router.get("/mcp/servers/{server_id}/icon")
    def get_mcp_icon(server_id: str) -> Response:
        server = store.get_mcp_server(server_id)
        if not server:
            raise HTTPException(404, "no such MCP server")
        icon = _stored_icon(server)
        if not icon:
            raise HTTPException(404, "no icon for this server")
        return Response(
            content=icon["data"],
            media_type=icon["mime"],
            headers={"Cache-Control": "private, max-age=86400"},
        )

    @router.put("/mcp/servers/{server_id}/icon")
    def put_mcp_icon(server_id: str, body: MCPIconIn) -> dict:
        server = store.get_mcp_server(server_id)
        if not server:
            raise HTTPException(404, "no such MCP server")
        try:
            raw = base64.b64decode(body.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(400, f"that is not valid base64: {exc}") from exc
        if len(raw) > mcp_icons.MAX_ICON_BYTES:
            raise HTTPException(400, "an icon has to be under 256KB")
        # Sniffed, not trusted: whatever this is gets served back with a media
        # type and rendered, so it has to actually be an image.
        mime = mcp_icons.sniff(raw)
        if not mime:
            raise HTTPException(400, "that file is not a PNG, JPEG, GIF, WebP, ICO or SVG")
        store.put_mcp_icon(f"server:{server_id}", mime=mime, data=raw, source="upload")
        return {"ok": True, "mime": mime, "bytes": len(raw)}

    @router.delete("/mcp/servers/{server_id}/icon")
    async def delete_mcp_icon(server_id: str) -> dict:
        """Drop the upload and go back to whatever the service's site offers."""
        server = store.get_mcp_server(server_id)
        if not server:
            raise HTTPException(404, "no such MCP server")
        removed = store.delete_mcp_icon(f"server:{server_id}")
        await _fetch_icon_quietly(server_id)
        return {"ok": True, "removed": removed, "has_icon": _stored_icon(server) is not None}

    @router.post("/mcp/servers/{server_id}/icon/refresh")
    async def refresh_mcp_icon(server_id: str) -> dict:
        """Look the logo up again, ignoring what is cached for that site."""
        server = store.get_mcp_server(server_id)
        if not server:
            raise HTTPException(404, "no such MCP server")
        if not store.get_settings(MCP_DEFAULTS)["mcp.fetch_icons"]:
            raise HTTPException(400, "logo fetching is switched off")
        _, site = _icon_keys(server)
        if not site:
            raise HTTPException(
                400,
                "there is no public website to look at -- give the server a "
                "homepage, or upload an icon.",
            )
        store.delete_mcp_icon(site)
        await _fetch_icon_quietly(server_id)
        return {"ok": True, "has_icon": _stored_icon(server) is not None}

    @router.get("/mcp/servers")
    def list_mcp_servers() -> dict:
        return {"servers": [_with_status(server) for server in store.list_mcp_servers()]}

    @router.post("/mcp/servers")
    async def create_mcp_server(body: MCPServerIn) -> dict:
        existing = store.get_mcp_server_by_name(body.name)
        if existing:
            raise HTTPException(400, f"An MCP server named {body.name!r} already exists")

        server = store.add_mcp_server(
            name=body.name,
            transport=body.transport,
            command=body.command,
            args=body.args,
            env=body.env,
            cwd=body.cwd,
            url=body.url,
            headers=body.headers,
            auto_approve=body.auto_approve,
            enabled=body.enabled,
            description=body.description,
            homepage=body.homepage,
        )
        # Off the response path: a logo is decoration and should never be the
        # reason adding a server feels slow, or the reason it fails.
        asyncio.create_task(_fetch_icon_quietly(server.id))

        tools: list[str] = []
        if mcp_manager and server.enabled:
            try:
                tools = await mcp_manager.sync_server(server)
            except Exception as exc:
                return _with_status(server, tools=[], error=str(exc))

        return _with_status(server, tools=tools)

    @router.post("/mcp/presets/instantiate")
    async def instantiate_mcp_preset(body: MCPPresetIn) -> dict:
        from .mcp.presets import get_preset, missing_inputs, resolve_preset

        preset = get_preset(body.preset)
        if not preset:
            raise HTTPException(404, f"No preset named {body.preset!r}")

        server_name = body.name or preset["name"]
        existing = store.get_mcp_server_by_name(server_name)
        if existing:
            raise HTTPException(400, f"An MCP server named {server_name!r} already exists")

        values = {**body.env, **body.values}

        # Fail loudly on a missing token rather than storing a server that will
        # connect, get a 401, and report it as a network problem later.
        missing = missing_inputs(preset, values)
        if missing:
            raise HTTPException(
                400,
                f"Preset {body.preset!r} needs {', '.join(missing)}. "
                "Supply them in `values`, or set them in the server's environment.",
            )

        resolved = resolve_preset(preset, values)
        # Anything the reader supplied that the preset did not declare a
        # placeholder for still reaches the subprocess as an env var, which is
        # how a custom flag on a preset server stays possible.
        declared = {str(i.get("key")) for i in (preset.get("inputs") or []) if isinstance(i, dict)}
        extra_env = {k: v for k, v in values.items() if k not in declared}
        merged_env = {**(resolved.get("env") or {}), **extra_env}

        server = store.add_mcp_server(
            name=server_name,
            transport=resolved.get("transport", "stdio"),
            command=resolved.get("command"),
            args=resolved.get("args"),
            env=merged_env,
            cwd=resolved.get("cwd"),
            url=resolved.get("url"),
            headers=resolved.get("headers"),
            auto_approve=resolved.get("auto_approve"),
            enabled=True,
            description=resolved.get("description"),
            homepage=resolved.get("homepage"),
        )
        asyncio.create_task(_fetch_icon_quietly(server.id))

        tools: list[str] = []
        if mcp_manager:
            try:
                tools = await mcp_manager.sync_server(server)
            except Exception as exc:
                return _with_status(server, tools=[], error=str(exc))

        return _with_status(server, tools=tools)

    @router.post("/mcp/servers/import")
    async def import_mcp_servers(body: MCPImportIn) -> dict:
        """Add servers from a pasted `mcpServers` config.

        This is the shape every MCP install page hands out, so importing it is
        how a server that has no preset gets added without retyping it field by
        field into a form. Both wrappings are accepted -- the whole config file
        and the bare mapping -- because both are what people copy.

        Partial success is the normal outcome and is reported as such: one bad
        entry in a block of six should not cost the other five.
        """
        from .mcp.importer import ImportError_, parse_mcp_config

        try:
            parsed = parse_mcp_config(body.config)
        except ImportError_ as exc:
            raise HTTPException(400, str(exc)) from exc
        if not parsed:
            raise HTTPException(400, "no servers found in that config")

        added: list[dict] = []
        skipped: list[dict] = []
        for spec in parsed:
            if store.get_mcp_server_by_name(spec.name):
                skipped.append({"name": spec.name, "reason": "a server by that name exists"})
                continue
            try:
                server = store.add_mcp_server(
                    name=spec.name,
                    transport=spec.transport,
                    command=spec.command,
                    args=spec.args,
                    env=spec.env,
                    cwd=spec.cwd,
                    url=spec.url,
                    headers=spec.headers,
                    enabled=body.enabled,
                    description=spec.description,
                    homepage=spec.homepage,
                )
            except Exception as exc:
                skipped.append({"name": spec.name, "reason": str(exc)})
                continue

            asyncio.create_task(_fetch_icon_quietly(server.id))
            error = None
            tools: list[str] = []
            if mcp_manager and body.enabled:
                try:
                    tools = await mcp_manager.sync_server(server)
                except Exception as exc:
                    error = str(exc)
            added.append(_with_status(server, tools=tools, error=error))

        return {"added": added, "skipped": skipped}

    @router.get("/mcp/settings")
    def get_mcp_settings() -> dict:
        stored = store.get_settings(MCP_DEFAULTS)
        return {key.split(".", 1)[1]: value for key, value in stored.items()}

    @router.patch("/mcp/settings")
    def set_mcp_settings(body: MCPSettingsIn) -> dict:
        store.set_settings(
            {f"mcp.{name}": value
             for name, value in body.model_dump(exclude_none=True).items()}
        )
        stored = store.get_settings(MCP_DEFAULTS)
        return {key.split(".", 1)[1]: value for key, value in stored.items()}

    @router.patch("/mcp/servers/{server_id}")
    async def update_mcp_server(server_id: str, body: MCPServerPatch) -> dict:
        server = store.get_mcp_server(server_id)
        if not server:
            raise HTTPException(404, "no such MCP server")

        updated = store.update_mcp_server(
            server_id,
            name=body.name,
            transport=body.transport,
            command=body.command,
            args=body.args,
            env=body.env,
            cwd=body.cwd,
            url=body.url,
            headers=body.headers,
            auto_approve=body.auto_approve,
            enabled=body.enabled,
            description=body.description,
        )
        if not updated:
            raise HTTPException(404, "no such MCP server")

        tools: list[str] = []
        if mcp_manager:
            try:
                tools = await mcp_manager.sync_server(updated)
            except Exception as exc:
                return _with_status(updated, tools=[], error=str(exc))

        return _with_status(updated, tools=tools)

    @router.delete("/mcp/servers/{server_id}")
    async def delete_mcp_server(server_id: str) -> dict:
        server = store.get_mcp_server(server_id)
        if not server:
            raise HTTPException(404, "no such MCP server")

        if mcp_manager:
            await mcp_manager.disconnect_server(server.name)

        store.delete_mcp_server(server_id)
        return {"ok": True}

    @router.post("/mcp/servers/{server_id}/sync")
    async def sync_mcp_server(server_id: str) -> dict:
        server = store.get_mcp_server(server_id)
        if not server:
            raise HTTPException(404, "no such MCP server")

        if not mcp_manager:
            raise HTTPException(500, "MCP manager is not initialized")

        try:
            tools = await mcp_manager.sync_server(server)
            return {
                "ok": True,
                "server": server.name,
                "connected": mcp_manager.is_server_connected(server.name),
                "tools": tools,
            }
        except Exception as exc:
            raise HTTPException(500, f"Sync failed for '{server.name}': {exc}") from exc

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
                "thinking": control_for("ollama", providers.local.model).to_dict(),
            },
            # Each side reports the reasoning control its own model takes, so
            # the composer can redraw when the provider changes rather than
            # keeping a list of model names in the browser.
            "cloud": {
                "healthy": cloud_ok,
                "model": providers.cloud.model,
                "thinking": control_for("anthropic", providers.cloud.model).to_dict(),
            },
            "serving": "local" if local_ok else ("cloud" if cloud_ok else "none"),
            # ADD THIS NEW KEY:
            "default_provider": "local", 
        }
    return router
