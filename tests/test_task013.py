"""TASK 013 — from a challenger to a decision: a stable Dixon–Coles half-life, the forecast id
of every coupon leg, the WYSOKA TRAFNOŚĆ diagnosis, the user's recorded v3 gate decision and
the age of the STS price after the match-day mode.

Every team, price, forecast and result below is SYNTHETIC test data, not a real fact.
"""

import json
import math
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import ROOT, SRC
from test_models_data import load, synthetic
from test_task011 import _event, _import, _market, _quote, _version

from sports_edge import automation, coupon_store, insights, learning, rolling, services
from sports_edge import prospective_eval as pe
from sports_edge.analysis.coupon_builder import build_coupons, closest
from sports_edge.collection import record_job
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.models.goals import score_matrix
from sports_edge.results_engine import effective_results
from sports_edge.sportsdata import _scoped, _to_match

FAR = datetime(2030, 1, 1, tzinfo=UTC)
SMALL = rolling.RollingPolicy(min_window_matches=60, min_fit_matches=60)
DAY = "2026-10-31"
NOON = datetime(2026, 10, 31, 12, 45, tzinfo=UTC)  # the fixture STS document is from 12:00


# 1 ---------------------------------------------------------------- the worker fix (§2)


def test_the_runtime_fix_is_merged_and_its_tests_are_present():
    import inspect

    source = inspect.getsource(automation.write_runtime)
    assert "NamedTemporaryFile" in source and 'with_suffix(".tmp")' not in source
    assert "_beat_lock" in inspect.getsource(automation.Worker.beat)
    assert "logger.exception" in inspect.getsource(automation.Worker._heartbeat_loop)
    tests = (ROOT / "tests" / "test_runtime_write.py").read_text(encoding="utf-8")
    assert tests.count("\ndef test_") == 6


# 2 ---------------------------------------------------------------- the half-life rule (§3)


@pytest.fixture(scope="module")
def history():
    from sports_edge.storage import Store

    store = Store(":memory:")
    from sports_edge.repository import ensure_reference_data

    ensure_reference_data(store)
    load(store, synthetic(1800))  # one match a day, 2020-01-01 … 2024-12-05
    rows = effective_results(store, sport="football", until=FAR, competitions=("es.1",))
    yield rows
    store.close()


def _train(rows, when, rule, previous=None, policy=SMALL):
    by_id = {r["id"]: r for r in rows}
    matches = _scoped([_to_match(r) for r in rows])
    known = [m for m in matches if m.available_at <= when]
    windows = rolling.windows_at(when, ((m.match_date, m.available_at) for m in known), policy)
    return rolling.dc_train(
        rolling.prepare(rows),
        by_id,
        windows,
        cutoff=when,
        rule=rule,
        previous=previous,
        rolling=policy,
    )


T = datetime(2024, 6, 1, tzinfo=UTC)


def test_the_half_life_rule_is_deterministic_and_recorded(history):
    one = _train(history, T, "sticky12", previous=480.0)
    two = _train(history, T, "sticky12", previous=480.0)
    assert one["half_life"] == two["half_life"] and one["half_life_rule"] == two["half_life_rule"]
    decision = one["half_life_rule"]
    assert decision["rule"] == "sticky12" and decision["previous"] == 480.0
    assert decision["chosen"] == one["half_life"]
    if decision["best"] != 480.0:  # a change only with an interval below zero
        assert decision["changed"] == (decision["ci95"][1] < 0)
    # fixed: the champion's value, only the maps move; select24 scores two 12-month blocks
    fixed = _train(history, T, "fixed", previous=240.0)
    assert fixed["half_life"] == 240.0 and [t["half_life_days"] for t in fixed["tuning"]] == [240.0]
    long = _train(history, T, "select24")
    assert long["half_life_rule"]["blocks_used"] == 2
    assert all(t["choice_n"] > t["selection_n"] for t in long["tuning"])
    with pytest.raises(ValueError, match="fixed"):
        _train(history, T, "fixed")


