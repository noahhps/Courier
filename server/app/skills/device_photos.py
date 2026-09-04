"""The user's photo library, as skills.

Four verbs, chosen so the job in the QuickView sketch -- "reorganise last
month's photos by where they were taken" -- is expressible without inventing a
fifth: look at what is there, see the albums that exist, make one, put photos
in it.

Coordinates come back as numbers rather than place names. Turning 37.44,-122.16
into "Los Altos" needs a reverse geocoder, and every one of those is a network
call to somebody's server; a project that keeps its model on loopback should
not post the user's location history to Apple to prettify a listing. The model
is perfectly able to say "these are all within a mile of each other" from the
numbers, and if the user wants real place names that is a decision to take
deliberately rather than one to inherit from a helper function.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..device import mac_photos as backend
from .skill import Skill

MAX_LISTED = 60


def _describe(photo) -> str:
    when = photo.created_at or "date unknown"
    where = (
        f"{photo.latitude:.4f},{photo.longitude:.4f}"
        if photo.latitude is not None and photo.longitude is not None
        else "no location"
    )
    star = " ★" if photo.favourite else ""
    return f"{photo.identifier}  {when}  {where}  {photo.width}x{photo.height} {photo.kind}{star}"


class _PhotoSkill(Skill):
    @property
    def available(self) -> bool:
        return backend.available()

    def _ready(self) -> str | None:
        if not backend.available():
            return backend.unavailable_reason()
        granted, reason = backend.request_access()
        if not granted:
            return reason
        if backend.authorization_status() == backend.LIMITED:
            # Said once, up front. A model that does not know the library is
            # partial will report "you have 12 photos" about a library of
            # twelve thousand.
            return None
        return None


class ListPhotos(_PhotoSkill):
    def __init__(self) -> None:
        super().__init__(
            name="list_photos",
            description=(
                "List photos and videos from the user's library over the last N "
                "days, newest first. Each line gives an id, when it was taken, "
                "and where as latitude,longitude — group by those coordinates "
                "yourself; there are no place names."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "How far back to look. Defaults to 30.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "How many at most. Defaults to 60.",
                    },
                },
            },
            requires="permission to read this Mac's photo library",
        )

    async def use(self, days: int = 30, limit: int = MAX_LISTED) -> str:
        problem = self._ready()
        if problem:
            return problem
        try:
            span = max(1, min(int(days), 3650))
        except (TypeError, ValueError):
            span = 30
        try:
            cap = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            cap = MAX_LISTED

        now = datetime.now()
        photos = backend.list_photos(now - timedelta(days=span), now + timedelta(days=1), limit=cap)
        if not photos:
            return f"No photos in the library from the last {span} days."

        partial = backend.authorization_status() == backend.LIMITED
        head = f"{len(photos)} from the last {span} days"
        if partial:
            head += " (the user has shared only selected photos, so this is not the whole library)"
        located = sum(1 for p in photos if p.latitude is not None)
        head += f"; {located} have a location."
        return head + "\n" + "\n".join(_describe(p) for p in photos)


class ListAlbums(_PhotoSkill):
    def __init__(self) -> None:
        super().__init__(
            name="list_albums",
            description=(
                "The albums the user already has, with how many photos are in "
                "each. Check before creating one, so you add to theirs rather "
                "than making a second album with a similar name."
            ),
            parameters={"type": "object", "properties": {}},
            requires="permission to read this Mac's photo library",
        )

    async def use(self) -> str:
        problem = self._ready()
        if problem:
            return problem
        found = backend.albums()
        if not found:
            return "There are no albums in the library yet."
        return "\n".join(f"{title}  ({count})" for title, count in found)


class CreateAlbum(_PhotoSkill):
    def __init__(self) -> None:
        super().__init__(
            name="create_album",
            description=(
                "Make a new, empty album in the user's photo library, then use "
                "add_to_album to fill it. Creating an album never moves or "
                "copies anything."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Name for the album."}
                },
                "required": ["title"],
            },
            requires="permission to change this Mac's photo library",
        )

    async def use(self, title: str) -> str:
        name = (title or "").strip()
        if not name:
            return "Give the album a name."
        problem = self._ready()
        if problem:
            return problem
        ok, error = backend.create_album(name)
        if not ok:
            return error
        return f"Created the album {name!r}. It is empty; add photos with add_to_album."


class AddToAlbum(_PhotoSkill):
    def __init__(self) -> None:
        super().__init__(
            name="add_to_album",
            description=(
                "Put photos into an existing album, by the ids from list_photos. "
                "This adds a reference: the photos stay where they are and are "
                "not moved out of anything else."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "album": {"type": "string", "description": "The album's name."},
                    "photo_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ids from list_photos.",
                    },
                },
                "required": ["album", "photo_ids"],
            },
            requires="permission to change this Mac's photo library",
        )

    async def use(self, album: str, photo_ids: list[str]) -> str:
        name = (album or "").strip()
        if not name:
            return "Which album?"
        # A model that has been told to pass an array will occasionally pass a
        # comma-separated string instead. Accepting both costs two lines and
        # saves a round.
        if isinstance(photo_ids, str):
            photo_ids = [p.strip() for p in photo_ids.split(",") if p.strip()]
        if not photo_ids:
            return "Give me the ids of the photos to add, from list_photos."

        problem = self._ready()
        if problem:
            return problem
        added, error = backend.add_to_album(name, list(photo_ids))
        if error:
            return error
        return f"Added {added} photo{'s' if added != 1 else ''} to {name!r}."
