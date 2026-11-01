"""TASK 006: understandable analysis, multi-day coupons, STS freshness, coverage, model scopes.

Every record is a labelled SYNTHETIC fixture (invented teams, scores and odds); providers run
through fakes, never the network. Passing tests show that the application behaves as
designed — not that its coupons will win.
"""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import ROOT
from test_task005 import NOW, RULES, cand, policy
from test_task005 import build as build_with

from sports_edge import insights as ins
from sports_edge import services
from sports_edge.analysis.coupon_builder import VALIDATED
from sports_edge.analysis.policy import Policy
from sports_edge.coverage import FUNNEL, coverage_report
from sports_edge.datasources.base import FetchOutcome, ProviderStatus, ResultRecord
from sports_edge.models.registry import latest_predictions, register_version, save_prediction
from sports_edge.offers.sources import SourceBlocked
from sports_edge.offers.text import load_normalizer
from sports_edge.paper import record_result
from sports_edge.probability_lab import league_gate, pair_dependence
from sports_edge.repository import ImportOutcome
from sports_edge.results_engine import backfill_results, effective_results
from sports_edge.settings import AppSettings

NAMES = load_normalizer(ROOT / "config" / "aliases.toml")


# ---------------------------------------------------------------- formatting and words


def test_numbers_are_shown_as_polish_percent_odds_and_money():
    assert ins.pct(0.712) == "71,2%" and ins.pct(0.662) == "66,2%"
    assert ins.pct(None) == "Brak danych" and ins.num(None) == "Brak danych"
    assert ins.num("1.36") == "1,36" and ins.money("11.97") == "11,97 zł"
    assert [ins.plural(n, "mecz", "mecze", "meczów") for n in (1, 2, 5, 12, 22, 504)] == [
        "1 mecz",
        "2 mecze",
        "5 meczów",
        "12 meczów",
        "22 mecze",
        "504 mecze",
    ]
    assert ins.sentence("lekka przewaga: Royal Charleroi") == "Lekka przewaga: Royal Charleroi"


def test_picks_are_described_in_plain_words():
    event = "Atalanta - Parma"
    assert ins.pick_label("total", "1.5", "over", event) == "Powyżej 1,5 gola"
    assert ins.pick_label("double_chance", None, "home_draw", event) == "Atalanta lub remis (1X)"
    assert ins.pick_label("1x2", None, "away", event) == "Wygrana: Parma"
    assert ins.pick_label("btts", None, "no", event) == "Nie obie drużyny strzelą"
    assert ins.pick_label("team_total_home", "0.5", "over", event) == "Atalanta: powyżej 0,5 gola"


def test_match_outlook_names_the_favourite_only_when_there_is_one():
    assert (
        ins.outlook({"home": 0.40, "draw": 0.29, "away": 0.31}, "Getafe", "Sevilla")["character"]
        == "mecz wyrównany"
    )
    clear = ins.outlook({"home": 0.62, "draw": 0.22, "away": 0.16}, "Getafe", "Sevilla")
    assert clear["character"] == "wyraźny faworyt: Getafe" and clear["favourite"] == "Getafe"
    assert ins.outlook(None, "a", "b") is None


# ---------------------------------------------------------------- reliability and candidates


def test_reliability_is_a_label_from_validation_uncertainty_price_and_time():
    soon = NOW + timedelta(hours=8)
    near = cand("A", "1.40", 0.80, 0.78, start_at=soon.isoformat())
    assert ins.reliability(near, now=NOW)[0] == "high"
    wide = cand("A", "1.40", 0.80, 0.74, start_at=soon.isoformat())
    assert ins.reliability(wide, now=NOW)[0] == "moderate"
    stale = cand("A", "1.40", 0.80, 0.78, start_at=soon.isoformat(), freshness="unconfirmed")
    level, reasons = ins.reliability(stale, now=NOW)
    assert level == "elevated" and any("kurs" in r for r in reasons)
    far = cand("A", "1.40", 0.80, 0.78, start_at=(NOW + timedelta(days=5)).isoformat())
    assert ins.reliability(far, now=NOW)[0] == "elevated"
    assert ins.reliability(cand("A", "1.40", 0.8, 0.78, p_low=None), now=NOW)[0] == "insufficient"


