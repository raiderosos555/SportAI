"""TASK 015 — the evidence clock and the decisions in one place: identical challengers expire
at once, the first v3 challenger needs no 150 new matches, a one-off review after the start of
the worker, n/300 with an estimated date, the panel "Decyzje do podjęcia" with an append-only
history, option D as a switch (default unchanged) and the age of today's STS prices.

Every team, price, forecast and result below is SYNTHETIC test data, not a real fact.
"""

import json
import os
import shutil
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from conftest import ROOT, SRC
from test_models_data import load, synthetic
from test_task011 import _event, _import, _market, _quote, _version
from test_task013 import DAY, NOON, _candidates, _forecast_world

from sports_edge import (
    automation,
    coupon_store,
    decisions,
    learning,
    retraining,
    services,
    simulation,
)
from sports_edge import prospective_eval as pe
from sports_edge.analysis.coupon_builder import build_coupons
from sports_edge.analysis.edge_engine import (
    BOUND_SINGLE,
    BOUND_SUM,
    derive_from_1x2,
    policy_version_with_bound,
)
from sports_edge.collection import record_job
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.settings import AppPaths, load_settings, save_settings

NOW = datetime(2026, 11, 8, 10, tzinfo=UTC)
CORE = list(services.CORE_COMPETITIONS)
DECISION = {"n": 1752, "log_loss": 0.98091, "brier": 0.5846, "ece": 0.01788}
HYPER = {"k": 15.0, "home_advantage": 40.0, "draw_margin": 0.25}
SPLIT = {"protocol": "v2", "selection_from": "2023-07-01", "holdout_from": "2024-07-01"}


def _report(**extra) -> dict:
    return {
        "protocol": "v2",
        "competitions": CORE,
        "split": SPLIT,
        "decision": DECISION,
        "hyperparameters": HYPER,
        "calibration": {"ece": 0.0179, "reliability": []},
        "holdout": {"n": 3927, "log_loss": 0.9875},
        **extra,
    }


def _champion(store, name="elo-champion", family=services.ELO_FAMILY, validation=None):
    version = _version(
        store, family, name, created=NOW - timedelta(days=7), validation=validation or _report()
    )
    learning.record(
        store,
        kind="activated",
        actor="user",
        reason="test",
        now=NOW - timedelta(days=6),
        version_id=version,
        scope="core",
        family=family,
    )
    return version


def _waiting(store, name, validation, *, ready=NOW - timedelta(days=2), family=None):
    family = family or services.ELO_FAMILY
    version = _version(store, family, name, created=ready, validation=validation)
    learning.record(
        store,
        kind="challenger_ready",
        actor="system",
        reason="test",
        now=ready,
        version_id=version,
        scope="core",
        family=family,
    )
    return version


# 1 ---------------------------------------------------------------- expiry of twins (§2.1)


def test_an_identical_challenger_expires_at_once_and_a_different_one_does_not(store):
    champion = _champion(store)
    # the same model: only the v2 holdout grew by matches played after the champion
    twin = _waiting(store, "elo-twin", _report(holdout={"n": 3936, "log_loss": 0.98774}))
    before = {r["id"]: r for r in store.rows("SELECT * FROM model_events")}
    found = retraining.expire_challengers(store, "core", now=NOW)
    assert [(f["version_id"], f["identical"]) for f in found] == [(twin, True)]
    [event] = [e for e in learning.events(store) if e["kind"] == "expired"]
    assert event["model_version_id"] == twin and event["actor"] == "system"
    assert event["reason"].startswith("Wygasły: identyczny z championem (ten sam protokół v2")
    assert "n 3927 → 3936" in event["reason"] and event["payload"]["identical"] is True
    after = {r["id"]: r for r in store.rows("SELECT * FROM model_events")}
    assert all(after[k] == v for k, v in before.items())  # old rows unchanged
    assert len(after) == len(before) + 1  # one new row, nothing activated
    assert learning.champion_id(store, "core", services.ELO_FAMILY) == champion
    assert retraining.expire_challengers(store, "core", now=NOW + timedelta(hours=1)) == []
    # a challenger with other hyperparameters (or other calibration maps) stays waiting
    other = _waiting(store, "elo-other", _report(hyperparameters={**HYPER, "k": 30.0}))
    maps = _waiting(
        store,
        "dc-other",
        _report(calibration={"ece": 0.02, "reliability": []}),
        family=services.GOALS_FAMILY,
    )
    assert retraining.expire_challengers(store, "core", now=NOW) == []
    assert learning.challengers(store, "core", services.ELO_FAMILY) == [other]
    assert learning.challengers(store, "core", services.GOALS_FAMILY) == [maps]
    assert not [
        e for e in learning.events(store) if e["kind"] == "activated" and e["actor"] == "system"
    ]


