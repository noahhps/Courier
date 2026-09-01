"""Model Context Protocol (MCP) package."""

from __future__ import annotations

from .manager import MCPManager
from .presets import PRESETS, get_preset, list_presets
from .protocol import (
    MCPError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPToolInfo,
    MCPTransportError,
)
from .transports import BaseMCPTransport, HttpSseTransport, StdioTransport

__all__ = [
    "BaseMCPTransport",
    "HttpSseTransport",
    "MCPError",
    "MCPManager",
    "MCPProtocolError",
    "MCPTimeoutError",
    "MCPToolInfo",
    "MCPTransportError",
    "PRESETS",
    "StdioTransport",
    "get_preset",
    "list_presets",
]
