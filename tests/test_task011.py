"""TASK 011 — prospective reliability in the coupon zone, the STS market benchmark, challengers.

Every team, price, forecast and result below is SYNTHETIC test data, not a real fact.
"""

import itertools
import math
import sqlite3
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from conftest import ROOT

from sports_edge import insights, learning, retraining, services
from sports_edge import prospective_eval as pe
from sports_edge.ingestion.types import canonical, digest, iso

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
LEAGUE = "ANGLIA - PREMIER LEAGUE"  # en.1 → scope "core"
CORE = list(services.CORE_COMPETITIONS)
POLICY = pe.load_policy(ROOT / "config")


# ---------------------------------------------------------------- synthetic world


def _version(store, family, name, *, created=NOW - timedelta(days=30), validation=None):
    model_id = digest(["model", family, "football"])
    store.insert("models", id=model_id, family=family, sport="football", description="test")
    version_id = digest(["test-version", family, name])
    store.insert(
        "model_versions",
        id=version_id,
        model_id=model_id,
        version=name,
        created_at=iso(created),
        trained_through=iso(created),
        training_data_json=canonical({"competitions": CORE}),
        validation_json=canonical(validation or {"protocol": "v2", "competitions": CORE}),
        status="experimental",
    )
    return version_id


def _import(store, key: str, generated: datetime) -> str:
    import_id = digest(["test-import", key])
    store.insert(
        "imports",
        id=import_id,
        source_id="sts_pdf_manual",
        bookmaker_id="sts",
        parser="test",
        parser_verified=1,
        raw_sha256=sha256(key.encode()).hexdigest(),
        data_kind="prospective",
        offer_kind="today",
        generated_at=iso(generated),
        ingested_at=iso(generated),
        rows_total=1,
        rows_ok=1,
        rows_review=0,
        unparsed=0,
        quality=1.0,
        summary_json="{}",
    )
    return import_id


def _event(store, n: int, start: datetime, first_import: str) -> str:
    event_id = digest(["test-event", n])
    store.insert(
        "events",
        id=event_id,
        bookmaker_id="sts",
        sport="football",
        sport_raw="PIŁKA NOŻNA",
        league=LEAGUE,
        home=f"Home {n} FC",
        away=f"Away {n} FC",
        participants_json=canonical([f"Home {n} FC", f"Away {n} FC"]),
        name_raw=f"Home {n} FC - Away {n} FC",
        start_at=iso(start),
        offer_date=start.date().isoformat(),
        first_import_id=first_import,
    )
    return event_id


def _market(store, event_id: str, market_key: str, line: str | None, keys) -> tuple[str, dict]:
    market_id = digest(["test-market", event_id, market_key, line])
    store.insert(
        "markets",
        id=market_id,
        event_id=event_id,
        market_key=market_key,
        period="FT",
        overtime="false",
        line=line,
        label_raw=market_key,
        rules_ref="test",
        mapping_version="test",
    )
    selections = {}
    for key in keys:
        selections[key] = digest(["selection", market_id, key])
        store.insert("selections", id=selections[key], market_id=market_id, key=key, label_raw=key)
    return market_id, selections


def _quote(store, market_id, selections, import_id, valid_at: datetime, prices: dict) -> str:
    quote_id = digest(["test-quote", market_id, import_id])
    store.insert(
        "quote_sets",
        id=quote_id,
        market_id=market_id,
        import_id=import_id,
        offer_code="1",
        page=1,
        source_text="test",
        status="ok",
        quality=1.0,
        issues_json="[]",
        valid_at=iso(valid_at),
        ingested_at=iso(valid_at),
    )
    for key, price in prices.items():
        store.insert(
            "odds",
            id=digest(["test-odds", quote_id, key]),
            quote_set_id=quote_id,
            selection_id=selections[key],
            price=str(price),
            price_text=str(price),
        )
    return quote_id


