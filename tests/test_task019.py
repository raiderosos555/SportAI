"""TASK 019 — national results, the daily flow without dead ends, plain Polish screens.

Every team, price, result and coupon below is SYNTHETIC test data, not a real fact."""

import sqlite3
import tomllib
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_task005 import cand
from test_task010 import _analysis, _proposal
from test_task017 import NATIONAL_HEADER, NOW, _append_results, _sts_offer

from sports_edge import (
    alerts,
    automation,
    coupon_store,
    decisions,
    manual_results,
    paper,
    services,
    team_gaps,
)
from sports_edge.ingestion.types import iso
from sports_edge.repository import event_status
from sports_edge.results_engine import effective_results
from sports_edge.settings import load_settings
from sports_edge.storage import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
START = NOW + timedelta(hours=5)  # _sts_offer: kick-off five hours after ``now``


def _odds(store, event_id: str, key: str = "home") -> str:
    return store.rows(
        """SELECT o.id FROM odds o JOIN selections s ON s.id=o.selection_id
        JOIN markets m ON m.id=s.market_id WHERE m.event_id=? AND s.key=?""",
        (event_id, key),
    )[0]["id"]


def _names(app_paths) -> dict:
    return {
        "normalizer": services.normalizer(app_paths),
        "team_aliases": services.team_aliases(app_paths),
        "identities": services.team_identities(app_paths),
    }


def _history(store):
    """Both teams known to the national results history (last match 29.03.2026)."""
    _append_results(
        store,
        [
            {"day": "2026-03-29", "home": "Aster", "away": "Beryl", "score": (1, 1)},
            {"day": "2026-03-25", "home": "Cyprus", "away": "Aster", "score": (9, 1)},
        ],
        available_at=datetime(2026, 3, 30, tzinfo=UTC),
    )


@pytest.fixture
def coupon(store, app_paths, profiles):
    """A national STS event (kick-off NOW+5h) on a user's virtual coupon."""
    _history(store)
    event_id, day = _sts_offer(store)
    saved = coupon_store.save_manual(
        store,
        odds_ids=[_odds(store, event_id)],
        stake=Decimal("10"),
        mode="paper_manual",
        sts=profiles["sts"],
        now=NOW,
    )
    return SimpleNamespace(event_id=event_id, day=day, coupon_id=saved.coupon_id)


# 1 ---------------------------------------------------------------- the source check


def test_no_unverified_national_adapter_is_added():
    """§1.2: no free source returned the 2026 national windows (report) — the current plan
    stays openfootball, no competition id is typed in without verification."""
    from sports_edge.international_data import national_current_plan

    plan = national_current_plan(NOW)
    assert {source for _code, source, *_rest in plan} == {"openfootball_int"}
    from sports_edge.datasources.pitchapi import LEAGUES

    assert not {"uefa.nl", "fifa.wc_q", "uefa.euro_q", "fifa.friendly"} & set(LEAGUES)


def test_a_manual_result_never_leaks_into_data_known_before_it_was_typed_in(
    store, app_paths, coupon
):
    entered = START + timedelta(hours=3)
    found = manual_results.record_manual(
        store,
        event_id=coupon.event_id,
        home_score=2,
        away_score=0,
        source="uefa.com — raport meczowy",
        entered_on=entered.date(),
        now=entered,
        **_names(app_paths),
    )
    assert found["history"] is not None
    row = store.get("historical_results", found["history"])
    assert row["provider"] == "manual_entry" and row["available_at"] == iso(entered)
    assert row["observed_at"] == iso(entered)
    before = effective_results(
        store, sport="football", until=entered - timedelta(minutes=1), competitions=("uefa.nl",)
    )
    after = effective_results(store, sport="football", until=entered, competitions=("uefa.nl",))
    assert len(after) == len(before) + 1
    assert all(r["provider_resolved"] != "manual_entry" for r in before)


# 2 ---------------------------------------------------------------- manual result


