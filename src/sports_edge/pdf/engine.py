"""Entry point: choose the parser for a PDF and refuse documents without a text layer."""

from sports_edge.offers.model import ParseResult
from sports_edge.pdf import generic, layout, sts


class ParseError(ValueError):
    """The document cannot be read reliably; nothing is imported."""


def detect_bookmaker(document: layout.Document) -> str | None:
    text = " ".join(t.text for page in document.pages[:3] for t in page).upper()
    if sts.looks_like_sts(document):
        return "sts"
    if "SUPERBET" in text:
        return "superbet"
    return None


def parse_pdf(
    data: bytes, *, bookmaker: str | None = None, file_name: str | None = None
) -> tuple[ParseResult, layout.Document]:
    if not data.startswith(b"%PDF"):
        raise ParseError("Plik nie jest dokumentem PDF")
    try:
        document = layout.load(data)
    except Exception as error:  # damaged or encrypted file
        raise ParseError(f"Nie można otworzyć PDF: {type(error).__name__}") from error
    if not any(document.pages):
        raise ParseError(
            "PDF nie zawiera warstwy tekstowej (skan lub obrazy). Kursy nie zostały odczytane; "
            "OCR nie jest używany, aby nie zgadywać wartości."
        )
    detected = detect_bookmaker(document)
    chosen = bookmaker or detected
    if chosen == "sts":
        if detected != "sts":
            raise ParseError("Dokument nie ma układu oferty drukowanej STS (OFERTA KURSOWA)")
        return sts.parse(document, file_name=file_name), document
    return generic.parse(document, bookmaker=chosen or "unknown", file_name=file_name), document
