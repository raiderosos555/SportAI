"""ZBUDUJ KUPON NA KURS… (TASK 007): a coupon close to a target total odds.

The request becomes a temporary profile: total odds within target ± tolerance, a maximum
number of legs, a minimum conservative probability and reliability of every leg, a maximum
uncertainty, chosen sports, excluded leagues and markets, and whether every market must be
validated. The search is the builder's one (exhaustive, or dynamic programming for long
coupons): it maximises the conservative probability of the whole coupon. That product is an
approximation — matches are assumed independent. No rule is relaxed to find a coupon.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sports_edge import insights as ins
from sports_edge.analysis.coupon_builder import (
    VALIDATED,
    _optimise,
    _proposal,
    _strength,
    eligibility,
)
from sports_edge.analysis.edge_engine import Candidate
from sports_edge.settlement import CostProfile

RELIABILITY_ORDER = {"high": 3, "moderate": 2, "elevated": 1, "insufficient": 0}
MIN_RELIABILITY = {"any": 0, "moderate": 2, "high": 3}


@dataclass(frozen=True)
class Request:
    target_odds: Decimal
    tolerance: float = 0.10  # ± share of the target
    max_legs: int = 4
    min_leg_probability: float = 0.55
    min_reliability: str = "any"  # any | moderate | high
    max_uncertainty: float | None = None  # largest P − P_low of a leg
    sports: tuple[str, ...] = ()
    exclude_leagues: tuple[str, ...] = ()
    exclude_markets: tuple[str, ...] = ()
    require_validation: bool = True
    min_coupon_probability: float = 0.0

    @property
    def low(self) -> Decimal:
        value = self.target_odds * Decimal(str(1 - self.tolerance))
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def high(self) -> Decimal:
        value = self.target_odds * Decimal(str(1 + self.tolerance))
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def preset(profile: dict) -> Request:
    """The request equivalent to one of the four policy profiles: the middle of its odds
    range and the tolerance that reproduces the range (1,80–2,20 → 2,00 ± 10%)."""
    low, high = Decimal(str(profile["min_total_odds"])), Decimal(str(profile["max_total_odds"]))
    target = (low + high) / 2
    return Request(
        target_odds=target,
        tolerance=round(float((high - low) / 2 / target), 4),
        max_legs=int(profile["max_legs"]),
        min_leg_probability=float(profile["min_leg_probability"]),
        require_validation=bool(profile["require_predictive_validation"]),
        min_coupon_probability=float(profile.get("min_coupon_probability") or 0),
    )


def _why_out(c: Candidate, request: Request, now: datetime, max_odds: float) -> str | None:
    why = eligibility(c, max_odds)
    if why:
        return why
    if request.sports and c.sport not in request.sports:
        return "dyscyplina spoza wyboru"
    if c.league in request.exclude_leagues:
        return "liga wykluczona"
    if c.market_key in request.exclude_markets:
        return "rynek wykluczony"
    if c.p_low < request.min_leg_probability:
        return f"ostrożna szansa < {ins.pct(request.min_leg_probability, 0)}"
    if request.require_validation and c.predictive_status != VALIDATED:
        return "rynek bez walidacji predykcyjnej"
    if request.max_uncertainty is not None and c.p_model - c.p_low > request.max_uncertainty:
        return "zbyt duża niepewność oszacowania"
    level, _reasons = ins.reliability(c, now=now)
    if RELIABILITY_ORDER[level] < MIN_RELIABILITY.get(request.min_reliability, 0):
        return "zbyt niska wiarygodność"
    return None


def generate(
    candidates: list[Candidate],
    request: Request,
    *,
    now: datetime,
    cost: CostProfile,
    on_date,
    stake: Decimal,
    max_odds_per_leg: float = 4.0,
    per_event: int = 3,
    pool_size: int = 40,
) -> dict:
    """{"proposal", "alternatives", "excluded", "pool", "refusal", "request"}."""
    excluded: dict[str, int] = {}
    per_match: dict[str, list[Candidate]] = {}
    for c in candidates:
        why = _why_out(c, request, now, max_odds_per_leg)
        if why:
            excluded[why] = excluded.get(why, 0) + 1
            continue
        per_match.setdefault(c.event_id, []).append(c)
    choices = [
        c
        for items in per_match.values()
        for c in sorted(items, key=_strength, reverse=True)[:per_event]
    ]
    pool = sorted(choices, key=_strength, reverse=True)[:pool_size]
    profile = {
        "label": f"KUPON NA KURS {request.target_odds}",
        "min_total_odds": request.low,
        "max_total_odds": request.high,
        "max_legs": request.max_legs,
        "min_leg_probability": request.min_leg_probability,
        "min_coupon_probability": request.min_coupon_probability,
        "min_net_profit_share": 0,
        "require_predictive_validation": request.require_validation,
    }
    ranked = _optimise(pool, profile, cost=cost, on_date=on_date, stake=stake)
    built = [
        _proposal("target", profile, found, cost=cost, on_date=on_date, stake=stake)
        for found in ranked
    ]
    refusal = None
    if not built:
        refusal = (
            "Brak selekcji spełniających warunki (świeży kurs, wiarygodne oszacowanie, filtry)."
            if not pool
            else f"Żadna kombinacja do {request.max_legs} selekcji nie daje kursu w przedziale "
            f"{request.low}–{request.high} przy wymaganej ostrożnej szansie — dane nie "
            "pozwalają zbudować takiego kuponu (progi nie są obniżane)."
        )
    return {
        "proposal": built[0] if built else None,
        "alternatives": built[1:],
        "excluded": excluded,
        "pool": len(pool),
        "refusal": refusal,
        "request": request,
        "profile": profile,
    }
