"""Getting the words out of a document.

A model cannot read a .docx any more than it can read a .png -- both are
containers. Images have a second path (a vision model looks at them); documents
do not, so the only useful thing to do with one is turn it into text before it
reaches the prompt.

PDF needs a real parser and gets one. The Office and OpenDocument formats do
not: every one of them is a ZIP of XML, and pulling the text runs out at about
forty lines of standard library. That is three dependencies not taken, for
formats whose layout the model was never going to see anyway.

Extraction happens once, when the file is uploaded, so a document that yields
nothing is refused while the person is still looking at the composer -- not
silently, two turns later, when the answer stops making sense.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path

# Everything here becomes text. Anything else attachable is an image or is
# already text; see attachments.py.
DOCUMENT_SUFFIXES = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".odt",
    ".odp",
    ".ods",
}

DOCUMENT_MIMES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
}


class ExtractionError(ValueError):
    """Nothing readable came out. The message is shown to the user verbatim."""


def document_suffix(name: str, mime: str) -> str | None:
    """The document type of a file, or None if it isn't one."""
    by_mime = DOCUMENT_MIMES.get((mime or "").split(";")[0].strip().lower())
    if by_mime:
        return by_mime
    suffix = Path(name).suffix.lower()
    return suffix if suffix in DOCUMENT_SUFFIXES else None


def extract(name: str, suffix: str, data: bytes) -> str:
    if suffix == ".pdf":
        text = _from_pdf(name, data)
    elif suffix == ".docx":
        text = _from_zip_xml(name, data, ["word/document.xml"], "}t", block="}p")
    elif suffix == ".pptx":
        text = _from_zip_xml(name, data, _slides, "}t", block="}p")
    elif suffix == ".xlsx":
        text = _from_xlsx(name, data)
    else:  # OpenDocument: one flat content.xml whatever the flavour
        text = _from_zip_xml(name, data, ["content.xml"], "}p", block="}p")

    text = _tidy(text)
    if not text:
        raise ExtractionError(
            f"{name}: no text could be read from this file. If it is a scan or "
            "a set of photographs, attach the pages as images instead."
        )
    return text


# -- PDF ---------------------------------------------------------------------


def _from_pdf(name: str, data: bytes) -> str:
    from pypdf import PdfReader  # local: only a PDF pays the import
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            # An empty user password is common and decrypts silently; a real
            # one cannot be guessed and the file is unusable.
            try:
                if not reader.decrypt(""):
                    raise ExtractionError(f"{name} is password-protected.")
            except (NotImplementedError, PdfReadError) as exc:
                raise ExtractionError(f"{name} is password-protected.") from exc

        # Pages are labelled because a page number is the only landmark a
        # reader and the model can both refer to.
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            body = (page.extract_text() or "").strip()
            if body:
                pages.append(f"[page {number}]\n{body}")
        return "\n\n".join(pages)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"{name} could not be read as a PDF.") from exc


# -- ZIP-of-XML formats ------------------------------------------------------


def _slides(archive: zipfile.ZipFile) -> list[str]:
    """Slide parts in the order a person would read them, not ZIP order."""

    def index(path: str) -> int:
        found = re.search(r"(\d+)", Path(path).stem)
        return int(found.group(1)) if found else 0

    return sorted(
        (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
        key=index,
    )


def _from_zip_xml(name: str, data: bytes, parts, leaf: str, *, block: str) -> str:
    """Text of every `leaf` element, with a line break at each `block`.

    Tags are matched on their local name -- the namespace prefixes differ
    between formats and versions, and none of them are worth resolving to read
    a paragraph.
    """
    chunks: list[str] = []
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = parts(archive) if callable(parts) else parts
            for part in names:
                try:
                    xml = archive.read(part)
                except KeyError:
                    continue
                root = ET.fromstring(xml)
                for element in root.iter():
                    if element.tag.endswith(block):
                        chunks.append("\n")
                    if element.tag.endswith(leaf) and element.text:
                        chunks.append(element.text)
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise ExtractionError(f"{name} could not be read -- the file looks damaged.") from exc
    return "".join(chunks)


def _from_xlsx(name: str, data: bytes) -> str:
    """Sheets as tab-separated rows, which is what a model reads best."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in root:
                    shared.append(
                        "".join(t.text or "" for t in item.iter() if t.tag.endswith("}t"))
                    )

            sheets = sorted(
                n for n in archive.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
            )
            out: list[str] = []
            for number, part in enumerate(sheets, start=1):
                root = ET.fromstring(archive.read(part))
                rows: list[str] = []
                for row in root.iter():
                    if not row.tag.endswith("}row"):
                        continue
                    cells = []
                    for cell in row:
                        value = _cell_text(cell, shared)
                        cells.append(value)
                    if any(cells):
                        rows.append("\t".join(cells))
                if rows:
                    out.append(f"[sheet {number}]\n" + "\n".join(rows))
            return "\n\n".join(out)
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise ExtractionError(f"{name} could not be read -- the file looks damaged.") from exc


def _cell_text(cell, shared: list[str]) -> str:
    # t="s" means the value is an index into the shared string table; anything
    # else is the literal in <v>, or inline text in <is>.
    kind = cell.get("t")
    value = ""
    for child in cell:
        if child.tag.endswith("}v"):
            value = child.text or ""
        elif child.tag.endswith("}is"):
            value = "".join(t.text or "" for t in child.iter() if t.tag.endswith("}t"))
    if kind == "s" and value.isdigit():
        index = int(value)
        return shared[index] if index < len(shared) else ""
    return value


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
