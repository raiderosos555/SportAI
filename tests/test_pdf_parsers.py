"""PDF import: STS layout (synthetic fixture with authentic geometry) and the generic parser."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from sports_edge.offers.model import OfferKind, RowStatus
from sports_edge.offers.text import parse_odds, split_line, split_participants

pymupdf = pytest.importorskip("pymupdf")

from sports_edge.pdf import layout, sts  # noqa: E402
from sports_edge.pdf.engine import ParseError, detect_bookmaker, parse_pdf  # noqa: E402


@pytest.fixture(scope="module")
def sts_result(sts_fixture_pdf):
    result, _document = parse_pdf(sts_fixture_pdf, file_name="fixture.pdf")
    return result


def rows_for(result, code):
    return {r.market_key: r for r in result.rows if r.offer_code == code}


def test_sts_import_reads_document_metadata(sts_result, sts_fixture_pdf):
    assert sts_result.bookmaker == "sts"
    assert sts_result.parser_verified is True
    assert sts_result.offer_kind == OfferKind.TODAY
    assert sts_result.offer_dates == (date(2026, 10, 31),)
    # Footer "2026-10-31 12:00" is Warsaw local time (CET, UTC+1 after the DST change).
    assert sts_result.document_generated_at == datetime(2026, 10, 31, 11, 0, tzinfo=UTC)
    assert sts_result.pages_total == 2  # long offer continues on a second page
    document = layout.load(sts_fixture_pdf)
    assert sts.looks_like_sts(document)
    assert detect_bookmaker(document) == "sts"


def test_sts_reads_events_with_polish_characters(sts_result):
    legia = rows_for(sts_result, "4201")["1x2"]
    assert legia.participants == ("Legia Warszawa", "Lech Poznań")
    assert legia.sport == "football" and legia.sport_raw == "PIŁKA NOŻNA"
    assert legia.league_raw == "POLSKA - EKSTRAKLASA" and legia.country == "POLSKA"
    assert legia.start_local.isoformat() == "2026-10-31T20:30:00+01:00"
    assert rows_for(sts_result, "4202")["1x2"].participants == ("Górnik Zabrze", "Śląsk Wrocław")
    assert sts_result.summary()["events"] >= 160


def test_sts_reads_odds_by_column_position(sts_result):
    real = rows_for(sts_result, "4101")
    prices = {s.key: s.odds for s in real["1x2"].selections}
    assert prices == {"home": Decimal("2.10"), "draw": Decimal("3.60"), "away": Decimal("3.25")}
    double = {s.key: s.odds for s in real["double_chance"].selections}
    # The "12" column is empty in the document: it stays missing, nothing is shifted or guessed.
    assert double == {"home_draw": Decimal("1.38"), "draw_away": Decimal("1.70")}
    total = rows_for(sts_result, "4103")["total"]
    assert total.line == "2.5" and {s.key: s.odds for s in total.selections} == {
        "under": Decimal("2.05"),
        "over": Decimal("1.75"),
    }
    basket = rows_for(sts_result, "4401")["total"]
    assert (basket.period, basket.includes_overtime, basket.line) == ("FT_OT", True, "215.5")


def test_sts_links_team_totals_and_completes_truncated_names(sts_result):
    team_total = rows_for(sts_result, "4104")["team_total_home"]
    assert team_total.participants == ("Real Madrid", "Barcelona")
    assert team_total.line == "1.5" and team_total.status == RowStatus.OK
    truncated = rows_for(sts_result, "4501")["1x2"]
    assert truncated.participants[1] == "Południowe Sokoły Uniwersyteckie"
    assert "name_completed_from_other_row" in truncated.issues


def test_sts_multi_column_and_page_continuity(sts_result):
    filler = [r for r in sts_result.rows if r.league_raw == "TESTLANDIA - LIGA WYPEŁNIACZY"]
    assert {r.column for r in filler} == {0, 1}
    assert {r.page for r in filler} == {1, 2}
    assert all(r.status == RowStatus.OK for r in filler)
    assert len({r.offer_code for r in filler}) == 160


def test_unknown_market_and_unrecognised_lines_go_to_review(sts_result):
    unknown = rows_for(sts_result, "4203")["unknown"]
    assert unknown.status == RowStatus.REVIEW and "unknown_market" in unknown.issues
    assert any("FIXTURE TESTOWY" in item.source_text for item in sts_result.review)
    assert sts_result.accepted and all(r.status == RowStatus.OK for r in sts_result.accepted)
    assert 0.9 < sts_result.quality < 1


def _sts_page(event_odds: list[tuple[str, float]]) -> bytes:
    """Minimal STS-geometry page with odds placed at explicit x centres."""
    font = pymupdf.Font("cjk")
    doc = pymupdf.open()
    page = doc.new_page(width=595.28, height=841.89)
    writer = pymupdf.TextWriter(page.rect)

    def put(x, y, text, size, center=False):
        width = font.text_length(text, fontsize=size)
        writer.append((x - width / 2 if center else x, y), text, font=font, fontsize=size)

    put(25.5, 26, "OFERTA KURSOWA", 12)
    put(160, 60, "SOBOTA 2026-10-31", 17, center=True)
    put(160, 85, "PIŁKA NOŻNA", 15, center=True)
    put(160, 105, "TESTLANDIA - LIGA", 13, center=True)
    put(53.3, 125, "Zwycięzca meczu", 9.5)
    for label, center in (("1", 247.95), ("2", 266.65)):
        put(center, 125, label, 8.5, center=True)
    put(25.5, 138, "777", 8.2)
    put(53.3, 138, "Alfa - Beta", 7.2)
    for text, center in event_odds:
        put(center, 138, text, 7.2, center=True)
    put(277.4, 138, "18:00", 7.2)
    writer.write_text(page)
    return doc.tobytes()


def test_odds_are_not_guessed_when_outside_columns():
    aligned, _ = parse_pdf(_sts_page([("1.50", 247.95), ("2.50", 266.65)]))
    assert aligned.rows[0].status == RowStatus.OK
    assert {s.key: str(s.odds) for s in aligned.rows[0].selections} == {
        "home": "1.50",
        "away": "2.50",
    }
    # A price printed between the two columns is not assigned to the nearest label.
    between, _ = parse_pdf(_sts_page([("2.50", 257.3)]))
    row = between.rows[0]
    assert row.status == RowStatus.REVIEW and "unassigned_odds" in row.issues
    assert row.selections == ()
    # Overlapping prices cannot be separated reliably: the line goes to review as well.
    merged, _ = parse_pdf(_sts_page([("1.50", 247.95), ("2.50", 252.0)]))
    assert merged.rows[0].status == RowStatus.REVIEW
    # Only the second column printed: the price stays with "2", never shifted to "1".
    partial, _ = parse_pdf(_sts_page([("2.50", 266.65)]))
    assert {s.key for s in partial.rows[0].selections} == {"away"}
    assert "missing_selection" in partial.rows[0].issues


def test_superbet_import_generic_parser_is_marked_unverified(superbet_fixture_pdf):
    result, document = parse_pdf(superbet_fixture_pdf, file_name="superbet.pdf")
    assert detect_bookmaker(document) == "superbet"
    assert result.bookmaker == "superbet" and result.parser_verified is False
    assert result.warnings and "niezweryfikowany" in result.warnings[0]
    real = {r.market_key: r for r in result.rows if r.offer_code == "7101"}
    assert real["1x2"].participants == ("Real Madryt", "FC Barcelona")
    assert {s.key: s.odds for s in real["1x2"].selections} == {
        "home": Decimal("2.15"),
        "draw": Decimal("3.50"),
        "away": Decimal("3.20"),
    }  # decimal commas
    assert all("layout_unverified" in r.issues for r in result.rows)
    assert all(r.quality <= 0.7 for r in result.rows)
    total = next(r for r in result.rows if r.offer_code == "7102")
    assert total.market_key == "total" and total.line == "2.5"
    assert result.offer_dates == (date(2026, 10, 31),)


def test_engine_refuses_non_pdf_and_image_only_documents():
    with pytest.raises(ParseError, match="nie jest dokumentem PDF"):
        parse_pdf(b"hello")
    doc = pymupdf.open()
    page = doc.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
    page.insert_image(page.rect, pixmap=pixmap)
    with pytest.raises(ParseError, match="warstwy tekstowej"):
        parse_pdf(doc.tobytes())
    with pytest.raises(ParseError, match="STS"):
        doc2 = pymupdf.open()
        doc2.new_page().insert_text((50, 50), "Inny dokument 1.50 2.50")
        parse_pdf(doc2.tobytes(), bookmaker="sts")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.85", "1.85"),
        ("1,85", "1.85"),
        ("17.0", "17.0"),
        ("185", None),
        ("1.855", None),
        ("x", None),
    ],
)
def test_decimal_odds_parsing(text, expected):
    value = parse_odds(text)
    assert (str(value) if value is not None else None) == expected
    assert parse_odds("125", allow_whole=True) == Decimal("125")


def test_name_line_and_participant_splitting():
    assert split_line("A - B (2.5)") == ("A - B", "2.5")
    assert split_line("A - B (0:3)") == ("A - B", "0:3")
    assert split_line("Kobiety FC (K)") == ("Kobiety FC (K)", None)
    assert split_participants("Real Madryt - FC Barcelona") == ("Real Madryt", "FC Barcelona")
    assert split_participants("Saint-Etienne vs Lyon") == ("Saint-Etienne", "Lyon")
    assert split_participants("Jedna nazwa") is None
    assert split_participants("A - B - C") is None  # ambiguous: no guessing