def test_candidates_are_split_into_qualifying_experimental_and_rejected():
    rows = [
        cand("A", "1.40", 0.80, 0.77),
        cand("B", "1.40", 0.80, 0.77, predictive_status="experimental"),
        cand("C", "1.40", 0.80, 0.77, freshness="unconfirmed", odds_age_hours=19.8),
        cand("D", "1.10", 0.95, 0.93),  # 1.10 × 0.88 < 1
        cand("E", "1.40", 0.80, 0.77, p_model=None),
    ]
    groups = {g["event"][:1]: g for g in ins.candidate_groups(rows, now=NOW, max_odds=4.0)}
    assert groups["A"]["group"] == "A" and groups["A"]["pick"] == "Powyżej 1,5 gola"
    assert groups["B"]["group"] == "B" and "eksperymentalny" in groups["B"]["reason"]
    assert groups["C"]["group"] == "B" and groups["C"]["price_status"] == "historyczny"
    assert "19,8 h" in groups["C"]["reason"]
    assert groups["D"]["group"] == "C" and "podatek" in groups["D"]["reason"]
    assert "E" not in groups  # no model probability: not a candidate at all


def test_a_pick_needs_a_reliable_probability_not_just_the_largest_one():
    weak = [cand("A", "1.90", 0.52, 0.48), cand("A", "2.10", 0.47, 0.44)]
    assert ins.best_pick(weak, now=NOW, max_odds=4.0) is None
    good = weak + [cand("A", "1.40", 0.81, 0.77, market_key="double_chance")]
    pick = ins.best_pick(good, now=NOW, max_odds=4.0)
    assert pick["candidate"].p_low == 0.77 and pick["eligible"]


# ---------------------------------------------------------------- why there is no coupon


def _offer(fresh: bool, age: float = 19.8) -> dict:
    return {"fresh": fresh, "age_hours": age if not fresh else 1.0, "limit_hours": 3.0}


def test_no_coupon_case_a_stale_prices_names_the_age_and_the_limit():
    rows = [cand(e, "1.40", 0.80, 0.77, freshness="unconfirmed") for e in "ABC"]
    built = build_with(rows)
    out = ins.explain_no_coupon(rows, built, _offer(False), now=NOW, max_odds=4.0)
    assert out["case"] == "A" and "19,8 h" in out["headline"] and "3 h" in out["headline"]
    assert out["count"] == 3


def test_no_coupon_case_b_reports_events_beyond_the_models():
    rows = [cand(f"E{i}", "1.90", 0.5, 0.45, p_model=None, p_low=None) for i in range(40)]
    rows.append(cand("M", "1.30", 0.6, 0.5))
    out = ins.explain_no_coupon(rows, build_with(rows), _offer(True), now=NOW, max_odds=4.0)
    assert out["case"] == "B" and "41 wydarzeń" in out["headline"] and "tylko 1" in out["headline"]


def test_no_coupon_cases_c_d_e_follow_the_builder_refusals():
    low = [cand(e, "1.60", 0.58, 0.52) for e in "ABCDEFGHIJ"]
    out = ins.explain_no_coupon(low, build_with(low), _offer(True), now=NOW, max_odds=4.0)
    assert out["case"] == "C" and "minimalnej ostrożnej szansy" in out["headline"]
    likely = [cand("A", "1.15", 0.95, 0.92)]  # very likely, but no profile's payout
    built = build_with(likely)
    assert set(built.refusal_codes.values()) == {"payout"}
    out = ins.explain_no_coupon(likely, built, _offer(True), now=NOW, max_odds=4.0)
    assert out["case"] == "D" and "wypłaty netto" in out["headline"]
    experimental = [cand(e, "1.30", 0.86, 0.83, predictive_status="experimental") for e in "ABC"]
    built = build_with(experimental)
    assert built.refusal_codes["safe"] == "not_validated"
    out = ins.explain_no_coupon(experimental, built, _offer(True), now=NOW, max_odds=4.0)
    assert out["case"] == "E" and "walidacji" in out["headline"]


