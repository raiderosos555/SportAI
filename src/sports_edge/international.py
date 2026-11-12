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
import re
import unicodedata
from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from itertools import groupby

import numpy as np

from sports_edge import advanced
from sports_edge.ablation import fit_softmax, predict_softmax
from sports_edge.cups import season_start
from sports_edge.datasources.competitions import COMPETITIONS, by_category
from sports_edge.ingestion.types import canonical, digest, instant, iso
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
        (
            r
            for r in rows
            if r.get("regulation_certain", True)
            and not r.get("conflict")
            and r["status"] == "finished"
        ),
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
                if window == "calibration":
                    metrics["reliability"] = advanced.reliability(market, p, y)
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


def _national_sts_benchmark(
    store: Store,
    ids: list[str],
    probabilities: np.ndarray,
    rows_by_id: dict[str, dict],
) -> dict | None:
    """Compare the selected national variant with same-event, pre-match STS odds only."""
    if not ids:
        return None
    by_id = dict(zip(ids, probabilities, strict=True))
    quotes: dict[str, dict[str, float]] = defaultdict(dict)
    for offset in range(0, len(ids), 500):
        chunk = ids[offset : offset + 500]
        marks = ",".join("?" for _ in chunk)
        for quote in store.rows(
            f"""SELECT result_id, selection, price, observed_at FROM benchmark_odds
            WHERE operator='sts' AND moment='pre_match' AND market_key='1x2'
                AND result_id IN ({marks})""",
            tuple(chunk),
        ):
            row = rows_by_id.get(quote["result_id"])
            start = row.get("start_at") if row else None
            observed = quote.get("observed_at")
            if not row or not start or not observed:
                continue
            try:
                if datetime.fromisoformat(observed) > datetime.fromisoformat(start):
                    continue
                price = float(quote["price"])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(price) or price <= 1.0:
                continue
            quotes[quote["result_id"]][quote["selection"]] = price
    market_probabilities, model_probabilities, outcomes = [], [], []
    for result_id, odds in sorted(quotes.items()):
        if set(odds) != {"home", "draw", "away"} or result_id not in by_id:
            continue
        inverse = {key: 1.0 / odds[key] for key in ("home", "draw", "away")}
        overround = sum(inverse.values())
        if not math.isfinite(overround) or overround <= 0:
            continue
        row = rows_by_id[result_id]
        actual = (
            0
            if row["home_score"] > row["away_score"]
            else 1
            if row["home_score"] == row["away_score"]
            else 2
        )
        market_probabilities.append([inverse[key] / overround for key in ("home", "draw", "away")])
        model_probabilities.append(by_id[result_id])
        outcomes.append(actual)
    if not outcomes:
        return None
    model = np.asarray(model_probabilities, dtype=float)
    market = np.asarray(market_probabilities, dtype=float)
    truth = np.asarray(outcomes, dtype=int)
    return {
        "same_events": True,
        "n_events": len(outcomes),
        "moment": "pre_match",
        "operator": "sts",
        "model_log_loss": round(float(advanced.losses("1x2", model, truth).mean()), 5),
        "market_log_loss": round(float(advanced.losses("1x2", market, truth).mean()), 5),
        "note": "Wyłącznie kurs STS 1X2 zapisany przed startem tych samych meczów.",
    }


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
        r["_venue_country"] = venue
        r["_venue_unknown"] = not venue
        home_country = details.get("home_country") or r["home"]
        r["_neutral"] = bool(venue) and _country_key(venue) != _country_key(home_country)
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
    params, diffs, friendly_outputs = {}, {}, {}
    friendly_ids = [r["id"] for r in played if r["_friendly"]]
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
                    home_of=lambda m, h=home, z=neutral: (
                        0.0 if m["_venue_unknown"] or (z and m["_neutral"]) else h
                    ),
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
        if key == "N1":
            friendly_d = []
            for target in (r for r in played if r["_friendly"]):
                ratings: dict[str, float] = {}
                prior = [r for r in official if r["match_date"] < target["match_date"]]
                elo_walk(
                    prior,
                    k_of=lambda row, value=k: value,
                    home_of=lambda row, value=home: 0.0 if row["_venue_unknown"] else value,
                    ratings=ratings,
                )
                venue_advantage = 0.0 if target["_venue_unknown"] else home
                friendly_d.append(
                    (
                        ratings.get(target["home"], 1500.0)
                        - ratings.get(target["away"], 1500.0)
                        + venue_advantage
                    )
                    / 400
                )
        else:
            friendly_d = [d[i] for i in friendly_ids]
        friendly_outputs[key] = _normalised(
            elo_probabilities(np.asarray(friendly_d, dtype=float), margin)
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
    if friendly_ids:
        friendly_baseline = np.tile(climate["1x2"], (len(friendly_ids), 1))
        friendly_truth = advanced.truth({r["id"]: r for r in played}, friendly_ids)["1x2"]
        friendly_windows = _index(
            friendly_ids,
            {
                r["id"]: _window_of(r, NATION_WINDOWS, by_season=False)
                for r in played
                if r["_friendly"]
            },
        )
        friendly_evaluation = {
            window: {
                name: advanced.metrics("1x2", probability[idx], friendly_truth[idx])
                for name, probability in {"baseline": friendly_baseline, **friendly_outputs}.items()
            }
            for window, idx in friendly_windows.items()
            if idx and window in {"selection", "holdout"}
        }
    else:
        friendly_evaluation = {}
    national_markets = _evaluate(outputs, ids, rows_by_id, windows, seed=seed)
    one_x_two = national_markets.get("1x2")
    selected_indices = windows.get("selection") or []
    selected_variant = (one_x_two or {}).get("decision", {}).get("best")
    if selected_variant and selected_indices:
        selected_ids = [ids[index] for index in selected_indices]
        one_x_two["market_benchmark"] = _national_sts_benchmark(
            store,
            selected_ids,
            outputs[selected_variant]["1x2"][selected_indices],
            rows_by_id,
        )
    return {
        "scope": "national",
        "competitions": list(NATIONAL_OFFICIAL),
        "friendlies_used": sum(1 for r in played if r["_friendly"]),
        "matches": {w: len(v) for w, v in windows.items()} | {"fit": len(fit_ids)},
        "venue_coverage": {
            "known": sum(not r["_venue_unknown"] for r in official),
            "unknown": sum(r["_venue_unknown"] for r in official),
            "neutral": sum(r["_neutral"] for r in official if not r["_venue_unknown"]),
        },
        "excluded_uncertain_90": sum(1 for r in rows if not r.get("regulation_certain", True)),
        "excluded_conflicts": sum(1 for r in rows if r.get("conflict")),
        "params": params,
        "markets": national_markets,
        "friendly_evaluation": friendly_evaluation,
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


def generator_allowed(
    store: Store,
    competition: str | None,
    market: str,
    *,
    model_version_id: str | None = None,
) -> tuple[bool, str]:
    """May the automatic coupon generator use this competition and market?

    Domestic leagues keep their own gates. National 1X2 additionally requires approval of
    the exact currently validated version. European markets remain locked."""
    category = category_of(competition)
    if category == "domestic":
        return True, "liga krajowa — obowiązują bramki modeli ligowych"
    if category == "national":
        # TASK 018: national teams follow the league path — the per-market gate
        # (predictive_status) and the profile rules decide, with no extra registry gate
        return True, "reprezentacje — obowiązują bramki modeli jak w ligach"
    status = market_status(latest(store), category).get(market)
    if not status or not status["validated"]:
        if category == "national":
            return False, (status or {}).get("verdict") or GENERATOR_MESSAGE
        return False, GENERATOR_MESSAGE
    if category == "european":
        return False, "rynek zwalidowany wstecznie — czeka na decyzję użytkownika (brak aktywacji)"
    if market != "1x2":
        return False, "rynek międzynarodowy nie ma zatwierdzonej ścieżki do generatora"
    active = active_national_version_id(store)
    if active is None:
        return False, "1X2 reprezentacji zwalidowane wstecznie — czeka na decyzję użytkownika"
    if model_version_id != active:
        return False, "prognoza pochodzi z innej wersji niż aktualnie zatwierdzona"
    return True, "1X2 reprezentacji — walidacja i decyzja użytkownika zgodne"


# ---------------------------------------------------------------- frozen shadow forecasts

SHADOW_KIND = "intl_shadow_forecast"
NATIONAL_SHADOW_KIND = "national_shadow_forecast"


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
        if (
            event
            and event["start_at"]
            and datetime.fromisoformat(row["observed_at"])
            < datetime.fromisoformat(event["start_at"])
        ):
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


def _national_history(store: Store, *, now: datetime) -> list[dict]:
    rows = effective_results(
        store,
        sport="football",
        until=now,
        competitions=(*NATIONAL_OFFICIAL, FRIENDLY),
    )
    out = _played(rows)
    for row in out:
        details = json.loads(row.get("details_json") or "{}")
        venue = details.get("venue_country")
        row["_venue_country"] = venue
        row["_venue_unknown"] = not venue
        home_country = details.get("home_country") or row["home"]
        row["_neutral"] = bool(venue) and _country_key(venue) != _country_key(home_country)
        row["_friendly"] = row["competition"] == FRIENDLY
    return out


def _country_key(value: str) -> str:
    plain = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", plain)


def _national_mapper(store: Store, paths, history: list[dict]):
    """Exact/approved team mapping that refuses keys or aliases with multiple targets."""
    from sports_edge.matching import (
        ParticipantMatch,
        alias_map,
        alias_targets,
        match_participant,
        participant_key,
    )
    from sports_edge.results_engine import known_names
    from sports_edge.services import normalizer, team_aliases

    names = normalizer(paths)
    aliases = alias_map(store, team_aliases(paths))
    known = known_names(history, "football", names)
    targets_by_key: dict[str, set[str]] = defaultdict(set)
    for row in history:
        for side in ("home", "away"):
            target = row.get(side)
            for spelling in (target, row.get(f"{side}_source")):
                if spelling and target:
                    targets_by_key[participant_key(spelling, "football", names)].add(target)

    def map_participant(raw: str):
        key = participant_key(raw, "football", names)
        if len(targets_by_key.get(key, ())) > 1:
            return ParticipantMatch(raw, "ambiguous", None, 1.0)
        if key in known:
            return ParticipantMatch(raw, "exact", known[key], 1.0)
        targets = {
            known[target] for target in alias_targets(aliases, ("football", key)) if target in known
        }
        if len(targets) > 1:
            return ParticipantMatch(raw, "ambiguous", None, 1.0)
        if len(targets) == 1:
            return ParticipantMatch(raw, "alias", next(iter(targets)), 1.0)
        return match_participant(raw, "football", known, names, aliases)

    return map_participant


def active_national_version_id(store: Store) -> str | None:
    """The exact currently validated national 1X2 version approved by the user, if any."""
    from sports_edge import decisions

    report = latest(store)
    status = market_status(report, "national")["1x2"]
    choice = decisions.last(store, decisions.NATIONAL_1X2)
    accepted = choice.get("new") or {} if choice else {}
    if not status["validated"] or not accepted.get("accepted"):
        return None
    if (
        accepted.get("competition") != "national"
        or accepted.get("market") != "1x2"
        or accepted.get("period") != "FT_90"
        or accepted.get("experiment_id") != (report or {}).get("experiment_id")
        or accepted.get("variant") != status.get("best")
    ):
        return None
    version_id = accepted.get("model_version_id")
    version = store.get("model_versions", version_id) if version_id else None
    if version is None or version["status"] != "validated":
        return None
    validation = json.loads(version["validation_json"])
    if (
        validation.get("scope") != "national"
        or validation.get("market") != "1x2"
        or validation.get("experiment_id") != accepted.get("experiment_id")
        or validation.get("variant") != accepted.get("variant")
    ):
        return None
    return version_id


def national_registry_status(store: Store, paths) -> dict:
    """Apply the unmodified project model registry policy to national 1X2 evidence."""
    from sports_edge.models.registry import validation_status
    from sports_edge.services import policy

    report = latest(store)
    status = market_status(report, "national")["1x2"]
    if not report or not status["validated"] or not status.get("best"):
        return {
            "status": "experimental",
            "reasons": ["1X2 reprezentacji nie spełnia kryteriów walidacji walk-forward"],
            "validation": None,
        }
    experiment_id, variant = report["experiment_id"], status["best"]
    market = report["national"]["markets"]["1x2"]
    sample = market["windows"]["selection"][variant]
    validation = {
        "protocol": "v2",
        "scope": "national",
        "market": "1x2",
        "period": "FT_90",
        "experiment_id": experiment_id,
        "variant": variant,
        "selection": {
            "n": sample["n"],
            "ece": sample["ece"],
            "log_loss": sample["log_loss"],
            "delta_log_loss": sample["delta_log_loss"],
            "ci95": sample["ci95"],
        },
        "decision": {"n": sample["n"], "ece": sample["ece"]},
        "market_benchmark": market.get("market_benchmark"),
        "leakage_checked": True,
    }
    state, reasons = validation_status(validation, policy(paths))
    return {"status": state, "reasons": reasons, "validation": validation}


def register_national_model_version(store: Store, paths, *, now: datetime) -> dict:
    """Register, but never activate, the currently validated national 1X2 experiment."""
    from sports_edge.models.registry import register_version
    from sports_edge.provenance import data_snapshot
    from sports_edge.services import policy

    report = latest(store)
    status = market_status(report, "national")["1x2"]
    if not report or not status["validated"] or not status.get("best"):
        raise ValueError("1X2 reprezentacji nie spełnia kryteriów walidacji")
    registry = national_registry_status(store, paths)
    if registry["status"] != "validated":
        raise ValueError(
            "Wersja nie spełnia istniejącej polityki rejestru modeli: "
            + "; ".join(registry["reasons"])
        )
    experiment_id, variant = report["experiment_id"], status["best"]
    version_name = f"national-1x2:{experiment_id}:{variant}"
    existing = store.rows(
        """SELECT v.*, m.family FROM model_versions v JOIN models m ON m.id=v.model_id
        WHERE m.family='national_1x2' AND v.version=?""",
        (version_name,),
    )
    if existing:
        row = existing[0]
        if row["status"] != "validated":
            raise ValueError("Zarejestrowana wersja reprezentacji nie ma statusu validated")
        return {"model_version_id": row["id"], "status": row["status"], "reasons": []}

    validation = registry["validation"]
    rows = _national_history(store, now=now)
    version_id, state, reasons = register_version(
        store,
        family="national_1x2",
        sport="football",
        description="Reprezentacje — wariant wybrany w oknie selekcji, 1X2 po 90 minutach",
        version=version_name,
        created_at=now,
        trained_through=max(
            (
                datetime.combine(date.fromisoformat(r["match_date"][:10]), time.max, UTC)
                for r in rows
            ),
            default=None,
        ),
        training_data={
            "scope": "national",
            "competitions": [*NATIONAL_OFFICIAL, FRIENDLY],
            "snapshot": data_snapshot(r["id"] for r in rows),
            "experiment_id": experiment_id,
            "variant": variant,
        },
        validation=validation,
        policy=policy(paths),
    )
    if state != "validated":
        raise ValueError(
            "Wersja reprezentacji nie przeszła walidacji rejestru: " + "; ".join(reasons)
        )
    return {"model_version_id": version_id, "status": state, "reasons": reasons}


def _national_interval(probabilities: dict[str, float], report: dict, variant: str) -> dict | None:
    """Calibration-window interval using the project's reliability-bin heuristic."""
    block = report["national"]["markets"]["1x2"]
    calibration = (block.get("windows") or {}).get("calibration") or {}
    row = calibration.get(variant) or {}
    reliability = row.get("reliability") or []
    ece = row.get("ece")
    if ece is None or not reliability:
        return None
    intervals = {}
    for key, probability in probabilities.items():
        matched = None
        for item in reliability:
            bounds = str(item.get("range", "")).rstrip("%").split("–")
            if len(bounds) != 2:
                continue
            low, high = (float(x) / 100 for x in bounds)
            if low <= probability < high or (high == 1.0 and probability == 1.0):
                matched = item
                break
        n = int((matched or {}).get("n") or 0)
        if n <= 0:
            return None
        half = max(float(ece), 1.96 * math.sqrt(probability * (1 - probability) / n))
        intervals[key] = [max(0.0, probability - half), min(1.0, probability + half)]
    return intervals


def national_serving_predictions(
    store: Store, paths, *, now: datetime, horizon_hours: int = 72
) -> dict:
    """Create immutable STS national predictions only for the user-approved model version."""
    from sports_edge.datasources.competitions import for_sts_league
    from sports_edge.international_data import fixtures
    from sports_edge.models.registry import save_prediction
    from sports_edge.provenance import code_state, data_snapshot

    version_id = active_national_version_id(store)
    if version_id is None:
        return {"stored": 0, "kept": 0, "skipped": {"model_not_approved": 1}, "unmatched": []}
    decision = store.rows(
        "SELECT new_json FROM user_decisions WHERE decision=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT 1",
        ("national_1x2",),
    )
    approved = json.loads(decision[0]["new_json"]) if decision else {}
    report = latest(store)
    history = _national_history(store, now=now)
    rows_ids = [r["id"] for r in history]
    map_participant = _national_mapper(store, paths, history)
    codes = (*NATIONAL_OFFICIAL, FRIENDLY)
    headers = [h for code in codes for h in COMPETITIONS[code].sts_leagues]
    all_events = store.rows(
        f"""SELECT * FROM events WHERE bookmaker_id='sts' AND sport='football'
        AND league IN ({",".join("?" for _ in headers) or "''"}) AND start_at IS NOT NULL
        ORDER BY start_at,id""",
        tuple(headers),
    )
    horizon = now + timedelta(hours=horizon_hours)
    events = [
        event for event in all_events if now < datetime.fromisoformat(event["start_at"]) <= horizon
    ]
    fixtures_by_code = {code: fixtures(store, code, until=now) for code in codes}
    stored, kept, unmatched = 0, 0, []
    skipped: dict[str, int] = defaultdict(int)
    for event in events:
        league = for_sts_league(event.get("league") or "", event.get("sport"))
        if league is None or league.code not in codes:
            skipped["unmapped_competition"] += 1
            continue
        if not event.get("start_at") or datetime.fromisoformat(event["start_at"]) <= now:
            skipped["not_before_start"] += 1
            continue
        if not event.get("home") or not event.get("away"):
            skipped["incomplete_participants"] += 1
            continue
        sides = [map_participant(event[side]) for side in ("home", "away")]
        if any(side.status not in {"exact", "alias"} or not side.target for side in sides):
            reason = (
                "ambiguous_participants"
                if any(side.status == "ambiguous" for side in sides)
                else "unmatched_participants"
            )
            skipped[reason] += 1
            unmatched.append(
                {
                    "event_id": event["id"],
                    "reason": "uczestnik wymaga mapowania",
                    "mapping_status": reason,
                    "statuses": [s.status for s in sides],
                }
            )
            continue
        home, away = sides[0].target, sides[1].target
        start_day = event.get("offer_date") or str(event["start_at"])[:10]
        venue_matches = [
            f
            for f in fixtures_by_code.get(league.code, [])
            if f.get("date") == start_day and f.get("home") == home and f.get("away") == away
        ]
        if len(venue_matches) > 1:
            skipped["ambiguous_fixture"] += 1
            continue
        venue = venue_matches[0].get("venue_country") if venue_matches else None
        variants = _national_ratings(report, history, home, away, venue)
        variant = approved.get("variant")
        if variant not in variants:
            skipped["variant_unavailable"] += 1
            continue
        _difference, values = variants[variant]
        probabilities = {
            "home": float(values[0]),
            "draw": float(values[1]),
            "away": float(values[2]),
        }
        interval = _national_interval(probabilities, report, variant)
        if interval is None:
            skipped["calibration_interval_unavailable"] += 1
            continue
        existing = store.rows(
            """SELECT id FROM predictions WHERE model_version_id=? AND event_id=?
            AND market_key='1x2' AND period='FT' AND kind='prospective' LIMIT 1""",
            (version_id, event["id"]),
        )
        if existing:
            kept += 1
            continue
        save_prediction(
            store,
            model_version_id=version_id,
            event_id=event["id"],
            market_key="1x2",
            period="FT",
            line=None,
            probabilities=probabilities,
            uncertainty={
                "method": "national calibration reliability-bin heuristic",
                "interval": interval,
                "family": "1x2",
                "predictive_status": "validated_predictive",
                "calibration_source": "okno kalibracji z raportu eksperymentu",
                "status_note": "1X2 po 90 minutach; decyzja dotyczy wyłącznie tej wersji i rynku",
            },
            inputs={
                "info_cutoff": iso(now),
                "data_snapshot": data_snapshot(rows_ids),
                "code_state": code_state(),
                "teams": [home, away],
                "competition": league.code,
                "category": "national",
                "period_scope": "FT_90",
                "experiment_id": report["experiment_id"],
                "variant": variant,
                "venue_country": venue,
                "venue_status": "confirmed" if venue else "unknown; no home advantage",
                "quality": "validated_version_user_approved",
            },
            kind="prospective",
            created_at=now,
        )
        stored += 1
    return {
        "stored": stored,
        "kept": kept,
        "skipped": dict(sorted(skipped.items())),
        "unmatched": unmatched,
        "events": len(events),
    }


def _national_ratings(
    report: dict, history: list[dict], home: str, away: str, venue_country: str | None
) -> dict[str, tuple[float, np.ndarray]]:
    """Current ratings and 1X2 probabilities for the predeclared national variants.

    Unknown venue information never receives home advantage. A confirmed home-country venue
    may use it; N3 also removes it for a confirmed neutral venue.
    """
    official = [row for row in history if not row["_friendly"]]
    played = history
    out: dict[str, tuple[float, np.ndarray]] = {}
    for key, pool, friendly_weight, neutral_adjustment in (
        ("N1", official, 0.0, False),
        ("N2", played, 0.5, False),
        ("N3", played, 0.5, True),
    ):
        params = report["national"]["params"].get(key)
        if not params:
            continue
        ratings: dict[str, float] = {}
        elo_walk(
            pool,
            k_of=lambda row, p=params, weight=friendly_weight: (
                p["k"] * (weight if row["_friendly"] else 1.0)
            ),
            home_of=lambda row, p=params, neutral=neutral_adjustment: (
                0.0
                if row["_venue_unknown"] or (neutral and row["_neutral"])
                else p["home_advantage"]
            ),
            ratings=ratings,
        )
        home_rating, away_rating = ratings.get(home, 1500.0), ratings.get(away, 1500.0)
        venue_known = venue_country is not None
        neutral = venue_known and _country_key(venue_country) != _country_key(home)
        home_advantage = (
            0.0 if not venue_known or (neutral_adjustment and neutral) else params["home_advantage"]
        )
        difference = (home_rating - away_rating + home_advantage) / 400
        probability = _normalised(elo_probabilities(np.array([difference]), params["draw_margin"]))[
            0
        ]
        out[key] = (difference, probability)

    goals = report["national"]["params"].get("N_goals") or {}
    rating = goals.get("rating")
    if rating in out and goals.get("models"):
        difference = out[rating][0]
        x = np.array([[difference]])
        lam = advanced.predict_poisson(goals["models"]["home"], x, None)
        mu = advanced.predict_poisson(goals["models"]["away"], -x, None)
        values = advanced.score_markets(lam, mu, np.array([goals["rho"]]))["1x2"][0]
        out["N_goals"] = (difference, values)
    return out


def national_shadow_forecasts(
    store: Store, paths, *, now: datetime, horizon_hours: int = 72
) -> dict:
    """Freeze research-only 1X2 forecasts for mapped national STS events.

    The observations are never stored in ``predictions`` and therefore cannot be served to
    the coupon generator. Forecasts are produced for all predeclared variants so prospective
    evaluation does not select one using its own outcomes.
    """
    from sports_edge.datasources.competitions import for_sts_league
    from sports_edge.international_data import fixtures
    from sports_edge.provenance import code_state, data_snapshot

    report = latest(store)
    if not report or not report.get("national"):
        return {
            "stored": 0,
            "events": 0,
            "skipped": {"no_experiment": 1},
            "unmatched": 0,
            "invalid": 0,
        }
    codes = (*NATIONAL_OFFICIAL, FRIENDLY)
    headers = [header for code in codes for header in COMPETITIONS[code].sts_leagues]
    marks = ",".join("?" for _ in headers) or "''"
    all_events = store.rows(
        f"""SELECT * FROM events WHERE bookmaker_id='sts' AND sport='football'
        AND (league IN ({marks}) OR (
            UPPER(league) LIKE '%MIĘDZYNARODOWE%'
            OR UPPER(league) LIKE '%REPREZENTAC%'
            OR UPPER(league) LIKE '%NARODÓW%'
            OR UPPER(league) LIKE '%NARODOW%'
            OR UPPER(league) LIKE '%TOWARZYSKIE%'
            OR UPPER(league) LIKE '%ELIMINACJE%'
            OR UPPER(league) LIKE '%MISTRZOSTWA%'
            OR UPPER(league) LIKE '%MŁODZIEŻOWE%'
        ) AND UPPER(league) NOT LIKE '%KLUB%') ORDER BY start_at,id""",
        tuple(headers),
    )
    horizon = now + timedelta(hours=horizon_hours)
    events = []
    skipped: dict[str, int] = defaultdict(int)
    for event in all_events:
        if not event.get("start_at"):
            skipped["missing_start_time"] += 1
            continue
        try:
            start = datetime.fromisoformat(event["start_at"])
        except ValueError:
            skipped["invalid_start_time"] += 1
            continue
        if start <= now:
            skipped["not_before_start"] += 1
        elif start > horizon:
            skipped["outside_horizon"] += 1
        else:
            events.append(event)
    if not events:
        return {
            "stored": 0,
            "events": 0,
            "skipped": dict(sorted(skipped.items())) or {"no_mapped_sts_events_in_horizon": 1},
            "unmatched": 0,
            "invalid": 0,
        }
    history = _national_history(store, now=now)
    current_code_state = code_state()
    map_participant = _national_mapper(store, paths, history)
    fixtures_by_code = {code: fixtures(store, code, until=now) for code in codes}
    stored, kept, unmatched, invalid = 0, 0, 0, 0
    for event in events:
        league = for_sts_league(event["league"], "football")
        if league is None or league.code not in codes:
            skipped["unmapped_competition"] += 1
            continue
        if not event["home"] or not event["away"]:
            invalid += 1
            skipped["incomplete_participants"] += 1
            continue
        sides = [map_participant(event[side]) for side in ("home", "away")]
        if any(side.status not in {"exact", "alias"} for side in sides):
            unmatched += 1
            reason = (
                "ambiguous_participants"
                if any(side.status == "ambiguous" for side in sides)
                else "unmatched_participants"
            )
            skipped[reason] += 1
            continue
        home, away = sides[0].target, sides[1].target
        start_day = event.get("offer_date") or str(event["start_at"])[:10]
        venue_matches = []
        for fixture in fixtures_by_code.get(league.code, []):
            if fixture.get("date") != start_day:
                continue
            if fixture.get("home") == home and fixture.get("away") == away:
                venue_matches.append(fixture)
        if len(venue_matches) > 1:
            invalid += 1
            skipped["ambiguous_fixture"] += 1
            continue
        venue_country = venue_matches[0].get("venue_country") if venue_matches else None
        variants = _national_ratings(report, history, home, away, venue_country)
        if not variants:
            skipped["no_forecast_variants"] += 1
            continue
        for variant, (difference, probs) in variants.items():
            probabilities = {
                "home": float(probs[0]),
                "draw": float(probs[1]),
                "away": float(probs[2]),
            }
            body = {
                "event_id": event["id"],
                "competition": league.code,
                "home": home,
                "away": away,
                "variant": variant,
                "experiment_id": report.get("experiment_id"),
                "model_version": (
                    f"national-shadow:{report.get('experiment_id')}:{variant}:{current_code_state}"
                ),
                "code_state": current_code_state,
                "made_at": iso(now),
                "info_cutoff": iso(now),
                "rating_difference": round(float(difference * 400), 1),
                "probabilities": probabilities,
                "venue_country": venue_country,
                "venue_status": "confirmed" if venue_country else "unknown; no home advantage",
                "market": "1x2",
                "period": "FT_90",
                "quality": "research_only",
            }
            key = digest(
                [
                    NATIONAL_SHADOW_KIND,
                    event["id"],
                    report.get("experiment_id"),
                    variant,
                    current_code_state,
                ]
            )
            if store.get("stat_observations", key) is not None:
                kept += 1
                continue
            previous = []
            for row in store.rows(
                "SELECT id,payload_json FROM stat_observations WHERE kind=? AND entity_key=? "
                "ORDER BY observed_at,id",
                (NATIONAL_SHADOW_KIND, event["id"]),
            ):
                old = json.loads(row["payload_json"])
                if old.get("variant") == variant:
                    previous.append(row["id"])
            body["supersedes"] = previous
            body["version_reason"] = (
                "nowy eksperyment lub stan kodu; wcześniejsze prognozy pozostają niezmienione"
                if previous
                else "pierwsza wersja prognozy cieniowej dla wydarzenia i wariantu"
            )
            snapshot = data_snapshot(row["id"] for row in history)
            with store.transaction():
                store.insert(
                    "stat_observations",
                    id=key,
                    kind=NATIONAL_SHADOW_KIND,
                    sport="football",
                    competition=league.code,
                    season=None,
                    entity_type="event",
                    entity_key=event["id"],
                    event_date=start_day,
                    source="national_walk_forward_shadow",
                    source_ref=report.get("experiment_id"),
                    payload_json=canonical({**body, "data_snapshot": snapshot}),
                    data_version="national-shadow-v1",
                    quality="unverified",
                    published_at=None,
                    fetched_at=iso(now),
                    observed_at=iso(now),
                    available_at=iso(now),
                    available_basis="observed_at_fetch",
                    import_id=None,
                )
            stored += 1
    return {
        "stored": stored,
        "kept": kept,
        "events": len(events),
        "skipped": dict(sorted(skipped.items())),
        "unmatched": unmatched,
        "invalid": invalid,
        "variants": sorted(VARIANT_LABELS),
        "experiment_id": report.get("experiment_id"),
    }


def national_shadow_evaluation(store: Store, *, now: datetime) -> dict:
    """Score frozen national 1X2 forecasts only against available certain 90-minute results."""
    from sports_edge.results_engine import effective_results

    observations = store.rows(
        "SELECT * FROM stat_observations WHERE kind=? AND available_at<=? ORDER BY observed_at,id",
        (NATIONAL_SHADOW_KIND, iso(now)),
    )
    results = effective_results(
        store,
        sport="football",
        until=now,
        competitions=(*NATIONAL_OFFICIAL, FRIENDLY),
    )
    by_pair: dict[str, list[dict]] = defaultdict(list)
    for row in results:
        if row.get("status") == "finished":
            pair = "|".join([row["competition"], row["home"], row["away"]])
            by_pair[pair].append(row)
    totals: dict[str, list] = defaultdict(list)
    by_competition: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    counts = {"forecasts": len(observations), "settled": 0, "unmatched": 0, "invalid_result": 0}
    reasons: dict[str, int] = defaultdict(int)
    for observation in observations:
        body = json.loads(observation["payload_json"])
        event = store.get("events", observation["entity_key"])
        if (
            not event
            or not event.get("start_at")
            or datetime.fromisoformat(observation["observed_at"])
            >= datetime.fromisoformat(event["start_at"])
        ):
            counts["invalid_result"] += 1
            reasons["forecast_not_pre_match"] += 1
            continue
        event_day = event.get("offer_date") or event["start_at"][:10]
        pair = "|".join([body["competition"], body["home"], body["away"]])
        nearby = [row for row in by_pair.get(pair, []) if row["match_date"] == event_day]
        matches = [
            row
            for row in nearby
            if row.get("regulation_certain", True)
            and not row.get("conflict")
            and row.get("status") == "finished"
            and row.get("available_at")
            and datetime.fromisoformat(row["available_at"]) <= now
        ]
        if not matches and nearby:
            counts["invalid_result"] += 1
            reasons["uncertain_or_conflicting_result"] += 1
            continue
        if not matches:
            counts["unmatched"] += 1
            reasons["no_same_event_90_minute_result"] += 1
            continue
        if len(matches) != 1:
            counts["invalid_result"] += 1
            reasons["ambiguous_same_day_result"] += 1
            continue
        result = matches[0]
        actual = (
            0
            if result["home_score"] > result["away_score"]
            else 1
            if result["home_score"] == result["away_score"]
            else 2
        )
        prediction = body["probabilities"]
        probability = [prediction["home"], prediction["draw"], prediction["away"]]
        if (
            not np.isfinite(probability).all()
            or min(probability) < 0
            or abs(sum(probability) - 1) > 1e-6
        ):
            counts["invalid_result"] += 1
            reasons["invalid_probabilities"] += 1
            continue
        variant = body["variant"]
        totals[variant].append((probability, actual))
        by_competition[body["competition"]][variant].append((probability, actual))
        counts["settled"] += 1
    metrics = {}
    for variant, entries in sorted(totals.items()):
        p = np.asarray([item[0] for item in entries], dtype=float)
        y = np.asarray([item[1] for item in entries], dtype=int)
        metrics[variant] = advanced.metrics("1x2", p, y)
    per_comp_metrics = {}
    for competition, variants in sorted(by_competition.items()):
        per_comp_metrics[competition] = {}
        for variant, entries in sorted(variants.items()):
            p = np.asarray([item[0] for item in entries], dtype=float)
            y = np.asarray([item[1] for item in entries], dtype=int)
            per_comp_metrics[competition][variant] = advanced.metrics("1x2", p, y)
    return {
        **counts,
        "reasons": dict(sorted(reasons.items())),
        "by_variant": metrics,
        "by_competition": per_comp_metrics,
        "checked_at": iso(now),
    }


def national_shadow_of(
    store: Store, event_id: str, *, before: datetime | None = None
) -> dict | None:
    report = latest(store)
    best = (market_status(report, "national").get("1x2") or {}).get("best")
    if not best:
        return None
    sql = """SELECT payload_json, observed_at FROM stat_observations WHERE kind=? AND entity_key=?
        AND source_ref=?"""
    rows = store.rows(sql, (NATIONAL_SHADOW_KIND, event_id, report.get("experiment_id")))
    cutoff = instant(before) if before is not None else None
    eligible = []
    for row in rows:
        try:
            observed = instant(row["observed_at"])
        except (TypeError, ValueError):
            continue
        if cutoff is None or observed < cutoff:
            body = json.loads(row["payload_json"])
            if body.get("variant") == best:
                eligible.append((observed, body))
    if not eligible:
        return None
    _observed, body = max(eligible, key=lambda item: item[0])
    return body


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
    if league.category == "national":
        map_participant = _national_mapper(store, paths, pool)
        sides = {side: map_participant(event.get(side) or "") for side in ("home", "away")}
    else:
        names, aliases = normalizer(paths), alias_map(store, team_aliases(paths))
        known = known_names(pool, "football", names)
        sides = {
            side: match_participant(event.get(side) or "", "football", known, names, aliases)
            for side in ("home", "away")
        }
    start = datetime.fromisoformat(event["start_at"]) if event.get("start_at") else now
    day = start.astimezone(ZoneInfo(league.zone)).date().isoformat()
    category = league.category
    allowed, message = generator_allowed(
        store,
        league.code,
        "1x2",
        model_version_id=active_national_version_id(store) if category == "national" else None,
    )
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
        "shadow": (
            shadow_of(store, event["id"], before=start)
            if category == "european"
            else national_shadow_of(store, event["id"], before=start)
            if category == "national"
            else None
        ),
        "results": sum(1 for r in pool if r["competition"] == league.code),
    }
    if any(m.status not in {"exact", "alias"} for m in sides.values()):
        out["note"] = (
            "niejednoznaczne mapowanie drużyny — wymagane ręczne potwierdzenie"
            if any(m.status == "ambiguous" for m in sides.values())
            else "drużyny niezidentyfikowane w danych rozgrywek — nazwy do potwierdzenia"
        )
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
            decided[key] = generator_allowed(
                store,
                league.code,
                x.market_key,
                model_version_id=x.model_version_id,
            )
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