def _forecast(store, version_id, event_id, market_key, line, probabilities, outcome, created):
    prediction_id = digest(
        ["test-prediction", version_id, event_id, market_key, line, iso(created)]
    )
    store.insert(
        "predictions",
        id=prediction_id,
        model_version_id=version_id,
        event_id=event_id,
        market_key=market_key,
        period="FT",
        line=line,
        created_at=iso(created),
        probabilities_json=canonical(probabilities),
        uncertainty_json="{}",
        inputs_json=canonical(
            {
                "competition": "en.1",
                "info_cutoff": iso(created - timedelta(hours=1)),
                "data_snapshot": "synthetic",
                "code_state": "test",
            }
        ),
        kind="prospective",
    )
    store.insert(
        "prediction_settlements",
        id=digest(["test-settlement", prediction_id]),
        prediction_id=prediction_id,
        revision=1,
        status="settled",
        outcome_json=canonical({"score": "0:0", "selections": outcome}),
        result_ref=None,
        source="test",
        settled_at=iso(NOW),
        supersedes_id=None,
        reason="",
    )
    return prediction_id


def _tables(store) -> dict[str, tuple[int, str]]:
    """Row count and content hash of every table (append-only check)."""
    out = {}
    for table in sorted(store.tables):
        rows = store.rows(f"SELECT * FROM {table} ORDER BY id")
        out[table] = (len(rows), digest([sorted(r.items()) for r in rows]))
    return out


# 1 ---------------------------------------------------------------- matches, not selections


def test_reliability_counts_matches_not_selections(store):
    """'Home team scores' and 'home team over 0.5 goals' are one event of one match: n=1."""
    goals = _version(store, pe.GOALS, "dc-test")
    start = NOW - timedelta(days=1)
    event = _event(store, 1, start, _import(store, "a", start - timedelta(hours=6)))
    created = start - timedelta(hours=5)
    _forecast(
        store,
        goals,
        event,
        "home_to_score",
        None,
        {"yes": 0.85, "no": 0.15},
        {"yes": 0, "no": 1},
        created,
    )
    _forecast(
        store,
        goals,
        event,
        "team_total_home",
        "0.5",
        {"over": 0.85, "under": 0.15},
        {"over": 0, "under": 1},
        created,
    )
    rows = pe.market_forecasts(store)
    assert len(rows) == 2 and {r["role"] for r in rows} == {"active"}
    report = pe.calibration_report(store, policy=POLICY, rows=rows)
    pooled = next(
        r
        for r in report["rows"]
        if r["scope"] == "all" and r["market"] == pe.POOLED and r["range"] == "80–90%"
    )
    assert pooled["matches"] == 1 and pooled["selections"] == 2
    assert pooled["mean_p"] == 0.85 and pooled["freq"] == 0.0
    assert pooled["flag"] == "insufficient"  # one match: no conclusion
    # the selection-level report of TASK 007 counted the same match twice
    from sports_edge import forecast_quality as fq

    old = [r for r in fq.calibration(fq.items(store)) if r["range"] == "80%–90%"]
    assert old and old[0]["n"] == 2


# 2 ---------------------------------------------------------------- flags


def _items(n_matches: int, hits: int, p: float = 0.85, market: str = "total 1.5"):
    return [
        {
            "role": "active",
            "family": pe.GOALS,
            "scope": "core",
            "market": market,
            "match": f"m{i}",
            "selection": "over",
            "p": p,
            "y": int(i < hits),
        }
        for i in range(n_matches)
    ]


