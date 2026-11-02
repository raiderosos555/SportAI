"""Model history and roles (TASK 007): champion, challenger, shadow, promotion, rollback.

Every decision is a new append-only row of ``model_events``; nothing is ever updated. The
served version (champion) of a model scope and family is the version named by the latest
``activated`` or ``rolled_back`` event; a store without such events serves the newest
version, as before TASK 007. A new version never replaces the champion by itself: it
becomes a challenger, runs in shadow mode (its prospective forecasts are archived but not
served) and is promoted only by an explicit user action. Automatic promotion is off.
"""

import json
from datetime import datetime

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.storage import Store

KINDS = {
    "registered": "Zarejestrowano wersję",
    "holdout_used": "Holdout użyty wcześniej do decyzji",
    "challenger_ready": "Challenger gotowy",
    "rejected": "Odrzucony",
    "activated": "Aktywowany",
    "kept_previous": "Pozostawiono poprzedni model",
    "rolled_back": "Przywrócono starszą wersję",
    "review": "Przegląd modelu",
}
ROLE_KINDS = ("activated", "rolled_back")
CLOSING_KINDS = ("activated", "rolled_back", "rejected", "kept_previous")
HOLDOUT_NOTE = (
    "Holdout od 2024-07-01 był w TASK 005–006 używany do wyboru modeli i statusów, więc "
    "nie jest czystym testem. Status tej wersji jest zawieszony (eksperymentalny) do czasu "
    "oceny w protokole v2 lub oceny prospektywnej."
)


def record(
    store: Store,
    *,
    kind: str,
    actor: str,
    reason: str,
    now: datetime,
    version_id: str | None = None,
    scope: str | None = None,
    family: str | None = None,
    payload: dict | None = None,
    key: list | None = None,
) -> str:
    """Append one event; ``key`` makes it idempotent (the same key is recorded once)."""
    if kind not in KINDS:
        raise ValueError(f"Nieznany rodzaj zdarzenia modelu: {kind}")
    event_id = digest(["model_event", *(key or [kind, version_id, scope, family, iso(now)])])
    if store.get("model_events", event_id) is None:
        store.insert(
            "model_events",
            id=event_id,
            created_at=iso(now),
            model_version_id=version_id,
            scope=scope,
            family=family,
            kind=kind,
            actor=actor,
            reason=reason,
            payload_json=canonical(payload or {}),
        )
    return event_id


def events(store: Store, *, scope: str | None = None, family: str | None = None) -> list[dict]:
    where, args = [], []
    if scope is not None:
        where.append("scope=?")
        args.append(scope)
    if family is not None:
        where.append("family=?")
        args.append(family)
    sql = "SELECT * FROM model_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = store.rows(sql + " ORDER BY created_at, id", tuple(args))
    for row in rows:
        row["payload"] = json.loads(row.pop("payload_json") or "{}")
        row["label"] = KINDS.get(row["kind"], row["kind"])
    return rows


def champion_id(store: Store, scope: str, family: str) -> str | None:
    """Version named by the latest activation or rollback (None: no recorded role)."""
    found = [e for e in events(store, scope=scope, family=family) if e["kind"] in ROLE_KINDS]
    return found[-1]["model_version_id"] if found else None


def served_check(store: Store):
    """``served(version_id, at)``: was the version the champion of its scope at that time?

    From the role events (activation / rollback); a store without them serves every
    version, as before TASK 007. Quality reports use it to leave out shadow forecasts of
    challengers, which were archived but never served."""
    timeline: dict[tuple, list[tuple[datetime, str]]] = {}
    for e in events(store):
        if e["kind"] in ROLE_KINDS:
            timeline.setdefault((e["scope"], e["family"]), []).append(
                (datetime.fromisoformat(e["created_at"]), e["model_version_id"])
            )
    if not timeline:
        return lambda version_id, at: True
    spans: dict[str, list[tuple[datetime, datetime | None]]] = {}
    for items in timeline.values():
        items.sort(key=lambda item: item[0])
        for i, (start, version_id) in enumerate(items):
            end = items[i + 1][0] if i + 1 < len(items) else None
            spans.setdefault(version_id, []).append((start, end))

    def served(version_id: str, at: str | datetime) -> bool:
        moment = datetime.fromisoformat(at) if isinstance(at, str) else at
        return any(
            start <= moment and (end is None or moment < end)
            for start, end in spans.get(version_id, ())
        )

    return served


