"""TASK 014 — hit rate of the coupons: expected against actual hits, a safer coupon measured
and offered as an option (off by default), fewer shared losses, and the data for the user's
decision on WYSOKA TRAFNOŚĆ (lower-bound coverage, team totals calibrated in new versions).

Every team, price, forecast, coupon and result below is SYNTHETIC test data, not a real fact.
"""

import itertools
import json
import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from conftest import ROOT
from test_models_data import load, synthetic
from test_task007 import _leg, _pool
from test_task010 import AT8, AT14, _import, _odds, _save
from test_task011 import _event
from test_task011 import _import as _import_doc
from test_task013 import _forecast_world, _prediction

from sports_edge import automation, performance, rolling, services, simulation
from sports_edge import my_coupon as mine
from sports_edge.analysis.coupon_builder import VALIDATED, build_coupons
from sports_edge.analysis.edge_engine import derive_from_1x2
from sports_edge.analysis.policy import Policy
from sports_edge.ingestion.types import iso
from sports_edge.probability_lab import (
    calibrated_market,
    load_probability_policy,
    team_total_family,
    team_total_map,
)
from sports_edge.settings import AppSettings, load_settings

POLICY_FILE = ROOT / "config" / "probability_policy.toml"
RULES = load_probability_policy(POLICY_FILE)
DAY = "2026-10-31"


def _cost():
    from sports_edge.bookmakers import load_profiles

    return load_profiles(ROOT / "config")["sts"].cost


def _exact(probabilities):
    """Brute force over every outcome: P(exactly k)."""
    out = [0.0] * (len(probabilities) + 1)
    for outcome in itertools.product((0, 1), repeat=len(probabilities)):
        out[sum(outcome)] += math.prod(
            p if y else 1 - p for p, y in zip(probabilities, outcome, strict=True)
        )
    return out


# 1 ---------------------------------------------------------------- Poisson-binomial (§2.1)


@pytest.mark.parametrize(
    "probabilities", [[0.5], [0.2, 0.9], [0.79, 0.21, 0.55, 0.66], [0.1, 0.3, 0.5, 0.7, 0.95]]
)
def test_the_poisson_binomial_equals_the_exact_distribution(probabilities):
    found = performance.poisson_binomial(probabilities)
    assert found == pytest.approx(_exact(probabilities), abs=1e-12)
    assert sum(found) == pytest.approx(1.0)
    p_le, p_ge = performance.tails(probabilities, 2)
    exact = _exact(probabilities)
    assert p_le == pytest.approx(sum(exact[:3]))
    assert p_ge == pytest.approx(sum(exact[2:]))


# synthetic chances shaped like §1.1: from 0.21 to 0.79, typically 0.45–0.66, sum 6.94
CHANCES_11 = [0.79, 0.66, 0.66, 0.57, 0.55, 0.51, 0.48, 0.47, 0.45, 0.45, 0.42, 0.40, 0.31, 0.22]


def _coupon(profile, outcome, p, low=None, n=1, start="2026-11-02T17:00:00+00:00", label=None):
    return {
        "id": f"{profile}-{n}",
        "profile": profile,
        "outcome": outcome,
        "first_start_at": start,
        "proposal": {
            "label": label or profile.upper(),
            "probability": {"calibrated": p, "conservative": low},
            "legs": [],
        },
    }


def test_seven_of_fifteen_with_the_chances_of_the_diagnosis_is_about_062(monkeypatch):
    assert sum(CHANCES_11) == pytest.approx(6.94)
    coupons = [
        _coupon("standard", "win" if i < 7 else "loss", p, p - 0.07, n=i)
        for i, p in enumerate(CHANCES_11)
    ]
    coupons.append(_coupon("my_coupon", "loss", None, n=99, label="MÓJ KUPON"))  # no chance
    monkeypatch.setattr(performance, "builder_coupons", lambda store: coupons)
    found = performance.expected_vs_actual(None)
    total = found["total"]
    assert (total["n"], total["hits"]) == (14, 7)  # the coupon without a chance is apart
    assert total["expected"] == pytest.approx(6.94)
    assert total["expected_conservative"] == pytest.approx(6.94 - 14 * 0.07)
    assert total["p_le"] == pytest.approx(0.62, abs=0.03)
    assert total["verdict"] == "consistent"
    assert total["sentence"].startswith("zgodne z modelem (taki lub lepszy wynik zdarza się w")
    assert found["without_model"] == [{"label": "MÓJ KUPON", "settled": 1, "wins": 0}]
    assert [r["label"] for r in found["rows"]] == ["STANDARD"]