def test_identity_needs_the_same_protocol_and_equal_shadow_forecasts(store):
    champion = _champion(store)
    v3 = _waiting(store, "elo-v3", _report(protocol="v3"))
    assert retraining.expire_challengers(store, "core", now=NOW) == []  # another protocol
    assert learning.challengers(store, "core", services.ELO_FAMILY) == [v3]
    # the same report, but stored forecasts on the same data state differ: not identical
    twin = _version(store, services.ELO_FAMILY, "elo-twin2", validation=_report())
    rows = {r["version_id"]: r for r in services.model_overview(store)}
    first = _import(store, "identity", NOW - timedelta(hours=3))
    event = _event(store, 1650, NOW + timedelta(hours=3), first)
    for version, p in ((champion, 0.50), (twin, 0.55)):
        store.insert(
            "predictions",
            id=digest(["p", version]),
            model_version_id=version,
            event_id=event,
            market_key="1x2",
            period="FT",
            line=None,
            created_at=iso(NOW),
            probabilities_json=canonical({"home": p, "draw": 0.3, "away": 0.7 - p}),
            uncertainty_json="{}",
            inputs_json=canonical({"data_snapshot": "same"}),
            kind="prospective",
        )
    assert retraining.identical_to_champion(store, rows[champion], rows[twin]) is None
    clean = _version(store, services.ELO_FAMILY, "elo-twin3", validation=_report())
    rows = {r["version_id"]: r for r in services.model_overview(store)}
    assert "identyczny" in retraining.identical_to_champion(store, rows[champion], rows[clean])


# 2 ---------------------------------------------------------------- the first v3 (§2.2)


@pytest.fixture(scope="module")
def clock(tmp_path_factory):
    """A v2 champion trained on 20.09.2026, an identical v2 twin from 06.11 waiting, 34 new
    SYNTHETIC matches since the champion (< 150) — the state of the real database on 08.11."""
    home = tmp_path_factory.mktemp("task015")
    shutil.copytree(ROOT / "config", home / "config")
    previous = os.environ.get("SPORTS_EDGE_HOME")
    os.environ["SPORTS_EDGE_HOME"] = str(home)
    paths = AppPaths(home)
    store = services.open_store(paths)
    load(store, synthetic(2490))  # one match a day, 2020-01-01 … 2026-10-24
    champion = services.run_protocol_v2(
        store, paths, now=datetime(2026, 9, 20, tzinfo=UTC), scope="core"
    )
    services.ensure_model_roles(store, now=datetime(2026, 9, 20, 1, tzinfo=UTC))
    twin = services.run_protocol_v2(store, paths, now=NOW - timedelta(days=2), scope="core")
    before = {e["id"] for e in learning.events(store)}
    found = retraining.review(store, paths, now=NOW, scopes=("core",), trigger="first_v3")
    yield {
        "store": store,
        "paths": paths,
        "champion": champion,
        "twin": twin,
        "found": found,
        "before": before,
    }
    store.close()
    if previous is None:
        os.environ.pop("SPORTS_EDGE_HOME", None)
    else:
        os.environ["SPORTS_EDGE_HOME"] = previous


