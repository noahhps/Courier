"""Making a document out of text.

The mirror of extract.py, and it takes the same position on dependencies: a
.docx is a ZIP of XML, so writing one is standard library. A PDF is a slightly
awkward but entirely documented byte format, and a text-only one is a few
hundred lines rather than a rendering engine -- so that is written here too,
which keeps a document generator out of a project whose whole point is running
on hardware you control.

Neither of these lays out a page in any interesting way. Headings, paragraphs
and bullets, wrapped to a measured column. Anything that needs a float or a
table wants a real toolchain, and this deliberately is not one.

Input is the plain-text-with-hashes that models produce naturally:

    # A heading
    Some prose, wrapped as needed.
    - a bullet
"""

from __future__ import annotations

import io
import re
import textwrap
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

# A4 in points, which is the only unit PDF has.
PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN = 64

BODY_SIZE = 11
HEADING_SIZE = 16
TITLE_SIZE = 20
LEADING = 1.45  # line height as a multiple of the font size


# -- house style -------------------------------------------------------------
#
# One palette across all four formats, so a deck and the spreadsheet beside it
# look like they came from the same place. Chosen to survive being printed in
# grey and to stay legible projected, which rules out anything pale.
#
# The model does not choose any of this. Models are poor at visual design and
# good at structure, so the skill takes content and applies the styling itself
# -- the only knob exposed is `theme`, which swaps the accent.

THEMES = {
    "slate": ("1F3A5F", "E8EDF3", "0F1B2A"),   # accent, wash, ink
    "ink": ("14171D", "ECEFF3", "14171D"),
    "green": ("1E5B45", "E4EFE9", "10261E"),
    "plum": ("4A2D5B", "EEE7F2", "241429"),
}
DEFAULT_THEME = "slate"


def theme_colours(theme: str) -> tuple[str, str, str]:
    """Accent, wash and ink for a named theme, as RRGGBB without a hash."""
    return THEMES.get((theme or "").strip().lower(), THEMES[DEFAULT_THEME])


def _rgb(hex6: str) -> tuple[float, float, float]:
    """RRGGBB to the 0-1 triple PDF's colour operators take."""
    return tuple(int(hex6[i : i + 2], 16) / 255 for i in (0, 2, 4))


# Characters models reach for that Helvetica's WinAnsi encoding has no slot
# for. Left alone each becomes '?', which is how "decision-making" arrives as
# "decision?making". Mapped to the nearest thing the font can actually draw.
_SUBSTITUTES = {
    "‐": "-", "‑": "-", "‒": "-", "−": "-",
    "⁄": "/", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ",
    "‘": "'", "’": "'", "‚": ",",
    "“": '"', "”": '"',
    "⁃": "-",  # not • or · -- both exist in cp1252 and render fine
    "→": "->", "←": "<-", "≥": ">=", "≤": "<=",
    "×": "x", "✓": "v", "✗": "x",
}

# Inline markup, removed rather than rendered. Bold inside a paragraph would
# need a per-run font, which this layout has no concept of -- and a document
# reading "**Automation**: frees workers" is worse than one that simply reads
# "Automation: frees workers".
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_NUMBERED = re.compile(r"^(\d{1,3})[.)]\s+(.*)$")


def inline(text: str) -> str:
    """A line of markdown reduced to the words in it."""
    text = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    text = _BOLD.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _ITALIC.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _CODE.sub(lambda m: m.group(1), text)
    return text.strip()


@dataclass(frozen=True)
class Block:
    """One paragraph-ish thing, already classified."""

    kind: str  # title | heading | bullet | text
    text: str


