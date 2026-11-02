"""TASK 007: correctness of probabilities (P0) and the new coupon tools.

Every probability below is a synthetic test input, not a forecast of a real match.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_models_data import load, synthetic
from test_task005 import cand

from sports_edge import advisor, collection, learning, llm, services
from sports_edge import explainer as ex
from sports_edge import my_coupon as mine
from sports_edge import probability_lab as lab
from sports_edge import simulation as sim
from sports_edge.analysis.coupon_builder import JOINT_BASIS, eligibility
from sports_edge.analysis.edge_engine import (
    consistent_interval,
    derive_from_1x2,
    evaluate,
    market_problem,
)
from sports_edge.analysis.policy import Policy, load_policy
from sports_edge.bookmakers import load_profiles
from sports_edge.insights import best_pick, candidate_groups
from sports_edge.models import calibration
from sports_edge.models.registry import (
    latest_predictions,
    register_version,
    save_prediction,
    validation_status,
)
from sports_edge.probability_lab import (
    INTERVAL_METHOD,
    calibrated_market,
    derived_double_chance,
)
from sports_edge.repository import ensure_reference_data, offer_table
from sports_edge.sportsdata import (
    Split,
    _forecasts,
    _scoped,
    elo_parameters,
    history,
    run_elo_experiment,
)
from sports_edge.storage import Store

pytest.importorskip("pymupdf")

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
INPUTS = {
    "info_cutoff": "2026-10-30T00:00:00+00:00",
    "data_snapshot": "synthetic-test",
    "code_state": "test",
}
VALIDATION = {
    "holdout": {"n": 500, "ece": 0.01, "log_loss": 0.95},
    "leakage_checked": True,
}


def _blocks(seed: int = 7):
    """Isotonic map of a SYNTHETIC, slightly overconfident forecaster."""
    import random

    rng = random.Random(seed)
    p, y = [], []
    for _ in range(3000):
        q = rng.uniform(0.02, 0.95)
        p.append(q)
        y.append(int(rng.random() < 0.05 + 0.85 * q))
    return calibration.isotonic_fit(p, y)


# ---------------------------------------------------------------- 4.1 interval consistency


def test_exclusive_market_is_normalised_before_its_intervals():
    blocks = _blocks()
    raw = {"home": 0.18, "draw": 0.21, "away": 0.61}
    final, interval = calibrated_market(raw, blocks)
    assert sum(final.values()) == pytest.approx(1.0, abs=1e-12)
    for sel, p in final.items():
        low, high = interval[sel]
        assert 0.0 <= low <= p <= high <= 1.0, (sel, low, p, high)


def test_binary_market_second_side_is_the_complement():
    final, interval = calibrated_market({"over": 0.71, "under": 0.29}, _blocks())
    assert final["over"] + final["under"] == pytest.approx(1.0, abs=1e-12)
    low, high = interval["over"]
    assert interval["under"] == pytest.approx([1 - high, 1 - low])
    for sel, p in final.items():
        assert consistent_interval(p, *interval[sel])


def test_market_without_calibration_has_no_interval():
    final, interval = calibrated_market({"yes": 0.4, "no": 0.6}, None)
    assert final == {"yes": 0.4, "no": 0.6} and interval == {}


def test_double_chance_is_coherent_with_the_final_1x2():
    final, interval = calibrated_market({"home": 0.5, "draw": 0.27, "away": 0.23}, _blocks())
    dc = derived_double_chance(final, interval, "experimental")
    assert dc["calibrated"]["home_draw"] == pytest.approx(1 - final["away"])
    assert dc["calibrated"]["draw_away"] == pytest.approx(1 - final["home"])
    assert sum(dc["calibrated"].values()) == pytest.approx(2.0)
    for sel, p in dc["calibrated"].items():
        assert consistent_interval(p, *dc["interval"][sel])
    assert dc["method"] == INTERVAL_METHOD


def test_property_random_markets_stay_consistent():
    import random

    rng = random.Random(11)
    blocks = _blocks(3)
    for _ in range(300):
        a, b = sorted(rng.random() for _ in range(2))
        raw = {"home": a, "draw": b - a, "away": 1 - b}
        final, interval = calibrated_market(raw, blocks)
        assert sum(final.values()) == pytest.approx(1.0)
        dc = derived_double_chance(final, interval, "x")
        for values, bounds in ((final, interval), (dc["calibrated"], dc["interval"])):
            for sel, p in values.items():
                assert consistent_interval(p, *bounds[sel])


def test_market_problem_detects_sums_ranges_and_drift():
    ok = {"probabilities": {"home": 0.5, "draw": 0.3, "away": 0.2}}
    assert market_problem(ok, "1x2") is None
    assert "suma" in market_problem({"probabilities": {"over": 0.6, "under": 0.5}}, "total")
    assert "zakresem" in market_problem({"probabilities": {"yes": 1.2, "no": -0.2}}, "btts")
    # pre-TASK 007 Dixon–Coles row: double chance calibrated side by side (drift 5 pp)
    row = {
        "probabilities": {"home": 0.5, "draw": 0.3, "away": 0.2},
        "uncertainty": {
            "derived": {
                "double_chance": {
                    "calibrated": {"home_draw": 0.75, "draw_away": 0.5, "home_away": 0.7},
                    "interval": {},
                }
            }
        },
    }
    derived = derive_from_1x2(row, "double_chance")
    assert derived["coherence_gap"] == pytest.approx(0.05)
    assert "niezgodna z 1X2" in market_problem(derived, "double_chance")


# ---- the stored bug: final P above its own upper bound must never be ranked


@pytest.fixture
def policy():
    return load_policy(ROOT / "config/coupon_policy.toml")


def _version(store, policy):
    version_id, _status, _ = register_version(
        store,
        family="synthetic_test_model",
        sport="football",
        description="test only",
        version="t007",
        created_at=NOW - timedelta(days=1),
        trained_through=NOW - timedelta(days=2),
        training_data={"synthetic": True},
        validation=VALIDATION,
        policy=policy,
    )
    return version_id


def _predict(store, version_id, home, probabilities, uncertainty, market="1x2"):
    event = store.rows("SELECT id FROM events WHERE home=?", (home,))[0]["id"]
    save_prediction(
        store,
        model_version_id=version_id,
        event_id=event,
        market_key=market,
        period="FT",
        line=None,
        probabilities=probabilities,
        uncertainty=uncertainty,
        inputs=INPUTS,
        kind="prospective",
        created_at=NOW - timedelta(minutes=30),
    )


def _candidates(store, policy, profiles, rules):
    return evaluate(
        offer_table(store, day="2026-10-31"),
        predictions=latest_predictions(store, as_of=NOW),
        profile=profiles["sts"],
        market_rules=rules,
        policy=policy,
        now=NOW,
        max_age=timedelta(minutes=180),
        stake=Decimal("10.00"),
    )


def test_inconsistent_stored_interval_is_excluded_everywhere(
    store, import_sts, policy, profiles, rules
):
    version_id = _version(store, policy)
    # the real defect found in the audit: away 75.8% with an upper bound of 75.2%
    _predict(
        store,
        version_id,
        "Legia Warszawa",
        {"home": 0.1121, "draw": 0.13, "away": 0.7579},
        {"interval": {"home": [0.09, 0.13], "draw": [0.11, 0.15], "away": [0.70, 0.7517]}},
    )
    found = _candidates(store, policy, profiles, rules)
    away = next(c for c in found if c.event.startswith("Legia") and c.selection == "away")
    home = next(c for c in found if c.event.startswith("Legia") and c.selection == "home")
    assert not away.probability_ok and away.p_low is None and away.tier == "rejected"
    assert any("Niespójny przedział" in r for r in away.reasons)
    assert eligibility(away, 10.0).startswith("niespójne")
    assert home.probability_ok and home.p_low == pytest.approx(0.09)
    groups = candidate_groups(found, now=NOW, max_odds=10.0)
    assert all(g["candidate"] is not away for g in groups)
    pick = best_pick([away], now=NOW, max_odds=10.0)
    assert pick is None


def test_drifting_double_chance_of_old_rows_is_blocked(store, import_sts, policy, profiles, rules):
    version_id = _version(store, policy)
    _predict(
        store,
        version_id,
        "Legia Warszawa",
        {"home": 0.5, "draw": 0.3, "away": 0.2},
        {
            "interval": {"home": [0.45, 0.55], "draw": [0.25, 0.35], "away": [0.15, 0.25]},
            "derived": {
                "double_chance": {  # sides calibrated separately before TASK 007
                    "calibrated": {"home_draw": 0.75, "draw_away": 0.5, "home_away": 0.7},
                    "interval": {
                        "home_draw": [0.7, 0.8],
                        "draw_away": [0.45, 0.55],
                        "home_away": [0.65, 0.75],
                    },
                    "predictive_status": "experimental",
                }
            },
        },
    )
    found = _candidates(store, policy, profiles, rules)
    dc = [c for c in found if c.event.startswith("Legia") and c.market_key == "double_chance"]
    one_x_two = [c for c in found if c.event.startswith("Legia") and c.market_key == "1x2"]
    assert dc and not any(c.probability_ok for c in dc)
    assert all(any("niezgodna z 1X2" in r for r in c.reasons) for c in dc)
    assert all(c.probability_ok for c in one_x_two)


# ---------------------------------------------------------------- 4.2 the holdout never decides

V2 = Split(
    validation_from="2021-07-01",
    calibration_from="2022-07-01",
    selection_from="2023-07-01",
    holdout_from="2024-07-01",
)
POLICY = Policy(min_validation_events=300)


def test_protocol_v2_windows():
    assert V2.protocol == "v2" and Split("2022-07-01", "2024-07-01").protocol == "v1"
    days = ("2021-06-30", "2021-07-01", "2022-07-01", "2023-07-01", "2024-07-01")
    assert [V2.window(d) for d in days] == [
        "train",
        "validation",
        "calibration",
        "selection",
        "holdout",
    ]
    assert V2.as_dict()["protocol"] == "v2"


def _elo_forecasts(db, report):
    params = report["hyperparameters"]
    items, _ = _forecasts(
        _scoped(history(db, sport="football", until=NOW)),
        k=params["k"],
        home_advantage=params["home_advantage"],
        margin_until=params["draw_margin_until"],
    )
    return {i["match"].result_id: i["forecast"] for i in items}


def test_final_holdout_results_change_no_decision(monkeypatch):
    """Swapping every holdout score must not change hyperparameters, calibration, statuses,
    league gates or the served model - only the reported holdout metrics."""
    monkeypatch.setattr(lab, "HALF_LIVES", (120, 240))
    rules = lab.load_probability_policy(ROOT / "config/probability_policy.toml")
    matches = synthetic(1800)
    scrambled = [
        replace(m, home_score=m.away_score, away_score=m.home_score)
        if m.match_date >= V2.holdout_from
        else m
        for m in matches
    ]
    runs = []
    for data in (matches, scrambled):
        db = Store(":memory:")
        ensure_reference_data(db)
        load(db, data)
        elo = run_elo_experiment(
            db, competitions=("es.1",), split=V2, now=NOW, policy=POLICY, register=False
        )
        goals = lab.run_probability_experiment(
            db,
            competitions=("es.1",),
            split=V2,
            now=NOW,
            policy=POLICY,
            probability_policy=rules,
            elo_forecasts=_elo_forecasts(db, elo),
            register=False,
        )
        runs.append((elo, goals))
    (elo_a, dc_a), (elo_b, dc_b) = runs
    for key in ("hyperparameters", "selection", "calibration", "decision", "decision_by_league"):
        assert elo_a[key] == elo_b[key], key
    assert validation_status(elo_a, POLICY) == validation_status(elo_b, POLICY)
    for key in (
        "half_life_days",
        "selection",
        "calibrators",
        "families",
        "predictive",
        "elo_families",
        "elo_predictive",
        "markets_served",
        "by_league",
        "decision",
    ):
        assert dc_a[key] == dc_b[key], key
    assert elo_a["holdout"] != elo_b["holdout"] and dc_a["holdout"] != dc_b["holdout"]
    assert dc_a["holdout_decision_free"] and elo_a["holdout_decision_free"]
    # prospective intervals of a v2 Elo version come from the calibration window
    margin, bins, ece = elo_parameters(elo_a)
    assert margin == elo_a["hyperparameters"]["draw_margin"]
    assert bins == elo_a["calibration"]["reliability"] and ece == elo_a["calibration"]["ece"]


def test_served_view_is_what_protocol_v2_evaluates():
    blocks = _blocks()
    raw = {
        ("1x2", None): {"home": 0.5, "draw": 0.28, "away": 0.22},
        ("double_chance", None): {"home_draw": 0.78, "draw_away": 0.5, "home_away": 0.72},
        ("total", 1.5): {"over": 0.7, "under": 0.3},
        ("total", 2.5): {"over": 0.5, "under": 0.5},
        ("total", 3.5): {"over": 0.3, "under": 0.7},
        ("btts", None): {"yes": 0.55, "no": 0.45},
    }
    calibrators = {f: blocks for f in ("1x2", "total_1.5", "total_2.5", "total_3.5", "btts")}
    view = lab.served_view(raw, calibrators)
    final, _ = calibrated_market(raw[("1x2", None)], blocks)
    assert view[("1x2", None)] == final
    assert view[("double_chance", None)]["home_draw"] == pytest.approx(1 - final["away"])
    for key in (("total", 2.5), ("btts", None)):
        assert sum(view[key].values()) == pytest.approx(1.0)


# ---------------------------------------------------------------- champion / challenger


def _v1_version(store, family, name, when):
    version_id, _status, _ = register_version(
        store,
        family=family,
        sport="football",
        description="Elo z marginesem remisu, 1X2 czas regulaminowy",  # one row per family
        version=name,
        created_at=when,
        trained_through=when,
        training_data={"competitions": ["es.1"]},
        validation={"hyperparameters": {"k": 20.0, "home_advantage": 60.0}},
        policy=POLICY,
    )
    return version_id


def test_new_versions_wait_as_challengers_until_the_user_decides(store, app_paths):
    load(store, synthetic(1800))
    old = _v1_version(store, services.ELO_FAMILY, "elo-v1", NOW - timedelta(days=30))
    assert services.ensure_model_roles(store, now=NOW) == 2  # champion + holdout flag
    assert services.ensure_model_roles(store, now=NOW) == 0  # idempotent
    found = services.run_protocol_v2(store, app_paths, now=NOW, scope="core")
    new_elo = found["elo"]["version_id"]
    new_dc = found["goals"]["version_id"]
    core = services.CORE_COMPETITIONS
    # the old champion is still served; the new Elo waits in shadow mode
    assert services.scope_version(store, core)["version_id"] == old
    assert services.challenger_versions(store)["core"]["elo"]["version_id"] == new_elo
    # no Dixon-Coles existed: the first version of the scope is served (recorded)
    assert services.scope_version(store, core, services.GOALS_FAMILY)["version_id"] == new_dc
    kinds = [e["kind"] for e in learning.events(store, scope="core")]
    assert kinds.count("challenger_ready") == 2 and "activated" in kinds
    learning.activate(
        store,
        scope="core",
        versions={services.ELO_FAMILY: new_elo},
        now=NOW + timedelta(hours=1),
        reason="decyzja uzytkownika (test)",
    )
    assert services.scope_version(store, core)["version_id"] == new_elo
    assert "core" not in services.challenger_versions(store)
    learning.rollback(
        store,
        scope="core",
        family=services.ELO_FAMILY,
        version_id=old,
        now=NOW + timedelta(hours=2),
        reason="powrot (test)",
    )
    assert services.scope_version(store, core)["version_id"] == old
    assert learning.previous_champions(store, "core", services.ELO_FAMILY) == [new_elo]
    flagged = [e for e in learning.events(store) if e["kind"] == "holdout_used"]
    assert [e["model_version_id"] for e in flagged] == [old]  # v2 versions are not flagged


# ---------------------------------------------------------------- 4.3 dependence of pairs


def test_pair_dependence_reports_a_day_block_bootstrap_interval():
    import random

    rng = random.Random(4)
    independent = []
    for d in range(400):
        for comp in ("L1", "L1", "L2"):
            independent.append((f"d{d}", comp, 0.6, int(rng.random() < 0.6)))
    found = lab.pair_dependence(independent, same_league=True)
    low, high = found["ratio_ci95"]
    assert low < 1 < high and found["days"] == 400
    assert "nie jest dowód niezależności" in found["conclusion"]
    assert "bootstrap blokowy po dniach" in found["method"]
    assert lab.pair_dependence(independent, same_league=True) == found  # seeded
    coupled = []
    for d in range(400):
        hit = int(rng.random() < 0.6)
        coupled += [(f"d{d}", "L1", 0.6, hit), (f"d{d}", "L1", 0.6, hit)]
    tied = lab.pair_dependence(coupled, same_league=True)
    assert tied["ratio_ci95"][0] > 1 and tied["conclusion"].startswith("dodatnia")


def test_ako_probability_is_labelled_as_an_approximation():
    assert JOINT_BASIS.startswith("PRZYBLIŻENIE")


# ---------------------------------------------------------------- MÓJ KUPON

STS_COST = load_profiles(ROOT / "config")["sts"].cost
DAY = "2026-10-31"


def _leg(event, market_key, selection, odds="1.50", p=0.7, low=0.65, **extra):
    """SYNTHETIC offer row of one selection (unique market per event and market key)."""
    where = f"{extra.get('line', '')}-{extra.get('period', 'FT')}"
    return cand(
        event,
        odds,
        p,
        low,
        market_id=f"m-{event}-{market_key}-{where}",
        selection_id=f"s-{event}-{market_key}-{selection}-{where}",
        odds_id=f"o-{event}-{market_key}-{selection}-{odds}",
        market_key=market_key,
        selection=selection,
        **{"line": None, **extra},
    )


def test_cart_refuses_duplicates_same_market_contradictions_and_mixed_periods():
    home = _leg("A", "1x2", "home", "1.80")
    over = _leg("A", "total", "over", "1.40", line="1.5")
    cart, why = mine.add([], [home], home, day=DAY, now=NOW)
    assert why is None and len(cart) == 1
    assert mine.add(cart, [home], home, day=DAY, now=NOW)[1] == "Ta selekcja jest już na kuponie"
    draw = _leg("A", "1x2", "draw", "3.50")
    assert "tego samego rynku" in mine.add(cart, [home, draw], draw, day=DAY, now=NOW)[1]
    away_or_draw = _leg("A", "double_chance", "draw_away", "2.10")
    why = mine.add(cart, [home, away_or_draw], away_or_draw, day=DAY, now=NOW)[1]
    assert why.startswith("Sprzeczne typy")
    half = _leg("A", "1x2", "home", "2.40", period="H1")
    assert "okresów" in mine.add(cart, [home, half], half, day=DAY, now=NOW)[1]
    # compatible selections of one match may be added, but cannot be saved or multiplied
    cart, why = mine.add(cart, [home, over], over, day=DAY, now=NOW)
    assert why is None and len(cart) == 2
    found = mine.summary(
        cart, [home, over], stake=Decimal("10.00"), cost=STS_COST, on_date=NOW.date()
    )
    assert found["blocked"] and found["probability"] is None
    assert found["probability_note"] == mine.SAME_EVENT


def test_cart_summary_of_independent_legs_is_an_approximation():
    a = _leg("A", "total", "over", "1.50", p=0.8, low=0.75, line="1.5")
    b = _leg("B", "1x2", "home", "1.80", p=0.6, low=0.55)
    cart = [mine.leg_from(x, day=DAY, now=NOW) for x in (a, b)]
    found = mine.summary(cart, [a, b], stake=Decimal("10.00"), cost=STS_COST, on_date=NOW.date())
    assert found["total_odds"] == Decimal("2.7000") and not found["blocked"]
    assert found["payout"] == Decimal("23.76") and found["net_profit"] == Decimal("13.76")
    assert found["break_even"] == pytest.approx(10 / 23.76)
    assert found["probability"] == pytest.approx(0.48)
    assert found["probability_low"] == pytest.approx(0.4125)
    assert found["probability_note"] == mine.APPROXIMATION and found["validated"]
    assert float(found["ev"]) == pytest.approx((0.48 * 13.76 - 0.52 * 10) / 10, abs=1e-4)


def test_cart_blocks_stale_started_and_unmodelled_legs():
    stale = _leg("A", "1x2", "home", freshness="unconfirmed")
    started = _leg("B", "1x2", "home", event_status="started")
    bare = _leg("C", "1x2", "home", p_model=None, p_low=None)
    cart = [mine.leg_from(x, day=DAY, now=NOW) for x in (stale, started, bare)]
    found = mine.summary(
        cart, [stale, started, bare], stake=Decimal("5.00"), cost=STS_COST, on_date=NOW.date()
    )
    codes = {i.code for i in found["issues"]}
    assert {"stale", "started", "no_model"} <= codes and found["blocked"]
    assert found["probability"] is None and found["probability_note"] == mine.NO_MODEL


def test_cart_shows_a_changed_price_and_a_missing_selection(tmp_path):
    a = _leg("A", "1x2", "home", "1.80")
    cart = [mine.leg_from(a, day=DAY, now=NOW)]
    newer = _leg("A", "1x2", "home", "1.65")
    rows = mine.current(cart, [newer])
    issues = mine.validate(cart, rows)
    assert any(i.code == "price_changed" and "1.65" in i.message for i in issues)
    assert mine.current(cart, [])[0] is None
    assert any(i.code == "missing" for i in mine.validate(cart, [None]))
    draft = tmp_path / "drafts" / "my_coupon.json"
    mine.save_draft(draft, cart, Decimal("7.50"))
    assert mine.load_draft(draft) == {"legs": cart, "stake": "7.50"}
    assert mine.load_draft(tmp_path / "missing.json") == {"legs": [], "stake": "10.00"}


# ---------------------------------------------------------------- AI Coupon Advisor


def _three():
    legs = [
        _leg("A", "total", "over", "1.40", p=0.82, low=0.78, line="1.5"),
        _leg("B", "1x2", "home", "1.70", p=0.62, low=0.55),
        _leg("C", "btts", "yes", "1.80", p=0.58, low=0.51),
    ]
    return legs, [mine.leg_from(x, day=DAY, now=NOW) for x in legs]


def _report(rows, cart, stake="10.00"):
    return advisor.evaluate(
        cart, rows, stake=Decimal(stake), cost=STS_COST, on_date=NOW.date(), now=NOW
    )


def test_advisor_finds_the_weakest_link_and_explains_every_leg():
    rows, cart = _three()
    report = _report(rows, cart)
    assert report["weakest"] == 2 and "51,0%" in report["weakest_reason"]
    assert all(leg["supported"] and leg["reason"].startswith("Model") for leg in report["legs"])
    assert "Szacowana szansa" in report["verdict"]
    assert report["summary"]["probability"] == pytest.approx(0.82 * 0.62 * 0.58)
    # a leg without a model is the weakest link and says so
    bare = _leg("D", "1x2", "draw", "3.10", p_model=None, p_low=None)
    rows2, cart2 = [*rows, bare], [*cart, mine.leg_from(bare, day=DAY, now=NOW)]
    report2 = _report(rows2, cart2)
    assert report2["weakest"] == 3 and report2["legs"][3]["reason"] == mine.NO_MODEL
    assert report2["summary"]["probability"] is None


def test_alternatives_are_proposals_only():
    rows, cart = _three()
    better = _leg("C", "total", "over", "1.30", p=0.8, low=0.75, line="1.5")
    found = advisor.alternatives(
        cart,
        rows,
        [*rows, better],
        stake=Decimal("10.00"),
        cost=STS_COST,
        on_date=NOW.date(),
        now=NOW,
    )
    titles = [o["title"] for o in found]
    assert titles[:2] == ["Usuń najsłabsze ogniwo", "Mniej selekcji"]
    swap = next(o for o in found if o["title"] == "Lepiej zamodelowany rynek")
    assert (
        better in swap["rows"]
        and swap["summary"]["probability"] > _report(rows, cart)["summary"]["probability"]
    )
    assert "SOLO" in titles
    assert len(cart) == 3  # nothing changed in the user's coupon


def test_template_explainer_answers_the_example_questions():
    rows, cart = _three()
    report = _report(rows, cart)
    kwargs = dict(
        cart=cart,
        rows=rows,
        stake=Decimal("10.00"),
        cost=STS_COST,
        on_date=NOW.date(),
        profiles={
            "standard": {
                "label": "STANDARDOWY",
                "min_total_odds": 1.8,
                "max_total_odds": 2.2,
                "max_legs": 3,
                "min_leg_probability": 0.65,
                "require_predictive_validation": True,
            }
        },
        automatic={"label": "STANDARDOWY", "p_conservative": 0.5, "total_odds": "1.95"},
    )
    why = ex.answer("Dlaczego ten typ ma tylko 58%?", report, **kwargs)
    assert why["recognised"] and why["answer"].startswith("3. C - Rywal Testowy")
    risky = ex.answer("Który mecz jest najbardziej ryzykowny?", report, **kwargs)
    assert "typ 3" in risky["answer"]
    without = ex.answer("Co się stanie, jeśli usunę trzeci mecz?", report, **kwargs)
    assert "4.2840 → 2.3800" in without["answer"]
    profile = ex.answer(
        "Dlaczego ten kupon nie kwalifikuje się do profilu standardowego?", report, **kwargs
    )
    assert "STANDARDOWY" in profile["answer"] and "powyżej 2.2" in profile["answer"]
    versus = ex.answer(
        "Czy ten kupon ma większą szansę niż propozycja automatyczna?", report, **kwargs
    )
    assert "mniejszą" in versus["answer"]
    assert not ex.answer("Jaka będzie pogoda?", report, **kwargs)["recognised"]


# ---------------------------------------------------------------- optional local model


class _Ollama:
    """SYNTHETIC stand-in for a local Ollama server: records requests, never connects."""

    def __init__(self, answer, models=(llm.DEFAULT_MODEL,), running=True):
        self.sent, self.answer, self.models, self.running = [], answer, list(models), running

    def __call__(self, request, timeout):
        from urllib.error import URLError

        if not self.running:
            raise URLError("connection refused")
        assert request.full_url.startswith("http://127.0.0.1:11434/")
        if request.full_url.endswith("/api/tags"):
            body = {"models": [{"name": name} for name in self.models]}
        else:
            self.sent.append(json.loads(request.data.decode("utf-8")))
            body = {
                "message": {"role": "assistant", "content": self.answer},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 900,
                "eval_count": 120,
                "total_duration": 4_200_000_000,
            }
        return _Body(body)


class _Body:
    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_local_model_is_off_by_default_free_and_sends_only_coupon_facts(store):
    from sports_edge.settings import AppSettings

    rows, cart = _three()
    report = _report(rows, cart)
    settings = AppSettings()
    assert not settings.llm_enabled and settings.llm_model == llm.DEFAULT_MODEL
    assert llm.available(settings)[0] is False
    on = AppSettings(llm_enabled=True)  # works in offline mode too: nothing leaves the computer
    answer = '{"answer": "Najsłabszy jest typ 3: szansa 58,0%.", "facts_used": [], "missing": ""}'
    server = _Ollama(answer)
    found = llm.ask("Który typ?", report, store=store, settings=on, now=NOW, opener=server)
    assert found["ok"] and found["cost_usd"] == 0 and found["seconds"] == 4.2
    request = server.sent[0]
    assert request["model"] == llm.DEFAULT_MODEL and request["stream"] is False
    assert request["format"]["required"] == ["answer", "facts_used", "missing"]
    sent = json.dumps(request["messages"], ensure_ascii=False)
    assert "s-A-" not in sent and "o-A-" not in sent and "sqlite" not in sent.lower()
    [usage] = store.rows("SELECT * FROM llm_usage")
    assert usage["provider"] == "ollama" and usage["cost_usd"] == "0"
    invented = _Ollama('{"answer": "Szansa wynosi 91,3%.", "facts_used": [], "missing": ""}')
    wrong = llm.ask("?", report, store=store, settings=on, now=NOW, opener=invented)
    assert not wrong["ok"] and wrong["unknown_numbers"] == ["91.3"]


def test_local_model_refuses_other_hosts_and_explains_what_is_missing(store):
    from sports_edge.settings import AppSettings

    rows, cart = _three()
    report = _report(rows, cart)
    remote = AppSettings(llm_enabled=True, llm_url="http://api.example.com:11434")
    usable, reason = llm.available(remote, opener=_Ollama("{}"))
    assert not usable and "127.0.0.1" in reason
    with pytest.raises(ValueError):
        llm.local_url("https://127.0.0.1:11434")
    on = AppSettings(llm_enabled=True)
    down = _Ollama("", running=False)
    stopped = llm.ask("?", report, store=store, settings=on, now=NOW, opener=down)
    assert not stopped["ok"] and "ollama.com" in stopped["reason"]
    missing = llm.available(on, opener=_Ollama("{}", models=["gemma3:12b"]))
    assert not missing[0] and f"ollama pull {llm.DEFAULT_MODEL}" in missing[1]
    broken = llm.ask("?", report, store=store, settings=on, now=NOW, opener=_Ollama("not json"))
    assert not broken["ok"] and "formacie" in broken["reason"]


# ---------------------------------------------------------------- four profiles and generator

RULES_P3 = lab.load_probability_policy(ROOT / "config/probability_policy.toml")


def _pool(n=14, seed=5):
    """SYNTHETIC validated selections of different matches with odds 1.15–1.95."""
    import random

    rng = random.Random(seed)
    out = []
    for i in range(n):
        odds = Decimal(str(round(rng.uniform(1.15, 1.95), 2)))
        p = min(0.95, 1 / float(odds) + rng.uniform(0.0, 0.06))
        out.append(_leg(f"E{i}", "total", "over", str(odds), p=p, low=p - 0.03, line="1.5"))
    return out


def _build(candidates, rules=RULES_P3):
    from sports_edge.analysis.coupon_builder import build_coupons

    return build_coupons(candidates, rules, cost=STS_COST, on_date=NOW.date())


def test_policy_has_the_four_profiles_and_never_lowers_thresholds():
    profiles = RULES_P3.profiles
    assert list(profiles) == ["high_hit", "standard", "higher_payout", "tasiemka"]
    ranges = {k: (v["min_total_odds"], v["max_total_odds"]) for k, v in profiles.items()}
    assert ranges == {
        "high_hit": (1.25, 1.60),
        "standard": (1.80, 2.20),
        "higher_payout": (2.50, 4.00),
        "tasiemka": (8.00, 12.00),
    }
    high_hit = profiles["high_hit"]
    assert high_hit["prefer_solo"] and high_hit["min_leg_probability"] >= 0.75
    assert profiles["standard"]["min_leg_probability"] >= 0.65
    assert profiles["higher_payout"]["min_leg_probability"] >= 0.55
    assert RULES_P3.version == "2026-11-02.p3"


def test_profiles_build_coupons_inside_their_odds_ranges():
    built = _build(_pool())
    for name, proposal in built.proposals.items():
        if proposal is None:
            continue
        profile = RULES_P3.profiles[name]
        assert Decimal(str(profile["min_total_odds"])) <= proposal.total_odds
        assert proposal.total_odds <= Decimal(str(profile["max_total_odds"]))
        assert len({leg.event_id for leg in proposal.legs}) == len(proposal.legs)
        assert proposal.p_conservative >= profile["min_coupon_probability"]
    assert built.proposals["high_hit"] is not None
    assert len(built.proposals["high_hit"].legs) == 1  # SOLO preferred


def test_tasiemka_is_built_only_when_the_data_justify_it():
    rich = _build(_pool(30, seed=9))
    tape = rich.proposals["tasiemka"]
    if tape is not None:
        assert Decimal("8") <= tape.total_odds <= Decimal("12")
        assert tape.p_conservative >= 0.07 and len(tape.legs) <= 8
    poor = _build(_pool(3))
    assert poor.proposals["tasiemka"] is None
    assert poor.refusal_codes["tasiemka"] in {"payout", "low_probability", "coupon_probability"}


def test_dynamic_programming_matches_the_exhaustive_search(monkeypatch):
    from sports_edge.analysis import coupon_builder as cb

    legs = _pool(12, seed=3)
    profile = {
        "label": "T",
        "min_total_odds": 3.0,
        "max_total_odds": 5.0,
        "max_legs": 4,
        "min_leg_probability": 0.0,
        "min_net_profit_share": 0,
        "require_predictive_validation": False,
    }
    args = dict(cost=STS_COST, on_date=NOW.date(), stake=Decimal("10.00"), keep=1)
    exhaustive = cb._optimise(legs, profile, **args)
    monkeypatch.setattr(cb, "EXHAUSTIVE_LIMIT", 10)
    dynamic = cb._optimise(legs, profile, **args)
    assert dynamic and exhaustive
    assert dynamic[0][0][0] == pytest.approx(exhaustive[0][0][0], rel=0.02)
    assert Decimal("3.0") <= dynamic[0][2] <= Decimal("5.0")


def test_generator_targets_the_odds_and_respects_filters():
    from sports_edge.analysis.generator import Request, generate, preset

    pool = _pool(16, seed=11)
    request = Request(Decimal("2.00"), tolerance=0.10, max_legs=3, min_leg_probability=0.5)
    found = generate(
        pool, request, now=NOW, cost=STS_COST, on_date=NOW.date(), stake=Decimal("10.00")
    )
    proposal = found["proposal"]
    assert proposal is not None and Decimal("1.80") <= proposal.total_odds <= Decimal("2.20")
    assert "PRZYBLIŻENIE" in proposal.joint_basis
    league = Request(Decimal("2.00"), exclude_leagues=("LIGA SYNTETYCZNA",))
    blocked = generate(
        pool, league, now=NOW, cost=STS_COST, on_date=NOW.date(), stake=Decimal("10.00")
    )
    assert blocked["proposal"] is None and blocked["excluded"] == {"liga wykluczona": 16}
    impossible = Request(Decimal("40.00"), tolerance=0.05, max_legs=2)
    none = generate(
        pool, impossible, now=NOW, cost=STS_COST, on_date=NOW.date(), stake=Decimal("10.00")
    )
    assert none["proposal"] is None and "progi nie są obniżane" in none["refusal"]
    standard = preset(RULES_P3.profiles["standard"])
    assert standard.low == Decimal("1.80") and standard.high == Decimal("2.20")


# ---------------------------------------------------------------- simulators


def test_net_win_matches_the_sts_settlement():
    import numpy as np

    from sports_edge.settlement import Outcome, settle

    for stake, odds in (("10.00", "2.40"), ("1000.00", "3.00"), ("50.00", "1.35")):
        exact = settle(Decimal(stake), Decimal(odds), Outcome.WIN, STS_COST, on_date=NOW.date())
        fast = sim.net_win(np.array([float(stake)]), float(odds))[0]
        assert fast == pytest.approx(float(exact.net_profit), abs=0.011)


def test_budget_simulation_is_seeded_and_never_chases_losses():
    cfg = sim.BudgetConfig(
        budget=500,
        stake_mode="fixed",
        stake=10,
        coupons=50,
        odds=2.0,
        probability=0.5,
        probability_low=0.45,
        runs=2000,
        seed=3,
    )
    first, second = sim.simulate_budget(cfg), sim.simulate_budget(cfg)
    assert first.final == second.final and first.sample_paths == second.sample_paths
    assert first.status == "warunkowa (eksperymentalna)"
    # fixed stake: every step changes the budget by -10 or by the net win of 10 zł only
    steps = {
        round(b - a, 2) for path in first.sample_paths for a, b in zip(path, path[1:], strict=False)
    }
    assert steps <= {-10.0, round(10 * 0.88 * 2.0 - 10, 2)}
    assert first.final["p5"] <= first.final["p50"] <= first.final["p95"]
    assert 0 < first.p_loss < 1 and first.drawdown["p95"] >= first.drawdown["median"]
    share = sim.simulate_budget(
        sim.BudgetConfig(
            budget=500,
            stake_mode="percent",
            stake=5,
            coupons=30,
            odds=2.0,
            probability=0.3,
            runs=500,
            seed=1,
            validated=True,
        )
    )
    assert share.status == "oparta na zwalidowanym modelu"
    for path in share.sample_paths:  # a share of the CURRENT budget: smaller after a loss
        for a, b in zip(path, path[1:], strict=False):
            if b < a:
                assert round(a - b, 2) == pytest.approx(round(a * 0.05, 2), abs=0.011)


def test_loss_limit_streaks_and_shared_exposure():
    base = dict(budget=200, stake_mode="fixed", stake=10, coupons=60, odds=1.9, runs=3000)
    stopped = sim.simulate_budget(sim.BudgetConfig(**base, probability=0.2, loss_limit=50))
    assert stopped.p_loss_limit > 0.9 and stopped.final["p50"] >= 200 - 50 - 10
    calm = sim.simulate_budget(sim.BudgetConfig(**base, probability=0.5, seed=5))
    tied = sim.simulate_budget(
        sim.BudgetConfig(**base, probability=0.5, seed=5, shared_exposure=0.6)
    )
    assert tied.streaks["median"] > calm.streaks["median"]  # correlated coupons: longer streaks
    with pytest.raises(ValueError):
        sim.simulate_budget(sim.BudgetConfig(**base, probability=1.2))


def test_historical_offer_is_read_as_of_the_decision_time(store, import_sts):
    from datetime import datetime as dt

    from sports_edge.repository import offer_table

    before = dt(2026, 10, 31, 12, 0, tzinfo=UTC)  # the fixture was received at 12:30
    after = dt(2026, 10, 31, 13, 0, tzinfo=UTC)
    assert offer_table(store, day="2026-10-31", as_of=before) == []
    later = offer_table(store, day="2026-10-31", as_of=after)
    assert later and later == offer_table(store, day="2026-10-31")


# ---------------------------------------------------------------- data collection engine


def _forecast(store, policy, home, market="1x2", probabilities=None, line=None):
    version_id = _version(store, policy)
    event = store.rows("SELECT id, start_at FROM events WHERE home=?", (home,))[0]
    save_prediction(
        store,
        model_version_id=version_id,
        event_id=event["id"],
        market_key=market,
        period="FT",
        line=line,
        probabilities=probabilities or {"home": 0.5, "draw": 0.3, "away": 0.2},
        uncertainty={"interval": {}},
        inputs=INPUTS,
        kind="prospective",
        created_at=NOW - timedelta(minutes=30),
    )
    return event


def test_every_forecast_is_settled_with_versioned_corrections(store, import_sts, policy):
    from sports_edge.paper import record_result

    event = _forecast(store, policy, "Legia Warszawa")
    start = datetime.fromisoformat(event["start_at"])
    assert collection.settle_predictions(store, now=start - timedelta(hours=1)) == {}
    later = start + timedelta(hours=3)
    assert collection.settle_predictions(store, now=later) == {"pending": 1}
    record_result(
        store, event_id=event["id"], status="completed", home_score=2, away_score=0, now=later
    )
    assert collection.settle_predictions(store, now=later) == {"settled": 1}
    assert collection.settle_predictions(store, now=later) == {"unchanged": 1}  # idempotent
    [first] = collection.settled_forecasts(store)
    assert first["outcome"]["selections"] == {"home": 1, "draw": 0, "away": 0}
    # the same source corrects the score: a new revision that points to the first one
    record_result(
        store,
        event_id=event["id"],
        status="completed",
        home_score=1,
        away_score=1,
        now=later + timedelta(hours=1),
    )
    found = collection.settle_predictions(store, now=later + timedelta(hours=2))
    assert found == {"settled": 1, "corrections": 1}
    [second] = collection.settled_forecasts(store)
    assert second["revision"] == 2 and second["outcome"]["selections"]["draw"] == 1
    assert second["supersedes_id"] == first["id"]  # history kept, nothing overwritten


def test_settlement_statuses_postponed_unresolved_uncertain_and_conflict(store, import_sts, policy):
    from sports_edge.paper import record_result

    legia = _forecast(store, policy, "Legia Warszawa")
    gornik = _forecast(store, policy, "Górnik Zabrze")
    start = max(datetime.fromisoformat(e["start_at"]) for e in (legia, gornik))
    record_result(store, event_id=legia["id"], status="postponed", now=start)
    record_result(
        store,
        event_id=gornik["id"],
        status="completed",
        home_score=1,
        away_score=0,
        now=start + timedelta(hours=2),
        verified=False,
    )
    found = collection.settle_predictions(store, now=start + timedelta(hours=3))
    assert found == {"postponed": 1, "uncertain": 1}
    record_result(
        store,
        event_id=gornik["id"],
        status="completed",
        home_score=0,
        away_score=0,
        now=start + timedelta(hours=4),
        verified=False,
        source="other",
    )
    found = collection.settle_predictions(store, now=start + timedelta(hours=5))
    assert found == {"unchanged": 1, "source_conflict": 1, "corrections": 1}
    report = collection.completeness(store, now=start + timedelta(hours=5))
    assert report["predictions"] == 2 and report["pending"] == 0
    assert report["settlements"] == {"postponed": 1, "source_conflict": 1}


def test_unresolved_after_two_days_and_bounded_catch_up(store, import_sts, policy):
    event = _forecast(store, policy, "Legia Warszawa")
    start = datetime.fromisoformat(event["start_at"])
    late = start + collection.UNRESOLVED_AFTER + timedelta(hours=1)
    calls = []
    out = collection.catch_up(store, now=late, update=lambda: calls.append(1) or {"ok": 1})
    assert calls == [1] and out["settlements"] == {"unresolved": 1}
    assert collection.last_runs(store)["prediction_settlement"]["status"] == "ok"
    gap = late + timedelta(days=30)
    out = collection.catch_up(store, now=gap, update=lambda: calls.append(2))
    assert calls == [1] and "przerwa" in out["results_update"]  # no unlimited downloads
    report = collection.completeness(store, now=gap)
    assert report["jobs"]["results_update"]["status"] == "ok"


# ---------------------------------------------------------------- learning, ablation, storage


def test_weekly_review_skips_with_a_reason_and_trains_only_on_request(store, app_paths):
    from sports_edge import retraining

    old = _v1_version(store, services.ELO_FAMILY, "elo-v1", NOW - timedelta(days=30))
    services.ensure_model_roles(store, now=NOW)
    assert retraining.due(store, now=NOW)
    calls = []

    def train(scope):
        calls.append(scope)
        return {"summary": {"scope": scope}}

    found = retraining.review(store, app_paths, now=NOW, scopes=("core",), train=train)
    assert found["core"]["status"] == "skipped" and calls == []
    assert any("nowych wyników" in r for r in found["core"]["reasons"])
    assert collection.last_runs(store)["retraining_review"]["status"] == "skipped"
    assert retraining.scope_status(store, "core", now=NOW) == "Oczekuje na nowe dane"
    forced = retraining.review(
        store, app_paths, now=NOW + timedelta(minutes=1), scopes=("core",), train=train, force=True
    )
    assert forced["core"]["status"] == "trained" and calls == ["core"]
    assert not retraining.due(store, now=NOW + timedelta(days=1))
    kinds = [e["kind"] for e in learning.events(store, scope="core")]
    assert (
        "review" in kinds
        and services.scope_version(store, services.CORE_COMPETITIONS)["version_id"] == old
    )  # a review never changes the served model


def test_champion_challenger_conclusion_is_plain_and_never_promotes(store, app_paths):
    from sports_edge import retraining

    load(store, synthetic(1800))
    old = _v1_version(store, services.ELO_FAMILY, "elo-v1", NOW - timedelta(days=30))
    services.ensure_model_roles(store, now=NOW)
    services.run_protocol_v2(store, app_paths, now=NOW, scope="core")
    compared = retraining.comparison(store, "core")
    elo = compared["elo"]
    assert elo["champion"]["version_id"] == old and elo["challenger"]["protocol"] == "v2"
    assert "zawieszone" in elo["conclusion"] and "tryb cienia" in elo["conclusion"]
    assert retraining.scope_status(store, "core", now=NOW) == "Challenger gotowy"
    assert services.scope_version(store, services.CORE_COMPETITIONS)["version_id"] == old
    history = retraining.history(store)
    assert history[0]["Zdarzenie"] in {"Challenger gotowy", "Aktywowany"}


def test_ablation_features_use_only_earlier_matches():
    from sports_edge import ablation

    rows = [
        {
            "id": f"m{i}",
            "match_date": f"2024-01-{i + 1:02d}",
            "competition": "es.1",
            "home": "A" if i % 2 == 0 else "B",
            "away": "B" if i % 2 == 0 else "A",
            "home_score": 2,
            "away_score": 0,
        }
        for i in range(10)
    ]
    before = ablation.features(rows)
    changed = [dict(r) for r in rows]
    changed[6] = {**changed[6], "home_score": 0, "away_score": 5}  # a later result changes
    after = ablation.features(changed)
    for i in range(7):  # features of m0..m6 do not depend on the result of m6 or later
        assert before[f"m{i}"] == after[f"m{i}"]
    assert before["m7"] != after["m7"]


def test_softmax_learns_a_simple_rule():
    import numpy as np

    from sports_edge import ablation

    rng = np.random.default_rng(1)
    x = rng.normal(size=(600, 1))
    y = np.where(x[:, 0] > 0.5, 0, np.where(x[:, 0] < -0.5, 2, 1))
    model = ablation.fit_softmax(x, y)
    p = ablation.predict_softmax(model, np.array([[2.0], [0.0], [-2.0]]))
    assert p[0].argmax() == 0 and p[1].argmax() == 1 and p[2].argmax() == 2
    assert np.allclose(p.sum(axis=1), 1)


def test_forecast_quality_metrics_and_honest_coupon_review():
    from sports_edge import forecast_quality as fq

    rows = [
        {
            "event": f"e{i}",
            "p": 0.8,
            "y": int(i % 5 != 0),
            "market": "total",
            "line": "1.5",
            "selection": "over",
            "family": "total_1.5",
            "competition": "es.1",
            "scope": "core",
            "version": "v",
            "start_at": "2026-10-31T19:00:00+00:00",
        }
        for i in range(50)
    ]
    found = fq.metrics(rows)
    assert found["events"] == 50 and not found["small_sample"]
    assert found["ece"] == pytest.approx(0.0, abs=1e-9)  # 80% forecasts, 80% hits
    [bin_] = fq.calibration(rows)
    assert bin_["range"] == "80%–90%" and bin_["freq"] == 0.8 and bin_["flag"] == ""
    assert fq.metrics(rows[:10])["small_sample"]
    review = fq.coupon_review(
        {
            "outcome": "loss",
            "proposal": {
                "probability": {"calibrated": 0.62},
                "legs": [
                    {"event": "A - B", "p_model": 0.8, "outcome": "loss"},
                    {"event": "C - D", "p_model": 0.78, "outcome": "win"},
                ],
            },
        }
    )
    assert review[0].startswith("Kupon nietrafiony: nie weszło 1 z 2")
    assert any("nie są zgadywane" in line for line in review)


def test_storage_report_integrity_and_restore_test(app_paths):
    from sports_edge import maintenance
    from sports_edge.backup import create_backup

    store = services.open_store(app_paths)
    report = maintenance.storage_report(store, app_paths, now=NOW)
    assert report["integrity"]["ok"] and report["schema"] == report["schema_expected"]
    assert "model_events" in report["rows"] and report["archive_plan"]
    folder = create_backup(app_paths, now=NOW)
    test = maintenance.restore_test(folder / "sports_edge.db", store)
    assert test["ok"] and test["integrity"] == "ok" and not test["problems"]
