"""Probability laboratory (TASK 005): Elo vs Dixon–Coles on the same matches, calibration,
predictive validation per market family, and pre-match probabilities for the STS offer.

Procedure — no peeking at the holdout:
* chronological split (validation, then holdout); a Dixon–Coles fit for a match uses only
  matches played before the fit date (weekly refits, as-of);
* the decay half-life is chosen on validation; isotonic calibration is fitted on validation;
* the holdout is evaluated once and decides the PREDICTIVE status of each market family.

"Zwalidowany predykcyjnie" says the probabilities are accurate and calibrated out of sample;
it says nothing about beating STS prices (that economic status stays in the EV policy).
"""

import json
import math
import random
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import combinations
from pathlib import Path

from sports_edge.analysis.policy import Policy
from sports_edge.datasources.competitions import COMPETITIONS
from sports_edge.ingestion.types import iso
from sports_edge.matching import TeamIdentities, alias_map, match_participant
from sports_edge.models import calibration
from sports_edge.models.goals import GoalsMatch, fit, markets
from sports_edge.models.registry import register_version, save_prediction
from sports_edge.offers.text import NameNormalizer
from sports_edge.provenance import code_state, data_snapshot
from sports_edge.results_engine import effective_results, known_names, priority
from sports_edge.sportsdata import MAX_DATA_GAP_DAYS, MIN_TEAM_GAMES, Split
from sports_edge.storage import Store

FAMILY = "dixon_coles_goals"
HALF_LIVES = (120.0, 240.0, 480.0)
REFIT_DAYS = 7
WINDOW_DAYS = 730
BIN_EDGES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
# Market families evaluated (and calibrated) separately; selection-side probabilities.
BINARY_FAMILIES = {
    "double_chance": ("double_chance", None, ("home_draw", "draw_away", "home_away")),
    "total_1.5": ("total", 1.5, ("over", "under")),
    "total_2.5": ("total", 2.5, ("over", "under")),
    "total_3.5": ("total", 3.5, ("over", "under")),
    "btts": ("btts", None, ("yes", "no")),
}


@dataclass(frozen=True)
class ProbabilityPolicy:
    version: str
    validation: dict
    profiles: dict
    builder: dict


def load_probability_policy(path: Path) -> ProbabilityPolicy:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return ProbabilityPolicy(
        version=data["policy"]["version"],
        validation=data["validation"],
        profiles=data["profiles"],
        builder=data["builder"],
    )


def family_of(market_key: str, line: float | None) -> str | None:
    if market_key == "1x2":
        return "1x2"
    if market_key == "double_chance":
        return "double_chance"
    if market_key == "btts":
        return "btts"
    if market_key == "total" and line in (1.5, 2.5, 3.5):
        return f"total_{line}"
    return None


def _outcome(h: int, a: int) -> str:
    return "home" if h > a else "away" if h < a else "draw"


def _truth(market_key: str, line: float | None, selection: str, h: int, a: int) -> int:
    side = _outcome(h, a)
    if market_key == "1x2":
        return int(selection == side)
    if market_key == "double_chance":
        return int(
            side
            in {
                "home_draw": {"home", "draw"},
                "draw_away": {"draw", "away"},
                "home_away": {"home", "away"},
            }[selection]
        )
    if market_key == "total":
        return int((h + a > line) == (selection == "over"))
    if market_key == "btts":
        return int((h > 0 and a > 0) == (selection == "yes"))
    raise ValueError(market_key)


def _league_matches(rows: list[dict]) -> dict[str, list[dict]]:
    by_league: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_league[row["competition"]].append(row)
    return by_league


def dc_walk_forward(
    rows: list[dict], *, half_life: float, from_date: str, refit_days: int = REFIT_DAYS
) -> dict[str, dict]:
    """Predictions for every match on/after ``from_date`` from the latest weekly as-of fit."""
    out: dict[str, dict] = {}
    for _code, mine in _league_matches(rows).items():
        mine.sort(key=lambda r: (r["match_date"], r["id"]))
        goals = [
            GoalsMatch(r["match_date"], r["home"], r["away"], r["home_score"], r["away_score"])
            for r in mine
        ]
        start = date.fromisoformat(from_date)
        cache: dict[str, object] = {}
        for row in mine:
            if row["match_date"] < from_date:
                continue
            offset = (date.fromisoformat(row["match_date"]) - start).days // refit_days
            fit_date = (start + timedelta(days=offset * refit_days)).isoformat()
            if fit_date not in cache:
                cache[fit_date] = fit(goals, fit_date=fit_date, half_life_days=half_life)
            model = cache[fit_date]
            if model is None:
                continue
            matrix = model.score_matrix(row["home"], row["away"])
            if matrix is None:
                continue  # a team without history in the window: no forecast
            out[row["id"]] = markets(matrix, LINES)
    return out


def _items(rows_by_id: dict[str, dict], predictions: dict[str, dict], family: str):
    """(p, y) pairs of every selection of a family (one-vs-rest for 1X2)."""
    pairs = []
    for result_id, prediction in predictions.items():
        row = rows_by_id[result_id]
        h, a = row["home_score"], row["away_score"]
        if family == "1x2":
            for sel, p in prediction[("1x2", None)].items():
                pairs.append((p, _truth("1x2", None, sel, h, a)))
            continue
        market_key, line, selections = BINARY_FAMILIES[family]
        for sel in selections:
            p = prediction[(market_key, line)][sel]
            pairs.append((p, _truth(market_key, line, sel, h, a)))
    return pairs


