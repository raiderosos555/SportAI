"""Probability operations on exhaustive, mutually exclusive state spaces."""

from collections.abc import Mapping
from math import fsum, isfinite


def validate_probabilities(values: Mapping[str, float], *, tolerance: float = 1e-9) -> None:
    if not isfinite(tolerance) or not 0 <= tolerance <= 1e-6:
        raise ValueError("Invalid probability tolerance")
    if not values or any(not isinstance(k, str) or not k for k in values):
        raise ValueError("A nonempty labelled distribution is required")
    if any(isinstance(p, bool) or not isfinite(p) or not 0 <= p <= 1 for p in values.values()):
        raise ValueError("Probabilities must be finite and in [0, 1]")
    if abs(fsum(values.values()) - 1) > tolerance:
        raise ValueError("Probabilities must sum to one")


def normalize_probabilities(weights: Mapping[str, float]) -> dict[str, float]:
    """Explicit normalization of nonnegative weights; never silently repairs predictions."""
    if not weights or any(not isinstance(k, str) or not k for k in weights):
        raise ValueError("Empty distribution or label")
    if any(isinstance(w, bool) or not isfinite(w) or w < 0 for w in weights.values()):
        raise ValueError("Invalid weights")
    scale = max(weights.values())
    if scale == 0:
        raise ValueError("All weights are zero")
    scaled = {k: w / scale for k, w in weights.items()}
    total = fsum(scaled.values())
    return {k: w / total for k, w in scaled.items()}


def market_probabilities(odds: Mapping[str, float]) -> dict[str, float]:
    """Proportional devig benchmark, only for a complete disjoint outcome market."""
    if any(isinstance(o, bool) or not isfinite(o) or o <= 1 for o in odds.values()):
        raise ValueError("Decimal odds must exceed one")
    return normalize_probabilities({k: 1 / o for k, o in odds.items()})