def test_overconfident_range_is_flagged_only_with_enough_matches():
    assert POLICY.min_matches == 30 and POLICY.zone == 0.6  # N lives in the policy file
    table = pe.reliability(_items(30, 18), POLICY)  # 85% predicted, 60% hit on 30 matches
    cell = next(
        r
        for r in table
        if r["scope"] == "core" and r["market"] == "total 1.5" and r["range"] == "80–90%"
    )
    assert cell["matches"] == 30 and cell["freq"] == 0.6 and cell["ci95"][1] < 0.85
    assert cell["flag"] == "optimistic" and cell["flag_label"] == "zbyt optymistyczna"
    leg = {
        "league": LEAGUE,
        "model": "dixon_coles_goals dc-test",
        "market_key": "total",
        "line": "1.5",
        "p_model": 0.86,
    }
    note = pe.note_for_leg(table, leg)
    assert note == (
        "prospektywnie ten rynek w tym przedziale trafia rzadziej niż przewiduje model "
        "(n=30 meczów)"
    )
    assert pe.note_for_leg(table, {**leg, "p_model": 0.75}) is None  # another range
    assert pe.note_for_leg(table, {**leg, "line": "2.5"}) is None  # another market
    few = pe.reliability(_items(29, 17), POLICY)
    cell = next(
        r
        for r in few
        if r["scope"] == "core" and r["market"] == "total 1.5" and r["range"] == "80–90%"
    )
    assert cell["flag"] == "insufficient" and cell["flag_label"] == "za mało meczów"
    assert pe.note_for_leg(few, leg) is None
    cautious = pe.reliability(_items(40, 40, p=0.72, market="btts"), POLICY)
    cell = next(
        r
        for r in cautious
        if r["scope"] == "core" and r["market"] == "btts" and r["range"] == "70–80%"
    )
    assert cell["flag"] == "cautious"


def _world(store, goals_validation: dict | None = None):
    """Two settled matches with Elo and Dixon–Coles forecasts and STS prices."""
    elo = _version(store, pe.ELO, "elo-test")
    goals = _version(store, pe.GOALS, "dc-test", validation=goals_validation)
    out = []
    for n, (home, draw, away) in enumerate(((1, 0, 0), (0, 0, 1))):
        start = NOW - timedelta(days=2 - n)
        before = _import(store, f"before{n}", start - timedelta(hours=3))
        event = _event(store, n, start, before)
        market, selections = _market(store, event, "1x2", None, ("home", "draw", "away"))
        _quote(
            store,
            market,
            selections,
            before,
            start - timedelta(hours=3),
            {"home": "1.80", "draw": "3.60", "away": "4.50"},
        )
        total, sides = _market(store, event, "total", "2.5", ("over", "under"))
        _quote(
            store,
            total,
            sides,
            before,
            start - timedelta(hours=3),
            {"over": "1.90", "under": "1.90"},
        )
        created = start - timedelta(hours=5)
        outcome = {"home": home, "draw": draw, "away": away}
        _forecast(
            store,
            elo,
            event,
            "1x2",
            None,
            {"home": 0.55, "draw": 0.25, "away": 0.2},
            outcome,
            created,
        )
        _forecast(
            store,
            goals,
            event,
            "total",
            "2.5",
            {"over": 0.62, "under": 0.38},
            {"over": home, "under": 1 - home},
            created,
        )
        out.append(event)
    return out


def test_flags_and_comparisons_change_no_forecast_threshold_or_status(store):
    _world(store)
    policy_file = (ROOT / "config" / "probability_policy.toml").read_bytes()
    before = _tables(store)
    report = pe.calibration_report(store, policy=POLICY)
    pe.market_comparison(store, policy=POLICY)
    pe.note_for_leg(
        report["rows"],
        {
            "league": LEAGUE,
            "model": "elo_ordered_draw x",
            "market_key": "1x2",
            "line": None,
            "p_model": 0.55,
        },
    )
    retraining.comparison(store, "core", now=NOW)
    retraining.conditions(store, "core", services.CORE_COMPETITIONS, now=NOW)
    assert _tables(store) == before  # nothing written, nothing changed
    assert (ROOT / "config" / "probability_policy.toml").read_bytes() == policy_file
    found = pe.record_market_benchmarks(store, now=NOW, policy=POLICY)
    after = _tables(store)
    changed = {t for t in after if after[t] != before[t]}
    assert changed == {"market_benchmarks"} and found["recorded"] == after["market_benchmarks"][0]
    assert pe.record_market_benchmarks(store, now=NOW + timedelta(hours=1), policy=POLICY) == {
        "groups": found["groups"],
        "recorded": 0,  # the same data and policy: nothing new
    }
    statuses = {r["status"] for r in store.rows("SELECT status FROM model_versions")}
    assert statuses == {"experimental"}
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        store.connection.execute("UPDATE market_benchmarks SET state='model_better'")


