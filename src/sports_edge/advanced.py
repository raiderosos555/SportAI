"""Advanced Football Prediction Engine (TASK 008): do the extra facts shown in "Dlaczego model
tak ocenia ten mecz?" really improve the forecasts?

The descriptive facts become model inputs and are tested honestly:

* every feature is computed strictly AS OF the match day — only results of EARLIER days
  enter (the rule of the Elo walk-forward); never statistics recorded after the match,
  lineups published later or matches that had not been played yet;
* a feature without enough history is MISSING: it is never replaced by zero or a typical
  value. A comparison uses only matches where every input exists and ALL variants are
  judged on the SAME matches;
* protocol v2: hyperparameters are tuned on the calibration window after fitting on the
  validation window, the models are refitted on both and compared on the selection
  window; the final holdout is reported only — it never chooses anything;
* markets stay coherent: double chance follows from the same 1X2; 1X2, Over/Under and BTTS
  of a goals model come from one score distribution;
* every variant is reported, also when it is worse than the active model.

Variants: A Elo and B Dixon–Coles exactly as served (baselines); C Elo + form + opponent
strength + home/away; D Dixon–Coles + offensive/defensive indicators; E1–E3 machine learning
(logistic regression, boosted shallow trees, Poisson regression of goals); F squad
information — only with enough as-of lineup history. ``ABLATION`` adds one group at a time.
"""

import math
import random
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import date
from itertools import groupby

import numpy as np

from sports_edge.ablation import fit_softmax, predict_softmax
from sports_edge.models import calibration
from sports_edge.models.goals import MAX_GOALS
from sports_edge.sportsdata import Split

FEATURE_VERSION = "advanced-f1"
OUTCOMES = ("home", "draw", "away")
MARKETS = ("1x2", "double_chance", "total_1.5", "total_2.5", "btts")
MARKET_LABELS = {
    "1x2": "1X2",
    "double_chance": "Podwójna szansa",
    "total_1.5": "Over/Under 1,5",
    "total_2.5": "Over/Under 2,5",
    "btts": "BTTS (obie drużyny strzelą)",
}
GOAL_MARKETS = ("total_1.5", "total_2.5", "btts")
REST_CAP = 21  # days; a longer break is still "rested", the cap is part of the definition
LOAD_DAYS = 14
BOOT = 1000
MIN_SELECTION = 300  # matches in the selection window needed to judge a challenger
MAX_ECE_LOSS = 0.01  # a challenger may not calibrate worse than the champion by more
STAT_MINIMUM = 300  # matches with statistics needed in the fit windows to test xG and shots
SQUAD_MINIMUM = 300  # matches with as-of lineups needed to test squad information
PARSIMONY = 0.0005  # log loss: a simpler variant this close to the best one is preferred
L2_GRID = (1e-3, 1e-2, 1e-1)


# ---------------------------------------------------------------- feature definitions


@dataclass(frozen=True)
class FeatureDef:
    key: str
    label: str
    group: str
    source: str
    observed: str  # when the underlying fact is observed
    available: str  # from when the feature may be used (as-of rule)
    history: int  # earlier matches needed; fewer → missing (never imputed)
    version: str = FEATURE_VERSION


RESULTS = "wyniki meczów ligowych (historical_results, hierarchia źródeł)"
RESULTS_ELO = RESULTS + " + oceny Elo sprzed meczu"
AFTER = "po zakończeniu meczu (wynik końcowy)"
ASOF = "od następnego dnia — liczą się tylko mecze z wcześniejszych dni"
STATS = "statystyki meczowe (stat_observations: PitchAPI, API-Sports)"
STATS_ASOF = "od chwili zapisu obserwacji (available_at), tylko mecze z wcześniejszych dni"
FEATURES = (
    FeatureDef("ppg5", "Punkty na mecz (5)", "forma", RESULTS, AFTER, ASOF, 5),
    FeatureDef("ppg10", "Punkty na mecz (10)", "forma", RESULTS, AFTER, ASOF, 10),
    FeatureDef("gf5", "Gole zdobyte na mecz (5)", "atak/obrona", RESULTS, AFTER, ASOF, 5),
    FeatureDef("ga5", "Gole stracone na mecz (5)", "atak/obrona", RESULTS, AFTER, ASOF, 5),
    FeatureDef("gd10", "Bilans bramkowy na mecz (10)", "atak/obrona", RESULTS, AFTER, ASOF, 10),
    FeatureDef(
        "res5",
        "Forma względem siły rywali (5): wynik ponad oczekiwanie Elo",
        "siła rywali",
        RESULTS_ELO,
        AFTER,
        ASOF,
        5,
    ),
    FeatureDef(
        "opp5", "Średnia siła Elo 5 ostatnich rywali", "siła rywali", RESULTS_ELO, AFTER, ASOF, 5
    ),
    FeatureDef(
        "venue_ppg5",
        "Punkty na mecz u siebie (gospodarz) / na wyjeździe (gość), 5",
        "dom/wyjazd",
        RESULTS,
        AFTER,
        ASOF,
        5,
    ),
    FeatureDef(
        "venue_gd5",
        "Bilans bramkowy u siebie / na wyjeździe na mecz (5)",
        "dom/wyjazd",
        RESULTS,
        AFTER,
        ASOF,
        5,
    ),
    FeatureDef(
        "rest",
        f"Dni odpoczynku od poprzedniego meczu ligowego (maks. {REST_CAP})",
        "odpoczynek",
        RESULTS + " — tylko mecze ligowe obecne w danych (bez pucharów)",
        "data meczu",
        ASOF,
        1,
    ),
    FeatureDef(
        "load14",
        f"Mecze ligowe w poprzednich {LOAD_DAYS} dniach (zagęszczenie terminarza)",
        "odpoczynek",
        RESULTS + " — tylko mecze ligowe obecne w danych (bez pucharów)",
        "data meczu",
        ASOF,
        1,
    ),
)
STAT_FEATURES = (
    FeatureDef("xg5", "xG na mecz (5)", "statystyki", STATS, "po meczu", STATS_ASOF, 5),
    FeatureDef("xga5", "xGA na mecz (5)", "statystyki", STATS, "po meczu", STATS_ASOF, 5),
    FeatureDef("shots5", "Strzały na mecz (5)", "statystyki", STATS, "po meczu", STATS_ASOF, 5),
    FeatureDef("sot5", "Strzały celne na mecz (5)", "statystyki", STATS, "po meczu", STATS_ASOF, 5),
    FeatureDef(
        "big5", "Duże okazje bramkowe na mecz (5)", "statystyki", STATS, "po meczu", STATS_ASOF, 5
    ),
)
SQUAD_FEATURES = (
    FeatureDef(
        "lineup_changes",
        "Zmiany w wyjściowym składzie względem poprzedniego meczu",
        "kadra",
        "składy (lineup_observations: PitchAPI)",
        "publikacja składu, zwykle ok. 60 min przed meczem",
        "od chwili obserwacji, tylko obserwacje sprzed rozpoczęcia (known_before_start)",
        1,
    ),
    FeatureDef(
        "absences",
        "Zweryfikowane kontuzje i zawieszenia",
        "kadra",
        "absencje (player_absence_observations) — brak bezpłatnego zweryfikowanego źródła",
        "publikacja informacji przez źródło",
        "od chwili obserwacji, tylko obserwacje sprzed rozpoczęcia (known_before_start)",
        1,
    ),
)
GROUPS = {
    "forma": ("ppg5", "ppg10"),
    "atak/obrona": ("gf5", "ga5", "gd10"),
    "siła rywali": ("res5", "opp5"),
    "dom/wyjazd": ("venue_ppg5", "venue_gd5"),
    "odpoczynek": ("rest", "load14"),
}
ALL_GROUPS = tuple(GROUPS)
KEYS = tuple(f.key for f in FEATURES)


