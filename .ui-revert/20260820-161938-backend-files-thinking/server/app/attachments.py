"""What may be attached to a message, and what it becomes.

Three kinds get through, because there are only so many things a model can
actually do with a file:

  image     -- handed to the model as an image, if it can see at all
  text      -- folded into the prompt as text, which every model can read
  document  -- a PDF or an Office file, read for its words and then folded in
               the same way; see extract.py

Anything else is refused at the door with a reason, rather than accepted and
silently ignored somewhere deeper where the user would never learn why the
answer made no sense.
"""

from __future__ import annotations

import base64
import binascii
import io
from dataclasses import dataclass
from pathlib import Path

from .extract import ExtractionError, document_suffix, extract

# One user, one server, so these exist to catch mistakes -- a video dragged in
# by accident -- not to ration a shared resource.
MAX_FILES = 10
MAX_BYTES_EACH = 12 * 1024 * 1024
MAX_BYTES_TOTAL = 24 * 1024 * 1024

# A text file is pasted into the prompt, so its size is measured in context
# rather than megabytes. Past this it is truncated with a visible marker: a
# clipped file the model can reason about beats a prompt that cannot fit.
MAX_TEXT_CHARS = 40_000

# What may be attached. SVG is deliberately absent: it is a document, not a
# raster, and no model takes it as an image.
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

# What may be *stored*. Ollama's runner rejects WebP outright -- "Failed to
# load image or audio file" -- and it fails at generation time, by which point
# the file is already saved and every later turn in that conversation fails
# with it. So anything outside this set is re-encoded on the way in, and a
# conversation can never be poisoned by a file the model cannot read.
STORABLE_MIMES = {"image/png", "image/jpeg", "image/gif"}

# Browsers guess `type` from the extension and give up on anything unusual, so
# a file arriving as "" or application/octet-stream is classified by suffix.
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".kt",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".sh",
    ".sql", ".html", ".css", ".scss", ".xml", ".diff", ".patch",
}


class AttachmentError(ValueError):
    """Refused. The message is written to be shown to the user verbatim."""


@dataclass(frozen=True)
class IncomingFile:
    kind: str  # image | text | document
    name: str
    mime: str
    data: bytes
    # Documents only: what was read out of them, kept so the extraction is
    # done once rather than on every turn that carries the file.
    text: str | None = None


def classify(name: str, mime: str) -> str:
    normalised = (mime or "").split(";")[0].strip().lower()
    suffix = Path(name).suffix.lower()

    if normalised in IMAGE_MIMES:
        return "image"
    if normalised.startswith("image/"):
        raise AttachmentError(
            f"{name}: {mime} isn't an image format any model reads. "
            "Convert it to PNG, JPEG, WebP, or GIF."
        )
    # Checked before the text branch: .docx would otherwise never be reached
    # from a browser that guesses a generic type for it.
    if document_suffix(name, mime):
        return "document"
    if normalised.startswith("text/") or suffix in TEXT_SUFFIXES:
        return "text"
    raise AttachmentError(
        f"{name}: that kind of file can't be attached (this one is "
        f"{mime or 'of unknown type'}). Images, text and code files, PDFs, "
        "and Word, Excel, PowerPoint or OpenDocument files all work."
    )


def decode(items: list) -> list[IncomingFile]:
    """Validate and decode what the client posted.

    Raises AttachmentError on the first thing wrong; the caller turns that into
    a 400 whose body is shown in the composer.
    """
    if len(items) > MAX_FILES:
        raise AttachmentError(f"Too many files at once -- the limit is {MAX_FILES}.")

    files: list[IncomingFile] = []
    total = 0
    for item in items:
        name = (item.name or "file").strip()
        try:
            data = base64.b64decode(item.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentError(f"{name}: could not be read.") from exc

        if not data:
            raise AttachmentError(f"{name} is empty.")
        if len(data) > MAX_BYTES_EACH:
            raise AttachmentError(
                f"{name} is {_mb(len(data))} -- the limit is {_mb(MAX_BYTES_EACH)} per file."
            )
        total += len(data)
        if total > MAX_BYTES_TOTAL:
            raise AttachmentError(
                f"Those files come to more than {_mb(MAX_BYTES_TOTAL)} together."
            )

        kind = classify(name, item.mime)
        mime = item.mime
        text = None
        if kind == "image":
            name, mime, data = normalize_image(name, mime, data)
        elif kind == "document":
            try:
                text = extract(name, document_suffix(name, mime), data)
            except ExtractionError as exc:
                # Re-raised as a refusal so it lands in the composer with the
                # rest of them, while the person is still holding the file.
                raise AttachmentError(str(exc)) from exc

        files.append(IncomingFile(kind=kind, name=name, mime=mime, data=data, text=text))

    return files


def normalize_image(name: str, mime: str, data: bytes) -> tuple[str, str, bytes]:
    """An image as it should be stored: a format the runner reads, and opaque.

    Two conversions, both for the same reason -- what the model receives should
    be what the person saw when they attached it:

      format -- WebP and friends are re-encoded to PNG, since the local runner
                refuses them and only says so once generation starts.
      alpha  -- transparency is flattened onto white. Left alone, whatever the
                runner composites against shows through, and a screenshot with
                a transparent background arrives as a black rectangle.

    Anything Pillow cannot open is passed through untouched: `decode` has
    already vouched for the type, and a file this cannot parse is better
    refused by the model with a real error than silently dropped here.
    """
    from PIL import Image  # local: only an image path pays the import

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return name, mime, data

    transparent = image.mode in ("RGBA", "LA", "P") and (
        image.mode != "P" or "transparency" in image.info
    )
    if mime in STORABLE_MIMES and not transparent:
        return name, mime, data

    flattened = Image.new("RGB", image.size, (255, 255, 255))
    converted = image.convert("RGBA")
    flattened.paste(converted, mask=converted.split()[-1])

    buffer = io.BytesIO()
    flattened.save(buffer, format="PNG", optimize=True)
    return f"{Path(name).stem}.png", "image/png", buffer.getvalue()


def as_prompt_text(name: str, body: str) -> str:
    """A file's words as they appear in the prompt.

    Fenced and named, so the model can tell the file apart from the sentence
    the user wrote about it.
    """
    if len(body) > MAX_TEXT_CHARS:
        body = body[:MAX_TEXT_CHARS] + f"\n\n[truncated -- {name} is longer than this]"
    return f"--- attached file: {name} ---\n{body}\n--- end of {name} ---"


def _mb(count: int) -> str:
    return f"{count / (1024 * 1024):.1f} MB"