# 3 ---------------------------------------------------------------- STS market benchmark


def test_market_benchmark_uses_only_the_price_published_before_the_kick_off(store):
    elo = _version(store, pe.ELO, "elo-test")
    start = NOW - timedelta(days=1)
    early = _import(store, "early", start - timedelta(hours=8))
    before = _import(store, "before", start - timedelta(hours=2))
    after = _import(store, "after", start + timedelta(minutes=30))  # in play: never used
    event = _event(store, 1, start, early)
    market, selections = _market(store, event, "1x2", None, ("home", "draw", "away"))
    _quote(
        store,
        market,
        selections,
        early,
        start - timedelta(hours=8),
        {"home": "2.40", "draw": "3.20", "away": "3.00"},
    )
    chosen = _quote(
        store,
        market,
        selections,
        before,
        start - timedelta(hours=2),
        {"home": "2.00", "draw": "3.50", "away": "4.00"},
    )
    _quote(
        store,
        market,
        selections,
        after,
        start + timedelta(minutes=30),
        {"home": "1.10", "draw": "8.00", "away": "15.00"},
    )
    _forecast(
        store,
        elo,
        event,
        "1x2",
        None,
        {"home": 0.5, "draw": 0.27, "away": 0.23},
        {"home": 1, "draw": 0, "away": 0},
        start - timedelta(hours=6),
    )
    quotes = pe.pre_match_quotes(store, {event})
    assert quotes[(event, "1x2", None)]["quote_set_id"] == chosen
    prices = {"home": 2.0, "draw": 3.5, "away": 4.0}
    power, proportional = pe.power_devig(prices), pe.proportional_devig(prices)
    assert math.isclose(sum(power.values()), 1.0) and math.isclose(sum(proportional.values()), 1.0)
    assert power["home"] > proportional["home"]  # the favourite keeps more of its chance
    rows = {(r["scope"], r["family"], r["market"]): r for r in pe.market_comparison(store)}
    one = rows[("all", pe.ELO, "1x2")]
    assert one["matches"] == 1 and one["state"] == "insufficient"
    assert one["state_label"] == "za mało (1 mecz < 300)"
    assert math.isclose(one["market_log_loss"], -math.log(power["home"]), abs_tol=1e-5)
    assert math.isclose(one["model_log_loss"], -math.log(0.5), abs_tol=1e-5)
    assert math.isclose(
        one["market_log_loss_other_devig"], -math.log(proportional["home"]), abs_tol=1e-5
    )
    # groups of the coupon zone without a common match say "unknown" — never a default false
    unknown = rows[("all", pe.GOALS, "total 2.5")]
    assert unknown["matches"] == 0 and unknown["state"] == "unknown"
    assert unknown["state_label"] == "nieznane (brak wspólnych meczów)"
    assert all(r["state"] in pe.STATES for r in rows.values())
    assert not any(r["state"] in (False, "false") or "beats_market" in r for r in rows.values())


def test_a_forecast_with_only_a_price_from_after_the_start_is_not_compared(store):
    elo = _version(store, pe.ELO, "elo-test")
    start = NOW - timedelta(days=1)
    after = _import(store, "after", start + timedelta(minutes=5))
    event = _event(store, 1, start, after)
    market, selections = _market(store, event, "1x2", None, ("home", "draw", "away"))
    _quote(
        store,
        market,
        selections,
        after,
        start + timedelta(minutes=5),
        {"home": "1.50", "draw": "4.00", "away": "6.00"},
    )
    _forecast(
        store,
        elo,
        event,
        "1x2",
        None,
        {"home": 0.5, "draw": 0.27, "away": 0.23},
        {"home": 1, "draw": 0, "away": 0},
        start - timedelta(hours=6),
    )
    assert pe.pre_match_quotes(store, {event}) == {}
    rows = {(r["scope"], r["family"], r["market"]): r for r in pe.market_comparison(store)}
    assert rows[("all", pe.ELO, "1x2")]["state"] == "unknown"