def test_manual_result_settles_is_append_only_and_yields_to_a_provider(
    store, app_paths, profiles, coupon
):
    with pytest.raises(ValueError, match="nie zakończyć"):  # the match cannot be over yet
        manual_results.record_manual(
            store,
            event_id=coupon.event_id,
            home_score=2,
            away_score=0,
            source="uefa.com",
            entered_on=START.date(),
            now=START + timedelta(minutes=30),
            **_names(app_paths),
        )
    with pytest.raises(ValueError, match="źródło"):
        manual_results.record_manual(
            store,
            event_id=coupon.event_id,
            home_score=2,
            away_score=0,
            source=" ",
            entered_on=START.date(),
            now=START + timedelta(hours=3),
            **_names(app_paths),
        )
    entered = START + timedelta(hours=3)
    manual_results.record_manual(
        store,
        event_id=coupon.event_id,
        home_score=2,
        away_score=0,
        source="uefa.com — raport meczowy",
        entered_on=entered.date(),
        now=entered,
        **_names(app_paths),
    )
    latest = paper.latest_result(store, coupon.event_id)
    assert latest["source"] == "manual_entry" and latest["verified"] == 1
    first = paper.settle_coupon(store, coupon.coupon_id, profiles, now=entered)
    assert first["status"] == "settled"
    assert paper.latest_settlement(store, coupon.coupon_id)["outcome"] == "win"
    assert manual_results.coupon_result_source(store, coupon.coupon_id) == "ręczny (ze źródłem)"
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        store.connection.execute("UPDATE results SET home_score=0")
    # a provider's result arrives later with a DIFFERENT score: it wins, an alert is made
    _append_results(
        store,
        [{"day": coupon.day, "home": "Aster", "away": "Beryl", "score": (0, 1)}],
        available_at=entered + timedelta(hours=10),
    )
    later = entered + timedelta(hours=11)
    found = paper.import_source_results(
        store,
        now=later,
        normalizer=services.normalizer(app_paths),
        team_aliases=services.team_aliases(app_paths),
    )
    assert found["replaced_manual"] == 1
    latest = paper.latest_result(store, coupon.event_id)
    assert latest["source"] == "openfootball_int" and (
        latest["home_score"],
        latest["away_score"],
    ) == (0, 1)
    assert store.scalar("SELECT COUNT(*) FROM results WHERE event_id=?", (coupon.event_id,)) == 2
    mismatch = store.rows("SELECT * FROM prematch_alerts WHERE kind='result_mismatch'")
    assert len(mismatch) == 1 and "2:0" in mismatch[0]["message"]
    paper.settle_coupon(store, coupon.coupon_id, profiles, now=later)
    assert paper.latest_settlement(store, coupon.coupon_id)["outcome"] == "loss"
    assert manual_results.coupon_result_source(store, coupon.coupon_id) == "dostawca"
    # the provider wins in the history and the user's entry is no "conflict"
    row = next(
        r
        for r in effective_results(store, sport="football", until=later, competitions=("uefa.nl",))
        if r["match_date"] == coupon.day
    )
    assert row["provider_resolved"] == "openfootball_int" and not row["conflict"]
    assert row["manual_differs"] is True


def test_a_provider_result_already_there_refuses_the_manual_entry(store, app_paths, coupon):
    paper.record_result(
        store,
        event_id=coupon.event_id,
        status="completed",
        now=START + timedelta(hours=2),
        home_score=1,
        away_score=0,
        source="football_data_org",
    )
    with pytest.raises(ValueError, match="Wynik od dostawcy już jest"):
        manual_results.record_manual(
            store,
            event_id=coupon.event_id,
            home_score=2,
            away_score=0,
            source="uefa.com",
            entered_on=START.date(),
            now=START + timedelta(hours=3),
            **_names(app_paths),
        )


# 3 ---------------------------------------------------------------- completeness of a team


def _archive_match(store):
    """An STS match of the archive four days ago that has no result anywhere."""
    return _sts_offer(store, now=NOW - timedelta(days=4), away="Cinder")


def test_completeness_counts_matches_that_took_place_without_a_result(store, app_paths):
    _history(store)
    _archive_match(store)
    gaps = team_gaps.measure(
        store,
        now=NOW,
        normalizer=services.normalizer(app_paths),
        aliases=services.alias_map(store, services.team_aliases(app_paths)),
    )
    aster = gaps["Aster"]
    assert aster["last_match"] == "2026-03-29" and aster["missing"] == 1
    assert aster["status"] == "missing" and aster["missing_matches"][0]["basis"] == (
        "archiwum oferty STS"
    )
    text = team_gaps.badge(aster, "Aster")
    assert text.startswith("Model nie zna 1 ostatniego meczu drużyny (Aster)")
    assert "29.03.2026" in text and "brak danych" in text  # the uncovered period, never 0
    # a team without any coverage: "nieznane", not "complete"
    assert gaps["Cyprus"]["status"] == "unknown" and gaps["Cyprus"]["missing"] == 0
    assert "brak danych" in team_gaps.badge(gaps["Cyprus"], "Cypr")


