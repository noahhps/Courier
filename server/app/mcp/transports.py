"""Transports for talking to MCP servers over stdio, Streamable HTTP, or legacy SSE.

Three wire protocols, two classes. ``StdioTransport`` spawns a subprocess and
speaks line-delimited JSON-RPC at it. ``HttpSseTransport`` covers both HTTP
shapes the MCP spec has had:

* **Streamable HTTP** (2025-03-26 onward, what Figma's Dev Mode server speaks).
  Every message is a POST to one endpoint. The reply comes back on that same
  POST, either as ``application/json`` or as an SSE stream carrying the single
  response frame. The server hands out an ``Mcp-Session-Id`` on initialize and
  rejects every later request that does not echo it.
* **HTTP+SSE** (2024-11-05, "legacy"). A long-lived ``GET /sse`` carries every
  response; requests are POSTed to a separate endpoint the stream names in its
  first ``endpoint`` event.

The old implementation only ever did the second one, and only halfway: it never
sent an ``Accept`` header, never tracked a session id, and could not read a
JSON-RPC response out of an SSE body returned from a POST. Against a Streamable
HTTP server that meant every request hung until it timed out.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .protocol import (
    MCPProtocolError,
    MCPTimeoutError,
    MCPTransportError,
    format_jsonrpc_notification,
    format_jsonrpc_request,
    parse_jsonrpc_response,
)

# Newest spec revision we know how to speak. A compliant server answers
# initialize with the version *it* picked, which is what we then echo back in
# the MCP-Protocol-Version header, so naming a version the server has never
# heard of is safe and negotiating down is automatic.
PREFERRED_PROTOCOL_VERSION = "2025-06-18"


def _error_from(msg: dict[str, Any]) -> MCPProtocolError:
    """Build the exception for a JSON-RPC error object."""
    err = msg.get("error")
    if isinstance(err, dict):
        return MCPProtocolError(
            err.get("code", -32000), err.get("message", str(err)), err.get("data")
        )
    return MCPProtocolError(-32000, str(err))


class BaseMCPTransport(ABC):
    """Abstract base class for MCP client transports."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish the connection or spawn the subprocess."""

    @abstractmethod
    async def send_request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0
    ) -> Any:
        """Send a JSON-RPC request and await its result."""

    @abstractmethod
    async def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        """Send a JSON-RPC notification (no response expected)."""

    @abstractmethod
    async def close(self) -> None:
        """Terminate the connection and free resources."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport is active and ready for requests."""


