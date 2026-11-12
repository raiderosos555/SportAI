"""Dixon–Coles goals model (TASK 005), fitted as of a date with numpy only.

Home goals ~ Poisson(exp(c + h + attack[home] + defence[away])), away goals ~
Poisson(exp(c + attack[away] + defence[home])); the low-score correction ρ (Dixon & Coles,
1997) and exponential time decay of match weights are included. A fit uses ONLY matches
played strictly before ``fit_date``.

Every market of one match is read from the same joint score matrix, so the markets are
mutually consistent and their joint probability is known (e.g. home win AND over 1.5).
"""

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

MAX_GOALS = 10
RHO_GRID = tuple(round(-0.20 + 0.01 * i, 2) for i in range(41))


@dataclass(frozen=True)
class GoalsMatch:
    match_date: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    neutral: bool = False  # TASK 018: no home advantage (neutral or unknown venue)


@dataclass(frozen=True)
class GoalsFit:
    teams: dict[str, int]
    attack: np.ndarray
    defence: np.ndarray
    intercept: float
    home_advantage: float
    rho: float
    n_matches: int
    fit_date: str
    games: dict[str, int]

    def rates(self, home: str, away: str, neutral: bool = False) -> tuple[float, float] | None:
        if home not in self.teams or away not in self.teams:
            return None
        i, j = self.teams[home], self.teams[away]
        home_advantage = 0.0 if neutral else self.home_advantage
        lam = math.exp(self.intercept + home_advantage + self.attack[i] + self.defence[j])
        mu = math.exp(self.intercept + self.attack[j] + self.defence[i])
        return lam, mu

    def score_matrix(self, home: str, away: str, neutral: bool = False) -> np.ndarray | None:
        found = self.rates(home, away, neutral)
        if found is None:
            return None
        return score_matrix(*found, self.rho)


def _poisson(rate: float) -> np.ndarray:
    k = np.arange(MAX_GOALS + 1)
    logs = -rate + k * math.log(rate) - np.array([math.lgamma(v + 1) for v in k])
    return np.exp(logs)


def _tau(lam: float, mu: float, rho: float) -> np.ndarray:
    tau = np.ones((2, 2))
    tau[0, 0] = 1 - lam * mu * rho
    tau[0, 1] = 1 + lam * rho
    tau[1, 0] = 1 + mu * rho
    tau[1, 1] = 1 - rho
    return np.clip(tau, 1e-9, None)


def score_matrix(lam: float, mu: float, rho: float) -> np.ndarray:
    matrix = np.outer(_poisson(lam), _poisson(mu))
    matrix[:2, :2] *= _tau(lam, mu, rho)
    return matrix / matrix.sum()


def fit(
    matches: list[GoalsMatch],
    *,
    fit_date: str,
    half_life_days: float = 240.0,
    window_days: int = 730,
    ridge: float = 1.0,
    max_iter: int = 30,
) -> GoalsFit | None:
    """Weighted Poisson regression (IRLS) on matches before ``fit_date``, then ρ by grid."""
    end = date.fromisoformat(fit_date)
    used = [
        m
        for m in matches
        if m.match_date < fit_date and (end - date.fromisoformat(m.match_date)).days <= window_days
    ]
    if len(used) < 60:
        return None
    teams: dict[str, int] = {}
    games: dict[str, int] = {}
    for m in used:
        for team in (m.home, m.away):
            teams.setdefault(team, len(teams))
            games[team] = games.get(team, 0) + 1
    n_teams = len(teams)
    rows = 2 * len(used)
    cols = 2 + 2 * n_teams  # intercept, home advantage, attack[], defence[]
    x = np.zeros((rows, cols))
    y = np.zeros(rows)
    w = np.zeros(rows)
    for r, m in enumerate(used):
        age = (end - date.fromisoformat(m.match_date)).days
        weight = 0.5 ** (age / half_life_days)
        h, a = teams[m.home], teams[m.away]
        x[2 * r, [0, 2 + h, 2 + n_teams + a]] = 1
        x[2 * r, 1] = 0.0 if m.neutral else 1.0
        x[2 * r + 1, [0, 2 + a, 2 + n_teams + h]] = 1
        y[2 * r], y[2 * r + 1] = m.home_goals, m.away_goals
        w[2 * r] = w[2 * r + 1] = weight
    penalty = np.full(cols, ridge)
    penalty[:2] = 0.0  # team effects shrink to zero; intercept and home are free
    beta = np.zeros(cols)
    beta[0] = math.log(max(y.mean(), 0.1))
    for _ in range(max_iter):
        eta = np.clip(x @ beta, -10, 10)
        mu = np.exp(eta)
        z = eta + (y - mu) / mu
        weights = w * mu
        lhs = x.T @ (weights[:, None] * x) + np.diag(penalty)
        new = np.linalg.solve(lhs, x.T @ (weights * z))
        if np.max(np.abs(new - beta)) < 1e-7:
            beta = new
            break
        beta = new
    attack, defence = beta[2 : 2 + n_teams], beta[2 + n_teams :]
    lam = np.exp(
        beta[0]
        + beta[1] * np.array([0.0 if m.neutral else 1.0 for m in used])
        + attack[[teams[m.home] for m in used]]
        + defence[[teams[m.away] for m in used]]
    )
    mus = np.exp(
        beta[0] + attack[[teams[m.away] for m in used]] + defence[[teams[m.home] for m in used]]
    )
    hg = np.array([m.home_goals for m in used])
    ag = np.array([m.away_goals for m in used])
    weights = w[::2]
    best_rho, best_ll = 0.0, -math.inf
    for rho in RHO_GRID:
        tau = np.ones(len(used))
        low = (hg <= 1) & (ag <= 1)
        t00 = (hg == 0) & (ag == 0)
        t01 = (hg == 0) & (ag == 1)
        t10 = (hg == 1) & (ag == 0)
        t11 = (hg == 1) & (ag == 1)
        tau[t00] = 1 - lam[t00] * mus[t00] * rho
        tau[t01] = 1 + lam[t01] * rho
        tau[t10] = 1 + mus[t10] * rho
        tau[t11] = 1 - rho
        if np.any(tau[low] <= 0):
            continue
        ll = float(np.sum(weights * np.log(tau)))
        if ll > best_ll:
            best_rho, best_ll = rho, ll
    return GoalsFit(
        teams=teams,
        attack=attack,
        defence=defence,
        intercept=float(beta[0]),
        home_advantage=float(beta[1]),
        rho=best_rho,
        n_matches=len(used),
        fit_date=fit_date,
        games=games,
    )