def test_without_the_users_decision_candidates_only_get_the_warning(store, app_paths):
    _history(store)
    _archive_match(store)
    event_id, _day = _sts_offer(store)
    base = [
        cand("x", "1.40", 0.80, 0.76, predictive_status="validated_predictive"),
        cand("y", "1.50", 0.70, 0.66, predictive_status="experimental"),
    ]
    base = [replace(x, event_id=event_id, league=NATIONAL_HEADER) for x in base]
    settings = load_settings(app_paths)
    assert settings.national_gap_rule == "allow"
    after = team_gaps.apply_rule(store, app_paths, settings, base, now=NOW)
    assert all(x.data_gaps.startswith("Model nie zna 1 ostatniego meczu") for x in after), [
        x.data_gaps for x in after
    ]
    names = [f.name for f in fields(base[0]) if f.name != "data_gaps"]
    for old, new in zip(base, after, strict=True):  # TASK 018 regression: nothing else changes
        assert [getattr(old, n) for n in names] == [getattr(new, n) for n in names]
    excluded = team_gaps.apply_rule(
        store, app_paths, replace(settings, national_gap_rule="exclude"), base, now=NOW
    )
    assert [x.predictive_status for x in excluded] == ["experimental", "experimental"]
    assert "decyzja użytkownika" in excluded[0].status_note
    # a league selection never gets the warning
    league = [replace(base[0], league="HISZPANIA - LALIGA")]
    assert team_gaps.apply_rule(store, app_paths, settings, league, now=NOW) == league


def test_the_decision_about_teams_with_missing_results_is_the_users(store, app_paths):
    settings = load_settings(app_paths)
    items = decisions.panel(store, app_paths, settings, now=NOW, compared={})
    item = next(i for i in items if i["key"] == decisions.NATIONAL_GAPS)
    assert item["open"] and item["value"] == "allow" and "domyślnie" in item["state"]
    saved = decisions.decide_national_gaps(store, app_paths, settings, value="exclude", now=NOW)
    assert saved and load_settings(app_paths).national_gap_rule == "exclude"
    row = decisions.last(store, decisions.NATIONAL_GAPS)
    assert row["previous"] == {"national_gap_rule": "allow"} and row["actor"] == "user"


# 4 ---------------------------------------------------------------- waiting for a result


def test_a_coupon_unsettled_after_24_hours_says_which_leg_and_why(store, app_paths, coupon):
    over = START + timedelta(hours=2)
    early = manual_results.pending_coupons(
        store, now=over + timedelta(hours=20), **_names(app_paths)
    )
    assert early == []
    found = manual_results.pending_coupons(
        store, now=over + timedelta(hours=25), **_names(app_paths)
    )
    assert len(found) == 1
    leg = found[0]["missing"][0]
    assert leg["event"] == "Aster - Beryl" and leg["expected"] == "wynik po 90 minutach"
    assert "openfootball" in leg["source"] and leg["reason"]
    assert leg["manual_possible"] is True and "last_attempt" in leg


# 5 ---------------------------------------------------------------- the coupon of the day job


def test_the_daily_coupon_job_is_off_by_default(store):
    settings = automation.load_settings(store)
    assert settings["jobs"]["daily_coupon"]["enabled"] is False
    assert automation.next_run(store, "daily_coupon", settings, now=NOW) is None
    settings["jobs"]["daily_coupon"]["enabled"] = True
    when = automation.next_run(store, "daily_coupon", settings, now=NOW)
    assert when is not None and when.astimezone(automation.WARSAW).strftime("%H:%M") == "09:00"


