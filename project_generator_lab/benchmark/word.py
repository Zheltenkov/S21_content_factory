"""A README kept as a Word document, read as the Markdown the repository reader reads.

The runs of an intensive keep the README of a day as «README.md.docx»: the text the learner sees, in a document with
heading styles. Here it is turned into Markdown — a heading by the style of its paragraph, an item of a list by its
numbering, a table by its rows, bold and monospaced runs by their marks — and nothing is added to it.

A link to the outside is written as Markdown writes it, «[слова](адрес)»: whether a project has a link to its
feedback form is something it is judged by. A link inside the document (its table of contents) keeps its words only.

What is not carried over: italics and colours, footnotes, comments,
text boxes, ruled lines, formulas, a sign set by the code of a symbol font; a picture is left as «![](рисунок)», so
that pictures are counted; text removed in tracked changes is not read, text inserted is. A merged cell of a table
is one cell of its row; a table inside a cell is read as lines of that cell.

A block of code has no mark of its own in such a document: a sample of output is a paragraph like any other. So a
line that Markdown would read as structure — «# …», a fence, «| …» — is written with «\\» before it: it stays the
line of text it was in the document instead of becoming a heading. A paragraph that is one bold phrase is written
as one, and the reader takes it for the title of a block of a task, as in a README typed in Markdown.

What is taken on trust: a monospaced run is known by the font the run itself names (Roboto Mono, Courier, Consolas),
not by a font its style gives it; a heading by the built-in styles «Heading 1…9» and «Title» — a paragraph merely
typed large and bold is text; an item of a list by the numbering of the paragraph or of its own style, not of a
style that one is based on; a numbered list counts from one. A heading is read by the reader as a heading of
Markdown is: «_» and «*» at its ends and «<…>» inside it are taken for emphasis and tags, and dropped.
"""

import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml.ns import qn

# A README with its pictures; larger than this is not a text somebody wrote for a learner. The text itself is
# the XML inside: a part of ten megabytes unpacks into hundreds in memory, and no README comes near it (the largest
# part among the documents this was written for is a third of a megabyte). The limit is on every part, whatever
# its name: which part is the text is said inside the archive, not by the ending of a name.
MAX_WORD = 30_000_000
MAX_UNPACKED = 60_000_000
MAX_PART = 10_000_000
# Whatever ends a line for Python's splitlines ends it here: the reader of a repository cuts the text by them all.
LINE = re.compile("\r\n?|[\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")
HEADING = re.compile(r"^heading ([1-9])$", re.I)
MONO = re.compile(r"mono|courier|consolas|menlo", re.I)
# The start of a line the reader of a repository would take for a heading, a fence or a row of a table.
STRUCTURE = re.compile(r"#{1,6}(?:[ \t]|$)|`{3}|~{3}|\|")
OFF = ("0", "false", "off")
PICTURE = "![](рисунок)"
P, R, T, TBL, TR, TC, SDT, INSIDE = (qn(name) for name in ("w:p", "w:r", "w:t", "w:tbl", "w:tr", "w:tc", "w:sdt", "w:sdtContent"))
ALTERNATE = "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"
LINK = qn("w:hyperlink")
# «http://materials/rules.md» is what Word makes of the name of a file typed into a document: a link to nowhere, not
# an address. An address has a host with a dot in it.
ADDRESS = re.compile(r"^(?:https?://[^\s/?#.]+(?:\.[^\s/?#.]+)+(?:[/?#:]\S*)?|mailto:\S+)$", re.I)
# What Word wraps blocks, rows and cells in without making them anything else.
WRAPPERS = (SDT, INSIDE, qn("w:customXml"), qn("w:smartTag"))
# What a run holds besides letters. A break of a page or a column is not a break of a line.
TYPED = {qn("w:tab"): "\t", qn("w:cr"): "\n", qn("w:noBreakHyphen"): "-"}
# Where a picture may be; it is one when an image is inside — a ruled line across the page is drawn the same way.
DRAWN = (qn("w:drawing"), qn("w:pict"), ALTERNATE)
IMAGE = ("{http://schemas.openxmlformats.org/drawingml/2006/main}blip", "{urn:schemas-microsoft-com:vml}imagedata")
REMOVED = (qn("w:del"), qn("w:moveFrom"), qn("w:pPr"))


class Piece(NamedTuple):
    text: str
    bold: bool
    mono: bool


def on(properties: Any, name: str) -> bool:
    mark = properties.find(qn(name)) if properties is not None else None
    return mark is not None and mark.get(qn("w:val")) not in OFF


