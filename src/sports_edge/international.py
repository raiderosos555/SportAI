"""International Football Intelligence — models and validation (TASK 009, stages 2–4).

Two separate pools that never share ratings: European club cups ("european") and national
teams ("national"). A domestic league rating enters a cup forecast only through the
calibration tested in variants B and C.

Club cups:

* ``baseline`` — climatology of the fit windows (frequencies of 1X2, goals and BTTS);
* ``A_cup_elo`` — Elo of cup matches only;
* ``B_domestic`` — the domestic Elo of both clubs (own league pool, as of the match day)
  plus a country offset calibrated on cup matches (cross-league calibration); only matches
  of two clubs whose league is in the catalogue (reported on that subset);
* ``C_combined`` — one Elo pool of domestic and cup matches, a separate K for cup matches
  (the level of the leagues is controlled by the cup matches that connect them);
* ``D_goals`` / ``D_goals_A`` — Poisson goals model on the C (or A) rating difference: one
  score distribution for 1X2, double chance, Over/Under 1.5/2.5 and BTTS;
* ``E_stats`` — extra match statistics, run only with enough history (``STAT_MINIMUM``).

National teams: ``baseline``, ``N1`` Elo of official matches, ``N2`` plus friendlies with
half the weight, ``N3`` = N2 without home advantage at neutral venues, ``N_goals``.

Protocol: walk-forward (a day's results count from the next day on), hyperparameters and
the draw margin chosen on the calibration window, the decision on the selection window, the
holdout only reported. A market is VALIDATED only when the best variant beats the baseline
on the selection window with the 95% interval of the paired log-loss difference below zero,
on at least ``MIN_SELECTION`` matches and with ECE ≤ ``MAX_ECE``. Validation activates
nothing: an international market reaches the coupon generator only after the user's
decision, and no such decision exists (``generator_allowed``).
"""

import json
import math
from bisect import bisect_left
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import groupby

import numpy as np

from sports_edge import advanced
from sports_edge.ablation import fit_softmax, predict_softmax
from sports_edge.cups import season_start
from sports_edge.datasources.competitions import COMPETITIONS, by_category
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.results_engine import effective_results
from sports_edge.storage import Store

HYPOTHESIS = "TASK 009 International Football Intelligence"
CUPS = by_category("european")
NATIONAL_OFFICIAL = ("uefa.nl", "uefa.euro", "uefa.euro_q", "fifa.wc", "fifa.wc_q")
FRIENDLY = "fifa.friendly"
MARKETS = advanced.MARKETS
GOAL_MARKETS = advanced.GOAL_MARKETS
MIN_SELECTION = 300
MAX_ECE = 0.03
STAT_MINIMUM = advanced.STAT_MINIMUM
GENERATOR_MESSAGE = "Wydarzenie dostępne — model predykcyjny jeszcze niezwalidowany"
SCOPES = {"european": "Europejskie puchary", "national": "Reprezentacje"}
# club cups: season start year; national teams: calendar dates
CUP_WINDOWS = {
    "fit": (None, 2021),
    "calibration": (2022, 2022),
    "selection": (2023, 2024),
    "holdout": (2025, None),
}
NATION_WINDOWS = {
    "fit": (None, "2021-12-31"),
    "calibration": ("2022-01-01", "2022-12-31"),
    "selection": ("2023-01-01", "2024-12-31"),
    "holdout": ("2025-01-01", None),
}
K_GRID = (16.0, 24.0, 32.0, 40.0)
HOME_GRID = (40.0, 70.0, 100.0)
VARIANT_LABELS = {
    "baseline": "częstości z okna dopasowania (klimatologia)",
    "A_cup_elo": "A: Elo wyłącznie z meczów pucharowych",
    "B_domestic": "B: Elo ligowe + kalibracja siły lig (przesunięcie kraju)",
    "C_combined": "C: wspólna pula liga + puchary z osobnym K pucharów",
    "D_goals": "D: model goli (Poisson) na różnicy oceny C",
    "D_goals_A": "D (wariant A): model goli na różnicy oceny A",
    "N1": "N1: Elo meczów oficjalnych",
    "N2": "N2: N1 + mecze towarzyskie z połową wagi",
    "N3": "N3: N2 bez przewagi gospodarza na neutralnym terenie",
    "N_goals": "model goli (Poisson) na najlepszej ocenie reprezentacji",
}


# ---------------------------------------------------------------- ratings


def elo_walk(
    matches: list[dict], *, k_of, home_of, ratings: dict[str, float] | None = None
) -> dict[str, float]:
    """Pre-match rating difference / 400 of every match (home advantage included).

    All matches of one day are forecast before any of the day's results is applied;
    ``ratings`` (when given) ends with the ratings after the last day."""
    ratings = {} if ratings is None else ratings
    out: dict[str, float] = {}
    for _day, group in groupby(matches, key=lambda m: m["match_date"]):
        group = list(group)
        for m in group:
            rh, ra = ratings.get(m["home"], 1500.0), ratings.get(m["away"], 1500.0)
            out[m["id"]] = (rh - ra + home_of(m)) / 400
        for m in group:
            d = out[m["id"]]
            expected = 1 / (1 + 10 ** (-d))
            h, a = m["home_score"], m["away_score"]
            actual = 1.0 if h > a else 0.5 if h == a else 0.0
            change = k_of(m) * (actual - expected)
            ratings[m["home"]] = ratings.get(m["home"], 1500.0) + change
            ratings[m["away"]] = ratings.get(m["away"], 1500.0) - change
    return out


def elo_probabilities(d: np.ndarray, margin: float) -> np.ndarray:
    home = 1 / (1 + 10 ** (-d + margin))
    away = 1 / (1 + 10 ** (d + margin))
    return np.column_stack([home, np.clip(1 - home - away, 1e-6, None), away])


def fit_margin(d: np.ndarray, y: np.ndarray) -> float:
    """Maximum-likelihood draw margin (grid 0.00–0.60)."""
    best, best_loss = 0.0, math.inf
    for c in np.arange(0.0, 0.61, 0.01):
        p = elo_probabilities(d, float(c))
        p /= p.sum(axis=1, keepdims=True)
        loss = float(advanced.losses("1x2", p, y).mean())
        if loss < best_loss:
            best, best_loss = float(c), loss
    return best


