"""Intelligent Coupon Builder (TASK 005, profiles of TASK 007): probability-first STS coupons.

For every profile the builder searches combinations of the best eligible selections and
keeps the one with the highest CONSERVATIVE probability of winning the whole coupon that
satisfies the profile: total odds range, number of legs, minimum leg probability, minimum
coupon probability and net profit on a win after the STS stake coefficient and payout tax.
Fewer legs win ties (a profile may prefer SOLO within a small tolerance); a coupon that does
not return more than the stake is never proposed. Small searches are exhaustive; long
accumulators (TASIEMKA) use dynamic programming over log-odds (one leg per match). A profile
that the data cannot satisfy produces no coupon — thresholds are never lowered.

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
from math import comb, log, prod

from sports_edge.analysis.edge_engine import Candidate
from sports_edge.settlement import CostProfile, Outcome, settle

VALIDATED = "validated_predictive"
JOINT_BASIS = (
    "PRZYBLIŻENIE: iloczyn prawdopodobieństw różnych meczów przy założeniu niezależności. "
    "Test par meczów jednego dnia (bootstrap blokowy po dniach) nie wykazał zależności, ale "
    "tego nie dowodzi — rzeczywista szansa kuponu AKO może być inna"
)


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
    # why a profile has no coupon: no_candidates | not_validated | low_probability | payout
    # (odds outside the profile range or too little net profit) | coupon_probability
    refusal_codes: dict[str, str] = field(default_factory=dict)
    alternatives: dict[str, tuple[Proposal, ...]] = field(default_factory=dict)


def eligibility(candidate: Candidate, max_odds: float) -> str | None:
    """Why a selection cannot enter any coupon (None = eligible)."""
    if candidate.p_model is None:
        return "brak prognozy modelu"
    if not candidate.probability_ok:
        return "niespójne prawdopodobieństwo (przedział nie obejmuje wartości)"
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
    proposals, refusals, codes, alternatives = {}, {}, {}, {}
    wide = tuple(
        sorted(alternatives, key=_strength, reverse=True)[: int(builder.get("long_candidates", 0))]
    )
    for name, profile in policy.profiles.items():
        source = wide if int(profile["max_legs"]) > 4 and wide else pool
        likely = [c for c in source if c.p_low >= float(profile["min_leg_probability"])]
        legs = [
            c
            for c in likely
            if not profile["require_predictive_validation"] or c.predictive_status == VALIDATED
        ]
        ranked = _optimise(legs, profile, cost=cost, on_date=on_date, stake=stake)
        if not ranked:
            proposals[name] = None
            floor = profile.get("min_coupon_probability")
            in_range = (
                floor is not None
                and legs
                and _optimise(
                    legs,
                    {**profile, "min_coupon_probability": 0},
                    cost=cost,
                    on_date=on_date,
                    stake=stake,
                    keep=1,
                )
            )
            refusals[name] = _refusal(legs, profile, floor_failed=bool(in_range))
            codes[name] = (
                "no_candidates"
                if not pool
                else "coupon_probability"
                if in_range
                else "payout"
                if legs
                else "not_validated"
                if likely
                else "low_probability"
            )
            continue
        built = [
            _proposal(name, profile, found, cost=cost, on_date=on_date, stake=stake)
            for found in ranked
        ]
        proposals[name] = built[0]
        alternatives[name] = tuple(built[1:])
    return BuilderReport(proposals, refusals, pool, excluded, codes, alternatives)


def _strength(candidate: Candidate) -> tuple[float, float]:
    return candidate.p_low, candidate.p_model


EXHAUSTIVE_LIMIT = 200_000  # combinations; beyond this the search is dynamic programming
LOG_STEP = 0.004  # resolution of the log-odds grid of the dynamic programming search


def _optimise(legs, profile, *, cost, on_date, stake, keep: int = 3):
    """Best combinations (most likely first): the proposal and up to two alternatives that
    share no match with a better one."""
    max_legs = int(profile["max_legs"])
    if sum(comb(len(legs), n) for n in range(1, max_legs + 1)) > EXHAUSTIVE_LIMIT:
        found = _dynamic(legs, profile, cost=cost, on_date=on_date, stake=stake, keep=keep)
    else:
        found = []
        for n in range(1, max_legs + 1):
            for combo in combinations(legs, n):
                if len({c.event_id for c in combo}) != n:
                    continue  # never two markets of one match
                item = _accept(combo, profile, cost=cost, on_date=on_date, stake=stake)
                if item is not None:
                    found.append(item)
    found.sort(key=lambda item: item[0], reverse=True)
    found = _prefer_solo(found, profile)
    chosen, used = [], set()
    for item in found:
        events = {c.event_id for c in item[1]}
        if chosen and events & used:
            continue  # an alternative is a different bet, not the same match again
        chosen.append(item)
        used |= events
        if len(chosen) == keep:
            break
    return chosen


def _accept(combo, profile, *, cost, on_date, stake):
    """(score, combo, total, net) when the combination meets every rule of the profile."""
    total = _total_odds(combo)
    if total < Decimal(str(profile["min_total_odds"])):
        return None
    high = profile.get("max_total_odds")
    if high is not None and total > Decimal(str(high)):
        return None
    net = _net_on_win(stake, total, cost, on_date)
    if net is None or net < stake * Decimal(str(profile["min_net_profit_share"])):
        return None
    p_low = prod(c.p_low for c in combo)
    if p_low < float(profile.get("min_coupon_probability") or 0):
        return None
    return (p_low, -len(combo), prod(c.p_model for c in combo)), tuple(combo), total, net


def _prefer_solo(found, profile):
    """A profile that prefers SOLO puts the best single selection first when it is within
    ``solo_tolerance`` of the most likely combination (TASK 007: WYSOKA TRAFNOŚĆ)."""
    if not profile.get("prefer_solo") or not found:
        return found
    tolerance = float(profile.get("solo_tolerance", 0))
    solo = next((item for item in found if len(item[1]) == 1), None)
    if solo is None or solo is found[0] or solo[0][0] < found[0][0][0] - tolerance:
        return found
    return [solo, *(item for item in found if item is not solo)]


def _dynamic(legs, profile, *, cost, on_date, stake, keep):
    """Long accumulators: maximise the sum of log conservative probabilities subject to the
    log-odds range, one leg per match (multiple-choice knapsack on a log-odds grid). Each
    rerun excludes the matches of the coupons already found, so alternatives differ."""
    low = log(float(profile["min_total_odds"]))
    high = log(float(profile.get("max_total_odds") or 1000.0))
    max_legs = int(profile["max_legs"])
    by_event: dict[str, list] = {}
    for c in legs:
        by_event.setdefault(c.event_id, []).append(c)
    out, banned = [], set()
    for _ in range(keep):
        top = int(high / LOG_STEP) + max_legs  # rounding slack of the grid
        states = {(0, 0): (0.0, ())}
        for event, options in by_event.items():
            if event in banned:
                continue
            new = dict(states)
            for (n, bucket), (score, chain) in states.items():
                if n == max_legs:
                    continue
                for c in options:
                    nb = bucket + round(log(float(c.odds)) / LOG_STEP)
                    if nb > top:
                        continue
                    value = score + log(c.p_low)
                    key = (n + 1, nb)
                    if key not in new or value > new[key][0]:
                        new[key] = (value, (*chain, c))
            states = new
        ranked = sorted(
            (
                item
                for (n, bucket), (score, chain) in states.items()
                if n and bucket * LOG_STEP >= low - max_legs * LOG_STEP
                for item in [_accept(chain, profile, cost=cost, on_date=on_date, stake=stake)]
                if item is not None
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked:
            break
        out.append(ranked[0])
        banned |= {c.event_id for c in ranked[0][1]}
    return out


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
        warnings.append("Łączna szansa AKO jest przybliżeniem: zakłada niezależność różnych meczów")
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


def _refusal(legs, profile, *, floor_failed: bool = False) -> str:
    if not legs:
        need = "zwalidowanych predykcyjnie " if profile["require_predictive_validation"] else ""
        return (
            f"Brak {need}selekcji z ostrożną szansą ≥ {float(profile['min_leg_probability']):.0%}"
            " i świeżym kursem"
        )
    if floor_failed:
        return (
            f"Kombinacje w przedziale kursu istnieją, ale ostrożna szansa kuponu jest niższa niż "
            f"{float(profile['min_coupon_probability']):.0%} — dane nie uzasadniają takiego kuponu"
        )
    high = profile.get("max_total_odds")
    span = (
        f"w przedziale {profile['min_total_odds']}–{high}"
        if high is not None
        else f"≥ {profile['min_total_odds']}"
    )
    return (
        f"Żadna kombinacja do {profile['max_legs']} selekcji nie daje kursu {span} i zysku "
        f"netto ≥ {float(profile['min_net_profit_share']):.0%} stawki"
    )