# ---------------------------------------------------------------- variants


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    kind: str  # served_elo | served_dc | softmax | boosting | poisson_offset | poisson
    groups: tuple[str, ...] = ()
    elo: bool = True  # Elo inputs (log odds of the served Elo forecast)
    ablation: bool = False

    @property
    def goals(self) -> bool:
        return self.kind in ("served_dc", "poisson_offset", "poisson")

    @property
    def size(self) -> int:
        return sum(len(GROUPS[g]) for g in self.groups) * 2 + (2 if self.elo else 0)


VARIANTS = (
    Variant("A_elo", "A — Elo (aktywny model)", "served_elo"),
    Variant("B_dixon_coles", "B — Dixon–Coles (aktywny model)", "served_dc"),
    Variant(
        "C_elo_form",
        "C — Elo + forma + siła rywali + dom/wyjazd",
        "softmax",
        ("forma", "atak/obrona", "siła rywali", "dom/wyjazd"),
    ),
    Variant(
        "D_dc_attack",
        "D — Dixon–Coles + historyczne wskaźniki ofensywne i defensywne",
        "poisson_offset",
        ("atak/obrona", "dom/wyjazd"),
        elo=False,
    ),
    Variant(
        "E1_logit",
        "E1 — ML: regresja logistyczna (Elo, forma, odpoczynek, …)",
        "softmax",
        ALL_GROUPS,
    ),
    Variant(
        "E2_boosting", "E2 — ML: boosting płytkich drzew (te same cechy)", "boosting", ALL_GROUPS
    ),
    Variant(
        "E3_poisson",
        "E3 — ML: regresja Poissona goli (Elo + cechy, bez Dixona–Colesa)",
        "poisson",
        ALL_GROUPS,
    ),
)
ABLATION = (
    Variant("abl_elo", "Elo bez dodatkowych cech (rekalibrowany)", "softmax", (), ablation=True),
    Variant(
        "abl_form",
        "Elo + forma (punkty, gole, bilans)",
        "softmax",
        ("forma", "atak/obrona"),
        ablation=True,
    ),
    Variant(
        "abl_form_opp",
        "Elo + forma i siła rywali",
        "softmax",
        ("forma", "atak/obrona", "siła rywali"),
        ablation=True,
    ),
    Variant("abl_venue", "Elo + dom/wyjazd", "softmax", ("dom/wyjazd",), ablation=True),
    Variant(
        "abl_rest",
        "Elo + odpoczynek i zagęszczenie terminarza",
        "softmax",
        ("odpoczynek",),
        ablation=True,
    ),
    Variant(
        "abl_form_rest",
        "Elo + forma + odpoczynek",
        "softmax",
        ("forma", "atak/obrona", "odpoczynek"),
        ablation=True,
    ),
    Variant(
        "abl_form_only",
        "Sama forma, bez Elo (kontrola powielania informacji Elo)",
        "softmax",
        ("forma", "atak/obrona"),
        elo=False,
        ablation=True,
    ),
    Variant(
        "gabl_dc",
        "Dixon–Coles rekalibrowany (bez cech)",
        "poisson_offset",
        (),
        elo=False,
        ablation=True,
    ),
    Variant(
        "gabl_attack",
        "Dixon–Coles + atak/obrona",
        "poisson_offset",
        ("atak/obrona",),
        elo=False,
        ablation=True,
    ),
    Variant(
        "gabl_rest",
        "Dixon–Coles + odpoczynek i terminarz",
        "poisson_offset",
        ("odpoczynek",),
        elo=False,
        ablation=True,
    ),
    Variant(
        "gabl_all",
        "Dixon–Coles + wszystkie dostępne cechy",
        "poisson_offset",
        ALL_GROUPS,
        elo=False,
        ablation=True,
    ),
)
BY_KEY = {v.key: v for v in (*VARIANTS, *ABLATION)}
BASELINE = {"1x2": "A_elo", "double_chance": "A_elo"} | dict.fromkeys(GOAL_MARKETS, "B_dixon_coles")


# ---------------------------------------------------------------- as-of features


def _mean(values) -> float:
    return sum(values) / len(values)


