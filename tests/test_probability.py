import math

import pytest

from sports_edge.edge import binary_ev, payout_ev
from sports_edge.probability import (
    market_probabilities,
    normalize_probabilities,
    validate_probabilities,
)


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"a": -0.1, "b": 1.1},
        {"a": math.nan},
        {"a": math.inf},
        {"a": 0.9},
        {"a": True},
        {"": 1.0},
    ],
)
def test_invalid_probabilities(values):
    with pytest.raises(ValueError):
        validate_probabilities(values)


def test_distribution_and_normalization():
    validate_probabilities({"a": 0, "b": 0.2, "c": 0.3, "d": 0.5})
    assert normalize_probabilities({"a": 2, "b": 3}) == pytest.approx({"a": 0.4, "b": 0.6})
    assert normalize_probabilities({"a": 1e308, "b": 1e308}) == {"a": 0.5, "b": 0.5}


@pytest.mark.parametrize("values", [{}, {"a": 0}, {"a": -1}, {"a": math.inf}])
def test_normalization_rejects_bad_weights(values):
    with pytest.raises(ValueError):
        normalize_probabilities(values)


def test_ev_and_devig():
    assert binary_ev(0.55, 2) == pytest.approx(0.1)
    assert binary_ev(0, 2) == -1
    assert binary_ev(1, 3) == 2
    assert market_probabilities({"a": 1.9, "b": 1.9}) == {"a": 0.5, "b": 0.5}
    assert payout_ev({"win": 0.4, "push": 0.2, "loss": 0.4}, {"win": 1, "push": 0, "loss": -1}) == 0


@pytest.mark.parametrize("odds", [0, 1, -2, math.nan, math.inf, True])
def test_invalid_odds(odds):
    with pytest.raises(ValueError):
        binary_ev(0.5, odds)
    with pytest.raises(ValueError):
        market_probabilities({"a": odds})


def test_state_payout_mismatch():
    with pytest.raises(ValueError):
        payout_ev({"win": 1}, {"loss": 0})