def _binary_metrics(pairs: list[tuple[float, int]], events_per_item: int) -> dict:
    if not pairs:
        return {"n": 0}
    p = [min(max(x, 1e-6), 1 - 1e-6) for x, _ in pairs]
    y = [v for _, v in pairs]
    ll = -sum(math.log(pi) if yi else math.log(1 - pi) for pi, yi in zip(p, y, strict=True))
    brier = sum((pi - yi) ** 2 for pi, yi in zip(p, y, strict=True))
    return {
        "n": len(pairs) // events_per_item,
        "log_loss": round(ll / len(pairs), 5),
        "brier": round(brier / len(pairs), 5),
        "ece": round(calibration.ece(p, y), 5),
    }


def _calibrate(blocks, p: float) -> float:
    return calibration.isotonic_apply(blocks, p)[0] if blocks else p


def _multiclass(predictions: dict[str, dict], rows_by_id: dict, blocks=None) -> dict:
    forecasts, outcomes = [], []
    for result_id, prediction in predictions.items():
        probs = dict(prediction[("1x2", None)])
        if blocks:
            probs = {k: _calibrate(blocks, v) for k, v in probs.items()}
            total = sum(probs.values())
            probs = {k: v / total for k, v in probs.items()}
        forecasts.append(probs)
        row = rows_by_id[result_id]
        outcomes.append(_outcome(row["home_score"], row["away_score"]))
    if not forecasts:
        return {"n": 0}
    p, y = calibration.one_vs_rest(forecasts, outcomes)
    hits = sum(max(f, key=f.get) == o for f, o in zip(forecasts, outcomes, strict=True))
    return {
        "n": len(forecasts),
        "log_loss": round(calibration.log_loss(forecasts, outcomes), 5),
        "brier": round(calibration.brier(forecasts, outcomes), 5),
        "ece": round(calibration.ece(p, y), 5),
        "accuracy": round(hits / len(forecasts), 4),
    }


def _season(day: str) -> str:
    year, month = int(day[:4]), int(day[5:7])
    start = year if month >= 7 else year - 1
    return f"{start}-{str(start + 1)[2:]}"