def test_the_first_v3_challenger_needs_no_150_new_matches_and_runs_in_shadow(clock):
    store, found = clock["store"], clock["found"]["core"]
    champion, twin = clock["champion"], clock["twin"]
    core = services.CORE_COMPETITIONS
    assert found["status"] == "trained" and found["first_v3"] is True
    assert (
        retraining.new_matches(
            store, core, since=services.scope_version(store, core)["trained_through"], now=NOW
        )
        < retraining.MIN_NEW_RESULTS
    )  # fewer than 150 new matches — trained anyway
    # the identical v2 twin expired first (same protocol, same numbers)
    expired = {e["model_version_id"]: e for e in learning.events(store) if e["kind"] == "expired"}
    assert {twin["elo"]["version_id"], twin["goals"]["version_id"]} <= set(expired)
    assert all(e["payload"]["identical"] for e in expired.values())
    # the new challengers are v3 with the TASK 013/014 rules; the champion stays served
    waiting = services.challenger_versions(store)["core"]
    elo, goals = (json.loads(waiting[k]["validation_json"]) for k in ("elo", "goals"))
    assert elo["protocol"] == goals["protocol"] == "v3"
    assert goals["half_life_rule"]["rule"] == "sticky12"
    assert goals["split"]["calibration_months"] == 24  # 5 leagues: 24-month maps (TASK 013)
    assert goals.get("team_total_calibrators")  # TASK 014: team totals calibrated
    assert services.scope_version(store, core)["version_id"] == champion["elo"]["version_id"]
    new = [e for e in learning.events(store) if e["id"] not in clock["before"]]
    assert {e["kind"] for e in new} == {"expired", "review", "challenger_ready"}
    assert any("pierwszy challenger v3" in e["reason"] for e in new if e["kind"] == "review")
    # the gate blocks the ACTIVATION only: the challenger is trained and in shadow mode
    assert learning.gate_state(store)["accepted"] is False
    versions = {services.ELO_FAMILY: waiting["elo"]["version_id"]}
    assert services.activation_block(store, "core", versions) == learning.GATE_WAITING
    with pytest.raises(ValueError, match="Aktywacja zablokowana"):
        services.switch_models(
            store, clock["paths"], scope="core", versions=versions, now=NOW, reason="test"
        )
    assert retraining.scope_status(store, "core", now=NOW) == "Challenger gotowy"


def test_the_second_v3_challenger_follows_the_usual_conditions(clock):
    store = clock["store"]
    assert retraining.has_v3(store, "core")
    again = retraining.review(store, clock["paths"], now=NOW + timedelta(hours=1), scopes=("core",))
    assert again["core"]["status"] == "skipped" and "first_v3" not in again["core"]
    reasons = " ".join(again["core"]["reasons"])
    assert "nowych unikalnych meczów od treningu" in reasons and "< 150" in reasons
    assert "czeka już na decyzję" in reasons


def test_without_a_new_match_the_150_rule_still_holds(store, app_paths):
    _champion(store)  # no results at all: nothing new since the champion
    found = retraining.training_conditions(store, "core", services.CORE_COMPETITIONS, now=NOW)
    assert found["first_v3"] is False and not found["ready"]
    assert any("< 150" in r for r in found["reasons"])


@pytest.mark.parametrize("new, fallback", [(40, False), (200, True)])
def test_without_feasible_v3_windows_no_v2_challenger_skips_the_usual_rules(
    store, app_paths, monkeypatch, new, fallback
):
    _champion(store)
    calls = []

    def not_feasible(*args, **kwargs):
        raise ValueError("Za mało meczów w oknach protokołu v3: test")

    monkeypatch.setattr(retraining, "new_matches", lambda *a, **k: new)
    monkeypatch.setattr(services, "run_protocol_v3", not_feasible)
    monkeypatch.setattr(
        services, "train_challengers", lambda *a, **k: calls.append("v2") or {"summary": {}}
    )
    found = retraining.review(store, app_paths, now=NOW, scopes=("core",))["core"]
    assert found["first_v3"] is True
    if fallback:  # the usual conditions hold too: the usual v2 fallback
        assert found["status"] == "trained" and calls == ["v2"]
    else:  # only the first-v3 waiver let it train: no v2 challenger without the rules
        assert found["status"] == "failed" and calls == []
        assert "oknach protokołu v3" in found["reasons"][0]


# 3 ---------------------------------------------------------------- one-off review (§2.3)


def _fake_runners(calls):
    def make(job):
        def run(store, paths, *, now, settings):
            calls.append(job)
            return automation.JobOutcome("ok", "", {"processed": 0})

        return run

    return {job: make(job) for job in automation.JOBS}