def test_the_penalty_keeps_the_previous_value_unless_the_gain_is_significant(history, monkeypatch):
    free = _train(history, T, "select12")
    previous = next(h for h in (120.0, 240.0, 480.0) if h != free["half_life"])
    monkeypatch.setattr(pe, "_bootstrap", lambda diffs, *, resamples, seed: [-0.01, 0.002])
    kept = _train(history, T, "sticky12", previous=previous)
    assert kept["half_life"] == previous and kept["half_life_rule"]["changed"] is False
    assert kept["half_life_rule"]["best"] == free["half_life"]
    monkeypatch.setattr(pe, "_bootstrap", lambda diffs, *, resamples, seed: [-0.02, -0.001])
    moved = _train(history, T, "sticky12", previous=previous)
    assert moved["half_life"] == free["half_life"] and moved["half_life_rule"]["changed"] is True
    # the calibration maps are always those of the 12-month windows of T
    assert set(moved["calibrators"]) == set(rolling.CALIBRATED_FAMILIES)


def test_choice_blocks_never_use_results_after_their_windows(history):
    by_id = {r["id"]: r for r in history}
    matches = _scoped([_to_match(r) for r in history])
    windows = rolling.windows_at(T, ((m.match_date, m.available_at) for m in matches), SMALL)
    blocks = rolling.choice_blocks(windows, 24)
    assert len(blocks) == 2
    assert blocks[1].selection_from == windows.calibration_from
    assert blocks[1].trained_at == datetime.fromisoformat(windows.selection_from).replace(
        tzinfo=UTC
    )
    for block in blocks:
        for row in history:
            name = block.window(row["match_date"], datetime.fromisoformat(row["available_at"]))
            if name == "selection":
                assert datetime.fromisoformat(row["available_at"]) <= block.trained_at
    del by_id


def test_walk_forward_rules_use_only_results_known_before_the_month(history):
    """Changing every result known only after T changes no rule's half-life or maps."""
    rules = ("sticky12", "select24", "fixed", "select12")
    matches = _scoped([_to_match(r) for r in history])
    previous = {rule: 480.0 for rule in rules}

    def model(rows):
        return rolling.train_at(
            T,
            matches=_scoped([_to_match(r) for r in rows]),
            prepared=rolling.prepare(rows),
            rows_by_id={r["id"]: r for r in rows},
            rolling=SMALL,
            cache={},
            rules=rules,
            previous=previous,
        )

    base = model(history)
    later = [
        {**r, "home_score": r["away_score"], "away_score": r["home_score"] + 2}
        if datetime.fromisoformat(r["available_at"]) > T
        else r
        for r in history
    ]
    changed = model(later)
    for rule in rules:
        assert base["goals"][rule]["half_life"] == changed["goals"][rule]["half_life"]
        assert base["goals"][rule]["calibrators"] == changed["goals"][rule]["calibrators"]
    earlier = [
        {**r, "home_score": r["away_score"], "away_score": r["home_score"] + 2}
        if "2022-07-01" <= r["match_date"] < "2023-01-01"  # inside the calibration window
        else r
        for r in history
    ]
    control = model(earlier)  # results before T DO change the model (control)
    assert control["goals"]["select12"]["calibrators"] != base["goals"]["select12"]["calibrators"]
    del matches


def test_the_protocol_file_holds_the_chosen_rule_and_the_core_windows():
    policy = rolling.load_policy(ROOT / "config")
    assert policy.half_life_rule == "sticky12" and policy.version.endswith("v3b")
    assert policy.for_scope("core").calibration_months == 24
    assert policy.for_scope("extended").calibration_months == 12
    assert policy.rule_info()["label"] == rolling.HALF_LIFE_RULES["sticky12"]
    assert policy.for_scope("core").half_life_rule == "sticky12"


# 3 ---------------------------------------------------------------- forecast id of a leg (§4)


def _forecast_world(
    store, import_sts, app_paths, created=datetime(2026, 10, 31, 12, 5, tzinfo=UTC)
):
    """An Elo-like forecast (protocol v2, synthetic) of the first 1X2 market of the fixture."""
    row = store.rows(
        """SELECT e.id event_id, e.league, m.id market_id FROM events e
        JOIN markets m ON m.event_id=e.id WHERE m.market_key='1x2' AND m.period='FT'
        AND e.start_at>? ORDER BY e.start_at, e.id LIMIT 1""",
        (iso(NOON + timedelta(hours=1)),),
    )[0]
    version = _version(
        store, "elo_ordered_draw", "elo-test-v2", created=created - timedelta(days=9)
    )
    return row, version, _prediction(store, version, row["event_id"], created, 0.6)


