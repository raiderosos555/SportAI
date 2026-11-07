"""Protocol v3 (TASK 012): ROLLING windows — a retraining can improve the model.

Protocol v2 (TASK 007) has fixed window dates (selection 2023/24, holdout from 2024-07-01), so
a retraining on new matches reproduces the same hyperparameters and calibration maps: every
challenger was identical to its champion (TASK 011 §1.4). Protocol v3 counts the windows back
from the training date T, with the data known at T only:

* fit / warm-up     everything before T − (calibration + selection) months (team strengths,
                    as-of; Elo ratings and the Dixon–Coles fits keep using every earlier result)
* calibration       [T − 24 mies., T − 12 mies.)  isotonic calibration maps (Dixon–Coles) and
                    the Elo draw margin with the interval widths
* selection         [T − 12 mies., T)  hyperparameters (Elo k and home advantage, Dixon–Coles
                    half-life) chosen on CALIBRATED forecasts; predictive statuses, league gates
                    and the 1X2 model are computed here as INFORMATION only

The lengths are in ``config/protocol_v3.toml``; a scope with a shorter history falls back to
half seasons (6 + 6 months). A match belongs to a window only when its result was AVAILABLE
(``available_at``) before the end of that window, so no window contains a result that was
known only later. Inside the windows the as-of rule of v2 holds: forecasts of day D use
results of days < D only; the Dixon–Coles fits are weekly on one fixed grid (``REFIT_ANCHOR``)
and use only matches before the fit date.

DECISION GATE. v3 has no untouched historical holdout any more: the windows move with T and
the most recent 12 months choose the hyperparameters. The gate of an activation is the
prospective comparison with the champion in SHADOW MODE (TASK 011: at least ``shadow_matches``
common settled matches); the selection window result is information only. A v3 version is
therefore always registered as a CHALLENGER — the champion (v2) stays served until the user
decides; nothing here activates a model.

WALK-FORWARD (``walk_forward``): the proof without waiting. A v3 retraining at the first day
of every month M (data available before M only) forecasts the matches of M; the frozen v2
champion (its stored hyperparameters and calibration maps) forecasts the SAME matches with the
same as-of team strengths. Log loss, Brier and the match-level reliability of the coupon zone
are compared per scope and market, with a bootstrap interval over matches. No profit claim.
"""

import bisect
import calendar
import json
import math
import tomllib
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sports_edge.analysis.policy import Policy
from sports_edge.ingestion.types import iso
from sports_edge.models import calibration
from sports_edge.models.elo import Elo, fit_draw_margin, rating_differences
from sports_edge.models.goals import GoalsMatch, fit, markets
from sports_edge.models.registry import register_version
from sports_edge.probability_lab import (
    BINARY_FAMILIES,
    FAMILY,
    HALF_LIVES,
    INTERVAL_METHOD,
    LINES,
    REFIT_DAYS,
    WINDOW_DAYS,
    ProbabilityPolicy,
    _binary_metrics,
    _elo_report,
    _family_report,
    _items,
    _league_gates,
    _league_matches,
    _multiclass,
    _outcome,
    _summary,
    _truth,
    predictive_status,
    served_view,
)
from sports_edge.provenance import code_state, data_snapshot
from sports_edge.results_engine import effective_results, priority
from sports_edge.sportsdata import (
    GRID,
    _climatology,
    _metrics,
    _scoped,
    _to_match,
    elo_parameters,
    history,
    market_benchmark,
)
from sports_edge.storage import Store

POLICY_FILE = "protocol_v3.toml"
PROTOCOL = "v3"
# Protocols whose final decisions are not taken on a used holdout (v1 statuses stay suspended).
DECISION_FREE = ("v2", "v3")
ELO_FAMILY = "elo_ordered_draw"
# Weekly Dixon–Coles refits on ONE grid for every training date (a Monday): the fit of a week
# does not depend on T, so retrainings at different dates share it.
REFIT_ANCHOR = "2000-01-03"
WINDOW_NAMES = ("fit", "calibration", "selection")
CALIBRATED_FAMILIES = tuple(f for f in ("1x2", *BINARY_FAMILIES) if f != "double_chance")
GATE_NOTE = (
    "Protokół v3: brak nietkniętego historycznego holdoutu — okna przesuwają się z datą "
    "treningu, a ostatnie 12 miesięcy wybiera hiperparametry. Bramką aktywacji jest porównanie "
    "prospektywne z aktywną wersją w trybie cienia (co najmniej {n} wspólnych rozliczonych "
    "meczów); wynik okna selekcji to tylko informacja. Aktywacja zawsze należy do użytkownika."
)
ORDERING_RULE = (
    "okna v3 liczone wstecz od daty treningu; mecz należy do okna tylko, gdy jego wynik był "
    "dostępny (available_at) przed końcem okna; prognozy dnia D wyłącznie z wyników dni < D"
)


def decision_free(report: dict | None) -> bool:
    """Protocol v2 or v3: statuses and the served model are not chosen on a used holdout."""
    return (report or {}).get("protocol") in DECISION_FREE


# ---------------------------------------------------------------- policy and windows


# TASK 013 §3: how the Dixon–Coles half-life is chosen at a retraining. The calibration maps
# always come from the calibration window of T (the half-life rule only changes which window
# scores a half-life and whether a change needs proof).
HALF_LIFE_RULES = {
    "select12": "wybór na oknie selekcji 12 mies. (TASK 012)",
    "select24": "wybór na 24 mies.: dwa kolejne okna 12 mies., każde oceniane z kalibratorami "
    "z 12 mies. przed nim; kalibratory modelu z okna kalibracji 12 mies.",
    "sticky12": "kara za zmianę (okno 12 mies.): inna wartość niż poprzedni wybór tylko, gdy "
    "górna granica 95% (bootstrap po meczach) różnicy log loss < 0",
    "sticky24": "kara za zmianę na 24 mies. (jak select24): inna wartość niż poprzedni wybór "
    "tylko, gdy górna granica 95% (bootstrap po meczach) różnicy log loss < 0",
    "fixed": "stały okres połowicznego zaniku championa v2; ruchome tylko kalibratory",
}
STICKY_RULES = ("sticky12", "sticky24")
LONG_RULES = ("select24", "sticky24")