def parse(body: str) -> list[Block]:
    """Text as a list of blocks.

    Deliberately tiny: `#` for a heading, `-` or `*` for a bullet, a blank line
    between paragraphs, everything else is prose. A model asked for a document
    writes exactly this without being told to.
    """
    blocks: list[Block] = []
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()

        # A rule is a visual device this layout does not have; dropping it
        # beats printing three hyphens on a line of their own.
        if set(stripped) <= {"-", "*", "_", " "} and len(stripped.strip()) >= 3:
            continue

        # A table would need column measurement. Passing the row through with
        # its pipes turned into spacing at least keeps the words.
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue  # the |---|---| separator row
            blocks.append(Block("text", inline("   ".join(cells))))
            continue

        if stripped.startswith("###"):
            blocks.append(Block("heading", inline(stripped.lstrip("#").strip())))
        elif stripped.startswith("##"):
            blocks.append(Block("heading", inline(stripped.lstrip("#").strip())))
        elif stripped.startswith("#"):
            blocks.append(Block("title", inline(stripped.lstrip("#").strip())))
        elif stripped.startswith("> "):
            blocks.append(Block("quote", inline(stripped[2:].strip())))
        elif stripped[:2] in ("- ", "* ", "+ "):
            blocks.append(Block("bullet", inline(stripped[2:].strip())))
        elif (numbered := _NUMBERED.match(stripped)) is not None:
            blocks.append(
                Block("number", f"{numbered.group(1)}.  {inline(numbered.group(2))}")
            )
        else:
            blocks.append(Block("text", inline(stripped)))
    return blocks


# -- docx --------------------------------------------------------------------
#
# The minimum a Word file can be and still open everywhere: a content-types
# part, a package relationship pointing at the document, and the document
# itself. Anything else Word adds on first save.

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_STYLE = {
    "title": ("Title", 40),
    "heading": ("Heading1", 30),
    "bullet": ("ListParagraph", 22),
    "number": ("ListParagraph", 22),
    "quote": ("Quote", 22),
    "text": ("Normal", 22),
}


def build_docx(title: str, body: str, theme: str = DEFAULT_THEME) -> bytes:
    """A .docx as bytes.

    Styles are named rather than defined: Word supplies its own Title and
    Heading1 when a paragraph claims them, so the file stays small and looks
    like a Word document rather than like something generated.
    """
    blocks = parse(body)
    if title and not any(b.kind == "title" for b in blocks):
        blocks.insert(0, Block("title", title))

    accent, _, _ = theme_colours(theme)

    paragraphs = []
    for block in blocks:
        style, size = _STYLE[block.kind]
        text = _xml_escape(block.text)
        bullet = "•  " if block.kind == "bullet" else ""
        # Headings take the accent; body text is left to Word's own colour so
        # the document still prints sensibly in black and white.
        colour = (
            f'<w:rPr><w:color w:val="{accent}"/></w:rPr>'
            if block.kind in ("title", "heading")
            else ""
        )
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/>'
            f'<w:spacing w:after="{size}"/></w:pPr>'
            f'<w:r>{colour}<w:t xml:space="preserve">{bullet}{text}</w:t></w:r></w:p>'
        )

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{"".join(paragraphs)}</w:body></w:document>'
    )

    buffer = io.BytesIO()
    # Deflated and with a fixed timestamp, so the same input produces the same
    # bytes -- which makes a generated document diffable.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in (
            ("[Content_Types].xml", _CONTENT_TYPES),
            ("_rels/.rels", _RELS),
            ("word/document.xml", document),
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


# -- pdf ---------------------------------------------------------------------


def build_pdf(title: str, body: str, theme: str = DEFAULT_THEME) -> bytes:
    """A PDF as bytes: Helvetica, wrapped, paginated.

    Written by hand because the alternative is a rendering dependency for what
    is, at this level of ambition, string formatting plus a byte-offset table.
    """
    blocks = parse(body)
    if title and not any(b.kind == "title" for b in blocks):
        blocks.insert(0, Block("title", title))

    pages = _paginate(blocks, theme)
    if not pages:
        pages = [[]]

    # Object 1 catalog, 2 pages, 3 and 4 fonts, then page/content pairs.
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # filled once the page ids are known
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]

    page_ids: list[int] = []
    for lines in pages:
        stream = _content_stream(lines)
        objects.append(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
            + stream + b"\nendstream"
        )
        content_id = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode("ascii")
        )
        page_ids.append(len(objects))

    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    return _assemble(objects, title)


