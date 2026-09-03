"""Tests for the accent endpoints -- /api/theme, and the two per-scope routes.

The palette itself is the client's arithmetic; what the server owns is the
*intent*, and the two things worth pinning down here are that an accent
survives a round trip unchanged and that clearing one is distinguishable from
turning colour off. Those are the same JSON shape everywhere else in the app,
and getting them the wrong way round is what would silently break inheritance.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(db_path=tmp_path / "accents.db", auth_token="test_token")
    return TestClient(
        create_app(settings), headers={"Authorization": "Bearer test_token"}
    )


def _session(client: TestClient) -> str:
    return client.post("/api/sessions", json={}).json()["id"]


def _project(client: TestClient, name: str = "Kitchen") -> str:
    return client.post("/api/projects", json={"name": name}).json()["id"]


def test_app_accent_is_absent_until_one_is_set(client: TestClient):
    assert client.get("/api/theme").json() == {"theme": None}


def test_app_accent_round_trips(client: TestClient):
    accent = {"mode": "preset", "preset": "ember", "strength": 0.6}
    assert client.put("/api/theme", json={"theme": accent}).status_code == 200
    assert client.get("/api/theme").json()["theme"] == accent


def test_only_what_was_chosen_is_stored(client: TestClient):
    """A preset does not come back carrying a null hue it never had."""
    client.put("/api/theme", json={"theme": {"mode": "preset", "preset": "iris"}})
    assert client.get("/api/theme").json()["theme"] == {
        "mode": "preset",
        "preset": "iris",
    }


def test_a_session_accent_reaches_the_listing(client: TestClient):
    session_id = _session(client)
    accent = {"mode": "custom", "hue": 40.0, "chroma": 0.13}
    client.put(f"/api/sessions/{session_id}/theme", json={"theme": accent})

    listed = client.get("/api/sessions").json()["sessions"][0]
    assert listed["theme"] == accent
    # And on the conversation itself, which is what the client reads when it
    # opens one directly rather than from the list.
    assert client.get(f"/api/sessions/{session_id}").json()["session"]["theme"] == accent


def test_a_project_accent_reaches_the_listing(client: TestClient):
    project_id = _project(client)
    client.put(f"/api/projects/{project_id}/theme", json={"theme": {"mode": "auto"}})
    listed = client.get("/api/projects").json()["projects"][0]
    assert listed["theme"] == {"mode": "auto"}


def test_clearing_an_accent_is_not_turning_it_off(client: TestClient):
    """The distinction the whole three-scope stack rests on.

    Cleared means "this scope has not decided" and the project above it gets
    to; off is a decision, and it stops the project's colour reaching the
    chat. They have to stay distinguishable through the database.
    """
    session_id = _session(client)

    client.put(f"/api/sessions/{session_id}/theme", json={"theme": {"mode": "off"}})
    assert client.get("/api/sessions").json()["sessions"][0]["theme"] == {"mode": "off"}

    client.put(f"/api/sessions/{session_id}/theme", json={"theme": None})
    assert client.get("/api/sessions").json()["sessions"][0]["theme"] is None


def test_an_accent_does_not_bump_the_conversation(client: TestClient):
    """Recolouring a chat is not a change to the chat.

    `updated_at` orders the rail, so touching it here would jump a conversation
    to the top of the list for a reason that has nothing to do with what is in
    it.
    """
    session_id = _session(client)
    before = client.get("/api/sessions").json()["sessions"][0]["updated_at"]
    client.put(
        f"/api/sessions/{session_id}/theme",
        json={"theme": {"mode": "preset", "preset": "fern"}},
    )
    assert client.get("/api/sessions").json()["sessions"][0]["updated_at"] == before


@pytest.mark.parametrize(
    "accent",
    [
        {"mode": "preset"},              # a preset that names none
        {"mode": "custom"},              # a custom accent with no hue
        {"mode": "chartreuse"},          # not a mode
        {"mode": "custom", "hue": 400},  # off the circle
        {"mode": "custom", "hue": 40, "chroma": 0.9},  # past the ceiling
        {"mode": "preset", "preset": "Ember!"},  # not an id
        {"mode": "auto", "strength": 4},
    ],
)
def test_incoherent_accents_are_refused(client: TestClient, accent: dict):
    assert client.put("/api/theme", json={"theme": accent}).status_code == 422


def test_the_most_saturated_preset_is_expressible_as_a_custom_hue(client: TestClient):
    """"Pick a hue" starts from the accent already in force.

    Cobalt is the most saturated of the named accents, so the chroma ceiling
    has to be above it -- otherwise dialling a hue while cobalt is on is a 422.
    """
    accent = {"mode": "custom", "hue": 264.5, "chroma": 0.216}
    assert client.put("/api/theme", json={"theme": accent}).status_code == 200
    assert client.get("/api/theme").json()["theme"] == accent


def test_accents_need_something_to_attach_to(client: TestClient):
    assert client.put("/api/sessions/nope/theme", json={"theme": None}).status_code == 404
    assert client.put("/api/projects/nope/theme", json={"theme": None}).status_code == 404


def test_an_unreadable_stored_accent_reads_as_none(client: TestClient):
    """A preference written by an older shape is stale, not fatal.

    The app has a perfectly good palette without an accent, so the failure mode
    for a row that no longer parses is "nothing chosen" -- and the next thing
    the reader picks overwrites it.
    """
    client.app.state.store.set_text_setting("theme.app", "{not json at all")
    assert client.get("/api/theme").json() == {"theme": None}

    client.put("/api/theme", json={"theme": {"mode": "preset", "preset": "teal"}})
    assert client.get("/api/theme").json()["theme"]["preset"] == "teal"