def test_builder_names_why_a_profile_is_empty_and_offers_different_alternatives():
    rules = policy()
    assert build_with([], rules).refusal_codes["safe"] == "no_candidates"
    low = build_with([cand("A", "1.60", 0.58, 0.52)], rules)
    assert low.refusal_codes["safe"] == "low_probability"
    pays = build_with([cand("A", "1.15", 0.95, 0.92)], rules)
    assert pays.refusal_codes["safe"] == "payout"  # likely, but 1.15 < the minimum odds
    rows = [cand(e, "1.30", 0.85 - i / 100, 0.82 - i / 100) for i, e in enumerate("ABCD")]
    built = build_with(rows, rules)
    best = {leg.event_id for leg in built.proposals["safe"].legs}
    for alternative in built.alternatives["safe"]:
        assert not best & {leg.event_id for leg in alternative.legs}  # a different bet


# ---------------------------------------------------------------- STS download status


class _Fetch:
    """SYNTHETIC official STS download: one outcome (or error) per offer kind."""

    def __init__(self, outcomes: dict):
        self.outcomes, self.calls = outcomes, []

    def __call__(self, store, paths, settings, kind, *, now):
        self.calls.append(kind)
        found = self.outcomes[kind]
        if isinstance(found, Exception):
            raise found
        return found


def _doc(rows: int, days: list[str], *, duplicate: bool = False) -> ImportOutcome:
    summary = {
        "rows": rows,
        "events": rows // 3,
        "generated_at": "2026-11-01T16:55:03+00:00",
        "offer_dates": days,
    }
    return ImportOutcome("synthetic", duplicate, summary)


def test_sts_refresh_says_exactly_what_the_download_did(store, app_paths):
    online = AppSettings(offline_mode=False, sts_download_enabled=True)
    now = datetime(2026, 11, 1, 17, tzinfo=UTC)
    fetch = _Fetch(
        {
            "today": _doc(0, ["2026-11-01"]),
            "tomorrow": _doc(1642, ["2026-11-02"]),
            "week": SourceBlocked("Ten plik pobrano przed chwilą; odczekaj 10 minut"),
        }
    )
    attempts = services.refresh_offers(
        store, app_paths, online, ["2026-11-01"], now=now, fetch=fetch
    )
    status = {a["kind"]: a["status"] for a in attempts}
    # the empty "today" file makes the other official variants be checked as well
    assert status == {"today": "empty", "tomorrow": "no_day_events", "week": "blocked"}
    assert "pusty" in attempts[0]["message"] and "0 wydarzeń" in attempts[0]["message"]
    same = _Fetch({"tomorrow": _doc(1642, ["2026-11-02"], duplicate=True)})
    again = services.refresh_offers(store, app_paths, online, ["2026-11-02"], now=now, fetch=same)
    assert again[0]["status"] == "duplicate" and "nie zostały potwierdzone" in again[0]["message"]
    new = _Fetch({"tomorrow": _doc(1642, ["2026-11-02"])})
    assert (
        services.refresh_offers(store, app_paths, online, ["2026-11-02"], now=now, fetch=new)[0][
            "status"
        ]
        == "new"
    )
    offline = services.refresh_offers(
        store, app_paths, AppSettings(), ["2026-11-02"], now=now, fetch=new
    )
    assert offline[0]["status"] == "skipped" and new.calls == ["tomorrow"]  # nothing fetched


def test_analysis_ranges_and_sts_files_follow_the_warsaw_calendar():
    today = date(2026, 11, 1)
    assert services.analysis_days("Dziś", today) == ["2026-11-01"]
    assert services.analysis_days("Jutro", today) == ["2026-11-02"]
    assert services.analysis_days("3 dni", today) == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert len(services.analysis_days("7 dni", today)) == 7
    assert services.sts_kinds_for(services.analysis_days("7 dni", today), today) == [
        "today",
        "tomorrow",
        "week",
    ]


