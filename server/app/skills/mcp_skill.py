"""MCP Skill implementation wrapping remote or subprocess MCP tools into Courier's Skill system."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from .skill import Skill

if TYPE_CHECKING:
    from ..mcp.manager import MCPManager
    from ..mcp.protocol import MCPToolInfo


def sanitize_tool_name(name: str) -> str:
    """Normalize a tool name to match model provider identifier rules (^[a-zA-Z0-9_-]{1,64}$)."""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_")
    return sanitized[:64] or "mcp_tool"


def coerce_arguments(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Coerce argument types based on schema to tolerate small/quantized model discrepancies."""
    if not isinstance(args, dict):
        return {}

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return args

    coerced = dict(args)
    for key, spec in properties.items():
        if key not in coerced or not isinstance(spec, dict):
            continue

        val = coerced[key]
        expected_type = spec.get("type")

        # String -> Number / Integer
        if expected_type in ("integer", "number") and isinstance(val, str):
            try:
                coerced[key] = int(val) if expected_type == "integer" else float(val)
            except ValueError:
                pass

        # String -> Boolean
        elif expected_type == "boolean" and isinstance(val, str):
            lowered = val.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                coerced[key] = True
            elif lowered in ("false", "0", "no", "off"):
                coerced[key] = False

        # String -> Array / Object
        elif expected_type in ("array", "object") and isinstance(val, str):
            try:
                parsed = json.loads(val)
                if (expected_type == "array" and isinstance(parsed, list)) or (
                    expected_type == "object" and isinstance(parsed, dict)
                ):
                    coerced[key] = parsed
            except Exception:
                pass

    return coerced


class MCPSkill(Skill):
    """Bridge exposing an MCP tool as a native Courier Skill."""

    def __init__(
        self,
        server_name: str,
        tool_info: MCPToolInfo,
        manager: MCPManager,
        *,
        name_override: str | None = None,
    ) -> None:
        self.server_name = server_name
        self.raw_tool_name = tool_info.name
        self.manager = manager

        # The manager owns collision handling, because only it can see every
        # other server's registered names; absent that, the sanitized tool name.
        name = name_override or sanitize_tool_name(tool_info.name)

        # Keep the server's schema as given, minus the keys a provider will
        # reject. Rebuilding it from properties/required alone dropped $defs and
        # every $ref that pointed into them, which turns a nested-object tool
        # into one the model cannot fill in correctly.
        raw_schema = tool_info.input_schema if isinstance(tool_info.input_schema, dict) else {}
        parameters: dict[str, Any] = {
            k: v for k, v in raw_schema.items() if k not in ("$schema", "title")
        }
        parameters["type"] = "object"
        if not isinstance(parameters.get("properties"), dict):
            parameters["properties"] = {}
        if not isinstance(parameters.get("required"), list):
            parameters.pop("required", None)
        else:
            parameters["required"] = [str(r) for r in parameters["required"]]

        desc = tool_info.description or f"MCP tool '{tool_info.name}' from server '{server_name}'"

        super().__init__(
            name=name,
            description=desc,
            parameters=parameters,
            requires=f"MCP server '{server_name}'",
        )

    @property
    def available(self) -> bool:
        return self.manager.is_server_connected(self.server_name)

    async def use(self, **kwargs) -> str:
        """Execute the MCP tool via the MCP manager."""
        coerced = coerce_arguments(kwargs, self.parameters)
        return await self.manager.call_tool(
            self.server_name, self.raw_tool_name, coerced
        )
