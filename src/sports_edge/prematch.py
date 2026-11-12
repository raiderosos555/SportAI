"""Pre-Match Intelligence Engine (TASK 008): lineups, absences and their changes.

Independent of the interface — called by the automation worker, the pages and the tests.
Every observation keeps its source, observation time and availability time, and whether it
was known BEFORE the match started (only such data may ever be used as-of). Nothing is
inferred that the source does not say: a player missing from a lineup is NOT an injury,
and absences without a verified source are reported as "no verified source".

Verified on 02.11.2026 (PitchAPI documentation and a real response):
* ``/v1/matches/{id}/lineups`` → per side ``formation``, ``confirmed`` (False while the
  lineup is the provider's prediction — available up to 48 h before kick-off; True once
  confirmed), ``lineup_type``, ``coach``, ``starters`` and ``subs`` with ``player_id``,
  ``name``, ``shirt_number``, ``position_id`` (starters: formation slot, 11 = goalkeeper;
  substitutes: 0 goalkeeper, 1 defender, 2 midfielder, 3 attacker), ``is_captain``.
* PitchAPI has NO injury or suspension endpoint.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.storage import Store

GOALKEEPER_SLOT = 11  # starters' formation grid: row 1, column 1
SUB_ROLES = {0: "bramkarz", 1: "obrońca", 2: "pomocnik", 3: "napastnik"}
LINEUP_STATUS = {
    "predicted": "przewidywany (prognoza dostawcy)",
    "confirmed": "potwierdzony przez dostawcę",
    "official": "potwierdzony przez oficjalne źródło",
    "not_published": "jeszcze nieopublikowany",
    "no_data": "brak danych",
    "error": "błąd źródła",
}
ABSENCE_REASON = {
    "injury_confirmed": "kontuzja potwierdzona",
    "injury_reported": "kontuzja zgłoszona",
    "suspension": "zawieszenie",
    "doubtful": "występ niepewny",
    "unavailable": "niedostępny",
    "return": "powrót do kadry",
}
# TASK 016 §5.3: an absence entered by the user — always with a source; a withdrawal is
# another row (``quality`` = WITHDRAWN) pointing at the entry it withdraws
USER_REASONS = {
    "kontuzja": "injury_reported",
    "zawieszenie": "suspension",
    "inne": "unavailable",
}
USER_QUALITY = "user"
WITHDRAWN = "withdrawn"
# Default checkpoints before kick-off (configurable in the automation panel).
CHECKPOINTS = (
    ("T-48h", timedelta(hours=48)),
    ("T-24h", timedelta(hours=24)),
    ("T-6h", timedelta(hours=6)),
    ("T-90min", timedelta(minutes=90)),
    ("T-60min", timedelta(minutes=60)),
    ("T-30min", timedelta(minutes=30)),
    ("T-15min", timedelta(minutes=15)),
)


# ---------------------------------------------------------------- lineups


def player(raw: dict, *, starter: bool) -> dict:
    position = raw.get("position_id")
    if starter:
        role = "bramkarz" if position == GOALKEEPER_SLOT else "zawodnik z pola"
    else:
        role = SUB_ROLES.get(position, "nieokreślona")
    return {
        "player_id": raw.get("player_id"),
        "name": raw.get("name"),
        "number": raw.get("shirt_number"),
        "position_id": position,
        "role": role,
        "captain": bool(raw.get("is_captain")),
    }


def parse_side(part: dict | None) -> dict:
    """One side of a PitchAPI lineup response, with an explicit status."""
    part = part or {}
    starters = [player(p, starter=True) for p in part.get("starters") or []]
    subs = [player(p, starter=False) for p in part.get("subs") or []]
    if not starters:
        status = "not_published"
    elif part.get("confirmed"):
        status = "confirmed"
    else:
        status = "predicted"
    return {
        "status": status,
        "formation": part.get("formation"),
        "coach": (part.get("coach") or {}).get("name"),
        "lineup_type": part.get("lineup_type"),
        "starters": starters,
        "subs": subs,
        "complete": len(starters) == 11,
    }


def goalkeeper(side: dict) -> dict | None:
    return next((p for p in side["starters"] if p["role"] == "bramkarz"), None)


def _key(p: dict) -> str:
    return p.get("player_id") or (p.get("name") or "").strip().lower()


def detect_changes(previous: dict | None, current: dict, *, team: str) -> list[dict]:
    """Lineup Change Detection: what changed between two observations of one team.

    A change is a fact about the lineup, never a judgement: a new starter or a different
    goalkeeper is not "bad" by itself."""
    if previous is None or current["status"] in ("not_published", "no_data", "error"):
        return []
    out = []
    if previous["status"] == "predicted" and current["status"] in ("confirmed", "official"):
        out.append(
            {
                "kind": "confirmed_published",
                "team": team,
                "detail": "opublikowano potwierdzony skład",
            }
        )
    old_gk, new_gk = goalkeeper(previous), goalkeeper(current)
    if old_gk and new_gk and _key(old_gk) != _key(new_gk):
        out.append(
            {
                "kind": "goalkeeper_change",
                "team": team,
                "detail": f"bramkarz: {old_gk['name']} → {new_gk['name']}",
                "player": new_gk["name"],
            }
        )
    before = {_key(p): p for p in previous["starters"]}
    after = {_key(p): p for p in current["starters"]}
    bench = {_key(p) for p in current["subs"]}
    for key, p in after.items():
        if key not in before and p["role"] != "bramkarz":
            out.append(
                {
                    "kind": "new_starter",
                    "team": team,
                    "player": p["name"],
                    "detail": f"nowy w wyjściowym składzie: {p['name']}",
                }
            )
    for key, p in before.items():
        if key not in after and p["role"] != "bramkarz":
            moved = key in bench
            out.append(
                {
                    "kind": "moved_to_bench" if moved else "removed_starter",
                    "team": team,
                    "player": p["name"],
                    "detail": f"{p['name']}: "
                    + ("przesunięty na ławkę" if moved else "nie ma go w składzie"),
                }
            )
    if (
        previous.get("formation")
        and current.get("formation")
        and (previous["formation"] != current["formation"])
    ):
        out.append(
            {
                "kind": "formation_change",
                "team": team,
                "detail": f"formacja {previous['formation']} → {current['formation']}",
            }
        )
    return out


def latest_side(
    store: Store, event_id: str, side: str, *, before: str | None = None
) -> dict | None:
    """Newest stored observation of one side (as of ``before``, when given)."""
    rows = store.rows(
        """SELECT * FROM lineup_observations WHERE event_id=? AND side=?
        AND (? IS NULL OR available_at<=?) ORDER BY observed_at DESC, id DESC LIMIT 1""",
        (event_id, side, before, before),
    )
    if not rows:
        return None
    row = rows[0]
    return {
        **row,
        "starters": json.loads(row["starters_json"]),
        "subs": json.loads(row["subs_json"]),
    }


def record_side(
    store: Store,
    *,
    event: dict,
    side: str,
    parsed: dict,
    provider: str,
    provider_match_id: str | None,
    team: str | None,
    now: datetime,
) -> tuple[dict, bool]:
    """Store one observation unless it is identical to the previous one; returns (row, new)."""
    previous = latest_side(store, event["id"], side)
    content = canonical(
        {k: parsed[k] for k in ("status", "formation", "coach", "starters", "subs")}
    )
    content_hash = digest(["lineup", content])
    if previous is not None and previous["content_hash"] == content_hash:
        return previous, False
    start = datetime.fromisoformat(event["start_at"]) if event.get("start_at") else None
    row_id = digest(["lineup_obs", event["id"], side, content_hash, iso(now)])
    store.insert(
        "lineup_observations",
        id=row_id,
        event_id=event["id"],
        side=side,
        provider=provider,
        provider_match_id=provider_match_id,
        team_name=team,
        formation=parsed["formation"],
        status=parsed["status"],
        coach=parsed["coach"],
        starters_json=canonical(parsed["starters"]),
        subs_json=canonical(parsed["subs"]),
        content_hash=content_hash,
        published_at=None,  # the provider gives no publication time
        observed_at=iso(now),
        available_at=iso(now),
        known_before_start=int(start is not None and now < start),
        previous_id=previous["id"] if previous else None,
    )
    return latest_side(store, event["id"], side), True


# ---------------------------------------------------------------- absences


@dataclass
class AbsenceRecord:
    team: str
    player: str
    reason: str  # injury_confirmed | injury_reported | suspension | doubtful | unavailable | return
    source: str
    observed_at: str
    player_id: str | None = None
    source_url: str | None = None
    published_at: str | None = None
    expected_return: str | None = None
    quality: str = "unverified"


@dataclass
class AbsenceResult:
    status: str  # ok | no_source | unavailable | error
    message: str
    records: list[AbsenceRecord] = field(default_factory=list)


class AbsenceProvider:
    """Common interface of absence sources; ``fetch`` never invents a record."""

    id = "base"
    name = "—"

    def fetch(self, event: dict, *, now: datetime) -> AbsenceResult:  # pragma: no cover
        raise NotImplementedError


class NoVerifiedAbsenceSource(AbsenceProvider):
    """The honest default: no legal, free, verified source of CURRENT injuries/suspensions
    was found (PitchAPI has no such endpoint; API-Sports' injuries need a paid plan for the
    current season; scraping club sites without permission is not allowed)."""

    id = "none"
    name = "brak zweryfikowanego źródła"

    def fetch(self, event: dict, *, now: datetime) -> AbsenceResult:
        return AbsenceResult(
            "no_source",
            "Brak zweryfikowanego, legalnego i bezpłatnego źródła bieżących kontuzji i "
            "zawieszeń — aplikacja niczego nie zgaduje. Możesz dodać informację ręcznie, "
            "podając źródło.",
        )


def record_absence(
    store: Store, event: dict, record: AbsenceRecord, *, entered_by: str, now: datetime
) -> tuple[str, str]:
    """Store an absence; the player is matched to observed lineups by id, then by exact name.
    Returns (id, match_status). An uncertain match stays "needs_review" — never guessed."""
    side_rows = [latest_side(store, event["id"], s) for s in ("home", "away")]
    # only the absence's own team: a namesake (or a transferred player) elsewhere never matches
    players = [
        p
        for side in side_rows
        if side and (side["team_name"] or "").strip().lower() == record.team.strip().lower()
        for p in side["starters"] + side["subs"]
    ]
    match_status = "needs_review"
    if record.player_id and any(p.get("player_id") == record.player_id for p in players):
        match_status = "verified"
    else:
        same = [
            p
            for p in players
            if (p.get("name") or "").strip().lower() == record.player.strip().lower()
        ]
        if len(same) == 1:
            match_status = "verified"
            record.player_id = record.player_id or same[0].get("player_id")
    start = datetime.fromisoformat(event["start_at"]) if event.get("start_at") else None
    absence_id = digest(
        [
            "absence",
            event["id"],
            record.team,
            record.player,
            record.reason,
            record.source,
            record.observed_at,
        ]
    )
    existing = store.get("player_absence_observations", absence_id)
    if existing is not None:  # the same report again: stored once
        return absence_id, existing["match_status"]
    previous = store.rows(
        """SELECT id FROM player_absence_observations WHERE event_id=? AND team_name=?
        AND player_name=? ORDER BY observed_at DESC LIMIT 1""",
        (event["id"], record.team, record.player),
    )
    store.insert(
        "player_absence_observations",
        id=absence_id,
        event_id=event["id"],
        team_name=record.team,
        player_name=record.player,
        player_id=record.player_id,
        reason=record.reason,
        source=record.source,
        source_url=record.source_url,
        published_at=record.published_at,
        observed_at=record.observed_at,
        expected_return=record.expected_return,
        quality=record.quality,
        match_status=match_status,
        entered_by=entered_by,
        known_before_start=int(
            start is not None and datetime.fromisoformat(record.observed_at) < start
        ),
        previous_id=previous[0]["id"] if previous else None,
    )
    return absence_id, match_status


def add_user_absence(
    store: Store,
    event: dict,
    *,
    team: str,
    player: str,
    reason: str,
    source: str,
    now: datetime,
    source_url: str | None = None,
    information_date: str | None = None,
) -> str:
    """TASK 016 §5.3: an absence entered by the user. The SOURCE is obligatory (a name and
    an address, or a description such as "komunikat klubu") — without it nothing is stored.
    The entry is append-only and counts as verified "by the user" (``entered_by = 'user'``):
    a warning in the pre-match check and the coupon reassessment, never a model input."""
    team, player, source = (team or "").strip(), (player or "").strip(), (source or "").strip()
    if not source:
        raise ValueError("Podaj źródło informacji o absencji — bez źródła wpis nie jest zapisywany")
    if not player:
        raise ValueError("Podaj zawodnika")
    if team not in {event.get("home"), event.get("away")}:
        raise ValueError("Wybierz drużynę tego meczu")
    code = USER_REASONS.get(reason, reason)
    if code not in ABSENCE_REASON or code == "return":
        raise ValueError("Wybierz powód: kontuzja, zawieszenie albo inne")
    start = datetime.fromisoformat(event["start_at"]) if event.get("start_at") else None
    absence_id = digest(["absence_user", event["id"], team, player, code, source, iso(now)])
    previous = store.rows(
        """SELECT id FROM player_absence_observations WHERE event_id=? AND team_name=?
        AND player_name=? ORDER BY observed_at DESC, rowid DESC LIMIT 1""",
        (event["id"], team, player),
    )
    store.insert(
        "player_absence_observations",
        id=absence_id,
        event_id=event["id"],
        team_name=team,
        player_name=player,
        player_id=None,
        reason=code,
        source=source,
        source_url=(source_url or "").strip() or None,
        published_at=information_date or None,
        observed_at=iso(now),
        expected_return=None,
        quality=USER_QUALITY,
        match_status="verified",
        entered_by="user",
        known_before_start=int(start is not None and now < start),
        previous_id=previous[0]["id"] if previous else None,
    )
    return absence_id


def withdraw_absence(store: Store, absence_id: str, *, now: datetime, note: str = "") -> str:
    """Withdrawing an entry is ANOTHER row (nothing is changed or deleted): the same match,
    team, player and reason, ``quality = 'withdrawn'`` and ``previous_id`` = the entry."""
    row = store.get("player_absence_observations", absence_id)
    if row is None:
        raise ValueError("Nie ma takiego wpisu absencji")
    if row["quality"] == WITHDRAWN:
        raise ValueError("Ten wpis jest już wycofaniem")
    event = store.get("events", row["event_id"]) if row["event_id"] else None
    start = datetime.fromisoformat(event["start_at"]) if event and event.get("start_at") else None
    withdrawal_id = digest(["absence_withdrawal", absence_id, iso(now)])
    store.insert(
        "player_absence_observations",
        id=withdrawal_id,
        event_id=row["event_id"],
        team_name=row["team_name"],
        player_name=row["player_name"],
        player_id=row["player_id"],
        reason=row["reason"],
        source=(note or "").strip() or f"wycofanie wpisu ({row['source']})",
        source_url=row["source_url"],
        published_at=None,
        observed_at=iso(now),
        expected_return=None,
        quality=WITHDRAWN,
        match_status=row["match_status"],
        entered_by="user",
        known_before_start=int(start is not None and now < start),
        previous_id=absence_id,
    )
    return withdrawal_id


def active_absences(store: Store, event_id: str) -> list[dict]:
    """What holds NOW for a match: the newest row of every (team, player); a player whose
    newest row is a withdrawal has no absence. The full history stays in ``absences``."""
    newest: dict[tuple, dict] = {}
    for row in store.rows(
        """SELECT * FROM player_absence_observations WHERE event_id=?
        ORDER BY observed_at, rowid""",
        (event_id,),
    ):
        newest[(row["team_name"], row["player_name"])] = row
    found = [r for r in newest.values() if r["quality"] != WITHDRAWN]
    return sorted(found, key=lambda r: r["observed_at"], reverse=True)


def verification_label(row: dict) -> str:
    if row["entered_by"] == "user":
        return "zweryfikowana przez użytkownika"
    return "zweryfikowany" if row["match_status"] == "verified" else "do sprawdzenia"


def conflicts(store: Store, event_id: str) -> list[dict]:
    """Players reported with different reasons by different sources (source conflict)."""
    seen: dict[tuple, dict] = {}
    out = []
    for row in absences(store, event_id):
        key = (row["team_name"], row["player_name"])
        first = seen.setdefault(key, row)
        if (
            first is not row
            and first["source"] != row["source"]
            and first["reason"] != row["reason"]
        ):
            out.append(
                {
                    "team": key[0],
                    "player": key[1],
                    "reasons": sorted({first["reason"], row["reason"]}),
                    "sources": sorted({first["source"], row["source"]}),
                }
            )
    return out


def absences(store: Store, event_id: str) -> list[dict]:
    return store.rows(
        "SELECT * FROM player_absence_observations WHERE event_id=? ORDER BY observed_at DESC",
        (event_id,),
    )


# ---------------------------------------------------------------- player importance


def importance(store: Store, team: str, player_key: str, *, minimum: int = 5) -> str:
    """Experimental, descriptive only (no probability is changed): how often the player
    started in the team's observed confirmed lineups."""
    rows = store.rows(
        """SELECT starters_json FROM lineup_observations WHERE team_name=?
        AND status IN ('confirmed','official')""",
        (team,),
    )
    if len(rows) < minimum:
        return "niewystarczające dane"
    starts = sum(any(_key(p) == player_key for p in json.loads(r["starters_json"])) for r in rows)
    share = starts / len(rows)
    if share >= 0.6:
        return "potencjalnie istotna"
    if share <= 0.4:
        return "typowa rotacja"
    return "brak podstaw do oceny"