def _prediction(store, version, event_id, created, p_home):
    pid = digest(["task013-prediction", version, event_id, iso(created)])
    rest = 1 - p_home
    store.insert(
        "predictions",
        id=pid,
        model_version_id=version,
        event_id=event_id,
        market_key="1x2",
        period="FT",
        line=None,
        created_at=iso(created),
        probabilities_json=canonical({"home": p_home, "draw": rest * 0.6, "away": rest * 0.4}),
        uncertainty_json=canonical(
            {
                "interval": {
                    "home": [p_home - 0.05, p_home + 0.05],
                    "draw": [rest * 0.6 - 0.04, rest * 0.6 + 0.04],
                    "away": [rest * 0.4 - 0.03, rest * 0.4 + 0.03],
                },
                "predictive_status": "validated_predictive",
                "family": "1x2",
            }
        ),
        inputs_json=canonical({"competition": "es.1"}),
        kind="prospective",
    )
    return pid


def _candidates(store, app_paths, versions, now=NOON):
    from sports_edge.settings import load_settings

    settings = load_settings(app_paths)
    found, _forecasts = services.day_candidates_at(
        store, app_paths, settings, DAY, now=now, versions=versions
    )
    return found


def _legs(store) -> dict[str, str | None]:
    return {
        r["odds_id"]: r["prediction_id"]
        for r in store.rows("SELECT odds_id, prediction_id FROM coupon_legs")
    }


def test_every_saving_path_stores_the_forecast_id_of_the_candidate(store, import_sts, app_paths):
    from types import SimpleNamespace

    from sports_edge import my_coupon

    row, version, pid = _forecast_world(store, import_sts, app_paths)
    candidates = _candidates(store, app_paths, {version})
    mine = [c for c in candidates if c.event_id == row["event_id"]]
    home = next(c for c in mine if c.market_key == "1x2" and c.selection == "home")
    double = next(c for c in mine if c.market_key == "double_chance")
    assert home.prediction_id == pid and double.prediction_id == pid  # derived: same forecast
    other = next(c for c in candidates if c.prediction_id is None and c.event_id != home.event_id)
    # the builder (Kupon dnia): a leg with a forecast and a leg without one
    proposal = SimpleNamespace(
        profile="standard",
        label="STANDARDOWY",
        level="experimental",
        p_calibrated=0.4,
        p_conservative=0.35,
        joint_basis="test",
        total_odds=home.odds * other.odds,
        stake=Decimal("10.00"),
        payout=Decimal("20.00"),
        net_profit=Decimal("10.00"),
        ev_net=Decimal(0),
        legs=[home, other],
        reasons=[],
        warnings=[],
    )
    saved = coupon_store.save_proposals(
        store,
        app_paths,
        SimpleNamespace(proposals={"standard": proposal}),
        analysis_id=None,
        now=NOON,
        day=DAY,
    )["standard"]
    assert saved["relation"] == "official"
    legs = _legs(store)
    assert legs[home.odds_id] == pid and legs[other.odds_id] is None
    payload = json.loads(store.get("coupons", saved["coupon_id"])["proposal_json"])
    assert [leg["prediction_id"] for leg in payload["legs"]] == [pid, None]
    # a NEWER forecast of the same selection: the same identity, no second coupon
    newer = _prediction(store, version, row["event_id"], NOON - timedelta(minutes=5), 0.62)
    again = _candidates(store, app_paths, {version})
    home2 = next(c for c in again if c.selection_id == home.selection_id)
    assert home2.prediction_id == newer != pid
    other2 = next(c for c in again if c.selection_id == other.selection_id)
    second = coupon_store.save_proposals(
        store,
        app_paths,
        SimpleNamespace(proposals={"standard": replace_legs(proposal, [home2, other2])}),
        analysis_id=None,
        now=NOON + timedelta(minutes=1),
        day=DAY,
    )["standard"]
    assert second["relation"] == "duplicate" and second["coupon_id"] == saved["coupon_id"]
    assert store.scalar("SELECT COUNT(*) FROM coupons") == 1
    sts = coupon_store.load_profiles(app_paths.config)["sts"]
    # Diagnostyka (one candidate) and Historia (the chosen odds of the day's candidates)
    double2 = next(c for c in again if c.selection_id == double.selection_id)
    for mode, picks in (("paper_system", [double2]), ("paper_manual", [home2, other2])):
        ids = [x.odds_id for x in picks]
        first = coupon_store.save_manual(
            store,
            odds_ids=ids,
            stake=Decimal("10"),
            mode=mode,
            sts=sts,
            now=NOON,
            prediction_ids=coupon_store.prediction_ids_of([x for x in again if x.odds_id in ids]),
        )
        assert first.relation == "user"
        click = coupon_store.save_manual(  # a double click is still one coupon
            store,
            odds_ids=ids,
            stake=Decimal("10"),
            mode=mode,
            sts=sts,
            now=NOON + timedelta(seconds=1),
            prediction_ids=coupon_store.prediction_ids_of(picks),
        )
        assert click.relation == "duplicate" and click.coupon_id == first.coupon_id
        stored = {
            r["odds_id"]: r["prediction_id"]
            for r in store.rows(
                "SELECT odds_id, prediction_id FROM coupon_legs WHERE coupon_id=?",
                (first.coupon_id,),
            )
        }
        assert stored == {x.odds_id: x.prediction_id for x in picks}
    # Mój kupon (and the cart of Tasiemka / Kupon na kurs, which only fill Mój kupon)
    cart = [my_coupon.leg_from(home2, day=DAY, now=NOON)]
    result = coupon_store.save_my_coupon_result(
        store, app_paths, cart, [home2], stake=Decimal("7"), now=NOON
    )
    assert (
        store.rows("SELECT prediction_id FROM coupon_legs WHERE coupon_id=?", (result.coupon_id,))[
            0
        ]["prediction_id"]
        == newer
    )
    # every interface path passes the ids (static check of the saving calls)
    app = SRC / "sports_edge" / "app"
    for module in ("diagnostics.py", "pages_history.py"):
        assert "prediction_ids=prediction_ids_of(" in (app / module).read_text(encoding="utf-8")
    store_source = (SRC / "sports_edge" / "coupon_store.py").read_text(encoding="utf-8")
    assert store_source.count("prediction_ids=prediction_ids_of(") == 2  # builder + Mój kupon