def test_the_job_and_a_click_on_the_same_day_make_one_official_coupon(
    store, app_paths, monkeypatch
):
    _history(store)
    event_id, day = _sts_offer(store)
    legs = [_odds(store, event_id)]
    calls = []

    def analysis(db, paths, settings, days, *, now, **options):
        calls.append(options)
        runs = {}
        for one in days:
            built = SimpleNamespace(proposals={"standard": _proposal(db, legs)})
            saves = coupon_store.save_proposals(
                db, paths, built, analysis_id=_analysis(db), now=now, day=one
            )
            runs[one] = services.PipelineRun(
                report=SimpleNamespace(headline="test", counts={}),
                candidates=[],
                analysis_id=_analysis(db),
                stages=[],
                coupons=built,
                coupon_ids={n: r["coupon_id"] for n, r in saves.items()},
                day=one,
                coupon_saves=saves,
            )
        return services.RangeRun(days=list(days), runs=runs, stages=[], refresh=[], run_at="")

    monkeypatch.setattr(services, "run_analysis", analysis)
    settings = automation.load_settings(store)
    outcome = automation.job_daily_coupon(store, app_paths, now=NOW, settings=settings)
    assert outcome.status == "ok" and outcome.payload["profiles"] == ["standard"]
    assert calls[0]["refresh_offer"] is False and calls[0]["refresh_results"] is False  # offline
    analysis(store, app_paths, load_settings(app_paths), [day], now=NOW + timedelta(minutes=5))
    relations = [r["relation"] for r in store.rows("SELECT relation FROM coupon_lineage")]
    assert relations.count("official") == 1
    ready = store.rows("SELECT * FROM prematch_alerts WHERE kind='daily_coupons_ready'")
    assert len(ready) == 1 and ready[0]["message"].startswith("Kupony dnia gotowe")
    automation.run_job(
        store,
        app_paths,
        "daily_coupon",
        now=NOW,
        settings=settings,
        trigger="schedule",
        runner=lambda *a, **k: outcome,
    )
    settings["jobs"]["daily_coupon"]["enabled"] = True
    tomorrow = automation.next_run(store, "daily_coupon", settings, now=NOW + timedelta(minutes=1))
    assert tomorrow.astimezone(automation.WARSAW).date() > NOW.astimezone(automation.WARSAW).date()


# 6/7 ---------------------------------------------------------------- Kupon dnia