# ---------------------------------------------------------------- one pre-match check


@dataclass
class CheckResult:
    event_id: str
    checkpoint: str
    status: str
    lineup_status: str
    absence_status: str
    changes: list[dict]
    requests: int
    detail: str = ""


def check_event(
    store: Store,
    event: dict,
    *,
    fetch_lineup: Callable[[dict], tuple[str | None, dict | None, int]],
    absence_provider: AbsenceProvider,
    checkpoint: str,
    now: datetime,
    trigger: str = "automation",
    planned_at: datetime | None = None,
) -> CheckResult:
    """One pre-match check of one event: lineups (both sides) and absences, changes since
    the previous observation, stored as a ``prematch_checks`` row. ``fetch_lineup(event)``
    returns (provider match id, raw lineup payload or None, requests made); it may raise."""
    started = datetime.now().astimezone()
    start = datetime.fromisoformat(event["start_at"]) if event.get("start_at") else None
    if start is not None and now >= start:
        return _store_check(
            store,
            event,
            checkpoint,
            planned_at,
            now,
            trigger,
            CheckResult(
                event["id"],
                checkpoint,
                "skipped",
                "—",
                "—",
                [],
                0,
                "mecz już się rozpoczął — kontrola niepotrzebna",
            ),
            started,
        )
    changes: list[dict] = []
    requests = 0
    try:
        match_id, payload, requests = fetch_lineup(event)
    except Exception as error:  # noqa: BLE001 — a source failure is a recorded status
        result = CheckResult(
            event["id"],
            checkpoint,
            "error",
            "error",
            "—",
            [],
            0,
            f"źródło składów: {type(error).__name__}",
        )
        return _store_check(store, event, checkpoint, planned_at, now, trigger, result, started)
    if payload is None:
        lineup_status = "no_data" if match_id is None else "not_published"
    else:
        data = payload.get("data") or {}
        statuses = []
        for side in ("home", "away"):
            parsed = parse_side(data.get(side))
            team = (data.get(f"{side}_team") or {}).get("name") or event.get(side)
            previous = latest_side(store, event["id"], side)
            current, new = record_side(
                store,
                event=event,
                side=side,
                parsed=parsed,
                provider="pitchapi",
                provider_match_id=match_id,
                team=team,
                now=now,
            )
            statuses.append(parsed["status"])
            if new and previous is not None:
                changes += detect_changes(previous, current, team=team)
        lineup_status = (
            "confirmed"
            if all(s == "confirmed" for s in statuses)
            else "predicted"
            if "predicted" in statuses or "confirmed" in statuses
            else "not_published"
        )
    found = absence_provider.fetch(event, now=now)
    for record in found.records:
        record_absence(store, event, record, entered_by="provider", now=now)
    status = (
        "ok"
        if lineup_status in ("confirmed", "predicted")
        else ("not_published" if lineup_status == "not_published" else "no_data")
    )
    result = CheckResult(
        event["id"],
        checkpoint,
        status,
        lineup_status,
        found.status,
        changes,
        requests,
        found.message if found.status != "ok" else "",
    )
    return _store_check(store, event, checkpoint, planned_at, now, trigger, result, started)


