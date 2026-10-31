"""Elo baseline with an ordered draw margin. Walk-forward: predict first, then update.

Chosen as the first baseline because it needs only legally obtainable historical results
(date, teams, score) and its predictions for match t use matches available before t.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from math import log


@dataclass(frozen=True)
class HistoricalMatch:
    available_at: datetime  # when the result was known; used for as-of ordering
    match_date: str
    home: str
    away: str
    home_score: int
    away_score: int

    @property
    def outcome(self) -> str:
        if self.home_score > self.away_score:
            return "home"
        return "away" if self.home_score < self.away_score else "draw"


@dataclass
class Elo:
    k: float = 20.0
    home_advantage: float = 60.0
    initial: float = 1500.0
    draw_margin: float = 0.0  # in rating units / 400; 0 means a two-way market
    ratings: dict[str, float] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)

    def diff(self, home: str, away: str) -> float:
        rh = self.ratings.get(home, self.initial)
        ra = self.ratings.get(away, self.initial)
        return (rh + self.home_advantage - ra) / 400

    def probabilities(self, home: str, away: str, *, three_way: bool) -> dict[str, float]:
        d = self.diff(home, away)
        if not three_way:
            p_home = 1 / (1 + 10 ** (-d))
            return {"home": p_home, "away": 1 - p_home}
        c = self.draw_margin
        p_home = 1 / (1 + 10 ** (-d + c))
        p_away = 1 / (1 + 10 ** (d + c))
        return {"home": p_home, "draw": max(0.0, 1 - p_home - p_away), "away": p_away}

    def update(self, match: HistoricalMatch) -> None:
        expected = 1 / (1 + 10 ** (-self.diff(match.home, match.away)))
        actual = {"home": 1.0, "draw": 0.5, "away": 0.0}[match.outcome]
        change = self.k * (actual - expected)
        self.ratings[match.home] = self.ratings.get(match.home, self.initial) + change
        self.ratings[match.away] = self.ratings.get(match.away, self.initial) - change
        for team in (match.home, match.away):
            self.games[team] = self.games.get(team, 0) + 1


def walk_forward(matches: Iterable[HistoricalMatch], model: Elo, *, three_way: bool) -> list[dict]:
    """Out-of-sample forecast for every match, made before its own result is used."""
    out = []
    for match in sorted(matches, key=lambda m: (m.available_at, m.home, m.away)):
        experience = min(model.games.get(match.home, 0), model.games.get(match.away, 0))
        forecast = model.probabilities(match.home, match.away, three_way=three_way)
        out.append({"match": match, "forecast": forecast, "experience": experience})
        model.update(match)
    return out


def fit_draw_margin(predictions: Sequence[dict], grid_step: float = 0.01) -> float:
    """Maximum-likelihood draw margin on a training segment (no access to later data)."""
    best, best_ll = 0.0, float("-inf")
    for i in range(1, int(1 / grid_step)):
        c = i * grid_step
        ll = 0.0
        for item in predictions:
            match: HistoricalMatch = item["match"]
            d = item["d"]
            p_home = 1 / (1 + 10 ** (-d + c))
            p_away = 1 / (1 + 10 ** (d + c))
            p = {"home": p_home, "away": p_away, "draw": 1 - p_home - p_away}[match.outcome]
            ll += log(max(p, 1e-12))
        if ll > best_ll:
            best, best_ll = c, ll
    return best


def rating_differences(matches: Iterable[HistoricalMatch], model: Elo) -> list[dict]:
    """Pre-match rating differences for draw-margin fitting (updates the model)."""
    out = []
    for match in sorted(matches, key=lambda m: (m.available_at, m.home, m.away)):
        out.append({"match": match, "d": model.diff(match.home, match.away)})
        model.update(match)
    return out