def _paginate(blocks: list[Block], theme: str = DEFAULT_THEME) -> list[list[tuple]]:
    """Blocks into pages of positioned lines.

    Each line is (font, size, x, y, text). Wrapping is by character count
    against an average glyph width -- Helvetica's real metrics would be more
    exact, but the column is generous and this never overflows it.
    """
    sizes = {
        "title": TITLE_SIZE, "heading": HEADING_SIZE,
        "bullet": BODY_SIZE, "number": BODY_SIZE,
        "quote": BODY_SIZE, "text": BODY_SIZE,
    }
    fonts = {
        "title": "F2", "heading": "F2", "bullet": "F1",
        "number": "F1", "quote": "F1", "text": "F1",
    }
    usable = PAGE_WIDTH - 2 * MARGIN
    accent, _, ink = theme_colours(theme)
    colours = {
        "title": accent, "heading": accent,
        "bullet": ink, "number": ink, "quote": accent, "text": ink,
    }

    pages: list[list[tuple]] = []
    current: list[tuple] = []
    y = PAGE_HEIGHT - MARGIN

    for block in blocks:
        size = sizes[block.kind]
        font = fonts[block.kind]
        indent = MARGIN + (14 if block.kind in ("bullet", "number", "quote") else 0)
        # 0.5 em is a safe average advance for Helvetica across mixed case.
        per_line = max(12, int((usable - (indent - MARGIN)) / (size * 0.5)))
        wrapped = textwrap.wrap(block.text, per_line) or [""]

        # Space above a heading, so it groups with what follows it.
        if block.kind in ("title", "heading") and current:
            y -= size * 0.6

        for index, line in enumerate(wrapped):
            if y - size * LEADING < MARGIN:
                pages.append(current)
                current = []
                y = PAGE_HEIGHT - MARGIN
            text = ("•  " + line) if (block.kind == "bullet" and index == 0) else line
            x = indent if index == 0 or block.kind not in ("bullet", "number") else indent + 12
            current.append((font, size, x, y, text, colours[block.kind]))
            y -= size * LEADING

        # A rule under the title, the document's one piece of decoration and
        # the thing that makes a generated page look composed rather than
        # dumped. Drawn after the text so its y is already past the baseline.
        if block.kind == "title":
            y -= 4
            current.append(("rule", MARGIN, y, 120, 2.5, accent))
            y -= 10

        y -= size * 0.45  # gap after the block

    pages.append(current)
    return [p for p in pages if p] or [current]


def _content_stream(lines: list[tuple]) -> bytes:
    """One page's drawing instructions.

    Text and rules are interleaved rather than drawn in two passes, because a
    rule belongs immediately under the heading it follows -- and PDF has no
    z-order beyond the order operators appear in.
    """
    parts: list[bytes] = []
    in_text = False
    for item in lines:
        if item[0] == "rule":
            _, x, y, width, height, colour = item
            if in_text:
                parts.append(b"ET")
                in_text = False
            red, green, blue = _rgb(colour)
            parts.append(f"{red:.3f} {green:.3f} {blue:.3f} rg".encode("ascii"))
            parts.append(f"{x:.1f} {y:.1f} {width:.1f} {height:.1f} re f".encode("ascii"))
            continue

        font, size, x, y, text, colour = item
        if not in_text:
            parts.append(b"BT")
            in_text = True
        red, green, blue = _rgb(colour)
        parts.append(f"{red:.3f} {green:.3f} {blue:.3f} rg".encode("ascii"))
        parts.append(f"/{font} {size} Tf".encode("ascii"))
        parts.append(f"1 0 0 1 {x:.1f} {y:.1f} Tm".encode("ascii"))
        parts.append(b"(" + _pdf_text(text) + b") Tj")
    if in_text:
        parts.append(b"ET")
    return b"\n".join(parts)


def _pdf_text(text: str) -> bytes:
    """A string literal for a content stream.

    WinAnsi, because that is what the font declares. Anything outside it -- CJK,
    emoji -- becomes '?' rather than corrupting the stream: a text-only PDF with
    one Latin font was never going to render it, and failing visibly beats
    producing a file that opens to gibberish.
    """
    for bad, good in _SUBSTITUTES.items():
        if bad in text:
            text = text.replace(bad, good)
    encoded = text.encode("cp1252", "replace")
    out = bytearray()
    for byte in encoded:
        if byte in (0x28, 0x29, 0x5C):  # ( ) \
            out.append(0x5C)
        out.append(byte)
    return bytes(out)


def _assemble(objects: list[bytes], title: str) -> bytes:
    """Objects into a file, with the cross-reference table that makes it valid.

    The xref is the fiddly part: it is byte offsets into this very file, so it
    can only be built while writing it.
    """
    stamp = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
    objects = objects + [
        b"<< /Title (" + _pdf_text(title or "Document") + b") "
        b"/Producer (unified-llm) /CreationDate (" + stamp.encode("ascii") + b") >>"
    ]
    info_id = len(objects)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