def test_model_better_needs_300_matches_and_an_interval_below_zero():
    part = [
        {
            "match": f"m{i}",
            "model_log_loss": 0.5,
            "market_log_loss": 0.6 + (i % 3) * 0.01,
            "model_brier": 0.2,
            "market_brier": 0.25,
            "market_log_loss_other": None,
            "lead_hours": 2.0,
            "prediction_id": f"p{i}",
            "quote_set_id": f"q{i}",
            "market": "1x2",
        }
        for i in range(300)
    ]
    row = pe._comparison_row("all", pe.ELO, "1x2", part, policy=POLICY, instances=part)
    assert row["state"] == "model_better" and row["ci95"][1] < 0
    short = pe._comparison_row(
        "all", pe.ELO, "1x2", part[:299], policy=POLICY, instances=part[:299]
    )
    assert short["state"] == "insufficient"


# 5 ---------------------------------------------------------------- challengers


DECISION = {"n": 1752, "log_loss": 0.98091, "brier": 0.5846, "ece": 0.01788}
HYPER = {"k": 15.0, "home_advantage": 40.0, "draw_margin": 0.25}


def _champions(store):
    elo = _version(
        store,
        pe.ELO,
        "elo-champion",
        validation={
            "protocol": "v2",
            "competitions": CORE,
            "decision": DECISION,
            "hyperparameters": HYPER,
            "holdout": {"n": 3936, "log_loss": 0.98774},
        },
    )
    goals = _version(
        store,
        pe.GOALS,
        "dc-champion",
        validation={
            "protocol": "v2",
            "competitions": CORE,
            "decision": {**DECISION, "accuracy": 0.53},
            "elo_version_id": elo,
            "elo_predictive": {
                "1x2": {"status": "validated_predictive"},
                "double_chance": {"status": "validated_predictive"},
            },
            "predictive": {
                "1x2": {"status": "validated_predictive"},
                "btts": {"status": "experimental"},
            },
        },
    )
    for family, version in ((pe.ELO, elo), (pe.GOALS, goals)):
        learning.record(
            store,
            kind="activated",
            actor="user",
            reason="test",
            now=NOW - timedelta(days=30),
            version_id=version,
            scope="core",
            family=family,
        )
    return elo, goals


def _challenger(store, family, name, ready: datetime, validation=None):
    version = _version(
        store,
        family,
        name,
        created=ready,
        validation=validation
        or {"protocol": "v2", "competitions": CORE, "decision": DECISION, "hyperparameters": HYPER},
    )
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


