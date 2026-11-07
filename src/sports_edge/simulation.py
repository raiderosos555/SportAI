"""SYMULATOR BUDŻETU and SYMULATOR HISTORYCZNY (TASK 007).

Budget simulator (Monte Carlo): a series of coupons with a fixed stake or a fixed share of
the CURRENT budget — never a stake raised after a loss. Every run draws its own hit
probability around the model value (parameter uncertainty, from the conservative bound),
consecutive coupons may share selections (correlated outcomes) and every win pays the STS
stake coefficient and the payout tax. A probability without predictive validation makes the
whole result a conditional, experimental simulation.

Historical simulator: an offer day of the past replayed AS OF a decision time — only
prices, documents and forecasts the application had before that moment, the coupons the
policy would have built, then settled with the recorded results. No future data, no
invented prices. Results are stored as simulation runs, apart from virtual or real coupons.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.storage import Store

STAKE_TAX = 0.12  # STS: 12% of the stake (coefficient 0.88)
PAYOUT_TAX = 0.10  # on the whole payout above the threshold
PAYOUT_TAX_FROM = 2280.0
MIN_STAKE = 1.0


def net_win(stake: np.ndarray, odds: float) -> np.ndarray:
    """Net profit of a winning coupon (float twin of settlement.settle for STS)."""
    payout = stake * (1 - STAKE_TAX) * odds
    payout = np.where(payout > PAYOUT_TAX_FROM, payout * (1 - PAYOUT_TAX), payout)
    return np.round(payout, 2) - stake


@dataclass(frozen=True)
class BudgetConfig:
    budget: float
    stake_mode: str  # "fixed" (PLN) | "percent" (of the current budget)
    stake: float
    coupons: int
    odds: float
    probability: float  # model probability of one coupon
    probability_low: float | None = None  # conservative bound → parameter uncertainty
    validated: bool = False
    source: str = "ręczne założenie"
    exposure_limit: float | None = None  # largest stake of one coupon (PLN)
    loss_limit: float | None = None  # stop when the loss reaches this amount (PLN)
    shared_exposure: float = 0.0  # 0–1: chance that a coupon repeats the previous outcome
    runs: int = 5000
    seed: int = 7
    paths: int = 20  # sample paths kept for the chart

    def check(self) -> None:
        if self.stake_mode not in {"fixed", "percent"}:
            raise ValueError("Stawka: stała kwota albo procent bieżącego budżetu")
        if self.budget <= 0 or self.stake <= 0 or self.coupons <= 0 or self.runs <= 0:
            raise ValueError("Budżet, stawka, liczba kuponów i przebiegów muszą być dodatnie")
        if self.stake_mode == "percent" and self.stake > 100:
            raise ValueError("Procent budżetu nie może przekraczać 100")
        if not 0 < self.probability < 1 or self.odds <= 1:
            raise ValueError("Szansa musi być w (0, 1), a kurs > 1")
        if not 0 <= self.shared_exposure < 1:
            raise ValueError("Wspólna ekspozycja: wartość z przedziału [0, 1)")


@dataclass
class BudgetResult:
    config: BudgetConfig
    status: str  # "warunkowa (eksperymentalna)" | "oparta na zwalidowanym modelu"
    final: dict  # percentiles and mean of the final budget
    p_loss: float
    p_loss_limit: float
    p_ruin: float
    streaks: dict
    drawdown: dict
    histogram: dict
    sample_paths: list[list[float]]
    expected_profit_per_coupon: float
    bands: dict = field(default_factory=dict)  # percentiles of the budget after each coupon
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = asdict(self)
        out["config"] = asdict(self.config)
        return out


def _run_probabilities(cfg: BudgetConfig, rng: np.random.Generator) -> np.ndarray:
    """One hit probability per run: Beta around the model value whose spread follows the
    conservative bound (the bound ≈ 5th percentile); no spread without a bound."""
    if cfg.probability_low is None or cfg.probability_low >= cfg.probability:
        return np.full(cfg.runs, cfg.probability)
    sd = (cfg.probability - cfg.probability_low) / 1.645
    mean = cfg.probability
    k = mean * (1 - mean) / max(sd * sd, 1e-9) - 1
    if k <= 0:
        return np.clip(rng.normal(mean, sd, cfg.runs), 0.001, 0.999)
    return rng.beta(mean * k, (1 - mean) * k, cfg.runs)


def simulate_budget(cfg: BudgetConfig) -> BudgetResult:
    cfg.check()
    rng = np.random.default_rng(cfg.seed)
    p = _run_probabilities(cfg, rng)
    budget = np.full(cfg.runs, float(cfg.budget))
    peak = budget.copy()
    worst = np.zeros(cfg.runs)
    streak = np.zeros(cfg.runs, dtype=int)
    longest = np.zeros(cfg.runs, dtype=int)
    stopped = np.zeros(cfg.runs, dtype=bool)
    hit_limit = np.zeros(cfg.runs, dtype=bool)
    previous = rng.random(cfg.runs) < p
    keep = min(cfg.paths, cfg.runs)
    history = np.zeros((cfg.runs, cfg.coupons + 1))
    history[:, 0] = cfg.budget
    for k in range(cfg.coupons):
        if cfg.stake_mode == "fixed":
            stake = np.full(cfg.runs, float(cfg.stake))
        else:  # a share of the CURRENT budget: smaller after losses, never chasing them
            stake = np.round(budget * cfg.stake / 100, 2)
        if cfg.exposure_limit:
            stake = np.minimum(stake, cfg.exposure_limit)
        stake = np.minimum(stake, budget)
        active = ~stopped & (stake >= MIN_STAKE)
        fresh = rng.random(cfg.runs) < p
        if cfg.shared_exposure and k:
            repeat = rng.random(cfg.runs) < cfg.shared_exposure
            win = np.where(repeat, previous, fresh)
        else:
            win = fresh
        previous = win
        change = np.where(win, net_win(stake, cfg.odds), -stake)
        budget = np.where(active, np.round(budget + change, 2), budget)
        lost = active & ~win
        streak = np.where(lost, streak + 1, np.where(active, 0, streak))
        longest = np.maximum(longest, streak)
        peak = np.maximum(peak, budget)
        worst = np.maximum(worst, peak - budget)
        if cfg.loss_limit:
            reached = active & (cfg.budget - budget >= cfg.loss_limit)
            hit_limit |= reached
            stopped |= reached
        stopped |= budget < MIN_STAKE
        history[:, k + 1] = budget
    paths = history[:keep]
    levels = (5, 25, 50, 75, 95)
    percentiles = np.percentile(history, levels, axis=0)
    final = budget
    edges = np.linspace(final.min(), final.max() + 1e-9, 21)
    counts, edges = np.histogram(final, bins=edges)
    win_net = float(
        net_win(
            np.array(
                [float(cfg.stake if cfg.stake_mode == "fixed" else cfg.budget * cfg.stake / 100)]
            ),
            cfg.odds,
        )[0]
    )
    stake0 = float(cfg.stake if cfg.stake_mode == "fixed" else cfg.budget * cfg.stake / 100)
    notes = [
        "Stawka stała lub procent bieżącego budżetu — bez podnoszenia stawki po przegranej.",
        f"Koszty STS: współczynnik stawki {1 - STAKE_TAX:.2f}, podatek {PAYOUT_TAX:.0%} od "
        f"wygranej powyżej {PAYOUT_TAX_FROM:.0f} zł.",
        "Szansa kuponu losowana w każdym przebiegu wokół wartości modelu (niepewność parametru)."
        if cfg.probability_low is not None
        else "Brak dolnej granicy szansy — symulacja bez niepewności parametru.",
    ]
    if cfg.shared_exposure:
        notes.append(
            f"Wspólne selekcje kolejnych kuponów: {cfg.shared_exposure:.0%} szansy na powtórzenie "
            "wyniku poprzedniego kuponu (skorelowane serie)."
        )
    if not cfg.validated:
        notes.append(
            "Szansa nie pochodzi ze zwalidowanego modelu — wynik jest symulacją warunkową "
            "(eksperymentalną), nie prognozą."
        )
    return BudgetResult(
        config=cfg,
        status="oparta na zwalidowanym modelu" if cfg.validated else "warunkowa (eksperymentalna)",
        final={
            "p5": round(float(np.percentile(final, 5)), 2),
            "p50": round(float(np.percentile(final, 50)), 2),
            "p95": round(float(np.percentile(final, 95)), 2),
            "mean": round(float(final.mean()), 2),
        },
        p_loss=round(float((final < cfg.budget).mean()), 4),
        p_loss_limit=round(float(hit_limit.mean()), 4),
        p_ruin=round(float((final < MIN_STAKE).mean()), 4),
        streaks={
            "median": int(np.median(longest)),
            "p95": int(np.percentile(longest, 95)),
            "max": int(longest.max()),
        },
        drawdown={
            "median": round(float(np.median(worst)), 2),
            "p95": round(float(np.percentile(worst, 95)), 2),
            "max": round(float(worst.max()), 2),
        },
        histogram={"edges": [round(float(e), 2) for e in edges], "counts": counts.tolist()},
        sample_paths=[[round(float(v), 2) for v in row] for row in paths],
        expected_profit_per_coupon=round(
            cfg.probability * win_net - (1 - cfg.probability) * stake0, 4
        ),
        bands={
            f"p{level}": [round(float(v), 2) for v in row]
            for level, row in zip(levels, percentiles, strict=True)
        },
        notes=notes,
    )


def save_run(
    store: Store,
    kind: str,
    config: dict,
    result: dict,
    *,
    now: datetime,
    status: str,
    policy_version: str | None = None,
) -> str:
    run_id = digest(["simulation", kind, canonical(config), iso(now)])
    store.insert(
        "simulation_runs",
        id=run_id,
        kind=kind,
        created_at=iso(now),
        policy_version=policy_version,
        config_json=canonical(config),
        result_json=canonical(result),
        status=status,
    )
    return run_id


# ---------------------------------------------------------------- historical simulator


def replay_day(
    store: Store,
    paths,
    settings,
    day: str,
    *,
    decision_at: datetime,
    stake: Decimal,
) -> dict:
    """The coupons the policy would have proposed for ``day`` at ``decision_at`` (only what
    was known then), settled with the results recorded since. Read-only: nothing is saved
    as a coupon."""
    from sports_edge.analysis.coupon_builder import build_coupons
    from sports_edge.bookmakers import load_profiles
    from sports_edge.paper import resolve
    from sports_edge.services import analyse_day, probability_policy
    from sports_edge.settlement import Outcome, settle

    rules = probability_policy(paths)
    sts = load_profiles(paths.config)["sts"]
    _report, candidates = analyse_day(
        store, paths, settings, day, now=decision_at, as_of=decision_at
    )
    built = build_coupons(
        candidates,
        rules,
        cost=sts.cost,
        on_date=decision_at.date(),
        stake=stake,
        # TASK 010: the same diversity setting as the analysis (None = the policy's value)
        max_profiles_per_selection=getattr(settings, "max_profiles_per_selection", None),
    )
    out = {
        "day": day,
        "decision_at": iso(decision_at),
        "policy_version": rules.version,
        "candidates": len(candidates),
        "with_forecast": sum(c.p_model is not None for c in candidates),
        "profiles": {},
    }
    for name, proposal in built.proposals.items():
        if proposal is None:
            out["profiles"][name] = {
                "label": rules.profiles[name]["label"],
                "coupon": None,
                "refusal": built.refusals.get(name),
            }
            continue
        legs, outcomes = [], []
        for leg in proposal.legs:
            market = {
                "market_key": leg.market_key,
                "period": leg.period,
                "line": leg.line,
                "overtime": "false",
            }
            result = _result(store, leg.event_id)
            outcome = resolve(market, leg.selection, result) if result else None
            outcomes.append(outcome)
            legs.append(
                {
                    "event": leg.event,
                    "pick": f"{leg.market_label} · {leg.selection}",
                    "odds": str(leg.odds),
                    "p_model": leg.p_model,
                    "p_conservative": leg.p_low,
                    "outcome": outcome.value if outcome else "nierozliczony",
                }
            )
        if any(o is None for o in outcomes):
            status, net = "nierozliczony", None
        elif all(o == Outcome.WIN for o in outcomes):
            status = "trafiony"
            net = str(
                settle(
                    stake, proposal.total_odds, Outcome.WIN, sts.cost, on_date=decision_at.date()
                ).net_profit
            )
        else:
            status, net = "nietrafiony", str(-stake)
        out["profiles"][name] = {
            "label": proposal.label,
            "coupon": {
                "legs": legs,
                "total_odds": str(proposal.total_odds),
                "p_calibrated": proposal.p_calibrated,
                "p_conservative": proposal.p_conservative,
                "level": proposal.level,
                "status": status,
                "net_profit": net,
            },
        }
    return out


def diversity_replay(
    store: Store,
    paths,
    settings,
    days: list[str],
    *,
    decision_time: time = time(10, 0),
    limits: tuple[int, ...] = (0, 1, 2),
    stake: Decimal = Decimal("10.00"),
) -> dict:
    """TASK 010 §4: the as-of replay of ``days`` with the diversity limit off (0), 1 and 2.

    One analysis per day — only what was known at ``decision_time`` (Warsaw) — then the
    builder once per limit, nothing saved. Measured: the mean conservative chance of the
    coupons, days with at least one coupon, days where ONE lost selection loses 2+ coupons
    (and, wider, days where one match is in 2+ coupons with any pick), and how many coupons
    the limit removed or changed against the first limit. A measurement, not a recommendation:
    the default of the policy stays off."""
    from sports_edge.analysis.coupon_builder import build_coupons
    from sports_edge.bookmakers import load_profiles
    from sports_edge.services import analyse_day, probability_policy

    warsaw = ZoneInfo("Europe/Warsaw")
    rules = probability_policy(paths)
    sts = load_profiles(paths.config)["sts"]
    totals = {
        limit: {
            "coupons": 0,
            "days_with_coupon": 0,
            "days_one_loss_2plus": 0,
            "days_shared_match": 0,
            "removed": 0,
            "changed": 0,
            "profiles": Counter(),
        }
        for limit in limits
    }
    chances: dict = {limit: defaultdict(list) for limit in limits}
    per_day = []
    for day in days:
        decision = datetime.combine(date.fromisoformat(day), decision_time, tzinfo=warsaw)
        _report, candidates = analyse_day(store, paths, settings, day, now=decision, as_of=decision)
        reference = None
        row = {"day": day, "candidates": len(candidates), "coupons": {}}
        for limit in limits:
            built = build_coupons(
                candidates,
                rules,
                cost=sts.cost,
                on_date=decision.date(),
                stake=stake,
                max_profiles_per_selection=limit,
            )
            made = {name: p for name, p in built.proposals.items() if p is not None}
            reference = made if reference is None else reference
            picks = Counter(leg.selection_id for p in made.values() for leg in p.legs)
            matches = Counter(e for p in made.values() for e in {leg.event_id for leg in p.legs})
            found = totals[limit]
            found["coupons"] += len(made)
            found["days_with_coupon"] += bool(made)
            found["days_one_loss_2plus"] += any(n >= 2 for n in picks.values())
            found["days_shared_match"] += any(n >= 2 for n in matches.values())
            found["removed"] += sum(name not in made for name in reference)
            found["changed"] += sum(
                name in reference
                and [x.selection_id for x in p.legs]
                != [x.selection_id for x in reference[name].legs]
                for name, p in made.items()
            )
            found["profiles"].update(made.keys())
            for name, p in made.items():
                chances[limit][name].append(p.p_conservative)
            row["coupons"][limit] = {
                name: {
                    "legs": len(p.legs),
                    "odds": str(p.total_odds),
                    "p_conservative": round(p.p_conservative, 4),
                }
                for name, p in made.items()
            }
        per_day.append(row)

    def mean(values):
        return round(sum(values) / len(values), 4) if values else None

    summary = {}
    for limit, found in totals.items():
        every = [v for values in chances[limit].values() for v in values]
        summary[limit] = {
            **found,
            "profiles": dict(found["profiles"]),
            "mean_p_conservative": mean(every),
            "mean_p_conservative_by_profile": {k: mean(v) for k, v in chances[limit].items()},
            "days": len(days),
        }
    return {
        "decision_time": decision_time.strftime("%H:%M"),
        "policy_version": rules.version,
        "limits": summary,
        "per_day": per_day,
    }


def _result(store: Store, event_id: str) -> dict | None:
    rows = store.rows(
        "SELECT * FROM results WHERE event_id=? ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (event_id,),
    )
    return rows[0] if rows else None


# ---------------------------------------------------------------- TASK 013 §5: WYSOKA TRAFNOŚĆ

HIGH_HIT = "high_hit"
P_LOW_BINS = (0.0, 0.60, 0.65, 0.70, 0.75, 1.0001)
N_BUCKETS = ((0, 100), (100, 300), (300, 1000), (1000, 10**9))


def _bin_label(value: float, edges: tuple[float, ...]) -> str:
    for low, high in zip(edges, edges[1:], strict=False):
        if low <= value < high:
            return f"{low:.2f}–{min(high, 1.0):.2f}"
    return "?"


def _bucket(n: int) -> str:
    for low, high in N_BUCKETS:
        if low <= n < high:
            return f"{low}–{high}" if high < 10**9 else f"≥ {low}"
    return "?"


def calibration_sample(row: dict, market_key: str, selection: str, line) -> tuple[str, int | None]:
    """What the conservative bound of a selection rests on: (label, n of the calibration
    sample). Elo: the calibration-window reliability bin of the probability (double chance:
    the bound is the SUM of the bounds of two 1X2 sides — the smaller bin); Dixon–Coles: the
    isotonic block of the raw probability (double chance: the complement of ONE 1X2 side)."""
    from sports_edge.analysis.edge_engine import DERIVED_FROM_1X2
    from sports_edge.models import calibration
    from sports_edge.probability_lab import OPPOSITE, family_of
    from sports_edge.sportsdata import elo_parameters

    probs = row["probabilities"]
    if row["family"] == "elo_ordered_draw":
        validation = row["validation"]
        bins = elo_parameters(validation)[1] if "hyperparameters" in validation else []

        def size(p: float) -> int | None:
            return next((b["n"] for b in bins if b["low"] <= p < b["high"]), None)

        if market_key == "double_chance":
            found = [size(probs[k]) for k in DERIVED_FROM_1X2["double_chance"][selection]]
            known = [n for n in found if n]
            label = "Elo podwójna szansa (suma przedziałów 2 wyników 1X2)"
            return label, min(known, default=None)
        return "Elo 1X2", size(probs.get(selection, 0.0))
    maps = row["validation"].get("calibrators") or {}
    raw = row["uncertainty"].get("raw") or {}
    if market_key == "double_chance":
        blocks, value = maps.get("1x2"), raw.get(OPPOSITE[selection])
        label = "Dixon–Coles podwójna szansa (dopełnienie 1 wyniku 1X2)"
    else:
        family = family_of(market_key, float(line) if line not in (None, "") else None)
        blocks = maps.get(family) if family else None
        first = next(iter(raw), None)
        value = raw.get(selection) if market_key == "1x2" else raw.get(first)
        label = f"Dixon–Coles {family or market_key}"
    if not blocks or value is None:
        return label, None
    return label, int(calibration.isotonic_apply(blocks, value)[1])


def _complement_low(row: dict, selection: str) -> float | None:
    """Elo double chance with the Dixon–Coles definition: 1 − upper bound of the excluded
    1X2 outcome (one interval instead of the sum of two) — a what-if, never served."""
    from sports_edge.probability_lab import OPPOSITE

    bounds = (row["uncertainty"].get("interval") or {}).get(OPPOSITE.get(selection))
    return None if not bounds else max(0.0, 1.0 - float(bounds[1]))


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def high_hit_diagnosis(
    store: Store,
    paths,
    settings,
    days: list[str],
    *,
    hours: tuple[int, ...] = (10, 14, 18),
    until: datetime | None = None,
    versions_at=None,
    goals: dict[str, dict] | None = None,
    profile_name: str = HIGH_HIT,
    stake: Decimal = Decimal("10.00"),
) -> dict:
    """TASK 013 §5.1: why WYSOKA TRAFNOŚĆ makes (almost) no coupon — a read-only as-of replay.

    For every day and decision hour (Warsaw; none after ``until``): the offer and the forecasts
    known then (``versions_at(t)``: the versions whose forecasts count — default: the versions
    SERVED at t), the builder run once with the unchanged policy, and measured: the binding
    refusal code of the profile; the conservative chance of the selections priced inside the
    profile's odds range (eligible ones: fresh price, before the start) by status; the gap
    model chance − conservative chance per kind of bound and size of its calibration sample;
    and a what-if of another definition of the conservative chance (Elo double chance from
    one 1X2 interval, like Dixon–Coles). Nothing is saved; thresholds never change."""
    from sports_edge import insights, learning
    from sports_edge.analysis.coupon_builder import (
        VALIDATED,
        build_coupons,
        eligibility,
        in_odds_range,
    )
    from sports_edge.bookmakers import load_profiles
    from sports_edge.models.registry import model_overview
    from sports_edge.services import day_candidates_at, probability_policy

    warsaw = ZoneInfo("Europe/Warsaw")
    rules = probability_policy(paths)
    sts = load_profiles(paths.config)["sts"]
    profile = rules.profiles[profile_name]
    floor = float(profile["min_leg_probability"])
    max_odds = float(rules.builder["max_odds_per_leg"])
    served = learning.served_check(store)
    every = [r["version_id"] for r in model_overview(store)]
    limit = getattr(settings, "max_profiles_per_selection", None)
    codes: Counter = Counter()
    counts = {s: Counter() for s in ("validated", "experimental")}
    unique_counts = {s: Counter() for s in ("validated", "experimental")}
    counted: set[tuple] = set()
    excluded: Counter = Counter()
    seen: dict[tuple, dict] = {}
    per_decision, coupons = [], []
    what_if = {"validated_ge_floor": 0, "decisions_with_coupon": 0, "p_conservative": []}
    for day in days:
        for hour in hours:
            moment = datetime.combine(date.fromisoformat(day), time(hour), tzinfo=warsaw)
            if until is not None and moment > until:
                continue
            versions = (
                versions_at(moment) if versions_at else {v for v in every if served(v, moment)}
            )
            candidates, predictions = day_candidates_at(
                store,
                paths,
                settings,
                day,
                now=moment,
                as_of=moment,
                versions=versions,
                goals=goals,
            )
            rows = {row["id"]: row for row in predictions.values()}
            built = build_coupons(
                candidates,
                rules,
                cost=sts.cost,
                on_date=moment.date(),
                stake=stake,
                max_profiles_per_selection=limit,
            )
            proposal = built.proposals.get(profile_name)
            code = "coupon" if proposal is not None else built.refusal_codes.get(profile_name, "?")
            codes[code] += 1
            ranged = [c for c in candidates if c.p_low is not None and in_odds_range(c, profile)]
            eligible = [c for c in ranged if eligibility(c, max_odds) is None]
            reasons = Counter(eligibility(c, max_odds) for c in ranged)
            reasons.pop(None, None)
            excluded.update(reasons)
            mine = {s: Counter() for s in counts}
            for c in eligible:
                status = "validated" if c.predictive_status == VALIDATED else "experimental"
                band = _bin_label(c.p_low, P_LOW_BINS)
                counts[status][band] += 1
                mine[status][band] += 1
                pick = (c.prediction_id, c.selection)
                if pick not in counted:
                    counted.add(pick)
                    unique_counts[status][band] += 1
                row = rows.get(c.prediction_id)
                if row is not None and pick not in seen:
                    label, n = calibration_sample(row, c.market_key, c.selection, c.line)
                    seen[pick] = {"label": label, "n": n, "gap": c.p_model - c.p_low}
            # what-if: Elo double chance bounded by ONE 1X2 interval (never served)
            changed = []
            for c in candidates:
                row = rows.get(c.prediction_id)
                if (
                    row is not None
                    and row["family"] == "elo_ordered_draw"
                    and c.market_key == "double_chance"
                    and c.p_low is not None
                ):
                    low = _complement_low(row, c.selection)
                    if low is not None and low <= c.p_model:
                        c = replace(c, p_low=max(c.p_low, low))
                changed.append(c)
            what_if["validated_ge_floor"] += sum(
                1
                for c in changed
                if c.p_low is not None
                and c.p_low >= floor
                and c.predictive_status == VALIDATED
                and in_odds_range(c, profile)
                and eligibility(c, max_odds) is None
            )
            other = build_coupons(
                changed,
                rules,
                cost=sts.cost,
                on_date=moment.date(),
                stake=stake,
                max_profiles_per_selection=limit,
            ).proposals.get(profile_name)
            if other is not None:
                what_if["decisions_with_coupon"] += 1
                what_if["p_conservative"].append(round(other.p_conservative, 4))
            if proposal is not None:
                coupons.append(
                    {
                        "decision_at": iso(moment),
                        "legs": len(proposal.legs),
                        "total_odds": str(proposal.total_odds),
                        "p_conservative": round(proposal.p_conservative, 4),
                        "p_calibrated": round(proposal.p_calibrated, 4),
                        "level": proposal.level,
                    }
                )
            per_decision.append(
                {
                    "decision_at": iso(moment),
                    "code": code,
                    "refusal": built.refusals.get(profile_name),
                    "candidates": len(candidates),
                    "in_range": len(ranged),
                    "in_range_eligible": len(eligible),
                    "in_range_excluded": dict(reasons),
                    "eligible_by_status": {
                        s: dict(sorted(v.items())) for s, v in mine.items() if v
                    },
                    "closest": insights.closest_text(candidates, profile, max_odds=max_odds),
                }
            )
    gaps: dict[str, dict] = {}
    for item in seen.values():
        entry = gaps.setdefault(item["label"], {"all": [], "by_n": defaultdict(list)})
        entry["all"].append(item["gap"])
        if item["n"] is not None:
            entry["by_n"][_bucket(item["n"])].append(item["gap"])
    return {
        "profile": profile["label"],
        "policy_version": rules.version,
        "decisions": len(per_decision),
        "hours": list(hours),
        "codes": dict(codes),
        "coupons": coupons,
        "in_range_eligible": {s: dict(sorted(v.items())) for s, v in counts.items()},
        "in_range_excluded": dict(excluded.most_common()),
        "in_range_eligible_unique": {s: dict(sorted(v.items())) for s, v in unique_counts.items()},
        "gap": {
            label: {
                "selections": len(entry["all"]),
                "median_points": round(100 * _median(entry["all"]), 1),
                "by_calibration_n": {
                    bucket: {"selections": len(v), "median_points": round(100 * _median(v), 1)}
                    for bucket, v in sorted(entry["by_n"].items())
                },
            }
            for label, entry in sorted(gaps.items())
        },
        "what_if_elo_double_chance_one_interval": {
            **what_if,
            "note": "pomiar: podwójna szansa Elo z dolną granicą 1 − górna granica wykluczonego "
            "wyniku (jak Dixon–Coles) zamiast sumy dolnych granic dwóch wyników; nie wdrożone",
        },
        "per_decision": per_decision,
    }