def test_freshness_comes_from_the_document_never_from_the_download(store, import_sts):
    settings = AppSettings()
    day = "2026-10-31"
    early = services.offer_status(
        store, settings, day, now=datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    )
    late = services.offer_status(
        store, settings, day, now=datetime(2026, 10, 31, 20, 0, tzinfo=UTC)
    )
    assert early["fresh"] and not late["fresh"]
    assert late["age_hours"] > late["limit_hours"] == 3.0


# ---------------------------------------------------------------- coverage


def test_coverage_counts_events_markets_and_selections_separately(store, import_sts, app_paths):
    report = coverage_report(
        store, app_paths, AppSettings(), ["2026-10-31"], now=datetime(2026, 10, 31, 9, tzinfo=UTC)
    )
    funnel = [f["events"] for f in report["funnel"]]
    assert [f["key"] for f in report["funnel"]] == [k for k, _ in FUNNEL]
    assert funnel == sorted(funnel, reverse=True)  # each condition only removes events
    events = report["events"]
    assert funnel[0] == len(events)
    # every event without a forecast has exactly one first failed condition
    assert all((e["reason"] is None) == e["checks"]["forecast"] for e in events)
    assert sum(r["events"] for r in report["by_reason"]) == funnel[0] - funnel[-1]
    totals = report["totals"]
    assert totals["selections"] >= totals["markets"] >= funnel[0]
    reasons = {r["reason"] for r in report["by_reason"]}
    assert "dyscyplina bez modelu" in reasons  # tennis / basketball in the fixture
    assert any(r["group"] == "tennis" for r in report["by_sport"])


# ---------------------------------------------------------------- scopes, preference, history


def _version(store, family, version, when, competitions, validation=None):
    version_id, _s, _r = register_version(
        store,
        family=family,
        sport="football",
        description="SYNTETYCZNY",
        version=version,
        created_at=when,
        trained_through=when,
        training_data={"competitions": list(competitions)},
        validation=validation or {},
        policy=Policy(),
    )
    return version_id


def test_each_scope_serves_its_own_versions_and_wide_experiments_stay_unserved(store, import_sts):
    core = _version(store, "elo_ordered_draw", "core", NOW, services.CORE_COMPETITIONS)
    wide = _version(
        store,
        "elo_ordered_draw",
        "wide",
        NOW + timedelta(minutes=5),
        services.RESEARCH_COMPETITIONS,
    )
    ext = _version(store, "elo_ordered_draw", "ext", NOW, services.EXTENDED_COMPETITIONS)
    assert services.scope_version(store, services.CORE_COMPETITIONS)["version_id"] == core
    assert services.scope_version(store, services.EXTENDED_COMPETITIONS)["version_id"] == ext
    assert wide not in services.active_version_ids(store)
    event = store.rows("SELECT * FROM events WHERE home LIKE 'Real Madrid%'")[0]
    inputs = {"info_cutoff": NOW.isoformat(), "data_snapshot": "s", "code_state": "c"}
    for version, home, minutes in ((core, 0.5, 0), (wide, 0.2, 10)):
        save_prediction(
            store,
            model_version_id=version,
            event_id=event["id"],
            market_key="1x2",
            period="FT",
            line=None,
            probabilities={"home": home, "draw": 0.3, "away": round(0.7 - home, 6)},
            uncertainty={},
            inputs=inputs,
            kind="prospective",
            created_at=NOW + timedelta(minutes=minutes),
        )
    key = (event["id"], "1x2", "FT", None)
    later = NOW + timedelta(hours=1)
    assert latest_predictions(store, as_of=later)[key]["model_version_id"] == wide
    served = latest_predictions(store, as_of=later, versions=services.active_version_ids(store))
    assert served[key]["model_version_id"] == core  # an experiment is not a release
    assert latest_predictions(store, as_of=later, offer_dates=["2026-11-05"]) == {}