def test_an_expired_challenger_stops_blocking_and_nothing_is_activated(store, app_paths):
    elo, goals = _champions(store)
    new_elo = _challenger(store, pe.ELO, "elo-new", NOW - timedelta(days=15))
    new_goals = _challenger(store, pe.GOALS, "dc-new", NOW - timedelta(days=15))
    # 3 days old: still waiting for evidence and blocking the training of a new one
    _ok, reasons = retraining.conditions(
        store, "core", services.CORE_COMPETITIONS, now=NOW - timedelta(days=12)
    )
    assert any("czeka już na decyzję" in r for r in reasons)
    # 15 days without a settled match in common: it expires and no longer blocks
    leaving = retraining.expiring(store, "core", now=NOW)
    assert {(f, v) for f, v, _why in leaving} == {(pe.ELO, new_elo), (pe.GOALS, new_goals)}
    _ok, reasons = retraining.conditions(store, "core", services.CORE_COMPETITIONS, now=NOW)
    assert not any("czeka już na decyzję" in r for r in reasons)
    before = {r["id"]: r for r in store.rows("SELECT * FROM model_events")}
    calls = []
    found = retraining.review(
        store, app_paths, now=NOW, scopes=("core",), train=lambda s: calls.append(s)
    )
    events = learning.events(store, scope="core")
    expired = [e for e in events if e["kind"] == "expired"]
    assert {e["model_version_id"] for e in expired} == {new_elo, new_goals}
    assert all(e["actor"] == "system" and "Wygasły (brak dowodów)" in e["reason"] for e in expired)
    assert found["core"]["status"] == "skipped" and calls == []  # no new matches (no data)
    assert not any(
        e["kind"] == "activated" and e["created_at"] >= iso(NOW - timedelta(days=1)) for e in events
    )  # never an automatic activation
    after = {r["id"]: r for r in store.rows("SELECT * FROM model_events")}
    assert all(after[k] == v for k, v in before.items())  # old rows unchanged
    core = services.CORE_COMPETITIONS
    assert services.scope_version(store, core)["version_id"] == elo
    assert services.scope_version(store, core, services.GOALS_FAMILY)["version_id"] == goals
    assert learning.challengers(store, "core", pe.ELO) == []
    retraining.review(
        store,
        app_paths,
        now=NOW + timedelta(minutes=5),
        scopes=("core",),
        train=lambda s: calls.append(s),
    )
    assert len([e for e in learning.events(store) if e["kind"] == "expired"]) == 2  # once
    roles = {r["version_id"]: r["role"] for r in services.model_versions_of(store, "core")}
    assert roles[new_elo] == "expired" and services.ROLE_LABELS["expired"].startswith("wygasła")


def test_elo_statuses_are_shown_and_an_identical_challenger_is_named(store):
    elo, _goals = _champions(store)
    _challenger(store, pe.ELO, "elo-old", NOW - timedelta(days=2))
    newest = _challenger(store, pe.ELO, "elo-newest", NOW - timedelta(days=1))
    compared = retraining.comparison(store, "core", now=NOW)
    entry = compared["elo"]
    # TASK 011 fix: the Elo statuses come from the goals experiment that measured them
    assert entry["champion"]["statuses"] == {
        "1x2": "validated_predictive",
        "double_chance": "validated_predictive",
    }
    assert entry["challenger"]["version_id"] == newest
    assert entry["state"] == "identical"
    assert entry["state_label"].startswith("identyczny z championem w oknie selekcji")
    assert "identyczny z championem" in entry["conclusion"]
    assert entry["prospective"]["matches"] == 0
    leaving = retraining.expiring(store, "core", now=NOW)
    assert [(v, "zastąpiony" in why) for _f, v, why in leaving] == [
        (digest(["test-version", pe.ELO, "elo-old"]), True)
    ]
    rows = {r["version_id"]: r for r in services.model_versions_of(store, "core")}
    assert rows[elo]["validated"] == ["1x2", "double_chance"]


def test_challenger_evidence_is_counted_in_matches_until_300(store):
    elo, _goals = _champions(store)
    other = {**HYPER, "k": 30.0}
    challenger = _challenger(
        store,
        pe.ELO,
        "elo-other",
        NOW - timedelta(days=3),
        validation={
            "protocol": "v2",
            "competitions": CORE,
            "decision": {**DECISION, "log_loss": 0.97},
            "hyperparameters": other,
        },
    )
    start = NOW - timedelta(days=1)
    event = _event(store, 1, start, _import(store, "x", start - timedelta(hours=9)))
    outcome = {"home": 1, "draw": 0, "away": 0}
    created = start - timedelta(hours=4)
    _forecast(
        store, elo, event, "1x2", None, {"home": 0.5, "draw": 0.3, "away": 0.2}, outcome, created
    )
    _forecast(
        store,
        challenger,
        event,
        "1x2",
        None,
        {"home": 0.6, "draw": 0.25, "away": 0.15},
        outcome,
        created,
    )
    entry = retraining.comparison(store, "core", now=NOW)["elo"]
    pro = entry["prospective"]
    assert pro["matches"] == 1 and pro["needed"] == 300 and not pro["enough"]
    assert math.isclose(pro["difference"], -math.log(0.6) + math.log(0.5), abs_tol=1e-4)
    assert entry["state"] == "waiting" and entry["state_label"] == "czeka na dowody (1/300)"
    assert "1/300" in entry["conclusion"]


