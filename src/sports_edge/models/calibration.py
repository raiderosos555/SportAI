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


# ---------------------------------------------------------------- TASK 005: calibration maps


def wilson_interval(hits: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval of a hit rate (hits may be fractional for weighted counts)."""
    if n <= 0:
        return 0.0, 1.0
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def isotonic_fit(p: Sequence[float], y: Sequence[int]) -> list[dict]:
    """Pool-adjacent-violators: monotone step function of observed frequency vs forecast.

    Returns blocks {low, high, value, n} sorted by forecast; fitted on validation data only.
    """
    if len(p) != len(y) or not p:
        raise ValueError("Paired forecasts are required")
    order = sorted(range(len(p)), key=lambda i: p[i])
    blocks: list[dict] = []
    for i in order:
        blocks.append({"low": p[i], "high": p[i], "sum": float(y[i]), "n": 1})
        while (
            len(blocks) > 1
            and blocks[-2]["sum"] / blocks[-2]["n"] > blocks[-1]["sum"] / blocks[-1]["n"]
        ):
            last = blocks.pop()
            blocks[-1]["high"] = last["high"]
            blocks[-1]["sum"] += last["sum"]
            blocks[-1]["n"] += last["n"]
    return [
        {"low": b["low"], "high": b["high"], "value": b["sum"] / b["n"], "n": b["n"]}
        for b in blocks
    ]


def isotonic_apply(blocks: Sequence[Mapping], p: float) -> tuple[float, int]:
    """(calibrated value, block size) for a raw forecast; linear between block centres."""
    if not blocks:
        return p, 0
    if p <= blocks[0]["high"]:
        return blocks[0]["value"], blocks[0]["n"]
    for left, right in zip(blocks, blocks[1:], strict=False):
        if p <= right["low"]:
            span = right["low"] - left["high"]
            share = (p - left["high"]) / span if span > 0 else 0.0
            value = left["value"] + share * (right["value"] - left["value"])
            return value, min(left["n"], right["n"])
        if p <= right["high"]:
            return right["value"], right["n"]
    return blocks[-1]["value"], blocks[-1]["n"]


def bin_report(p: Sequence[float], y: Sequence[int], edges: Sequence[float]) -> list[dict]:
    """Observed frequency with a Wilson 95% interval per forecast range (e.g. 50–60%)."""
    out = []
    for low, high in zip(edges, edges[1:], strict=False):
        idx = [i for i, v in enumerate(p) if low <= v < high or (high == 1.0 and v == 1.0)]
        n = len(idx)
        hits = sum(y[i] for i in idx)
        mean_p = sum(p[i] for i in idx) / n if n else None
        lo, hi = wilson_interval(hits, n) if n else (None, None)
        out.append(
            {
                "range": f"{low:.0%}–{high:.0%}",
                "n": n,
                "mean_p": round(mean_p, 4) if mean_p is not None else None,
                "freq": round(hits / n, 4) if n else None,
                "ci95": [round(lo, 4), round(hi, 4)] if n else None,
                "overconfident": bool(n >= 30 and hi is not None and hi < mean_p),
                "underconfident": bool(n >= 30 and lo is not None and lo > mean_p),
            }
        )
    return out