def test_the_validated_model_serves_1x2_when_only_one_passed(store):
    def goals(name, scope, elo_ok, dc_ok, served):
        status = {True: "validated_predictive", False: "experimental"}
        _version(
            store,
            "dixon_coles_goals",
            name,
            NOW,
            scope,
            {
                "elo_predictive": {"1x2": {"status": status[elo_ok]}},
                "predictive": {"1x2": {"status": status[dc_ok]}},
                "markets_served": served,
            },
        )

    goals("core", services.CORE_COMPETITIONS, True, True, ["total"])
    goals("ext", services.EXTENDED_COMPETITIONS, False, True, ["total"])
    preference = services.prediction_preference(store)
    assert preference[("1x2", "es.1")] == "elo_ordered_draw"  # both passed: lower log loss
    assert preference[("1x2", "en.2")] == "dixon_coles_goals"  # only DC passed
    assert preference["1x2"] == "elo_ordered_draw"


class _History:
    """SYNTHETIC history source: one season of results per window."""

    paid, requires_network, license = False, False, "dane syntetyczne (test)"

    def __init__(self, provider_id, family, seasons):
        self.id, self.family, self.name = provider_id, family, f"Historia {provider_id}"
        self.competitions = ("en.2",)
        self.seasons = seasons

    def status(self, *, offline):
        return ProviderStatus(True, "test")

    def fetch(self, competition, date_from, date_to, *, now):
        records = [
            ResultRecord(
                provider=self.family,
                sport="football",
                competition="en.2",
                season=None,
                source_event_id=f"{self.id}-{date_from.year}-{i}",
                home=home,
                away=away,
                start_at=datetime(date_from.year, 9, 1 + i, 15, tzinfo=UTC),
                match_date=date(date_from.year, 9, 1 + i).isoformat(),
                status="finished",
                home_score=1,
                away_score=0,
                observed_at=now,
                license=self.license,
            )
            for i, (home, away) in enumerate(self.seasons[date_from.year])
        ]
        return FetchOutcome(
            records, [(f"synthetic://{date_from.year}", json.dumps(str(records)).encode())], 1
        )


def test_history_backfill_accepts_promoted_clubs_only_from_the_naming_source(store, tmp_path):
    seasons = {
        2021: [("Klub A", "Klub B"), ("Klub C", "Klub D")],
        2022: [("Klub A", "Klub Awansowany"), ("Klub B", "Klub C")],  # promoted club
    }
    owner = _History("pitchapi", "pitchapi", seasons)
    windows = [(date(y, 7, 1), date(y + 1, 7, 1)) for y in seasons]
    now = datetime(2026, 11, 1, tzinfo=UTC)
    report = backfill_results(
        store, owner, "en.2", windows, now=now, normalizer=NAMES, aliases={}, raw_dir=tmp_path
    )
    assert report["inserted"] == 4 and report["unmatched"] == []
    files = list((tmp_path / "pitchapi").glob("*.json"))
    assert len(files) == 2  # one raw file per season, never overwritten within a second
    other = _History("api_sports_football", "api_sports", {2023: [("Klub A", "Klub Nieznany")]})
    rejected = backfill_results(
        store,
        other,
        "en.2",
        [(date(2023, 7, 1), date(2024, 7, 1))],
        now=now,
        normalizer=NAMES,
        aliases={},
        raw_dir=tmp_path,
    )
    assert rejected["inserted"] == 0 and rejected["unmatched"][0]["source"] == "Klub Nieznany"
    reviewed = backfill_results(
        store,
        other,
        "en.2",
        [(date(2023, 7, 1), date(2024, 7, 1))],
        now=now,
        normalizer=NAMES,
        aliases={},
        raw_dir=tmp_path,
        accept_new_clubs=True,
    )
    assert reviewed["inserted"] == 1
    rows = effective_results(store, sport="football", until=now, competitions=("en.2",))
    assert {r["available_at_basis"] for r in rows} == {"assumed_lag_36h"}


