from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from sports_edge.domain import Experiment, FeatureSnapshot, Prediction
from sports_edge.markets import (
    AsianTotalRule,
    Result,
    RuleRegistry,
    WinnerRule,
    load_catalog,
    market_from_catalog,
    validate_prediction,
    validate_selection,
)
from sports_edge.settlement import Outcome
from sports_edge.temporal import Timestamps


@pytest.mark.parametrize(
    ("line", "total", "side", "expected"),
    [
        ("2.25", "2", "over", Outcome.HALF_LOSS),
        ("2.25", "2", "under", Outcome.HALF_WIN),
        ("2.75", "3", "over", Outcome.HALF_WIN),
        ("2.75", "3", "under", Outcome.HALF_LOSS),
        ("2", "2", "over", Outcome.PUSH),
        ("2.5", "3", "over", Outcome.WIN),
        ("2.5", "3", "under", Outcome.LOSS),
    ],
)
def test_asian_totals(line, total, side, expected, market, selection):
    result = Result("completed", "match", False, total=Decimal(total))
    assert (
        AsianTotalRule().resolve(
            replace(market, line=Decimal(line)), replace(selection, key=side), result
        )
        == expected
    )


def test_unknown_rules_status_overtime_and_period_rejected(market, selection):
    registry = RuleRegistry()
    result = Result("completed", "match", False, total=Decimal(2))
    with pytest.raises(ValueError):
        registry.resolve(market, selection, result)
    registry.register("total.v1", AsianTotalRule())
    for bad in (
        replace(result, status="retired"),
        replace(result, includes_overtime=True),
        replace(result, period="half1"),
    ):
        with pytest.raises(ValueError):
            registry.resolve(market, selection, bad)
    with pytest.raises(ValueError):
        registry.register("total.v1", AsianTotalRule())


def test_arbitrary_field_size_and_catalog(event, market, selection):
    assert len(event.participant_ids) == 4
    catalog = load_catalog(Path(__file__).resolve().parents[1] / "config/markets.toml")
    race = market_from_catalog(
        catalog,
        "winner",
        id="m",
        event_id="e",
        selection_keys=event.participant_ids,
        state_keys=event.participant_ids,
    )
    assert (
        WinnerRule().resolve(
            race, replace(selection, key="d"), Result("completed", "match", False, winner_key="d")
        )
        == Outcome.WIN
    )
    dc = market_from_catalog(catalog, "double_chance", id="dc", event_id="e")
    assert dc.selection_keys != dc.state_keys
    with pytest.raises(ValueError):
        validate_selection(replace(selection, market_id="different"), market)
    with pytest.raises(ValueError):
        validate_selection(replace(selection, key="winner"), market)


def test_prediction_is_immutable_and_validates_states(now, market):
    p = Prediction(
        id="p",
        market_id="m",
        model_version_id="mv",
        feature_snapshot_id="f",
        created_at=now,
        probabilities=(("0", 0.1), ("1", 0.2), ("2", 0.3), ("3+", 0.4)),
        uncertainty_method="not_estimated",
    )
    validate_prediction(p, market)
    with pytest.raises(FrozenInstanceError):
        p.market_id = "changed"
    with pytest.raises(ValueError):
        replace(p, probabilities=(("0", 0.5), ("0", 0.5)))
    with pytest.raises(ValueError):
        validate_prediction(replace(p, probabilities=(("other", 1),)), market)


def test_experiment_chronology(now):
    experiment = Experiment(
        id="exp",
        hypothesis="synthetic",
        data_version="d",
        code_version="c",
        model_version_id="m",
        config_json="{}",
        registered_at=now,
        train_start=now,
        train_end=now + timedelta(days=1),
        validation_end=now + timedelta(days=2),
        test_end=now + timedelta(days=3),
        independence="synthetic",
    )
    with pytest.raises(ValueError):
        replace(experiment, test_end=now)


def test_feature_snapshot_does_not_accept_mutable_or_nonfinite_inputs(now):
    feature = FeatureSnapshot(
        id="f",
        event_id="e",
        feature_version="v1",
        values=(("rest_days", 3.0), ("injury", None)),
        source_record_ids=("source-1",),
        timestamps=Timestamps(ingested_at=now),
    )
    for bad in ([("x", 1.0)], (("x", float("nan")),), (("x", 1.0), ("x", 2.0))):
        with pytest.raises(ValueError):
            replace(feature, values=bad)