def test_the_first_review_is_planned_after_the_start_and_skipped_with_a_reason(app_paths):
    store = services.open_store(app_paths)
    _champion(store)
    calls = []
    clock = SimpleNamespace(now=NOW)
    worker = automation.Worker(app_paths, clock=lambda: clock.now, runners=_fake_runners(calls))
    assert worker.run(max_ticks=1, sleep=lambda s: None) == "zatrzymany"
    weekly = calls.count("review")  # the (fake) weekly review of the catch-up
    settings = automation.load_settings(store)
    [task] = [t for t in settings["one_off"] if t.get("variant") == automation.FIRST_REVIEW]
    at = datetime.fromisoformat(task["at"])
    assert NOW < at <= NOW + timedelta(hours=1) and task["job"] == "review"
    assert any(p.get("kind") == "jednorazowo" for p in automation.planned(store, settings, now=NOW))
    # a second start does not plan it twice while it is pending
    automation.Worker(app_paths, clock=lambda: clock.now, runners=_fake_runners(calls)).run(
        max_ticks=1, sleep=lambda s: None
    )
    pending = [
        t
        for t in automation.load_settings(store)["one_off"]
        if t.get("variant") == automation.FIRST_REVIEW
    ]
    assert len(pending) == 1
    # its time comes: offline (the default of a new home) — skipped with a reason
    clock.now = at + timedelta(minutes=1)
    automation.Worker(app_paths, clock=lambda: clock.now, runners=_fake_runners(calls)).run(
        max_ticks=1, sleep=lambda s: None
    )
    [run] = [h for h in automation.history(store) if h["trigger"] == "one_off"]
    assert run["job"] == "auto:review" and run["status"] == "skipped"
    assert "tryb offline" in run["reason"] and "v3" in run["reason"]
    assert calls.count("review") == weekly  # the first review is not the weekly job
    # online, but the training of challengers is off in the automation: skipped too
    save_settings(app_paths, replace(load_settings(app_paths), offline_mode=False))
    found = automation.job_first_review(
        store, app_paths, now=clock.now, settings=automation.load_settings(store)
    )
    assert found.status == "skipped" and "trening challengera jest wyłączony" in found.reason
    assert not [e for e in learning.events(store) if e["kind"] == "challenger_ready"]
    # nothing to do when every scope with a model already has a v3 version
    store.close()


def test_no_first_review_without_a_served_model(app_paths):
    store = services.open_store(app_paths)
    assert automation.plan_first_review(store, now=NOW) is None
    store.close()


# 4 ---------------------------------------------------------------- n/300 estimate (§2.4)


def _row(n, start, *, family="elo_ordered_draw", scope="core", market="1x2"):
    return {
        "role": "active",
        "family": family,
        "scope": scope,
        "market_key": market,
        "match": f"m{n}",
        "start_at": iso(start),
    }


def test_the_estimate_follows_the_pace_of_14_days_or_says_too_little_data():
    rows = [_row(n, NOW - timedelta(days=30) + timedelta(hours=12 * n)) for n in range(60)]
    found = retraining.evidence_estimate(
        rows, "core", "elo_ordered_draw", matches=20, needed=300, now=NOW
    )
    recent = [r for r in rows if NOW - timedelta(days=14) <= datetime.fromisoformat(r["start_at"])]
    assert found["state"] == "estimated" and found["pace_matches"] == len(recent) == 28
    per_day = 28 / 14  # 2 settled matches a day
    eta = (NOW + timedelta(days=(300 - 20) / per_day)).astimezone(retraining.WARSAW).date()
    assert found["eta"] == eta.isoformat() and found["per_week"] == pytest.approx(14.0)
    assert found["label"].startswith("szacunek: ok.") and "ostatnich 14 dni" in found["label"]
    # other scopes, families, double chance and shadow rows do not count
    extra = [
        _row(900, NOW - timedelta(days=1), scope="extended"),
        _row(901, NOW - timedelta(days=1), family="dixon_coles_goals"),
        _row(902, NOW - timedelta(days=1), market="double_chance"),
        {**_row(903, NOW - timedelta(days=1)), "role": "shadow"},
    ]
    assert (
        retraining.evidence_estimate(
            rows + extra, "core", "elo_ordered_draw", matches=20, needed=300, now=NOW
        )["pace_matches"]
        == 28
    )
    # a record younger than 14 days: its own span, named
    young = [_row(n, NOW - timedelta(days=4) + timedelta(hours=12 * n)) for n in range(8)]
    short = retraining.evidence_estimate(
        young, "core", "elo_ordered_draw", matches=0, needed=300, now=NOW
    )
    assert (
        short["span_days"] == pytest.approx(4.0) and "od pierwszego rozliczenia" in short["label"]
    )
    # no settlement in 14 days: "za mało danych", never a guessed date
    old = [_row(n, NOW - timedelta(days=40 + n)) for n in range(5)]
    for given in ([], old):
        none = retraining.evidence_estimate(
            given, "core", "elo_ordered_draw", matches=0, needed=300, now=NOW
        )
        assert none["state"] == "insufficient" and none["eta"] is None
        assert none["label"].startswith("za mało danych")
    reached = retraining.evidence_estimate(
        rows, "core", "elo_ordered_draw", matches=300, needed=300, now=NOW
    )
    assert reached["state"] == "reached"