def _side(games: deque, at_venue: deque, days: list[date], today: date) -> dict:
    """Features of one team before ``today`` (None = not enough history)."""
    last5 = list(games)[-5:]
    full = len(games) >= 10
    out: dict[str, float | None] = {
        "ppg5": _mean([g[0] for g in last5]) if len(last5) == 5 else None,
        "ppg10": _mean([g[0] for g in games]) if full else None,
        "gf5": _mean([g[1] for g in last5]) if len(last5) == 5 else None,
        "ga5": _mean([g[2] for g in last5]) if len(last5) == 5 else None,
        "gd10": _mean([g[1] - g[2] for g in games]) if full else None,
        "res5": _mean([g[3] for g in last5]) if len(last5) == 5 else None,
        "opp5": (_mean([g[4] for g in last5]) - 1500) / 100 if len(last5) == 5 else None,
        "venue_ppg5": _mean([g[0] for g in at_venue]) if len(at_venue) == 5 else None,
        "venue_gd5": _mean([g[1] - g[2] for g in at_venue]) if len(at_venue) == 5 else None,
        "rest": float(min((today - days[-1]).days, REST_CAP)) if days else None,
        "load14": float(sum(1 for d in days if (today - d).days <= LOAD_DAYS)) if days else None,
    }
    return out


def team_features(rows: list[dict], *, k: float, home_advantage: float) -> dict[str, dict]:
    """{match id: {"home": {...}, "away": {...}}} strictly as of the match day.

    All matches of a day are described before any of the day's results is added (as in the
    Elo walk-forward). A row without a score (an upcoming match) gets features and changes
    nothing."""
    history: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
    venue: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=5))
    dates: dict[str, list[date]] = defaultdict(list)
    ratings: dict[str, float] = {}
    out: dict[str, dict] = {}
    ordered = sorted(rows, key=lambda r: (r["match_date"], r["id"]))
    for day, group in groupby(ordered, key=lambda r: r["match_date"]):
        today = date.fromisoformat(day)
        played = []
        for r in group:
            out[r["id"]] = {
                side: _side(history[r[side]], venue[(r[side], side)], dates[r[side]], today)
                for side in ("home", "away")
            }
            if r.get("home_score") is not None and r.get("away_score") is not None:
                played.append(r)
        for r in played:  # the day's results enter from the next day on
            rh, ra = ratings.get(r["home"], 1500.0), ratings.get(r["away"], 1500.0)
            expected = 1 / (1 + 10 ** (-(rh + home_advantage - ra) / 400))
            h, a = r["home_score"], r["away_score"]
            actual = 1.0 if h > a else 0.5 if h == a else 0.0
            for side, team, own, other, score, exp, opp in (
                ("home", r["home"], h, a, actual, expected, ra),
                ("away", r["away"], a, h, 1 - actual, 1 - expected, rh),
            ):
                points = 3 if own > other else 1 if own == other else 0
                game = (points, own, other, score - exp, opp)
                history[team].append(game)
                venue[(team, side)].append(game)
                dates[team] = [d for d in dates[team] if (today - d).days <= 60] + [today]
            change = k * (actual - expected)
            ratings[r["home"]] = rh + change
            ratings[r["away"]] = ra - change
    return out


def complete(values: dict, keys=KEYS) -> bool:
    return all(values[side][k] is not None for side in ("home", "away") for k in keys)


# ---------------------------------------------------------------- models


def _design(ids, feats, variant: Variant, elo: dict | None) -> np.ndarray:
    keys = [k for g in variant.groups for k in GROUPS[g]]
    rows = []
    for i in ids:
        values = []
        if variant.elo:
            p = elo[i]
            values += [
                math.log(max(1e-6, p["home"]) / max(1e-6, p["away"])),
                math.log(max(1e-6, p["draw"])),
            ]
        values += [feats[i][side][k] for side in ("home", "away") for k in keys]
        rows.append(values)
    return np.array(rows, dtype=float).reshape(len(ids), -1)


def fit_poisson(x: np.ndarray, y: np.ndarray, offset: np.ndarray | None, *, l2: float) -> dict:
    """Poisson regression with a log link (Newton–Raphson, L2 on standardised inputs)."""
    mean, std = x.mean(axis=0), x.std(axis=0) + 1e-9
    z = np.hstack([(x - mean) / std, np.ones((len(x), 1))])
    base = offset if offset is not None else np.zeros(len(x))
    w = np.zeros(z.shape[1])
    w[-1] = math.log(max(1e-6, y.mean()) / max(1e-6, float(np.exp(base).mean())))
    penalty = l2 * len(x) * np.r_[np.ones(z.shape[1] - 1), 0.0]
    for _ in range(100):
        mu = np.exp(np.clip(z @ w + base, -8, 4))
        grad = z.T @ (y - mu) - penalty * w
        hess = (z * mu[:, None]).T @ z + np.diag(penalty)
        step = np.linalg.solve(hess, grad)
        w += step
        if float(np.abs(step).max()) < 1e-9:
            break
    return {"w": w.tolist(), "mean": mean.tolist(), "std": std.tolist()}


def predict_poisson(model: dict, x: np.ndarray, offset: np.ndarray | None) -> np.ndarray:
    mean, std = np.array(model["mean"]), np.array(model["std"])
    z = np.hstack([(x - mean) / std, np.ones((len(x), 1))])
    base = offset if offset is not None else np.zeros(len(x))
    return np.exp(np.clip(z @ np.array(model["w"]) + base, -8, 4))


def _bin_edges(x: np.ndarray, bins: int = 16) -> list[list[float]]:
    qs = np.linspace(0, 1, bins + 1)[1:-1]
    return [np.unique(np.quantile(col, qs)).tolist() for col in x.T]


def _binned(x: np.ndarray, edges: list[list[float]]) -> np.ndarray:
    if not edges:
        return np.zeros((len(x), 0), dtype=int)
    return np.column_stack(
        [np.searchsorted(np.array(e), x[:, j], side="right") for j, e in enumerate(edges)]
    )


def _split(b, g, h, rows, *, min_leaf: int, lam: float):
    total_g, total_h, n = g[rows].sum(), h[rows].sum(), len(rows)
    best, best_gain = None, 1e-9
    for j in range(b.shape[1]):
        col = b[rows, j]
        size = int(col.max()) + 1
        gl = np.cumsum(np.bincount(col, weights=g[rows], minlength=size))[:-1]
        hl = np.cumsum(np.bincount(col, weights=h[rows], minlength=size))[:-1]
        nl = np.cumsum(np.bincount(col, minlength=size))[:-1]
        ok = (nl >= min_leaf) & (n - nl >= min_leaf)
        if not ok.any():
            continue
        gain = (
            gl**2 / (hl + lam)
            + (total_g - gl) ** 2 / (total_h - hl + lam)
            - total_g**2 / (total_h + lam)
        )
        gain[~ok] = -np.inf
        t = int(np.argmax(gain))
        if gain[t] > best_gain:
            best, best_gain = (j, t), float(gain[t])
    return best


