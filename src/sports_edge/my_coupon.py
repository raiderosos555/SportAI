"""MÓJ KUPON (TASK 007): the user's own STS coupon built from the offer.

Pure functions, no interface. A leg keeps the existing identifiers of the offer (event,
market, selection, odds) and a snapshot of what the user saw when adding it; every view
re-reads the current offer, so a changed or stale price is never shown as current.

Rules: no duplicate, no two selections of one market, no contradictory selections of one
match (no score would make all of them win), no mixed periods of one match, no started
event or unconfirmed price in a coupon saved as current. Two selections of one match are
dependent and the application has no joint model for them: the coupon probability is then
not computed (STS also does not accept such legs on an ordinary AKO coupon).
"""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from math import prod
from pathlib import Path

from sports_edge import insights as ins
from sports_edge.analysis.coupon_builder import VALIDATED
from sports_edge.analysis.edge_engine import Candidate
from sports_edge.ingestion.types import iso
from sports_edge.paper import resolve
from sports_edge.settlement import CostProfile, Outcome, settle

NO_MODEL = "Brak zwalidowanego modelu dla tego rynku — nie można wiarygodnie oszacować szansy"
SAME_EVENT = (
    "Dwa typy z jednego meczu są od siebie zależne, a aplikacja nie ma dla nich modelu "
    "łącznego — szansa kuponu nie jest liczona (STS nie przyjmuje takich typów na zwykłym "
    "kuponie AKO)"
)
APPROXIMATION = (
    "Szansa kuponu to przybliżenie: iloczyn szans różnych meczów przy założeniu ich niezależności"
)


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str  # "block": the coupon cannot be saved as current; "warn": shown only
    message: str
    legs: tuple[int, ...] = ()


def leg_from(candidate: Candidate, *, day: str, now: datetime) -> dict:
    """What the cart stores: identifiers plus a snapshot of the moment of adding."""
    return {
        "selection_id": candidate.selection_id,
        "odds_id": candidate.odds_id,
        "event_id": candidate.event_id,
        "market_id": candidate.market_id,
        "day": day,
        "added_odds": str(candidate.odds),
        "added_at": iso(now),
        "event": candidate.event,
        "label": ins.candidate_pick(candidate),
        "market": candidate.market_label,
        "start_at": candidate.start_at,
    }


def current(cart: list[dict], candidates: list[Candidate]) -> list[Candidate | None]:
    """The current offer row of every leg (the newest price of the same selection)."""
    by_selection = {c.selection_id: c for c in candidates}
    return [by_selection.get(leg["selection_id"]) for leg in cart]


def _market(c: Candidate) -> dict:
    # football markets of the STS offer are regular time (no extra time)
    return {"market_key": c.market_key, "period": c.period, "line": c.line, "overtime": "false"}


def _scores():
    for h in range(9):
        for a in range(9):
            for hh in range(h + 1):
                for ha in range(a + 1):
                    yield {
                        "status": "completed",
                        "verified": 1,
                        "home_score": h,
                        "away_score": a,
                        "ht_home": hh,
                        "ht_away": ha,
                        "details_json": "{}",
                    }


def compatible(legs: list[Candidate]) -> bool | None:
    """Can all selections of ONE match win together? None: a market we cannot simulate."""
    seen_any = False
    for result in _scores():
        outcomes = [resolve(_market(c), c.selection, result) for c in legs]
        if any(o is None for o in outcomes):
            continue
        seen_any = True
        if all(o == Outcome.WIN for o in outcomes):
            return True
    return False if seen_any else None


def conflict(cart: list[dict], rows: list[Candidate | None], new: Candidate) -> str | None:
    """Why ``new`` cannot join the coupon (None: it can)."""
    for leg, row in zip(cart, rows, strict=True):
        if leg["selection_id"] == new.selection_id:
            return "Ta selekcja jest już na kuponie"
        if leg["market_id"] == new.market_id:
            return "Na kuponie jest już inna selekcja z tego samego rynku tego meczu"
        if row is not None and row.event_id == new.event_id and row.period != new.period:
            return "Nie łącz różnych okresów jednego meczu (np. 1. połowa i cały mecz)"
    same = [r for r in rows if r is not None and r.event_id == new.event_id]
    if same and compatible([*same, new]) is False:
        return "Sprzeczne typy: żaden wynik meczu nie daje wygranej obu selekcji"
    return None


def add(
    cart: list[dict], candidates: list[Candidate], new: Candidate, *, day: str, now: datetime
) -> tuple[list[dict], str | None]:
    why = conflict(cart, current(cart, candidates), new)
    if why:
        return cart, why
    return [*cart, leg_from(new, day=day, now=now)], None


def remove(cart: list[dict], selection_id: str) -> list[dict]:
    return [leg for leg in cart if leg["selection_id"] != selection_id]


