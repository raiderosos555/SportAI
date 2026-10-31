"""Calibration metrics, Elo walk-forward without leakage, data import as-of and model gate.

Results below are generated synthetic matches; no real sports statistics are used.
"""

import random
from datetime import UTC, date, datetime, timedelta
from math import log

import pytest

from sports_edge.analysis.policy import Policy
from sports_edge.models import calibration
from sports_edge.models.elo import Elo, HistoricalMatch, walk_forward
from sports_edge.models.registry import validation_status
from sports_edge.offers.text import NameNormalizer
from sports_edge.sportsdata import (
    evaluate_elo,
    history,
    import_results_csv,
    predict_events,
    train_baseline,
)

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


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


def synthetic_season(n=400, seed=7):
    rng = random.Random(seed)
    strength = {f"T{i}": rng.gauss(0, 0.6) for i in range(12)}
    teams = list(strength)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    matches = []
    for k in range(n):
        home, away = rng.sample(teams, 2)
        lam_h = 1.4 * 2.718 ** (strength[home] - strength[away] + 0.1)
        lam_a = 1.1 * 2.718 ** (strength[away] - strength[home])
        hs = sum(rng.random() < lam_h / 6 for _ in range(6))
        as_ = sum(rng.random() < lam_a / 6 for _ in range(6))
        when = start + timedelta(days=k)
        matches.append(
            HistoricalMatch(
                when + timedelta(hours=36), when.date().isoformat(), home, away, hs, as_
            )
        )
    return matches


def test_walk_forward_does_not_use_the_current_result():
    matches = synthetic_season(60)
    baseline = walk_forward(matches, Elo(), three_way=False)
    flipped = list(matches)
    m = flipped[30]
    score = (0, 9) if m.outcome != "away" else (9, 0)  # a different W/D/L outcome
    flipped[30] = HistoricalMatch(m.available_at, m.match_date, m.home, m.away, *score)
    changed = walk_forward(flipped, Elo(), three_way=False)
    assert baseline[30]["forecast"] == changed[30]["forecast"]  # own result unseen
    assert baseline[:30] == changed[:30]
    later = [i for i in range(31, 60) if baseline[i]["forecast"] != changed[i]["forecast"]]
    assert later  # used only for later matches


def test_elo_evaluation_scores_only_out_of_sample():
    report = evaluate_elo(synthetic_season())
    assert report["train_matches"] == 240 and 0 < report["n_out_of_sample"] <= 160
    assert report["beats_market"] is None and report["leakage_checked"] is True
    assert 0 < report["draw_margin"] < 1
    with pytest.raises(ValueError, match="Za mało"):
        evaluate_elo(synthetic_season(50))


def test_model_without_market_benchmark_is_not_validated():
    policy = Policy()
    status, reasons = validation_status(
        {"n_out_of_sample": 1000, "ece": 0.01, "beats_market": None, "leakage_checked": True},
        policy,
    )
    assert status == "experimental" and any("rynku" in r for r in reasons)
    status, reasons = validation_status(
        {"n_out_of_sample": 1000, "ece": 0.01, "beats_market": True, "leakage_checked": True},
        policy,
    )
    assert status == "validated" and not reasons


def csv_bytes(matches):
    lines = ["date;home;away;home_score;away_score"]
    lines += [f"{m.match_date};{m.home};{m.away};{m.home_score};{m.away_score}" for m in matches]
    return "\n".join(lines).encode()


def test_results_import_is_as_of_and_licensed(store):
    matches = synthetic_season(300)
    with pytest.raises(ValueError, match="prawną"):
        import_results_csv(
            store,
            csv_bytes(matches),
            file_name="r.csv",
            source_name="x",
            license_note=" ",
            sport="football",
            competition="SYN",
            now=NOW,
        )
    data = csv_bytes(matches) + b"\n2026-10-31;T1;T2;1;0\nzla;T1;T2;x;0"
    out = import_results_csv(
        store,
        data,
        file_name="r.csv",
        source_name="synthetic",
        license_note="dane syntetyczne do testów",
        sport="football",
        competition="SYN",
        now=NOW,
    )
    assert out["inserted"] == 300 and len(out["errors"]) == 2  # future result + bad row
    again = import_results_csv(
        store,
        data,
        file_name="r.csv",
        source_name="synthetic",
        license_note="dane syntetyczne do testów",
        sport="football",
        competition="SYN",
        now=NOW,
    )
    assert again["duplicate"] is True
    cutoff = datetime(2024, 3, 1, tzinfo=UTC)
    known = history(store, sport="football", competition="SYN", until=cutoff)
    assert known and all(m.available_at <= cutoff for m in known)
    assert max(date.fromisoformat(m.match_date) for m in known) < date(2024, 3, 1)


def test_baseline_training_registers_experimental_model_and_predicts(store):
    matches = synthetic_season(300)
    import_results_csv(
        store,
        csv_bytes(matches),
        file_name="r.csv",
        source_name="synthetic",
        license_note="dane syntetyczne do testów",
        sport="football",
        competition="SYN",
        now=NOW,
    )
    trained = train_baseline(store, sport="football", competition="SYN", now=NOW, policy=Policy())
    assert trained["status"] == "experimental"
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
        ingested_at=NOW.isoformat(),
        rows_total=0,
        rows_ok=0,
        rows_review=0,
        unparsed=0,
        quality=1.0,
        summary_json="{}",
    )
    events = [
        {"id": "e1", "sport": "football", "home": "T1", "away": "T2"},
        {"id": "e2", "sport": "football", "home": "Nieznany FC", "away": "T2"},
    ]
    for event in events:
        store.insert(
            "events",
            id=event["id"],
            bookmaker_id="sts",
            sport="football",
            sport_raw="",
            league="SYN",
            country=None,
            home=event["home"],
            away=event["away"],
            participants_json="[]",
            name_raw="",
            start_at=None,
            offer_date=None,
            first_import_id="imp",
        )
    out = predict_events(
        store,
        version_id=trained["version_id"],
        sport="football",
        competition="SYN",
        events=events,
        normalizer=NameNormalizer(),
        now=NOW,
    )
    assert out["saved"] == 1 and "Nieznany FC" in out["skipped"][0]
    row = store.rows("SELECT * FROM predictions")[0]
    assert row["kind"] == "prospective" and row["event_id"] == "e1"
