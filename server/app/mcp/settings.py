"""Switches for the MCP subsystem, stored alongside the memory ones."""

from __future__ import annotations

# Fetching a service's logo means one HTTPS request to that service's website,
# which is the only outbound traffic Courier makes on a reader's behalf that is
# not a model call or an MCP call. Small, but it does tell github.com that
# somebody here uses their MCP server -- so it is a switch rather than an
# assumption, defaulting on because the icons are the point of the feature.
MCP_DEFAULTS: dict[str, bool] = {
    "mcp.fetch_icons": True,
}