# 5 ---------------------------------------------------------------- option D (§3.2)


def test_the_option_d_bound_is_the_complement_of_the_excluded_upper_bound():
    prediction = {
        "probabilities": {"home": 0.60, "draw": 0.24, "away": 0.16},
        "uncertainty": {
            "interval": {"home": [0.55, 0.65], "draw": [0.20, 0.28], "away": [0.13, 0.19]}
        },
    }
    today = derive_from_1x2(prediction, "double_chance")
    assert today["uncertainty"]["interval"]["home_draw"][0] == pytest.approx(0.55 + 0.20)
    assert today["uncertainty"]["bound_definition"] == BOUND_SUM
    single = derive_from_1x2(prediction, "double_chance", bound=BOUND_SINGLE)
    interval = single["uncertainty"]["interval"]
    assert interval["home_draw"][0] == pytest.approx(1 - 0.19)  # 1 − upper bound of "away"
    assert interval["draw_away"][0] == pytest.approx(1 - 0.65)
    assert interval["home_away"][0] == pytest.approx(1 - 0.28)
    assert single["probabilities"] == today["probabilities"]  # chances unchanged
    assert interval["home_draw"][1] == today["uncertainty"]["interval"]["home_draw"][1]
    assert single["uncertainty"]["bound_definition"] == BOUND_SINGLE
    # exactly the measured what-if (TASK 013/014): never below the sum, never above the chance
    wide = {**prediction, "uncertainty": {"interval": {**prediction["uncertainty"]["interval"]}}}
    wide["uncertainty"]["interval"]["away"] = [0.0, 0.40]  # a very wide excluded outcome
    kept = derive_from_1x2(wide, "double_chance", bound=BOUND_SINGLE)
    assert kept["uncertainty"]["interval"]["home_draw"][0] == pytest.approx(0.75)  # the sum
    assert policy_version_with_bound("2026-11-07.p4", BOUND_SUM) == "2026-11-07.p4"
    assert policy_version_with_bound("2026-11-07.p4", BOUND_SINGLE) == "2026-11-07.p4+D"


def _settings(app_paths, **changes):
    found = replace(load_settings(app_paths), **changes)
    save_settings(app_paths, found)
    return found


def test_option_d_is_off_by_default_and_changes_no_number(store, import_sts, app_paths):
    row, version, pid = _forecast_world(store, import_sts, app_paths)
    assert load_settings(app_paths).elo_dc_bound == BOUND_SUM
    default = _candidates(store, app_paths, {version})
    explicit, _ = services.day_candidates_at(
        store,
        app_paths,
        replace(load_settings(app_paths), elo_dc_bound="sum"),
        DAY,
        now=NOON,
        versions={version},
    )
    assert [(c.key, c.p_model, c.p_low, c.p_high, c.tier) for c in default] == [
        (c.key, c.p_model, c.p_low, c.p_high, c.tier) for c in explicit
    ]
    double = next(
        c for c in default if c.event_id == row["event_id"] and c.selection == "home_draw"
    )
    assert double.p_low == pytest.approx((0.6 - 0.05) + (0.24 - 0.04))  # the sum, as before
    rules = services.coupon_rules(app_paths, load_settings(app_paths))
    cost = coupon_store.load_profiles(app_paths.config)["sts"].cost
    a = build_coupons(default, rules, cost=cost, on_date=NOON.date())
    b = build_coupons(explicit, rules, cost=cost, on_date=NOON.date())
    assert {k: (v.legs, v.p_conservative) if v else None for k, v in a.proposals.items()} == {
        k: (v.legs, v.p_conservative) if v else None for k, v in b.proposals.items()
    }
    # switched on (only in the settings of this test home): the complement of one interval
    _settings(app_paths, elo_dc_bound=BOUND_SINGLE)
    switched = _candidates(store, app_paths, {version})
    single = next(
        c for c in switched if c.event_id == row["event_id"] and c.selection == "home_draw"
    )
    assert single.p_low == pytest.approx(1 - (0.16 + 0.03))  # 1 − upper bound of "away"
    rows = {r["id"]: r for r in services.latest_predictions(store, as_of=NOON).values()}
    what_if = simulation.option_d([double], rows)[0]
    assert what_if.p_low == pytest.approx(single.p_low)  # the production = the measured what-if
    others = {c.key: c.p_low for c in default if c.market_key != "double_chance"}
    assert others == {c.key: c.p_low for c in switched if c.market_key != "double_chance"}


