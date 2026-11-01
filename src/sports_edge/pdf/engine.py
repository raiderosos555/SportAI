"""Entry point: STS printed offers only; other documents are refused (TASK 003, STS ONLY)."""

from sports_edge.offers.model import ParseResult
from sports_edge.pdf import layout, sts


class ParseError(ValueError):
    """The document cannot be read reliably; nothing is imported."""


def parse_pdf(data: bytes, *, file_name: str | None = None) -> tuple[ParseResult, layout.Document]:
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
    if not sts.looks_like_sts(document):
        raise ParseError(
            "To nie jest oferta drukowana STS (OFERTA KURSOWA). Aplikacja obsługuje wyłącznie "
            "STS — innych operatorów nie importujemy."
        )
    return sts.parse(document, file_name=file_name), document