def run_probability_experiment(
    store: Store,
    *,
    competitions: tuple[str, ...],
    split: Split,
    now: datetime,
    policy: Policy,
    probability_policy: ProbabilityPolicy,
    elo_forecasts: dict[str, dict] | None = None,
    register: bool = True,
    identities: TeamIdentities | None = None,
    elo_version_id: str | None = None,
) -> dict:
    """Dixon–Coles walk-forward, calibration on validation, one holdout evaluation.

    ``elo_forecasts`` (as-of forecasts of the Elo version ``elo_version_id``) are the
    benchmark and are judged by the same predictive rules.
    """
    rows = effective_results(
        store, sport="football", until=now, competitions=competitions, identities=identities
    )
    if not rows:
        raise ValueError("Brak danych historycznych dla wybranych rozgrywek")
    rows_by_id = {r["id"]: r for r in rows}
    if split.protocol == "v2":
        return _run_dc_v2(
            store,
            rows,
            rows_by_id,
            competitions=competitions,
            split=split,
            now=now,
            policy=policy,
            probability_policy=probability_policy,
            elo_forecasts=elo_forecasts or {},
            register=register,
            elo_version_id=elo_version_id,
        )
    validation_ids = {
        r["id"] for r in rows if split.validation_from <= r["match_date"] < split.holdout_from
    }
    holdout_ids = {r["id"] for r in rows if r["match_date"] >= split.holdout_from}
    # 1. half-life chosen on validation (1X2 + total 2.5 + BTTS log loss)
    selection = []
    best = None
    for half_life in HALF_LIVES:
        preds = dc_walk_forward(rows, half_life=half_life, from_date=split.validation_from)
        val = {k: v for k, v in preds.items() if k in validation_ids}
        score = (
            _multiclass(val, rows_by_id)["log_loss"]
            + _binary_metrics(_items(rows_by_id, val, "total_2.5"), 2)["log_loss"]
            + _binary_metrics(_items(rows_by_id, val, "btts"), 2)["log_loss"]
        )
        selection.append({"half_life_days": half_life, "validation_score": round(score, 5)})
        if best is None or score < best[0]:
            best = (score, half_life, preds)
    _, half_life, preds = best
    val = {k: v for k, v in preds.items() if k in validation_ids}
    hold = {k: v for k, v in preds.items() if k in holdout_ids}
    # 2. calibration maps fitted on validation only
    calibrators = {
        family: calibration.isotonic_fit(*zip(*_items(rows_by_id, val, family), strict=True))
        for family in ("1x2", *BINARY_FAMILIES)
    }
    # 3. holdout: raw vs calibrated, ranges with Wilson intervals, seasons, baselines
    families: dict[str, dict] = {}
    for family in ("1x2", *BINARY_FAMILIES):
        events = 3 if family in ("1x2", "double_chance") else 2
        raw_pairs = _items(rows_by_id, hold, family)
        cal_pairs = [(_calibrate(calibrators[family], p), y) for p, y in raw_pairs]
        side = [(p, y) for p, y in cal_pairs if p >= 0.5]
        base = _baseline(rows, split, family, hold, rows_by_id)
        seasons = defaultdict(list)
        for result_id in hold:
            seasons[_season(rows_by_id[result_id]["match_date"])].append(result_id)
        by_season = {}
        for season, ids in sorted(seasons.items()):
            subset = {k: hold[k] for k in ids}
            pairs = [
                (_calibrate(calibrators[family], p), y)
                for p, y in _items(rows_by_id, subset, family)
            ]
            by_season[season] = _binary_metrics(pairs, events)
        families[family] = {
            "raw": _binary_metrics(raw_pairs, events),
            "calibrated": _binary_metrics(cal_pairs, events),
            "baseline_log_loss": base,
            "ranges": calibration.bin_report([p for p, _ in side], [y for _, y in side], BIN_EDGES),
            "by_season": by_season,
        }
        if family == "1x2":
            families[family]["multiclass_raw"] = _multiclass(hold, rows_by_id)
            families[family]["multiclass_calibrated"] = _multiclass(
                hold, rows_by_id, calibrators["1x2"]
            )
    predictive = {
        family: predictive_status(info, probability_policy.validation)
        for family, info in families.items()
    }
    comparison = {}
    for name, part in (("validation", val), ("holdout", hold)):
        common = sorted(set(elo_forecasts or {}) & set(part))
        if not common:
            continue
        outcomes = [
            _outcome(rows_by_id[k]["home_score"], rows_by_id[k]["away_score"]) for k in common
        ]
        comparison[name] = {
            "n_common": len(common),
            "elo": _summary([elo_forecasts[k] for k in common], outcomes),
            "dixon_coles": _summary([part[k][("1x2", None)] for k in common], outcomes),
        }
    # The served 1X2 model (Elo) is judged by the same predictive rules, uncalibrated.
    elo_families, elo_predictive = {}, {}
    if elo_forecasts:
        elo_hold = {
            k: {("1x2", None): f, ("double_chance", None): _double_chance(f)}
            for k, f in elo_forecasts.items()
            if k in hold
        }
        for family in ("1x2", "double_chance"):
            pairs = _items(rows_by_id, elo_hold, family)
            side = [(p, y) for p, y in pairs if p >= 0.5]
            seasons = defaultdict(list)
            for result_id in elo_hold:
                seasons[_season(rows_by_id[result_id]["match_date"])].append(result_id)
            info = {
                "raw": _binary_metrics(pairs, 3),
                "calibrated": _binary_metrics(pairs, 3),  # Elo is used uncalibrated
                "baseline_log_loss": _baseline(rows, split, family, elo_hold, rows_by_id),
                "ranges": calibration.bin_report(
                    [p for p, _ in side], [y for _, y in side], BIN_EDGES
                ),
                "by_season": {
                    season: _binary_metrics(
                        _items(rows_by_id, {k: elo_hold[k] for k in ids}, family), 3
                    )
                    for season, ids in sorted(seasons.items())
                },
            }
            elo_families[family] = info
            elo_predictive[family] = predictive_status(info, probability_policy.validation)
    served = [
        "total",
        "btts",
        "home_to_score",
        "away_to_score",
        "team_total_home",
        "team_total_away",
    ]
    decision = comparison.get("validation")
    # Which model serves 1X2 (and the double chance derived from it) is decided on VALIDATION;
    # the holdout comparison is reported only.
    if decision is None or decision["dixon_coles"]["log_loss"] <= decision["elo"]["log_loss"]:
        served = ["1x2", *served]
    report = {
        "family": FAMILY,
        "competitions": list(competitions),
        "split": {"validation_from": split.validation_from, "holdout_from": split.holdout_from},
        "half_life_days": half_life,
        "selection": selection,
        "refit_days": REFIT_DAYS,
        "window_days": WINDOW_DAYS,
        "n": {"validation": len(val), "holdout": len(hold)},
        "families": families,
        "predictive": predictive,
        "comparison_vs_elo": comparison or None,
        "elo_families": elo_families,
        "elo_predictive": elo_predictive,
        "elo_version_id": elo_version_id,
        "calibrators": calibrators,
        "markets_served": served,
        "holdout": families["1x2"].get("multiclass_calibrated", {}),
        "leakage_checked": True,
        "ordering_rule": "dopasowanie co 7 dni wyłącznie na meczach sprzed daty dopasowania",
        "data_snapshot": data_snapshot(r["id"] for r in rows),
        "code_state": code_state(),
        "data_until": iso(max(datetime.fromisoformat(r["available_at"]) for r in rows)),
        "providers": sorted({r["provider_resolved"] for r in rows}, key=priority),
    }
    report["by_league"] = _league_gates(
        rows,
        split,
        hold,
        rows_by_id,
        calibrators,
        elo_forecasts or {},
        competitions,
        min_n=int(probability_policy.validation["min_holdout_n"]),
    )
    if register:
        version = f"dc-goals-{'-'.join(competitions)}-{now:%Y%m%d%H%M}"
        version_id, status, reasons = register_version(
            store,
            family=FAMILY,
            sport="football",
            description="Dixon–Coles (gole), rynki z macierzy wyników, kalibracja izotoniczna",
            version=version,
            created_at=now,
            trained_through=max(datetime.fromisoformat(r["available_at"]) for r in rows),
            training_data={
                "competitions": list(competitions),
                "data_snapshot": report["data_snapshot"],
                "code_state": report["code_state"],
                "half_life_days": half_life,
            },
            validation=report,
            policy=policy,
        )
        report.update(version_id=version_id, status=status, status_reasons=reasons)
    return report


def served_view(prediction: dict, calibrators: dict) -> dict:
    """Markets of one forecast exactly as they are served (TASK 007): calibrated 1X2
    normalised, binary markets as complements, double chance derived from the final 1X2.
    Protocol v2 evaluates these values, so the validated numbers are the served numbers."""
    one, interval = calibrated_market(prediction[("1x2", None)], calibrators.get("1x2"))
    out = {
        ("1x2", None): one,
        ("double_chance", None): derived_double_chance(one, interval, "")["calibrated"],
    }
    for family, (market_key, line, _selections) in BINARY_FAMILIES.items():
        if family != "double_chance":
            out[(market_key, line)] = calibrated_market(
                prediction[(market_key, line)], calibrators.get(family)
            )[0]
    return out