def _normalised(p: np.ndarray) -> np.ndarray:
    return p / p.sum(axis=1, keepdims=True)


# ---------------------------------------------------------------- data


def _window_of(row: dict, windows: dict, *, by_season: bool) -> str | None:
    value = season_start(row.get("season"), row["match_date"]) if by_season else row["match_date"]
    for name, (low, high) in windows.items():
        if (low is None or value >= low) and (high is None or value <= high):
            return name
    return None


def _played(rows: list[dict]) -> list[dict]:
    """Matches whose 90-minute score is a fact (regulation certain), in date order."""
    return sorted(
        (r for r in rows if r.get("regulation_certain", True) and r["status"] == "finished"),
        key=lambda r: (r["match_date"], r.get("start_at") or "", r["id"]),
    )


def _climatology(rows: list[dict]) -> dict[str, np.ndarray]:
    h = np.array([r["home_score"] for r in rows])
    a = np.array([r["away_score"] for r in rows])
    y = np.where(h > a, 0, np.where(h == a, 1, 2))
    return {
        "1x2": np.bincount(y, minlength=3) / len(y),
        "total_1.5": float((h + a > 1.5).mean()),
        "total_2.5": float((h + a > 2.5).mean()),
        "btts": float(((h > 0) & (a > 0)).mean()),
    }


def _outputs_from_1x2(p: np.ndarray) -> dict[str, np.ndarray]:
    return advanced.with_double_chance({"1x2": p})


def _goals_variant(ids, d_by_id, rows_by_id, fit_ids, tune_ids) -> dict[str, np.ndarray]:
    """Poisson goals on one rating difference, rho of Dixon–Coles tuned on ``tune_ids``."""
    x_fit = np.array([[d_by_id[i]] for i in fit_ids])
    home_model = advanced.fit_poisson(
        x_fit, np.array([rows_by_id[i]["home_score"] for i in fit_ids]), None, l2=1e-3
    )
    away_model = advanced.fit_poisson(
        -x_fit, np.array([rows_by_id[i]["away_score"] for i in fit_ids]), None, l2=1e-3
    )

    def markets(sel, rho):
        x = np.array([[d_by_id[i]] for i in sel])
        lam = advanced.predict_poisson(home_model, x, None)
        mu = advanced.predict_poisson(away_model, -x, None)
        return advanced.with_double_chance(advanced.score_markets(lam, mu, np.full(len(sel), rho)))

    tune_truth = advanced.truth(rows_by_id, tune_ids)
    best_rho = min(
        (-0.15, -0.1, -0.05, 0.0, 0.05),
        key=lambda rho: float(
            advanced.losses("1x2", markets(tune_ids, rho)["1x2"], tune_truth["1x2"]).mean()
        ),
    )
    out = markets(ids, best_rho)
    out["_rho"] = np.array([best_rho])
    out["_models"] = {"home": home_model, "away": away_model}
    return out


# ---------------------------------------------------------------- evaluation


def _evaluate(
    outputs: dict[str, dict[str, np.ndarray]],
    ids: list[str],
    rows_by_id: dict,
    windows: dict[str, list[int]],
    *,
    seed: int,
) -> dict:
    """Metrics, paired differences against the baseline and the decision of every market."""
    truths = advanced.truth(rows_by_id, ids)
    report: dict = {}
    for market in MARKETS:
        variants = [v for v, out in outputs.items() if market in out]
        if len(variants) < 2:
            continue
        block = {"windows": {}, "reliability": None}
        for window, idx in windows.items():
            if not idx:
                continue
            y = truths[market][idx]
            base_loss = advanced.losses(market, outputs["baseline"][market][idx], y)
            rows = {}
            for v in variants:
                p = outputs[v][market][idx]
                metrics = advanced.metrics(market, p, y)
                diff = advanced.losses(market, p, y) - base_loss
                metrics["delta_log_loss"] = round(float(diff.mean()), 5)
                metrics["ci95"] = advanced.interval(diff, seed) if v != "baseline" else None
                rows[v] = metrics
            block["windows"][window] = rows
        block["decision"] = _decide(block["windows"].get("selection") or {})
        best = block["decision"].get("best")
        if best and windows.get("selection"):
            idx = windows["selection"]
            block["reliability"] = advanced.reliability(
                market, outputs[best][market][idx], truths[market][idx]
            )
        report[market] = block
    return report


def _decide(selection: dict) -> dict:
    pool = {k: v for k, v in selection.items() if k != "baseline"}
    if not pool:
        return {"best": None, "validated": False, "criteria": [], "verdict": "brak wariantów"}
    best = min(pool, key=lambda k: pool[k]["log_loss"])
    row = pool[best]
    ci = row.get("ci95")
    criteria = [
        {
            "name": f"co najmniej {MIN_SELECTION} meczów w oknie selekcji",
            "value": row["n"],
            "passed": row["n"] >= MIN_SELECTION,
        },
        {
            "name": "log loss lepszy od bazowego (95% przedział różnicy poniżej zera)",
            "value": ci,
            "passed": bool(ci and ci[1] < 0),
        },
        {
            "name": f"kalibracja ECE ≤ {MAX_ECE}",
            "value": row["ece"],
            "passed": row["ece"] <= MAX_ECE,
        },
    ]
    validated = all(c["passed"] for c in criteria)
    return {
        "best": best,
        "validated": validated,
        "criteria": criteria,
        "verdict": (
            "zwalidowany w eksperymencie wstecznym — do generatora tylko po decyzji użytkownika"
            if validated
            else "niezwalidowany — rynek poza generatorem"
        ),
    }