def _store_check(store, event, checkpoint, planned_at, now, trigger, result, started):
    duration = int((datetime.now().astimezone() - started).total_seconds() * 1000)
    store.insert(
        "prematch_checks",
        id=digest(["prematch", event["id"], checkpoint, iso(now)]),
        event_id=event["id"],
        checkpoint=checkpoint,
        planned_at=iso(planned_at) if planned_at else None,
        executed_at=iso(now),
        trigger=trigger,
        status=result.status,
        lineup_status=result.lineup_status,
        absence_status=result.absence_status,
        changes_json=canonical(result.changes),
        requests=result.requests,
        duration_ms=max(0, duration),
        detail=result.detail,
    )
    return result


def record_missed(
    store: Store, event: dict, checkpoint: str, planned_at: datetime, now: datetime
) -> None:
    """A checkpoint that passed while the application was off: recorded as missed, never
    replayed with invented past observations."""
    store.insert(
        "prematch_checks",
        id=digest(["prematch", event["id"], checkpoint, "missed"]),
        event_id=event["id"],
        checkpoint=checkpoint,
        planned_at=iso(planned_at),
        executed_at=iso(now),
        trigger="catch_up",
        status="missed",
        detail="pominięty — aplikacja nie działała w tym czasie",
    )


def due_checkpoints(
    store: Store, event: dict, *, now: datetime, checkpoints=CHECKPOINTS
) -> tuple[tuple[str, datetime] | None, list[tuple[str, datetime]]]:
    """(the checkpoint to run now, older passed checkpoints to mark as missed).

    Only the most recent passed checkpoint runs; nothing runs after the kick-off."""
    if not event.get("start_at"):
        return None, []
    start = datetime.fromisoformat(event["start_at"])
    if now >= start:
        return None, []
    done = {
        r["checkpoint"]
        for r in store.rows(
            "SELECT checkpoint FROM prematch_checks WHERE event_id=?", (event["id"],)
        )
    }
    passed = [
        (name, start - offset)
        for name, offset in checkpoints
        if start - offset <= now and name not in done
    ]
    if not passed:
        return None, []
    passed.sort(key=lambda item: item[1])
    return passed[-1], passed[:-1]