def markets(matrix: np.ndarray, lines: tuple[float, ...] = (0.5, 1.5, 2.5, 3.5, 4.5)) -> dict:
    """Probabilities of every supported market of one match from its score matrix."""
    size = matrix.shape[0]
    i, j = np.indices((size, size))
    home, draw, away = (
        float(matrix[i > j].sum()),
        float(np.trace(matrix)),
        float(matrix[i < j].sum()),
    )
    out: dict[tuple, dict[str, float]] = {
        ("1x2", None): {"home": home, "draw": draw, "away": away},
        ("double_chance", None): {
            "home_draw": home + draw,
            "draw_away": draw + away,
            "home_away": home + away,
        },
        ("btts", None): {
            "yes": float(matrix[1:, 1:].sum()),
            "no": 1.0 - float(matrix[1:, 1:].sum()),
        },
        ("home_to_score", None): {
            "yes": float(matrix[1:, :].sum()),
            "no": float(matrix[0, :].sum()),
        },
        ("away_to_score", None): {
            "yes": float(matrix[:, 1:].sum()),
            "no": float(matrix[:, 0].sum()),
        },
    }
    total = i + j
    home_goals = matrix.sum(axis=1)
    away_goals = matrix.sum(axis=0)
    for line in lines:
        over = float(matrix[total > line].sum())
        out[("total", line)] = {"over": over, "under": 1.0 - over}
        home_over = float(home_goals[np.arange(size) > line].sum())
        away_over = float(away_goals[np.arange(size) > line].sum())
        out[("team_total_home", line)] = {"over": home_over, "under": 1.0 - home_over}
        out[("team_total_away", line)] = {"over": away_over, "under": 1.0 - away_over}
    return out


def outcome_mask(
    market_key: str, line: float | str | None, selection: str, size: int = MAX_GOALS + 1
) -> np.ndarray | None:
    """Scores (home goals i, away goals j) where a selection wins, as a 0/1 matrix — the
    same definitions as ``markets``; None for a market that the score matrix does not give
    (TASK 012: joint chance of several selections of one match)."""
    i, j = np.indices((size, size))
    value = float(line) if line not in (None, "") else None
    found = {
        ("1x2", "home"): i > j,
        ("1x2", "draw"): i == j,
        ("1x2", "away"): i < j,
        ("double_chance", "home_draw"): i >= j,
        ("double_chance", "draw_away"): i <= j,
        ("double_chance", "home_away"): i != j,
        ("btts", "yes"): (i > 0) & (j > 0),
        ("btts", "no"): (i == 0) | (j == 0),
        ("home_to_score", "yes"): i > 0,
        ("home_to_score", "no"): i == 0,
        ("away_to_score", "yes"): j > 0,
        ("away_to_score", "no"): j == 0,
    }.get((market_key, selection))
    if found is None and value is not None:
        goals = {"total": i + j, "team_total_home": i, "team_total_away": j}.get(market_key)
        if goals is not None and selection in ("over", "under"):
            found = goals > value if selection == "over" else goals < value
    return None if found is None else found.astype(float)
