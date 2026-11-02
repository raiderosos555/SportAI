"""Coupon Reassessment Engine (TASK 008): is a saved coupon still what it was?

For every unsettled coupon the engine looks at what changed since the coupon was saved:
the event status (postponed, cancelled), the STS price and presence of each selection,
lineup changes and published lineups, verified absences and missing data. It records the
outcome — "bez zmian", "wymaga uwagi" or "nieaktualny" — with reasons, risks and sources.
The original forecast and the coupon itself are never changed: a coupon really placed in
STS is only reported on.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal

from sports_edge import alerts, prematch
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.repository import latest_result_status, offer_table
from sports_edge.storage import Store

PRICE_MOVE = Decimal("0.05")  # 5% change of the STS price is reported
NEAR_KICKOFF = timedelta(minutes=90)


def active_coupons(store: Store, *, now: datetime) -> list[dict]:
    """Unsettled coupons whose matches have not all finished long ago."""
    return store.rows(
        """SELECT c.* FROM coupons c WHERE NOT EXISTS
        (SELECT 1 FROM settlements s WHERE s.coupon_id=c.id) AND c.first_start_at>?
        ORDER BY c.first_start_at""",
        ((now - timedelta(hours=6)).isoformat(),),
    )


def _legs(store: Store, coupon_id: str) -> list[dict]:
    return store.rows(
        """SELECT l.*, s.key selection, m.id market_id, m.market_key, e.id event_id,
        e.home, e.away, e.start_at, e.offer_date FROM coupon_legs l
        JOIN odds o ON o.id=l.odds_id JOIN selections s ON s.id=l.selection_id
        JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id
        WHERE l.coupon_id=?""",
        (coupon_id,),
    )


def latest(store: Store, coupon_id: str) -> dict | None:
    rows = store.rows(
        """SELECT * FROM coupon_reassessments WHERE coupon_id=?
        ORDER BY checked_at DESC, id DESC LIMIT 1""",
        (coupon_id,),
    )
    if not rows:
        return None
    row = rows[0]
    return {
        **row,
        "reasons": json.loads(row["reasons_json"]),
        "risks": json.loads(row["risks_json"]),
        "sources": json.loads(row["sources_json"]),
    }


def reassess(
    store: Store,
    coupon: dict,
    *,
    now: datetime,
    offers: dict,
    results: dict,
    trigger: str = "automation",
) -> dict:
    """One coupon: returns the stored (or unchanged previous) reassessment."""
    reasons, risks, sources = [], [], set()
    outdated = False
    for leg in _legs(store, coupon["id"]):
        name = f"{leg['home']} - {leg['away']}"
        status = results.get(leg["event_id"])
        if status in ("postponed", "cancelled", "abandoned", "void"):
            reasons.append(f"{name}: mecz {status}")
            outdated = True
            continue
        start = datetime.fromisoformat(leg["start_at"]) if leg["start_at"] else None
        if start is not None and now >= start:
            continue  # started: nothing more to check before the result
        row = offers.setdefault(leg["offer_date"], _offer(store, leg["offer_date"]))
        current = row.get(leg["selection_id"])
        if current is None or current["presence"] == "withdrawn":
            reasons.append(f"{name}: selekcji nie ma już w aktualnej ofercie STS")
            outdated = True
        else:
            sources.add("oferta STS")
            old, new = Decimal(leg["price"]), current["price"]
            if old and abs(new - old) / old >= PRICE_MOVE:
                reasons.append(f"{name}: kurs zmienił się z {old} na {new}")
        info = prematch.summary(store, leg["event_id"])
        if info["last_check"]:
            sources.add("PitchAPI (składy)")
        changed = [
            c
            for c in info["changes"]
            if info["last_check"] and info["last_check"]["executed_at"] > coupon["created_at"]
        ]
        for change in changed:
            reasons.append(f"{name}: {change['detail']}")
        verified = [a for a in info["absences"] if a["match_status"] == "verified"]
        for absence in verified:
            reasons.append(f"{name}: absencja {absence['player_name']} ({absence['reason']})")
            sources.add(absence["source"])
        if (
            start is not None
            and start - now <= NEAR_KICKOFF
            and info["lineup_status"] != ("confirmed")
        ):
            risks.append(f"{name}: brak potwierdzonych składów na {NEAR_KICKOFF} przed meczem")
        if not verified:
            risks.append(f"{name}: brak zweryfikowanych informacji o absencjach")
    result = "nieaktualny" if outdated else "wymaga uwagi" if reasons else "bez zmian"
    previous = latest(store, coupon["id"])
    if previous and previous["result"] == result and previous["reasons"] == reasons:
        return previous  # nothing new: no duplicate record, no duplicate alert
    store.insert(
        "coupon_reassessments",
        id=digest(["reassessment", coupon["id"], iso(now), canonical(reasons)]),
        coupon_id=coupon["id"],
        checked_at=iso(now),
        trigger=trigger,
        result=result,
        previous_result=previous["result"] if previous else None,
        reasons_json=canonical(reasons),
        risks_json=canonical(risks),
        sources_json=canonical(sorted(sources)),
        previous_id=previous["id"] if previous else None,
    )
    if result != "bez zmian":
        alerts.create(
            store,
            kind="coupon_outdated" if outdated else "coupon_attention",
            message=f"Kupon {coupon['id'][:8]}… ({coupon['note'] or 'kupon'}): "
            + "; ".join(reasons[:3]),
            source="Sports Edge AI",
            now=now,
            coupon_id=coupon["id"],
            fingerprint=canonical(reasons),
        )
    return latest(store, coupon["id"])


def _offer(store: Store, day: str | None) -> dict:
    if not day:
        return {}
    out = {}
    for row in offer_table(store, day=day):
        for quote in row["selections"].values():
            out[quote["selection_id"]] = {"price": quote["price"], "presence": row["presence"]}
    return out


def reassess_all(store: Store, *, now: datetime, trigger: str = "automation") -> dict:
    offers: dict = {}
    results = latest_result_status(store)
    counts = {"coupons": 0, "bez zmian": 0, "wymaga uwagi": 0, "nieaktualny": 0}
    for coupon in active_coupons(store, now=now):
        found = reassess(store, coupon, now=now, offers=offers, results=results, trigger=trigger)
        counts["coupons"] += 1
        counts[found["result"]] += 1
    return counts