def replace_legs(proposal, legs):
    from types import SimpleNamespace

    return SimpleNamespace(**{**vars(proposal), "legs": legs})


def test_the_forecast_id_never_enters_the_coupon_identity():
    one = coupon_store.coupon_identity(
        source="kreator", profile="standard", day=DAY, policy_version="p", selection_ids=["a"]
    )
    assert "prediction" not in coupon_store.coupon_identity.__code__.co_varnames
    assert one == coupon_store.coupon_identity(
        source="kreator", profile="standard", day=DAY, policy_version="p", selection_ids=["a"]
    )


# 4 ---------------------------------------------------------------- risk of the day (§4.2)

OLD = {"lambda": 1.5, "mu": 1.1, "rho": -0.05}
NEW = {"lambda": 2.4, "mu": 0.6, "rho": 0.0}


def _goals_forecast(store, version, event, market_key, line, created, rates):
    pid = digest(["task013-goals", version, event, market_key, line, iso(created)])
    store.insert(
        "predictions",
        id=pid,
        model_version_id=version,
        event_id=event,
        market_key=market_key,
        period="FT",
        line=line,
        created_at=iso(created),
        probabilities_json=canonical({"over": 0.7, "under": 0.3}),
        uncertainty_json="{}",
        inputs_json=canonical({"score_matrix": rates, "competition": "en.1"}),
        kind="prospective",
    )
    return pid


def _risk_leg(event, market_key, line, selection, q, version, prediction_id=None):
    leg = {
        "event_id": event,
        "event": f"Mecz {event[:4]}",
        "market_key": market_key,
        "line": line,
        "selection": selection,
        "p_conservative": q,
        "model_version_id": version,
    }
    if prediction_id:
        leg["prediction_id"] = prediction_id
    return leg