def test_no_challenger_on_the_same_data_unless_the_user_forces_it(store, app_paths):
    from test_models_data import load, synthetic

    load(store, synthetic(1800))
    services.run_protocol_v2(store, app_paths, now=NOW, scope="core")  # first v2 version
    with pytest.raises(ValueError, match="Brak nowych meczów"):
        services.train_now(store, app_paths, now=NOW + timedelta(hours=1), scope="core")
    found = services.train_now(
        store, app_paths, now=NOW + timedelta(hours=2), scope="core", force=True
    )
    assert found["summary"]["forced"] is True and found["summary"]["new_matches"] == 0
    ready = [e for e in learning.events(store, scope="core") if e["kind"] == "challenger_ready"]
    assert ready[-1]["payload"]["forced"] is True and "WYMUSZONY" in ready[-1]["reason"]
    assert ready[0]["payload"]["forced"] is False


# 6 ---------------------------------------------------------------- risk of the day


def _leg(event: str, selection: str, q: float) -> dict:
    return {
        "event_id": event,
        "market_key": "1x2",
        "line": None,
        "selection": selection,
        "p_conservative": q,
    }


def test_day_risk_counts_a_shared_selection_once():
    proposals = {
        "high_hit": {"label": "A", "legs": [_leg("m1", "home", 0.8)]},
        "standard": {"label": "B", "legs": [_leg("m1", "home", 0.8), _leg("m2", "home", 0.9)]},
        "tasiemka": None,
    }
    found = insights.day_risk(proposals)
    # A wins with m1 (0.8), B with m1 and m2 (0.72). Both lose only when m1 loses: 0.2.
    assert math.isclose(found["p_all_win"], 0.72)
    assert math.isclose(found["p_two_or_more_lose"], 0.2)
    assert math.isclose(found["expected_losing"], 0.2 + 0.28)
    assert math.isclose(found["independent_two_or_more_lose"], 0.2 * 0.28)
    assert found["shared_selections"] == 1 and found["coupons"] == 2
    assert insights.day_risk({"a": proposals["high_hit"]}) is None  # one coupon: no "day risk"


def test_day_risk_matches_a_full_enumeration_of_outcomes():
    proposals = {
        "a": {"legs": [_leg("m1", "home", 0.8), _leg("m2", "away", 0.7)]},
        "b": {"legs": [_leg("m1", "home", 0.8), _leg("m3", "draw", 0.6)]},
        "c": {"legs": [_leg("m2", "away", 0.7), _leg("m3", "draw", 0.6), _leg("m4", "home", 0.9)]},
    }
    chances = {"m1": 0.8, "m2": 0.7, "m3": 0.6, "m4": 0.9}
    members = {"a": ("m1", "m2"), "b": ("m1", "m3"), "c": ("m2", "m3", "m4")}
    two_or_more = expected = 0.0
    for wins in itertools.product((0, 1), repeat=4):
        outcome = dict(zip(chances, wins, strict=True))
        weight = math.prod(chances[k] if outcome[k] else 1 - chances[k] for k in chances)
        lost = sum(not all(outcome[k] for k in legs) for legs in members.values())
        two_or_more += weight * (lost >= 2)
        expected += weight * lost
    found = insights.day_risk(proposals)
    assert math.isclose(found["p_two_or_more_lose"], two_or_more)
    assert math.isclose(found["expected_losing"], expected)


def test_kupon_dnia_shows_the_day_risk_as_a_number():
    from sports_edge.app.page_coupon import risk_numbers

    points = risk_numbers(
        {
            "a": {"legs": [_leg("m1", "home", 0.8)]},
            "b": {"legs": [_leg("m1", "home", 0.8), _leg("m2", "home", 0.9)]},
        }
    )
    assert points[0].startswith("Szansa, że przegrają co najmniej dwa kupony dnia: 20,0%")
    assert "Oczekiwana liczba przegranych kuponów: 0,48 z 2" in points[0]
    assert "5,6%" in points[1] and "nie rekomendacja stawek" in points[-1]


