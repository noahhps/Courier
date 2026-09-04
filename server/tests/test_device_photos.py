"""The photo skills, with PhotoKit stubbed out.

Same shape as the calendar tests, and the same priority: the interesting cases
are the ones where the library is not fully available, because a partial
library reported as a whole one is how an assistant ends up saying "you only
took twelve photos in Japan".
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.device import mac_photos as backend
from app.skills import device_photos as skills


@dataclass(frozen=True)
class FakePhoto:
    identifier: str = "A1/L0/001"
    created_at: str | None = "2026-08-14T10:22"
    latitude: float | None = 37.4419
    longitude: float | None = -122.1430
    width: int = 4032
    height: int = 3024
    favourite: bool = False
    kind: str = "image"


@pytest.fixture
def granted(monkeypatch):
    monkeypatch.setattr(backend, "available", lambda: True)
    monkeypatch.setattr(backend, "unavailable_reason", lambda: None)
    monkeypatch.setattr(backend, "request_access", lambda timeout=60.0: (True, None))
    monkeypatch.setattr(backend, "authorization_status", lambda: backend.AUTHORIZED)
    return backend


# -- availability and refusal --------------------------------------------------


@pytest.mark.anyio
async def test_unavailable_platform_explains_itself(monkeypatch):
    monkeypatch.setattr(backend, "available", lambda: False)
    monkeypatch.setattr(backend, "unavailable_reason", lambda: "only on macOS")
    assert "only on macOS" in await skills.ListPhotos().use()


@pytest.mark.anyio
async def test_refusal_reaches_the_model_as_a_sentence(monkeypatch):
    monkeypatch.setattr(backend, "available", lambda: True)
    monkeypatch.setattr(
        backend, "request_access", lambda timeout=60.0: (False, "Photo access was refused.")
    )
    assert "refused" in (await skills.ListPhotos().use()).lower()


def test_skills_are_unavailable_without_the_bridge(monkeypatch):
    monkeypatch.setattr(backend, "available", lambda: False)
    assert skills.ListPhotos().available is False


# -- the partial-library case --------------------------------------------------


@pytest.mark.anyio
async def test_a_limited_library_says_so_in_the_result(granted, monkeypatch):
    """The user shared some photos, not all. The model has to be told."""
    monkeypatch.setattr(backend, "authorization_status", lambda: backend.LIMITED)
    monkeypatch.setattr(backend, "list_photos", lambda *a, **k: [FakePhoto()])
    out = await skills.ListPhotos().use(30)
    assert "only selected photos" in out


@pytest.mark.anyio
async def test_a_full_library_does_not_add_the_caveat(granted, monkeypatch):
    monkeypatch.setattr(backend, "list_photos", lambda *a, **k: [FakePhoto()])
    out = await skills.ListPhotos().use(30)
    assert "only selected photos" not in out


# -- listing -------------------------------------------------------------------


@pytest.mark.anyio
async def test_listing_reports_coordinates_not_place_names(granted, monkeypatch):
    monkeypatch.setattr(backend, "list_photos", lambda *a, **k: [FakePhoto()])
    out = await skills.ListPhotos().use()
    assert "37.4419,-122.1430" in out
    assert "1 have a location" in out


@pytest.mark.anyio
async def test_a_photo_without_a_location_says_so(granted, monkeypatch):
    monkeypatch.setattr(
        backend, "list_photos", lambda *a, **k: [FakePhoto(latitude=None, longitude=None)]
    )
    out = await skills.ListPhotos().use()
    assert "no location" in out
    assert "0 have a location" in out


@pytest.mark.anyio
async def test_empty_window_says_so(granted, monkeypatch):
    monkeypatch.setattr(backend, "list_photos", lambda *a, **k: [])
    assert "No photos" in await skills.ListPhotos().use(7)


# -- albums --------------------------------------------------------------------


@pytest.mark.anyio
async def test_albums_are_listed_with_counts(granted, monkeypatch):
    monkeypatch.setattr(backend, "albums", lambda: [("Japan 2026", 84), ("Screenshots", 12)])
    out = await skills.ListAlbums().use()
    assert "Japan 2026  (84)" in out


@pytest.mark.anyio
async def test_creating_a_duplicate_album_is_refused_with_a_reason(granted, monkeypatch):
    monkeypatch.setattr(
        backend, "create_album", lambda t: (False, "There is already an album called 'Japan'.")
    )
    assert "already an album" in await skills.CreateAlbum().use("Japan")


@pytest.mark.anyio
async def test_creating_an_album_points_at_the_next_step(granted, monkeypatch):
    monkeypatch.setattr(backend, "create_album", lambda t: (True, None))
    assert "add_to_album" in await skills.CreateAlbum().use("Korea")


@pytest.mark.anyio
async def test_empty_album_name_is_refused_before_permission(monkeypatch):
    asked = []
    monkeypatch.setattr(backend, "available", lambda: True)
    monkeypatch.setattr(
        backend, "request_access", lambda timeout=60.0: (asked.append(1), (True, None))[1]
    )
    out = await skills.CreateAlbum().use("   ")
    assert "name" in out.lower()
    assert asked == []


@pytest.mark.anyio
async def test_adding_accepts_a_comma_separated_string(granted, monkeypatch):
    seen = {}

    def add(title, ids):
        seen["ids"] = ids
        return len(ids), None

    monkeypatch.setattr(backend, "add_to_album", add)
    out = await skills.AddToAlbum().use("Korea", "a,b,c")
    assert seen["ids"] == ["a", "b", "c"]
    assert "Added 3 photos" in out


@pytest.mark.anyio
async def test_adding_to_a_missing_album_says_which(granted, monkeypatch):
    monkeypatch.setattr(
        backend, "add_to_album", lambda t, i: (0, "There is no album called 'Nope'.")
    )
    assert "no album called" in await skills.AddToAlbum().use("Nope", ["a"])


@pytest.mark.anyio
async def test_adding_one_photo_is_singular(granted, monkeypatch):
    monkeypatch.setattr(backend, "add_to_album", lambda t, i: (1, None))
    out = await skills.AddToAlbum().use("Korea", ["a"])
    assert "Added 1 photo to" in out


# -- the crash that made every list_photos call fail ---------------------------


class _TupleCoord:
    """PyObjC on this build bridges CLLocationCoordinate2D as a plain tuple."""

    def coordinate(self):
        return (37.4419, -122.1430)


class _StructCoord:
    """Other builds hand back something with attributes."""

    class _C:
        latitude = 51.5072
        longitude = -0.1276

    def coordinate(self):
        return self._C()


def test_coordinates_survive_a_tuple_bridge():
    lat, lon = backend._coordinate(_TupleCoord())
    assert (round(lat, 4), round(lon, 4)) == (37.4419, -122.143)


def test_coordinates_survive_a_struct_bridge():
    lat, lon = backend._coordinate(_StructCoord())
    assert (round(lat, 4), round(lon, 4)) == (51.5072, -0.1276)


def test_a_photo_with_no_location_is_not_an_error():
    assert backend._coordinate(None) == (None, None)