def test_risk_of_the_day_uses_the_forecast_of_the_leg_not_the_newest_one(store):
    from sports_edge.app.page_coupon import risk_numbers

    version = _version(store, pe.GOALS, "dc-task013")
    start = datetime(2026, 11, 6, 18, tzinfo=UTC)
    event = _event(store, 1300, start, _import(store, "task013-risk", start - timedelta(days=1)))
    built_at, later = start - timedelta(hours=8), start - timedelta(hours=2)
    over = _goals_forecast(store, version, event, "total", "1.5", built_at, OLD)
    scores = _goals_forecast(store, version, event, "home_to_score", None, built_at, OLD)
    _goals_forecast(store, version, event, "total", "1.5", later, NEW)  # newer, other λ μ ρ
    matrix = score_matrix(OLD["lambda"], OLD["mu"], OLD["rho"])
    p_home = float(matrix[1:, :].sum())
    p_over = float(sum(matrix[i, j] for i in range(11) for j in range(11) if i + j > 1.5))
    proposals = {
        "a": {"legs": [_risk_leg(event, "total", "1.5", "over", p_over, version, over)]},
        "b": {"legs": [_risk_leg(event, "home_to_score", None, "yes", p_home, version, scores)]},
    }
    rates = insights.matrix_rates(store, proposals)
    assert rates == {insights.prediction_key(over): OLD, insights.prediction_key(scores): OLD}
    found = insights.day_risk(proposals, rates)
    joint = float(matrix[1:, :].sum() - matrix[1, 0])  # home scores AND 2+ goals
    assert found["joint"][event]["method"] == "matrix" and found["joint"][event]["source"] == "leg"
    assert math.isclose(found["p_all_win"], joint, rel_tol=1e-9)
    assert found["fallback_matches"] == 0
    assert "prognoza nogi kuponu" in " ".join(risk_numbers(proposals, rates))
    # old legs WITHOUT the forecast id: the newest forecast of the version, marked fallback
    old = {
        k: {"legs": [{key: v for key, v in p["legs"][0].items() if key != "prediction_id"}]}
        for k, p in proposals.items()
    }
    fallback_rates = insights.matrix_rates(store, old)
    assert fallback_rates == {(event, version): NEW}
    found_old = insights.day_risk(old, fallback_rates)
    assert found_old["joint"][event]["source"] == "fallback" and found_old["fallback_matches"] == 1
    assert "FALLBACK" in " ".join(risk_numbers(old, fallback_rates))
    # two legs of one match from forecasts with different parameters: independence, said so
    mixed = {
        "a": proposals["a"],
        "b": {
            "legs": [
                _risk_leg(
                    event,
                    "home_to_score",
                    None,
                    "yes",
                    p_home,
                    version,
                    _goals_forecast(store, version, event, "home_to_score", None, later, NEW),
                )
            ]
        },
    }
    other = insights.day_risk(mixed, insights.matrix_rates(store, mixed))
    assert other["joint"][event]["method"] == "independent"
    assert "różnych prognoz" in other["joint"][event]["reason"]


def test_a_coupon_review_names_the_source_of_the_leg_chances():
    from sports_edge.forecast_quality import coupon_review, forecast_source

    legs = [{"event": "A - B", "p_model": 0.8, "outcome": "loss", "prediction_id": "p1"}]
    review = coupon_review({"proposal": {"legs": legs, "probability": {}}, "outcome": "loss"})
    assert any("prognoza zapisana przy każdej nodze" in line for line in review)
    assert "FALLBACK" in forecast_source([{"p_model": 0.8}])
    assert "1 z 2" in forecast_source([{"prediction_id": "x"}, {}])


# 5 ---------------------------------------------------------------- "najbliżej" (§5.3)


