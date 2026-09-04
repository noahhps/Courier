"""The user's photo library, through PhotoKit.

The same argument as the calendar: the pictures are already in Photos.app, and
the useful thing is to work on those rather than to keep a second copy.

Additive only, and here that word means more than usual. An album in Photos is
a *reference* to assets, not a container holding them: adding a picture to one
does not move it, copy it, or take it out of anywhere else, and deleting the
album later leaves every original untouched. That is what makes "organise my
photos" safe to hand to a local model -- the worst case is an album nobody
wanted, which the person removes in one gesture.

Nothing here deletes an asset, edits one, or reads its bytes. Metadata and
album membership are the whole surface.

One thing this deliberately does not do is turn coordinates into place names.
That needs a reverse geocoder, which means Apple's servers, and a module in a
project whose rule is "nothing leaves the machine" should not quietly make a
network call. Coordinates come back as numbers and the caller decides.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

try:  # pragma: no cover - the import is the platform check
    import Photos
    import Foundation

    _IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover
    Photos = None  # type: ignore[assignment]
    Foundation = None  # type: ignore[assignment]
    _IMPORT_ERROR = str(exc)


# PHAuthorizationStatus. `Limited` is the one that matters here and has no
# equivalent in EventKit: the user picked specific photos to share, so the
# library reads as though it contains only those. That is a legitimate answer,
# not a failure, and the skills say so rather than pretending the library is
# small.
NOT_DETERMINED = 0
RESTRICTED = 1
DENIED = 2
AUTHORIZED = 3
LIMITED = 4

# PHAccessLevel
READ_WRITE = 2

PROMPT_TIMEOUT = 60.0


@dataclass(frozen=True)
class DevicePhoto:
    identifier: str
    created_at: str | None  # YYYY-MM-DDTHH:MM
    latitude: float | None
    longitude: float | None
    width: int
    height: int
    favourite: bool
    kind: str  # image | video


def available() -> bool:
    return Photos is not None


def unavailable_reason() -> str | None:
    if Photos is None:
        return (
            "The photo library is only reachable on macOS, and the PhotoKit "
            f"bridge is not installed here ({_IMPORT_ERROR})."
        )
    return None


def authorization_status() -> int:
    if Photos is None:
        return DENIED
    return Photos.PHPhotoLibrary.authorizationStatusForAccessLevel_(READ_WRITE)


def describe_status(status: int) -> str:
    return {
        NOT_DETERMINED: "not yet asked",
        RESTRICTED: "blocked by policy on this machine",
        DENIED: "refused",
        AUTHORIZED: "granted",
        LIMITED: "granted for selected photos only",
    }.get(status, f"unknown ({status})")


def request_access(timeout: float = PROMPT_TIMEOUT) -> tuple[bool, str | None]:
    """Ask for read/write access to the library. Returns (granted, reason).

    `Limited` counts as granted: the user chose to share a subset, and refusing
    to work with it would be overruling a decision they made deliberately.
    """
    if Photos is None:
        return False, unavailable_reason()

    status = authorization_status()
    if status in (AUTHORIZED, LIMITED):
        return True, None
    if status in (DENIED, RESTRICTED):
        return False, (
            f"Photo access is {describe_status(status)}. Change it in System "
            "Settings > Privacy & Security > Photos, then ask me again."
        )

    done = threading.Event()
    outcome: dict[str, int] = {"status": NOT_DETERMINED}

    def handler(new_status):
        outcome["status"] = int(new_status)
        done.set()

    Photos.PHPhotoLibrary.requestAuthorizationForAccessLevel_handler_(READ_WRITE, handler)
    if not done.wait(timeout):
        return False, (
            "macOS asked for photo permission and nothing answered within "
            f"{int(timeout)} seconds. The prompt may be behind another window."
        )
    if outcome["status"] not in (AUTHORIZED, LIMITED):
        return False, f"Photo access was {describe_status(outcome['status'])}."
    return True, None


def _to_nsdate(when: datetime):
    return Foundation.NSDate.dateWithTimeIntervalSince1970_(when.timestamp())


def _asset(a) -> DevicePhoto:
    created = a.creationDate()
    location = a.location()
    coord = location.coordinate() if location else None
    return DevicePhoto(
        identifier=str(a.localIdentifier()),
        created_at=(
            datetime.fromtimestamp(created.timeIntervalSince1970()).strftime("%Y-%m-%dT%H:%M")
            if created
            else None
        ),
        latitude=float(coord.latitude) if coord else None,
        longitude=float(coord.longitude) if coord else None,
        width=int(a.pixelWidth()),
        height=int(a.pixelHeight()),
        favourite=bool(a.isFavorite()),
        kind="video" if int(a.mediaType()) == 2 else "image",
    )


def list_photos(
    since: datetime | None = None,
    until: datetime | None = None,
    *,
    limit: int = 200,
) -> list[DevicePhoto]:
    """Assets in a date window, newest first."""
    options = Photos.PHFetchOptions.alloc().init()
    options.setSortDescriptors_(
        [Foundation.NSSortDescriptor.sortDescriptorWithKey_ascending_("creationDate", False)]
    )
    if since and until:
        options.setPredicate_(
            Foundation.NSPredicate.predicateWithFormat_(
                "creationDate >= %@ AND creationDate < %@", _to_nsdate(since), _to_nsdate(until)
            )
        )
    options.setFetchLimit_(max(1, min(int(limit), 1000)))

    result = Photos.PHAsset.fetchAssetsWithOptions_(options)
    return [_asset(result.objectAtIndex_(i)) for i in range(result.count())]


def albums() -> list[tuple[str, int]]:
    """Every user-made album, as (title, count)."""
    found = Photos.PHAssetCollection.fetchAssetCollectionsWithType_subtype_options_(
        Photos.PHAssetCollectionTypeAlbum, Photos.PHAssetCollectionSubtypeAny, None
    )
    out = []
    for i in range(found.count()):
        collection = found.objectAtIndex_(i)
        assets = Photos.PHAsset.fetchAssetsInAssetCollection_options_(collection, None)
        out.append((str(collection.localizedTitle()), int(assets.count())))
    return sorted(out)


def _perform(changes) -> tuple[bool, str | None]:
    """Run a PhotoKit change block and wait for it, the sync way.

    Every mutation in PhotoKit goes through `performChanges:` and reports back
    on another queue. Blocking here keeps the skills straight-line, which is
    what they need to be: a skill returns a sentence, not a promise.
    """
    done = threading.Event()
    outcome: dict[str, object] = {"ok": False, "error": None}

    def completion(success, error):
        outcome["ok"] = bool(success)
        outcome["error"] = str(error) if error else None
        done.set()

    Photos.PHPhotoLibrary.sharedPhotoLibrary().performChanges_completionHandler_(
        changes, completion
    )
    if not done.wait(30.0):
        return False, "The photo library did not answer in time."
    return bool(outcome["ok"]), outcome["error"]  # type: ignore[return-value]


def find_album(title: str):
    options = Photos.PHFetchOptions.alloc().init()
    options.setPredicate_(
        Foundation.NSPredicate.predicateWithFormat_("localizedTitle == %@", title)
    )
    found = Photos.PHAssetCollection.fetchAssetCollectionsWithType_subtype_options_(
        Photos.PHAssetCollectionTypeAlbum, Photos.PHAssetCollectionSubtypeAny, options
    )
    return found.objectAtIndex_(0) if found.count() else None


def create_album(title: str) -> tuple[bool, str | None]:
    if find_album(title) is not None:
        return False, f"There is already an album called {title!r}."

    def changes():
        Photos.PHAssetCollectionChangeRequest.creationRequestForAssetCollectionWithTitle_(title)

    ok, error = _perform(changes)
    if not ok:
        return False, f"macOS refused to create the album: {error}"
    return True, None


def add_to_album(title: str, identifiers: list[str]) -> tuple[int, str | None]:
    """Add assets to an existing album by local identifier. Returns (added, error)."""
    collection = find_album(title)
    if collection is None:
        return 0, f"There is no album called {title!r}."

    fetched = Photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_(identifiers, None)
    if not fetched.count():
        return 0, "None of those photo ids are in the library."

    assets = [fetched.objectAtIndex_(i) for i in range(fetched.count())]

    def changes():
        request = Photos.PHAssetCollectionChangeRequest.changeRequestForAssetCollection_(
            collection
        )
        request.addAssets_(assets)

    ok, error = _perform(changes)
    if not ok:
        return 0, f"macOS refused to add the photos: {error}"
    return len(assets), None