@dataclass(frozen=True)
class RollingPolicy:
    version: str = "2026-11-06.v3a"
    calibration_months: int = 12
    selection_months: int = 12
    short_calibration_months: int = 6
    short_selection_months: int = 6
    min_window_matches: int = 300
    min_fit_matches: int = 300
    shadow_matches: int = 300
    first_month: str = "2024-07"
    resamples: int = 1000
    seed: int = 7
    # TASK 013: the half-life rule of Dixon–Coles (HALF_LIFE_RULES) and its parameters
    half_life_rule: str = "select12"
    half_life_months: int = 24
    change_resamples: int = 1000
    change_seed: int = 7
    # TASK 013 §3: window lengths of one scope ([windows.scopes.<scope>]), e.g. a 24-month
    # calibration window for the 5 top leagues
    scope_windows: dict = field(default_factory=dict, compare=False)

    def for_scope(self, scope: str | None) -> "RollingPolicy":
        """The policy of one scope: its own window lengths, the rest unchanged."""
        found = self.scope_windows.get(scope or "") or {}
        return replace(self, **found) if found else self

    def rule_info(self, rule: str | None = None) -> dict:
        rule = rule or self.half_life_rule
        return {
            "rule": rule,
            "label": HALF_LIFE_RULES[rule],
            "months": self.half_life_months if rule in LONG_RULES else None,
            "resamples": self.change_resamples if rule in STICKY_RULES else None,
            "seed": self.change_seed if rule in STICKY_RULES else None,
        }


def load_policy(config: Path | None = None) -> RollingPolicy:
    """``config/protocol_v3.toml``; the defaults above when the file is missing."""
    if config is None:
        from sports_edge.settings import AppPaths, project_root

        config = AppPaths(project_root()).config
    path = Path(config) / POLICY_FILE
    if not path.is_file():
        return RollingPolicy()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    windows, short = data["windows"], data["windows"].get("short", {})
    walk = data.get("walk_forward", {})
    goals = data.get("dixon_coles", {})
    rule = str(goals.get("half_life_rule", "select12"))
    if rule not in HALF_LIFE_RULES:
        raise ValueError(f"Nieznana reguła okresu połowicznego zaniku: {rule}")
    allowed = ("calibration_months", "selection_months")
    scopes = {
        name: {k: int(v) for k, v in values.items() if k in allowed}
        for name, values in (windows.get("scopes") or {}).items()
    }
    return RollingPolicy(
        scope_windows=scopes,
        half_life_rule=rule,
        half_life_months=int(goals.get("half_life_months", 24)),
        change_resamples=int(goals.get("change_resamples", 1000)),
        change_seed=int(goals.get("change_seed", 7)),
        version=data["policy"]["version"],
        calibration_months=int(windows["calibration_months"]),
        selection_months=int(windows["selection_months"]),
        short_calibration_months=int(short.get("calibration_months", 6)),
        short_selection_months=int(short.get("selection_months", 6)),
        min_window_matches=int(windows["min_window_matches"]),
        min_fit_matches=int(windows["min_fit_matches"]),
        shadow_matches=int(data.get("decision", {}).get("shadow_matches", 300)),
        first_month=str(walk.get("first_month", "2024-07")),
        resamples=int(walk.get("resamples", 1000)),
        seed=int(walk.get("seed", 7)),
    )