def _family_report(rows, rows_by_id, raw: dict, view: dict, *, before: str) -> dict:
    """Metrics of every family in one window: raw model vs served probabilities."""
    families: dict[str, dict] = {}
    seasons: dict[str, list] = defaultdict(list)
    for result_id in view:
        seasons[_season(rows_by_id[result_id]["match_date"])].append(result_id)
    for family in ("1x2", *BINARY_FAMILIES):
        events = 3 if family in ("1x2", "double_chance") else 2
        pairs = _items(rows_by_id, view, family)
        side = [(p, y) for p, y in pairs if p >= 0.5]
        families[family] = {
            "raw": _binary_metrics(_items(rows_by_id, raw, family), events),
            "calibrated": _binary_metrics(pairs, events),
            "baseline_log_loss": _baseline(rows, None, family, view, rows_by_id, before=before),
            "ranges": calibration.bin_report([p for p, _ in side], [y for _, y in side], BIN_EDGES),
            "by_season": {
                season: _binary_metrics(
                    _items(rows_by_id, {k: view[k] for k in ids}, family), events
                )
                for season, ids in sorted(seasons.items())
            },
        }
    families["1x2"]["multiclass_raw"] = _multiclass(raw, rows_by_id)
    families["1x2"]["multiclass_calibrated"] = _multiclass(view, rows_by_id)
    return families


def _elo_report(rows, rows_by_id, elo_forecasts: dict, ids, *, before: str) -> dict:
    """Elo (uncalibrated) judged by the same rules on the same matches as Dixon–Coles."""
    group = {
        k: {("1x2", None): f, ("double_chance", None): _double_chance(f)}
        for k, f in elo_forecasts.items()
        if k in ids
    }
    if not group:
        return {}
    seasons: dict[str, list] = defaultdict(list)
    for result_id in group:
        seasons[_season(rows_by_id[result_id]["match_date"])].append(result_id)
    out = {}
    for family in ("1x2", "double_chance"):
        pairs = _items(rows_by_id, group, family)
        side = [(p, y) for p, y in pairs if p >= 0.5]
        out[family] = {
            "raw": _binary_metrics(pairs, 3),
            "calibrated": _binary_metrics(pairs, 3),  # Elo is used uncalibrated
            "baseline_log_loss": _baseline(rows, None, family, group, rows_by_id, before=before),
            "ranges": calibration.bin_report([p for p, _ in side], [y for _, y in side], BIN_EDGES),
            "by_season": {
                season: _binary_metrics(_items(rows_by_id, {k: group[k] for k in found}, family), 3)
                for season, found in sorted(seasons.items())
            },
        }
    return out


def _run_dc_v2(
    store: Store,
    rows: list[dict],
    rows_by_id: dict,
    *,
    competitions: tuple[str, ...],
    split: Split,
    now: datetime,
    policy: Policy,
    probability_policy: ProbabilityPolicy,
    elo_forecasts: dict[str, dict],
    register: bool,
    elo_version_id: str | None,
) -> dict:
    """Protocol v2 (TASK 007): half-life on validation, calibration maps on the calibration
    window, statuses / league gates / the 1X2 model on the selection window. The final
    holdout is computed once and reported; nothing is decided on it."""
    windows: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        windows[split.window(r["match_date"])].add(r["id"])
    needed = ("validation", "calibration", "selection")
    if min(len(windows[w]) for w in needed) < 300:
        raise ValueError(
            "Za mało meczów w oknach protokołu v2: "
            + ", ".join(f"{w} {len(windows[w])}" for w in needed)
        )
    # 1. half-life chosen on the validation window
    tuning, best = [], None
    for half_life in HALF_LIVES:
        preds = dc_walk_forward(rows, half_life=half_life, from_date=split.validation_from)
        val = {k: v for k, v in preds.items() if k in windows["validation"]}
        score = (
            _multiclass(val, rows_by_id)["log_loss"]
            + _binary_metrics(_items(rows_by_id, val, "total_2.5"), 2)["log_loss"]
            + _binary_metrics(_items(rows_by_id, val, "btts"), 2)["log_loss"]
        )
        tuning.append({"half_life_days": half_life, "validation_score": round(score, 5)})
        if best is None or score < best[0]:
            best = (score, half_life, preds)
    _, half_life, preds = best
    part = {
        name: {k: v for k, v in preds.items() if k in windows[name]}
        for name in ("calibration", "selection", "holdout")
    }
    # 2. calibration maps fitted on the calibration window only (double chance is derived)
    calibrators = {
        family: calibration.isotonic_fit(
            *zip(*_items(rows_by_id, part["calibration"], family), strict=True)
        )
        for family in ("1x2", *BINARY_FAMILIES)
        if family != "double_chance"
    }
    view = {
        name: {k: served_view(v, calibrators) for k, v in part[name].items()}
        for name in ("selection", "holdout")
    }
    # 3. decisions on the selection window, the holdout is reported only
    families = _family_report(
        rows, rows_by_id, part["selection"], view["selection"], before=split.selection_from
    )
    families_holdout = _family_report(
        rows, rows_by_id, part["holdout"], view["holdout"], before=split.holdout_from
    )
    rules = probability_policy.validation
    predictive = {family: predictive_status(info, rules) for family, info in families.items()}
    elo_families = _elo_report(
        rows, rows_by_id, elo_forecasts, set(view["selection"]), before=split.selection_from
    )
    elo_predictive = {
        family: predictive_status(info, rules) for family, info in elo_families.items()
    }
    comparison = {}
    for name in ("selection", "holdout"):
        common = sorted(set(elo_forecasts) & set(view[name]))
        if not common:
            continue
        outcomes = [
            _outcome(rows_by_id[k]["home_score"], rows_by_id[k]["away_score"]) for k in common
        ]
        comparison[name] = {
            "n_common": len(common),
            "elo": _summary([elo_forecasts[k] for k in common], outcomes),
            "dixon_coles": _summary([view[name][k][("1x2", None)] for k in common], outcomes),
        }
    served = [
        "total",
        "btts",
        "home_to_score",
        "away_to_score",
        "team_total_home",
        "team_total_away",
    ]
    decision = comparison.get("selection")
    if decision is None or decision["dixon_coles"]["log_loss"] <= decision["elo"]["log_loss"]:
        served = ["1x2", *served]
    min_n = int(rules["min_holdout_n"])
    report = {
        "family": FAMILY,
        "protocol": "v2",
        "competitions": list(competitions),
        "split": split.as_dict(),
        "half_life_days": half_life,
        "selection": tuning,
        "refit_days": REFIT_DAYS,
        "window_days": WINDOW_DAYS,
        "n": {name: len(part[name]) for name in part}
        | {"validation": sum(1 for k in preds if k in windows["validation"])},
        "families": families,
        "predictive": predictive,
        "families_holdout": families_holdout,
        "comparison_vs_elo": comparison or None,
        "elo_families": elo_families,
        "elo_predictive": elo_predictive,
        "elo_families_holdout": _elo_report(
            rows, rows_by_id, elo_forecasts, set(view["holdout"]), before=split.holdout_from
        ),
        "elo_version_id": elo_version_id,
        "calibrators": calibrators,
        "markets_served": served,
        "decision": families["1x2"]["multiclass_calibrated"],
        "holdout": families_holdout["1x2"]["multiclass_calibrated"],
        "holdout_decision_free": True,
        "leakage_checked": True,
        "ordering_rule": "dopasowanie co 7 dni wyłącznie na meczach sprzed daty dopasowania",
        "data_snapshot": data_snapshot(r["id"] for r in rows),
        "code_state": code_state(),
        "data_until": iso(max(datetime.fromisoformat(r["available_at"]) for r in rows)),
        "providers": sorted({r["provider_resolved"] for r in rows}, key=priority),
        "interval_method": INTERVAL_METHOD,
    }
    report["by_league"] = _league_gates(
        rows,
        split,
        view["selection"],
        rows_by_id,
        calibrators,
        elo_forecasts,
        competitions,
        min_n=min_n,
        before=split.selection_from,
        served=True,
    )
    report["by_league_holdout"] = _league_gates(
        rows,
        split,
        view["holdout"],
        rows_by_id,
        calibrators,
        elo_forecasts,
        competitions,
        min_n=min_n,
        before=split.holdout_from,
        served=True,
    )
    if register:
        version = f"dc-goals-v2-{'-'.join(competitions)}-{now:%Y%m%d%H%M}"
        version_id, status, reasons = register_version(
            store,
            family=FAMILY,
            sport="football",
            description="Dixon–Coles (gole), rynki z macierzy wyników, kalibracja izotoniczna",
            version=version,
            created_at=now,
            trained_through=max(datetime.fromisoformat(r["available_at"]) for r in rows),
            training_data={
                "competitions": list(competitions),
                "data_snapshot": report["data_snapshot"],
                "code_state": report["code_state"],
                "half_life_days": half_life,
                "split": report["split"],
            },
            validation=report,
            policy=policy,
        )
        report.update(version_id=version_id, status=status, status_reasons=reasons)
    return report


