"""Word tokens with coordinates (PyMuPDF), grouped into columns and visual rows."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

ROW_TOLERANCE = 2.6  # points between vertical centres of tokens on one printed row


@dataclass(frozen=True)
class Token:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    page: int  # 1-based

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class Row:
    page: int
    column: int
    tokens: tuple[Token, ...]

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.tokens)

    @property
    def y(self) -> float:
        return min(t.y0 for t in self.tokens)

    @property
    def max_size(self) -> float:
        return max(t.size for t in self.tokens)


@dataclass(frozen=True)
class Document:
    pages: tuple[tuple[Token, ...], ...]
    page_widths: tuple[float, ...]
    images_only_pages: tuple[int, ...]
    created_at: datetime | None
    sha256: str


def _pdf_date(value: str | None) -> datetime | None:
    """PDF 'D:YYYYMMDDHHmmSS+HH'mm'' -> aware datetime; missing offset stays unknown."""
    if not value or not value.startswith("D:") or len(value) < 16:
        return None
    raw = value[2:]
    try:
        base = datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    rest = raw[14:].replace("'", "")
    if rest.startswith("Z"):
        return base.replace(tzinfo=UTC)
    if len(rest) >= 5 and rest[0] in "+-" and rest[1:5].isdigit():
        sign = 1 if rest[0] == "+" else -1
        offset = timedelta(hours=int(rest[1:3]), minutes=int(rest[3:5]))
        return base.replace(tzinfo=timezone(sign * offset))
    return None


def load(source: Path | bytes) -> Document:
    import pymupdf  # optional dependency, needed only for PDF import

    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages, widths, image_pages = [], [], []
    for number, page in enumerate(doc, start=1):
        tokens = []
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", ()):
                for span in line["spans"]:
                    tokens.extend(_words(span, number))
        pages.append(tuple(tokens))
        widths.append(page.rect.width)
        if not tokens and page.get_images():
            image_pages.append(number)
    created = _pdf_date((doc.metadata or {}).get("creationDate"))
    doc.close()
    return Document(
        tuple(pages), tuple(widths), tuple(image_pages), created, hashlib.sha256(data).hexdigest()
    )


def _words(span: dict, page: int) -> list[Token]:
    words, current = [], []
    bold = bool(span["flags"] & 16) or "bold" in span["font"].lower()
    for char in span["chars"] + [{"c": " ", "bbox": (0, 0, 0, 0)}]:
        if char["c"].isspace():
            if current:
                text = "".join(c["c"] for c in current)
                x0 = min(c["bbox"][0] for c in current)
                x1 = max(c["bbox"][2] for c in current)
                y0, y1 = span["bbox"][1], span["bbox"][3]
                words.append(Token(text, x0, y0, x1, y1, span["size"], bold, page))
            current = []
        else:
            current.append(char)
    return words


def rows(tokens: tuple[Token, ...], column_edges: tuple[float, ...]) -> list[Row]:
    """Group tokens into rows per column. ``column_edges`` are x boundaries between columns."""
    by_column: dict[int, list[Token]] = {}
    for token in tokens:
        column = sum(token.xc > edge for edge in column_edges)
        by_column.setdefault(column, []).append(token)
    result = []
    for column, items in sorted(by_column.items()):
        items.sort(key=lambda t: (t.yc, t.x0))
        current: list[Token] = []
        for token in items:
            if (
                current
                and abs(token.yc - sum(t.yc for t in current) / len(current)) > ROW_TOLERANCE
            ):
                result.append(Row(token.page, column, tuple(sorted(current, key=lambda t: t.x0))))
                current = []
            current.append(token)
        if current:
            result.append(Row(current[0].page, column, tuple(sorted(current, key=lambda t: t.x0))))
    return result


def split_phrase(tokens: tuple[Token, ...], max_gap: float = 6.0) -> tuple[tuple, tuple]:
    """Leading phrase (tokens separated by normal word spacing) and the remaining tokens."""
    for i in range(1, len(tokens)):
        if tokens[i].x0 - tokens[i - 1].x1 > max_gap:
            return tokens[:i], tokens[i:]
    return tokens, ()