def months_before(day: date, months: int) -> date:
    """The same day ``months`` calendar months earlier (the month end when it is shorter)."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _midnight(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC)


@dataclass(frozen=True)
class RollingWindows:
    """The windows of one training date ``trained_at`` (T)."""

    trained_at: datetime
    calibration_from: str
    selection_from: str
    calibration_months: int
    selection_months: int
    rule: str
    counts: dict = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_calibration_start", _midnight(self.calibration_from))
        object.__setattr__(self, "_selection_start", _midnight(self.selection_from))

    @property
    def protocol(self) -> str:
        return PROTOCOL

    def window(self, match_date: str, available_at: datetime) -> str | None:
        """fit / calibration / selection — or None when the result became available only
        after the end of the window of its date (it then belongs to no window)."""
        if match_date < self.calibration_from:
            return "fit" if available_at < self._calibration_start else None
        if match_date < self.selection_from:
            return "calibration" if available_at < self._selection_start else None
        return "selection" if available_at <= self.trained_at else None

    def as_dict(self) -> dict:
        return {
            "protocol": PROTOCOL,
            "trained_at": iso(self.trained_at),
            "fit_until": self.calibration_from,
            "calibration_from": self.calibration_from,
            "selection_from": self.selection_from,
            "selection_until": iso(self.trained_at),
            "calibration_months": self.calibration_months,
            "selection_months": self.selection_months,
            "rule": self.rule,
            "counts": dict(self.counts),
            "membership": "mecz w oknie tylko, gdy jego wynik był dostępny przed końcem okna",
        }


def windows_at(
    trained_at: datetime,
    observations: Iterable[tuple[str, datetime]],
    policy: RollingPolicy | None = None,
) -> RollingWindows:
    """Windows of protocol v3 at T from (match date, available at) of a scope's results.

    The standard lengths first; when a window or the warm-up misses its minimum, the half
    season rule (shorter history); when that misses too, a ValueError (v3 not feasible)."""
    policy = policy or load_policy()
    seen = [(d, a) for d, a in observations if a <= trained_at]
    tried = []
    rules = (
        (policy.calibration_months, policy.selection_months, "standard"),
        (policy.short_calibration_months, policy.short_selection_months, "short"),
    )
    for cal_months, sel_months, kind in rules:
        selection_from = months_before(trained_at.date(), sel_months)
        calibration_from = months_before(selection_from, cal_months)
        label = (
            f"{cal_months} + {sel_months} mies."
            if kind == "standard"
            else f"półsezony {cal_months} + {sel_months} mies. (krótsza historia)"
        )
        windows = RollingWindows(
            trained_at,
            calibration_from.isoformat(),
            selection_from.isoformat(),
            cal_months,
            sel_months,
            label,
        )
        counts = Counter(windows.window(d, a) for d, a in seen)
        found = {name: counts.get(name, 0) for name in WINDOW_NAMES}
        tried.append(f"{label}: " + ", ".join(f"{k} {v}" for k, v in found.items()))
        if (
            found["fit"] >= policy.min_fit_matches
            and found["calibration"] >= policy.min_window_matches
            and found["selection"] >= policy.min_window_matches
        ):
            return replace(windows, counts=found)
    raise ValueError("Za mało meczów w oknach protokołu v3: " + "; ".join(tried))


# ---------------------------------------------------------------- Elo


def three_way(d: float, margin: float) -> dict[str, float]:
    """1X2 of a rating difference with the draw margin (identical to ``Elo.probabilities``)."""
    p_home = 1 / (1 + 10 ** (-d + margin))
    p_away = 1 / (1 + 10 ** (d + margin))
    return {"home": p_home, "draw": max(0.0, 1 - p_home - p_away), "away": p_away}


def _parts(items: list[dict], windows: RollingWindows) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {name: [] for name in WINDOW_NAMES}
    for item in items:
        match = item["match"]
        name = windows.window(match.match_date, match.available_at)
        if name:
            out[name].append(item)
    return out


def _priced(items: list[dict], margin: float) -> list[dict]:
    return [
        {"match": i["match"], "forecast": three_way(i["d"], margin), "d": i["d"]} for i in items
    ]


def elo_train(matches, windows: RollingWindows, grid: dict = GRID) -> dict:
    """Every (k, home advantage) of the grid: as-of rating differences, the draw margin fitted
    on the calibration window, 1X2 log loss on the selection window; the lowest wins.
    ``matches`` must be the results known at T (scoped team keys)."""
    candidates, best = [], None
    for k in grid["k"]:
        for hfa in grid["home_advantage"]:
            parts = _parts(rating_differences(matches, Elo(k=k, home_advantage=hfa)), windows)
            if not parts["calibration"] or not parts["selection"]:
                raise ValueError("Protokół v3: puste okno kalibracji albo selekcji")
            margin = fit_draw_margin(parts["calibration"])
            selection = _priced(parts["selection"], margin)
            metrics = _metrics(selection)
            row = {
                "k": k,
                "home_advantage": hfa,
                "draw_margin": margin,
                **{key: v for key, v in metrics.items() if key != "reliability"},
            }
            candidates.append(row)
            if best is None or row["log_loss"] < best[0]["log_loss"]:
                best = (row, parts, selection)
    row, parts, selection = best
    return {
        "best": row,
        "candidates": candidates,
        "fit": parts["fit"],
        "calibration": _priced(parts["calibration"], row["draw_margin"]),
        "selection": selection,
    }


def _season_key(day: str) -> str:
    year, month = int(day[:4]), int(day[5:7])
    start = year if month >= 7 else year - 1
    return f"{start}-{str(start + 1)[2:]}"


def elo_report(
    store: Store,
    matches,
    windows: RollingWindows,
    trained: dict,
    competitions: tuple[str, ...],
    policy: RollingPolicy,
) -> dict:
    best, selection = trained["best"], trained["selection"]
    climate = _climatology([m for m in matches if m.match_date < windows.selection_from])

    def baseline(group):
        known = [i for i in group if i["match"].competition in climate]
        if not known:
            return {"log_loss": None, "brier": None}
        base = [climate[i["match"].competition] for i in known]
        outcomes = [i["match"].outcome for i in known]
        return {
            "log_loss": round(calibration.log_loss(base, outcomes), 5),
            "brier": round(calibration.brier(base, outcomes), 5),
        }

    leagues: dict[str, list] = defaultdict(list)
    for item in selection:
        leagues[item["match"].competition].append(item)
    seasons: dict[str, list] = defaultdict(list)
    for item in trained["calibration"] + selection:
        seasons[_season_key(item["match"].match_date)].append(item)
    decision = _metrics(selection)
    report = {
        "family": ELO_FAMILY,
        "market": "1x2 (czas regulaminowy)",
        "protocol": PROTOCOL,
        "policy_version": policy.version,
        "competitions": list(competitions),
        "split": windows.as_dict(),
        "n": {
            "fit": len(trained["fit"]),
            "calibration": len(trained["calibration"]),
            "selection": len(selection),
        },
        "hyperparameters": {
            "k": best["k"],
            "home_advantage": best["home_advantage"],
            "draw_margin": best["draw_margin"],
            "draw_margin_window": f"kalibracja od {windows.calibration_from}",
        },
        "selection": trained["candidates"],
        # interval widths of prospective forecasts come from the calibration window
        "calibration": _metrics(trained["calibration"]),
        "decision": decision,
        "baseline_climatology": baseline(selection),
        "decision_by_league": {
            league: {k: v for k, v in _metrics(found).items() if k != "reliability"}
            for league, found in sorted(leagues.items())
        },
        "holdout": {},
        "holdout_decision_free": True,
        "decision_gate": GATE_NOTE.format(n=policy.shadow_matches),
        "walk_forward_by_season": {
            season: {k: v for k, v in _metrics(group).items() if k != "reliability"}
            for season, group in sorted(seasons.items())
        },
        "market_benchmark": market_benchmark(store, selection),
        "availability_basis": sorted(
            {
                r["available_at_basis"]
                for r in store.rows(
                    "SELECT DISTINCT available_at_basis FROM historical_results "
                    f"WHERE competition IN ({','.join('?' for _ in competitions)})",
                    competitions,
                )
            }
        ),
        "leakage_checked": True,
        "ordering_rule": ORDERING_RULE,
        "data_snapshot": data_snapshot(m.result_id for m in matches),
        "code_state": code_state(),
        "data_until": iso(max(m.available_at for m in matches)),
    }
    base = report["baseline_climatology"]["log_loss"]
    report["improves_on_baseline"] = base is not None and decision.get("log_loss", 9) < base
    return report


def run_elo_v3(
    store: Store,
    *,
    competitions: tuple[str, ...],
    now: datetime,
    policy: Policy,
    register: bool = True,
    identities=None,
    rolling: RollingPolicy | None = None,
) -> dict:
    """Elo 1X2 of one scope under protocol v3 at T = ``now`` (data available up to now)."""
    rolling = rolling or load_policy()
    matches = _scoped(
        history(
            store, sport="football", until=now, competitions=competitions, identities=identities
        )
    )
    if not matches:
        raise ValueError("Brak danych historycznych dla wybranych rozgrywek")
    windows = windows_at(now, ((m.match_date, m.available_at) for m in matches), rolling)
    report = elo_report(store, matches, windows, elo_train(matches, windows), competitions, rolling)
    if register:
        version_id, status, reasons = register_version(
            store,
            family=ELO_FAMILY,
            sport="football",
            # the model row is shared by every version of the family: the same description
            description="Elo z marginesem remisu, 1X2 czas regulaminowy",
            version=f"elo-1x2-v3-{'-'.join(competitions)}-{now:%Y%m%d%H%M}",
            created_at=now,
            trained_through=max(m.available_at for m in matches),
            training_data={
                "competitions": list(competitions),
                "data_snapshot": report["data_snapshot"],
                "code_state": report["code_state"],
                "hyperparameters": report["hyperparameters"],
                "split": report["split"],
            },
            validation=report,
            policy=policy,
        )
        report.update(version_id=version_id, status=status, status_reasons=reasons)
    return report


# ---------------------------------------------------------------- Dixon–Coles


def refit_date(match_date: str) -> str:
    """Date of the weekly as-of fit that forecasts a match (fixed grid, see REFIT_ANCHOR)."""
    start = date.fromisoformat(REFIT_ANCHOR)
    offset = (date.fromisoformat(match_date) - start).days // REFIT_DAYS
    return (start + timedelta(days=offset * REFIT_DAYS)).isoformat()


def prepare(rows: list[dict]) -> dict[str, dict]:
    """Results of every league sorted by date, with the running maximum of availability."""
    out = {}
    for code, mine in _league_matches(rows).items():
        mine = sorted(mine, key=lambda r: (r["match_date"], r["id"]))
        available = [datetime.fromisoformat(r["available_at"]) for r in mine]
        running, top = [], None
        for value in available:
            top = value if top is None or value > top else top
            running.append(top)
        out[code] = {
            "rows": mine,
            "dates": [r["match_date"] for r in mine],
            "goals": [
                GoalsMatch(r["match_date"], r["home"], r["away"], r["home_score"], r["away_score"])
                for r in mine
            ],
            "available": available,
            "running_max": running,
        }
    return out


def _league_fit(league: dict, code: str, fit_date: str, half_life: float, cutoff, cache: dict):
    """(cache key, fit) on the matches before ``fit_date`` that were available at ``cutoff``
    (None: every earlier match). The key names the exact set of matches used."""
    idx = bisect.bisect_left(league["dates"], fit_date)
    if cutoff is None or idx == 0 or league["running_max"][idx - 1] <= cutoff:
        key = (code, fit_date, half_life, idx)
        used = league["goals"][:idx]
    else:
        keep = tuple(i for i in range(idx) if league["available"][i] <= cutoff)
        key = (code, fit_date, half_life, keep)
        used = [league["goals"][i] for i in keep]
    if key not in cache:
        cache[key] = fit(used, fit_date=fit_date, half_life_days=half_life)
    return key, cache[key]


def dc_forecasts(
    prepared: dict[str, dict], *, half_life: float, wanted, cutoff=None, cache: dict | None = None
) -> dict[str, dict]:
    """Markets of every result id in ``wanted`` from its weekly as-of fit."""
    cache = {} if cache is None else cache
    out: dict[str, dict] = {}
    for code, league in prepared.items():
        for row in league["rows"]:
            if row["id"] not in wanted:
                continue
            key, model = _league_fit(
                league, code, refit_date(row["match_date"]), half_life, cutoff, cache
            )
            if model is None:
                continue
            memo = ("markets", row["id"], key)
            if memo not in cache:
                matrix = model.score_matrix(row["home"], row["away"])
                cache[memo] = None if matrix is None else markets(matrix, LINES)
            if cache[memo] is not None:
                out[row["id"]] = cache[memo]
    return out


def _fit_calibrators(rows_by_id: dict, part: dict) -> dict:
    out = {}
    for family in CALIBRATED_FAMILIES:
        pairs = _items(rows_by_id, part, family)
        if not pairs:
            raise ValueError(f"Protokół v3: brak prognoz {family} w oknie kalibracji")
        out[family] = calibration.isotonic_fit(*zip(*pairs, strict=True))
    return out


def _selection_score(rows_by_id: dict, view: dict) -> float:
    return (
        _multiclass(view, rows_by_id)["log_loss"]
        + _binary_metrics(_items(rows_by_id, view, "total_2.5"), 2)["log_loss"]
        + _binary_metrics(_items(rows_by_id, view, "btts"), 2)["log_loss"]
    )


def _members(prepared: dict[str, dict], windows: RollingWindows) -> dict[str, str]:
    member: dict[str, str] = {}
    for league in prepared.values():
        for row, available in zip(league["rows"], league["available"], strict=True):
            name = windows.window(row["match_date"], available)
            if name in ("calibration", "selection"):
                member[row["id"]] = name
    return member


def choice_blocks(windows: RollingWindows, months: int) -> list[RollingWindows]:
    """Blocks of the half-life choice (TASK 013 §3): the selection window of T and, for a
    choice window longer than it, the earlier blocks of the same length — each block's
    selection window is the calibration window of the next one, and it is scored with the
    calibration maps of the window right before it (always out of sample)."""
    out = [windows]
    covered = windows.selection_months
    while covered < months:
        current = out[-1]
        selection_from = current.calibration_from
        calibration_from = months_before(
            date.fromisoformat(selection_from), current.calibration_months
        ).isoformat()
        out.append(
            RollingWindows(
                _midnight(current.selection_from),
                calibration_from,
                selection_from,
                current.calibration_months,
                current.calibration_months,
                "blok wcześniejszy wyboru okresu połowicznego zaniku",
            )
        )
        covered += current.calibration_months
    return out


def match_losses(rows_by_id: dict, view: dict) -> dict[str, float]:
    """Per match: the log losses of the served 1X2, O/U 2.5 and BTTS added up (the criterion
    of the half-life choice, ``_selection_score``, per match)."""
    out = {}
    for result_id, served in view.items():
        row = rows_by_id[result_id]
        h, a = row["home_score"], row["away_score"]
        loss = -math.log(max(served[("1x2", None)][_outcome(h, a)], 1e-12))
        for family in ("total_2.5", "btts"):
            market_key, line, selections = BINARY_FAMILIES[family]
            side = next(s for s in selections if _truth(market_key, line, s, h, a))
            p = min(max(served[(market_key, line)][side], 1e-6), 1 - 1e-6)
            loss -= math.log(p)
        out[result_id] = loss
    return out


def dc_train(
    prepared: dict[str, dict],
    rows_by_id: dict,
    windows: RollingWindows,
    *,
    cutoff=None,
    cache: dict | None = None,
    half_lives: tuple[float, ...] = HALF_LIVES,
    rule: str = "select12",
    previous: float | None = None,
    rolling: RollingPolicy | None = None,
) -> dict:
    """Every half-life: calibration maps on the calibration window, the CALIBRATED forecasts
    scored on the selection window (1X2 + O/U 2.5 + BTTS log loss); the lowest wins.

    TASK 013 ``rule`` (HALF_LIFE_RULES): ``select24``/``sticky24`` score every half-life on
    the earlier blocks too (``choice_blocks``; a block without enough matches is left out, then
    the choice falls back to fewer blocks — said in ``half_life_rule``); ``sticky*`` keep
    ``previous`` (the previous choice) unless the best half-life is better per match with the
    upper bound of a bootstrap 95% interval below 0; ``fixed`` keeps ``previous`` (the v2
    champion's half-life). The calibration maps and the reported windows are always those of
    the 12-month windows of T."""
    from sports_edge import prospective_eval as pe

    rolling = rolling or RollingPolicy()
    if rule not in HALF_LIFE_RULES:
        raise ValueError(f"Nieznana reguła okresu połowicznego zaniku: {rule}")
    if rule == "fixed" and previous is None:
        raise ValueError("Reguła „fixed” wymaga okresu połowicznego zaniku championa v2")
    if rule == "fixed":
        half_lives = (float(previous),)
    elif rule in STICKY_RULES and previous is not None and float(previous) not in half_lives:
        half_lives = tuple(sorted({*half_lives, float(previous)}))
    cache = {} if cache is None else cache
    months = rolling.half_life_months if rule in LONG_RULES else windows.selection_months
    blocks = choice_blocks(windows, months)
    members = [_members(prepared, block) for block in blocks]
    wanted = set().union(*members)
    tuning, results = [], {}
    used_blocks = len(blocks)
    for half_life in half_lives:
        preds = dc_forecasts(
            prepared, half_life=half_life, wanted=wanted, cutoff=cutoff, cache=cache
        )
        parts = []
        for index, member in enumerate(members):
            cal = {k: v for k, v in preds.items() if member.get(k) == "calibration"}
            sel = {k: v for k, v in preds.items() if member.get(k) == "selection"}
            if index == 0:
                if not cal or not sel:
                    raise ValueError(
                        "Protokół v3: brak prognoz Dixon–Coles w oknach kalibracji/selekcji"
                    )
            elif len(cal) < rolling.min_window_matches or len(sel) < rolling.min_window_matches:
                break  # a shorter history: the earlier block is not available
            calibrators = _fit_calibrators(rows_by_id, cal)
            view = {k: served_view(v, calibrators) for k, v in sel.items()}
            parts.append(
                {
                    "calibrators": calibrators,
                    "calibration_raw": cal,
                    "selection_raw": sel,
                    "view": view,
                    "score": _selection_score(rows_by_id, view),
                }
            )
        used_blocks = min(used_blocks, len(parts))
        results[half_life] = parts
    best = None
    for half_life in half_lives:
        parts = results[half_life][:used_blocks]
        n = sum(len(p["view"]) for p in parts)
        score = sum(p["score"] * len(p["view"]) for p in parts) / n
        tuning.append(
            {
                "half_life_days": half_life,
                "selection_score": round(score, 5),
                "calibration_n": len(parts[0]["calibration_raw"]),
                "selection_n": len(parts[0]["selection_raw"]),
                "blocks": len(parts),
                "choice_n": n,
            }
        )
        if best is None or score < best[0]:
            best = (score, half_life)
    chosen = best[1]
    decision: dict = {
        **rolling.rule_info(rule),
        "blocks_used": used_blocks,
        "previous": previous,
        "best": best[1],
    }
    if rule in STICKY_RULES and previous is not None and best[1] != float(previous):
        losses = {}
        for key in (best[1], float(previous)):
            found: dict[str, float] = {}
            for part in results[key][:used_blocks]:
                found.update(match_losses(rows_by_id, part["view"]))
            losses[key] = found
        common = sorted(set(losses[best[1]]) & set(losses[float(previous)]))
        diffs = [losses[best[1]][k] - losses[float(previous)][k] for k in common]
        ci = pe._bootstrap(diffs, resamples=rolling.change_resamples, seed=rolling.change_seed)
        decision.update(
            matches=len(common),
            difference=round(sum(diffs) / len(diffs), 5),
            ci95=ci,
            changed=ci[1] < 0,
        )
        chosen = best[1] if ci[1] < 0 else float(previous)
    elif rule in STICKY_RULES:
        decision["changed"] = previous is not None and best[1] != float(previous)
    decision["chosen"] = chosen
    if used_blocks < len(blocks):
        decision["note"] = (
            f"wcześniejszy blok niedostępny (za mało meczów) — wybór na {used_blocks} z "
            f"{len(blocks)} bloków"
        )
    final = results[chosen][0]
    return {
        "score": final["score"],
        "half_life": chosen,
        "calibrators": final["calibrators"],
        "calibration_raw": final["calibration_raw"],
        "selection_raw": final["selection_raw"],
        "view": final["view"],
        "tuning": tuning,
        "half_life_rule": decision,
    }


def dc_report(
    store: Store,
    rows: list[dict],
    rows_by_id: dict,
    windows: RollingWindows,
    trained: dict,
    *,
    competitions: tuple[str, ...],
    probability_policy: ProbabilityPolicy,
    elo_forecasts: dict[str, dict],
    elo_version_id: str | None,
    policy: RollingPolicy,
) -> dict:
    view, calibrators = trained["view"], trained["calibrators"]
    before = windows.selection_from
    families = _family_report(rows, rows_by_id, trained["selection_raw"], view, before=before)
    rules = probability_policy.validation
    predictive = {family: predictive_status(info, rules) for family, info in families.items()}
    elo_families = _elo_report(rows, rows_by_id, elo_forecasts, set(view), before=before)
    elo_predictive = {
        family: predictive_status(info, rules) for family, info in elo_families.items()
    }
    comparison = {}
    common = sorted(set(elo_forecasts) & set(view))
    if common:
        outcomes = [
            _outcome(rows_by_id[k]["home_score"], rows_by_id[k]["away_score"]) for k in common
        ]
        comparison["selection"] = {
            "n_common": len(common),
            "elo": _summary([elo_forecasts[k] for k in common], outcomes),
            "dixon_coles": _summary([view[k][("1x2", None)] for k in common], outcomes),
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
    report = {
        "family": FAMILY,
        "protocol": PROTOCOL,
        "policy_version": policy.version,
        "competitions": list(competitions),
        "split": windows.as_dict(),
        "half_life_days": trained["half_life"],
        "half_life_rule": trained.get("half_life_rule"),  # TASK 013 §3.4
        "selection": trained["tuning"],
        "refit_days": REFIT_DAYS,
        "refit_anchor": REFIT_ANCHOR,
        "window_days": WINDOW_DAYS,
        "n": {"calibration": len(trained["calibration_raw"]), "selection": len(view)},
        "families": families,
        "predictive": predictive,
        "families_holdout": {},
        "comparison_vs_elo": comparison or None,
        "elo_families": elo_families,
        "elo_predictive": elo_predictive,
        "elo_families_holdout": {},
        "elo_version_id": elo_version_id,
        "calibrators": calibrators,
        "markets_served": served,
        "decision": families["1x2"]["multiclass_calibrated"],
        "holdout": {},
        "holdout_decision_free": True,
        "decision_gate": GATE_NOTE.format(n=policy.shadow_matches),
        "leakage_checked": True,
        "ordering_rule": ORDERING_RULE
        + "; dopasowanie Dixon–Coles co 7 dni (stała siatka) wyłącznie na meczach sprzed daty",
        "data_snapshot": data_snapshot(r["id"] for r in rows),
        "code_state": code_state(),
        "data_until": iso(max(datetime.fromisoformat(r["available_at"]) for r in rows)),
        "providers": sorted({r["provider_resolved"] for r in rows}, key=priority),
        "interval_method": INTERVAL_METHOD,
    }
    report["by_league"] = _league_gates(
        rows,
        None,
        view,
        rows_by_id,
        calibrators,
        elo_forecasts,
        competitions,
        min_n=int(rules["min_holdout_n"]),
        before=before,
        served=True,
    )
    report["by_league_holdout"] = {}
    return report


def run_dc_v3(
    store: Store,
    *,
    competitions: tuple[str, ...],
    now: datetime,
    policy: Policy,
    probability_policy: ProbabilityPolicy,
    elo_forecasts: dict[str, dict] | None = None,
    register: bool = True,
    identities=None,
    elo_version_id: str | None = None,
    rolling: RollingPolicy | None = None,
    previous_half_life: float | None = None,
) -> dict:
    """Dixon–Coles of one scope under protocol v3 at T = ``now``. ``previous_half_life``: the
    previous choice of the scope (the newest v3 version, else the champion) — the rule
    ``sticky*`` keeps it without proof of an improvement and ``fixed`` always keeps it."""
    rolling = rolling or load_policy()
    rows = effective_results(
        store, sport="football", until=now, competitions=competitions, identities=identities
    )
    if not rows:
        raise ValueError("Brak danych historycznych dla wybranych rozgrywek")
    rows_by_id = {r["id"]: r for r in rows}
    windows = windows_at(
        now, ((r["match_date"], datetime.fromisoformat(r["available_at"])) for r in rows), rolling
    )
    trained = dc_train(
        prepare(rows),
        rows_by_id,
        windows,
        rule=rolling.half_life_rule,
        previous=previous_half_life,
        rolling=rolling,
    )
    report = dc_report(
        store,
        rows,
        rows_by_id,
        windows,
        trained,
        competitions=competitions,
        probability_policy=probability_policy,
        elo_forecasts=elo_forecasts or {},
        elo_version_id=elo_version_id,
        policy=rolling,
    )
    if register:
        version_id, status, reasons = register_version(
            store,
            family=FAMILY,
            sport="football",
            description="Dixon–Coles (gole), rynki z macierzy wyników, kalibracja izotoniczna",
            version=f"dc-goals-v3-{'-'.join(competitions)}-{now:%Y%m%d%H%M}",
            created_at=now,
            trained_through=max(datetime.fromisoformat(r["available_at"]) for r in rows),
            training_data={
                "competitions": list(competitions),
                "data_snapshot": report["data_snapshot"],
                "code_state": report["code_state"],
                "half_life_days": report["half_life_days"],
                "half_life_rule": report["half_life_rule"],
                "split": report["split"],
            },
            validation=report,
            policy=policy,
        )
        report.update(version_id=version_id, status=status, status_reasons=reasons)
    return report


# ---------------------------------------------------------------- differences to the champion


def _tuned(report: dict) -> dict:
    """Hyperparameters of a report without the bookkeeping keys of the draw margin."""
    out = {
        k: v
        for k, v in (report.get("hyperparameters") or {}).items()
        if not k.startswith("draw_margin_")
    }
    if "half_life_days" in report:
        out["half_life_days"] = report["half_life_days"]
    return out


def differences(champion: dict | None, challenger: dict | None) -> dict:
    """What a challenger changes against the champion (``validation_json`` of both rows):
    hyperparameters (Elo k, home advantage, draw margin; Dixon–Coles half-life), the
    calibration maps and the windows. ``identical``: nothing changed."""
    if not champion or not challenger:
        return {}
    old = json.loads(champion["validation_json"])
    new = json.loads(challenger["validation_json"])
    before, after = _tuned(old), _tuned(new)
    changed = {
        key: [before.get(key), after.get(key)]
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    }
    maps = {}
    for family in sorted(set(old.get("calibrators") or {}) | set(new.get("calibrators") or {})):
        a = (old.get("calibrators") or {}).get(family)
        b = (new.get("calibrators") or {}).get(family)
        maps[family] = {
            "same": a == b,
            "champion": calibrator_summary(a),
            "challenger": calibrator_summary(b),
        }
    return {
        "protocol": [old.get("protocol", "v1"), new.get("protocol", "v1")],
        "windows": [old.get("split"), new.get("split")],
        "hyperparameters": changed,
        "calibrators": maps,
        "identical": not changed and all(v["same"] for v in maps.values()),
    }


def calibrator_summary(blocks) -> dict:
    """Size of an isotonic map and its value at 0.6, 0.7 and 0.8 (to show what changed)."""
    if not blocks:
        return {"blocks": 0}
    return {
        "blocks": len(blocks),
        "n": sum(int(b.get("n", 0)) for b in blocks),
        **{
            f"p{int(x * 100)}": round(calibration.isotonic_apply(blocks, x)[0], 4)
            for x in (0.6, 0.7, 0.8)
        },
    }


# ---------------------------------------------------------------- walk-forward v3 vs frozen v2


def month_starts(first_month: str, now: datetime) -> list[datetime]:
    year, month = (int(x) for x in first_month.split("-"))
    out = []
    moment = datetime(year, month, 1, tzinfo=UTC)
    while moment < now:
        out.append(moment)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        moment = datetime(year, month, 1, tzinfo=UTC)
    return out


def train_at(
    trained_at: datetime,
    *,
    matches,
    prepared: dict,
    rows_by_id: dict,
    rolling: RollingPolicy,
    cache: dict,
    rules: tuple[str, ...] | None = None,
    previous: dict[str, float] | None = None,
) -> dict:
    """The v3 model of one retraining date: only results available at T are used.

    ``rules`` (TASK 013): the half-life rules to train (default: the policy's one); the
    first is the model's own (``half_life``/``calibrators``), every rule is in ``goals``.
    ``previous``: rule -> the previous choice (sticky rules) or the v2 half-life (fixed)."""
    rules = rules or (rolling.half_life_rule,)
    known = [m for m in matches if m.available_at <= trained_at]
    windows = windows_at(trained_at, ((m.match_date, m.available_at) for m in known), rolling)
    elo = elo_train(known, windows)
    goals = {
        rule: dc_train(
            prepared,
            rows_by_id,
            windows,
            cutoff=trained_at,
            cache=cache,
            rule=rule,
            previous=(previous or {}).get(rule),
            rolling=rolling,
        )
        for rule in rules
    }
    first = goals[rules[0]]
    return {
        "windows": windows,
        "elo": elo["best"],
        "half_life": first["half_life"],
        "calibrators": first["calibrators"],
        "goals": {
            rule: {
                "half_life": found["half_life"],
                "calibrators": found["calibrators"],
                "decision": found["half_life_rule"],
            }
            for rule, found in goals.items()
        },
    }


def _elo_markets(forecast: dict, row: dict) -> list[tuple[str, str | None, dict, dict]]:
    h, a = row["home_score"], row["away_score"]
    one = {sel: _truth("1x2", None, sel, h, a) for sel in forecast}
    double = {
        "home_draw": forecast["home"] + forecast["draw"],
        "draw_away": forecast["draw"] + forecast["away"],
        "home_away": forecast["home"] + forecast["away"],
    }
    return [
        ("1x2", None, forecast, one),
        (
            "double_chance",
            None,
            double,
            {sel: _truth("double_chance", None, sel, h, a) for sel in double},
        ),
    ]


DC_MARKETS = (
    ("1x2", None),
    ("double_chance", None),
    ("total", 1.5),
    ("total", 2.5),
    ("total", 3.5),
    ("btts", None),
)


# TASK 013 §3.2: team totals are served raw (no calibration map) — scored as one group
TEAM_TOTALS = (
    ("team_total_home", 0.5),
    ("team_total_home", 1.5),
    ("team_total_away", 0.5),
    ("team_total_away", 1.5),
)
TEAM_TOTALS_GROUP = "team totals"


def _team_truth(market_key: str, line: float, selection: str, h: int, a: int) -> int:
    goals = h if market_key == "team_total_home" else a
    return int((goals > line) == (selection == "over"))


def _dc_markets(view: dict, row: dict, raw: dict | None = None) -> list[tuple]:
    """Markets of one served forecast with their outcomes; with ``raw`` (the uncalibrated
    markets of the same forecast) also the team totals, which are served raw."""
    h, a = row["home_score"], row["away_score"]
    out = []
    for market_key, line in DC_MARKETS:
        probs = view.get((market_key, line))
        if not probs:
            continue
        text = f"{line:.1f}" if line is not None else None
        out.append((market_key, text, probs, {s: _truth(market_key, line, s, h, a) for s in probs}))
    for market_key, line in TEAM_TOTALS if raw else ():
        probs = raw.get((market_key, line))
        if probs:
            out.append(
                (
                    market_key,
                    f"{line:.1f}",
                    probs,
                    {s: _team_truth(market_key, line, s, h, a) for s in probs},
                )
            )
    return out


def _forecast_rows(variant: str, family: str, scope: str, row: dict, found) -> list[dict]:
    from sports_edge import prospective_eval as pe

    return [
        {
            "role": variant,
            "family": family,
            "scope": scope,
            "match": row["id"],
            "market_key": market_key,
            "line": line,
            "market": pe.market_name(market_key, line),
            "probabilities": dict(probs),
            "outcomes": outcomes,
            "month": row["match_date"][:7],
        }
        for market_key, line, probs, outcomes in found
    ]


def compare(rows: list[dict], *, resamples: int = 1000, seed: int = 7) -> list[dict]:
    """v3 vs v2 on the SAME matches per (family, market) and "*" (all markets of a family
    except double chance): mean log loss and Brier of both, the difference v3 − v2 averaged
    per match and its bootstrap interval over matches."""
    from sports_edge import prospective_eval as pe

    by_key: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        by_key[(row["family"], row["market"], row["match"])][row["role"]] = row
    groups: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for (family, market, match), pair in by_key.items():
        if "v3" not in pair or "v2" not in pair:
            continue
        a = pe.score(pair["v3"]["probabilities"], pair["v3"]["outcomes"], pair["v3"]["market_key"])
        b = pe.score(pair["v2"]["probabilities"], pair["v2"]["outcomes"], pair["v2"]["market_key"])
        if a is None or b is None:
            continue
        if market.startswith("team_total"):  # TASK 013: own group, "*" unchanged
            targets = [market, TEAM_TOTALS_GROUP]
        else:
            targets = [market] + ([pe.POOLED] if market != "double_chance" else [])
        for target in targets:
            groups[(family, target)][match].append((a, b))
    out = []
    for (family, market), per_match in sorted(groups.items()):
        diffs = [sum(x[0][0] - x[1][0] for x in v) / len(v) for v in per_match.values()]
        pairs = [x for v in per_match.values() for x in v]
        n = len(diffs)
        ci = pe._bootstrap(diffs, resamples=resamples, seed=seed) if n else None
        out.append(
            {
                "family": family,
                "market": market,
                "matches": n,
                "forecasts": len(pairs),
                "v3_log_loss": round(sum(p[0][0] for p in pairs) / len(pairs), 5),
                "v2_log_loss": round(sum(p[1][0] for p in pairs) / len(pairs), 5),
                "v3_brier": round(sum(p[0][1] for p in pairs) / len(pairs), 5),
                "v2_brier": round(sum(p[1][1] for p in pairs) / len(pairs), 5),
                "difference": round(sum(diffs) / n, 5),
                "ci95": ci,
                "verdict": "v3 lepszy"
                if ci and ci[1] < 0
                else "v3 gorszy"
                if ci and ci[0] > 0
                else "brak istotnej różnicy",
            }
        )
    return out


def zone_reliability(rows: list[dict], *, zone: float = 0.6) -> list[dict]:
    """Match-level reliability of the coupon zone (TASK 011 method) for both variants on
    the matches that both forecast."""
    from sports_edge import prospective_eval as pe

    policy = replace(pe.EvalPolicy(), zone=zone)
    both: dict[tuple, set] = defaultdict(set)
    for row in rows:
        both[(row["family"], row["market"], row["match"])].add(row["role"])
    common = [r for r in rows if both[(r["family"], r["market"], r["match"])] >= {"v2", "v3"}]
    table = pe.reliability(pe.selections(common, policy), policy)
    return [
        {
            k: r[k]
            for k in ("role", "family", "market", "matches", "mean_p", "freq", "ci95", "flag_label")
        }
        for r in table
        if r["zone_total"] and r["scope"] == "all"
    ]


def range_reliability(
    rows: list[dict], *, low: float = 0.7, high: float = 0.8, markets=("1x2", "double_chance")
) -> list[dict]:
    """TASK 013 §3.3: match-level reliability of one probability range (default 70–80%) of
    both variants on the matches that both forecast — 1X2 and double chance."""
    from sports_edge import prospective_eval as pe

    policy = pe.EvalPolicy()
    both: dict[tuple, set] = defaultdict(set)
    for row in rows:
        both[(row["family"], row["market"], row["match"])].add(row["role"])
    common = [
        r
        for r in rows
        if both[(r["family"], r["market"], r["match"])] >= {"v2", "v3"} and r["market"] in markets
    ]
    table = pe.reliability(pe.selections(common, policy), policy)
    keys = (
        "role",
        "family",
        "market",
        "range",
        "matches",
        "mean_p",
        "freq",
        "ci95",
        "flag",
        "flag_label",
    )
    return [
        {k: r[k] for k in keys}
        for r in table
        if r["scope"] == "all"
        and not r["zone_total"]
        and abs(r["low"] - low) < 1e-9
        and abs(r["high"] - high) < 1e-9
        and r["market"] in markets
    ]


def variant_rows(rows: list[dict], rule_role: str) -> list[dict]:
    """The Dixon–Coles rows of one half-life rule (role ``rule_role``) as "v3" next to v2."""
    return [
        {**r, "role": "v3"} if r["role"] == rule_role else r
        for r in rows
        if r["family"] == FAMILY and r["role"] in (rule_role, "v2")
    ]


def walk_forward(
    store: Store,
    scope: str,
    *,
    now: datetime,
    competitions: tuple[str, ...],
    champion_elo: dict,
    champion_goals: dict,
    identities=None,
    rolling: RollingPolicy | None = None,
    months: list[datetime] | None = None,
    progress=None,
    rules: tuple[str, ...] | None = None,
) -> dict:
    """§2.3: monthly v3 retraining vs the frozen v2 champion on the same matches.

    TASK 013 §3: ``rules`` — the half-life rules measured on the SAME matches (default: the
    policy's rule). The first rule is the "v3" of the comparison; every rule has its own
    Dixon–Coles comparison in ``variants`` (role ``v3:<rule>``). The sticky rules start from
    the v2 champion's half-life and then keep their own previous monthly choice."""
    rolling = rolling or load_policy()
    rules = rules or (rolling.half_life_rule,)
    rows = effective_results(
        store, sport="football", until=now, competitions=competitions, identities=identities
    )
    if not rows:
        raise ValueError("Brak danych historycznych dla wybranych rozgrywek")
    rows_by_id = {r["id"]: r for r in rows}
    matches = _scoped([_to_match(r) for r in rows])
    prepared = prepare(rows)
    cache: dict = {}
    elo_v2 = json.loads(champion_elo["validation_json"])
    v2_params = elo_v2["hyperparameters"]
    v2_margin = elo_parameters(elo_v2)[0]
    goals_v2 = json.loads(champion_goals["validation_json"])
    v2_half_life, v2_calibrators = goals_v2["half_life_days"], goals_v2["calibrators"]
    # as-of rating differences of every match per (k, home advantage) — "days < D" rule
    pairs = {(k, h) for k in GRID["k"] for h in GRID["home_advantage"]}
    pairs.add((v2_params["k"], v2_params["home_advantage"]))
    rating = {
        pair: {
            i["match"].result_id: i["d"]
            for i in rating_differences(matches, Elo(k=pair[0], home_advantage=pair[1]))
        }
        for pair in sorted(pairs)
    }
    starts = months or month_starts(rolling.first_month, now)
    forecast_rows: list[dict] = []
    trained_months = []
    previous = {rule: float(v2_half_life) for rule in rules}  # sticky: the v2 choice first
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else min(now, _next_month(start))
        first, last = start.date().isoformat(), end.date().isoformat()
        month_ids = {r["id"] for r in rows if first <= r["match_date"] < last}
        if not month_ids:
            continue
        if progress:
            progress(f"{scope}: retrening v3 {start:%Y-%m}")
        try:
            model = train_at(
                start,
                matches=matches,
                prepared=prepared,
                rows_by_id=rows_by_id,
                rolling=rolling,
                cache=cache,
                rules=rules,
                previous=previous,
            )
        except ValueError as error:
            trained_months.append({"month": f"{start:%Y-%m}", "skipped": str(error)})
            continue
        for rule, found in model["goals"].items():
            if rule in STICKY_RULES:
                previous[rule] = found["half_life"]
        best = model["elo"]
        d3 = rating[(best["k"], best["home_advantage"])]
        d2 = rating[(v2_params["k"], v2_params["home_advantage"])]
        dc3 = dc_forecasts(prepared, half_life=model["half_life"], wanted=month_ids, cache=cache)
        dc2 = dc_forecasts(prepared, half_life=v2_half_life, wanted=month_ids, cache=cache)
        extra = {
            rule: (
                found,
                dc_forecasts(prepared, half_life=found["half_life"], wanted=month_ids, cache=cache),
            )
            for rule, found in model["goals"].items()
            if rule != rules[0]
        }
        for result_id in sorted(month_ids):
            row = rows_by_id[result_id]
            if result_id in d3 and result_id in d2:
                forecast_rows += _forecast_rows(
                    "v3",
                    ELO_FAMILY,
                    scope,
                    row,
                    _elo_markets(three_way(d3[result_id], best["draw_margin"]), row),
                )
                forecast_rows += _forecast_rows(
                    "v2",
                    ELO_FAMILY,
                    scope,
                    row,
                    _elo_markets(three_way(d2[result_id], v2_margin), row),
                )
            if result_id in dc3 and result_id in dc2:
                forecast_rows += _forecast_rows(
                    "v3",
                    FAMILY,
                    scope,
                    row,
                    _dc_markets(
                        served_view(dc3[result_id], model["calibrators"]), row, dc3[result_id]
                    ),
                )
                forecast_rows += _forecast_rows(
                    "v2",
                    FAMILY,
                    scope,
                    row,
                    _dc_markets(served_view(dc2[result_id], v2_calibrators), row, dc2[result_id]),
                )
                for rule, (found, dcx) in extra.items():
                    if result_id in dcx:
                        forecast_rows += _forecast_rows(
                            f"v3:{rule}",
                            FAMILY,
                            scope,
                            row,
                            _dc_markets(
                                served_view(dcx[result_id], found["calibrators"]),
                                row,
                                dcx[result_id],
                            ),
                        )
        trained_months.append(
            {
                "month": f"{start:%Y-%m}",
                "matches": len(month_ids),
                "rule": model["windows"].rule,
                "calibration_from": model["windows"].calibration_from,
                "selection_from": model["windows"].selection_from,
                "elo": {k: best[k] for k in ("k", "home_advantage", "draw_margin")},
                "half_life_days": model["half_life"],
                "half_life_rules": {
                    rule: {
                        "half_life_days": found["half_life"],
                        "best": found["decision"].get("best"),
                        "changed": found["decision"].get("changed"),
                        "ci95": found["decision"].get("ci95"),
                        "blocks_used": found["decision"].get("blocks_used"),
                    }
                    for rule, found in model["goals"].items()
                },
            }
        )
    primary = [r for r in forecast_rows if r["role"] in ("v3", "v2")]
    variants = {}
    for rule in rules:
        role = "v3" if rule == rules[0] else f"v3:{rule}"
        mine = variant_rows(forecast_rows, role)
        variants[rule] = {
            "label": HALF_LIFE_RULES[rule],
            "half_lives": [
                (m.get("half_life_rules") or {}).get(rule, {}).get("half_life_days")
                for m in trained_months
                if "skipped" not in m
            ],
            "comparison": compare(mine, resamples=rolling.resamples, seed=rolling.seed),
            "range_70_80": range_reliability(mine),
        }
    return {
        "rules": list(rules),
        "variants": variants,
        "range_70_80": range_reliability(primary),
        "scope": scope,
        "protocol": "v3 (retrening co miesiąc) vs v2 (zamrożony champion)",
        "champions": {
            "elo": champion_elo["version"],
            "goals": champion_goals["version"],
            "elo_hyperparameters": {
                "k": v2_params["k"],
                "home_advantage": v2_params["home_advantage"],
                "draw_margin": v2_margin,
            },
            "half_life_days": v2_half_life,
        },
        "months": trained_months,
        "matches": len({r["match"] for r in primary}),
        "comparison": compare(primary, resamples=rolling.resamples, seed=rolling.seed),
        "zone": zone_reliability(primary),
        "leakage": "parametry miesiąca M (hiperparametry, margines remisu, kalibratory) wyłącznie "
        "z wyników dostępnych przed M; siły drużyn as-of (dni < D) — te same dla v3 i v2",
        "rows": forecast_rows,
    }


def _next_month(moment: datetime) -> datetime:
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return datetime(year, month, 1, tzinfo=UTC)