def pair_dependence(
    picks: list[tuple[str, str, float, int]],
    *,
    same_league: bool,
    resamples: int = 2000,
    seed: int = 7,
) -> dict:
    """Do two selections of the same day win together as often as their separate hit rates
    say? ``picks``: (day, competition, forecast, hit).

    The ratio of the joint hit rate to the product of the separate hit rates is ~1 for
    independent matches. Pairs of one day share that day (weather, rotation, schedule), so
    they are not independent observations: the 95% interval of the ratio comes from a block
    bootstrap that resamples whole DAYS (TASK 007 §4.3). A ratio near 1 with an interval
    containing 1 means "no evidence of dependence" — never proof of independence.
    """
    by_day: dict[str, list[tuple[str, float, int]]] = defaultdict(list)
    for day, competition, p, hit in picks:
        by_day[day].append((competition, p, hit))
    blocks = []  # per day: pairs, both hit, first hit, second hit, product of forecasts
    for items in by_day.values():
        n = both = first = second = 0
        forecast = 0.0
        for (c1, p1, y1), (c2, p2, y2) in combinations(items, 2):
            if (c1 == c2) != same_league:
                continue
            n += 1
            both += y1 * y2
            first += y1
            second += y2
            forecast += p1 * p2
        if n:
            blocks.append((n, both, first, second, forecast))
    if not blocks:
        return {"pairs": 0}

    def ratio(sample) -> float | None:
        n = sum(b[0] for b in sample)
        separate = (sum(b[2] for b in sample) / n) * (sum(b[3] for b in sample) / n)
        return (sum(b[1] for b in sample) / n) / separate if separate else None

    n = sum(b[0] for b in blocks)
    both = sum(b[1] for b in blocks)
    separate = (sum(b[2] for b in blocks) / n) * (sum(b[3] for b in blocks) / n)
    rng = random.Random(seed)
    draws = sorted(
        r
        for r in (ratio(rng.choices(blocks, k=len(blocks))) for _ in range(resamples))
        if r is not None
    )
    ci = (
        [round(draws[int(0.025 * len(draws))], 3), round(draws[int(0.975 * len(draws)) - 1], 3)]
        if draws
        else None
    )
    value = round(both / n / separate, 3) if separate else None
    if ci is None or value is None:
        conclusion = "brak danych do oceny"
    elif ci[0] > 1:
        conclusion = "dodatnia zależność (pary trafiają razem częściej, niż wynika z osobnych)"
    elif ci[1] < 1:
        conclusion = "ujemna zależność (pary trafiają razem rzadziej)"
    else:
        conclusion = "brak dowodu zależności — to nie jest dowód niezależności"
    low, high = calibration.wilson_interval(both, n)
    return {
        "pairs": n,
        "days": len(blocks),
        "joint": round(both / n, 4),
        "joint_ci95": [round(low, 4), round(high, 4)],
        "separate_product": round(separate, 4),
        "forecast_product": round(sum(b[4] for b in blocks) / n, 4),
        "ratio": value,
        "ratio_ci95": ci,
        "method": f"bootstrap blokowy po dniach ({resamples} losowań, ziarno {seed})",
        "conclusion": conclusion,
    }


