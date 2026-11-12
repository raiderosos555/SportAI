"""Daily Coupon Engine. No selection is produced to fill a daily quota.

SOLO candidates must pass every gate of the policy ("validated" tier). Experimental
candidates (positive EV from a model whose edge is not confirmed) are listed separately and
never presented as tips. AKO is analysed only with an explicit joint-probability model;
correlated legs (same event) are never multiplied together.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from math import prod
from typing import Protocol

from sports_edge.analysis.edge_engine import Candidate
from sports_edge.analysis.policy import Policy
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.settlement import CostProfile, Outcome, settle
from sports_edge.storage import Store

NO_EDGE = "BRAK POTWIERDZONEJ PRZEWAGI — BRAK KUPONU DNIA"
NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def reason_group(text: str) -> str:
    """Reasons that differ only by a number (odds, EV, hours) are counted together."""
    return NUMBER.sub("…", text)


class JointModel(Protocol):
    name: str

    def joint_probability(self, legs: Sequence[Candidate]) -> float | None: ...


class IndependentEventsModel:
    """Product of marginals, allowed only for legs from different events.

    Independence between different matches is an explicit modelling assumption that the
    user must enable; it is never applied to legs of the same event.
    """

    name = "independent-events-assumption"

    def joint_probability(self, legs: Sequence[Candidate]) -> float | None:
        if len({leg.event_id for leg in legs}) != len(legs):
            return None
        if any(leg.p_model is None for leg in legs):
            return None
        return prod(leg.p_model for leg in legs)


@dataclass(frozen=True)
class AkoAnalysis:
    created: bool
    reason: str
    legs: tuple[Candidate, ...] = ()
    joint_probability: float | None = None
    total_odds: Decimal | None = None
    ev_net: Decimal | None = None
    solo_ev_net_sum: Decimal | None = None


@dataclass(frozen=True)
class CouponReport:
    day: str
    created_at: datetime
    policy_version: str
    headline: str
    solo: tuple[Candidate, ...]
    analysed: int
    rejection_reasons: dict[str, int]
    ako: AkoAnalysis
    experimental: tuple[Candidate, ...] = ()
    probable: tuple[Candidate, ...] = ()  # informational: highest model win probability
    probable_unconfirmed: tuple[Candidate, ...] = ()  # same, but the price needs confirmation
    counts: dict[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    diagnosis: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_coupon(self) -> bool:
        return bool(self.solo)


def rank_key(candidate: Candidate) -> tuple:
    return (
        candidate.ev_net_low if candidate.ev_net_low is not None else Decimal("-9"),
        candidate.quality,
        -(candidate.p_high - candidate.p_low) if candidate.p_low is not None else -1.0,
        candidate.freshness == "fresh",
    )


def build_daily_coupon(
    candidates: Sequence[Candidate],
    policy: Policy,
    *,
    day: str,
    now: datetime,
    joint_model: JointModel | None = None,
    cost: CostProfile | None = None,
    stake: Decimal = Decimal("10.00"),
) -> CouponReport:
    accepted = sorted((c for c in candidates if c.accepted), key=rank_key, reverse=True)
    # One selection per event in the SOLO ranking: several legs of one match are one risk.
    best_per_event: dict[str, Candidate] = {}
    for candidate in accepted:
        best_per_event.setdefault(candidate.event_id, candidate)
    solo = tuple(sorted(best_per_event.values(), key=rank_key, reverse=True))[
        : policy.max_selections
    ]
    reasons = Counter(reason_group(r) for c in candidates if not c.accepted for r in c.reasons)
    experimental = tuple(
        sorted(
            (c for c in candidates if c.tier == "experimental"),
            key=lambda c: c.ev_net if c.ev_net is not None else Decimal("-9"),
            reverse=True,
        )
    )
    probable_all = probable_ranking(candidates, policy)
    probable = tuple(c for c in probable_all if c.freshness == "fresh")
    probable_unconfirmed = tuple(c for c in probable_all if c.freshness != "fresh")
    counts = {
        "probable": len(probable),
        "events": len({c.event_id for c in candidates}),
        "markets": len({c.market_id for c in candidates}),
        "markets_ok": len({c.market_id for c in candidates if c.data_status == "ok"}),
        "model_markets": len({c.market_id for c in candidates if c.p_model is not None}),
        "analysed": len(candidates),
        "with_forecast": sum(c.p_model is not None for c in candidates),
        "ev_positive": sum(c.ev_net is not None and c.ev_net > 0 for c in candidates),
        "experimental": len(experimental),
        "validated": sum(c.accepted for c in candidates),
        "rejected": sum(c.tier == "rejected" for c in candidates),
    }
    warnings = []
    if not candidates:
        warnings.append("Brak zaimportowanych ofert dla wybranego dnia")
    if experimental:
        warnings.append(
            f"{len(experimental)} kandydatów eksperymentalnych: przewaga niepotwierdzona, "
            "to nie są typy do gry"
        )
    ako = analyse_ako(solo, policy, joint_model=joint_model, cost=cost, stake=stake, now=now)
    return CouponReport(
        day=day,
        created_at=now,
        policy_version=policy.version,
        headline=NO_EDGE if not solo else f"Kandydaci SOLO: {len(solo)} (tryb analityczny)",
        solo=solo,
        analysed=len(candidates),
        rejection_reasons=dict(reasons.most_common()),
        ako=ako,
        experimental=experimental,
        probable=probable,
        probable_unconfirmed=probable_unconfirmed,
        counts=counts,
        warnings=tuple(warnings),
        diagnosis=diagnose(counts),
    )


def probable_ranking(candidates: Sequence[Candidate], policy: Policy) -> tuple[Candidate, ...]:
    """Selections with the highest model win probability — an information view only.

    Only events before their start, readable rows and markets still in the offer qualify.
    High probability is not profit: each row carries the break-even probability after the STS
    stake tax and the net EV, which is negative whenever the price pays less than the risk.
    """
    eligible = [
        c
        for c in candidates
        if c.p_model is not None
        and c.probability_ok
        and c.p_model >= policy.probable_min_probability
        and c.event_status == "before_start"
        and c.data_status == "ok"
        and c.presence != "withdrawn"
    ]
    eligible.sort(
        key=lambda c: (c.p_model, c.ev_net if c.ev_net is not None else Decimal("-9")),
        reverse=True,
    )
    return tuple(eligible[:50])


def diagnose(counts: dict[str, int], *, sports_data_fresh: bool | None = None) -> tuple[str, ...]:
    """Separate explanations; "no confirmed edge" is never the only message when the
    real cause is missing data, missing forecasts or a missing model."""
    out = []
    if not counts.get("analysed"):
        return ("Brak oferty STS dla wybranego dnia",)
    if sports_data_fresh is False:
        out.append("Brak aktualnych danych sportowych — prognozy wstrzymane (próg 21 dni)")
    if not counts.get("with_forecast"):
        out.append("Brak prognoz modelu dla tej oferty")
    unsupported = counts.get("markets", 0) - counts.get("model_markets", 0)
    if unsupported > 0:
        out.append(f"Brak modelu dla {unsupported} z {counts.get('markets', 0)} rynków")
    if counts.get("with_forecast") and not counts.get("ev_positive"):
        out.append("Brak dodatniego EV netto wśród selekcji z prognozą")
    if counts.get("ev_positive") and not counts.get("validated"):
        out.append("Brak wystarczającej walidacji — kandydaci wyłącznie eksperymentalni")
    if not counts.get("validated"):
        out.append("Brak potwierdzonej przewagi")
    return tuple(out)


def analyse_ako(
    legs: Sequence[Candidate],
    policy: Policy,
    *,
    joint_model: JointModel | None,
    cost: CostProfile | None,
    stake: Decimal,
    now: datetime,
) -> AkoAnalysis:
    if not policy.ako_enabled:
        return AkoAnalysis(False, "Analiza AKO wyłączona w polityce (domyślnie)")
    if len(legs) < 2:
        return AkoAnalysis(False, "Mniej niż dwie zaakceptowane selekcje SOLO")
    if joint_model is None:
        return AkoAnalysis(False, "Brak wiarygodnego modelu prawdopodobieństwa łącznego")
    p_joint = joint_model.joint_probability(legs)
    if p_joint is None:
        return AkoAnalysis(False, "Model łączny odrzucił zestaw (zależne lub niepełne selekcje)")
    total = prod(leg.odds for leg in legs)  # operator rounding of AKO odds is not assumed
    solo_sum = sum((leg.ev_net for leg in legs if leg.ev_net is not None), Decimal(0))
    if cost is None:
        return AkoAnalysis(False, "Brak profilu kosztów", tuple(legs), p_joint, total)
    on_date = min(datetime.fromisoformat(leg.start_at).date() for leg in legs if leg.start_at)
    try:
        win = settle(stake, total, Outcome.WIN, cost, on_date=on_date).net_profit
        loss = settle(stake, total, Outcome.LOSS, cost, on_date=on_date).net_profit
    except ValueError as error:
        return AkoAnalysis(False, f"Rozliczenie AKO nieobsługiwane: {error}", tuple(legs))
    ev = (Decimal(str(p_joint)) * win + Decimal(str(1 - p_joint)) * loss) / stake
    reason = (
        f"Model łączny: {joint_model.name}. AKO nie jest domyślnie lepsze od SOLO "
        f"(suma EV SOLO {solo_sum:.3f} vs EV AKO {ev:.3f})."
    )
    ev = ev.quantize(Decimal("0.0001"))
    return AkoAnalysis(True, reason, tuple(legs), p_joint, total, ev, solo_sum)


def save_analysis(
    store: Store,
    report: CouponReport,
    candidates: Sequence[Candidate],
    policy: Policy,
    *,
    pipeline: dict | None = None,
) -> str:
    """Immutable snapshot of the analysis with the exact quote ids and prices it used.

    ``pipeline`` keeps the stage report of the one-button flow (sources, data update, model
    versions, data snapshots) so the analysis can be reproduced and settled later.

    TASK 016: only the selections the analysis could judge are stored item by item — those
    with a model forecast and those named in the report. A selection without a forecast is
    an offer row and nothing else: its price and document are in the offer tables (and
    ``offer_table(as_of=created_at)`` returns them), so copying thousands of them into every
    analysis only grew the database (measured: 95% of the bytes of ``analyses``). The
    summary says how many selections were analysed and how many are stored.
    """
    named = {
        c.key for group in (report.solo, report.experimental, report.probable) for c in group
    } | {c.key for c in report.probable_unconfirmed}
    stored = [c for c in candidates if c.p_model is not None or c.key in named]
    items = [
        {
            "key": c.key,
            "event": c.event,
            "start_at": c.start_at,
            "offer_code": c.offer_code,
            "market": c.market_key,
            "period": c.period,
            "line": c.line,
            "selection": c.selection,
            "odds": str(c.odds),
            "odds_id": c.odds_id,
            "p_model": c.p_model,
            "p_market": c.p_market,
            "ev_net": str(c.ev_net) if c.ev_net is not None else None,
            "ev_net_low": str(c.ev_net_low) if c.ev_net_low is not None else None,
            "freshness": c.freshness,
            "odds_age_hours": c.odds_age_hours,
            "document_at": c.document_at,
            "event_status": c.event_status,
            "break_even": c.break_even,
            "calibration": c.calibration,
            "market_label": c.market_label,
            "label_raw": c.label_raw,
            "tier": c.tier,
            "model_version_id": c.model_version_id,
            "statuses": {
                "data": c.data_status,
                "model": c.model_status,
                "rules": c.rules_status,
                "tax": c.tax_status,
                "presence": c.presence,
            },
            "reasons": list(c.reasons),
        }
        for c in stored
    ]
    summary = {
        "headline": report.headline,
        "diagnosis": list(report.diagnosis),
        "analysed": report.analysed,
        "accepted": [c.key for c in report.solo],
        "experimental": [c.key for c in report.experimental],
        "probable": [c.key for c in report.probable],
        "probable_unconfirmed": [c.key for c in report.probable_unconfirmed],
        "counts": report.counts,
        "rejection_reasons": report.rejection_reasons,
        "ako": {"created": report.ako.created, "reason": report.ako.reason},
        "model_versions": sorted({c.model_version_id for c in candidates if c.model_version_id}),
        "pipeline": pipeline or {},
        "items_scope": "with_forecast_or_named",
        "items_total": len(candidates),
        "items_stored": len(stored),
    }
    key = digest(["analysis", report.day, iso(report.created_at), policy.version])
    with store.transaction():
        store.insert(
            "analyses",
            id=key,
            created_at=iso(report.created_at),
            analysis_date=report.day,
            policy_json=canonical(asdict(policy)),
            summary_json=canonical(summary),
            items_json=canonical(items),
        )
    return key
