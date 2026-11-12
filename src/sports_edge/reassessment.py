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
from sports_edge.coupon_store import counted_sql
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.repository import latest_result_status, offer_table
from sports_edge.storage import Store

PRICE_MOVE = Decimal("0.05")  # 5% change of the STS price is reported
NEAR_KICKOFF = timedelta(minutes=90)


def active_coupons(store: Store, *, now: datetime) -> list[dict]:
    """Unsettled coupons whose matches have not all finished long ago — official and user
    coupons only (TASK 010: a duplicate, a price update or a revision is never re-checked)."""
    return store.rows(
        f"""SELECT c.* FROM coupons c WHERE NOT EXISTS
        (SELECT 1 FROM settlements s WHERE s.coupon_id=c.id) AND c.first_start_at>?
        AND {counted_sql()} ORDER BY c.first_start_at""",
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
    facts: list | None = None,
) -> dict:
    """One coupon: returns the stored (or unchanged previous) reassessment.

    Every reason is also a FACT (kind, event, selection, change). Called by ``reassess_all``
    the facts are collected (``facts``) and alerted once for all coupons they concern;
    called alone, the coupon's facts are alerted at once. ``change`` is what happened in the
    world, not on one coupon: a price move is "kurs zmienił się na 1.65" for every coupon
    holding the selection, whatever price each of them was saved at."""
    reasons, risks, sources = [], [], set()
    found: list[tuple] = []
    outdated = False

    def fact(
        kind: str, leg: dict, detail: str, selection: bool = False, change: str | None = None
    ) -> None:
        reasons.append(detail)
        found.append(
            (kind, leg["event_id"], leg["selection_id"] if selection else None, change or detail)
        )

    for leg in _legs(store, coupon["id"]):
        name = f"{leg['home']} - {leg['away']}"
        status = results.get(leg["event_id"])
        if status in ("postponed", "cancelled", "abandoned", "void"):
            fact("coupon_outdated", leg, f"{name}: mecz {status}")
            outdated = True
            continue
        start = datetime.fromisoformat(leg["start_at"]) if leg["start_at"] else None
        if start is not None and now >= start:
            continue  # started: nothing more to check before the result
        row = offers.setdefault(leg["offer_date"], _offer(store, leg["offer_date"]))
        current = row.get(leg["selection_id"])
        if current is None or current["presence"] == "withdrawn":
            fact(
                "coupon_outdated",
                leg,
                f"{name}: selekcji nie ma już w aktualnej ofercie STS",
                selection=True,
            )
            outdated = True
        else:
            sources.add("oferta STS")
            old, new = Decimal(leg["price"]), current["price"]
            if old and abs(new - old) / old >= PRICE_MOVE:
                fact(
                    "coupon_attention",
                    leg,
                    f"{name}: kurs zmienił się z {old} na {new}",
                    selection=True,
                    change=f"{name}: kurs zmienił się na {new}",
                )
        info = prematch.summary(store, leg["event_id"])
        if info["last_check"]:
            sources.add("PitchAPI (składy)")
        changed = [
            c
            for c in info["changes"]
            if info["last_check"] and info["last_check"]["executed_at"] > coupon["created_at"]
        ]
        for change in changed:
            fact("coupon_attention", leg, f"{name}: {change['detail']}")
        verified = [a for a in info["absences"] if a["match_status"] == "verified"]
        for absence in verified:
            fact(
                "coupon_attention",
                leg,
                f"{name}: absencja {absence['player_name']} ({absence['reason']})"
                # TASK 016 §5.3: a manual entry is shown as verified by the user, with its source
                + (
                    f" — wpis użytkownika, źródło: {absence['source']}"
                    if absence["entered_by"] == "user"
                    else ""
                ),
            )
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
    if facts is not None:
        facts.extend((item, coupon) for item in found)
    else:
        alert_facts(store, [(item, coupon) for item in found], now=now)
    previous = latest(store, coupon["id"])
    if previous and previous["result"] == result and previous["reasons"] == reasons:
        return previous  # nothing new: no duplicate record
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
    return latest(store, coupon["id"])


def alert_facts(store: Store, facts: list[tuple], *, now: datetime) -> int:
    """One alert per fact (kind, event, selection, change) with the coupons it concerns:
    one price or lineup change is one alert, never one per coupon or copy of a coupon."""
    grouped: dict[tuple, list[dict]] = {}
    for item, coupon in facts:
        coupons = grouped.setdefault(item, [])
        if all(c["id"] != coupon["id"] for c in coupons):
            coupons.append(coupon)
    added = 0
    for (kind, event_id, selection_id, detail), coupons in grouped.items():
        names = ", ".join(c["note"] or c["id"][:8] for c in coupons[:3])
        added += alerts.create(
            store,
            kind=kind,
            message=f"{detail} — dotyczy kuponów: {len(coupons)} ({names})",
            source="Sports Edge AI",
            now=now,
            event_id=event_id,
            coupon_id=coupons[0]["id"],
            identity=[kind, event_id, selection_id, detail],
            payload={"coupons": [c["id"] for c in coupons], "selection_id": selection_id},
        )
    return added


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
    facts: list[tuple] = []
    for coupon in active_coupons(store, now=now):
        found = reassess(
            store, coupon, now=now, offers=offers, results=results, trigger=trigger, facts=facts
        )
        counts["coupons"] += 1
        counts[found["result"]] += 1
    counts["alerts"] = alert_facts(store, facts, now=now)
    return counts