def _index(ids: list[str], window_of: dict[str, str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for position, i in enumerate(ids):
        out[window_of[i]].append(position)
    return dict(out)


# ---------------------------------------------------------------- club cups


def cup_experiment(
    store: Store, *, now: datetime, identities, domestic_identities, seed: int = 9
) -> dict:
    """Variants A–E of the European club cups on the protocol windows."""
    cups = _played(
        effective_results(
            store, sport="football", until=now, competitions=CUPS, identities=identities
        )
    )
    domestic_codes = [
        c
        for c, comp in COMPETITIONS.items()
        if comp.sport == "football" and comp.category == "domestic"
    ]
    domestic = _played(
        effective_results(
            store,
            sport="football",
            until=now,
            competitions=domestic_codes,
            identities=domestic_identities,
        )
    )
    rows_by_id = {r["id"]: r for r in cups}
    window_of = {r["id"]: _window_of(r, CUP_WINDOWS, by_season=True) for r in cups}
    fit_ids = [r["id"] for r in cups if window_of[r["id"]] == "fit"]
    cal_ids = [r["id"] for r in cups if window_of[r["id"]] == "calibration"]
    ids = [r["id"] for r in cups if window_of[r["id"]] in ("calibration", "selection", "holdout")]
    windows = _index(ids, window_of)
    cal_truth = advanced.truth(rows_by_id, cal_ids)["1x2"]
    climate = _climatology([rows_by_id[i] for i in fit_ids + cal_ids])
    n = len(ids)
    outputs: dict[str, dict[str, np.ndarray]] = {
        "baseline": advanced.with_double_chance(
            {
                "1x2": np.tile(climate["1x2"], (n, 1)),
                **{m: np.full(n, climate[m]) for m in GOAL_MARKETS},
            }
        )
    }
    params: dict[str, dict] = {}
    # A: cup matches only
    best_a = None
    for k in K_GRID:
        for home in HOME_GRID:
            d = elo_walk(cups, k_of=lambda _m, k=k: k, home_of=lambda _m, h=home: h)
            dc = np.array([d[i] for i in cal_ids])
            margin = fit_margin(dc, cal_truth)
            loss = float(
                advanced.losses("1x2", _normalised(elo_probabilities(dc, margin)), cal_truth).mean()
            )
            if best_a is None or loss < best_a[0]:
                best_a = (loss, k, home, margin, d)
    _loss, k_a, home_a, margin_a, d_a = best_a
    params["A_cup_elo"] = {"k": k_a, "home_advantage": home_a, "draw_margin": margin_a}
    outputs["A_cup_elo"] = _outputs_from_1x2(
        _normalised(elo_probabilities(np.array([d_a[i] for i in ids]), margin_a))
    )
    # C: one pool of domestic and cup matches, separate K for cups
    combined = sorted(
        [{**r, "_cup": False} for r in domestic] + [{**r, "_cup": True} for r in cups],
        key=lambda r: (r["match_date"], r.get("start_at") or "", r["id"]),
    )
    best_c = None
    for k_cup in K_GRID:
        for home in HOME_GRID:
            d = elo_walk(
                combined,
                k_of=lambda m, kc=k_cup: kc if m["_cup"] else 20.0,
                home_of=lambda m, h=home: h if m["_cup"] else 60.0,
            )
            dc = np.array([d[i] for i in cal_ids])
            margin = fit_margin(dc, cal_truth)
            loss = float(
                advanced.losses("1x2", _normalised(elo_probabilities(dc, margin)), cal_truth).mean()
            )
            if best_c is None or loss < best_c[0]:
                best_c = (loss, k_cup, home, margin, d)
    _loss, k_c, home_c, margin_c, d_c = best_c
    params["C_combined"] = {
        "k_cup": k_c,
        "k_domestic": 20.0,
        "home_advantage_cup": home_c,
        "home_advantage_domestic": 60.0,
        "draw_margin": margin_c,
    }
    outputs["C_combined"] = _outputs_from_1x2(
        _normalised(elo_probabilities(np.array([d_c[i] for i in ids]), margin_c))
    )
    # D: goals models on the C and A differences
    recent_fit = [
        i
        for i in fit_ids
        if season_start(rows_by_id[i]["season"], rows_by_id[i]["match_date"]) >= 2016
    ]
    goals_c = _goals_variant(ids, d_c, rows_by_id, recent_fit, cal_ids)
    goals_a = _goals_variant(ids, d_a, rows_by_id, recent_fit, cal_ids)
    params["D_goals"] = {
        "rho": float(goals_c.pop("_rho")[0]),
        "fit_seasons": "2016/17–2021/22",
        "models": goals_c.pop("_models"),
    }
    params["D_goals_A"] = {"rho": float(goals_a.pop("_rho")[0]), "models": goals_a.pop("_models")}
    outputs["D_goals"], outputs["D_goals_A"] = goals_c, goals_a
    report = {
        "scope": "european",
        "competitions": list(CUPS),
        "matches": {w: len(v) for w, v in windows.items()} | {"fit": len(fit_ids)},
        "excluded_uncertain_90": sum(
            1
            for r in effective_results(store, sport="football", until=now, competitions=CUPS)
            if not r.get("regulation_certain", True)
        ),
        "params": params,
        "markets": _evaluate(outputs, ids, rows_by_id, windows, seed=seed),
        "subset_b": _variant_b(domestic, ids, rows_by_id, window_of, outputs, seed=seed),
        "e_stats": _e_stats(store),
    }
    return report


def _variant_b(domestic, ids, rows_by_id, window_of, outputs, *, seed: int) -> dict:
    """B on the matches of two clubs with a domestic league: league Elo + country offset."""
    country_of: dict[str, str] = {}
    history: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for code in sorted({r["competition"] for r in domestic}):
        league = [r for r in domestic if r["competition"] == code]
        ratings: dict[str, float] = {}
        for day, group in groupby(league, key=lambda r: r["match_date"]):
            group = list(group)
            for r in group:
                rh, ra = ratings.get(r["home"], 1500.0), ratings.get(r["away"], 1500.0)
                expected = 1 / (1 + 10 ** (-(rh + 60 - ra) / 400))
                actual = (
                    1.0
                    if r["home_score"] > r["away_score"]
                    else (0.5 if r["home_score"] == r["away_score"] else 0.0)
                )
                change = 20 * (actual - expected)
                ratings[r["home"]], ratings[r["away"]] = rh + change, ra - change
            for team in {t for r in group for t in (r["home"], r["away"])}:
                history[team].append((day, ratings[team]))
                country_of[team] = COMPETITIONS[code].country

    def rating(team: str, day: str) -> float | None:
        items = history.get(team)
        if not items:
            return None
        position = bisect_left(items, (day, -math.inf))
        return items[position - 1][1] if position else None

    countries = sorted(set(country_of.values()))
    features, kept = [], []
    recent = [
        i
        for i, r in rows_by_id.items()
        if window_of[i] == "fit" and season_start(r["season"], r["match_date"]) >= 2016
    ]
    for i in [*recent, *ids]:
        r = rows_by_id[i]
        rh, ra = rating(r["home"], r["match_date"]), rating(r["away"], r["match_date"])
        if rh is None or ra is None:
            continue
        offset = [
            (country_of[r["home"]] == c) - (country_of[r["away"]] == c) for c in countries[1:]
        ]
        features.append([(rh - ra) / 400, *offset])
        kept.append(i)
    if not kept:
        return {"run": False, "reason": "brak meczów dwóch klubów z ligami krajowymi"}
    x = np.array(features, dtype=float)
    position = {i: n for n, i in enumerate(ids)}
    y = advanced.truth(rows_by_id, kept)["1x2"]
    win = np.array([window_of[i] for i in kept])
    fit_mask = (win == "fit") | (win == "calibration")
    if fit_mask.sum() < 200:
        return {"run": False, "reason": f"za mało meczów do kalibracji lig ({fit_mask.sum()})"}
    model = fit_softmax(x[fit_mask], y[fit_mask], l2=1e-2)
    p_b = predict_softmax(model, x)
    out = {"run": True, "matches": int(len(kept)), "countries": countries, "windows": {}}
    for window in ("selection", "holdout"):
        mask = win == window
        if not mask.any():
            continue
        rows = {}
        idx = [position[i] for i, keep in zip(kept, mask, strict=True) if keep]
        base = advanced.losses("1x2", outputs["baseline"]["1x2"][idx], y[mask])
        for name, p in (
            ("B_domestic", p_b[mask]),
            ("A_cup_elo", outputs["A_cup_elo"]["1x2"][idx]),
            ("C_combined", outputs["C_combined"]["1x2"][idx]),
            ("baseline", outputs["baseline"]["1x2"][idx]),
        ):
            metrics = advanced.metrics("1x2", p, y[mask])
            diff = advanced.losses("1x2", p, y[mask]) - base
            metrics["delta_log_loss"] = round(float(diff.mean()), 5)
            metrics["ci95"] = advanced.interval(diff, seed) if name != "baseline" else None
            rows[name] = metrics
        out["windows"][window] = rows
    out["note"] = (
        "B oceniany tylko na meczach dwóch klubów z lig krajowych katalogu; przesunięcia krajów "
        "dopasowane na sezonach 2016/17–2022/23, bez danych z okna selekcji"
    )
    return out


def _e_stats(store: Store) -> dict:
    marks = ",".join("?" for _ in CUPS)
    found = store.scalar(
        f"""SELECT COUNT(DISTINCT entity_key) FROM stat_observations
        WHERE kind='event_statistics' AND competition IN ({marks})""",
        tuple(CUPS),
    )
    return {
        "run": found >= STAT_MINIMUM,
        "matches_with_statistics": found,
        "reason": (
            f"za mało meczów pucharowych ze statystykami ({found} < {STAT_MINIMUM})"
            if found < STAT_MINIMUM
            else "wystarczająca historia"
        ),
    }


# ---------------------------------------------------------------- national teams


def national_experiment(store: Store, *, now: datetime, seed: int = 11) -> dict:
    rows = effective_results(
        store, sport="football", until=now, competitions=(*NATIONAL_OFFICIAL, FRIENDLY)
    )
    played = _played(rows)
    for r in played:
        details = json.loads(r.get("details_json") or "{}")
        venue = details.get("venue_country")
        r["_neutral"] = bool(venue) and venue not in (r["home"], r["away"])
        r["_friendly"] = r["competition"] == FRIENDLY
    official = [r for r in played if not r["_friendly"]]
    rows_by_id = {r["id"]: r for r in official}
    window_of = {r["id"]: _window_of(r, NATION_WINDOWS, by_season=False) for r in official}
    fit_ids = [i for i in rows_by_id if window_of[i] == "fit"]
    cal_ids = [i for i in rows_by_id if window_of[i] == "calibration"]
    ids = [
        r["id"] for r in official if window_of[r["id"]] in ("calibration", "selection", "holdout")
    ]
    windows = _index(ids, window_of)
    cal_truth = advanced.truth(rows_by_id, cal_ids)["1x2"]
    climate = _climatology([rows_by_id[i] for i in fit_ids + cal_ids])
    n = len(ids)
    outputs = {
        "baseline": advanced.with_double_chance(
            {
                "1x2": np.tile(climate["1x2"], (n, 1)),
                **{m: np.full(n, climate[m]) for m in GOAL_MARKETS},
            }
        )
    }
    params, diffs = {}, {}
    for key, pool, friendly_weight, neutral in (
        ("N1", official, 0.0, False),
        ("N2", played, 0.5, False),
        ("N3", played, 0.5, True),
    ):
        best = None
        for k in K_GRID:
            for home in HOME_GRID:
                d = elo_walk(
                    pool,
                    k_of=lambda m, k=k, w=friendly_weight: k * (w if m["_friendly"] else 1.0),
                    home_of=lambda m, h=home, z=neutral: 0.0 if z and m["_neutral"] else h,
                )
                dc = np.array([d[i] for i in cal_ids])
                margin = fit_margin(dc, cal_truth)
                loss = float(
                    advanced.losses(
                        "1x2", _normalised(elo_probabilities(dc, margin)), cal_truth
                    ).mean()
                )
                if best is None or loss < best[0]:
                    best = (loss, k, home, margin, d)
        _loss, k, home, margin, d = best
        params[key] = {
            "k": k,
            "home_advantage": home,
            "draw_margin": margin,
            "friendly_weight": friendly_weight,
            "neutral_venues": neutral,
        }
        outputs[key] = _outputs_from_1x2(
            _normalised(elo_probabilities(np.array([d[i] for i in ids]), margin))
        )
        diffs[key] = (best[0], d)
    best_key = min(diffs, key=lambda key: diffs[key][0])
    goals = _goals_variant(ids, diffs[best_key][1], rows_by_id, fit_ids[-1500:], cal_ids)
    params["N_goals"] = {
        "rating": best_key,
        "rho": float(goals.pop("_rho")[0]),
        "models": goals.pop("_models"),
    }
    outputs["N_goals"] = goals
    return {
        "scope": "national",
        "competitions": list(NATIONAL_OFFICIAL),
        "friendlies_used": sum(1 for r in played if r["_friendly"]),
        "matches": {w: len(v) for w, v in windows.items()} | {"fit": len(fit_ids)},
        "excluded_uncertain_90": sum(1 for r in rows if not r.get("regulation_certain", True)),
        "params": params,
        "markets": _evaluate(outputs, ids, rows_by_id, windows, seed=seed),
        "not_available": [
            "zmiany selekcjonera — brak źródła danych",
            "dostępność zawodników reprezentacji — brak źródła danych",
        ],
    }


# ---------------------------------------------------------------- schedule (stage 2)

SCHEDULE_WINDOWS = {
    "fit": ("2021-07-01", "2023-06-30"),
    "selection": ("2023-07-01", "2024-06-30"),
    "holdout": ("2024-07-01", None),
}


def schedule_experiment(
    store: Store, *, now: datetime, identities, domestic_identities, seed: int = 13
) -> dict:
    """Does the schedule WITH European matches improve league forecasts?

    League matches of clubs that played in a UEFA cup in the same season; softmax on the
    league Elo difference plus rest days and matches in 14 days computed from (L) league
    matches only, (X) league + cup matches, (X7) X plus a European match in the last 7 days.
    Fitted on 2021/22–2022/23, decided on 2023/24, 2024/25+ reported. No player workload is
    estimated: there are no minutes played in the sources."""
    domestic_codes = [
        c
        for c, comp in COMPETITIONS.items()
        if comp.sport == "football" and comp.category == "domestic"
    ]
    league = _played(
        effective_results(
            store,
            sport="football",
            until=now,
            competitions=domestic_codes,
            identities=domestic_identities,
        )
    )
    cups = _played(
        effective_results(
            store, sport="football", until=now, competitions=CUPS, identities=identities
        )
    )
    league_dates: dict[str, list[date]] = defaultdict(list)
    cup_dates: dict[str, list[date]] = defaultdict(list)
    for r in league:
        for side in ("home", "away"):
            league_dates[r[side]].append(date.fromisoformat(r["match_date"]))
    for r in cups:
        for side in ("home", "away"):
            cup_dates[r[side]].append(date.fromisoformat(r["match_date"]))
    elo = elo_walk(league, k_of=lambda _m: 20.0, home_of=lambda _m: 60.0)

    def describe(team: str, day: date, with_cups: bool) -> tuple[float, float, float] | None:
        dates = [d for d in league_dates[team] if d < day]
        if with_cups:
            dates += [d for d in cup_dates.get(team, []) if d < day]
        if not dates:
            return None
        last = max(dates)
        rest = float(min((day - last).days, advanced.REST_CAP))
        load = float(sum(1 for d in dates if (day - d).days <= advanced.LOAD_DAYS))
        euro7 = float(any(0 < (day - d).days <= 7 for d in cup_dates.get(team, [])))
        return rest, load, euro7

    items = []
    for r in league:
        day = date.fromisoformat(r["match_date"])
        if r["match_date"] < SCHEDULE_WINDOWS["fit"][0]:
            continue
        season = day.year if day.month >= 7 else day.year - 1
        in_cup = [
            any((d.year if d.month >= 7 else d.year - 1) == season for d in cup_dates.get(t, []))
            for t in (r["home"], r["away"])
        ]
        if not any(in_cup):
            continue
        sides = []
        for with_cups in (False, True):
            h = describe(r["home"], day, with_cups)
            a = describe(r["away"], day, with_cups)
            sides.append((h, a))
        if any(x is None for pair in sides for x in pair):
            continue
        items.append((r, sides))
    if len(items) < 500:
        return {
            "run": False,
            "reason": f"za mało meczów ligowych klubów pucharowych ({len(items)})",
        }
    rows_by_id = {r["id"]: r for r, _ in items}
    ids = [r["id"] for r, _ in items]
    y = advanced.truth(rows_by_id, ids)["1x2"]
    window = np.array([_window_of(r, SCHEDULE_WINDOWS, by_season=False) for r, _ in items])
    designs = {
        "L": np.array(
            [[elo[r["id"]], s[0][0][0], s[0][1][0], s[0][0][1], s[0][1][1]] for r, s in items]
        ),
        "X": np.array(
            [[elo[r["id"]], s[1][0][0], s[1][1][0], s[1][0][1], s[1][1][1]] for r, s in items]
        ),
        "X7": np.array(
            [
                [
                    elo[r["id"]],
                    s[1][0][0],
                    s[1][1][0],
                    s[1][0][1],
                    s[1][1][1],
                    s[1][0][2],
                    s[1][1][2],
                ]
                for r, s in items
            ]
        ),
        "elo_only": np.array([[elo[r["id"]]] for r, _ in items]),
    }
    fit = window == "fit"
    probs = {
        k: predict_softmax(fit_softmax(x[fit], y[fit], l2=1e-2), x) for k, x in designs.items()
    }
    out = {
        "run": True,
        "matches": {w: int((window == w).sum()) for w in SCHEDULE_WINDOWS},
        "windows": {},
    }
    for w in ("selection", "holdout"):
        mask = window == w
        if not mask.any():
            continue
        base = advanced.losses("1x2", probs["L"][mask], y[mask])
        rows = {}
        for key, p in probs.items():
            metrics = advanced.metrics("1x2", p[mask], y[mask])
            diff = advanced.losses("1x2", p[mask], y[mask]) - base
            metrics["delta_vs_L"] = round(float(diff.mean()), 5)
            metrics["ci95_vs_L"] = advanced.interval(diff, seed) if key != "L" else None
            rows[key] = metrics
        out["windows"][w] = rows
    selection = out["windows"].get("selection", {})
    gain = selection.get("X", {}).get("ci95_vs_L")
    out["verdict"] = (
        "terminarz z pucharami poprawia prognozy (95% przedział poniżej zera)"
        if gain and gain[1] < 0
        else "brak potwierdzonej poprawy — terminarz z pucharami pozostaje informacją opisową"
    )
    out["labels"] = {
        "elo_only": "tylko różnica Elo ligi",
        "L": "Elo + odpoczynek i mecze w 14 dniach (tylko liga)",
        "X": "Elo + odpoczynek i mecze w 14 dniach (liga + puchary)",
        "X7": "X + mecz europejski w ostatnich 7 dniach",
    }
    return out


# ---------------------------------------------------------------- run, store, gate


def run(store: Store, paths, *, now: datetime) -> dict:
    """Every experiment of TASK 009 on the current data; stored in ``experiments``."""
    from sports_edge.services import club_identities, team_identities

    identities = club_identities(store, paths, now=now)
    base = team_identities(paths)
    report = {
        "ran_at": iso(now),
        "european": cup_experiment(store, now=now, identities=identities, domestic_identities=base),
        "national": national_experiment(store, now=now),
        "schedule": schedule_experiment(
            store, now=now, identities=identities, domestic_identities=base
        ),
        "results_count": store.scalar(
            f"SELECT COUNT(*) FROM historical_results WHERE competition IN "
            f"({','.join('?' for _ in (*CUPS, *NATIONAL_OFFICIAL))})",
            (*CUPS, *NATIONAL_OFFICIAL),
        ),
    }
    report = json.loads(json.dumps(report, default=_plain))
    experiment_id = digest(["international", iso(now), canonical(report)])
    report["experiment_id"] = experiment_id
    with store.transaction():
        store.insert(
            "experiments",
            id=experiment_id,
            hypothesis=f"{HYPOTHESIS} — puchary, reprezentacje, terminarz",
            registered_at=iso(now),
            kind="backtest",
            config_json=canonical(report),
            status="completed",
        )
    return report


def _plain(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value))


def latest(store: Store) -> dict | None:
    row = store.rows(
        "SELECT config_json FROM experiments WHERE hypothesis LIKE ? "
        "ORDER BY registered_at DESC, rowid DESC LIMIT 1",
        (f"{HYPOTHESIS}%",),
    )
    return json.loads(row[0]["config_json"]) if row else None


def market_status(report: dict | None, scope: str) -> dict[str, dict]:
    """{market: {validated, best, verdict}} of one scope from the newest report."""
    if not report or scope not in report:
        return {
            m: {"validated": False, "best": None, "verdict": "eksperyment nie został uruchomiony"}
            for m in MARKETS
        }
    markets = report[scope].get("markets", {})
    out = {}
    for market in MARKETS:
        decision = (markets.get(market) or {}).get("decision") or {}
        out[market] = {
            "validated": bool(decision.get("validated")),
            "best": decision.get("best"),
            "verdict": decision.get("verdict") or "rynek nieoceniany",
        }
    return out


def category_of(competition: str | None) -> str:
    league = COMPETITIONS.get(competition or "")
    return league.category if league else "domestic"


def generator_allowed(store: Store, competition: str | None, market: str) -> tuple[bool, str]:
    """May the automatic coupon generator use this competition and market?

    Domestic leagues keep their own gates. International markets need a validated
    experiment AND the user's decision recorded for the scope — no such decision exists."""
    category = category_of(competition)
    if category == "domestic":
        return True, "liga krajowa — obowiązują bramki modeli ligowych"
    status = market_status(latest(store), category).get(market)
    if not status or not status["validated"]:
        return False, GENERATOR_MESSAGE
    return False, "rynek zwalidowany wstecznie — czeka na decyzję użytkownika (brak aktywacji)"


# ---------------------------------------------------------------- frozen shadow forecasts

SHADOW_KIND = "intl_shadow_forecast"


def current_ratings(
    store: Store, report: dict, *, now: datetime, identities, domestic_identities
) -> dict[str, float]:
    """Ratings of the validated C pool after every result known by ``now``."""
    params = report["european"]["params"]["C_combined"]
    domestic_codes = [
        c
        for c, comp in COMPETITIONS.items()
        if comp.sport == "football" and comp.category == "domestic"
    ]
    league = _played(
        effective_results(
            store,
            sport="football",
            until=now,
            competitions=domestic_codes,
            identities=domestic_identities,
        )
    )
    cups = _played(
        effective_results(
            store, sport="football", until=now, competitions=CUPS, identities=identities
        )
    )
    combined = sorted(
        [{**r, "_cup": False} for r in league] + [{**r, "_cup": True} for r in cups],
        key=lambda r: (r["match_date"], r.get("start_at") or "", r["id"]),
    )
    ratings: dict[str, float] = {}
    elo_walk(
        combined,
        k_of=lambda m: params["k_cup"] if m["_cup"] else params["k_domestic"],
        home_of=lambda m: (
            params["home_advantage_cup"] if m["_cup"] else params["home_advantage_domestic"]
        ),
        ratings=ratings,
    )
    return ratings


def forecast(report: dict, ratings: dict[str, float], home: str, away: str) -> dict | None:
    """Validated markets of one cup match from the C ratings and the D goals model."""
    if home not in ratings or away not in ratings:
        return None
    params = report["european"]["params"]
    c = params["C_combined"]
    d = (ratings[home] - ratings[away] + c["home_advantage_cup"]) / 400
    status = market_status(report, "european")
    p = _normalised(elo_probabilities(np.array([d]), c["draw_margin"]))[0]
    markets: dict[str, dict[str, float]] = {}
    if status["1x2"]["validated"]:
        markets["1x2"] = {"home": p[0], "draw": p[1], "away": p[2]}
    if status["double_chance"]["validated"]:
        markets["double_chance"] = {"1X": 1 - p[2], "X2": 1 - p[0], "12": 1 - p[1]}
    goals = params.get("D_goals") or {}
    if goals.get("models"):
        x = np.array([[d]])
        lam = advanced.predict_poisson(goals["models"]["home"], x, None)
        mu = advanced.predict_poisson(goals["models"]["away"], -x, None)
        scored = advanced.score_markets(lam, mu, np.array([goals["rho"]]))
        for market in ("total_1.5", "total_2.5", "btts"):
            if status[market]["validated"]:
                value = float(scored[market][0])
                markets[market] = {"over": value, "under": 1 - value}
    if not markets:
        return None
    return {
        "rating_difference": round(d * 400, 1),
        "markets": {
            m: {k: round(float(v), 4) for k, v in values.items()} for m, values in markets.items()
        },
    }


def shadow_forecasts(store: Store, paths, *, now: datetime, horizon_hours: int = 72) -> dict:
    """Frozen pre-match forecasts of upcoming STS cup events for the VALIDATED markets only.

    Stored as append-only observations (never in ``predictions``): the coupon generator
    cannot use them; they are the prospective test that precedes any decision."""
    from sports_edge.datasources.competitions import for_sts_league
    from sports_edge.matching import alias_map, match_participant
    from sports_edge.results_engine import known_names
    from sports_edge.services import club_identities, normalizer, team_aliases, team_identities

    report = latest(store)
    if not report or not any(v["validated"] for v in market_status(report, "european").values()):
        return {"stored": 0, "skipped": "brak zwalidowanych rynków pucharowych"}
    headers = [h for code in CUPS for h in COMPETITIONS[code].sts_leagues]
    marks = ",".join("?" for _ in headers)
    events = store.rows(
        f"""SELECT * FROM events WHERE bookmaker_id='sts' AND sport='football'
        AND league IN ({marks}) AND start_at > ? AND start_at <= ?""",
        (*headers, iso(now), iso(now + timedelta(hours=horizon_hours))),
    )
    if not events:
        return {"stored": 0, "skipped": "brak nadchodzących meczów pucharowych w ofercie"}
    identities = club_identities(store, paths, now=now)
    ratings = current_ratings(
        store, report, now=now, identities=identities, domestic_identities=team_identities(paths)
    )
    names, aliases = normalizer(paths), alias_map(store, team_aliases(paths))
    pool = effective_results(
        store, sport="football", until=now, competitions=CUPS, identities=identities
    )
    known = known_names(pool, "football", names)
    stored, unmatched = 0, []
    for event in events:
        if not event["home"] or not event["away"]:
            continue  # not a match of two teams (e.g. a special bet)
        code = for_sts_league(event["league"], "football").code
        sides = [
            match_participant(event[s] or "", "football", known, names, aliases)
            for s in ("home", "away")
        ]
        if any(m.status not in {"exact", "alias"} for m in sides):
            unmatched += [
                event[s]
                for s, m in zip(("home", "away"), sides, strict=True)
                if m.status not in {"exact", "alias"}
            ]
            continue
        found = forecast(report, ratings, sides[0].target, sides[1].target)
        if found is None:
            continue
        body = {
            "event_id": event["id"],
            "competition": code,
            "home": sides[0].target,
            "away": sides[1].target,
            "experiment_id": report.get("experiment_id"),
            "variant": "C_combined + D_goals",
            "made_at": iso(now),
            **found,
        }
        key = digest(
            ["intl_shadow", event["id"], report.get("experiment_id"), canonical(found["markets"])]
        )
        if store.get("stat_observations", key) is not None:
            continue
        with store.transaction():
            store.insert(
                "stat_observations",
                id=key,
                kind=SHADOW_KIND,
                sport="football",
                competition=code,
                season=None,
                entity_type="event",
                entity_key=event["id"],
                event_date=event["start_at"][:10],
                source="international_c_combined",
                source_ref=report.get("experiment_id"),
                payload_json=canonical(body),
                data_version="intl-shadow-v1",
                quality="unverified",
                published_at=None,
                fetched_at=iso(now),
                observed_at=iso(now),
                available_at=iso(now),
                available_basis="observed_at_fetch",
                import_id=None,
            )
        stored += 1
    return {"stored": stored, "events": len(events), "unmatched": sorted(set(unmatched))}


def shadow_of(store: Store, event_id: str, *, before: datetime | None = None) -> dict | None:
    """Newest frozen shadow forecast of one event made before ``before`` (kick-off)."""
    sql = "SELECT payload_json FROM stat_observations WHERE kind=? AND entity_key=?"
    params: list = [SHADOW_KIND, event_id]
    if before is not None:
        sql += " AND observed_at < ?"
        params.append(iso(before))
    rows = store.rows(sql + " ORDER BY observed_at DESC LIMIT 1", tuple(params))
    return json.loads(rows[0]["payload_json"]) if rows else None


def shadow_evaluation(store: Store, *, now: datetime) -> dict:
    """Prospective check of the frozen shadow forecasts against the 90-minute results."""
    from sports_edge.paper import latest_result

    rows = store.rows(
        "SELECT entity_key, payload_json, observed_at FROM stat_observations WHERE kind=? "
        "ORDER BY observed_at",
        (SHADOW_KIND,),
    )
    newest: dict[str, dict] = {}
    for row in rows:
        event = store.get("events", row["entity_key"])
        if event and event["start_at"] and row["observed_at"] < event["start_at"]:
            newest[row["entity_key"]] = json.loads(row["payload_json"])
    settled, losses = 0, []
    for event_id, body in newest.items():
        result = latest_result(store, event_id)
        if not result or result["status"] != "completed" or "1x2" not in body["markets"]:
            continue
        h, a = result["home_score"], result["away_score"]
        outcome = "home" if h > a else "draw" if h == a else "away"
        losses.append(-math.log(max(body["markets"]["1x2"][outcome], 1e-12)))
        settled += 1
    return {
        "forecasts": len(newest),
        "settled": settled,
        "log_loss_1x2": round(float(np.mean(losses)), 4) if losses else None,
        "checked_at": iso(now),
    }


# ---------------------------------------------------------------- analysis of one STS event


def event_view(store: Store, paths, event: dict, *, now: datetime) -> dict | None:
    """What the analysis shows for an STS event of a European cup or a national competition:
    identification, stage and leg, the schedule with every identified competition, the
    model status and — for validated markets only — the frozen shadow forecast."""
    from zoneinfo import ZoneInfo

    from sports_edge import cups as cup_rules
    from sports_edge.datasources.competitions import for_sts_league
    from sports_edge.matching import alias_map, match_participant
    from sports_edge.results_engine import known_names
    from sports_edge.services import club_identities, normalizer, team_aliases, team_identities

    league = for_sts_league(event.get("league") or "", event.get("sport"))
    if league is None or league.category == "domestic":
        return None
    base = team_identities(paths)
    identities = club_identities(store, paths, now=now) if league.category == "european" else base
    pool = effective_results(
        store,
        sport="football",
        until=now,
        competitions=by_category(league.category),
        identities=identities,
    )
    names, aliases = normalizer(paths), alias_map(store, team_aliases(paths))
    known = known_names(pool, "football", names)
    sides = {
        side: match_participant(event.get(side) or "", "football", known, names, aliases)
        for side in ("home", "away")
    }
    start = datetime.fromisoformat(event["start_at"]) if event.get("start_at") else now
    day = start.astimezone(ZoneInfo(league.zone)).date().isoformat()
    category = league.category
    allowed, message = generator_allowed(store, league.code, "1x2")
    out: dict = {
        "competition": league.code,
        "name": league.name,
        "category": category,
        "category_label": SCOPES.get(category, category),
        "kind": league.kind,
        "teams": {
            side: {"sts": event.get(side), "status": m.status, "team": m.target}
            for side, m in sides.items()
        },
        "status": market_status(latest(store), category),
        "generator": {"allowed": allowed, "message": message},
        "shadow": shadow_of(store, event["id"], before=start) if category == "european" else None,
        "results": sum(1 for r in pool if r["competition"] == league.code),
    }
    if any(m.status not in {"exact", "alias"} for m in sides.values()):
        out["note"] = "drużyny niezidentyfikowane w danych rozgrywek — nazwy do potwierdzenia"
        return out
    home, away = sides["home"].target, sides["away"].target
    own = [r for r in pool if r["competition"] == league.code]
    if category == "european":
        notes = cup_rules.annotate(own, league.code)
        out["tie"] = cup_rules.tie_situation(own, notes, home=home, away=away, day=day)
        out["season_format"] = cup_rules.season_format(
            league.code, cup_rules.season_start(None, day)
        )
        domestic_codes = [
            c
            for c, comp in COMPETITIONS.items()
            if comp.sport == "football" and comp.category == "domestic"
        ]
        domestic = effective_results(
            store, sport="football", until=now, competitions=domestic_codes, identities=base
        )
        history = [*domestic, *pool]
    else:
        history = pool
    out["schedule"] = {
        side: _schedule(history, team, day) for side, team in (("home", home), ("away", away))
    }
    out["meetings"] = [
        {
            "date": r["match_date"],
            "competition": COMPETITIONS[r["competition"]].name,
            "match": f"{r['home']} - {r['away']}",
            "score": f"{r['home_score']}:{r['away_score']}",
        }
        for r in sorted(pool, key=lambda r: r["match_date"], reverse=True)
        if {r["home"], r["away"]} == {home, away} and r["match_date"] < day
    ][:5]
    return out


def _schedule(rows: list[dict], team: str, day: str) -> dict:
    """Rest and load from EVERY identified match of the team before the day (no minutes
    played exist in the sources, so player workload is never estimated)."""
    played = sorted(
        (r for r in rows if team in (r["home"], r["away"]) and r["match_date"] < day),
        key=lambda r: (r["match_date"], r.get("start_at") or ""),
    )
    when = date.fromisoformat(day)
    recent = []
    for r in played[-5:][::-1]:
        details = json.loads(r.get("details_json") or "{}")
        recent.append(
            {
                "date": r["match_date"],
                "competition": COMPETITIONS[r["competition"]].name,
                "category": COMPETITIONS[r["competition"]].category,
                "opponent": r["away"] if r["home"] == team else r["home"],
                "venue": "dom" if r["home"] == team else "wyjazd",
                "score_90": f"{r['home_score']}:{r['away_score']}",
                "extra_time": details.get("duration") in {"EXTRA_TIME", "PENALTY_SHOOTOUT"},
            }
        )
    last = date.fromisoformat(played[-1]["match_date"]) if played else None
    days14 = [r for r in played if (when - date.fromisoformat(r["match_date"])).days <= 14]
    return {
        "rest_days": (when - last).days if last else None,
        "matches_14": len(days14),
        "european_14": sum(
            1 for r in days14 if COMPETITIONS[r["competition"]].category == "european"
        ),
        "last_extra_time": bool(recent and recent[0]["extra_time"]),
        "recent": recent,
        "competitions": sorted({COMPETITIONS[r["competition"]].name for r in played[-10:]}),
    }


def gate_candidates(store: Store, candidates: list) -> list:
    """International selections reach the automatic generator only with a validated AND
    user-approved model: otherwise their model probabilities are removed (TASK 009)."""
    from dataclasses import replace

    from sports_edge.datasources.competitions import for_sts_league

    decided: dict[tuple, tuple[bool, str]] = {}
    out = []
    for x in candidates:
        league = for_sts_league(x.league or "", x.sport)
        if league is None or league.category == "domestic" or x.p_model is None:
            out.append(x)
            continue
        key = (league.code, x.market_key)
        if key not in decided:
            decided[key] = generator_allowed(store, league.code, x.market_key)
        allowed, message = decided[key]
        out.append(
            x
            if allowed
            else replace(
                x,
                p_model=None,
                p_low=None,
                p_high=None,
                model=None,
                model_version_id=None,
                warnings=(*x.warnings, message),
            )
        )
    return out