def league_gate(
    pairs: list[tuple[float, int]], events_per_item: int, baseline: float | None, *, min_n: int
) -> dict:
    """Extra check of one market family in one league on the holdout (TASK 006).

    It adds to the pooled predictive validation and never replaces it: enough forecasts of
    this league and log loss below the league's own base rates. Overconfident ranges are
    reported per league but decided on the pooled sample (one range test per league and
    family would fail well-calibrated leagues by chance). A league that fails keeps its
    forecasts experimental.
    """
    metrics = _binary_metrics(pairs, events_per_item)
    side = [(p, y) for p, y in pairs if p >= 0.5]
    ranges = calibration.bin_report([p for p, _ in side], [y for _, y in side], BIN_EDGES)
    reasons = []
    if metrics.get("n", 0) < min_n:
        reasons.append(f"za mało prognoz w holdoucie ligi: {metrics.get('n', 0)} < {min_n}")
    if baseline is None or metrics.get("log_loss", 9.0) >= baseline:
        reasons.append("nie lepszy od częstości bazowej tej ligi")
    return {
        "n": metrics.get("n", 0),
        "log_loss": metrics.get("log_loss"),
        "baseline_log_loss": baseline,
        "ece": metrics.get("ece"),
        "ok": not reasons,
        "reasons": reasons,
        "overconfident_ranges": [r["range"] for r in ranges if r["overconfident"]],
    }


def _league_gates(
    rows,
    split,
    hold,
    rows_by_id,
    calibrators,
    elo_forecasts,
    competitions,
    *,
    min_n,
    before: str | None = None,
    served: bool = False,
) -> dict:
    """Per-league gates. ``served``: ``hold`` already holds the served probabilities
    (protocol v2); otherwise every side is calibrated here (v1)."""
    out: dict[str, dict] = {}
    for code in competitions:
        ids = [k for k in hold if rows_by_id[k]["competition"] == code]
        if not ids:
            continue
        subset = {k: hold[k] for k in ids}
        entry: dict[str, dict] = {"dc": {}, "elo": {}}
        for family in ("1x2", *BINARY_FAMILIES):
            events = 3 if family in ("1x2", "double_chance") else 2
            pairs = (
                _items(rows_by_id, subset, family)
                if served
                else [
                    (_calibrate(calibrators[family], p), y)
                    for p, y in _items(rows_by_id, subset, family)
                ]
            )
            base = _baseline(rows, split, family, subset, rows_by_id, before=before)
            entry["dc"][family] = league_gate(pairs, events, base, min_n=min_n)
        elo = {
            k: {
                ("1x2", None): elo_forecasts[k],
                ("double_chance", None): _double_chance(elo_forecasts[k]),
            }
            for k in ids
            if k in elo_forecasts
        }
        if elo:
            for family in ("1x2", "double_chance"):
                base = _baseline(rows, split, family, elo, rows_by_id, before=before)
                entry["elo"][family] = league_gate(
                    _items(rows_by_id, elo, family), 3, base, min_n=min_n
                )
        out[code] = entry
    return out


def league_status(validation: dict, competition: str | None, model: str, family: str) -> dict:
    """Per-league gate of a family (``model``: "dc" or "elo"); {} when not evaluated."""
    if not competition:
        return {}
    return (((validation.get("by_league") or {}).get(competition) or {}).get(model) or {}).get(
        family
    ) or {}


def _double_chance(probs: dict[str, float]) -> dict[str, float]:
    return {
        "home_draw": probs["home"] + probs["draw"],
        "draw_away": probs["draw"] + probs["away"],
        "home_away": probs["home"] + probs["away"],
    }


def _summary(forecasts: list[dict], outcomes: list[str]) -> dict:
    p, y = calibration.one_vs_rest(forecasts, outcomes)
    hits = sum(max(f, key=f.get) == o for f, o in zip(forecasts, outcomes, strict=True))
    return {
        "log_loss": round(calibration.log_loss(forecasts, outcomes), 5),
        "brier": round(calibration.brier(forecasts, outcomes), 5),
        "ece": round(calibration.ece(p, y), 5),
        "accuracy": round(hits / len(forecasts), 4),
    }


def _baseline(rows, split, family, hold, rows_by_id, *, before: str | None = None):
    """Log loss of the per-league base rates from matches before the evaluated window
    (``before``; the holdout start in protocol v1)."""
    cutoff = before or split.holdout_from
    train = [r for r in rows if r["match_date"] < cutoff]
    if not train or not hold:
        return None
    rates: dict[tuple, float] = {}
    by_league = _league_matches(train)
    for code, mine in by_league.items():
        if family == "1x2":
            for sel in ("home", "draw", "away"):
                rates[(code, sel)] = sum(
                    _truth("1x2", None, sel, r["home_score"], r["away_score"]) for r in mine
                ) / len(mine)
        else:
            market_key, line, selections = BINARY_FAMILIES[family]
            for sel in selections:
                rates[(code, sel)] = sum(
                    _truth(market_key, line, sel, r["home_score"], r["away_score"]) for r in mine
                ) / len(mine)
    pairs = []
    for result_id in hold:
        row = rows_by_id[result_id]
        selections = ("home", "draw", "away") if family == "1x2" else BINARY_FAMILIES[family][2]
        market_key, line = ("1x2", None) if family == "1x2" else BINARY_FAMILIES[family][:2]
        for sel in selections:
            p = rates.get((row["competition"], sel))
            if p is not None:
                pairs.append(
                    (p, _truth(market_key, line, sel, row["home_score"], row["away_score"]))
                )
    events = 3 if family in ("1x2", "double_chance") else 2
    return _binary_metrics(pairs, events).get("log_loss")


