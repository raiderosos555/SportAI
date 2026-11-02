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

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal

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
    built = build_coupons(candidates, rules, cost=sts.cost, on_date=decision_at.date(), stake=stake)
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


def _result(store: Store, event_id: str) -> dict | None:
    rows = store.rows(
        "SELECT * FROM results WHERE event_id=? ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (event_id,),
    )
    return rows[0] if rows else None