class StdioTransport(BaseMCPTransport):
    """Communicates with a local subprocess over line-delimited JSON-RPC on stdio."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._request_counter = 0
        self._pending: dict[int | str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._stderr_lines: list[str] = []
        self._stderr_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def stderr_tail(self, n: int = 3) -> str:
        """The last few stderr lines, for error messages that would otherwise say nothing."""
        return " | ".join(self._stderr_lines[-n:])

    async def connect(self) -> None:
        if self.is_connected:
            return

        full_env = dict(os.environ)
        # Apply custom environment variables. Empty values are dropped rather
        # than exported as "": a server that checks `if (process.env.KEY)` and
        # one that checks `if ('KEY' in process.env)` should agree that an
        # unfilled field means absent.
        full_env.update({k: v for k, v in self.env.items() if v != ""})

        # Resolve command path if needed
        cmd_path = shutil.which(self.command) or self.command

        try:
            self._proc = await asyncio.create_subprocess_exec(
                cmd_path,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
                cwd=self.cwd,
            )
        except OSError as exc:
            raise MCPTransportError(
                f"Failed to start MCP process '{self.command}': {exc}"
            ) from exc

        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    async def _stderr_loop(self) -> None:
        """Read and buffer stderr lines from the child process for error diagnostics."""
        if not self._proc or not self._proc.stderr:
            return
        try:
            while True:
                line_bytes = await self._proc.stderr.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", "replace").strip()
                if line:
                    self._stderr_lines.append(line)
                    if len(self._stderr_lines) > 50:
                        self._stderr_lines.pop(0)
        except asyncio.CancelledError:
            pass

    async def _read_loop(self) -> None:
        """Continuously read stdout lines from the child process."""
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line_bytes = await self._proc.stdout.readline()
                if not line_bytes:
                    break  # EOF / process terminated
                line = line_bytes.decode("utf-8", "replace").strip()
                if not line:
                    continue

                try:
                    msg = parse_jsonrpc_response(line)
                except MCPProtocolError:
                    continue

                req_id = msg.get("id")

                # A message carrying both an id and a method is the server
                # calling *us* -- roots/list, sampling/createMessage, elicitation.
                # Courier offers none of those, but a server that waits on the
                # answer stalls forever unless it is told so.
                if req_id is not None and "method" in msg:
                    await self._reply_unsupported(req_id, str(msg.get("method")))
                    continue

                if req_id is not None and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(_error_from(msg))
                        else:
                            fut.set_result(msg.get("result"))
        except asyncio.CancelledError:
            pass
        finally:
            # Let stderr loop flush
            await asyncio.sleep(0.05)
            err_detail = self.stderr_tail()
            exit_code = self._proc.returncode if self._proc else "unknown"
            msg_text = f"MCP process '{self.command}' exited ({exit_code})"
            if err_detail:
                msg_text += f": {err_detail}"
            exc = MCPTransportError(msg_text)
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(exc)
            self._pending.clear()

    async def _reply_unsupported(self, req_id: Any, method: str) -> None:
        """Answer a server-initiated request with 'method not found'."""
        if not self._proc or not self._proc.stdin:
            return
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        )
        try:
            self._proc.stdin.write((payload + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        except (OSError, RuntimeError):
            pass

    async def send_request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0
    ) -> Any:
        if not self.is_connected:
            await self.connect()

        assert self._proc is not None and self._proc.stdin is not None

        async with self._lock:
            self._request_counter += 1
            req_id = self._request_counter
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending[req_id] = fut

            payload = format_jsonrpc_request(req_id, method, params) + "\n"
            try:
                self._proc.stdin.write(payload.encode("utf-8"))
                await self._proc.stdin.drain()
            except (OSError, BrokenPipeError) as exc:
                self._pending.pop(req_id, None)
                raise MCPTransportError(
                    f"Failed writing to MCP process '{self.command}': {exc}"
                ) from exc

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            detail = self.stderr_tail()
            raise MCPTimeoutError(
                f"MCP request '{method}' to '{self.command}' timed out after {timeout}s"
                + (f" (stderr: {detail})" if detail else "")
            ) from exc

    async def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        if not self.is_connected:
            await self.connect()

        assert self._proc is not None and self._proc.stdin is not None
        payload = format_jsonrpc_notification(method, params) + "\n"
        try:
            self._proc.stdin.write(payload.encode("utf-8"))
            await self._proc.stdin.drain()
        except OSError as exc:
            raise MCPTransportError(
                f"Failed writing notification to '{self.command}': {exc}"
            ) from exc

    async def close(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        self._reader_task = None
        self._stderr_task = None
        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
            except (ProcessLookupError, OSError):
                pass
            finally:
                self._proc = None


class HttpSseTransport(BaseMCPTransport):
    """Talks to a remote MCP server over Streamable HTTP, falling back to legacy SSE."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        mode: str | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        # Blank values are dropped: an unfilled token field should mean "send no
        # Authorization header", not "send an empty one", which some servers
        # answer with a 401 that reads like a wrong password.
        self.headers = {k: v for k, v in (headers or {}).items() if v not in ("", None)}
        # "streamable" | "sse". Chosen from the path, overridable, and switched
        # automatically if the server turns out to speak the other one.
        self.mode = mode or self._guess_mode(self.url)
        self._client: httpx.AsyncClient | None = None
        self._request_counter = 0
        self._post_url: str = self.url
        self._sse_task: asyncio.Task | None = None
        self._endpoint_ready: asyncio.Event = asyncio.Event()
        self._pending: dict[int | str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._connected = False
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._tried_fallback = False

    @staticmethod
    def _guess_mode(url: str) -> str:
        """Legacy SSE only when the *path* says so -- not when 'sse' appears anywhere.

        The old check was ``"sse" in self.url``, which reads a host like
        ``sse-gateway.example.com`` or a token in a query string as a protocol
        choice.
        """
        path = urlparse(url).path.rstrip("/")
        return "sse" if path.endswith("/sse") else "streamable"

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None and not self._client.is_closed

    def _request_headers(self, *, streaming: bool = False) -> dict[str, str]:
        headers = dict(self.headers)
        headers["Content-Type"] = "application/json"
        # Both types, always: the spec lets a Streamable HTTP server answer a
        # POST either way, and one that cannot satisfy the Accept header replies
        # 406 rather than guessing.
        headers["Accept"] = "text/event-stream" if streaming else "application/json, text/event-stream"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    async def connect(self) -> None:
        if self.is_connected:
            return

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=5.0),
                follow_redirects=True,
            )

        if self.mode == "sse":
            await self._start_sse_listener()
        else:
            # Streamable HTTP has no connect step -- the first POST is the
            # handshake. Nothing to open, nothing to wait for.
            self._post_url = self.url
            self._connected = True

    async def _start_sse_listener(self) -> None:
        """Open the long-lived GET stream and wait for it to name the POST endpoint."""
        self._post_url = self.url
        self._endpoint_ready = asyncio.Event()
        self._connected = True
        self._sse_task = asyncio.create_task(self._sse_listener())
        try:
            await asyncio.wait_for(self._endpoint_ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            # Some servers accept POSTs at the same URL without announcing an
            # endpoint. Keep going rather than failing the connection outright.
            pass

    async def _sse_listener(self) -> None:
        """Read the GET stream: capture the POST endpoint, resolve pending requests."""
        assert self._client is not None
        headers = dict(self.headers)
        headers["Accept"] = "text/event-stream"
        headers["Cache-Control"] = "no-cache"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            async with self._client.stream(
                "GET", self.url, headers=headers, timeout=None
            ) as response:
                if response.status_code >= 400:
                    self._connected = False
                    self._endpoint_ready.set()
                    return

                event_type = "message"
                async for raw_line in response.aiter_lines():
                    line = raw_line.rstrip("\r\n")
                    if not line:
                        event_type = "message"
                        continue
                    if line.startswith(":"):
                        continue  # keep-alive comment

                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_str = line[5:].strip()
                        if event_type == "endpoint":
                            # Relative paths resolve against the stream's own URL;
                            # splitting on the literal "/sse" broke on any server
                            # that mounted the stream under a prefix.
                            self._post_url = urljoin(str(response.url), data_str)
                            self._endpoint_ready.set()
                        elif event_type == "message":
                            self._dispatch(data_str)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self._connected = False
            self._endpoint_ready.set()
            self._fail_pending(
                MCPTransportError(f"SSE stream to '{self.url}' closed")
            )

    def _dispatch(self, data_str: str) -> None:
        """Route one JSON-RPC message from the SSE stream to whoever is waiting."""
        try:
            msg = parse_jsonrpc_response(data_str)
        except MCPProtocolError:
            return
        req_id = msg.get("id")
        if req_id is None or req_id not in self._pending:
            return
        fut = self._pending.pop(req_id)
        if fut.done():
            return
        if "error" in msg:
            fut.set_exception(_error_from(msg))
        else:
            fut.set_result(msg.get("result"))

    def _fail_pending(self, exc: Exception) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    @staticmethod
    def _messages_from_sse(body: str) -> list[dict[str, Any]]:
        """Pull JSON-RPC messages out of an SSE-formatted response body."""
        messages: list[dict[str, Any]] = []
        for raw_line in body.splitlines():
            line = raw_line.rstrip("\r")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                messages.append(parsed)
        return messages

    def _capture_session(self, response: httpx.Response) -> None:
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session

    def _next_id(self) -> int:
        self._request_counter += 1
        return self._request_counter

    async def send_request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0
    ) -> Any:
        if not self.is_connected:
            await self.connect()
        assert self._client is not None

        if self.mode == "sse":
            return await self._send_request_sse(method, params, timeout=timeout)
        return await self._send_request_streamable(method, params, timeout=timeout)

    async def _send_request_streamable(
        self, method: str, params: dict[str, Any] | None, *, timeout: float
    ) -> Any:
        assert self._client is not None
        async with self._lock:
            req_id = self._next_id()

        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params

        try:
            response = await self._client.post(
                self._post_url,
                json=payload,
                headers=self._request_headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise MCPTimeoutError(
                f"MCP request '{method}' to '{self._post_url}' timed out after {timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise MCPTransportError(
                f"Network error connecting to '{self._post_url}': {exc}"
            ) from exc

        # Order matters: once we hold a session id we are demonstrably talking
        # to a Streamable HTTP server, so a 404 means that session expired --
        # not that we guessed the protocol wrong.
        if response.status_code == 404 and self._session_id:
            self._session_id = None
            self._connected = False
            raise MCPTransportError(
                f"MCP session expired on '{self._post_url}'; reconnect required"
            )

        # A 404/405 before that usually means a 2024-11-05 server that only
        # serves the GET-stream shape. Try that once before giving up.
        if response.status_code in (404, 405) and not self._tried_fallback:
            self._tried_fallback = True
            self.mode = "sse"
            self.url = self.url if self._guess_mode(self.url) == "sse" else f"{self.url}/sse"
            await self._start_sse_listener()
            return await self._send_request_sse(method, params, timeout=timeout)

        if response.status_code >= 400:
            detail = response.text.strip()[:200]
            raise MCPTransportError(
                f"HTTP {response.status_code} from '{self._post_url}'"
                + (f": {detail}" if detail else "")
            )

        self._capture_session(response)

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            messages = self._messages_from_sse(response.text)
        elif response.content:
            try:
                body = response.json()
            except ValueError as exc:
                raise MCPTransportError(
                    f"Non-JSON response from '{self._post_url}': {response.text[:200]}"
                ) from exc
            messages = body if isinstance(body, list) else [body]
        else:
            messages = []

        for msg in messages:
            if not isinstance(msg, dict) or msg.get("id") != req_id:
                continue  # a notification or an unrelated frame on the same stream
            if "error" in msg:
                raise _error_from(msg)
            result = msg.get("result")
            if method == "initialize" and isinstance(result, dict):
                self._protocol_version = str(
                    result.get("protocolVersion") or PREFERRED_PROTOCOL_VERSION
                )
            return result

        raise MCPTransportError(
            f"No JSON-RPC response for '{method}' from '{self._post_url}'"
        )

    async def _send_request_sse(
        self, method: str, params: dict[str, Any] | None, *, timeout: float
    ) -> Any:
        assert self._client is not None
        async with self._lock:
            req_id = self._next_id()
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending[req_id] = fut

        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params

        try:
            response = await self._client.post(
                self._post_url,
                json=payload,
                headers=self._request_headers(),
                timeout=timeout,
            )
            if response.status_code >= 400:
                self._pending.pop(req_id, None)
                raise MCPTransportError(
                    f"HTTP {response.status_code} from '{self._post_url}'"
                )
            self._capture_session(response)

            # Some SSE servers answer inline anyway; take it if they do.
            if "application/json" in response.headers.get("content-type", "") and response.content:
                self._pending.pop(req_id, None)
                data = response.json()
                if isinstance(data, dict) and "error" in data:
                    raise _error_from(data)
                result = data.get("result") if isinstance(data, dict) else None
                if method == "initialize" and isinstance(result, dict):
                    self._protocol_version = str(
                        result.get("protocolVersion") or PREFERRED_PROTOCOL_VERSION
                    )
                return result
        except httpx.TimeoutException as exc:
            self._pending.pop(req_id, None)
            raise MCPTimeoutError(f"HTTP request to '{self._post_url}' timed out") from exc
        except httpx.HTTPError as exc:
            self._pending.pop(req_id, None)
            raise MCPTransportError(
                f"Network error connecting to '{self._post_url}': {exc}"
            ) from exc

        # Otherwise the answer arrives on the GET stream.
        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise MCPTimeoutError(
                f"MCP request '{method}' timed out after {timeout}s waiting on the SSE stream"
            ) from exc
        if method == "initialize" and isinstance(result, dict):
            self._protocol_version = str(
                result.get("protocolVersion") or PREFERRED_PROTOCOL_VERSION
            )
        return result

    async def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        if not self.is_connected:
            await self.connect()
        assert self._client is not None

        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params

        try:
            response = await self._client.post(
                self._post_url,
                json=payload,
                headers=self._request_headers(),
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            # notifications/initialized is not decoration -- a server that never
            # receives it can refuse every subsequent call, and swallowing the
            # failure silently is how that turns into an unexplainable timeout.
            raise MCPTransportError(
                f"Failed sending notification '{method}' to '{self._post_url}': {exc}"
            ) from exc

        if response.status_code >= 400:
            raise MCPTransportError(
                f"HTTP {response.status_code} sending notification '{method}' "
                f"to '{self._post_url}'"
            )
        self._capture_session(response)

    async def close(self) -> None:
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (asyncio.CancelledError, Exception):
                pass
        self._sse_task = None
        self._connected = False

        # Streamable HTTP sessions are server-side state; releasing it is a
        # DELETE. Best effort -- the server is entitled to refuse.
        if self._client and not self._client.is_closed and self._session_id:
            try:
                await self._client.delete(
                    self._post_url, headers=self._request_headers(), timeout=5.0
                )
            except Exception:
                pass
        self._session_id = None

        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
