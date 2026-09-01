"""JSON-RPC 2.0 and MCP protocol message definitions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class MCPError(Exception):
    """Base exception for Model Context Protocol errors."""


class MCPTransportError(MCPError):
    """Raised when communication with an MCP server fails or disconnects."""


class MCPProtocolError(MCPError):
    """Raised when an MCP server returns a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"MCP Error [{code}]: {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPTimeoutError(MCPError):
    """Raised when an MCP request times out."""


@dataclass
class MCPToolInfo:
    """Metadata describing a single tool discovered from an MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


def format_jsonrpc_request(
    request_id: int | str, method: str, params: dict[str, Any] | None = None
) -> str:
    """Encode a JSON-RPC 2.0 request as a single-line JSON string."""
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    return json.dumps(payload, ensure_ascii=False)


def format_jsonrpc_notification(
    method: str, params: dict[str, Any] | None = None
) -> str:
    """Encode a JSON-RPC 2.0 notification (no id) as a single-line JSON string."""
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    return json.dumps(payload, ensure_ascii=False)


def parse_jsonrpc_response(line: str) -> dict[str, Any]:
    """Parse a single line of JSON-RPC response."""
    line = line.strip()
    if not line:
        raise MCPProtocolError(-32700, "Empty JSON-RPC message received")
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MCPProtocolError(-32700, f"Parse error: {exc}") from exc

    if not isinstance(data, dict):
        raise MCPProtocolError(-32600, "Invalid Request: expected JSON object")
    return data