def last_check(store: Store, event_id: str) -> dict | None:
    rows = store.rows(
        """SELECT * FROM prematch_checks WHERE event_id=? AND status!='missed'
        ORDER BY executed_at DESC LIMIT 1""",
        (event_id,),
    )
    if not rows:
        return None
    return {**rows[0], "changes": json.loads(rows[0]["changes_json"])}


def summary(store: Store, event_id: str) -> dict:
    """What the interface shows about one event (no network)."""
    sides = {s: latest_side(store, event_id, s) for s in ("home", "away")}
    check = last_check(store, event_id)
    statuses = [sides[s]["status"] for s in sides if sides[s]]
    lineup = (
        "confirmed"
        if statuses and all(s in ("confirmed", "official") for s in statuses)
        else "predicted"
        if statuses and any(s == "predicted" for s in statuses)
        else (check or {}).get("lineup_status") or "no_data"
    )
    return {
        "lineup_status": lineup,
        "lineup_label": LINEUP_STATUS.get(lineup, lineup),
        "sides": sides,
        # TASK 016: what holds now (a withdrawn manual entry is not an absence any more)
        "absences": active_absences(store, event_id),
        "last_check": check,
        "changes": (check or {}).get("changes", []),
        "model_uses_lineups": False,  # Elo and Dixon–Coles do not use lineups or absences
    }