def test_closest_is_the_highest_conservative_chance_inside_the_odds_range(
    store, import_sts, app_paths
):
    from sports_edge.probability_lab import load_probability_policy

    row, version, pid = _forecast_world(store, import_sts, app_paths)
    base = next(
        c
        for c in _candidates(store, app_paths, {version})
        if c.prediction_id == pid and c.market_key == "1x2" and c.selection == "home"
    )
    rules = load_probability_policy(ROOT / "config" / "probability_policy.toml")
    profile = rules.profiles["high_hit"]
    before = {k: profile[k] for k in ("min_leg_probability", "min_coupon_probability")}
    pool = [
        replace(base, odds=Decimal("1.40"), p_low=0.71, p_model=0.78, selection_id="s1", key="1"),
        replace(base, odds=Decimal("1.55"), p_low=0.73, p_model=0.80, selection_id="s2", key="2"),
        replace(base, odds=Decimal("1.90"), p_low=0.90, p_model=0.92, selection_id="s3", key="3"),
        replace(base, odds=Decimal("1.30"), p_low=0.95, freshness="unconfirmed", key="4"),
    ]
    found, excluded = closest(pool, profile, max_odds=4.0)
    assert found.selection_id == "s2"  # 1.90 is outside 1.25–1.60; the stale one is not eligible
    assert excluded == {"kurs niepotwierdzony (stara migawka)": 1}
    text = insights.closest_text(pool, profile, max_odds=4.0)
    assert text.startswith("najbliżej: ") and "@ 1,55" in text and "ostrożnie 73,0%" in text
    assert "zwalidowany predykcyjnie" in text
    # read only: the proposals and thresholds are those of the builder alone
    built = build_coupons(pool, rules, cost=_cost(), on_date=NOON.date())
    assert built.proposals["high_hit"] is None
    assert (
        {k: profile[k] for k in before}
        == before
        == {
            "min_leg_probability": 0.75,
            "min_coupon_probability": 0.65,
        }
    )
    assert insights.closest_text([], profile, max_odds=4.0) == (
        "najbliżej: brak kandydatów w przedziale kursu"
    )
    stale = insights.closest_text(pool[3:], profile, max_odds=4.0)
    assert "brak kandydatów w przedziale kursu" in stale and "kurs niepotwierdzony" in stale


def _cost():
    from sports_edge.bookmakers import load_profiles

    return load_profiles(ROOT / "config")["sts"].cost


def test_the_high_hit_diagnosis_measures_without_saving(store, import_sts, app_paths):
    from sports_edge import simulation
    from sports_edge.settings import load_settings

    _row, version, _pid = _forecast_world(store, import_sts, app_paths)
    tables = {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("coupons", "predictions")}
    found = simulation.high_hit_diagnosis(
        store,
        app_paths,
        load_settings(app_paths),
        [DAY],
        hours=(13, 14),
        versions_at=lambda t: {version},
    )
    assert found["decisions"] == 2 and sum(found["codes"].values()) == 2
    assert set(found["codes"]) <= {
        "coupon",
        "no_candidates",
        "not_validated",
        "low_probability",
        "payout",
        "coupon_probability",
    }
    assert all(d["closest"].startswith("najbliżej:") for d in found["per_decision"])
    assert "nie wdrożone" in found["what_if_elo_double_chance_one_interval"]["note"]
    assert {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in tables} == tables


# 6 ---------------------------------------------------------------- the v3 gate (§6)


def test_v3_activation_is_blocked_until_the_user_accepts_the_gate(store, app_paths):
    v2 = _version(store, "elo_ordered_draw", "elo-v2-champion")
    v3 = _version(
        store,
        "elo_ordered_draw",
        "elo-v3-challenger",
        validation={"protocol": "v3", "competitions": list(services.CORE_COMPETITIONS)},
    )
    learning.record(
        store,
        kind="activated",
        actor="system",
        reason="test",
        now=datetime(2026, 11, 1, tzinfo=UTC),
        version_id=v2,
        scope="core",
        family=services.ELO_FAMILY,
    )
    assert learning.gate_state(store)["accepted"] is False  # no decision: NOT accepted
    versions = {services.ELO_FAMILY: v3}
    assert services.activation_block(store, "core", versions) == learning.GATE_WAITING
    with pytest.raises(ValueError, match="metodologia v3 czeka na Twoją akceptację"):
        learning.activate(store, scope="core", versions=versions, now=FAR, reason="test")
    with pytest.raises(ValueError, match="Aktywacja zablokowana"):
        services.switch_models(
            store, app_paths, scope="core", versions=versions, now=FAR, reason="test"
        )
    # a v2 version is not gated
    assert services.activation_block(store, "core", {services.ELO_FAMILY: v2}) is None
    # the user's acceptance is a new row; activation then still needs shadow evidence
    first = learning.accept_gate(
        store, now=datetime(2026, 11, 7, 10, tzinfo=UTC), shadow_matches=300
    )
    state = learning.gate_state(store)
    assert state["accepted"] and state["decision"]["id"] == first
    assert (
        state["decision"]["actor"] == "user"
        and "≥ 300 wspólnych meczów" in state["decision"]["reason"]
    )
    assert "za mało dowodów w trybie cienia (0/300" in services.activation_block(
        store, "core", versions
    )
    # withdrawing is another row: nothing is overwritten
    learning.withdraw_gate(store, now=datetime(2026, 11, 7, 11, tzinfo=UTC))
    kinds = [e["kind"] for e in learning.events(store) if e["kind"] in learning.GATE_KINDS]
    assert kinds == ["protocol_gate_accepted", "protocol_gate_withdrawn"]
    assert learning.gate_state(store)["accepted"] is False
    assert store.get("model_events", first)["kind"] == "protocol_gate_accepted"  # still there
    assert services.scope_version(store, services.CORE_COMPETITIONS)["version_id"] == v2


