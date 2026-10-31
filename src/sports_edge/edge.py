"""Expected cash flows, not evidence of a predictive edge."""

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from math import fsum, isfinite

from sports_edge.probability import validate_probabilities
from sports_edge.settlement import CostProfile, Outcome, settle


def binary_ev(probability: float, decimal_odds: float) -> float:
    validate_probabilities({"win": probability, "loss": 1 - probability})
    if isinstance(decimal_odds, bool) or not isfinite(decimal_odds) or decimal_odds <= 1:
        raise ValueError("Invalid decimal odds")
    return probability * decimal_odds - 1


def payout_ev(probabilities: Mapping[str, float], net_returns: Mapping[str, float]) -> float:
    """Full disjoint state distribution with net returns per unit staked."""
    validate_probabilities(probabilities)
    if probabilities.keys() != net_returns.keys():
        raise ValueError("State and payout keys differ")
    if any(not isfinite(v) for v in net_returns.values()):
        raise ValueError("Nonfinite return")
    return fsum(probabilities[k] * net_returns[k] for k in probabilities)


def net_ev(
    probabilities: Mapping[Outcome, float],
    stake: Decimal,
    odds: Decimal,
    profile: CostProfile,
    *,
    on_date: date,
) -> Decimal:
    """Expected net ROI; nonlinear thresholds evaluated before expectation."""
    if any(not isinstance(key, Outcome) for key in probabilities):
        raise ValueError("Distribution must describe settlement outcomes")
    validate_probabilities(probabilities)
    with localcontext() as context:
        context.prec = 38
        context.rounding = ROUND_HALF_UP
        return (
            sum(
                (
                    Decimal(str(p))
                    * settle(stake, odds, outcome, profile, on_date=on_date).net_profit
                    for outcome, p in probabilities.items()
                    if p > 0
                ),
                Decimal(0),
            )
            / stake
        )
