"""Tests for MCP server database migrations and Store CRUD operations."""

from __future__ import annotations

from pathlib import Path
import pytest

from app.db import Database
from app.store import Store, StoredMCPServer


@pytest.fixture
def store(tmp_path: Path) -> Store:
    db_file = tmp_path / "test_mcp.db"
    db = Database(db_file)
    return Store(db)


def test_add_and_get_mcp_server(store: Store):
    server = store.add_mcp_server(
        name="github",
        transport="stdio",
        command="npx",
        args=["-y", "@mcp/server-github"],
        env={"GITHUB_TOKEN": "secret_token"},
        description="GitHub operations",
        auto_approve=["search_repos"],
    )

    assert server.id.startswith("mcp_")
    assert server.name == "github"
    assert server.transport == "stdio"
    assert server.command == "npx"
    assert server.parsed_args() == ["-y", "@mcp/server-github"]
    assert server.parsed_env() == {"GITHUB_TOKEN": "secret_token"}
    assert server.parsed_auto_approve() == ["search_repos"]
    assert server.enabled == 1
    assert server.description == "GitHub operations"

    fetched = store.get_mcp_server(server.id)
    assert fetched is not None
    assert fetched.id == server.id
    assert fetched.name == "github"

    by_name = store.get_mcp_server_by_name("github")
    assert by_name is not None
    assert by_name.id == server.id


def test_list_mcp_servers(store: Store):
    s1 = store.add_mcp_server("server1", "stdio", command="cmd1", enabled=True)
    s2 = store.add_mcp_server("server2", "sse", url="https://api.example.com", enabled=False)

    all_servers = store.list_mcp_servers(enabled_only=False)
    assert len(all_servers) == 2
    assert {s.name for s in all_servers} == {"server1", "server2"}

    enabled = store.list_mcp_servers(enabled_only=True)
    assert len(enabled) == 1
    assert enabled[0].name == "server1"


def test_update_mcp_server(store: Store):
    server = store.add_mcp_server("my_tool", "stdio", command="python")

    updated = store.update_mcp_server(
        server.id,
        command="python3",
        args=["-m", "mcp_tool"],
        enabled=False,
    )

    assert updated is not None
    assert updated.command == "python3"
    assert updated.parsed_args() == ["-m", "mcp_tool"]
    assert updated.enabled == 0

    assert store.set_mcp_server_enabled(server.id, True) is True
    re_fetched = store.get_mcp_server(server.id)
    assert re_fetched.enabled == 1


def test_delete_mcp_server(store: Store):
    server = store.add_mcp_server("to_delete", "stdio", command="echo")
    assert store.get_mcp_server(server.id) is not None

    assert store.delete_mcp_server(server.id) is True
    assert store.get_mcp_server(server.id) is None
    assert store.delete_mcp_server(server.id) is False


def test_stored_mcp_server_to_dict(store: Store):
    server = store.add_mcp_server(
        name="weather",
        transport="sse",
        url="https://weather.mcp.io/sse",
        headers={"X-Auth": "key123"},
        enabled=True,
    )
    d = server.to_dict()
    assert d["name"] == "weather"
    assert d["transport"] == "sse"
    assert d["url"] == "https://weather.mcp.io/sse"
    assert d["headers"] == {"X-Auth": "key123"}
    assert d["enabled"] is True
