"""Central alert register (TASK 008).

One alert per distinct fact: the id is derived from the kind, the event or coupon and the
fact itself, so the next check of an unchanged situation never repeats an alert. Reading an
alert adds a row to ``alert_reads`` (the alert itself is never modified).
"""

import json
from datetime import datetime

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.storage import Store

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
    """Alerts of one pre-match check: every lineup change, source errors and — for matches
    on coupons — the lack of a verified absence source (once per match)."""
    added = 0
    for change in result.changes:
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
    if event.get("priority") == 1 and result.absence_status == "no_source":
        added += create(
            store,
            kind="no_absence_source",
            message=f"{event_label(event)}: brak wiarygodnych informacji o kontuzjach i "
            "zawieszeniach (żadne zweryfikowane źródło ich nie podaje)",
            source="Sports Edge AI",
            now=now,
            event_id=event["id"],
        )
    return added


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
