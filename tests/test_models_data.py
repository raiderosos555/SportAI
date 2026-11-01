"""Calibration, as-of walk-forward without leakage, chronological experiments, validation
gates, market benchmark and reproducibility.

All matches below are generated synthetic data (FIXTURE) — no real sports statistics.
"""

import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from math import log

import pytest

from sports_edge.analysis.policy import Policy
from sports_edge.ingestion.types import digest, iso
from sports_edge.models import calibration
from sports_edge.models.elo import Elo, HistoricalMatch, walk_forward
from sports_edge.models.registry import beats_market, validation_status
from sports_edge.offers.text import NameNormalizer
from sports_edge.repository import ensure_reference_data
from sports_edge.sportsdata import (
    Split,
    history,
    import_results_csv,
    predict_offer,
    run_elo_experiment,
)
from sports_edge.storage import Store

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
SPLIT = Split(validation_from="2023-01-01", holdout_from="2024-01-01")
POLICY = Policy(min_validation_events=300)


def test_calibration_metrics_on_known_values():
    forecasts = [{"a": 0.8, "b": 0.2}, {"a": 0.4, "b": 0.6}]
    outcomes = ["a", "b"]
    assert calibration.log_loss(forecasts, outcomes) == pytest.approx(-(log(0.8) + log(0.6)) / 2)
    assert calibration.brier(forecasts, outcomes) == pytest.approx((0.08 + 0.32) / 2)
    p, y = calibration.one_vs_rest(forecasts, outcomes)
    assert calibration.ece([0.1, 0.9], [0, 1]) == pytest.approx(0.1)
    assert sum(row["n"] for row in calibration.reliability(p, y)) == 4
    low, high = calibration.wilson(50, 100)
    assert low < 0.5 < high