def test_the_marks_below_5_and_above_95_percent():
    assert performance.verdict(0.04, 0.99) == "worse"
    assert performance.verdict(0.99, 0.04) == "better"  # P(X < actual) > 95%
    assert performance.verdict(0.05, 0.96) == "consistent"
    bad = performance.judge([0.9] * 10, 5)
    assert bad["verdict"] == "worse" and "gorzej niż przewiduje model" in bad["sentence"]
    assert "tylko w" in bad["sentence"]
    good = performance.judge([0.3] * 10, 9)
    assert good["verdict"] == "better" and good["sentence"].startswith("lepiej niż przewiduje")
    one = performance.judge([0.79], 1)  # a single hit is never "better" (P(≥1) = 0.79)
    assert one["verdict"] == "consistent"
    assert performance.judge([], 0)["sentence"].startswith("brak danych")
    # legs counted on matches may be fractional: the tails round towards the middle
    p_le, p_ge = performance.tails([0.8, 0.8], 1.5)
    assert p_le == pytest.approx(1.0) and p_ge == pytest.approx(1 - 0.2 * 0.2)


def test_the_panel_counts_only_coupons_of_the_statistics(store, app_paths, profiles):
    from sports_edge import paper

    first, second = _import(store, "a", AT8), _import(store, "b", AT14, {"4101": "1.95"})
    official = _save(store, app_paths, [_odds(store, first, "4101")], AT8)
    update = _save(store, app_paths, [_odds(store, second, "4101")], AT14)  # a price update
    again = _save(store, app_paths, [_odds(store, second, "4101")], AT14 + timedelta(hours=1))
    assert official["relation"] == "official" and update["relation"] == "price_update"
    assert again["relation"] == "duplicate"
    event = store.rows(
        """SELECT e.id, e.start_at FROM coupon_legs l JOIN selections s ON s.id=l.selection_id
        JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id
        WHERE l.coupon_id=?""",
        (official["coupon_id"],),
    )[0]
    after = datetime.fromisoformat(event["start_at"]) + timedelta(hours=3)
    paper.record_result(
        store, event_id=event["id"], status="completed", now=after, home_score=2, away_score=0
    )
    for saved in (official, update, again):
        paper.settle_coupon(store, saved["coupon_id"], profiles, now=after)
    found = performance.expected_vs_actual(store)
    assert found["total"]["n"] == 1 and found["total"]["hits"] == 1
    legs = performance.legs_expected_vs_actual(store)
    assert legs["total"]["matches"] == 1 and legs["total"]["legs"] == 1
    assert {r["label"] for r in legs["rows"]} == {"1X2"}
    line = performance.recent_line(store, now=after, days=30)
    assert line.startswith("Ostatnie 30 dni: 1 z 1 trafionych, model oczekiwał 0,4")
    assert performance.recent_line(store, now=after + timedelta(days=60)).endswith(
        "brak rozliczonych kuponów z szansą modelu."
    )


def test_the_leg_table_counts_one_match_as_one_observation():
    rows = [
        {"selection_id": "a", "match": "M1", "market_group": "Gole: powyżej", "p_model": 0.8,
         "outcome": "win"},
        {"selection_id": "b", "match": "M1", "market_group": "Gole: powyżej", "p_model": 0.7,
         "outcome": "loss"},
        {"selection_id": "c", "match": "M2", "market_group": "Podwójna szansa", "p_model": 0.75,
         "outcome": "win"},
        {"selection_id": "d", "match": "M3", "market_group": "Podwójna szansa", "p_model": None,
         "outcome": "loss"},
    ]  # fmt: skip
    found = performance.legs_expected_vs_actual(None, rows)
    over = next(r for r in found["rows"] if r["label"] == "Gole: powyżej")
    assert (over["legs"], over["matches"], over["hits"]) == (2, 1, 0.5)
    assert over["expected"] == pytest.approx(0.75)
    assert found["total"]["matches"] == 2 and found["without_model"] == 1
    assert performance.market_group("total", "under") == "Gole: poniżej"


