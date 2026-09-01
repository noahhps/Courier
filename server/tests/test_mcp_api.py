"""Tests for MCP API endpoints (/api/mcp)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        db_path=tmp_path / "test_api.db",
        auth_token="test_token",
    )
    app = create_app(settings)
    # Mock sync_server so API unit tests do not spawn external network subprocesses
    if hasattr(app.state, "mcp_manager"):
        app.state.mcp_manager.sync_server = AsyncMock(return_value=["mock_tool_1", "mock_tool_2"])
    return TestClient(app, headers={"Authorization": "Bearer test_token"})


def test_list_presets_endpoint(client: TestClient):
    response = client.get("/api/mcp/presets")
    assert response.status_code == 200
    data = response.json()
    assert "presets" in data
    preset_names = {p["name"] for p in data["presets"]}
    assert "google_calendar" in preset_names
    assert "figma" in preset_names
    assert "github" in preset_names
    # Removed: it ran the same package as google_workspace against the same
    # OAuth grant, so having both registered every tool under two names.
    assert "gmail" not in preset_names


def test_mcp_server_crud_endpoints(client: TestClient):
    # 1. Create server
    payload = {
        "name": "custom_tools",
        "transport": "stdio",
        "command": "python3",
        "args": ["-m", "custom_mcp"],
        "env": {"DEBUG": "1"},
        "enabled": False,
        "description": "Custom tools server",
    }
    create_res = client.post("/api/mcp/servers", json=payload)
    assert create_res.status_code == 200
    created = create_res.json()
    assert created["name"] == "custom_tools"
    server_id = created["id"]

    # 2. List servers
    list_res = client.get("/api/mcp/servers")
    assert list_res.status_code == 200
    servers = list_res.json()["servers"]
    assert any(s["id"] == server_id for s in servers)

    # 3. Patch server
    patch_res = client.patch(
        f"/api/mcp/servers/{server_id}",
        json={"description": "Updated description", "enabled": False},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["description"] == "Updated description"

    # 4. Delete server
    del_res = client.delete(f"/api/mcp/servers/{server_id}")
    assert del_res.status_code == 200
    assert del_res.json()["ok"] is True

    # Confirm gone
    list_res2 = client.get("/api/mcp/servers")
    assert not any(s["id"] == server_id for s in list_res2.json()["servers"])


def test_instantiate_preset_endpoint(client: TestClient):
    """The Dev Mode preset needs nothing from the reader -- the desktop app is signed in."""
    res = client.post("/api/mcp/presets/instantiate", json={"preset": "figma"})
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "figma"
    assert data["transport"] == "http"
    assert data["url"] == "http://127.0.0.1:3845/mcp"
    assert "mock_tool_1" in data["tools"]


def test_instantiate_preset_substitutes_supplied_values(client: TestClient):
    payload = {"preset": "figma_api", "values": {"FIGMA_API_KEY": "figd_test_123"}}
    res = client.post("/api/mcp/presets/instantiate", json=payload)
    assert res.status_code == 200
    data = res.json()
    # The token reaches the subprocess as a real value, not as the literal
    # "${FIGMA_API_KEY}" the preset is written with.
    assert data["env"]["FIGMA_API_KEY"] == "figd_test_123"
    assert "--stdio" in data["args"]


def test_instantiate_preset_refuses_without_required_inputs(client: TestClient, monkeypatch):
    monkeypatch.delenv("FIGMA_API_KEY", raising=False)
    res = client.post("/api/mcp/presets/instantiate", json={"preset": "figma_api"})
    assert res.status_code == 400
    assert "FIGMA_API_KEY" in res.json()["detail"]


def test_websocket_transport_is_rejected(client: TestClient):
    """Nothing ever implemented ws://; accepting it only deferred the failure."""
    res = client.post(
        "/api/mcp/servers",
        json={"name": "ws_server", "transport": "websocket", "url": "ws://localhost:9/x"},
    )
    assert res.status_code == 422