# -- tables ------------------------------------------------------------------


def parse_table(text: str) -> list[list[str]]:
    """Rows out of whatever shape a model produced them in.

    Models reach for a markdown table when asked for one and for CSV when asked
    for data, and there is no reason to make the caller pick. Both land here,
    along with the tab-separated form that comes out of a spreadsheet paste.
    """
    rows: list[list[str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue  # the |---|---| rule under a markdown header
            rows.append([inline(c) for c in cells])
            continue

        if "\t" in line:
            rows.append([c.strip() for c in line.split("\t")])
            continue

        rows.append(_split_csv(line))
    return rows


def _split_csv(line: str) -> list[str]:
    """One CSV line, honouring quotes.

    csv.reader would do this, but it wants an iterable of lines and a dialect
    guess; the rule here is small enough to state outright, and stating it
    keeps the doubled-quote escape visible.
    """
    cells: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(line):
        char = line[index]
        if quoted:
            if char == '"':
                if index + 1 < len(line) and line[index + 1] == '"':
                    current.append('"')
                    index += 1
                else:
                    quoted = False
            else:
                current.append(char)
        elif char == '"':
            quoted = True
        elif char == ",":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _is_number(value: str) -> bool:
    """Whether a cell should reach Excel as a number rather than as text.

    Written out rather than a bare float() try, because a leading zero is
    almost always an identifier -- a postcode, an account number -- and turning
    "007" into 7 loses data the user typed on purpose.
    """
    candidate = value.strip().replace(",", "")
    if not candidate:
        return False
    if candidate.startswith("0") and candidate not in ("0", "0.0") and "." not in candidate:
        return False
    try:
        float(candidate)
    except ValueError:
        return False
    return True


def _column(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


# -- xlsx --------------------------------------------------------------------

_XLSX_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_XLSX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _xlsx_styles(accent: str, wash: str) -> str:
    """Three cell styles: default, header, and banded row.

    Fills 0 and 1 have to be `none` and `gray125` in that order -- Excel treats
    the first two entries as reserved and silently misreads every later index
    if they are missing, which shows up as the wrong colour on the wrong cells.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        f'<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        "</fonts>"
        '<fills count="4">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{accent}"/>'
        '<bgColor indexed="64"/></patternFill></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{wash}"/>'
        '<bgColor indexed="64"/></patternFill></fill>'
        "</fills>"
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" '
        'applyFont="1" applyFill="1" applyAlignment="1">'
        '<alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>'
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def build_xlsx(title: str, body: str, theme: str = DEFAULT_THEME) -> bytes:
    """A .xlsx as bytes, one sheet.

    Inline strings rather than a shared-string table: the table is a size
    optimisation for spreadsheets with heavy repetition, and skipping it
    removes a whole part and an index that has to stay in step with the cells.
    """
    rows = parse_table(body)
    if not rows:
        rows = [[""]]

    sheet_name = _xml_escape((title or "Sheet1").strip()[:31]) or "Sheet1"
    # Excel refuses these in a sheet name regardless of quoting.
    for bad in "[]:*?/\\":
        sheet_name = sheet_name.replace(bad, "-")

    accent, wash, _ = theme_colours(theme)

    # The first row is treated as headings whenever there is more than one row.
    # A single-row sheet is data, not a header with nothing under it.
    has_header = len(rows) > 1

    xml_rows = []
    for r, row in enumerate(rows, start=1):
        # 1 = header, 2 = banded, 0 = plain. Banding every other body row is
        # what makes a wide table readable across.
        style = 1 if (has_header and r == 1) else (2 if r % 2 == 1 else 0)
        cells = []
        for c, value in enumerate(row):
            ref = f"{_column(c)}{r}"
            attr = f' s="{style}"' if style else ""
            if _is_number(value) and not (has_header and r == 1):
                number = value.strip().replace(",", "")
                cells.append(f'<c r="{ref}"{attr}><v>{number}</v></c>')
            elif value:
                cells.append(
                    f'<c r="{ref}"{attr} t="inlineStr"><is><t xml:space="preserve">'
                    f"{_xml_escape(value)}</t></is></c>"
                )
            elif style:
                cells.append(f'<c r="{ref}"{attr}/>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')

    # Widths from the widest cell in each column. Excel's own auto-fit only
    # runs when a person double-clicks the divider, so a generated sheet opens
    # full of ### without this.
    widths = []
    for c in range(max((len(row) for row in rows), default=1)):
        longest = max((len(row[c]) for row in rows if c < len(row)), default=8)
        widths.append(
            f'<col min="{c + 1}" max="{c + 1}" '
            f'width="{min(max(longest + 4, 9), 60)}" customWidth="1"/>'
        )

    # A frozen header stays put when the sheet is scrolled, which is the single
    # most useful thing a spreadsheet can do for you and costs one element.
    panes = (
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        if has_header
        else ""
    )

    # Element order matters: the schema is a sequence, and Excel rejects a
    # worksheet whose children are shuffled.
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{panes}"
        f'<cols>{"".join(widths)}</cols>'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )

    return _zip(
        {
            "[Content_Types].xml": _XLSX_TYPES,
            "_rels/.rels": _XLSX_RELS,
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS,
            "xl/worksheets/sheet1.xml": sheet,
            "xl/styles.xml": _xlsx_styles(accent, wash),
        }
    )


def _zip(parts: dict[str, str]) -> bytes:
    """A ZIP with fixed timestamps, so identical input gives identical bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()


# -- pptx --------------------------------------------------------------------
#
# The most involved of the three, because a presentation cannot consist of
# slides alone: PowerPoint requires a master and at least one layout for the
# slides to inherit from, each pointing at the other through its own
# relationship part. That scaffolding is fixed, so it sits here as constants
# and only the slides themselves are generated.

# English Metric Units: 914400 to the inch. A 16:9 deck at 13.333 x 7.5in.
_SLIDE_W = 12192000
_SLIDE_H = 6858000
_SLIDE_MARGIN = 685800  # 0.75in

_PPTX_TYPES_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
    '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
)

_PPTX_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
    "</Relationships>"
)

_NS = (
    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
)

_EMPTY_TREE = (
    "<p:cSld><p:spTree>"
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    "<p:grpSpPr/></p:spTree></p:cSld>"
)

_CLR_MAP = (
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1"'
    ' accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5"'
    ' accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
)

_MASTER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f"<p:sldMaster{_NS}>{_EMPTY_TREE}{_CLR_MAP}"
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    "</p:sldMaster>"
)

