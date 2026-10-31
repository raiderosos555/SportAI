"""Probabilistic scoring on out-of-sample predictions only. Bins are fixed in advance."""

from collections.abc import Mapping, Sequence
from math import log, sqrt


def log_loss(
    probabilities: Sequence[Mapping[str, float]], outcomes: Sequence[str], eps: float = 1e-12
) -> float:
    if not probabilities or len(probabilities) != len(outcomes):
        raise ValueError("Paired, nonempty predictions and outcomes are required")
    return -sum(log(max(p[o], eps)) for p, o in zip(probabilities, outcomes, strict=True)) / len(
        outcomes
    )


def brier(probabilities: Sequence[Mapping[str, float]], outcomes: Sequence[str]) -> float:
    """Multi-class Brier: mean over events of the sum over states (range 0..2)."""
    if not probabilities or len(probabilities) != len(outcomes):
        raise ValueError("Paired, nonempty predictions and outcomes are required")
    total = 0.0
    for p, outcome in zip(probabilities, outcomes, strict=True):
        total += sum((value - (state == outcome)) ** 2 for state, value in p.items())
    return total / len(outcomes)


def reliability(p: Sequence[float], y: Sequence[int], bins: int = 10) -> list[dict]:
    if len(p) != len(y) or bins < 1:
        raise ValueError("Paired forecasts and a positive bin count are required")
    table = []
    for b in range(bins):
        low, high = b / bins, (b + 1) / bins
        idx = [i for i, v in enumerate(p) if low <= v < high or (b == bins - 1 and v == 1.0)]
        if not idx:
            table.append({"low": low, "high": high, "n": 0, "mean_p": None, "freq": None})
            continue
        mean_p = sum(p[i] for i in idx) / len(idx)
        freq = sum(y[i] for i in idx) / len(idx)
        table.append({"low": low, "high": high, "n": len(idx), "mean_p": mean_p, "freq": freq})
    return table


def ece(p: Sequence[float], y: Sequence[int], bins: int = 10) -> float:
    table = reliability(p, y, bins)
    n = len(p)
    if not n:
        raise ValueError("No forecasts")
    return sum(row["n"] / n * abs(row["mean_p"] - row["freq"]) for row in table if row["n"])


def one_vs_rest(
    probabilities: Sequence[Mapping[str, float]], outcomes: Sequence[str]
) -> tuple[list[float], list[int]]:
    """Pool every state of every event as a binary forecast (for reliability/ECE)."""
    p, y = [], []
    for forecast, outcome in zip(probabilities, outcomes, strict=True):
        for state, value in forecast.items():
            p.append(value)
            y.append(int(state == outcome))
    return p, y


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    phat = successes / n
    denominator = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denominator
    half = z * sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)
