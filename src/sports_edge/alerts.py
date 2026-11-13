"""Central alert register (TASK 008).

One alert per distinct fact: the id is derived from the kind, the event or coupon and the
fact itself, so the next check of an unchanged situation never repeats an alert. Reading an
alert adds a row to ``alert_reads`` (the alert itself is never modified).

TASK 016 — alerts without noise:
* the lineup changes of one match found by one check are ONE alert with the list of changes
  (``lineup_changes``); older alerts — one per change — stay as they are and are grouped
  when shown (same match, same check);
* order: alerts about coupons, then lineup changes of matches that are on coupons (and
  problems of the backup), then everything else; "ważne" = the first two groups;
* an alert about a match that has already started is "nieaktualny" (collapsed by default,
  never deleted); "Oznacz wszystkie jako przeczytane" appends ``alert_reads`` rows;
* the lack of an absence source is one collective notice a day, not one alert per match.
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.storage import Store

WARSAW = ZoneInfo("Europe/Warsaw")
LINEUP_KINDS = (
    "new_starter",
    "moved_to_bench",
    "removed_starter",
    "formation_change",
    "goalkeeper_change",
)
GROUP_KIND = "lineup_changes"
COUPON_KINDS = (
    "coupon_attention",
    "coupon_outdated",
    "result_mismatch",
    "daily_coupons_ready",
    "daily_coupons_none",
)
SYSTEM_KINDS = ("backup_skipped", "backup_failed")
ORDER = {"high": 0, "medium": 1, "low": 2}
RANKS = {0: "kupony", 1: "składy meczów z kuponów", 2: "pozostałe"}
PRIORITY = {
    "confirmed_published": "medium",
    "goalkeeper_change": "high",
    "new_starter": "low",
    "removed_starter": "medium",
    "moved_to_bench": "low",
    "formation_change": "low",
    "absence": "high",
    "no_absence_source": "low",
    "source_error": "medium",
    "coupon_attention": "high",
    "coupon_outdated": "high",
    "odds_changed": "medium",
    # TASK 012: weekly prospective report (information only — nothing changes in the models)
    "calibration_flag": "medium",
    "market_verdict": "medium",
    # TASK 016: the cyclic backup could not be made or failed its integrity check
    "backup_skipped": "high",
    "backup_failed": "high",
    # TASK 019: the coupon of the day by the worker; a provider's result against the user's
    "daily_coupons_ready": "medium",
    "daily_coupons_none": "low",
    "result_mismatch": "high",
}
LABELS = {
    "confirmed_published": "Opublikowano potwierdzony skład",
    "goalkeeper_change": "Zmienił się podstawowy bramkarz",
    "new_starter": "Nowy zawodnik w wyjściowym składzie",
    "removed_starter": "Zawodnika nie ma w składzie",
    "moved_to_bench": "Zawodnik przesunięty na ławkę",
    "formation_change": "Zmiana formacji",
    "absence": "Wykryto nową potwierdzoną absencję",
    "no_absence_source": "Brak wiarygodnych informacji o kontuzjach",
    "source_error": "Błąd źródła danych",
    "coupon_attention": "Kupon wymaga ponownej kontroli",
    "coupon_outdated": "Kupon nieaktualny",
    "odds_changed": "Kurs STS nie jest już aktualny",
    "calibration_flag": "Kalibracja strefy kuponów: nowa flaga",
    "market_verdict": "Porównanie z rynkiem STS: pierwszy werdykt",
    "lineup_changes": "Zmiany w składzie",
    "backup_skipped": "Kopia zapasowa pominięta",
    "backup_failed": "Kopia zapasowa nie przeszła kontroli",
    "daily_coupons_ready": "Kupony dnia gotowe",
    "daily_coupons_none": "Brak kuponu dnia",
    "result_mismatch": "Wynik dostawcy różni się od wpisu ręcznego",
}


def create(
    store: Store,
    *,
    kind: str,
    message: str,
    source: str,
    now: datetime,
    event_id: str | None = None,
    coupon_id: str | None = None,
    fingerprint: str = "",
    priority: str | None = None,
    payload: dict | None = None,
    identity: list | None = None,
) -> bool:
    """Add an alert unless the same fact was already reported (returns True when added).

    ``identity`` (TASK 010) replaces the default identity of the alert: a fact shared by
    several coupons (one price or lineup change) is one alert listing those coupons."""
    alert_id = digest(
        ["alert", *identity]
        if identity
        else ["alert", kind, event_id, coupon_id, fingerprint or message]
    )
    if store.get("prematch_alerts", alert_id) is not None:
        return False
    store.insert(
        "prematch_alerts",
        id=alert_id,
        created_at=iso(now),
        event_id=event_id,
        coupon_id=coupon_id,
        kind=kind,
        message=message,
        source=source,
        priority=priority or PRIORITY.get(kind, "medium"),
        payload_json=canonical(payload or {}),
    )
    return True


def event_label(event: dict) -> str:
    return f"{event.get('home')} - {event.get('away')}"


def from_check(store: Store, event: dict, result, *, now: datetime) -> int:
    """Alerts of one pre-match check: the lineup changes of the match as ONE alert with the
    list of changes (TASK 016 §4.1), a published confirmed lineup and source errors. The
    lack of an absence source is reported once a day for all matches (``absence_notice``)."""
    added = 0
    grouped = [c for c in result.changes if c["kind"] in LINEUP_KINDS]
    if grouped:
        details = [f"{c['detail']} ({c['team']})" for c in grouped]
        added += create(
            store,
            kind=GROUP_KIND,
            message=f"{event_label(event)}: zmiany w składzie ({len(grouped)}): "
            + "; ".join(details),
            source="PitchAPI",
            now=now,
            event_id=event["id"],
            fingerprint="|".join(sorted(details)),
            priority=min((PRIORITY.get(c["kind"], "low") for c in grouped), key=ORDER.get),
            payload={"changes": grouped},
        )
    for change in (c for c in result.changes if c["kind"] not in LINEUP_KINDS):
        added += create(
            store,
            kind=change["kind"],
            message=f"{event_label(event)}: {change['detail']} ({change['team']})",
            source="PitchAPI",
            now=now,
            event_id=event["id"],
            fingerprint=f"{change['team']}|{change['detail']}",
            payload=change,
        )
    if result.status == "error":
        added += create(
            store,
            kind="source_error",
            message=f"{event_label(event)}: {result.detail}",
            source="PitchAPI",
            now=now,
            event_id=event["id"],
            fingerprint=f"{result.detail}|{now.date().isoformat()}",
        )
    return added


def absence_notice(store: Store, events: list[dict], *, now: datetime) -> int:
    """TASK 016 §4.4: ONE collective notice a day (Warsaw day) that no verified source of
    injuries and suspensions exists for the matches on coupons — not an alert per match.
    ``events``: the coupon matches of this run without a source and without a manual entry."""
    if not events:
        return 0
    day = now.astimezone(WARSAW).date().isoformat()
    names = ", ".join(event_label(e) for e in events[:3])
    more = f" i {len(events) - 3} innych" if len(events) > 3 else ""
    return int(
        create(
            store,
            kind="no_absence_source",
            message="Brak zweryfikowanego źródła kontuzji i zawieszeń dla meczów z Twoich "
            f"kuponów (m.in. {names}{more}). Absencję możesz wpisać ręcznie w Analizie meczu, "
            "podając źródło.",
            source="Sports Edge AI",
            now=now,
            identity=["no_absence_source", "day", day],
            payload={"day": day, "event_ids": [e["id"] for e in events]},
        )
    )


def recent(store: Store, *, limit: int = 50, unread_only: bool = False) -> list[dict]:
    rows = store.rows(
        """SELECT a.*, e.home, e.away, e.start_at,
        (SELECT MAX(read_at) FROM alert_reads r WHERE r.alert_id=a.id) read_at
        FROM prematch_alerts a LEFT JOIN events e ON e.id=a.event_id
        ORDER BY a.created_at DESC LIMIT ?""",
        (limit * 4 if unread_only else limit,),
    )
    out = []
    for row in rows:
        if unread_only and row["read_at"]:
            continue
        out.append(
            {
                **row,
                "payload": json.loads(row.pop("payload_json") or "{}"),
                "label": LABELS.get(row["kind"], row["kind"]),
            }
        )
    return out[:limit]


def coupon_events(store: Store) -> set[str]:
    """Matches that are on a coupon which counts (official coupons of the day and the
    user's own — never a duplicate, a price update or a revision)."""
    from sports_edge.coupon_store import counted_sql

    return {
        r["event_id"]
        for r in store.rows(
            f"""SELECT DISTINCT m.event_id FROM coupon_legs l
            JOIN coupons c ON c.id=l.coupon_id JOIN selections s ON s.id=l.selection_id
            JOIN markets m ON m.id=s.market_id WHERE {counted_sql("c")}"""
        )
    }


def rank_of(kind: str, event_id: str | None, on_coupons: set[str]) -> int:
    """0 — alerts about coupons; 1 — lineup changes of matches on coupons (and a backup
    problem: nothing else guards the data); 2 — everything else."""
    if kind in COUPON_KINDS:
        return 0
    if kind in SYSTEM_KINDS:
        return 1
    if kind in (GROUP_KIND, "confirmed_published", *LINEUP_KINDS) and event_id in on_coupons:
        return 1
    return 2


def overview(store: Store, *, now: datetime) -> dict:
    """Every alert as the interface shows it (TASK 016 §4): old one-per-change lineup alerts
    of the same match and check are one item, items are ranked (coupons → lineups of coupon
    matches → the rest), an item about a started match is ``stale``. Counts are of unread
    ITEMS: ``important`` (rank 0–1, not stale) and ``unread`` (all)."""
    rows = store.rows(
        # an alert stored without its match (older coupon alerts): the coupon's first match
        """SELECT a.*, e.home, e.away, COALESCE(e.start_at, c.first_start_at) start_at,
        (SELECT MAX(read_at) FROM alert_reads r WHERE r.alert_id=a.id) read_at
        FROM prematch_alerts a LEFT JOIN events e ON e.id=a.event_id
        LEFT JOIN coupons c ON c.id=a.coupon_id
        ORDER BY a.created_at DESC, a.id"""
    )
    on_coupons = coupon_events(store)
    moment = now.isoformat()
    items: dict[tuple, dict] = {}
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        legacy = row["kind"] in LINEUP_KINDS
        key = ("group", row["event_id"], row["created_at"]) if legacy else ("one", row["id"])
        kind = GROUP_KIND if legacy else row["kind"]
        item = items.get(key)
        if item is None:
            start = row["start_at"]
            item = items[key] = {
                "ids": [],
                "kind": kind,
                "label": LABELS.get(kind, kind),
                "message": row["message"],
                "source": row["source"],
                "created_at": row["created_at"],
                "event_id": row["event_id"],
                "coupon_id": row["coupon_id"],
                "priority": row["priority"],
                "changes": list(payload.get("changes") or []) if kind == GROUP_KIND else [],
                "read": True,
                "legacy": legacy,
                "rank": rank_of(kind, row["event_id"], on_coupons),
                "stale": bool(start) and datetime.fromisoformat(start).isoformat() <= moment,
                "match": f"{row['home']} - {row['away']}" if row["home"] else None,
            }
        item["ids"].append(row["id"])
        item["read"] = item["read"] and bool(row["read_at"])
        if ORDER.get(row["priority"], 1) < ORDER.get(item["priority"], 1):
            item["priority"] = row["priority"]
        if legacy:
            item["changes"].append(payload)
    out = []
    for item in items.values():
        if item.pop("legacy"):  # alerts stored one per change: one line with the list
            details = [
                f"{c.get('detail')} ({c.get('team')})" for c in item["changes"] if c.get("detail")
            ]
            if details:
                item["message"] = (
                    f"{item['match'] or 'Mecz'}: zmiany w składzie ({len(details)}): "
                    + "; ".join(details)
                )
        out.append(item)
    out.sort(key=lambda i: i["created_at"], reverse=True)
    out.sort(key=lambda i: (i["rank"], ORDER.get(i["priority"], 1)))
    unread = [i for i in out if not i["read"]]
    archived = archived_ids(store)
    for item in out:
        item["archived"] = any(i in archived for i in item["ids"])
    return {
        "items": out,
        "unread": len(unread),
        # TASK 019 §4.3: what the user can still act on (no started match, not archived)
        "actionable": sum(1 for i in unread if not i["stale"] and not i["archived"]),
        "important": sum(1 for i in unread if i["rank"] <= 1 and not i["stale"]),
        "stale_unread": sum(1 for i in unread if i["stale"]),
        "raw_total": len(rows),
        "raw_unread": sum(1 for r in rows if not r["read_at"]),
    }


def counts_text(found: dict) -> str:
    """ "ważne 3 · wszystkie 12" — the Dashboard and the navigation. TASK 019 §4.3: only the
    alerts the user can still act on; alerts of started matches are archived, not counted."""
    actionable = found.get("actionable", found["unread"])
    if not actionable:
        return "brak nowych"
    return f"ważne {found['important']} · wszystkie {actionable}"


def archived_ids(store: Store) -> set[str]:
    if "alert_states" not in getattr(store, "tables", {"alert_states"}):
        return set()
    return {
        r["alert_id"]
        for r in store.rows("SELECT alert_id FROM alert_states WHERE state='archived'")
    }


def archive_started(store: Store, *, now: datetime) -> int:
    """TASK 019 §4.3: an alert about a match that has already started is archived — a new
    row of ``alert_states`` (the alert and its read marks are never changed or deleted).
    Alerts without a match (system, coupon of the day) are never archived this way."""
    rows = store.rows(
        """SELECT a.id FROM prematch_alerts a JOIN events e ON e.id=a.event_id
        WHERE e.start_at IS NOT NULL AND e.start_at<=?
        AND NOT EXISTS (SELECT 1 FROM alert_states s WHERE s.alert_id=a.id)""",
        (iso(now),),
    )
    done = 0
    with store.transaction():
        for row in rows:
            done += store.insert(
                "alert_states",
                id=digest(["alert_state", row["id"], "archived"]),
                alert_id=row["id"],
                state="archived",
                reason="mecz już się rozpoczął",
                recorded_at=iso(now),
            )
    return done


def mark_all_read(store: Store, *, now: datetime) -> int:
    """ "Oznacz wszystkie jako przeczytane": a row of ``alert_reads`` for EVERY unread alert
    (not only the ones on the screen); the alerts themselves are never changed."""
    ids = [
        r["id"]
        for r in store.rows(
            """SELECT a.id FROM prematch_alerts a
            WHERE NOT EXISTS (SELECT 1 FROM alert_reads r WHERE r.alert_id=a.id)"""
        )
    ]
    with store.transaction():
        return mark_read(store, ids, now=now)


def unread_count(store: Store) -> int:
    return store.scalar(
        """SELECT COUNT(*) FROM prematch_alerts a
        WHERE NOT EXISTS (SELECT 1 FROM alert_reads r WHERE r.alert_id=a.id)"""
    )


def mark_read(store: Store, alert_ids: list[str], *, now: datetime) -> int:
    done = 0
    for alert_id in alert_ids:
        done += store.insert(
            "alert_reads",
            id=digest(["alert_read", alert_id]),
            alert_id=alert_id,
            read_at=iso(now),
        )
    return done
