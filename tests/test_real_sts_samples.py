"""Regression on authentic STS printed offers kept locally in data/samples (not distributed).

Skipped when the samples are absent. Values checked here were verified visually against the
rendered pages (row 4202 of 31.10.2026: empty "12" column; HT/FT row 52493 of 1.11.2026).
"""

from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

from sports_edge.pdf.engine import parse_pdf  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples" / "sts" / "2026-10-31"
FILES = {
    "today": "oferta-dzisiaj-www.pdf",
    "tomorrow": "oferta-jutro-www.pdf",
    "week": "oferta-nastepne-dni-www.pdf",
}
pytestmark = pytest.mark.skipif(not SAMPLES.is_dir(), reason="no local authentic STS samples")


@pytest.fixture(scope="module")
def parsed():
    out = {}
    for kind, name in FILES.items():
        path = SAMPLES / name
        if path.is_file():
            out[kind] = parse_pdf(path.read_bytes(), file_name=name)[0]
    return out


def test_authentic_documents_are_read(parsed):
    for kind, result in parsed.items():
        summary = result.summary()
        assert summary["offer_kind"] == kind
        assert summary["rows_ok"] / summary["rows"] > 0.97
        assert summary["unparsed_lines"] == 0
        assert summary["events"] > 40


def test_known_rows_match_the_printed_page(parsed):
    if "today" not in parsed:
        pytest.skip("today's sample missing")
    rows = {r.market_key: r for r in parsed["today"].rows if r.offer_code == "4202"}
    assert {s.key: s.odds for s in rows["1x2"].selections} == {
        "home": Decimal("1.42"),
        "draw": Decimal("20.00"),
        "away": Decimal("2.90"),
    }
    assert {s.key for s in rows["double_chance"].selections} == {"home_draw", "draw_away"}
    if "tomorrow" in parsed:
        htft = next(r for r in parsed["tomorrow"].rows if r.offer_code == "52493")
        assert len(htft.selections) == 9
        assert {s.key: s.odds for s in htft.selections}["D/D"] == Decimal("100")


def test_complete_markets_have_plausible_margins(parsed):
    for result in parsed.values():
        for row in result.accepted:
            if row.market_key in {"1x2", "total", "btts"} and len(row.selections) in {2, 3}:
                if row.market_key == "1x2" and len(row.selections) != 3:
                    continue
                overround = sum(1 / s.odds for s in row.selections)
                assert Decimal("0.98") <= overround <= Decimal("1.40"), row.source_text