# 2 ---------------------------------------------------------------- the variant replay (§3.1)


def test_the_variant_replay_uses_the_same_decisions_no_later_data_and_saves_nothing(
    store, import_sts, app_paths
):
    _row, version, _pid = _forecast_world(store, import_sts, app_paths)
    settings = load_settings(app_paths)
    tables = ("coupons", "predictions", "analyses", "coupon_lineage", "simulation_runs")
    before = {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in tables}

    def replay(hours=(14, 15)):
        return simulation.variant_replay(
            store, app_paths, settings, [DAY], hours=hours, versions_at=lambda t: {version}
        )

    first = replay()
    assert len(first["decisions"]) == 2 and first["days"] == [DAY]
    for name, entry in first["variants"].items():
        assert entry["decisions"] == 2 and entry["days"] == 1, name
        assert entry["days_with_coupon"] + entry["days_without_coupon"] == 1
    assert {"current", "current_limit1", "solo_70", "solo_75", "two_60", "high_hit_d"} <= set(
        first["variants"]
    )
    assert "brak challengera v3" in first["variants"]["solo_75_shadow"]["note"]
    assert {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in tables} == before
    # a forecast made AFTER the 14:00 decision (at 14:30 Warsaw) changes nothing in it ...
    early = replay(hours=(14,))
    later = datetime(2026, 10, 31, 13, 30, tzinfo=UTC)
    _prediction(store, version, _row["event_id"], later, 0.97)
    assert replay(hours=(14,))["variants"] == early["variants"]
    # ... while a decision after it sees it (control)
    seen = services.day_candidates_at(
        store, app_paths, settings, DAY, now=later + timedelta(minutes=5), versions={version}
    )[0]
    assert any(c.p_model == pytest.approx(0.97) for c in seen if c.event_id == _row["event_id"])
    unseen = services.day_candidates_at(
        store, app_paths, settings, DAY, now=later - timedelta(minutes=25), versions={version}
    )[0]
    assert not any(c.p_model == pytest.approx(0.97) for c in unseen)


def test_the_variant_summary_counts_the_first_coupon_of_a_profile_and_day():
    def item(decision, status, p=0.8, profile="solo_75", day=DAY, sel="s1"):
        return {
            "day": day,
            "decision_at": decision,
            "profile": profile,
            "p_model": p,
            "p_conservative": p - 0.05,
            "total_odds": Decimal("1.20"),
            "payout": Decimal("10.56"),
            "net_on_win": Decimal("0.56"),
            "level": VALIDATED,
            "legs": [{"selection_id": sel, "p_model": p, "validated": True, "outcome": status}],
            "status": status,
            "net_profit": Decimal("0.56") if status == "win" else Decimal("-10.00"),
        }

    coupons = [
        item("2026-10-31T09:00:00.000000+00:00", "win"),
        item("2026-10-31T13:00:00.000000+00:00", "loss", sel="s2"),  # a later version
        item("2026-11-01T09:00:00.000000+00:00", "loss", day="2026-11-01", sel="s3"),
    ]
    found = simulation._variant_summary(
        coupons, days=[DAY, "2026-11-01", "2026-11-02"], decisions=9
    )
    assert (found["coupons"], found["days_with_coupon"], found["days_without_coupon"]) == (2, 2, 1)
    assert (found["settled"], found["wins"], found["legs_settled"]) == (2, 1, 2)
    assert found["net_profit_settled"] == "-9.44" and found["validated_coupons"] == 2
    shared = simulation._shared_losses(
        [item("a", "loss", profile="standard"), item("b", "loss", profile="higher_payout")]
    )
    assert shared == {"days_shared_selection": 1, "days_one_lost_leg_2plus": 1}


# 3 ---------------------------------------------------------------- the new profile (§3.2)


