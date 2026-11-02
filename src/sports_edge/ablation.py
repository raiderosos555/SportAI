"""Feature ablation experiment (TASK 007 §P5): what do features add to the models?

Four 1X2 models judged on the SAME matches of the protocol v2 selection window:

* A — Elo (ordered draw), as served;
* B — Dixon–Coles (goals), calibrated and normalised as served;
* C — Dixon–Coles + pre-match features: multinomial logistic stacking of B's log
  probabilities with form and rest features, fitted on the calibration window;
* D — simple machine learning: multinomial logistic regression on Elo difference and the
  same features, fitted on the validation and calibration windows.

Features are computed only from matches BEFORE the match (no post-match data): points and
goal difference of the last five matches of each team and days since the previous match.
Every result is reported, also when a model is worse; the final holdout is reported only.
"""

import math
from collections import defaultdict, deque
from datetime import date

import numpy as np

from sports_edge.models import calibration
from sports_edge.sportsdata import Split

OUTCOMES = ("home", "draw", "away")
FORM = 5


def features(rows: list[dict]) -> dict[str, list[float]]:
    """As-of features of every match id (computed before the match is added)."""
    recent: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=FORM))
    last_day: dict[tuple, date] = {}
    out = {}
    for r in sorted(rows, key=lambda r: (r["match_date"], r["id"])):
        day = date.fromisoformat(r["match_date"])
        values = []
        for side in ("home", "away"):
            team = (r["competition"], r[side])
            games = recent[team]
            points = sum(g[0] for g in games) / len(games) if games else 1.35
            goals = sum(g[1] for g in games) / len(games) if games else 0.0
            rest = (day - last_day[team]).days if team in last_day else 7
            values += [points, goals, min(rest, 30) / 7]
        out[r["id"]] = values
        for side, own, other in (
            ("home", "home_score", "away_score"),
            ("away", "away_score", "home_score"),
        ):
            team = (r["competition"], r[side])
            diff = r[own] - r[other]
            recent[team].append((3 if diff > 0 else 1 if diff == 0 else 0, diff))
            last_day[team] = day
    return out


def _outcome(r: dict) -> int:
    home, away = r["home_score"], r["away_score"]
    return 0 if home > away else 1 if home == away else 2


def fit_softmax(x: np.ndarray, y: np.ndarray, *, l2: float = 1e-3, steps: int = 600) -> dict:
    """Multinomial logistic regression (standardised inputs, gradient descent, L2)."""
    mean, std = x.mean(axis=0), x.std(axis=0) + 1e-9
    z = np.hstack([(x - mean) / std, np.ones((len(x), 1))])
    w = np.zeros((z.shape[1], 3))
    target = np.eye(3)[y]
    for _ in range(steps):
        logits = z @ w
        logits -= logits.max(axis=1, keepdims=True)
        p = np.exp(logits)
        p /= p.sum(axis=1, keepdims=True)
        grad = z.T @ (p - target) / len(z) + l2 * w
        w -= 0.5 * grad
    return {"w": w, "mean": mean, "std": std}


def predict_softmax(model: dict, x: np.ndarray) -> np.ndarray:
    z = np.hstack([(x - model["mean"]) / model["std"], np.ones((len(x), 1))])
    logits = z @ model["w"]
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    return p / p.sum(axis=1, keepdims=True)


def _score(forecasts: list[dict], outcomes: list[str]) -> dict:
    p, y = calibration.one_vs_rest(forecasts, outcomes)
    hits = sum(max(f, key=f.get) == o for f, o in zip(forecasts, outcomes, strict=True))
    return {
        "n": len(forecasts),
        "log_loss": round(calibration.log_loss(forecasts, outcomes), 5),
        "brier": round(calibration.brier(forecasts, outcomes), 5),
        "ece": round(calibration.ece(p, y), 5),
        "accuracy": round(hits / len(forecasts), 4) if forecasts else None,
    }


def run(
    rows: list[dict],
    *,
    split: Split,
    elo: dict[str, dict],
    goals: dict[str, dict],
) -> dict:
    """``elo``/``goals``: as-of 1X2 forecasts per match id (served values of A and B)."""
    feats = features(rows)
    by_id = {r["id"]: r for r in rows}
    common = [k for k in by_id if k in elo and k in goals and k in feats]

    def window(name):
        return [k for k in common if split.window(by_id[k]["match_date"]) == name]

    fit_c, fit_d = window("calibration"), window("validation") + window("calibration")
    test = {"selection": window("selection"), "holdout": window("holdout")}
    if min(len(fit_c), len(test["selection"])) < 100:
        raise ValueError("Za mało wspólnych meczów w oknach protokołu v2 dla ablacji")

    def x_c(ids):
        return np.array(
            [[math.log(max(1e-6, goals[k][o])) for o in OUTCOMES] + feats[k] for k in ids]
        )

    def x_d(ids):
        return np.array(
            [
                [math.log(max(1e-6, elo[k]["home"])) - math.log(max(1e-6, elo[k]["away"]))]
                + feats[k]
                for k in ids
            ]
        )

    y = {
        name: np.array([_outcome(by_id[k]) for k in ids])
        for name, ids in {"c": fit_c, "d": fit_d}.items()
    }
    model_c = fit_softmax(x_c(fit_c), y["c"])
    model_d = fit_softmax(x_d(fit_d), y["d"])
    report = {
        "split": split.as_dict(),
        "features": [
            "punkty/mecz (5 ostatnich) gosp.",
            "bilans bramek (5) gosp.",
            "dni przerwy gosp.",
            "punkty/mecz (5 ostatnich) gości",
            "bilans bramek (5) gości",
            "dni przerwy gości",
        ],
        "windows": {},
    }
    for name, ids in test.items():
        if not ids:
            continue
        outcomes = [OUTCOMES[_outcome(by_id[k])] for k in ids]
        pc = predict_softmax(model_c, x_c(ids))
        pd_ = predict_softmax(model_d, x_d(ids))
        report["windows"][name] = {
            "A_elo": _score([elo[k] for k in ids], outcomes),
            "B_dixon_coles": _score([goals[k] for k in ids], outcomes),
            "C_dc_features": _score(
                [dict(zip(OUTCOMES, map(float, row), strict=True)) for row in pc], outcomes
            ),
            "D_simple_ml": _score(
                [dict(zip(OUTCOMES, map(float, row), strict=True)) for row in pd_], outcomes
            ),
        }
    selection = report["windows"].get("selection", {})
    if selection:
        best = min(selection, key=lambda m: selection[m]["log_loss"])
        base = selection["B_dixon_coles"]["log_loss"]
        report["conclusion"] = (
            f"Najniższy log loss w oknie selekcji: {best}. Cechy formy i przerwy zmieniają log "
            f"loss Dixona–Colesa o {selection['C_dc_features']['log_loss'] - base:+.5f} "
            "(ujemna wartość = poprawa). Holdout końcowy tylko raportowany."
        )
    return report
