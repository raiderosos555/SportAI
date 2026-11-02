"""Offer sources (opt-in network, no paid calls), CLI checks and the Streamlit pages."""

import io
from datetime import UTC, date, datetime

import pytest

from sports_edge.offers.sources import (
    STS_URLS,
    ArbiScanAdapter,
    SourceBlocked,
    StsOfficialPdf,
    arbiscan_to_result,
)
from sports_edge.services import startup_check


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, headers: dict):
        super().__init__(data)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_sts_download_is_opt_in_and_rate_limited(tmp_path):
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        return FakeResponse(
            b"%PDF-1.7 test", {"Content-Disposition": "inline; filename=oferta-jutro-www.pdf"}
        )

    source = StsOfficialPdf(tmp_path / "log.json")
    with pytest.raises(SourceBlocked, match="offline"):
        source.fetch("tomorrow", offline=True, enabled=True, opener=opener)
    with pytest.raises(SourceBlocked, match="wyłączone"):
        source.fetch("tomorrow", offline=False, enabled=False, opener=opener)
    assert calls == []
    document = source.fetch("tomorrow", offline=False, enabled=True, opener=opener)
    assert document.file_name == "oferta-jutro-www.pdf" and calls == [STS_URLS["tomorrow"]]
    with pytest.raises(SourceBlocked, match="10 minut"):
        source.fetch("tomorrow", offline=False, enabled=True, opener=opener)
    with pytest.raises(ValueError):
        source.fetch("yesterday", offline=False, enabled=True, opener=opener)


def test_sts_download_rejects_non_pdf(tmp_path):
    source = StsOfficialPdf(tmp_path / "log.json")
    with pytest.raises(SourceBlocked, match="PDF"):
        source.fetch(
            "today",
            offline=False,
            enabled=True,
            opener=lambda request, timeout: FakeResponse(b"<html>", {}),
        )


def test_paid_api_is_never_called_without_key_and_consent():
    adapter = ArbiScanAdapter(key="")

    def forbidden(request, timeout):
        raise AssertionError("network call attempted")

    for offline, enabled in ((True, True), (False, False), (False, True)):
        with pytest.raises(SourceBlocked):
            adapter.odds(offline=offline, enabled=enabled, params={}, opener=forbidden)
    with pytest.raises(SourceBlocked):
        adapter.health(offline=True, opener=forbidden)


def test_arbiscan_documented_schema_conversion():
    payload = {
        "events": [
            {
                "sport": "Football",
                "league": "Ekstraklasa",
                "home_team": "Legia Warszawa",
                "away_team": "Lech Poznań",
                "commence_time": "2026-10-31T19:30:00Z",
                "bookmakers": [
                    {
                        "key": "Sts",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Legia Warszawa", "price": 2.4},
                                    {"name": "Draw", "price": 3.3},
                                    {"name": "Lech Poznań", "price": 2.9},
                                ],
                            },
                            {
                                "key": "Total|2.5",
                                "outcomes": [
                                    {"name": "Over", "price": 1.9},
                                    {"name": "Under", "price": 1.9},
                                ],
                            },
                            {"key": "Corners|9.5", "outcomes": [{"name": "Over", "price": 1.8}]},
                        ],
                    },
                    {"key": "Betclic", "markets": []},
                ],
            }
        ]
    }
    result = arbiscan_to_result(payload, fetched_at=datetime(2026, 10, 31, 12, tzinfo=UTC))
    assert [r.market_key for r in result.rows] == ["1x2", "total"]
    assert result.rows[0].start_local.isoformat() == "2026-10-31T20:30:00+01:00"
    assert result.rows[1].line == "2.5" and result.parser_verified is False
    assert all("rules_unverified" in r.issues for r in result.rows)


def test_startup_check_reports_configuration(app_paths):
    lines = startup_check(app_paths, today=date(2026, 11, 2))
    assert not [line for line in lines if line.startswith("BŁĄD")]
    assert any("profil STS" in line for line in lines)
    expired = startup_check(app_paths, today=date(2027, 1, 1))
    assert any(line.startswith("UWAGA") and "wygasł" in line for line in expired)
    (app_paths.config / "coupon_policy.toml").write_text(
        "[policy]\nunknown = 1\n", encoding="utf-8"
    )
    assert any(line.startswith("BŁĄD polityka") for line in startup_check(app_paths))


PAGES = {
    "dashboard": "from sports_edge.app.page_dashboard import dashboard_page; dashboard_page()",
    "kupon": "from sports_edge.app.page_coupon import coupon_page; coupon_page()",
    "analiza": "from sports_edge.app.page_match import match_page; match_page()",
    "wydarzenia": "from sports_edge.app.page_events import events_page; events_page()",
    "historia": "from sports_edge.app.pages_history import history_page; history_page()",
    "modele": "from sports_edge.app.page_models import models_page; models_page()",
    "zrodla": "from sports_edge.app.page_sources import sources_page; sources_page()",
    "moj_kupon": "from sports_edge.app.page_my_coupon import my_coupon_page; my_coupon_page()",
    "generator": "from sports_edge.app.page_generator import generator_page; generator_page()",
    "tasiemka": "from sports_edge.app.page_generator import tasiemka_page; tasiemka_page()",
    "budzet": "from sports_edge.app.page_simulators import budget_page; budget_page()",
    "historyczny": "from sports_edge.app.page_simulators import historical_page; historical_page()",
    "uczenie": "from sports_edge.app.page_learning import learning_page; learning_page()",
    "automatyzacja": (
        "from sports_edge.app.page_automation import automation_page; automation_page()"
    ),
}


@pytest.mark.parametrize("demo", [False, True])
def test_streamlit_pages_render(app_paths, demo):
    pytest.importorskip("streamlit")
    pytest.importorskip("pymupdf")
    from streamlit.testing.v1 import AppTest

    if demo:
        loader = AppTest.from_string(
            "from sports_edge.app import common as c; c.load_demo_data()", default_timeout=60
        )
        loader.session_state["demo_mode"] = True
        loader.run()
        assert not loader.exception
    for name, script in PAGES.items():
        app = AppTest.from_string(script, default_timeout=60)
        app.session_state["demo_mode"] = demo
        app.run()
        assert not app.exception, (name, [e.value for e in app.exception])
        if name == "kupon" and demo:
            app.button[0].click().run()
            assert not app.exception, [e.value for e in app.exception]
            # a coupon card or the explanation why there is none — never a silent page
            assert any(
                "Brak kuponu" in m.value or "Zwalidowany" in m.value or "SYMULACJA" in m.value
                for m in app.markdown
            )
    if demo:
        assert not app_paths.database.exists()  # DEMO never writes the production database