def test_the_new_profile_is_off_by_default_and_changes_no_proposal():
    assert list(RULES.profiles) == ["high_hit", "standard", "higher_payout", "tasiemka"]
    assert list(RULES.optional) == ["safe_solo"] and RULES.optional["safe_solo"]["enabled"] is False
    assert RULES.version == "2026-11-07.p4"
    off = services.coupon_rules(SimpleNamespace(config=ROOT / "config"), AppSettings())
    assert list(off.profiles) == list(RULES.profiles) and AppSettings().enabled_profiles == []
    pool = _pool(20, seed=11)
    plain = build_coupons(pool, RULES, cost=_cost(), on_date=date(2026, 10, 31))
    default = build_coupons(pool, off, cost=_cost(), on_date=date(2026, 10, 31))
    assert set(default.proposals) == set(plain.proposals) == set(RULES.profiles)
    assert default.proposals == plain.proposals


@pytest.mark.parametrize("limit", [0, 1])
def test_switched_on_it_respects_its_thresholds_and_the_four_profiles_stay_identical(limit):
    on = RULES.with_profiles(["safe_solo"])
    assert list(on.profiles)[-1] == "safe_solo"  # built after the four
    pool = _pool(20, seed=11)
    before = build_coupons(
        pool, RULES, cost=_cost(), on_date=date(2026, 10, 31), max_profiles_per_selection=limit
    )
    after = build_coupons(
        pool, on, cost=_cost(), on_date=date(2026, 10, 31), max_profiles_per_selection=limit
    )
    for name in RULES.profiles:  # regression: identical proposals of the existing profiles
        assert after.proposals[name] == before.proposals[name], name
    solo = after.proposals["safe_solo"]
    assert solo is not None and len(solo.legs) == 1
    assert solo.p_conservative >= 0.75 and solo.total_odds >= Decimal("1.15")
    assert solo.net_profit > 0 and solo.level == VALIDATED
    # predictive validation is required: experimental selections give no coupon
    experimental = [replace(c, predictive_status="experimental") for c in pool]
    none = build_coupons(experimental, on, cost=_cost(), on_date=date(2026, 10, 31))
    assert none.proposals["safe_solo"] is None
    assert none.refusal_codes["safe_solo"] == "not_validated"
    weak = [replace(c, p_low=min(c.p_low, 0.74)) for c in pool]  # cautious < 0.75 everywhere
    assert (
        build_coupons(weak, on, cost=_cost(), on_date=date(2026, 10, 31)).proposals["safe_solo"]
        is None
    )


def test_the_profile_card_says_how_many_of_ten_lose():
    from sports_edge import insights as ins
    from sports_edge.app import coupon_view

    payload = {"probability": {"calibrated": 0.78, "conservative": 0.72}, "payout": "11.44"}
    text = coupon_view.loss_text(payload)
    assert text == (
        "Szansa 78% (ostrożnie 72%) — statystycznie przegrywa 2,2 z 10 takich kuponów "
        "(ostrożnie 2,8); wypłata 11,44 zł"
    )
    assert ins.losses_of_ten(0.8) == "2"
    assert "Szansa nieznana" in coupon_view.loss_text({"probability": {}, "payout": None})


# 4 ---------------------------------------------------------------- shared losses (§4)


def test_the_risk_link_appears_only_for_the_same_selection_in_two_coupons():
    from sports_edge.app.page_coupon import same_leg_shared

    same = {
        "E1": [
            ("standard", "S", ("1x2", None, "home")),
            ("higher_payout", "W", ("1x2", None, "home")),
        ]
    }
    other = {
        "E1": [
            ("standard", "S", ("1x2", None, "home")),
            ("higher_payout", "W", ("btts", None, "yes")),
        ]
    }
    assert same_leg_shared(same) and not same_leg_shared(other)
    assert not same_leg_shared({})