def predictive_status(info: dict, rules: dict) -> dict:
    """'validated_predictive' only when every rule passes on the holdout."""
    reasons = []
    cal = info["calibrated"]
    if cal.get("n", 0) < rules["min_holdout_n"]:
        reasons.append(f"Za mało prognoz poza próbą: {cal.get('n', 0)} < {rules['min_holdout_n']}")
    if cal.get("ece") is None or cal["ece"] > rules["max_ece"]:
        reasons.append(f"ECE {cal.get('ece')} > {rules['max_ece']}")
    base = info.get("baseline_log_loss")
    if rules.get("require_beats_baseline") and (base is None or cal.get("log_loss", 9) >= base):
        reasons.append(f"Log loss {cal.get('log_loss')} nie jest niższy od bazowego {base}")
    worst = max((s.get("ece") or 0 for s in info["by_season"].values()), default=0)
    if worst > rules["max_season_ece"]:
        reasons.append(f"Sezon z ECE {worst} > {rules['max_season_ece']} (niestabilność)")
    if rules.get("forbid_overconfident_bins"):
        bad = [r["range"] for r in info["ranges"] if r["overconfident"]]
        if bad:
            reasons.append("Nadmierna pewność w przedziałach: " + ", ".join(bad))
    skill = round(base - cal["log_loss"], 5) if base is not None and "log_loss" in cal else None
    return {
        "status": "validated_predictive" if not reasons else "experimental",
        "reasons": reasons,
        "skill_vs_baseline": skill,
        "note": "przewaga nad częstością bazową minimalna"
        if skill is not None and 0 <= skill < 0.005
        else None,
    }


# ---------------------------------------------------------------- prospective forecasts


def predict_offer_goals(
    store: Store,
    *,
    version_id: str,
    events: list[dict],
    normalizer: NameNormalizer,
    now: datetime,
    team_aliases: dict[tuple[str, str], str] | None = None,
    identities: TeamIdentities | None = None,
) -> dict:
    """Pre-match probabilities of the markets served by the Dixon–Coles version."""
    version = store.get("model_versions", version_id)
    if version is None:
        raise ValueError("Nieznana wersja modelu")
    validation = json.loads(version["validation_json"])
    competitions = tuple(validation["competitions"])
    served = set(validation["markets_served"])
    calibrators = validation["calibrators"]
    predictive = validation["predictive"]
    rows = effective_results(
        store, sport="football", until=now, competitions=competitions, identities=identities
    )
    providers = sorted({r["provider_resolved"] for r in rows}, key=priority)
    snapshot = data_snapshot(r["id"] for r in rows)
    state = code_state()
    info_cutoff = iso(max(datetime.fromisoformat(r["available_at"]) for r in rows))
    aliases = alias_map(store, team_aliases)
    by_league = _league_matches(rows)
    fit_date = (now.date() + timedelta(days=1)).isoformat()
    fits, known, last = {}, {}, {}
    for code in competitions:
        mine = sorted(by_league.get(code, []), key=lambda r: r["match_date"])
        goals = [
            GoalsMatch(r["match_date"], r["home"], r["away"], r["home_score"], r["away_score"])
            for r in mine
        ]
        fits[code] = fit(goals, fit_date=fit_date, half_life_days=validation["half_life_days"])
        known[code] = known_names(mine, "football", normalizer)
        last[code] = mine[-1]["match_date"] if mine else None
    saved, kept, skipped = 0, 0, []
    for event in events:
        competition = next(
            (c for c in competitions if event.get("league") in COMPETITIONS[c].sts_leagues), None
        )
        if event.get("sport") != "football" or competition is None:
            continue
        label = f"{event.get('home')} - {event.get('away')}"
        if not event.get("start_at") or datetime.fromisoformat(event["start_at"]) <= now:
            skipped.append(f"{label}: wydarzenie rozpoczęte lub bez godziny")
            continue
        model = fits.get(competition)
        if model is None:
            skipped.append(f"{label}: za mało danych do dopasowania modelu")
            continue
        gap = last[competition]
        event_day = datetime.fromisoformat(event["start_at"]).date()
        if gap is None or (event_day - date.fromisoformat(gap)).days > MAX_DATA_GAP_DAYS:
            skipped.append(f"{label}: nieaktualne dane (ostatni wynik {gap})")
            continue
        pair = []
        for side in ("home", "away"):
            found = match_participant(
                event[side] or "", "football", known[competition], normalizer, aliases
            )
            if found.status in {"exact", "alias"}:
                pair.append(found.target)
        if len(pair) != 2:
            skipped.append(f"{label}: brak zweryfikowanego dopasowania nazw")
            continue
        games = (model.games.get(pair[0], 0), model.games.get(pair[1], 0))
        if min(games) < MIN_TEAM_GAMES:
            skipped.append(f"{label}: za mało meczów w oknie modelu ({min(games)})")
            continue
        matrix = model.score_matrix(*pair)
        if matrix is None:
            skipped.append(f"{label}: drużyna poza modelem")
            continue
        priced = markets(matrix, LINES)
        for (market_key, line), probs in priced.items():
            if market_key == "double_chance" or market_key not in (served | {"1x2"}):
                continue  # double chance travels inside the 1X2 row (its sides sum to two)
            text_line = f"{line:.1f}" if line is not None else None
            if _already(store, version_id, event["id"], market_key, text_line, snapshot):
                kept += 1
                continue
            family = family_of(market_key, line)
            blocks = calibrators.get(family) if family else None
            calibrated, interval = calibrated_market(probs, blocks)
            conservative = {sel: (interval.get(sel) or [None])[0] for sel in calibrated}
            status = _served_status(predictive, validation, competition, family)
            derived = {}
            if market_key == "1x2":
                derived["double_chance"] = derived_double_chance(
                    calibrated,
                    interval,
                    _served_status(predictive, validation, competition, "double_chance"),
                    raw=priced[("double_chance", None)],
                )
            save_prediction(
                store,
                model_version_id=version_id,
                event_id=event["id"],
                market_key=market_key,
                period="FT",
                line=text_line,
                probabilities=calibrated,
                uncertainty={
                    "raw": probs,
                    "calibrated": calibrated,
                    "conservative": conservative,
                    "interval": interval,
                    "method": INTERVAL_METHOD,
                    "predictive_status": status,
                    "family": family,
                    "derived": derived,
                    "serves": market_key in served,
                },
                inputs={
                    "info_cutoff": info_cutoff,
                    "data_snapshot": snapshot,
                    "code_state": state,
                    "teams": pair,
                    "competition": competition,
                    "games": list(games),
                    "fit_date": fit_date,
                    "data": "wyniki as-of: " + ", ".join(providers),
                },
                kind="prospective",
                created_at=now,
            )
            saved += 1
    return {"saved": saved, "kept": kept, "skipped": skipped}