_MASTER_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    "</Relationships>"
)

_LAYOUT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<p:sldLayout{_NS} type="blank" preserve="1">{_EMPTY_TREE}'
    "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
)

_LAYOUT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
    "</Relationships>"
)

_SLIDE_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    "</Relationships>"
)


def slides_from(body: str, title: str = "") -> list[tuple[str, list[str]]]:
    """Blocks grouped into slides.

    A heading of any level starts a new slide and becomes its title; everything
    beneath it is that slide's body. So the same markup that writes a document
    also writes a deck, and the model does not have to learn a second format to
    get one.
    """
    decks: list[tuple[str, list[str]]] = []
    current_title = title or ""
    current_body: list[str] = []
    # True while `current_title` is still the caller's argument rather than
    # something read out of the body. A deck whose text opens with its own
    # heading would otherwise get an empty duplicate slide in front of it.
    seeded = bool(title)

    for block in parse(body):
        if block.kind in ("title", "heading"):
            if seeded and not current_body:
                current_title = block.text
                seeded = False
                continue
            if current_title or current_body:
                decks.append((current_title, current_body))
            current_title, current_body = block.text, []
        else:
            # Each line carries its own marker: a bullet gets one, a numbered
            # item already has its number, and prose gets neither. Deciding it
            # here rather than in the renderer is what stops "1." arriving as
            # "-  1.".
            prefix = "•  " if block.kind == "bullet" else ""
            current_body.append(prefix + block.text)
        seeded = False

    if current_title or current_body:
        decks.append((current_title, current_body))
    return decks or [(title or "Untitled", [])]