def test_the_system_never_accepts_the_gate_nor_activates_a_model():
    calls = []
    for path in (SRC / "sports_edge").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "accept_gate(" in text:
            calls.append(path.relative_to(SRC).as_posix())
    # defined in learning, called ONLY by the user's button in the Models panel — TASK 015:
    # also by ``decisions.decide_gate``, which only the button of the panel "Decyzje do
    # podjęcia" calls (checked in test_task015)
    assert sorted(calls) == [
        "sports_edge/app/page_learning.py",
        "sports_edge/decisions.py",
        "sports_edge/learning.py",
    ]
    source = (SRC / "sports_edge" / "learning.py").read_text(encoding="utf-8")
    assert source.count('kind="protocol_gate_accepted",\n        actor="user"') == 1
    for module in ("rolling.py", "retraining.py", "automation.py"):
        text = (SRC / "sports_edge" / module).read_text(encoding="utf-8")
        assert 'kind="activated"' not in text and "learning.activate(" not in text


# 7 ---------------------------------------------------------------- odds age (§7)


def test_odds_age_after_the_match_day_mode_is_measured_or_insufficient(store):
    now = datetime(2026, 11, 7, 20, tzinfo=UTC)
    empty = pe.odds_age_report(store, now=now)
    assert empty["state"] == "insufficient" and empty["matches"] == 0
    assert empty["label"] == "za mało danych (0 dni z pobraniami w dniu meczu; potrzeba 3)"
    assert empty["median_hours"] is None and empty["within_3h"] is None  # no data ≠ 0
    ages = []
    for n, day in enumerate(("2026-11-03", "2026-11-04", "2026-11-05")):
        start = datetime.fromisoformat(f"{day}T18:00:00+00:00")
        early = _import(store, f"age-early-{n}", start - timedelta(hours=20))
        late = _import(store, f"age-late-{n}", start - timedelta(hours=1 + n))
        event = _event(store, 1400 + n, start, early)
        market, selections = _market(store, event, "1x2", None, ("home", "draw", "away"))
        prices = {"home": 2.0, "draw": 3.2, "away": 3.9}
        _quote(store, market, selections, early, start - timedelta(hours=20), prices)
        _quote(store, market, selections, late, start - timedelta(hours=1 + n), prices)
        ages.append(1 + n)
        after = _import(store, f"age-after-{n}", start + timedelta(minutes=5))
        _quote(store, market, selections, after, start + timedelta(minutes=5), prices)  # ignored
        done = start - timedelta(hours=1 + n)
        record_job(
            store,
            "auto:sts_offer",
            trigger="schedule",
            started=done - timedelta(minutes=1),
            finished=done,
            status="ok",
            payload={"matchday": True, "new": 1},
        )
    found = pe.odds_age_report(store, now=now)
    assert found["state"] == "measured" and found["matchday_days"] == 3
    assert found["matches"] == 3 and math.isclose(found["median_hours"], 2.0)
    assert math.isclose(found["within_3h"], 1.0) and found["matchday_downloads"] == 3
    assert (
        found["by_mode"]["matchday"]["matches"] == 3 and found["by_mode"]["other"]["matches"] == 0
    )
    day = next(d for d in found["per_day"] if d["day"] == "2026-11-04")
    assert day["downloads"] == 1 and day["documents"] >= 2 and day["median_hours"] == 2.0
    # the weekly report writes the numbers; the same state adds no second entry
    weekly = pe.weekly_report(store, now=now)
    assert weekly["recorded"] and weekly["odds_age"]["median_hours"] == 2.0
    entry = pe.latest_weekly_report(store)["payload"]["odds_age"]
    assert entry["state"] == "measured" and entry["matches"] == 3
    assert not pe.weekly_report(store, now=now)["recorded"]
    del ages