# ---------------------------------------------------------------- validation helpers


def test_league_gate_needs_samples_and_skill_and_only_reports_ranges():
    good = [(0.7, 1)] * 230 + [(0.7, 0)] * 100 + [(0.3, 0)] * 230 + [(0.3, 1)] * 100
    gate = league_gate(good, 2, 0.69, min_n=300)
    assert gate["ok"] and gate["n"] == 330
    assert not league_gate(good[:200], 2, 0.69, min_n=300)["ok"]
    assert not league_gate(good, 2, 0.50, min_n=300)["ok"]  # not better than base rates
    overconfident = [(0.9, 1)] * 250 + [(0.9, 0)] * 150 + [(0.2, 0)] * 300
    result = league_gate(overconfident, 2, 0.69, min_n=300)
    assert result["overconfident_ranges"] and "nadmierna" not in " ".join(result["reasons"])


def test_pair_dependence_is_one_for_independent_matches_and_shows_coupling():
    import random

    rng = random.Random(4)
    picks = []
    for d in range(400):
        for comp in ("L1", "L1", "L2"):
            picks.append((f"d{d}", comp, 0.6, int(rng.random() < 0.6)))
    independent = pair_dependence(picks, same_league=True)
    assert independent["pairs"] == 400 and 0.85 < independent["ratio"] < 1.15
    coupled = []
    for d in range(400):
        hit = int(rng.random() < 0.6)
        coupled += [(f"d{d}", "L1", 0.6, hit), (f"d{d}", "L1", 0.6, hit)]
    assert pair_dependence(coupled, same_league=True)["ratio"] > 1.4


# ---------------------------------------------------------------- post-match review


def test_post_match_review_keeps_the_pre_match_forecast_and_explains_the_result(store, import_sts):
    event = store.rows("SELECT * FROM events WHERE home LIKE 'Real Madrid%'")[0]
    start = datetime.fromisoformat(event["start_at"])
    version = _version(store, "elo_ordered_draw", "old", start - timedelta(days=1), ("es.1",))
    save_prediction(
        store,
        model_version_id=version,
        event_id=event["id"],
        market_key="1x2",
        period="FT",
        line=None,
        probabilities={"home": 0.5, "draw": 0.28, "away": 0.22},
        uncertainty={},
        inputs={
            "info_cutoff": (start - timedelta(hours=4)).isoformat(),
            "data_snapshot": "s",
            "code_state": "c",
            "competition": "es.1",
        },
        kind="prospective",
        created_at=start - timedelta(hours=3),
    )
    before = services.match_review(
        store, event_id=event["id"], candidates=[], now=start - timedelta(hours=1)
    )
    assert before == {"started": False, "start_at": event["start_at"]}
    record_result(
        store,
        event_id=event["id"],
        status="completed",
        now=start + timedelta(hours=3),
        home_score=1,
        away_score=2,
        source="manual",
        verified=True,
    )
    after = services.match_review(
        store, event_id=event["id"], candidates=[], now=start + timedelta(hours=4)
    )
    assert after["finished"] and after["favourite"] == "home" and not after["favourite_hit"]
    outcomes = {s["selection"]: s["outcome"] for s in after["selections"]}
    assert outcomes == {"home": "loss", "draw": "loss", "away": "win"}
    assert after["forecast"]["probabilities"]["home"] == 0.5  # the result never edits it
    assert any("składy" in item for item in after["unknown_before"])


