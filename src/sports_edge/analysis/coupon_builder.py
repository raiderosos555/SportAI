"""Intelligent Coupon Builder (TASK 005): probability-first STS coupons.

For every profile (safe / balanced / aggressive) the builder searches all combinations of
the best eligible selections and keeps the one with the highest CONSERVATIVE probability of
winning the whole coupon that satisfies the profile: number of legs, minimum leg
probability, minimum total odds and net profit on a win after the STS stake coefficient and
payout tax. Fewer legs win ties; a coupon that does not return more than the stake is never
proposed.

Joint probability: legs always come from different matches and the product of marginals is
used under the explicit assumption that different matches are independent. Two markets of
one match are never multiplied (they are strongly dependent); such combinations are refused.

Levels are kept apart: "zwalidowany predykcyjnie" (every leg's market family passed the
predictive validation) versus "eksperymentalny" (simulation only). Neither claims an
economic edge over STS — that separate status belongs to the EV policy.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations
from math import prod

from sports_edge.analysis.edge_engine import Candidate
from sports_edge.settlement import CostProfile, Outcome, settle

VALIDATED = "validated_predictive"
JOINT_BASIS = "iloczyn prawdopodobieństw różnych meczów (założenie niezależności)"


@dataclass(frozen=True)
class Proposal:
    profile: str
    label: str
    legs: tuple[Candidate, ...]
    p_calibrated: float
    p_conservative: float
    total_odds: Decimal
    stake: Decimal
    payout: Decimal
    net_profit: Decimal
    ev_net: Decimal
    level: str
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    joint_basis: str = JOINT_BASIS

    @property
    def validated(self) -> bool:
        return self.level == VALIDATED


@dataclass(frozen=True)
class BuilderReport:
    proposals: dict[str, Proposal | None]
    refusals: dict[str, str]
    pool: tuple[Candidate, ...]
    excluded: dict[str, int] = field(default_factory=dict)


def eligibility(candidate: Candidate, max_odds: float) -> str | None:
    """Why a selection cannot enter any coupon (None = eligible)."""
    if candidate.p_model is None:
        return "brak prognozy modelu"
    if candidate.p_low is None:
        return "brak konserwatywnego oszacowania"
    if candidate.event_status != "before_start":
        return "wydarzenie nie jest przed rozpoczęciem"
    if candidate.freshness != "fresh":
        return "kurs niepotwierdzony (stara migawka)"
    if candidate.presence != "current":
        return "rynek niepotwierdzony lub wycofany"
    if candidate.data_status != "ok":
        return "jakość odczytu oferty"
    if candidate.rules_status != "zweryfikowane":
        return "niezweryfikowane zasady rozliczenia"
    if candidate.break_even is None or candidate.break_even >= 1:
        return "wygrana nie pokrywa stawki (podatek)"
    if float(candidate.odds) > max_odds:
        return f"kurs powyżej {max_odds}"
    return None


def build_coupons(
    candidates: list[Candidate],
    policy,
    *,
    cost: CostProfile,
    on_date: date,
    stake: Decimal | None = None,
) -> BuilderReport:
    builder = policy.builder
    stake = stake or Decimal(builder["stake"])
    excluded: dict[str, int] = {}
    per_event: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        why = eligibility(candidate, float(builder["max_odds_per_leg"]))
        if why:
            excluded[why] = excluded.get(why, 0) + 1
            continue
        per_event.setdefault(candidate.event_id, []).append(candidate)
    # A few alternatives per match: a slightly less likely selection with higher odds can make
    # a better coupon. The search still takes at most one leg from one match.
    alternatives = [
        c
        for items in per_event.values()
        for c in sorted(items, key=_strength, reverse=True)[: int(builder.get("per_event", 1))]
    ]
    pool = tuple(sorted(alternatives, key=_strength, reverse=True)[: int(builder["candidates"])])
    proposals, refusals = {}, {}
    for name, profile in policy.profiles.items():
        legs = [
            c
            for c in pool
            if c.p_low >= float(profile["min_leg_probability"])
            and (not profile["require_predictive_validation"] or c.predictive_status == VALIDATED)
        ]
        found = _optimise(legs, profile, cost=cost, on_date=on_date, stake=stake)
        if found is None:
            proposals[name] = None
            refusals[name] = _refusal(legs, profile)
            continue
        proposals[name] = _proposal(name, profile, found, cost=cost, on_date=on_date, stake=stake)
    return BuilderReport(proposals, refusals, pool, excluded)


def _strength(candidate: Candidate) -> tuple[float, float]:
    return candidate.p_low, candidate.p_model


def _optimise(legs, profile, *, cost, on_date, stake):
    best = None
    for n in range(1, int(profile["max_legs"]) + 1):
        for combo in combinations(legs, n):
            if len({c.event_id for c in combo}) != n:
                continue  # never two markets of one match
            total = _total_odds(combo)
            if total < Decimal(str(profile["min_total_odds"])):
                continue
            net = _net_on_win(stake, total, cost, on_date)
            if net is None or net < stake * Decimal(str(profile["min_net_profit_share"])):
                continue
            score = (prod(c.p_low for c in combo), -n, prod(c.p_model for c in combo))
            if best is None or score > best[0]:
                best = (score, combo, total, net)
    return best


def _total_odds(combo) -> Decimal:
    # STS rounding of combined odds is not documented in the reviewed rules: exact product.
    return prod((c.odds for c in combo), start=Decimal(1)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _net_on_win(stake, total, cost, on_date) -> Decimal | None:
    try:
        return settle(stake, total, Outcome.WIN, cost, on_date=on_date).net_profit
    except ValueError:
        return None


def _proposal(name, profile, found, *, cost, on_date, stake) -> Proposal:
    (p_cons, _neg_n, p_cal), combo, total, net = found
    flow = settle(stake, total, Outcome.WIN, cost, on_date=on_date)
    ev = (Decimal(str(p_cal)) * net - Decimal(str(1 - p_cal)) * stake) / stake
    level = VALIDATED if all(c.predictive_status == VALIDATED for c in combo) else "experimental"
    reasons = [
        f"Szansa trafienia (ostrożnie): {p_cons:.0%}, według modelu {p_cal:.0%}",
        "1 selekcja (SOLO)" if len(combo) == 1 else f"{len(combo)} selekcje z różnych meczów (AKO)",
        f"Wypłata {flow.payout} zł przy stawce {stake} zł — zysk netto {net} zł",
    ]
    for leg in combo:
        reasons.append(
            f"{leg.event}: {leg.market_label} · {leg.selection} @ {leg.odds} — P {leg.p_model:.0%}"
            f" (ostrożnie {leg.p_low:.0%}); {leg.calibration}"
        )
    warnings = []
    if len(combo) > 1:
        warnings.append("Łączne prawdopodobieństwo zakłada niezależność różnych meczów")
    if level != VALIDATED:
        warnings.append(
            "Co najmniej jedna selekcja z modelu bez walidacji predykcyjnej — symulacja"
        )
    if ev < 0:
        warnings.append(f"EV netto {ev:.3f} < 0: wysoka szansa trafienia nie oznacza zysku")
    return Proposal(
        profile=name,
        label=profile["label"],
        legs=tuple(combo),
        p_calibrated=p_cal,
        p_conservative=p_cons,
        total_odds=total,
        stake=stake,
        payout=flow.payout,
        net_profit=net,
        ev_net=ev.quantize(Decimal("0.0001")),
        level=level,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def _refusal(legs, profile) -> str:
    if not legs:
        need = "zwalidowanych predykcyjnie " if profile["require_predictive_validation"] else ""
        return (
            f"Brak {need}selekcji z ostrożną szansą ≥ {float(profile['min_leg_probability']):.0%}"
            " i świeżym kursem"
        )
    return (
        f"Żadna kombinacja do {profile['max_legs']} selekcji nie daje kursu ≥ "
        f"{profile['min_total_odds']} i zysku netto ≥ {float(profile['min_net_profit_share']):.0%}"
        " stawki"
    )