def pieces(element: Any, links: dict[str, str] | None = None) -> Iterator[Piece]:
    """The runs of a paragraph in reading order — those inside a link or an insertion too, not those removed.
    `links` — the addresses of the document's links to the outside, by their ids; without it a link is its words."""
    for child in element:
        if child.tag == R:
            properties = child.find(qn("w:rPr"))
            fonts = properties.find(qn("w:rFonts")) if properties is not None else None
            mono = fonts is not None and any(MONO.search(fonts.get(qn(name)) or "") for name in ("w:ascii", "w:hAnsi", "w:cs"))
            text = ""
            for part in child:
                if part.tag == T:
                    text += part.text or ""
                elif part.tag == qn("w:br"):
                    text += "" if part.get(qn("w:type")) in ("page", "column") else "\n"
                elif part.tag in DRAWN:
                    text += PICTURE if any(True for _ in part.iter(*IMAGE)) else ""
                else:
                    text += TYPED.get(part.tag, "")
            yield Piece(LINE.sub("\n", text), on(properties, "w:b"), mono)
        elif child.tag == ALTERNATE:
            # The same runs twice, for a new Word and for an old one: the first of the two that says anything is read.
            for branch in child:
                found = list(pieces([branch], links)) if isinstance(branch.tag, str) else []
                if any(piece.text for piece in found):
                    yield from found
                    break
        elif child.tag == LINK and (address := (links or {}).get(child.get(qn("r:id")) or "")):
            inside = [piece for piece in pieces(child, links) if piece.text]
            if inside:
                yield Piece("[", False, False)
                yield from inside
                yield Piece(f"]({address})", False, False)
        elif child.tag not in REMOVED and isinstance(child.tag, str):
            yield from pieces(child, links)


def wrapped(text: str, mark: str, close: str) -> str:
    """The words inside the marks, the spaces around them outside: «**слово** » is bold, «**слово **» is not."""
    core = text.strip()
    start = len(text) - len(text.lstrip())
    return f"{text[:start]}{mark}{core}{close}{text[start + len(core):]}" if core else text


def marked(found: Iterator[Piece]) -> str:
    """The runs as Markdown. Neighbours of one kind are one run: Word cuts a phrase into runs where it pleases."""
    merged: list[Piece] = []
    for piece in found:
        if merged and piece.text and (merged[-1].bold, merged[-1].mono) == (piece.bold, piece.mono):
            merged[-1] = merged[-1]._replace(text=merged[-1].text + piece.text)
        elif piece.text:
            merged.append(piece)
    out = ""
    for piece in merged:
        lines = piece.text.split("\n")
        if piece.mono:
            lines = [wrapped(line, "`` ", " ``") if "`" in line else wrapped(line, "`", "`") for line in lines]
        if piece.bold:
            lines = [wrapped(line, "**", "**") for line in lines]
        out += "\n".join(lines)
    return out


def kept(line: str) -> str:
    """A line of text that Markdown would read as structure, kept a line of text."""
    start = len(line) - len(line.lstrip())
    return f"{line[:start]}\\{line[start:]}" if STRUCTURE.match(line, start) else line


