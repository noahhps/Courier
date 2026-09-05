"""Choosing a provider and a model over HTTP.

The providers themselves are stubbed: what is under test is the surface the
client drives -- what /models says, what a chosen model does to the running
server and to the next boot of it, and the two ways OpenRouter gets connected.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers import model_setting_key

CATALOGUE = {
    "local": [
        {"id": "gpt-oss", "name": "gpt-oss", "reasoning": True},
        {"id": "gemma4:12b", "name": "gemma4:12b", "reasoning": False},
    ],
    "cloud": [{"id": "claude-opus-5", "name": "Claude Opus 5"}],
    "openrouter": [
        {"id": "openrouter/auto", "name": "Auto"},
        {"id": "anthropic/claude-sonnet-4.5", "name": "Claude Sonnet 4.5"},
    ],
}


def stub(app, *, healthy=("local", "openrouter"), configured=True) -> None:
    """Answer every provider question from memory rather than the network."""
    providers = app.state.providers
    for provider_id, provider in providers.by_id.items():
        provider.health = AsyncMock(return_value=provider_id in healthy)
        provider.list_models = AsyncMock(return_value=CATALOGUE[provider_id])
    providers.openrouter.account = AsyncMock(return_value={"label": "courier", "usage": 1.5})
    if configured:
        providers.openrouter.set_api_key("sk-or-existing")
    # The router caches local health for a few seconds; these tests change it
    # between calls, so the cache has to go with it.
    providers.invalidate_health()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "providers.db",
        auth_token="test_token",
        openrouter_key_path=tmp_path / "openrouter_key",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    stub(app)
    return TestClient(app, headers={"Authorization": "Bearer test_token"})


# -- what the picker reads ----------------------------------------------------


def test_models_lists_every_backend_with_its_catalogue(client: TestClient):
    payload = client.get("/api/models").json()["providers"]
    assert [p["id"] for p in payload] == ["local", "cloud", "openrouter"]
    local = payload[0]
    assert local["healthy"] is True
    assert [m["id"] for m in local["models"]] == ["gpt-oss", "gemma4:12b"]
    # The control a model's family takes travels with it, so the composer can
    # redraw when the provider changes without keeping a name list in the browser.
    assert local["thinking"]["mode"] == "effort"
    assert payload[2]["account"] == {"label": "courier", "usage": 1.5}


def test_one_unreachable_backend_does_not_blank_the_others(client: TestClient, settings):
    client.app.state.providers.local.list_models = AsyncMock(side_effect=OSError("no ollama"))
    payload = {p["id"]: p for p in client.get("/api/models").json()["providers"]}
    assert payload["local"]["models"] == []
    assert "no ollama" in payload["local"]["error"]
    assert payload["openrouter"]["models"]  # still choosable


def test_status_names_which_backend_would_answer(client: TestClient):
    body = client.get("/api/status").json()
    assert body["serving"] == "local"
    assert body["local"]["url"].startswith("http")
    assert {p["id"] for p in body["providers"]} == {"local", "cloud", "openrouter"}

    # Local down and OpenRouter connected: auto walks to the fallback in the
    # same order `resolve()` does, so this is genuinely what the next turn uses.
    stub(client.app, healthy=("openrouter",))
    assert client.get("/api/status").json()["serving"] == "openrouter"

    stub(client.app, healthy=())
    assert client.get("/api/status").json()["serving"] == "none"


# -- choosing a model ---------------------------------------------------------


def test_a_chosen_model_takes_effect_and_survives_a_restart(client, settings):
    response = client.put("/api/providers/local/model", json={"model": "gemma4:12b"})
    assert response.status_code == 200
    assert response.json()["model"] == "gemma4:12b"
    # In force for the process that was told, including the two off-path
    # passes -- titling and curation -- which have no request to carry a choice.
    assert client.app.state.providers.local.model == "gemma4:12b"
    assert client.app.state.store.get_text_setting(model_setting_key("local")) == "gemma4:12b"

    rebooted = create_app(settings)
    assert rebooted.state.providers.local.model == "gemma4:12b"


def test_a_model_that_does_not_exist_is_refused_with_the_fix(client: TestClient):
    response = client.put("/api/providers/local/model", json={"model": "gpt-oss:1t"})
    assert response.status_code == 400
    assert "ollama pull" in response.json()["detail"]
    assert client.app.state.providers.local.model != "gpt-oss:1t"


def test_an_unknown_model_is_accepted_when_the_listing_is_down(client: TestClient):
    # A listing that happens to be unreachable is not a reason to refuse a
    # model the caller may well know exists.
    client.app.state.providers.openrouter.list_models = AsyncMock(side_effect=OSError("down"))
    response = client.put("/api/providers/openrouter/model", json={"model": "x/y"})
    assert response.status_code == 200
    assert client.app.state.providers.openrouter.model == "x/y"


def test_an_unknown_provider_is_a_404(client: TestClient):
    assert client.put("/api/providers/groq/model", json={"model": "x"}).status_code == 404


def test_choosing_a_model_needs_the_token(settings):
    app = create_app(settings)
    stub(app)
    anonymous = TestClient(app)
    assert anonymous.put("/api/providers/local/model", json={"model": "gpt-oss"}).status_code == 401


# -- connecting OpenRouter with a key -----------------------------------------


def test_a_key_is_checked_before_it_is_written_down(client: TestClient, settings):
    client.app.state.providers.openrouter.health = AsyncMock(return_value=False)
    response = client.put("/api/providers/openrouter/key", json={"key": "sk-or-wrong"})
    assert response.status_code == 400
    # Neither the running provider nor the disk keeps a key that was refused.
    assert client.app.state.providers.openrouter.api_key == "sk-or-existing"
    assert not settings.openrouter_key_path.exists()


def test_a_good_key_is_persisted_and_an_empty_one_clears_it(client: TestClient, settings):
    body = client.put("/api/providers/openrouter/key", json={"key": "sk-or-good"}).json()
    assert body["configured"] is True
    assert settings.openrouter_key_path.read_text() == "sk-or-good"

    body = client.put("/api/providers/openrouter/key", json={"key": ""}).json()
    assert body["configured"] is False
    # Disconnecting deletes the file rather than leaving a blank one, so what
    # is on disk matches what is in memory.
    assert not settings.openrouter_key_path.exists()


def test_a_saved_key_is_picked_up_at_boot(settings):
    settings.openrouter_key_path.parent.mkdir(parents=True, exist_ok=True)
    settings.openrouter_key_path.write_text("sk-or-from-disk")
    from app.config import load_settings

    # `load_settings` is what reads it; Settings() alone is only the defaults.
    import os

    os.environ["DB_PATH"] = str(settings.db_path)
    os.environ["OPENROUTER_KEY_PATH"] = str(settings.openrouter_key_path)
    try:
        assert load_settings().openrouter_api_key == "sk-or-from-disk"
    finally:
        del os.environ["DB_PATH"], os.environ["OPENROUTER_KEY_PATH"]


# -- connecting OpenRouter by signing in --------------------------------------


def test_a_sign_in_hands_back_a_url_and_can_be_polled(client: TestClient):
    started = client.post(
        "/api/providers/openrouter/signin", json={"callback_base": "http://10.0.0.4:8080"}
    ).json()
    assert started["url"].startswith("https://openrouter.ai/auth?")
    assert started["callback"].endswith("/openrouter/callback/" + started["state"])

    polled = client.get(f"/api/providers/openrouter/signin/{started['state']}").json()
    assert polled["status"] == "pending"
    # A state nobody minted is not an error, it is simply not a sign-in.
    assert client.get("/api/providers/openrouter/signin/nope").json()["status"] == "unknown"


def test_a_callback_base_a_browser_cannot_return_to_is_refused(client: TestClient):
    response = client.post(
        "/api/providers/openrouter/signin", json={"callback_base": "tauri://localhost"}
    )
    assert response.status_code == 400


def test_the_callback_stores_the_key_without_a_token(client: TestClient, settings):
    started = client.post(
        "/api/providers/openrouter/signin", json={"callback_base": "http://127.0.0.1:8080"}
    ).json()
    client.app.state.openrouter_oauth.complete = AsyncMock(return_value="sk-or-v1-minted")

    # No Authorization header: the browser arrives here from openrouter.ai, and
    # the state in the path is what stands in for the token.
    anonymous = TestClient(client.app)
    landed = anonymous.get(f"/openrouter/callback/{started['state']}?code=abc")
    assert landed.status_code == 200
    assert "OpenRouter connected" in landed.text
    # And the key itself is never on the page a browser was just handed.
    assert "sk-or-v1-minted" not in landed.text

    assert client.app.state.providers.openrouter.api_key == "sk-or-v1-minted"
    assert settings.openrouter_key_path.read_text() == "sk-or-v1-minted"


def test_a_refused_sign_in_says_why_on_the_page(client: TestClient, settings):
    landed = TestClient(client.app).get("/openrouter/callback/stale?code=abc")
    assert landed.status_code == 400
    assert "expired" in landed.text
    assert not settings.openrouter_key_path.exists()


def test_a_cancelled_sign_in_is_not_an_error_page_about_something_else(client: TestClient):
    landed = TestClient(client.app).get("/openrouter/callback/whatever?error=access_denied")
    assert "Sign-in cancelled" in landed.text
    assert "access_denied" in landed.text


# -- a turn, end to end -------------------------------------------------------


def test_a_message_can_be_answered_by_openrouter(client: TestClient, settings):
    """The whole path: /api/chat -> orchestrator -> the OpenRouter encoder.

    The one test here that is not about the picker. It exists because every
    other test in this file would still pass if the provider could not
    actually answer a message -- and the two ends of this path were written
    against different vocabularies (`prefer="openrouter"`, `provider.name`),
    which is exactly the kind of seam that only fails in front of a person.
    """
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        body = (
            b'data: {"choices":[{"delta":{"content":"Two"}}]}\n\n'
            b'data: {"model":"anthropic/claude-sonnet-4.5",'
            b'"choices":[{"delta":{},"finish_reason":"stop"}],'
            b'"usage":{"prompt_tokens":9,"completion_tokens":1}}\n\n'
            b"data: [DONE]\n\n"
        )
        return httpx.Response(200, content=body)

    openrouter = client.app.state.providers.openrouter
    openrouter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=openrouter.base_url
    )

    with client.stream(
        "POST", "/api/chat", json={"message": "one plus one?", "provider": "openrouter"}
    ) as response:
        assert response.status_code == 200
        frames = "".join(response.iter_text())

    assert sent["model"] == "openrouter/auto"
    assert sent["messages"][-1] == {"role": "user", "content": "one plus one?"}
    # The client is told which backend answered and with what, on the frame
    # that opens the reply -- never switched silently.
    assert '"provider": "openrouter"' in frames
    assert '"source": "fallback"' in frames
    assert '"text": "Two"' in frames

    # And it is on the message, so reopening the conversation still says so.
    session_id = client.get("/api/sessions").json()["sessions"][0]["id"]
    stored = client.get(f"/api/sessions/{session_id}").json()["messages"]
    answer = [m for m in stored if m["role"] == "assistant"][-1]
    assert answer["content"] == "Two"
    assert answer["provider"] == "openrouter"