def _tree(b, g, h, rows, depth: int, *, min_leaf: int, lam: float) -> dict:
    found = _split(b, g, h, rows, min_leaf=min_leaf, lam=lam) if depth > 0 else None
    if found is None:
        return {"v": float(g[rows].sum() / (h[rows].sum() + lam))}
    j, t = found
    left = rows[b[rows, j] <= t]
    right = rows[b[rows, j] > t]
    return {
        "f": j,
        "t": t,
        "l": _tree(b, g, h, left, depth - 1, min_leaf=min_leaf, lam=lam),
        "r": _tree(b, g, h, right, depth - 1, min_leaf=min_leaf, lam=lam),
    }


def _apply_tree(tree: dict, b: np.ndarray) -> np.ndarray:
    out = np.empty(len(b))

    def walk(node, rows):
        if "v" in node:
            out[rows] = node["v"]
            return
        mask = b[rows, node["f"]] <= node["t"]
        walk(node["l"], rows[mask])
        walk(node["r"], rows[~mask])

    walk(tree, np.arange(len(b)))
    return out


def _softmax(f: np.ndarray) -> np.ndarray:
    f = f - f.max(axis=1, keepdims=True)
    p = np.exp(f)
    return p / p.sum(axis=1, keepdims=True)


def fit_boosting(
    x: np.ndarray,
    y: np.ndarray,
    *,
    rounds: int,
    rate: float = 0.05,
    depth: int = 2,
    min_leaf: int = 50,
    lam: float = 1.0,
    watch: tuple[np.ndarray, np.ndarray] | None = None,
) -> dict:
    """Multinomial gradient boosting of shallow trees on quantile bins (Newton leaves).
    With ``watch`` (x, y of a later window) it also returns the log loss after each round."""
    edges = _bin_edges(x)
    b = _binned(x, edges)
    prior = np.log(np.bincount(y, minlength=3) / len(y) + 1e-9)
    f = np.tile(prior, (len(x), 1))
    target = np.eye(3)[y]
    trees = []
    curve = []
    if watch is not None:
        wb = _binned(watch[0], edges)
        wf = np.tile(prior, (len(wb), 1))
    rows = np.arange(len(x))
    for _ in range(rounds):
        p = _softmax(f)
        step = []
        for c in range(3):
            g = target[:, c] - p[:, c]
            h = np.maximum(p[:, c] * (1 - p[:, c]), 1e-9)
            tree = _tree(b, g, h, rows, depth, min_leaf=min_leaf, lam=lam)
            f[:, c] += rate * _apply_tree(tree, b)
            step.append(tree)
            if watch is not None:
                wf[:, c] += rate * _apply_tree(tree, wb)
        trees.append(step)
        if watch is not None:
            q = _softmax(wf)
            curve.append(float(-np.log(np.clip(q[np.arange(len(q)), watch[1]], 1e-12, 1)).mean()))
    return {"edges": edges, "prior": prior.tolist(), "rate": rate, "trees": trees, "curve": curve}


def predict_boosting(model: dict, x: np.ndarray, rounds: int | None = None) -> np.ndarray:
    b = _binned(x, model["edges"])
    f = np.tile(np.array(model["prior"]), (len(x), 1))
    for step in model["trees"][:rounds]:
        for c, tree in enumerate(step):
            f[:, c] += model["rate"] * _apply_tree(tree, b)
    return _softmax(f)


def score_markets(lam: np.ndarray, mu: np.ndarray, rho: np.ndarray) -> dict[str, np.ndarray]:
    """1X2, Over/Under 1.5/2.5 and BTTS of every match from ONE score distribution (the
    Dixon–Coles matrix of models.goals, vectorised) — the markets cannot contradict each
    other."""
    k = np.arange(MAX_GOALS + 1)
    logfact = np.array([math.lgamma(v + 1) for v in k])

    def pmf(rate):
        rate = np.clip(rate, 1e-6, None)
        return np.exp(-rate[:, None] + k[None, :] * np.log(rate)[:, None] - logfact[None, :])

    m = pmf(lam)[:, :, None] * pmf(mu)[:, None, :]
    tau = np.empty((len(lam), 2, 2))
    tau[:, 0, 0] = 1 - lam * mu * rho
    tau[:, 0, 1] = 1 + lam * rho
    tau[:, 1, 0] = 1 + mu * rho
    tau[:, 1, 1] = 1 - rho
    m[:, :2, :2] *= np.clip(tau, 1e-9, None)
    m /= m.sum(axis=(1, 2), keepdims=True)
    i, j = np.indices((MAX_GOALS + 1, MAX_GOALS + 1))
    total = i + j
    return {
        "1x2": np.column_stack([m[:, i > j].sum(1), m[:, i == j].sum(1), m[:, i < j].sum(1)]),
        "total_1.5": m[:, total > 1.5].sum(1),
        "total_2.5": m[:, total > 2.5].sum(1),
        "btts": m[:, 1:, 1:].sum(axis=(1, 2)),
    }