def validate(cart: list[dict], rows: list[Candidate | None]) -> list[Issue]:
    issues: list[Issue] = []
    if not cart:
        return [Issue("empty", "block", "Kupon jest pusty — dodaj co najmniej jedną selekcję")]
    for i, (leg, row) in enumerate(zip(cart, rows, strict=True)):
        name = f"{i + 1}. {leg['event']}"
        if row is None:
            issues.append(
                Issue("missing", "block", f"{name}: selekcji nie ma w bieżącej ofercie", (i,))
            )
            continue
        if row.event_status != "before_start":
            issues.append(
                Issue("started", "block", f"{name}: wydarzenie nie jest przed startem", (i,))
            )
        if row.presence == "withdrawn":
            issues.append(Issue("withdrawn", "block", f"{name}: rynek wycofany z oferty", (i,)))
        if row.freshness != "fresh":
            issues.append(
                Issue(
                    "stale",
                    "block",
                    f"{name}: kurs niepotwierdzony w świeżym dokumencie STS — odśwież ofertę",
                    (i,),
                )
            )
        if Decimal(leg["added_odds"]) != row.odds:
            issues.append(
                Issue(
                    "price_changed",
                    "warn",
                    f"{name}: kurs zmienił się z {leg['added_odds']} na {row.odds}",
                    (i,),
                )
            )
        if not modelled(row):
            issues.append(Issue("no_model", "warn", f"{name}: {NO_MODEL}", (i,)))
    by_event: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        if row is not None:
            by_event.setdefault(row.event_id, []).append(i)
    for idx in by_event.values():
        if len(idx) < 2:
            continue
        legs = [rows[i] for i in idx]
        if len({r.period for r in legs}) > 1:
            issues.append(Issue("mixed_period", "block", "Różne okresy jednego meczu", tuple(idx)))
        if compatible(legs) is False:
            issues.append(
                Issue("contradictory", "block", "Sprzeczne typy z jednego meczu", tuple(idx))
            )
        issues.append(Issue("same_event", "block", SAME_EVENT, tuple(idx)))
    return issues


def modelled(row: Candidate) -> bool:
    """A calibrated, consistent model probability with a conservative bound: only such a
    selection counts in the coupon probability (an uncalibrated raw value never does)."""
    return row.p_model is not None and row.probability_ok and row.p_low is not None


def total_odds(rows: list[Candidate]) -> Decimal:
    return prod((r.odds for r in rows), start=Decimal(1)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def summary(
    cart: list[dict],
    rows: list[Candidate | None],
    *,
    stake: Decimal,
    cost: CostProfile,
    on_date,
) -> dict:
    """Odds, payout, net profit, break-even hit rate and — when it can be computed — the
    approximate probability of the whole coupon (never from odds alone)."""
    issues = validate(cart, rows)
    present = [r for r in rows if r is not None]
    out: dict = {
        "legs": len(cart),
        "issues": issues,
        "blocked": any(i.severity == "block" for i in issues),
        "stake": stake,
        "total_odds": total_odds(present) if present else None,
        "probability": None,
        "probability_low": None,
        "probability_note": None,
        "validated": False,
    }
    if present and out["total_odds"] > 1:
        flow = settle(stake, out["total_odds"], Outcome.WIN, cost, on_date=on_date)
        out.update(
            payout=flow.payout,
            net_profit=flow.net_profit,
            stake_tax=flow.stake_tax,
            break_even=float(stake / flow.payout) if flow.payout else None,
        )
    if len(present) != len(cart) or not present:
        out["probability_note"] = "Brak części selekcji w bieżącej ofercie"
        return out
    if any(i.code == "same_event" for i in issues):
        out["probability_note"] = SAME_EVENT
        return out
    if not all(modelled(r) for r in present):
        out["probability_note"] = NO_MODEL
        return out
    out["probability"] = prod(r.p_model for r in present)
    out["probability_low"] = prod(r.p_low for r in present)
    out["validated"] = all(r.predictive_status == VALIDATED for r in present)
    out["probability_note"] = APPROXIMATION if len(present) > 1 else None
    if "net_profit" in out:
        p = out["probability"]
        out["ev"] = (Decimal(str(p)) * out["net_profit"] - Decimal(str(1 - p)) * stake) / stake
    return out


# ---------------------------------------------------------------- local draft (one file)


def load_draft(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"legs": [], "stake": "10.00"}
    legs = [leg for leg in data.get("legs", []) if isinstance(leg, dict) and "selection_id" in leg]
    return {"legs": legs, "stake": str(data.get("stake", "10.00"))}


def save_draft(path: Path, cart: list[dict], stake: Decimal) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"legs": cart, "stake": str(stake)}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