# 7 ---------------------------------------------------------------- migration v8 → v9


def test_migration_to_v9_backs_up_and_keeps_every_row(tmp_path):
    from sports_edge.repository import ensure_reference_data
    from sports_edge.storage import SCHEMA_VERSION, Store

    path = tmp_path / "old.db"
    store = Store(path)
    ensure_reference_data(store)
    _world(store)
    store.close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE market_benchmarks")
    raw.execute("PRAGMA user_version=8")
    tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {t: raw.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    raw.commit()
    raw.close()
    migrated = Store(path, backup_dir=tmp_path / "backups")
    assert SCHEMA_VERSION >= 9  # TASK 012: v10 adds calibration_reports on top of v9
    assert migrated.migration_backup is not None and migrated.migration_backup.exists()
    assert migrated.migration_backup.name.startswith(f"pre-migration-v{SCHEMA_VERSION}_")
    for table, n in counts.items():
        if table != "schema_migrations":
            assert migrated.scalar(f"SELECT COUNT(*) FROM {table}") == n, table
    assert "market_benchmarks" in migrated.tables
    query = "SELECT name FROM sqlite_master WHERE type='trigger'"
    triggers = {r[0] for r in migrated.connection.execute(query)}
    assert {"immutable_market_benchmarks_UPDATE", "immutable_market_benchmarks_DELETE"} <= triggers
    note = migrated.rows(  # TASK 012: the one migration run is logged under the current version
        "SELECT description FROM schema_migrations WHERE version=?", (SCHEMA_VERSION,)
    )
    assert note and "v9 TASK 011" in note[-1]["description"]
    migrated.close()


# 8 ---------------------------------------------------------------- interface


def test_models_show_the_coupon_zone_and_the_market_state(app_paths):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    validated = {"status": "validated_predictive", "skill_vs_baseline": 0.05}
    store = services.open_store(app_paths)
    _world(
        store,
        goals_validation={
            "protocol": "v2",
            "competitions": CORE,
            "elo_predictive": {"1x2": validated, "double_chance": validated},
            "predictive": {"total_2.5": validated, "btts": {"status": "experimental"}},
            "markets_served": ["total", "btts"],
        },
    )
    store.close()
    app = AppTest.from_string(
        "from sports_edge.app.page_models import models_page\nmodels_page()\n",
        default_timeout=120,
    )
    app.session_state["demo_mode"] = False
    app.session_state["models_view"] = "Uczenie AI"
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    text = " ".join(m.value for m in app.markdown)
    assert "Strefa kuponów — kalibracja prospektywna" in text
    assert "Porównanie z rynkiem STS" in text
    zone = [d.value for d in app.dataframe if "Mecze (n)" in d.value.columns]
    assert zone and "za mało meczów (n < 30)" in set(zone[0]["Ocena"])
    market = [d.value for d in app.dataframe if "Wspólne mecze" in d.value.columns]
    states = set(market[0]["Stan"])
    assert "za mało (2 mecze < 300)" in states and "nieznane (brak wspólnych meczów)" in states
    assert not any(str(s).lower() == "false" for s in states)
    # Podstawowy: the market column instead of a bare "beats_market: false"
    app.session_state["models_view"] = "Podstawowy"
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    basic = " ".join(m.value for m in app.markdown)
    assert "Porównanie z rynkiem STS" in basic
    for row in ("Wynik meczu (1X2)", "Podwójna szansa", "Liczba goli — linia 2,5"):
        cell = basic.split(row, 1)[1].split("</tr>", 1)[0]
        assert "za mało (2 mecze &lt; 300)" in cell, row  # each market finds its own state
    cell = basic.split("Obie drużyny strzelą", 1)[1].split("</tr>", 1)[0]
    assert "nieznane (brak wspólnych meczów)" in cell  # no BTTS forecast: unknown, not false
