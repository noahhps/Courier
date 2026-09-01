"""Tests for MCP server logos and for importing a pasted `mcpServers` config."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.mcp.icons import FetchedIcon, domain_for, is_public_host, sniff, _pick_link
from app.mcp.importer import ImportError_, parse_mcp_config

# The smallest valid PNG: 1x1, transparent.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGNgAAAAAgABc3n0"
    "kQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(Settings(db_path=tmp_path / "icons.db", auth_token="t"))
    if hasattr(app.state, "mcp_manager"):
        app.state.mcp_manager.sync_server = AsyncMock(return_value=["tool_a"])
    return TestClient(app, headers={"Authorization": "Bearer t"})


# -- what the fetcher will and will not talk to -------------------------------


def test_only_public_hosts_are_fetched():
    """The one place Courier fetches a URL a reader typed, so it stays off the LAN."""
    assert is_public_host("github.com")
    for private in ("127.0.0.1", "localhost", "192.168.1.1", "10.0.0.5", "169.254.1.1"):
        assert not is_public_host(private), private


def test_a_loopback_endpoint_yields_no_domain():
    """Figma's server lives on 127.0.0.1 and has no logo to fetch there."""
    assert domain_for(url="http://127.0.0.1:3845/mcp") is None
    # ...but the preset names figma.com, which does.
    assert domain_for(homepage="figma.com", url="http://127.0.0.1:3845/mcp") == "figma.com"


def test_a_stdio_server_with_no_homepage_has_nowhere_to_look():
    assert domain_for(homepage=None, url=None) is None
    assert domain_for(homepage="   ") is None


def test_bare_domains_and_full_urls_both_work():
    assert domain_for(homepage="exa.ai") == "exa.ai"
    assert domain_for(homepage="https://exa.ai/docs") == "exa.ai"
    assert domain_for(url="https://api.githubcopilot.com/mcp/") == "api.githubcopilot.com"


# -- what counts as an image --------------------------------------------------


def test_images_are_recognised_by_their_bytes():
    assert sniff(PNG) == "image/png"
    assert sniff(b"GIF89a...") == "image/gif"
    assert sniff(b"\x00\x00\x01\x00rest") == "image/x-icon"
    assert sniff(b'<svg xmlns="http://www.w3.org/2000/svg"/>') == "image/svg+xml"


def test_html_is_not_an_image():
    """A 404 page served as a favicon must not become the icon."""
    assert sniff(b"<!DOCTYPE html><html><body>Not found</body></html>") is None
    assert sniff(b"") is None
    # RIFF without the WEBP tag is some other RIFF file, not an image.
    assert sniff(b"RIFF....AVI ") is None


def test_bigger_declared_icons_are_preferred():
    html = """
      <link rel="icon" href="/small.png" sizes="16x16">
      <link rel="apple-touch-icon" href="/big.png">
      <link rel="icon" href="/huge.png" sizes="192x192">
      <link rel="stylesheet" href="/not-an-icon.css">
    """
    picked = _pick_link(html, "https://example.com/")
    assert picked[0] == "https://example.com/huge.png"
    assert "https://example.com/not-an-icon.css" not in picked


# -- the endpoints ------------------------------------------------------------