def test_the_daily_cap_is_reported_when_it_cuts_slots(store):
    now = datetime(2026, 11, 2, 20, tzinfo=UTC)
    first = _import(store, "cap", datetime(2026, 11, 1, 6, tzinfo=UTC))
    for n, hour in enumerate(range(8, 22)):  # 14 groups of matches on Sunday 01.11
        _event(store, 1500 + n, datetime(2026, 11, 1, hour, 30, tzinfo=UTC), first)
    found = pe.odds_age_report(store, now=now, days=2)
    sunday = next(d for d in found["per_day"] if d["day"] == "2026-11-01")
    assert sunday["slots_uncapped"] > 12 and sunday["slots_capped"] == 12 and sunday["cap_cut"]
    assert "2026-11-01" in found["cap_cut_days"]
    assert sunday["matches"] == 0 and sunday["without_price"] == 14
    assert sunday["median_hours"] is None  # no price before the start: no number, not 0


# 8 ---------------------------------------------------------------- schema (§0)


def test_no_schema_change_is_needed(tmp_path):
    """TASK 013 adds no table: the gate decision lives in model_events, the forecast id in
    coupon_legs.prediction_id, the odds age in the weekly report entry (schema stays v10)."""
    from sports_edge.storage import SCHEMA_VERSION, Store

    assert SCHEMA_VERSION >= 10  # TASK 015: v11 adds user_decisions (TASK 013 added none)
    db = Store(tmp_path / "x.db")
    columns = {r[1] for r in db.connection.execute("PRAGMA table_info(coupon_legs)")}
    assert "prediction_id" in columns
    learning.accept_gate(db, now=datetime(2026, 11, 7, tzinfo=UTC), shadow_matches=300)
    with pytest.raises(sqlite3.DatabaseError):
        db.connection.execute("DELETE FROM model_events")
    with pytest.raises(sqlite3.DatabaseError):
        db.connection.execute("UPDATE model_events SET actor='system'")
    db.close()


# 9 ---------------------------------------------------------------- interface (AppTest)


def test_kupon_dnia_shows_the_closest_line_and_models_show_the_gate(app_paths):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from sports_edge import qa

    loader = AppTest.from_string(
        "from sports_edge.app import common as c; c.load_demo_data()", default_timeout=180
    )
    loader.session_state["demo_mode"] = True
    loader.run()
    coupon = AppTest.from_string(qa._page("kupon"), default_timeout=180)
    coupon.session_state["demo_mode"] = True
    coupon.run()
    assert not coupon.exception, [e.value for e in coupon.exception]
    lines = [str(x.value) for x in coupon.caption]
    assert any("WYSOKA TRAFNOŚĆ — najbliżej:" in line for line in lines)
    models = AppTest.from_string(
        "from sports_edge.app.page_models import models_page\nmodels_page()\n",
        default_timeout=180,
    )
    models.session_state["demo_mode"] = True
    models.session_state["models_view"] = "Uczenie AI"
    models.run()
    assert not models.exception, [e.value for e in models.exception]
    text = " ".join(m.value for m in models.markdown)
    assert "Bramka protokołu v3" in text and "metodologia v3 czeka na Twoją akceptację" in text
    box = next(b for b in models.checkbox if b.label.startswith("Akceptuję bramkę v3"))
    box.check().run()
    next(b for b in models.button if b.label == "Zapisz akceptację bramki v3").click().run()
    assert not models.exception, [e.value for e in models.exception]
    assert "Bramka v3 zaakceptowana przez Ciebie" in " ".join(m.value for m in models.markdown)
    from sports_edge.storage import Store

    store = Store(app_paths.demo_database)  # the DEMO session writes to the DEMO database
    rows = store.rows("SELECT actor, kind FROM model_events WHERE kind LIKE 'protocol_gate%'")
    store.close()
    assert rows == [{"actor": "user", "kind": "protocol_gate_accepted"}]
    basic = AppTest.from_string(
        "from sports_edge.app.page_models import models_page\nmodels_page()\n",
        default_timeout=180,
    )
    basic.session_state["demo_mode"] = True
    basic.run()
    assert not basic.exception, [e.value for e in basic.exception]
    assert "Bramka v3 zaakceptowana" in " ".join(m.value for m in basic.markdown)