def test_my_coupon_warns_about_a_leg_of_an_official_coupon_and_names_the_weakest():
    home = _leg("A", "1x2", "home", "1.60", p=0.70, low=0.64)
    over = _leg("B", "total", "over", "1.30", p=0.80, low=0.74, line="1.5")
    btts = _leg("C", "btts", "yes", "1.70", p=0.62, low=0.55)
    under = _leg("D", "total", "under", "1.50", p=0.70, low=0.66, line="3.5")
    now = datetime(2026, 10, 31, 9, tzinfo=UTC)
    cart = []
    for row in (home, over, btts, under):
        cart, why = mine.add(cart, [home, over, btts, under], row, day=DAY, now=now)
        assert why is None
    official = {
        DAY: {
            "standard": {"label": "STANDARDOWY", "legs": [{"selection_id": over.selection_id}]},
            "higher_payout": {"label": "WYŻSZA WYPŁATA", "legs": [{"odds_id": "other"}]},
        }
    }
    warnings = mine.shared_with_official(cart, official)
    assert len(warnings) == 1
    assert "ta noga jest też w STANDARDOWY — jej porażka przegra oba" in warnings[0]
    rows = [home, over, btts, under]
    assert mine.weakest_leg(rows) == 2  # the lowest cautious chance (0.55)
    assert mine.weakest_leg([None, home]) == 1
    summary = mine.summary(cart, rows, stake=Decimal("10.00"), cost=_cost(), on_date=now.date())
    note = mine.chance_note(summary)
    assert note.startswith("Szansa ") and "przegrywa średnio" in note and " z 10" in note
    three = mine.summary(
        cart[:3], rows[:3], stake=Decimal("10.00"), cost=_cost(), on_date=now.date()
    )
    assert mine.chance_note(three) is None  # only from 4 legs on


def test_shared_losses_on_settled_coupons(monkeypatch):
    legs = {"x": "loss", "y": "win", "z": "loss"}
    monkeypatch.setattr(
        performance,
        "selection_outcomes",
        lambda store: [
            {"selection_id": k, "outcome": v, "match_start": "2026-11-07T14:00:00+00:00"}
            for k, v in legs.items()
        ],
    )

    def coupon(n, outcome, picks):
        return {
            "id": f"c{n}",
            "outcome": outcome,
            "first_start_at": "2026-11-07T13:00:00+00:00",
            "proposal": {"legs": [{"selection_id": p, "odds_id": p} for p in picks]},
        }

    monkeypatch.setattr(
        performance,
        "builder_coupons",
        lambda store: [coupon(1, "loss", "xy"), coupon(2, "loss", "x"), coupon(3, "loss", "zy")],
    )
    found = performance.shared_losses(None)
    assert found["days_one_leg_2plus"] == 1 and found["settled_days"] == 1
    assert found["coupons_lost_by_shared_leg"] == 2 and found["lost_selections_2plus"] == 1
    assert found["lost_coupons_by_lost_legs"] == {1: 3}


# 5 ---------------------------------------------------------------- odds age and the cap (§5.1)


def test_a_merged_slot_keeps_the_lead_of_its_own_group_so_the_cap_keeps_it(store):
    imported = _import_doc(store, "sunday", datetime(2026, 11, 8, 6, tzinfo=UTC))
    utc = lambda h, m: datetime(2026, 11, 8, h, m, tzinfo=UTC)  # noqa: E731
    # groups at 11:15 and 12:30 Warsaw: 45 min before the first = 120 min before the second
    _event(store, 700, utc(10, 15), imported)
    _event(store, 701, utc(11, 30), imported)
    settings = automation.load_settings(store)
    plan = automation.matchday_plan(store, settings, now=utc(6, 0))
    merged = next(s for s in plan if s["at"] == utc(9, 30))
    assert merged["group"] == utc(10, 15) and merged["lead"] == 45
    capped = json.loads(json.dumps(settings))
    capped["jobs"]["sts_offer"]["matchday"]["daily_cap"] = 2
    kept = automation.matchday_plan(store, capped, now=utc(6, 0))
    assert {s["at"] for s in kept} == {utc(9, 30), utc(10, 45)}  # both 45-min downloads
    effect = automation.matchday_cap_effect(store, capped, day=date(2026, 11, 8))
    assert effect["groups"] == 2 and effect["groups_losing_closest"] == 0
    assert effect["slots_uncapped"] == 3 and effect["slots_capped"] == 2


# 6 ---------------------------------------------------- lower bounds (§5.2), team totals (§5.4)