def _served_status(predictive: dict, validation: dict, competition: str, family) -> str:
    """Pooled predictive status, downgraded when the league fails its own holdout gate."""
    if not family:
        return "experimental"
    status = (predictive.get(family) or {}).get("status", "experimental")
    gate = league_status(validation, competition, "dc", family)
    return "experimental" if gate and not gate["ok"] else status


INTERVAL_METHOD = (
    "izotoniczna kalibracja; przedział Wilsona 90% liczony wokół końcowej wartości "
    "(po normalizacji); w rynkach binarnych druga strona jako dopełnienie (TASK 007)"
)


def _wilson(value: float, n: int) -> list[float] | None:
    """90% Wilson interval of a block with ``n`` forecasts around the FINAL probability.

    The Wilson interval always contains its centre value, so 0 ≤ low ≤ p ≤ high ≤ 1 holds;
    min/max only guard floating-point edges.
    """
    if not n:
        return None
    low, high = calibration.wilson_interval(value * n, n, z=1.645)
    return [max(0.0, min(low, value)), min(1.0, max(high, value))]


def calibrated_market(
    probs: dict[str, float], blocks
) -> tuple[dict[str, float], dict[str, list[float]]]:
    """Calibrated probabilities and consistent intervals of one exclusive market.

    * 3 outcomes (1X2): every side is calibrated, the sides are normalised to sum to one
      and only then each interval is computed around its final value;
    * 2 outcomes: the first side is calibrated, the second is its complement and its
      interval is the complement of the first one (sum = 1, bounds stay consistent);
    * no calibration map: raw probabilities, no interval (the selection then has no
      conservative estimate and never enters a coupon).
    """
    sizes = {}
    raw = {}
    for sel, p in probs.items():
        value, n = calibration.isotonic_apply(blocks, p) if blocks else (p, 0)
        raw[sel], sizes[sel] = value, n
    if len(raw) == 2:
        first, second = list(raw)
        p = min(1.0, max(0.0, raw[first]))
        final = {first: p, second: 1.0 - p}
        bounds = _wilson(p, sizes[first])
        interval = {first: bounds, second: [1.0 - bounds[1], 1.0 - bounds[0]]} if bounds else {}
        return final, interval
    total = sum(raw.values())
    final = {k: v / total for k, v in raw.items()} if total > 0 else dict(raw)
    interval = {}
    for sel, value in final.items():
        bounds = _wilson(value, sizes[sel])
        if bounds:
            interval[sel] = bounds
    return final, interval


# double chance side → the 1X2 outcome it excludes: P(1X) = 1 − P(2)
OPPOSITE = {"home_draw": "away", "draw_away": "home", "home_away": "draw"}


def derived_double_chance(
    probs: dict[str, float], interval: dict[str, list[float]], status: str, *, raw=None
) -> dict:
    """Double chance from the FINAL (calibrated, normalised) 1X2 of the same model.

    P(1X) = 1 − P(2) and its interval is the complement of the interval of the excluded
    outcome, so double chance is coherent with 1X2 by construction (TASK 007). Before
    TASK 007 each side was calibrated separately and could differ from 1 − P(2) by several
    percentage points.
    """
    return {
        "raw": raw,
        "calibrated": {sel: 1.0 - probs[out] for sel, out in OPPOSITE.items() if out in probs},
        "interval": {
            sel: [1.0 - interval[out][1], 1.0 - interval[out][0]]
            for sel, out in OPPOSITE.items()
            if out in interval
        },
        "predictive_status": status,
        "method": INTERVAL_METHOD,
    }


def _already(store, version_id, event_id, market_key, line, snapshot) -> bool:
    """The same model, data and probability method already produced this forecast."""
    for row in store.rows(
        """SELECT inputs_json, uncertainty_json FROM predictions WHERE model_version_id=?
        AND event_id=? AND market_key=? AND period='FT' AND line IS ? AND kind='prospective'""",
        (version_id, event_id, market_key, line),
    ):
        same_data = json.loads(row["inputs_json"]).get("data_snapshot") == snapshot
        method = json.loads(row["uncertainty_json"] or "{}").get("method")
        if same_data and method == INTERVAL_METHOD:
            return True
    return False