def _proposal(leg):
    return SimpleNamespace(
        profile="high_hit",
        label="WYSOKA TRAFNOŚĆ",
        level="validated_predictive",
        p_calibrated=leg.p_model,
        p_conservative=leg.p_low,
        joint_basis="test",
        total_odds=leg.odds,
        stake=Decimal("10.00"),
        payout=Decimal("13.00"),
        net_profit=Decimal("3.00"),
        ev_net=Decimal(0),
        legs=[leg],
        reasons=[],
        warnings=[],
    )


def test_a_change_during_the_day_is_a_revision_and_the_definition_is_stored(
    store, import_sts, app_paths
):
    row, version, _pid = _forecast_world(store, import_sts, app_paths)
    leg = next(
        c
        for c in _candidates(store, app_paths, {version})
        if c.event_id == row["event_id"] and c.selection == "home_draw"
    )
    first = coupon_store.save_proposals(
        store,
        app_paths,
        SimpleNamespace(proposals={"high_hit": _proposal(leg)}),
        analysis_id=None,
        now=NOON,
        day=DAY,
    )["high_hit"]
    assert first["relation"] == "official"
    stored = store.get("coupons", first["coupon_id"])
    payload = json.loads(stored["proposal_json"])
    assert payload["cautious_definition"]["elo_double_chance"] == BOUND_SUM
    assert (
        stored["policy_version"]
        == payload["policy_version"]
        == services.probability_policy(app_paths).version
    )  # the default changes no identity
    _settings(app_paths, elo_dc_bound=BOUND_SINGLE)
    leg2 = next(
        c for c in _candidates(store, app_paths, {version}) if c.selection_id == leg.selection_id
    )
    second = coupon_store.save_proposals(
        store,
        app_paths,
        SimpleNamespace(proposals={"high_hit": _proposal(leg2)}),
        analysis_id=None,
        now=NOON + timedelta(minutes=5),
        day=DAY,
        bound=BOUND_SINGLE,
    )["high_hit"]
    assert second["relation"] == "revision" and second["official_id"] == first["coupon_id"]
    lineage = coupon_store.lineage_of(store, second["coupon_id"])
    assert "inna wersja polityki lub definicji szansy ostrożnej" in lineage["reason"]
    revised = store.get("coupons", second["coupon_id"])
    assert revised["policy_version"].endswith("+D")
    assert json.loads(revised["proposal_json"])["cautious_definition"]["elo_double_chance"] == (
        BOUND_SINGLE
    )
    official = store.rows("SELECT coupon_id FROM coupon_lineage WHERE relation='official'")
    assert [r["coupon_id"] for r in official] == [first["coupon_id"]]  # still the official one


# 6 ---------------------------------------------------------------- the decisions panel (§3)