def test_the_range_shows_saved_proposals_of_the_chosen_day_without_analysis(app_paths, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from sports_edge.app import common

    store = services.open_store(app_paths)
    _history(store)
    tomorrow_now = NOW + timedelta(days=1)
    event_id, day = _sts_offer(store, now=tomorrow_now)
    store.insert(
        "analyses",
        id="analysis-tomorrow",
        created_at=iso(NOW),
        analysis_date=str(day),
        policy_json='{"version":"test"}',
        summary_json='{"headline":"test","analysed":0,"accepted":[],"counts":{}}',
        items_json="[]",
    )
    built = SimpleNamespace(proposals={"standard": _proposal(store, [_odds(store, event_id)])})
    coupon_store.save_proposals(
        store, app_paths, built, analysis_id="analysis-tomorrow", now=NOW, day=str(day)
    )
    store.close()
    monkeypatch.setattr(common, "now", lambda: NOW)
    app = AppTest.from_string(
        "from sports_edge.app.page_coupon import coupon_page\ncoupon_page()\n",
        default_timeout=120,
    )
    app.session_state["demo_mode"] = False
    app.session_state["coupon_range"] = "Jutro"
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    text = " ".join(m.value for m in app.markdown) + " ".join(c.value for c in app.caption)
    assert "Aster" in text and "Zapisane propozycje tego dnia" in text
    app.session_state["coupon_range"] = "Dziś"
    app.run()
    text = " ".join(m.value for m in app.markdown)
    assert "Na ten dzień nie ma jeszcze kuponów — kliknij Analizuj" in text


def test_a_started_coupon_is_in_play_then_waits_and_cannot_go_to_the_cart():
    from sports_edge.app import coupon_view

    payload = {"legs": [{"start_at": iso(START)}, {"start_at": iso(START + timedelta(hours=2))}]}
    assert coupon_view.play_state(payload, NOW) is None
    assert not coupon_view.started(payload, NOW)
    state = coupon_view.play_state(payload, START + timedelta(minutes=10))
    assert state[0].startswith("W grze od ") and coupon_view.started(payload, START)
    assert coupon_view.play_state(payload, START + timedelta(hours=5))[0] == "Czeka na wynik"
    assert coupon_view.play_state({**payload, "outcome": "win"}, START + timedelta(hours=5)) is None
    source = (ROOT / "src/sports_edge/app/coupon_view.py").read_text(encoding="utf-8")
    assert "if started(payload):" in source.split("def card(")[1]  # the add button is hidden


# 8 ---------------------------------------------------------------- competitions without data


def test_matches_without_data_are_unique_closed_and_under_the_coupons():
    from sports_edge.app import international_ui as iui

    events = [
        {
            "league": "MIĘDZYNARODOWE - PUCHAR NARODÓW AFRYKI - KWALIFIKACJE",
            "home": "Gwinea",
            "away": "Erytrea",
            "name_raw": "Gwinea - Erytrea",
            "start_at": iso(START),
            "sport": "football",
        },
    ] * 2 + [
        {
            "league": "MIĘDZYNARODOWE - KING'S CUP",
            "home": "A",
            "away": "B",
            "name_raw": "A - B",
            "start_at": iso(START),
            "sport": "football",
        },
    ]
    rows = iui.no_data_matches(events)
    assert len(rows) == 2 and rows[0][0] <= rows[1][0]  # grouped by competition
    assert iui._matches(24) == "24 mecze" and iui._matches(25) == "25 meczów"
    source = (ROOT / "src/sports_edge/app/international_ui.py").read_text(encoding="utf-8")
    block = source.split("def national_coupon_block(")[1].split("def no_data_matches(")[0]
    assert "with ui.details(summary" in block  # a closed panel with one summary line
    page = (ROOT / "src/sports_edge/app/page_coupon.py").read_text(encoding="utf-8")
    day = page.split("def _day(")[1].split("def _exposure(")[0]
    assert day.rindex("national_coupon_block") > day.index("coupon_view.card(")


def test_day_risk_says_two_sentences_by_default():
    from sports_edge.app import page_coupon

    leg = {
        "event": "Aster - Beryl",
        "event_id": "e1",
        "market_key": "1x2",
        "selection": "home",
        "p_model": 0.8,
        "p_conservative": 0.75,
    }
    proposals = {
        "standard": {"label": "STANDARDOWY", "legs": [leg]},
        "high_hit": {"label": "WYSOKA TRAFNOŚĆ", "legs": [leg]},
    }
    exposure = page_coupon._exposure(proposals)
    lines = page_coupon.risk_summary(proposals, exposure)
    assert len(lines) == 2 and lines[0].startswith("Szansa, że przegrają co najmniej dwa")
    assert "Aster - Beryl (STANDARDOWY, WYSOKA TRAFNOŚĆ)" in lines[1]
    assert not any("starszy zapis" in line for line in lines)


# 9 ---------------------------------------------------------------- Analiza meczu


def test_national_analysis_has_one_rest_value_polish_names_and_the_teams_side(
    store, app_paths, monkeypatch
):
    from sports_edge import international
    from sports_edge.app import match_view

    _history(store)
    names = international.polish_names(store, app_paths, now=NOW)
    assert names.get("Cyprus") == "Cypr"  # an existing alias of config/team_aliases.toml
    rows = effective_results(store, sport="football", until=NOW, competitions=("uefa.nl",))
    plan = international._schedule(rows, "Aster", "2026-04-10", names)
    lost = next(r for r in plan["recent"] if r["opponent"] == "Cypr")
    assert lost["own_score"] == "1:9" and lost["outcome"] == "P" and lost["score_90"] == "9:1"
    assert plan["rest_days"] == 12 and plan["last_match"] == "2026-03-29"
    view = {
        "category": "national",
        "schedule": {"home": plan, "away": {**plan, "rest_days": 30, "matches_14": 0}},
    }
    captured = []
    monkeypatch.setattr(match_view.ui, "compare_table", lambda h, a, r: captured.extend(r) or "")
    monkeypatch.setattr(match_view.ui, "card", lambda body, **k: body)
    monkeypatch.setattr(match_view.st, "markdown", lambda *a, **k: None)
    monkeypatch.setattr(match_view.st, "caption", lambda *a, **k: None)
    head = SimpleNamespace(event="Aster - Beryl")
    teams = {
        "home": {"form5": {"points_per_game": 1.0}, "rest_days": 599},
        "away": {"form5": {"points_per_game": 1.0}, "rest_days": 724},
    }
    match_view.comparison(head, teams, view)
    rest = next(r for r in captured if r[0] == "Dni odpoczynku")
    assert rest[1:3] == ("12", "30") and "wszystkie mecze reprezentacji" in rest[4]
    assert "ligowe" not in rest[4]
    # the form comes from the same matches as the cards (not from another feature source)
    form = next(r for r in captured if r[0] == "Forma (5 ostatnich)")
    assert form[1] == " ".join(r["outcome"] for r in reversed(plan["recent"]))


# 10/11 ---------------------------------------------------------------- statuses and events


def test_a_match_past_its_duration_without_a_result_waits_for_the_result():
    from sports_edge.app import common

    late = START + timedelta(hours=4)
    assert event_status(iso(START), late, sport="football") == "awaiting_result"
    assert common.EVENT_STATUS["awaiting_result"] == "zakończony — czekamy na wynik"
    assert event_status(None, late, sport="football") == "unknown"


def test_events_category_only_for_football_and_polish_sports():
    from sports_edge.app import common
    from sports_edge.app import international_ui as iui

    assert iui.category_label("CHALLENGER - KNOXVILLE", "tennis") == "—"
    assert iui.category_label("NHL", "ice_hockey") == "—"
    assert iui.category_label("HISZPANIA - LALIGA", "football") == iui.CHOICES["domestic"]
    assert common.sport_label("ice_hockey") == "hokej na lodzie"
    assert common.sport_label("tennis") == "tenis" and common.sport_label("football") == (
        "piłka nożna"
    )


# 12 ---------------------------------------------------------------- Mój kupon


def test_started_cart_legs_are_marked_and_left_out_of_the_chance():
    from sports_edge.app import cart

    legs = [
        {"selection_id": "a", "start_at": iso(NOW - timedelta(hours=1))},
        {"selection_id": "b", "start_at": iso(NOW + timedelta(hours=3))},
    ]
    assert [x["selection_id"] for x in cart.started_legs(legs, NOW)] == ["a"]
    kept, rows = cart.counted(legs, ["ra", "rb"], late=False, now=NOW)
    assert [x["selection_id"] for x in kept] == ["b"] and rows == ["rb"]
    assert cart.counted(legs, ["ra", "rb"], late=True, now=NOW) == (legs, ["ra", "rb"])


# 13 ---------------------------------------------------------------- alerts and the worker


def test_alerts_of_started_matches_are_archived_and_not_counted(store, app_paths, coupon):
    alerts.create(
        store,
        kind="coupon_attention",
        message="kurs",
        source="test",
        now=NOW,
        event_id=coupon.event_id,
    )
    before = alerts.overview(store, now=NOW)
    assert alerts.counts_text(before) == "ważne 1 · wszystkie 1"
    later = START + timedelta(minutes=1)
    assert alerts.archive_started(store, now=later) == 1
    assert alerts.archive_started(store, now=later) == 0  # idempotent: one state row
    after = alerts.overview(store, now=later)
    assert after["actionable"] == 0 and alerts.counts_text(after) == "brak nowych"
    assert store.scalar("SELECT COUNT(*) FROM prematch_alerts") == 1  # nothing deleted
    assert SCHEMA_VERSION >= 13 and "alert_states" in store.tables


def test_job_statuses_and_skip_reasons_are_polish_and_errors_heal():
    from sports_edge.app import common, page_automation

    assert [common.job_status(s) for s in ("ok", "partial", "skipped", "failed", None)] == [
        "wykonane",
        "częściowo",
        "pominięte",
        "błąd",
        "—",
    ]
    text = page_automation.reason_text(
        "not_before_start: 73; outside_horizon: 16; unmapped_competition: 52"
    )
    assert "73 — mecz już rozpoczęty" in text and "_" not in text
    history = [
        {"job": "auto:national_results", "status": "ok"},
        {"job": "auto:national_results", "status": "failed"},
        {"job": "auto:results", "status": "partial"},
    ]
    assert [r["job"] for r in page_automation.open_errors(history)] == ["auto:results"]
    assert page_automation.control_buttons({"alive": False, "label": "zatrzymana"}) == [
        "start",
        "check",
    ]
    assert len(page_automation.control_buttons({"alive": True, "label": "aktywna"})) == 3


def test_national_results_run_like_league_results_on_a_match_day(store, app_paths):
    _history(store)
    event_id, _day = _sts_offer(store, now=NOW - timedelta(hours=7))  # kick-off 2 h ago
    settings = automation.load_settings(store)
    assert automation.national_matchdays(store, now=NOW)
    when = automation.next_run(store, "national_results", settings, now=NOW)
    assert when == NOW  # never ran: due now
    automation.run_job(
        store,
        app_paths,
        "national_results",
        now=NOW,
        settings=settings,
        trigger="schedule",
        runner=lambda *a, **k: automation.JobOutcome("ok", ""),
    )
    nxt = automation.next_run(store, "national_results", settings, now=NOW + timedelta(minutes=1))
    every = settings["jobs"]["results"]["every_minutes"]
    assert nxt <= NOW + timedelta(minutes=every, seconds=5)  # not three days later


# 14 ---------------------------------------------------------------- Streamlit configuration


def test_the_agent_skills_card_is_switched_off():
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["logger"]["hideWelcomeMessage"] is True
    import streamlit.web.skills as skills

    source = Path(skills.__file__).read_text(encoding="utf-8")
    assert 'get_option("logger.hideWelcomeMessage")' in source  # the nudge honours it