def test_form_rows_show_the_match_and_the_teams_own_view(app_paths, store, import_sts):
    from sports_edge.sportsdata import import_results

    import_results(
        store,
        [
            {
                "match_date": "2026-10-20",
                "start_at": datetime(2026, 10, 20, 19, tzinfo=UTC),
                "home": "Real Madrid CF",
                "away": "FC Barcelona",
                "home_score": 1,
                "away_score": 3,
                "available_at": datetime(2026, 10, 22, 7, tzinfo=UTC),
            }
        ],
        file_bytes=b"form-fixture",
        file_name="synthetic.json",
        source_name="SYNTHETIC FIXTURE",
        license_note="dane syntetyczne (test)",
        sport="football",
        competition="es.1",
        season="2026-27",
        now=NOW,
        availability_basis="assumed_lag_36h",
    )
    event = store.rows("SELECT * FROM events WHERE home LIKE 'Real Madrid%'")[0]
    context = services.event_context(store, app_paths, event_id=event["id"], now=NOW)
    away = context["teams"]["away"]["matches"][0]
    assert away["match"] == "Real Madrid CF 1:3 FC Barcelona"  # as played, home first
    assert away["team_score"] == "3:1" and away["result"] == "W"  # Barcelona's own view
    home = context["teams"]["home"]["matches"][0]
    assert home["team_score"] == "1:3" and home["result"] == "P"


# ---------------------------------------------------------------- multi-day analysis


def test_each_analysed_day_gets_its_own_coupons_and_shared_steps_run_once(
    app_paths, sts_fixture_pdf
):
    store = services.open_store(app_paths)
    settings = AppSettings()
    services.import_pdf_bytes(
        store, app_paths, settings, sts_fixture_pdf, file_name="f.pdf", now=NOW
    )
    seen = []
    run = services.run_analysis(
        store,
        app_paths,
        settings,
        ["2026-10-31", "2026-11-01"],
        now=NOW,
        refresh_results=False,
        progress=lambda index, name, detail: seen.append(index),
    )
    assert set(run.runs) == {"2026-10-31", "2026-11-01"}
    assert seen.count(0) == 1 and seen.count(7) == 2  # shared once, ranking per day
    for day, one in run.runs.items():
        assert one.day == day and one.offer["day"] == day
        assert [s["stage"] for s in one.stages] == list(services.STAGES)
        for proposal in one.coupons.proposals.values():
            if proposal:
                assert {leg.start_at[:10] for leg in proposal.legs} <= {day}
    analyses = store.rows("SELECT analysis_date FROM analyses")
    assert sorted(a["analysis_date"] for a in analyses) == ["2026-10-31", "2026-11-01"]


# ---------------------------------------------------------------- interface scenarios


@pytest.mark.parametrize("page", ["kupon", "dashboard", "analiza", "modele"])
def test_pages_explain_a_day_without_models_or_fresh_prices(app_paths, page):
    """Scenarios 2, 3, 9: offer present, no model, stale prices — pages explain, never crash."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    loader = AppTest.from_string(
        "from sports_edge.app import common as c; c.load_demo_data()", default_timeout=60
    )
    loader.session_state["demo_mode"] = True
    loader.run()
    scripts = {
        "kupon": "from sports_edge.app.page_coupon import coupon_page; coupon_page()",
        "dashboard": "from sports_edge.app.page_dashboard import dashboard_page; dashboard_page()",
        "analiza": "from sports_edge.app.page_match import match_page; match_page()",
        "modele": "from sports_edge.app.page_models import models_page; models_page()",
    }
    app = AppTest.from_string(scripts[page], default_timeout=120)
    app.session_state["demo_mode"] = True
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    if page == "kupon":
        app.segmented_control[0].set_value("7 dni").run()  # scenario 10
        app.button[0].click().run()
        assert not app.exception, [e.value for e in app.exception]
        text = " ".join(m.value for m in app.markdown)
        assert "Brak kuponu" in text  # demo teams have no model: the reason is shown
    if page == "modele":
        app.segmented_control[0].set_value("Pokrycie oferty").run()
        assert not app.exception, [e.value for e in app.exception]


def test_rules_profiles_keep_separate_thresholds():
    rules = policy()
    assert [
        rules.profiles[n]["min_leg_probability"] for n in ("safe", "balanced", "aggressive")
    ] == [
        0.75,
        0.65,
        0.55,
    ]
    assert RULES["min_holdout_n"] == 300
    payout = build_with([cand("A", "1.30", 0.83, 0.80)], rules).proposals["safe"]
    assert payout.payout == Decimal("11.44") and payout.level == VALIDATED