def synthetic(n=1800, seed=7, start=date(2020, 1, 1), teams=20, per_day=1):
    rng = random.Random(seed)
    strength = {f"Team {chr(65 + i)} CF": rng.gauss(0, 0.6) for i in range(teams)}
    names = list(strength)
    matches = []
    for k in range(n):
        home, away = rng.sample(names, 2)
        lam_h = 1.45 * 2.718 ** (strength[home] - strength[away] + 0.1)
        lam_a = 1.10 * 2.718 ** (strength[away] - strength[home])
        hs = sum(rng.random() < lam_h / 6 for _ in range(6))
        as_ = sum(rng.random() < lam_a / 6 for _ in range(6))
        played = start + timedelta(days=k // per_day)
        available = datetime.combine(played, datetime.min.time(), UTC) + timedelta(hours=36)
        matches.append(
            HistoricalMatch(
                available,
                played.isoformat(),
                home,
                away,
                hs,
                as_,
                competition="es.1",
                result_id=f"r{k}",
            )
        )
    return matches


def csv_bytes(matches):
    lines = ["date;home;away;home_score;away_score"]
    lines += [f"{m.match_date};{m.home};{m.away};{m.home_score};{m.away_score}" for m in matches]
    return "\n".join(lines).encode()


def load(store, matches, competition="es.1"):
    return import_results_csv(
        store,
        csv_bytes(matches),
        file_name="synthetic.csv",
        source_name="synthetic fixture",
        license_note="dane syntetyczne do testów",
        sport="football",
        competition=competition,
        now=NOW,
    )


def test_result_cannot_be_available_before_its_match():
    with pytest.raises(ValueError, match="leakage"):
        HistoricalMatch(datetime(2026, 1, 1, tzinfo=UTC), "2026-01-02", "A", "B", 1, 0)


def test_walk_forward_does_not_use_the_current_or_same_day_results():
    matches = synthetic(60)
    baseline = walk_forward(matches, Elo(), three_way=False)
    flipped = list(matches)
    m = flipped[30]
    score = (0, 9) if m.outcome != "away" else (9, 0)
    flipped[30] = replace(m, home_score=score[0], away_score=score[1])
    changed = walk_forward(flipped, Elo(), three_way=False)
    assert baseline[30]["forecast"] == changed[30]["forecast"]
    assert baseline[:30] == changed[:30]
    assert [i for i in range(31, 60) if baseline[i]["forecast"] != changed[i]["forecast"]]
    same_day = synthetic(40, per_day=2)  # two matches per day
    first, second = walk_forward(same_day, Elo(), three_way=False)[10:12]
    assert first["match"].match_date == second["match"].match_date
    flipped = list(same_day)
    flipped[10] = replace(same_day[10], home_score=0, away_score=9)
    again = walk_forward(flipped, Elo(), three_way=False)
    assert again[11]["forecast"] == second["forecast"]  # same-day result never used


def test_history_is_as_of(store):
    matches = synthetic(400)
    out = load(store, matches)
    assert out["inserted"] == 400 and not out["errors"]
    cutoff = datetime(2020, 6, 1, tzinfo=UTC)
    known = history(store, sport="football", until=cutoff, competitions=("es.1",))
    assert known and all(m.available_at <= cutoff for m in known)
    assert max(date.fromisoformat(m.match_date) for m in known) < date(2020, 6, 1)
    basis = store.rows("SELECT DISTINCT available_at_basis FROM historical_results")
    assert basis == [{"available_at_basis": "assumed_lag_36h"}]
    with pytest.raises(ValueError, match="prawną"):
        import_results_csv(
            store,
            csv_bytes(matches),
            file_name="x.csv",
            source_name="x",
            license_note=" ",
            sport="football",
            competition="es.1",
            now=NOW,
        )


def test_source_timestamps_are_kept_apart_from_the_assumed_lag(store):
    data = (
        b"date;home;away;home_score;away_score;available_at\n"
        b"2026-09-01;A FC;B FC;1;0;2026-09-01T21:05:00+00:00\n"
        b"2026-09-02;C FC;D FC;2;2;2026-08-31T10:00:00+00:00\n"
        b"2026-09-03;E FC;F FC;0;1;2026-09-03T21:00:00\n"
    )
    out = import_results_csv(
        store,
        data,
        file_name="timestamps.csv",
        source_name="synthetic fixture",
        license_note="dane syntetyczne do testów",
        sport="football",
        competition="es.1",
        now=NOW,
    )
    assert out["inserted"] == 1
    assert "UTC offset" in out["errors"][0] and "przed dniem meczu" in out["errors"][1]
    stored = store.rows("SELECT available_at, available_at_basis FROM historical_results")
    assert stored == [
        {
            "available_at": "2026-09-01T21:05:00.000000+00:00",
            "available_at_basis": "source_timestamp",
        }
    ]
    assert not history(store, sport="football", until=datetime(2026, 9, 1, 21, tzinfo=UTC))
    assert history(store, sport="football", until=datetime(2026, 9, 1, 21, 5, tzinfo=UTC))


def test_chronological_experiment_keeps_the_holdout_untouched(store):
    matches = synthetic(1800)
    load(store, matches)
    report = run_elo_experiment(
        store, competitions=("es.1",), split=SPLIT, now=NOW, policy=POLICY, register=False
    )
    assert report["n"]["train"] + report["n"]["validation"] + report["n"]["holdout"] == 1800
    assert report["holdout"]["n"] == report["n"]["holdout"] > 0
    assert report["holdout"]["log_loss"] < report["baseline_climatology"]["log_loss"]
    assert report["market_benchmark"] is None and report["leakage_checked"] is True
    # Hyper-parameters chosen on validation must not depend on holdout results.
    other = Store(":memory:")
    ensure_reference_data(other)
    scrambled = [
        replace(m, home_score=m.away_score, away_score=m.home_score)
        if m.match_date >= SPLIT.holdout_from
        else m
        for m in matches
    ]
    load(other, scrambled)
    report2 = run_elo_experiment(
        other, competitions=("es.1",), split=SPLIT, now=NOW, policy=POLICY, register=False
    )
    assert report2["selection"] == report["selection"]
    assert report2["hyperparameters"]["k"] == report["hyperparameters"]["k"]


def test_experiment_is_reproducible(store):
    load(store, synthetic(1800))
    first = run_elo_experiment(
        store, competitions=("es.1",), split=SPLIT, now=NOW, policy=POLICY, register=False
    )
    second = run_elo_experiment(
        store, competitions=("es.1",), split=SPLIT, now=NOW, policy=POLICY, register=False
    )
    for key in ("holdout", "selection", "data_snapshot", "code_state", "hyperparameters"):
        assert first[key] == second[key]


def test_validated_status_needs_derived_market_evidence():
    good = {
        "holdout": {"n": 1000, "ece": 0.01},
        "leakage_checked": True,
        "market_benchmark": {
            "same_events": True,
            "n_events": 1000,
            "model_log_loss": 0.95,
            "market_log_loss": 0.97,
        },
    }
    assert validation_status(good, Policy()) == ("validated", [])
    for broken in (
        {**good, "market_benchmark": None, "beats_market": True},  # a flag is not evidence
        {**good, "market_benchmark": {**good["market_benchmark"], "same_events": False}},
        {**good, "market_benchmark": {**good["market_benchmark"], "model_log_loss": 0.98}},
        {**good, "holdout": {"n": 100, "ece": 0.01}},
        {**good, "holdout": {"n": 1000, "ece": 0.2}},
        {**good, "leakage_checked": False},
    ):
        status, reasons = validation_status(broken, Policy())
        assert status == "experimental" and reasons
    assert beats_market(None, Policy())[0] is False


def test_market_benchmark_uses_the_same_events(store):
    matches = synthetic(1800)
    load(store, matches)
    holdout = [m for m in matches if m.match_date >= SPLIT.holdout_from]
    import_id = store.rows("SELECT id FROM data_imports")[0]["id"]
    with store.transaction():
        for m in holdout:
            result_id = digest(["hist", "football", "es.1", m.match_date, m.home, m.away])
            for selection, price in (("home", "2.10"), ("draw", "3.40"), ("away", "3.60")):
                store.insert(
                    "benchmark_odds",
                    id=digest([result_id, selection]),
                    import_id=import_id,
                    result_id=result_id,
                    operator="synthetic-operator",
                    moment="closing",
                    market_key="1x2",
                    selection=selection,
                    price=price,
                    observed_at=None,
                )
    report = run_elo_experiment(store, competitions=("es.1",), split=SPLIT, now=NOW, policy=POLICY)
    bench = report["market_benchmark"]
    assert bench["n_events"] == len(holdout) and bench["same_events"] is True
    assert "nie są ofertą STS" in bench["note"]
    expected = (
        "validated"
        if (
            len(holdout) >= POLICY.min_validation_events
            and bench["model_log_loss"] < bench["market_log_loss"]
            and report["holdout"]["ece"] <= POLICY.max_calibration_ece
        )
        else "experimental"
    )
    assert report["status"] == expected
    assert store.rows("SELECT kind, status FROM experiments")[0]["kind"] == "backtest"


def _sts_event(store, event_id, home, away, start):
    store.insert(
        "imports",
        id="imp",
        source_id="fixture",
        bookmaker_id="sts",
        parser="t",
        parser_verified=1,
        raw_sha256="x",
        file_name=None,
        raw_path=None,
        data_kind="synthetic",
        offer_kind="today",
        generated_at=None,
        ingested_at=iso(NOW),
        rows_total=0,
        rows_ok=0,
        rows_review=0,
        unparsed=0,
        quality=1.0,
        summary_json="{}",
    )
    store.insert(
        "events",
        id=event_id,
        bookmaker_id="sts",
        sport="football",
        sport_raw="",
        league="HISZPANIA - LALIGA",
        country=None,
        home=home,
        away=away,
        participants_json="[]",
        name_raw="",
        start_at=iso(start),
        offer_date=None,
        first_import_id="imp",
    )
    return {
        "id": event_id,
        "sport": "football",
        "league": "HISZPANIA - LALIGA",
        "home": home,
        "away": away,
        "start_at": iso(start),
    }


def test_forecasts_only_before_start_and_with_current_data(store):
    matches = synthetic(1800)
    load(store, matches)
    now = matches[-1].available_at + timedelta(hours=1)
    report = run_elo_experiment(store, competitions=("es.1",), split=SPLIT, now=now, policy=POLICY)
    assert report["status"] == "experimental"
    upcoming = _sts_event(store, "e1", "Team A CF", "Team B CF", now + timedelta(days=1))
    started = {**upcoming, "id": "e2", "start_at": iso(now - timedelta(minutes=1))}
    unknown = {**upcoming, "id": "e3", "home": "Nieznany Klub"}
    late = {**upcoming, "id": "e4", "start_at": iso(now + timedelta(days=60))}
    out = predict_offer(
        store,
        version_id=report["version_id"],
        events=[upcoming, started, unknown, late],
        normalizer=NameNormalizer(),
        now=now,
    )
    assert out["saved"] == 1
    assert any("rozpoczęte" in s for s in out["skipped"])
    assert any("nieaktualne dane" in s for s in out["skipped"])
    assert out["unmatched"][0]["sts"] == "Nieznany Klub"
    prediction = store.rows("SELECT * FROM predictions")[0]
    assert prediction["event_id"] == "e1" and prediction["kind"] == "prospective"
    assert '"info_cutoff"' in prediction["inputs_json"]
    assert '"code_state"' in prediction["inputs_json"]