def with_double_chance(out: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Double chance from the SAME 1X2 (1X = 1 − P(2), X2 = 1 − P(1), 12 = 1 − P(X))."""
    p = out["1x2"]
    return {**out, "double_chance": np.column_stack([1 - p[:, 2], 1 - p[:, 0], 1 - p[:, 1]])}


# ---------------------------------------------------------------- scoring


def truth(rows_by_id: dict, ids: list[str]) -> dict[str, np.ndarray]:
    h = np.array([rows_by_id[i]["home_score"] for i in ids])
    a = np.array([rows_by_id[i]["away_score"] for i in ids])
    y = np.where(h > a, 0, np.where(h == a, 1, 2))
    return {
        "1x2": y,
        "double_chance": np.column_stack([y != 2, y != 0, y != 1]).astype(int),
        "total_1.5": (h + a > 1.5).astype(int),
        "total_2.5": (h + a > 2.5).astype(int),
        "btts": ((h > 0) & (a > 0)).astype(int),
    }


def losses(market: str, p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Log loss of every match (double chance: mean of its three binary sides)."""
    if market == "1x2":
        return -np.log(np.clip(p[np.arange(len(p)), y], 1e-12, 1.0))
    q = np.clip(p, 1e-12, 1 - 1e-12)
    loss = -(y * np.log(q) + (1 - y) * np.log(1 - q))
    return loss.mean(axis=1) if loss.ndim == 2 else loss


def _pooled(market: str, p: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Every state of every match as one binary forecast (1X2 one-vs-rest)."""
    if market == "1x2":
        return p.ravel(), np.eye(3)[y].ravel()
    return np.asarray(p, dtype=float).ravel(), np.asarray(y, dtype=float).ravel()


def _bins(p: np.ndarray, bins: int = 10) -> np.ndarray:
    """Bin of every forecast — the ranges of ``calibration.reliability`` (1.0 in the last)."""
    return np.minimum((p * bins).astype(int), bins - 1)


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error, vectorised (same bins as ``calibration.ece``)."""
    idx = _bins(p, bins)
    total = 0.0
    for b in range(bins):
        mask = idx == b
        if mask.any():
            total += mask.sum() / len(p) * abs(float(p[mask].mean()) - float(y[mask].mean()))
    return total


def metrics(market: str, p: np.ndarray, y: np.ndarray) -> dict:
    n = len(y)
    if market == "1x2":
        brier = float(((p - np.eye(3)[y]) ** 2).sum(axis=1).mean())
        accuracy = float((p.argmax(axis=1) == y).mean())
    else:
        brier = float(((p - y) ** 2).mean())
        accuracy = None
    return {
        "n": n,
        "log_loss": round(float(losses(market, p, y).mean()), 5),
        "brier": round(brier, 5),
        "ece": round(ece(*_pooled(market, p, y)), 5),
        "accuracy": round(accuracy, 4) if accuracy is not None else None,
    }


def interval(diff: np.ndarray, seed: int, draws: int = BOOT) -> list[float] | None:
    """95% interval of the mean paired difference; resamples whole matches."""
    if len(diff) < 20:
        return None
    rng = np.random.default_rng(seed)
    means = diff[rng.integers(0, len(diff), size=(draws, len(diff)))].mean(axis=1)
    return [round(float(np.quantile(means, 0.025)), 5), round(float(np.quantile(means, 0.975)), 5)]


def period(day: str) -> str:
    """Half-season label: 2023/24 I (July–December) or 2023/24 II (January–June)."""
    year, month = int(day[:4]), int(day[5:7])
    start = year if month >= 7 else year - 1
    return f"{start}/{str(start + 1)[2:]} {'I' if month >= 7 else 'II'}"


def reliability(market: str, p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    """Calibration table: mean forecast vs observed frequency (Wilson 95%) per range."""
    pooled_p, pooled_y = _pooled(market, p, y)
    idx = _bins(pooled_p, bins)
    out = []
    for b in range(bins):
        mask = idx == b
        if not mask.any():
            continue
        n = int(mask.sum())
        hits = float(pooled_y[mask].sum())
        low, high = calibration.wilson_interval(hits, n)
        out.append(
            {
                "range": f"{b * 100 // bins}–{(b + 1) * 100 // bins}%",
                "n": n,
                "mean_p": round(float(pooled_p[mask].mean()), 4),
                "freq": round(hits / n, 4),
                "freq_ci95": [round(low, 4), round(high, 4)],
            }
        )
    return out


# ---------------------------------------------------------------- the experiment


def _fit_predict(variant: Variant, data: dict, fit: list[str], tune: tuple, evaluate: dict):
    """Fit one variant on ``fit`` (hyperparameters tuned on ``tune`` = (train, check)) and
    return (model, {window: market outputs})."""
    feats, elo, rates, rows_by_id = data["feats"], data["elo"], data["rates"], data["rows"]

    def design(ids):
        return _design(ids, feats, variant, elo)

    def goals_of(ids, side):
        key = "home_score" if side == "home" else "away_score"
        return np.array([rows_by_id[i][key] for i in ids], dtype=float)

    def offset(ids, side):
        if variant.kind != "poisson_offset":
            return None
        return np.log(np.array([rates[i][0 if side == "home" else 1] for i in ids]))

    def outcome(ids):
        return truth(rows_by_id, ids)["1x2"]

    if variant.kind == "softmax":

        def fitted(ids, l2):
            return fit_softmax(design(ids), outcome(ids), l2=l2)

        train, check = tune
        best = min(
            L2_GRID,
            key=lambda l2: float(
                losses(
                    "1x2", predict_softmax(fitted(train, l2), design(check)), outcome(check)
                ).mean()
            ),
        )
        model = fitted(fit, best)
        hyper = {"l2": best}

        def predict(ids):
            return with_double_chance({"1x2": predict_softmax(model, design(ids))})

    elif variant.kind == "boosting":
        train, check = tune
        trial = fit_boosting(
            design(train), outcome(train), rounds=300, watch=(design(check), outcome(check))
        )
        best = int(np.argmin(trial["curve"])) + 1
        model = fit_boosting(design(fit), outcome(fit), rounds=best)
        hyper = {"rounds": best, "depth": 2, "rate": 0.05, "min_leaf": 50}

        def predict(ids):
            return with_double_chance({"1x2": predict_boosting(model, design(ids))})

    else:  # poisson_offset / poisson: one score distribution per match

        def fitted(ids, l2):
            x = design(ids)
            return {
                side: fit_poisson(x, goals_of(ids, side), offset(ids, side), l2=l2)
                for side in ("home", "away")
            }

        def rates_of(m, ids):
            x = design(ids)
            return (
                predict_poisson(m["home"], x, offset(ids, "home")),
                predict_poisson(m["away"], x, offset(ids, "away")),
            )

        def rho(ids):
            if variant.kind == "poisson_offset":
                return np.array([rates[i][2] for i in ids])
            return np.zeros(len(ids))  # independent goals (no Dixon–Coles correction)

        def loglik(m, ids):
            lam, mu = rates_of(m, ids)
            h, a = goals_of(ids, "home"), goals_of(ids, "away")
            return float((h * np.log(lam) - lam + a * np.log(mu) - mu).mean())

        train, check = tune
        best = max(L2_GRID, key=lambda l2: loglik(fitted(train, l2), check))
        model = fitted(fit, best)
        hyper = {"l2": best, "rho": "z Dixona–Colesa" if variant.kind == "poisson_offset" else 0}

        def predict(ids):
            lam, mu = rates_of(model, ids)
            return with_double_chance(score_markets(lam, mu, rho(ids)))

    return {"kind": variant.kind, "params": model, "hyper": hyper}, {
        name: predict(ids) for name, ids in evaluate.items() if ids
    }


def _served(variant: Variant, data: dict, ids: list[str]) -> dict[str, np.ndarray]:
    if variant.kind == "served_elo":
        p = np.array([[data["elo"][i][o] for o in OUTCOMES] for i in ids])
        return with_double_chance({"1x2": p})
    served = [data["goals"][i] for i in ids]
    out = {
        "1x2": np.array([[g[("1x2", None)][o] for o in OUTCOMES] for g in served]),
        "total_1.5": np.array([g[("total", 1.5)]["over"] for g in served]),
        "total_2.5": np.array([g[("total", 2.5)]["over"] for g in served]),
        "btts": np.array([g[("btts", None)]["yes"] for g in served]),
    }
    return with_double_chance(out)


def coverage_report(feats: dict, ids_by_window: dict[str, list[str]]) -> list[dict]:
    """Source, timing, version, sample and missing values of every feature."""
    out = []
    for f in FEATURES:
        windows = {}
        for name, ids in ids_by_window.items():
            have = sum(
                1
                for i in ids
                if feats[i]["home"][f.key] is not None and feats[i]["away"][f.key] is not None
            )
            windows[name] = {"available": have, "missing": len(ids) - have}
        out.append({**asdict(f), "windows": windows})
    return out


def run(
    rows: list[dict],
    *,
    split: Split,
    elo: dict[str, dict],
    goals: dict[str, dict],
    rates: dict[str, tuple[float, float, float]],
    elo_params: dict,
    coverage: dict | None = None,
    seed: int = 7,
) -> dict:
    """The whole comparison. ``elo``/``goals``: served as-of forecasts per match id (A, B);
    ``rates``: as-of Dixon–Coles (λ, μ, ρ) per match id; ``coverage``: matches with match
    statistics / as-of lineups per window (decides whether F and the xG ablation run)."""
    coverage = coverage or {}
    rows_by_id = {r["id"]: r for r in rows}
    feats = team_features(rows, k=elo_params["k"], home_advantage=elo_params["home_advantage"])
    windows = ("validation", "calibration", "selection", "holdout")
    every = {w: [] for w in windows}
    for r in sorted(rows, key=lambda r: (r["match_date"], r["id"])):
        name = split.window(r["match_date"])
        if name in every:
            every[name].append(r["id"])
    common = {
        w: [i for i in ids if i in elo and i in goals and i in rates and complete(feats[i])]
        for w, ids in every.items()
    }
    if min(len(common["validation"]), len(common["calibration"])) < 150:
        raise ValueError("Za mało meczów z kompletem cech w oknach dopasowania protokołu v2")
    if len(common["selection"]) < 100:
        raise ValueError("Za mało meczów z kompletem cech w oknie selekcji")
    fit = common["validation"] + common["calibration"]
    data = {"feats": feats, "elo": elo, "goals": goals, "rates": rates, "rows": rows_by_id}
    evaluate = {"fit": fit, "selection": common["selection"], "holdout": common["holdout"]}
    truths = {name: truth(rows_by_id, ids) for name, ids in evaluate.items() if ids}
    outputs: dict[str, dict] = {}
    fitted: dict[str, dict] = {}
    for variant in (*VARIANTS, *ABLATION):
        if variant.kind.startswith("served"):
            outputs[variant.key] = {
                name: _served(variant, data, ids) for name, ids in evaluate.items() if ids
            }
            continue
        model, outputs[variant.key] = _fit_predict(
            variant,
            data,
            fit,
            (common["validation"], common["calibration"]),
            evaluate,
        )
        fitted[variant.key] = model

    league = {i: rows_by_id[i]["competition"] for i in rows_by_id}
    report: dict = {
        "feature_version": FEATURE_VERSION,
        "split": split.as_dict(),
        "elo_parameters": {"k": elo_params["k"], "home_advantage": elo_params["home_advantage"]},
        "sample": {
            "all": {w: len(ids) for w, ids in every.items()},
            "complete": {w: len(ids) for w, ids in common.items()},
            "fit": len(fit),
            "rule": "porównanie tylko na meczach z kompletem cech obu drużyn (bez imputacji)",
        },
        "features": coverage_report(feats, every),
        "variants": {
            v.key: {
                "label": v.label,
                "kind": v.kind,
                "groups": list(v.groups),
                "elo_inputs": v.elo,
                "inputs": v.size,
                "ablation": v.ablation,
                "markets": list(MARKETS) if v.goals else ["1x2", "double_chance"],
                "hyper": (fitted.get(v.key) or {}).get("hyper"),
            }
            for v in (*VARIANTS, *ABLATION)
        },
        "markets": {},
    }
    rng_seed = seed
    for market in MARKETS:
        base = BASELINE[market]
        block: dict = {"baseline": base, "windows": {}, "leagues": {}, "periods": {}}
        for name in ("fit", "selection", "holdout"):
            if name not in truths:
                continue
            y = truths[name][market]
            ids = evaluate[name]
            base_loss = losses(market, outputs[base][name][market], y)
            base_row = metrics(market, outputs[base][name][market], y)
            found = {}
            for key, out in outputs.items():
                if market not in out[name]:
                    continue
                p = out[name][market]
                row = base_row if key == base else metrics(market, p, y)
                loss = losses(market, p, y)
                if key != base:
                    rng_seed += 1
                    row["delta_log_loss"] = round(float((loss - base_loss).mean()), 5)
                    row["delta_brier"] = round(row["brier"] - base_row["brier"], 5)
                    row["delta_ece"] = round(row["ece"] - base_row["ece"], 5)
                    if name != "fit":
                        row["ci95"] = interval(loss - base_loss, rng_seed)
                found[key] = row
                if name == "selection" and not BY_KEY[key].ablation:
                    by_league = defaultdict(list)
                    by_period = defaultdict(list)
                    for idx, i in enumerate(ids):
                        by_league[league[i]].append(idx)
                        by_period[period(rows_by_id[i]["match_date"])].append(idx)
                    block["leagues"][key] = _segments(
                        by_league, loss, base_loss, key == base, rng_seed
                    )
                    block["periods"][key] = _segments(
                        by_period, loss, base_loss, key == base, rng_seed
                    )
                if name == "holdout" and not BY_KEY[key].ablation:
                    by_period = defaultdict(list)
                    for idx, i in enumerate(ids):
                        by_period[period(rows_by_id[i]["match_date"])].append(idx)
                    block.setdefault("holdout_periods", {})[key] = _segments(
                        by_period, loss, base_loss, key == base, rng_seed
                    )
            block["windows"][name] = found
        report["markets"][market] = block

    report["redundancy"] = _redundancy(fit, feats, elo)
    report["skipped"] = _skipped(coverage)
    report["worse"] = [
        {
            "market": market,
            "variant": key,
            "label": BY_KEY[key].label,
            "delta_log_loss": row["delta_log_loss"],
        }
        for market, block in report["markets"].items()
        for key, row in block["windows"].get("selection", {}).items()
        if row.get("delta_log_loss") is not None and row["delta_log_loss"] > 0
    ]
    report["decision"] = {m: decide(report, m, outputs, truths) for m in MARKETS}
    report["leakage"] = audit(rows, feats, common["selection"], elo_params, seed=seed)
    chosen = {d["candidate"] for d in report["decision"].values() if d.get("passed")}
    report["models"] = {key: plain(fitted[key]) for key in chosen if key in fitted}
    for d in report["decision"].values():
        if d.get("candidate") in fitted:
            d["hyper"] = fitted[d["candidate"]]["hyper"]
    report["conclusion"] = conclusion(report)
    return report


def _segments(groups: dict, loss, base_loss, is_base: bool, seed: int) -> dict:
    out = {}
    for name, idx in sorted(groups.items()):
        idx = np.array(idx)
        row = {"n": int(len(idx)), "log_loss": round(float(loss[idx].mean()), 5)}
        if not is_base:
            diff = loss[idx] - base_loss[idx]
            row["delta_log_loss"] = round(float(diff.mean()), 5)
            row["ci95"] = interval(diff, seed, draws=500)
        out[name] = row
    return out


def _redundancy(fit: list[str], feats: dict, elo: dict) -> list[dict]:
    """Correlation of every feature (home − away) with the Elo log odds on the fit windows:
    a high value means the feature mostly repeats what Elo already knows."""
    log_odds = np.array(
        [math.log(max(1e-6, elo[i]["home"]) / max(1e-6, elo[i]["away"])) for i in fit]
    )
    out = []
    for f in FEATURES:
        diff = np.array([feats[i]["home"][f.key] - feats[i]["away"][f.key] for i in fit])
        corr = float(np.corrcoef(diff, log_odds)[0, 1]) if diff.std() > 0 else 0.0
        out.append({"feature": f.key, "label": f.label, "correlation_with_elo": round(corr, 3)})
    return out


def _skipped(coverage: dict) -> list[dict]:
    fit_stats = sum((coverage.get("stats") or {}).get(w, 0) for w in ("validation", "calibration"))
    fit_squad = sum(
        (coverage.get("lineups") or {}).get(w, 0) for w in ("validation", "calibration")
    )
    out = []
    if fit_stats < STAT_MINIMUM:
        out.append(
            {
                "variant": "abl_xg",
                "label": "Elo + statystyki xG, strzały, duże okazje",
                "reason": (
                    f"za mało meczów ze statystykami w oknach dopasowania: {fit_stats} < "
                    f"{STAT_MINIMUM} (statystyki zapisane: {coverage.get('stats', {})})"
                ),
                "features": [asdict(f) for f in STAT_FEATURES],
            }
        )
    if fit_squad < SQUAD_MINIMUM:
        out.append(
            {
                "variant": "F_squad",
                "label": "F — model rozszerzony o informacje kadrowe",
                "reason": (
                    f"za mało meczów ze składami znanymi przed meczem: {fit_squad} < "
                    f"{SQUAD_MINIMUM} (obserwacje: {coverage.get('lineups', {})})"
                ),
                "features": [asdict(f) for f in SQUAD_FEATURES],
            }
        )
    return out


def decide(report: dict, market: str, outputs: dict, truths: dict) -> dict:
    """Is the best extended variant a candidate for the prospective (shadow) evaluation of
    this market? Judged on the selection window only; the user decides afterwards."""
    block = report["markets"][market]
    selection = block["windows"].get("selection", {})
    base = block["baseline"]
    pool = [k for k in selection if k != base and not k.startswith(("A_", "B_"))]
    if not pool:
        return {"candidate": None, "passed": False, "criteria": [], "verdict": "brak wariantów"}
    best = min(pool, key=lambda k: selection[k]["log_loss"])
    simpler = [
        k
        for k in pool
        if BY_KEY[k].size < BY_KEY[best].size
        and selection[k]["log_loss"] <= selection[best]["log_loss"] + PARSIMONY
    ]
    if simpler:  # do not prefer a bigger model for a negligible gain
        best = min(simpler, key=lambda k: (BY_KEY[k].size, selection[k]["log_loss"]))
    row = selection[best]
    base_row = selection[base]
    ci = row.get("ci95")
    leagues = block["leagues"].get(best) or {}
    periods = block["periods"].get(best) or {}
    fit_row = block["windows"].get("fit", {}).get(best, {})
    gain_out = -row["delta_log_loss"]
    gain_in = -(fit_row.get("delta_log_loss") or 0.0)
    worse_leagues = [
        name
        for name, seg in leagues.items()
        if seg.get("ci95") and seg["delta_log_loss"] > 0 and seg["ci95"][0] > 0
    ]
    unstable = [
        name
        for name, seg in periods.items()
        if seg["n"] >= 50 and seg.get("delta_log_loss", 0) >= 0
    ]
    criteria = [
        (
            "Odpowiednia próba",
            row["n"] >= MIN_SELECTION,
            f"{row['n']} meczów w oknie selekcji (wymagane ≥ {MIN_SELECTION})",
        ),
        (
            "Poprawa log loss potwierdzona",
            bool(ci) and row["delta_log_loss"] < 0 and ci[1] < 0,
            f"Δ {row['delta_log_loss']:+.5f}, 95% CI {ci}",
        ),
        ("Brier nie gorszy", row["delta_brier"] <= 0, f"Δ Brier {row['delta_brier']:+.5f}"),
        (
            "Kalibracja",
            row["ece"] <= base_row["ece"] + MAX_ECE_LOSS,
            f"ECE {row['ece']:.4f} vs {base_row['ece']:.4f} (tolerancja +{MAX_ECE_LOSS})",
        ),
        (
            "Brak istotnego pogorszenia w ligach",
            not worse_leagues,
            "istotnie gorzej: " + ", ".join(worse_leagues) if worse_leagues else "żadna liga",
        ),
        (
            "Stabilność w kolejnych okresach",
            not unstable,
            "bez poprawy: " + ", ".join(unstable) if unstable else "poprawa w każdym półroczu",
        ),
        (
            "Brak oznak przeuczenia",
            gain_out > 0 and gain_in <= 3 * gain_out + 0.002,
            f"poprawa w danych dopasowania {gain_in:+.5f}, poza nimi {gain_out:+.5f}",
        ),
    ]
    passed = all(ok for _name, ok, _detail in criteria)
    holdout = block["windows"].get("holdout", {}).get(best)
    return {
        "candidate": best,
        "label": BY_KEY[best].label,
        "baseline": base,
        "passed": passed,
        "criteria": [{"name": n, "ok": bool(ok), "detail": d} for n, ok, d in criteria]
        + [{"name": "Decyzja użytkownika", "ok": None, "detail": "wymagana przed oceną"}],
        "holdout": holdout,  # reported only — it never chooses the candidate
        "reliability": {
            "candidate": reliability(
                market, outputs[best]["selection"][market], truths["selection"][market]
            ),
            "baseline": reliability(
                market, outputs[base]["selection"][market], truths["selection"][market]
            ),
        },
        "verdict": (
            "kandydat do oceny prospektywnej (tryb cienia) — wymaga decyzji użytkownika"
            if passed
            else "odrzucony offline — aktywny model zostaje"
        ),
    }


def audit(rows: list[dict], feats: dict, ids: list[str], elo_params: dict, *, seed: int) -> dict:
    """Leakage control: the features of sampled matches recomputed from ONLY the results of
    earlier days must be identical to the stored ones."""
    rng = random.Random(seed)
    sample = rng.sample(ids, min(12, len(ids)))
    mismatches = []
    for i in sample:
        day = next(r["match_date"] for r in rows if r["id"] == i)
        earlier = [r for r in rows if r["match_date"] < day or r["id"] == i]
        again = team_features(
            earlier, k=elo_params["k"], home_advantage=elo_params["home_advantage"]
        )[i]
        if again != feats[i]:
            mismatches.append(i)
    return {
        "checked": len(sample),
        "mismatches": len(mismatches),
        "ok": not mismatches,
        "rules": [
            "cechy liczone przed dodaniem wyników dnia meczu (tylko mecze z wcześniejszych dni)",
            "statystyki meczowe i składy: tylko obserwacje dostępne przed meczem "
            "(available_at / known_before_start)",
            "hiperparametry: okno kalibracji; wybór modelu: okno selekcji; holdout tylko "
            "raportowany",
            "brakujące cechy nie są uzupełniane — mecz bez kompletu cech nie wchodzi do porównania",
        ],
    }


def conclusion(report: dict) -> str:
    passed = [m for m, d in report["decision"].items() if d.get("passed")]
    if not passed:
        return (
            "Żaden wariant rozszerzony nie dał potwierdzonej poprawy względem aktywnych modeli "
            "w oknie selekcji — Elo i Dixon–Coles pozostają aktywne. Wyniki wszystkich "
            "wariantów, także gorszych, są zapisane."
        )
    names = ", ".join(f"{MARKET_LABELS[m]} ({report['decision'][m]['label']})" for m in passed)
    return (
        f"Potwierdzona poprawa offline: {names}. Challenger może trafić do oceny prospektywnej "
        "(tryb cienia) dopiero po decyzji użytkownika; aktywne modele pozostają bez zmian."
    )


# ---------------------------------------------------------------- prospective forecasts


def plain(value):
    """JSON-ready copy of fitted parameters (arrays become lists)."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _softmax_params(params: dict) -> dict:
    return {k: np.array(params[k], dtype=float) for k in ("w", "mean", "std")}


def predict_match(
    model: dict,
    variant: str,
    features: dict,
    *,
    elo: dict | None,
    rates: tuple[float, float, float] | None,
) -> dict[str, list[float] | float]:
    """Markets of ONE upcoming match from a stored challenger — the same code as the
    backtest, so the frozen forecast is exactly what was validated."""
    v = BY_KEY[variant]
    key = "upcoming"
    data_feats = {key: features}
    x = _design([key], data_feats, v, {key: elo} if elo else None)
    if v.kind == "softmax":
        out = with_double_chance({"1x2": predict_softmax(_softmax_params(model["params"]), x)})
    elif v.kind == "boosting":
        out = with_double_chance({"1x2": predict_boosting(model["params"], x)})
    else:
        if v.kind == "poisson_offset" and rates is None:
            raise ValueError("Brak prognozy Dixona–Colesa dla tego meczu")
        off_h = np.log([rates[0]]) if v.kind == "poisson_offset" else None
        off_a = np.log([rates[1]]) if v.kind == "poisson_offset" else None
        lam = predict_poisson(model["params"]["home"], x, off_h)
        mu = predict_poisson(model["params"]["away"], x, off_a)
        rho = np.array([rates[2] if v.kind == "poisson_offset" and rates else 0.0])
        out = with_double_chance(score_markets(lam, mu, rho))
    return {
        m: (value[0].tolist() if value.ndim == 2 else float(value[0])) for m, value in out.items()
    }