def _textbox(
    shape_id: int,
    name: str,
    x: int,
    y: int,
    width: int,
    height: int,
    lines: list[str],
    size: int,
    bold: bool,
    colour: str = "14171D",
    spacing: int = 0,
) -> str:
    """One text frame. Sizes are in hundredths of a point, hence the *100.

    `spacing` is the gap before each paragraph, also in hundredths -- bullets
    need air between them or a slide reads as a wall.
    """
    weight = ' b="1"' if bold else ""
    before = f'<a:spcBef><a:spcPts val="{spacing}"/></a:spcBef>' if spacing else ""
    paragraphs = "".join(
        f"<a:p><a:pPr>{before}</a:pPr>"
        f'<a:r><a:rPr lang="en-US" sz="{size * 100}"{weight} dirty="0">'
        f'<a:solidFill><a:srgbClr val="{colour}"/></a:solidFill></a:rPr>'
        f"<a:t>{_xml_escape(line)}</a:t></a:r></a:p>"
        for line in (lines or [""])
    )
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{name}"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square"><a:normAutofit/></a:bodyPr>'
        f"<a:lstStyle/>{paragraphs}</p:txBody></p:sp>"
    )


def build_pptx(title: str, body: str, theme: str = DEFAULT_THEME) -> bytes:
    """A .pptx as bytes: one title-and-bullets slide per heading."""
    accent, wash, ink = theme_colours(theme)
    decks = slides_from(body, title)
    inner_width = _SLIDE_W - 2 * _SLIDE_MARGIN
    title_height = 1200000

    parts: dict[str, str] = {
        "_rels/.rels": _PPTX_ROOT_RELS,
        "ppt/slideMasters/slideMaster1.xml": _MASTER,
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _MASTER_RELS,
        "ppt/slideLayouts/slideLayout1.xml": _LAYOUT,
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _LAYOUT_RELS,
    }

    overrides: list[str] = []
    slide_ids: list[str] = []
    # rId1 on the presentation is the master, so slides start at rId2.
    presentation_rels = [
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/slideMaster" '
        'Target="slideMasters/slideMaster1.xml"/>'
    ]

    for number, (heading, lines) in enumerate(decks, start=1):
        # A short accent rule under the title, drawn as a filled rectangle.
        # It is the one piece of decoration here, and it is what stops a slide
        # reading as two blocks of text floating on nothing.
        rule = (
            '<p:sp><p:nvSpPr><p:cNvPr id="4" name="Rule"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{_SLIDE_MARGIN}" '
            f'y="{_SLIDE_MARGIN + title_height - 120000}"/>'
            f'<a:ext cx="1100000" cy="52000"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            f'<a:solidFill><a:srgbClr val="{accent}"/></a:solidFill>'
            "</p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>"
        )

        shapes = _textbox(
            2, "Title", _SLIDE_MARGIN, _SLIDE_MARGIN,
            inner_width, title_height,
            [heading] if heading else [], 32, True, accent,
        ) + (rule if heading else "")

        if lines:
            top = _SLIDE_MARGIN + title_height + 200000
            shapes += _textbox(
                3, "Body", _SLIDE_MARGIN, top,
                inner_width, _SLIDE_H - top - _SLIDE_MARGIN,
                lines, 18, False, ink, 900,
            )

        parts[f"ppt/slides/slide{number}.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<p:sld{_NS}><p:cSld>"
            f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="{wash}"/></a:solidFill>'
            "<a:effectLst/></p:bgPr></p:bg>"
            "<p:spTree>"
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            f"<p:grpSpPr/>{shapes}</p:spTree></p:cSld>"
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
        )
        parts[f"ppt/slides/_rels/slide{number}.xml.rels"] = _SLIDE_RELS
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{number}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'presentationml.slide+xml"/>'
        )
        slide_ids.append(f'<p:sldId id="{255 + number}" r:id="rId{number + 1}"/>')
        presentation_rels.append(
            f'<Relationship Id="rId{number + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/slide" '
            f'Target="slides/slide{number}.xml"/>'
        )

    parts["[Content_Types].xml"] = _PPTX_TYPES_HEAD + "".join(overrides) + "</Types>"
    parts["ppt/presentation.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<p:presentation{_NS}>"
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f'<p:sldIdLst>{"".join(slide_ids)}</p:sldIdLst>'
        f'<p:sldSz cx="{_SLIDE_W}" cy="{_SLIDE_H}"/>'
        f'<p:notesSz cx="{_SLIDE_H}" cy="{_SLIDE_W}"/>'
        "</p:presentation>"
    )
    parts["ppt/_rels/presentation.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(presentation_rels)}</Relationships>'
    )
    return _zip(parts)