@pytest.fixture(scope="module")
def world():
    from sports_edge.repository import ensure_reference_data
    from sports_edge.storage import Store

    def make(matches):
        store = Store(":memory:")
        ensure_reference_data(store)
        load(store, matches)
        return store

    base = synthetic(1800)
    stores = {"base": make(base)}
    end = datetime(2024, 7, 1, tzinfo=UTC)
    stores["later"] = make(
        [
            replace(m, home_score=m.away_score, away_score=m.home_score + 2)
            if m.available_at >= end
            else m
            for m in base
        ]
    )
    yield stores
    for store in stores.values():
        store.close()


SMALL = rolling.RollingPolicy(min_window_matches=60, min_fit_matches=60)
T0 = datetime(2024, 1, 1, tzinfo=UTC)
JUNE = datetime(2024, 6, 1, tzinfo=UTC)


def _champions(store):
    """SYNTHETIC frozen champions trained at T0 (protocol v3 reports, never registered)."""
    elo = rolling.run_elo_v3(
        store, competitions=("es.1",), now=T0, policy=Policy(), register=False, rolling=SMALL
    )
    goals = rolling.run_dc_v3(
        store,
        competitions=("es.1",),
        now=T0,
        policy=Policy(),
        probability_policy=RULES,
        register=False,
        rolling=SMALL,
        previous_half_life=480.0,
    )
    return (
        {"version": "elo-synthetic", "validation_json": json.dumps(elo, default=str)},
        {"version": "dc-synthetic", "validation_json": json.dumps(goals, default=str)},
        goals,
    )


def _walk(store, champions):
    return rolling.walk_forward(
        store,
        "core",
        now=datetime(2024, 7, 1, tzinfo=UTC),
        competitions=("es.1",),
        champion_elo=champions[0],
        champion_goals=champions[1],
        rolling=SMALL,
        months=[JUNE],
    )


def test_the_lower_bound_coverage_is_counted_on_matches_as_of(world):
    champions = _champions(world["base"])
    base = _walk(world["base"], champions)
    rows = base["bound_rows"]
    assert rows and {r["definition"] for r in rows} == set(rolling.BOUND_DEFINITIONS)
    assert all(r["match"] in {m["match"] for m in base["rows"]} for r in rows)
    assert {r["role"] for r in rows} == {"v2", "v3"}
    # option D is never below the served definition and never above the model chance
    by_key = {(r["role"], r["match"], r["selection"], r["definition"]): r for r in rows}
    for (role, match, selection, definition), row in by_key.items():
        if definition == "elo_dc_sum":
            one = by_key[(role, match, selection, "elo_dc_one")]
            assert row["q"] <= one["q"] + 1e-12 and one["q"] <= one["p"] + 1e-12
    table = base["bounds"]
    assert table and all(c["matches"] <= base["bound_matches"] for c in table)
    zone = [c for c in table if c["zone_75"]]
    assert all(c["range"] == "75–100%" for c in zone)
    for cell in table:
        assert cell["ci95"][0] <= cell["freq"] <= cell["ci95"][1]
        assert cell["nominal"] and cell["verdict"]
    # results known only after the month change no bound of the month (no leakage)
    later = _walk(world["later"], _champions(world["later"]))

    def bounds(found):
        return sorted(
            (r["role"], r["match"], r["definition"], r["selection"], round(r["q"], 12))
            for r in found["bound_rows"]
        )

    assert bounds(later) == bounds(base)


def test_the_production_definition_of_the_cautious_chance_is_unchanged():
    prediction = {
        "probabilities": {"home": 0.55, "draw": 0.27, "away": 0.18},
        "uncertainty": {
            "interval": {"home": [0.50, 0.60], "draw": [0.23, 0.31], "away": [0.15, 0.21]}
        },
    }
    derived = derive_from_1x2(prediction, "double_chance")
    assert derived["uncertainty"]["interval"]["home_draw"][0] == pytest.approx(0.50 + 0.23)
    profile = RULES.profiles["high_hit"]
    assert (profile["min_leg_probability"], profile["min_coupon_probability"]) == (0.75, 0.65)
    assert RULES.validation["min_holdout_n"] == 300 and RULES.validation["max_ece"] == 0.03