def test_every_decision_and_its_withdrawal_is_a_new_row(store, app_paths):
    settings = load_settings(app_paths)
    items = decisions.panel(store, app_paths, settings, now=NOW, compared={})
    assert [i["key"] for i in items] == list(decisions.KEYS)
    assert all(i["changes"] and i["evidence"] and i["risk"] for i in items)
    assert decisions.open_count(items) == 4  # nothing decided; challengers wait for nothing
    decisions.decide_option_d(store, app_paths, settings, value=BOUND_SINGLE, now=NOW)
    assert load_settings(app_paths).elo_dc_bound == BOUND_SINGLE
    decisions.decide_option_d(
        store, app_paths, load_settings(app_paths), value=BOUND_SUM, now=NOW + timedelta(minutes=1)
    )
    assert load_settings(app_paths).elo_dc_bound == BOUND_SUM
    decisions.decide_safe_solo(
        store, app_paths, load_settings(app_paths), enabled=True, now=NOW + timedelta(minutes=2)
    )
    assert load_settings(app_paths).enabled_profiles == ["safe_solo"]
    decisions.decide_limit(
        store, app_paths, load_settings(app_paths), value=1, now=NOW + timedelta(minutes=3)
    )
    assert load_settings(app_paths).max_profiles_per_selection == 1
    decisions.decide_gate(store, app_paths, accept=True, now=NOW + timedelta(minutes=4))
    decisions.decide_gate(store, app_paths, accept=False, now=NOW + timedelta(minutes=5))
    assert learning.gate_state(store)["accepted"] is False  # the newest decision holds
    rows = decisions.history(store)
    assert len(rows) == 6 and all(r["actor"] == "user" for r in rows)
    option = [r for r in rows if r["decision"] == decisions.OPTION_D]
    assert [(r["previous"], r["new"]) for r in option] == [
        ({"elo_dc_bound": "sum"}, {"elo_dc_bound": "single"}),
        ({"elo_dc_bound": "single"}, {"elo_dc_bound": "sum"}),  # the withdrawal: a new row
    ]
    gate = [e["kind"] for e in learning.events(store) if e["kind"] in learning.GATE_KINDS]
    assert gate == ["protocol_gate_accepted", "protocol_gate_withdrawn"]
    items = decisions.panel(store, app_paths, load_settings(app_paths), now=NOW, compared={})
    assert decisions.open_count(items) == 0
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        store.connection.execute("UPDATE user_decisions SET summary='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        store.connection.execute("DELETE FROM user_decisions")


def test_without_a_click_nothing_changes_and_settings_log_their_decisions(store, app_paths):
    settings = _settings(app_paths)
    before = app_paths.settings_file.read_bytes()
    tables = {
        t: store.scalar(f"SELECT COUNT(*) FROM {t}")
        for t in ("user_decisions", "model_events", "automation_settings")
    }
    for _ in range(2):
        decisions.panel(store, app_paths, settings, now=NOW)
    assert app_paths.settings_file.read_bytes() == before
    assert {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in tables} == tables
    # Ustawienia → "Zapisz ustawienia": a row only for the decisions that really changed
    after = replace(settings, enabled_profiles=["safe_solo"], nominal_stake="20.00")
    logged = decisions.log_settings_change(store, app_paths, settings, after, now=NOW)
    assert len(logged) == 1 and decisions.history(store)[0]["source"] == "Ustawienia"
    assert decisions.log_settings_change(store, app_paths, after, after, now=NOW) == []


def test_the_challenger_decision_opens_only_after_the_threshold():
    entry = {"challenger": {"protocol": "v3"}, "state": "waiting", "state_label": "czeka (12/300)"}
    item = decisions._challengers_item({"core": {"elo": entry}})
    assert not item["open"] and "12/300" in " ".join(item["evidence"])
    ready = {**entry, "state": "sufficient", "state_label": "dowody wystarczające"}
    assert decisions._challengers_item({"core": {"elo": ready}})["open"]


def test_the_system_never_takes_a_decision_by_itself():
    calls = {}
    for path in (SRC / "sports_edge").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in ("decide_gate(", "decide_option_d(", "decide_safe_solo(", "decide_limit("):
            if name in text:
                calls.setdefault(name, set()).add(path.relative_to(SRC).as_posix())
    # defined in decisions, called ONLY by the buttons of the panel
    for name, found in calls.items():
        assert found == {"sports_edge/decisions.py", "sports_edge/app/page_decisions.py"}, name
    assert "log_settings_change(" in (SRC / "sports_edge/app/pages_settings.py").read_text(
        encoding="utf-8"
    )
    # the background code never imports the decisions nor touches the option D setting
    for module in ("automation.py", "retraining.py", "rolling.py", "learning.py"):
        text = (SRC / "sports_edge" / module).read_text(encoding="utf-8")
        assert "import decisions" not in text and "elo_dc_bound" not in text, module
    writers = {
        path.relative_to(SRC).as_posix()
        for path in (SRC / "sports_edge").rglob("*.py")
        if "settings.elo_dc_bound =" in path.read_text(encoding="utf-8")
        or "replace(settings, elo_dc_bound=value)" in path.read_text(encoding="utf-8")
    }
    assert writers == {"sports_edge/decisions.py", "sports_edge/app/pages_settings.py"}


# 7 ---------------------------------------------------------------- odds age (§4)


def test_todays_odds_age_line_and_too_little_data(store):
    now = datetime(2026, 11, 8, 15, tzinfo=UTC)
    empty = pe.today_odds_age(store, now=now)
    assert empty["matches"] == 0 and empty["age_hours"] is None
    assert "brak meczów 12 lig" in empty["text"]
    report = pe.odds_age_report(store, now=now)
    assert report["state"] == "insufficient" and report["label"].startswith("za mało danych (0")
    first = _import(store, "today-early", datetime(2026, 11, 8, 6, tzinfo=UTC))
    late = _import(store, "today-late", datetime(2026, 11, 8, 13, 30, tzinfo=UTC))
    for n, hour in enumerate((14, 18)):
        event = _event(store, 1700 + n, datetime(2026, 11, 8, hour, tzinfo=UTC), first)
        market, selections = _market(store, event, "1x2", None, ("home", "draw", "away"))
        prices = {"home": 2.0, "draw": 3.2, "away": 3.9}
        _quote(store, market, selections, first, datetime(2026, 11, 8, 6, tzinfo=UTC), prices)
        _quote(store, market, selections, late, datetime(2026, 11, 8, 13, 30, tzinfo=UTC), prices)
    record_job(
        store,
        "auto:sts_offer",
        trigger="schedule",
        started=datetime(2026, 11, 8, 13, 29, tzinfo=UTC),
        finished=datetime(2026, 11, 8, 13, 30, tzinfo=UTC),
        status="ok",
        payload={"matchday": True},
    )
    found = pe.today_odds_age(store, now=now)
    assert found["matches"] == 2 and found["upcoming"] == 1
    assert found["age_hours"] == pytest.approx(1.5) and found["matchday_downloads"] == 1
    assert "1,5 h temu" in found["text"] and "1/12" in found["text"]
    report = pe.odds_age_report(store, now=now)
    assert report["matchday_days"] == 1 and report["label"].startswith("za mało danych (1 dzień")


# 8 ---------------------------------------------------------------- migration v10 → v11


def test_migration_to_v11_backs_up_and_keeps_every_row(tmp_path):
    from sports_edge.repository import ensure_reference_data
    from sports_edge.storage import SCHEMA_VERSION, Store

    path = tmp_path / "old.db"
    store = Store(path)
    ensure_reference_data(store)
    _champion(store)
    store.close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE user_decisions")
    raw.execute("PRAGMA user_version=10")
    tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {t: raw.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    raw.commit()
    raw.close()
    migrated = Store(path, backup_dir=tmp_path / "backups")
    assert SCHEMA_VERSION >= 11  # TASK 016: v12 adds quote_confirmations on top of v11
    assert migrated.migration_backup.name.startswith(f"pre-migration-v{SCHEMA_VERSION}_")
    assert migrated.migration_backup.exists()
    for table, n in counts.items():
        if table != "schema_migrations":
            assert migrated.scalar(f"SELECT COUNT(*) FROM {table}") == n, table
    assert "user_decisions" in migrated.tables
    note = migrated.rows(
        "SELECT description FROM schema_migrations WHERE version=?", (SCHEMA_VERSION,)
    )
    assert note and "v11 TASK 015" in note[-1]["description"]
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        migrated.connection.execute("DELETE FROM model_events")
    migrated.close()


# 9 ---------------------------------------------------------------- interface (AppTest)


def test_dashboard_shows_the_decisions_and_the_panel_has_all_six(app_paths):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    dashboard = AppTest.from_string(
        "from sports_edge.app.page_dashboard import dashboard_page; dashboard_page()",
        default_timeout=90,
    )
    dashboard.session_state["demo_mode"] = False
    dashboard.run()
    assert not dashboard.exception, dashboard.exception
    text = " ".join(m.value for m in dashboard.markdown)
    assert "Decyzje do podjęcia" in text and "otwarte: 4 z 6" in text
    models = AppTest.from_string(
        "from sports_edge.app.page_models import models_page; models_page()",
        default_timeout=90,
    )
    models.session_state["demo_mode"] = False
    models.query_params["widok"] = "Decyzje do podjęcia"
    models.run()
    assert not models.exception, models.exception
    shown = " ".join(m.value for m in models.markdown)
    assert all(title in shown for title in decisions.TITLES.values())
    # one decision through the interface: option D, behind the confirmation
    radio = next(r for r in models.radio if r.label.startswith("Definicja granicy"))
    radio.set_value(BOUND_SINGLE).run()
    button = next(b for b in models.button if b.label == "Zapisz decyzję o opcji D")
    assert button.disabled  # nothing without the confirmation
    next(b for b in models.checkbox if b.label.startswith("Potwierdzam definicję")).check().run()
    next(b for b in models.button if b.label == "Zapisz decyzję o opcji D").click().run()
    assert not models.exception, models.exception
    assert load_settings(app_paths).elo_dc_bound == BOUND_SINGLE
    store = services.open_store(app_paths)
    assert [r["decision"] for r in decisions.history(store)] == [decisions.OPTION_D]
    store.close()