def _server(client: TestClient, **kwargs) -> dict:
    body = {"name": "custom", "transport": "stdio", "command": "echo", **kwargs}
    res = client.post("/api/mcp/servers", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_an_uploaded_logo_is_served_back(client: TestClient):
    server = _server(client)
    assert server["has_icon"] is False

    res = client.put(f"/api/mcp/servers/{server['id']}/icon",
                     json={"data": base64.b64encode(PNG).decode()})
    assert res.status_code == 200
    assert res.json()["mime"] == "image/png"

    icon = client.get(f"/api/mcp/servers/{server['id']}/icon")
    assert icon.status_code == 200
    assert icon.headers["content-type"] == "image/png"
    assert icon.content == PNG
    assert client.get("/api/mcp/servers").json()["servers"][0]["has_icon"] is True


def test_an_upload_that_is_not_an_image_is_refused(client: TestClient):
    server = _server(client)
    res = client.put(
        f"/api/mcp/servers/{server['id']}/icon",
        json={"data": base64.b64encode(b"<html>gotcha</html>").decode()},
    )
    assert res.status_code == 400
    assert "not a PNG" in res.json()["detail"]


def test_malformed_base64_is_refused(client: TestClient):
    server = _server(client)
    res = client.put(f"/api/mcp/servers/{server['id']}/icon", json={"data": "not base64!!"})
    assert res.status_code == 400


def test_deleting_a_server_takes_its_uploaded_logo_with_it(client: TestClient):
    server = _server(client)
    client.put(f"/api/mcp/servers/{server['id']}/icon",
               json={"data": base64.b64encode(PNG).decode()})
    client.delete(f"/api/mcp/servers/{server['id']}")

    again = _server(client)
    # A new id, so the old upload cannot follow it.
    assert again["has_icon"] is False


def test_a_site_logo_is_shared_between_servers_on_that_domain(client: TestClient):
    """Two servers, one brand, one fetch: the cache is keyed by site, not by server."""
    store = client.app.state.store
    store.put_mcp_icon("site:example.com", mime="image/png", data=PNG, source="test")

    first = _server(client, name="one", transport="http",
                    url="https://example.com/mcp", command=None)
    second = _server(client, name="two", transport="http",
                     url="https://example.com/other", command=None)
    assert first["has_icon"] and second["has_icon"]
    assert client.get(f"/api/mcp/servers/{second['id']}/icon").content == PNG


def test_icon_fetching_can_be_switched_off(client: TestClient):
    assert client.get("/api/mcp/settings").json()["fetch_icons"] is True
    patched = client.patch("/api/mcp/settings", json={"fetch_icons": False})
    assert patched.json()["fetch_icons"] is False

    server = _server(client, name="off", transport="http",
                     url="https://example.com/mcp", command=None)
    res = client.post(f"/api/mcp/servers/{server['id']}/icon/refresh")
    assert res.status_code == 400
    assert "switched off" in res.json()["detail"]


def test_refresh_needs_somewhere_to_look(client: TestClient):
    server = _server(client)  # stdio, no homepage
    res = client.post(f"/api/mcp/servers/{server['id']}/icon/refresh")
    assert res.status_code == 400
    assert "no public website" in res.json()["detail"]


# -- importing a config -------------------------------------------------------


def test_transport_is_inferred_when_the_config_does_not_say():
    parsed = {s.name: s for s in parse_mcp_config({"mcpServers": {
        "local": {"command": "npx", "args": ["-y", "thing"]},
        "modern": {"url": "https://example.com/mcp"},
        "legacy": {"url": "https://example.com/sse"},
        "declared": {"type": "streamable-http", "url": "https://example.com/x"},
    }})}
    assert parsed["local"].transport == "stdio"
    assert parsed["modern"].transport == "http"
    assert parsed["legacy"].transport == "sse"
    assert parsed["declared"].transport == "http"


def test_entries_with_no_way_in_are_dropped():
    parsed = parse_mcp_config({"mcpServers": {
        "good": {"command": "npx"},
        "nothing": {"description": "names neither a command nor a url"},
        "disabled": {"command": "npx", "disabled": True},
    }})
    assert [s.name for s in parsed] == ["good"]


def test_args_pasted_as_one_string_are_split_like_a_shell():
    parsed = parse_mcp_config({"mcpServers": {
        "x": {"command": "python", "args": '-m srv "/a path/db.sqlite"'}
    }})
    assert parsed[0].args == ["-m", "srv", "/a path/db.sqlite"]


def test_the_bare_mapping_is_accepted_too():
    """People copy the inner object as often as the whole file."""
    assert [s.name for s in parse_mcp_config({"x": {"command": "npx"}})] == ["x"]


def test_an_unreadable_config_says_so():
    with pytest.raises(ImportError_):
        parse_mcp_config("not an object")
    with pytest.raises(ImportError_):
        parse_mcp_config({"mcpServers": {}})


def test_import_endpoint_reports_partial_success(client: TestClient):
    _server(client, name="taken")
    res = client.post("/api/mcp/servers/import", json={"config": {"mcpServers": {
        "fresh": {"command": "npx", "args": ["-y", "thing"]},
        "taken": {"command": "npx"},
    }}, "enabled": False})
    assert res.status_code == 200
    body = res.json()
    assert [s["name"] for s in body["added"]] == ["fresh"]
    assert body["skipped"][0]["name"] == "taken"


def test_import_derives_a_homepage_from_the_endpoint(client: TestClient):
    res = client.post("/api/mcp/servers/import", json={"config": {"mcpServers": {
        "notion": {"url": "https://mcp.notion.com/mcp"},
    }}, "enabled": False})
    assert res.json()["added"][0]["homepage"] == "mcp.notion.com"


def test_import_rejects_a_config_with_nothing_in_it(client: TestClient):
    assert client.post("/api/mcp/servers/import", json={"config": {}}).status_code == 400