def test_new_versions_calibrate_team_totals_and_old_ones_stay_raw(world, store):
    _elo, _goals, report = _champions(world["base"])
    maps = report["team_total_calibrators"]
    assert set(maps) == {
        f"{side}_{line}"
        for side in ("team_total_home", "team_total_away")
        for line in (0.5, 1.5, 2.5)
    }
    assert set(report["calibrators"]) == set(rolling.CALIBRATED_FAMILIES)  # others unchanged
    assert team_total_family("home_to_score", None) == "team_total_home_0.5"
    assert team_total_map(report, "team_total_away", "1.5") == maps["team_total_away_1.5"]
    probs, interval = calibrated_market({"over": 0.66, "under": 0.34}, maps["team_total_home_1.5"])
    assert interval["over"][0] <= probs["over"] <= interval["over"][1]
    # an older version (or a champion) has no map: its team totals stay raw, no interval
    old = {"protocol": "v2", "calibrators": report["calibrators"]}
    assert team_total_map(old, "team_total_home", "1.5") is None
    raw, none = calibrated_market({"over": 0.66, "under": 0.34}, None)
    assert raw["over"] == 0.66 and raw["under"] == pytest.approx(0.34) and none == {}
    # a stored champion's validation_json is never touched by the new training
    from test_task011 import _version

    champion = _version(store, "dixon_coles_goals", "dc-v2-champion", validation=old)
    text = store.get("model_versions", champion)["validation_json"]
    rolling.run_dc_v3(
        world["base"],
        competitions=("es.1",),
        now=T0,
        policy=Policy(),
        probability_policy=RULES,
        register=False,
        rolling=SMALL,
        previous_half_life=480.0,
    )
    assert store.get("model_versions", champion)["validation_json"] == text


def test_the_walk_forward_measures_team_totals_before_and_after(world):
    found = _walk(world["base"], _champions(world["base"]))
    roles = {r["role"] for r in found["team_totals"]}
    assert roles <= {"v2", "v3", rolling.RAW_TEAM_TOTALS} and rolling.RAW_TEAM_TOTALS in roles
    raw = [r for r in found["rows"] if r["role"] == rolling.RAW_TEAM_TOTALS]
    assert raw and {r["market_key"] for r in raw} == {"team_total_home", "team_total_away"}
    calibrated = {
        (r["match"], r["market"]): r["probabilities"]
        for r in found["rows"]
        if r["role"] == "v3" and r["market_key"].startswith("team_total")
    }
    assert any(calibrated[(r["match"], r["market"])] != r["probabilities"] for r in raw)


# 7 ---------------------------------------------------------------- interface (§6)


def test_history_shows_expected_against_actual_and_settings_show_the_new_profile(app_paths):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    history = AppTest.from_string(
        "from sports_edge.app.pages_history import history_page; history_page()",
        default_timeout=90,
    )
    history.session_state["demo_mode"] = False
    history.run()
    assert not history.exception, history.exception
    text = " ".join(m.value for m in history.markdown) + " ".join(
        str(x.value) for x in [*history.caption, *history.info]
    )
    assert "Oczekiwane kontra faktyczne" in text
    settings = AppTest.from_string(
        "from sports_edge.app.page_sources import sources_page; sources_page()",
        default_timeout=90,
    )
    settings.session_state["demo_mode"] = False
    settings.query_params["widok"] = "Ustawienia"  # the direct link of Ryzyko dnia
    settings.run()
    assert not settings.exception, settings.exception
    toggle = next(t for t in settings.toggle if t.label.startswith("Profil PEWNY SOLO"))
    assert toggle.value is False
    toggle.set_value(True).run()
    next(b for b in settings.button if b.label == "Zapisz ustawienia").click().run()
    assert load_settings(app_paths).enabled_profiles == ["safe_solo"]
    assert list(services.coupon_rules(app_paths, load_settings(app_paths)).profiles)[-1] == (
        "safe_solo"
    )


def test_the_new_settings_field_reads_old_files(app_paths):
    app_paths.data.mkdir(parents=True, exist_ok=True)
    app_paths.settings_file.write_text(json.dumps({"offline_mode": True}), encoding="utf-8")
    assert load_settings(app_paths).enabled_profiles == []
    assert iso(datetime(2026, 11, 7, tzinfo=UTC)).startswith("2026-11-07")