class Lists:
    """The marks of list items: «-» for a bulleted one, the next number for a numbered one."""

    def __init__(self, numbering: Any, styled: dict[str, Any] | None = None) -> None:
        self.formats: dict[tuple[str, str], str] = {}
        self.counts: dict[tuple[str, int], int] = {}
        self.styled = styled or {}  # the numbering a style of paragraphs carries: «List Bullet» is a list by its style
        if numbering is None:
            return
        value = qn("w:val")
        abstract = {a.get(qn("w:abstractNumId")): {level.get(qn("w:ilvl")): fmt.get(value) for level in a.findall(qn("w:lvl"))
                                                    if (fmt := level.find(qn("w:numFmt"))) is not None}
                    for a in numbering.findall(qn("w:abstractNum"))}
        for num in numbering.findall(qn("w:num")):
            link = num.find(qn("w:abstractNumId"))
            for level, fmt in abstract.get(link.get(value) if link is not None else None, {}).items():
                self.formats[num.get(qn("w:numId")), level] = fmt

    def mark(self, paragraph: Any) -> tuple[int, str] | None:
        """The level and the mark of the item; None — the paragraph is not an item of a list."""
        numbered = paragraph.find(f"{qn('w:pPr')}/{qn('w:numPr')}")
        if numbered is None and (style := paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")) is not None:
            numbered = self.styled.get(style.get(qn("w:val")))
        ident = numbered.find(qn("w:numId")) if numbered is not None else None
        if ident is None or ident.get(qn("w:val")) in (None, "0"):
            return None
        number, step = ident.get(qn("w:val")), numbered.find(qn("w:ilvl"))
        raw = step.get(qn("w:val")) if step is not None else "0"
        level = min(int(raw), 8) if raw and raw.isascii() and raw.isdigit() else 0
        # A list whose numbering is not described is still a list: its items are bulleted.
        if self.formats.get((number, str(level)), "bullet") in ("bullet", "none"):
            return level, "- "
        for key in [key for key in self.counts if key[0] == number and key[1] > level]:
            del self.counts[key]  # a list inside an item starts over under the next item
        self.counts[number, level] = self.counts.get((number, level), 0) + 1
        return level, f"{self.counts[number, level]}. "


def paragraph(element: Any, level: int, lists: Lists, links: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """What the paragraph is — heading, item or text — and its lines; no lines for an empty one."""
    if level:
        words = " ".join("".join(piece.text for piece in pieces(element)).split())  # a heading that is a link is its words
        return "heading", [f"{'#' * min(level, 6)} {words}"] if words else []
    lines = [line.rstrip() for line in marked(pieces(element, links)).split("\n")]
    if not any(lines):
        return "text", []
    item = lists.mark(element)
    if item is None:
        return "text", [kept(line) for line in lines]
    depth, mark = item
    indent = "    " * depth
    return "item", [f"{indent}{mark}{lines[0].lstrip()}", *(kept(f"{indent}{' ' * len(mark)}{line.lstrip()}") for line in lines[1:])]


def within(parent: Any, tags: tuple[str, ...]) -> Iterator[Any]:
    """The children of the parent with one of the tags, those inside a wrapper Word put around them too."""
    for child in parent:
        if child.tag in tags:
            yield child
        elif child.tag in WRAPPERS:
            yield from within(child, tags)


def said_in(cell: Any) -> Iterator[Any]:
    """The paragraphs of a cell, those of a table inside it too — not those of a text box drawn in it."""
    for child in within(cell, (P, TBL)):
        if child.tag == P:
            yield child
        else:
            for row in within(child, (TR,)):
                for inner in within(row, (TC,)):
                    yield from said_in(inner)


def table(element: Any, links: dict[str, str] | None = None) -> list[str]:
    """The table as rows of Markdown; the first row is its header. A cell of several paragraphs is one line."""
    rows = []
    for row in within(element, (TR,)):
        cells = []
        for cell in within(row, (TC,)):
            said = [" ".join(line.split()) for part in said_in(cell) for line in marked(pieces(part, links)).split("\n")]
            cells.append("<br>".join(line for line in said if line).replace("|", "\\|"))
        rows.append(cells)
    width = max((len(row) for row in rows), default=0)
    if not width:
        return []
    lines = ["| " + " | ".join(row + [""] * (width - len(row))) + " |" for row in rows]
    return [lines[0], "|" + "---|" * width, *lines[1:]]


def blocks(parent: Any, levels: dict[str, int], lists: Lists, links: dict[str, str] | None = None) -> Iterator[tuple[str, list[str]]]:
    for child in within(parent, (P, TBL)):
        if child.tag == P:
            style = child.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
            kind, lines = paragraph(child, levels.get(style.get(qn("w:val")), 0) if style is not None else 0, lists, links)
            if lines:
                yield kind, lines
        elif lines := table(child, links):
            yield "table", lines


def markdown(path: Path) -> str:
    """The document as Markdown. Not a Word document, or one too large to be a README — ValueError."""
    if path.stat().st_size > MAX_WORD:
        raise ValueError(f"{path.name} больше {MAX_WORD // 1_000_000} МБ")
    # A file under the name of a document may hold anything, and the libraries that open it say so in a dozen ways
    # of their own: here they are one — «not a Word document», by the name of the file.
    try:
        with zipfile.ZipFile(path) as packed:
            sizes = [info.file_size for info in packed.infolist()]
    except MemoryError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"{path.name} не читается как документ Word ({type(error).__name__})") from error
    # The sizes are those the archive declares, and it is held to them: a part longer than it says does not unpack.
    if sum(sizes) > MAX_UNPACKED or max(sizes, default=0) > MAX_PART:
        raise ValueError(f"{path.name} после распаковки больше {(MAX_UNPACKED if sum(sizes) > MAX_UNPACKED else MAX_PART) // 1_000_000} МБ")
    try:
        document = Document(str(path))
        levels, styled = {}, {}
        for style in document.styles:
            found = HEADING.match(style.name or "")
            if found or (style.name or "").lower() == "title":
                levels[style.style_id] = int(found[1]) if found else 1
            if (numbered := style.element.find(f"{qn('w:pPr')}/{qn('w:numPr')}")) is not None:
                styled[style.style_id] = numbered
        numbering = next((rel.target_part.element for rel in document.part.rels.values()
                          if rel.reltype == RELATIONSHIP_TYPE.NUMBERING and not rel.is_external), None)
        # An address is carried only when it is one — «http…» or «mailto:» in one word: it is written into a line of Markdown.
        links = {ident: rel.target_ref.strip().replace(")", "%29") for ident, rel in document.part.rels.items()
                 if rel.reltype == RELATIONSHIP_TYPE.HYPERLINK and rel.is_external and ADDRESS.match(rel.target_ref.strip())}
        out: list[str] = []
        before = ""
        for kind, lines in blocks(document.element.body, levels, Lists(numbering, styled), links):
            # The items of a list stand together; everything else is a paragraph of its own.
            out += [""] if out and not (kind == "item" and before == "item") else []
            out += lines
            before = kind
    except MemoryError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"{path.name} не читается как документ Word ({type(error).__name__})") from error
    return "\n".join(out) + "\n" if out else ""