def has_roles(store: Store) -> bool:
    return bool(
        store.scalar("SELECT COUNT(*) FROM model_events WHERE kind IN ('activated','rolled_back')")
    )


def challengers(store: Store, scope: str, family: str) -> list[str]:
    """Challengers still waiting for a decision (shadow mode), oldest first."""
    found = events(store, scope=scope, family=family)
    closed = {e["model_version_id"] for e in found if e["kind"] in CLOSING_KINDS}
    waiting = []
    for e in found:
        if e["kind"] == "challenger_ready" and e["model_version_id"] not in closed:
            if e["model_version_id"] not in waiting:
                waiting.append(e["model_version_id"])
    return waiting


def ensure_roles(
    store: Store,
    *,
    now: datetime,
    served: dict[tuple[str, str], dict],
    holdout_used: list[str],
) -> int:
    """Record, once, which versions were served before TASK 007 (``served``: version rows
    with ``version_id`` and ``created_at``) and which versions had their holdout used for
    decisions. Idempotent: running it again records nothing."""
    added = 0
    for (scope, family), row in sorted(served.items()):
        if champion_id(store, scope, family) is not None:
            continue
        version_id = row["version_id"]
        record(
            store,
            kind="activated",
            actor="migration",
            reason="Wersja serwowana przed TASK 007 (stan zastany, bez zmiany wyników)",
            now=datetime.fromisoformat(row["created_at"]),  # served since its registration
            version_id=version_id,
            scope=scope,
            family=family,
            key=["seed_activated", scope, family, version_id],
        )
        added += 1
    for version_id in holdout_used:
        key = ["holdout_used", version_id]
        if store.get("model_events", digest(["model_event", *key])) is None:
            record(
                store,
                kind="holdout_used",
                actor="migration",
                reason=HOLDOUT_NOTE,
                now=now,
                version_id=version_id,
                key=key,
            )
            added += 1
    return added


def activate(
    store: Store,
    *,
    scope: str,
    versions: dict[str, str],
    now: datetime,
    reason: str,
    actor: str = "user",
) -> list[str]:
    """Promote challengers (one per family) of a scope. Called only by a user action."""
    return [
        record(
            store,
            kind="activated",
            actor=actor,
            reason=reason,
            now=now,
            version_id=version_id,
            scope=scope,
            family=family,
            payload={"previous": champion_id(store, scope, family)},
        )
        for family, version_id in sorted(versions.items())
    ]


def keep_previous(
    store: Store, *, scope: str, versions: dict[str, str], now: datetime, reason: str
) -> list[str]:
    """The user keeps the champion; the challengers leave shadow mode."""
    return [
        record(
            store,
            kind="kept_previous",
            actor="user",
            reason=reason,
            now=now,
            version_id=version_id,
            scope=scope,
            family=family,
            payload={"champion": champion_id(store, scope, family)},
        )
        for family, version_id in sorted(versions.items())
    ]


def rollback(
    store: Store, *, scope: str, family: str, version_id: str, now: datetime, reason: str
) -> str:
    """Serve an older version again (the newer one stays in the history)."""
    return record(
        store,
        kind="rolled_back",
        actor="user",
        reason=reason,
        now=now,
        version_id=version_id,
        scope=scope,
        family=family,
        payload={"previous": champion_id(store, scope, family)},
    )


def previous_champions(store: Store, scope: str, family: str) -> list[str]:
    """Versions that were served before the current champion, newest first."""
    order = [
        e["model_version_id"]
        for e in events(store, scope=scope, family=family)
        if e["kind"] in ROLE_KINDS
    ]
    current = order[-1] if order else None
    out = []
    for version_id in reversed(order[:-1]):
        if version_id != current and version_id not in out:
            out.append(version_id)
    return out
