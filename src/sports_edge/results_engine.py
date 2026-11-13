"""Sports Results Data Engine (TASK 004): incremental, idempotent results updates.

Per competition: last effective result → missing window (with a short overlap to catch
corrections) → fetch from the best active provider → verification → canonical team names
(exact keys or reviewed aliases only) → duplicates, confirmations, corrections, conflicts →
append-only insert → provenance (raw payload file + ``data_imports`` row with the report).

Nothing is overwritten. A correction from the same provider is a new row (the newest
observation wins); disagreement between providers is a conflict that stays visible and is
resolved only through ``SOURCE_PRIORITY``. That hierarchy follows STS R 14.6: the official
organiser first (results entered by the user from the organiser's site), then the documented
API, then community archives. Settlement never uses a conflicted result automatically.
"""

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_edge.datasources.base import ResultRecord, SportsResultsProvider
from sports_edge.datasources.competitions import COMPETITIONS
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.matching import (
    ParticipantMatch,
    TeamIdentities,
    match_participant,
    participant_key,
)
from sports_edge.offers.sources import SourceBlocked
from sports_edge.offers.text import NameNormalizer
from sports_edge.storage import Store

SOURCE_PRIORITY = {
    "manual_official": 1,
    "football_data_org": 2,
    "pitchapi": 3,
    "api_sports": 4,
    "openfootball": 5,
    "openfootball_cups": 5,
    "openfootball_int": 5,
    "csv": 6,
    # TASK 019: a result typed in by the user with its source — the emergency exit when no
    # provider has the match; any provider's later result takes precedence over it
    "manual_entry": 8,
}
# TASK 009: in cups and national-team competitions the score semantics decide the order —
# football-data.org and openfootball/champions-league report the 90-minute score, extra time
# and penalties separately; PitchAPI counts extra-time goals in its score and has no stage.
CUP_SOURCE_PRIORITY = {
    "manual_official": 1,
    "football_data_org": 2,
    "openfootball_cups": 3,
    "openfootball_int": 3,
    "pitchapi": 4,
    "api_sports": 5,
    "csv": 6,
    "manual_entry": 8,
}
SOURCE_LABELS = {
    "manual_official": "oficjalny organizator (wpis użytkownika)",
    "football_data_org": "football-data.org API v4",
    "pitchapi": "PitchAPI",
    "api_sports": "API-Sports",
    "openfootball": "openfootball (CC0)",
    "openfootball_cups": "openfootball/champions-league (CC0)",
    "openfootball_int": "openfootball/internationals (CC0)",
    "csv": "import CSV użytkownika",
    "manual_entry": "wpis ręczny użytkownika (ze źródłem)",
}
MANUAL_ENTRY = "manual_entry"
UNCERTAIN = '"regulation_certain":false'  # canonical JSON of details (no spaces)
OVERLAP_DAYS = 3
FIRST_WINDOW_DAYS = 60
AVAILABILITY_LAG = timedelta(hours=36)
STALE_AFTER_DAYS = 21  # same threshold as sportsdata.MAX_DATA_GAP_DAYS (never raised here)
ACCEPTED = {"exact", "alias", "source"}  # "source": first source of a new competition


def provider_of(row: dict) -> str:
    """Provider of a stored observation (rows before v4 derive it from their import)."""
    if row.get("provider"):
        return row["provider"]
    return "openfootball" if str(row.get("source_name", "")).startswith("openfootball") else "csv"


def priority(provider: str, competition: str | None = None) -> int:
    league = COMPETITIONS.get(competition) if competition else None
    if league is not None and league.category != "domestic":
        return CUP_SOURCE_PRIORITY.get(provider, 9)
    return SOURCE_PRIORITY.get(provider, 9)


def regulation_certain(row: dict) -> bool:
    """False when the stored score may include extra-time goals (never a 90-minute fact)."""
    return UNCERTAIN not in (row.get("details_json") or "")


def source_family(provider: SportsResultsProvider) -> str:
    """Name used in the hierarchy and stored with every row (several adapters, one source:
    e.g. API-Sports football, basketball and hockey adapters are all "api_sports")."""
    return getattr(provider, "family", provider.id)


def effective_results(
    store: Store,
    *,
    sport: str | None,
    until: datetime,
    competitions: Sequence[str] | None = None,
    identities: TeamIdentities | None = None,
) -> list[dict]:
    """One row per match known by ``until``: best provider by hierarchy, newest observation.

    ``sport=None`` covers every sport (the competition code already identifies the sport).

    Each row carries ``providers`` (all sources that reported it), ``conflict`` (sources
    disagree on the score) and ``corrections`` (earlier observations of the chosen source).
    A score that may include extra time (``regulation_certain`` False, TASK 009) is chosen
    only when no source reports the 90-minute score, and it never counts as a conflict.
    """
    sql = """SELECT h.*, d.source_name FROM historical_results h
             JOIN data_imports d ON d.id=h.import_id
             WHERE h.available_at<=?"""
    params: list = [iso(until)]
    if sport is not None:
        sql += " AND h.sport=?"
        params.append(sport)
    if competitions:
        sql += f" AND h.competition IN ({','.join('?' for _ in competitions)})"
        params.extend(competitions)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in store.rows(sql, tuple(params)):
        key = (row["sport"], row["competition"], row["match_date"], row["home"], row["away"])
        groups[key].append(row)
    out = []
    for items in groups.values():
        newest: dict[str, dict] = {}
        for row in items:
            source = provider_of(row)
            stamp = (row["observed_at"] or row["available_at"], row["id"])
            current = newest.get(source)
            if current is None or stamp > (
                current["observed_at"] or current["available_at"],
                current["id"],
            ):
                newest[source] = row
        code = items[0]["competition"]
        certain = [s for s, r in newest.items() if regulation_certain(r)]
        pool = certain or list(newest)
        chosen_source = min(pool, key=lambda s: priority(s, code))
        chosen = dict(newest[chosen_source])
        # TASK 019: the user's entry never blocks a provider's result as a "conflict" — the
        # provider wins and a different entry is reported separately (``manual_differs``)
        compared = [s for s in pool if s != MANUAL_ENTRY] or pool
        scores = {(newest[s]["home_score"], newest[s]["away_score"]) for s in compared}
        manual = newest.get(MANUAL_ENTRY)
        chosen["manual_differs"] = bool(
            manual is not None
            and chosen_source != MANUAL_ENTRY
            and (manual["home_score"], manual["away_score"])
            != (chosen["home_score"], chosen["away_score"])
        )
        chosen["provider_resolved"] = chosen_source
        chosen["providers"] = sorted(newest, key=lambda s: priority(s, code))
        chosen["regulation_certain"] = bool(certain)
        chosen["conflict"] = len(scores) > 1
        chosen["corrections"] = sum(provider_of(r) == chosen_source for r in items) - 1
        out.append(chosen)
    out.sort(key=lambda r: (r["match_date"], r["id"]))
    return apply_identities(out, identities) if identities else out


def apply_identities(rows: list[dict], identities: TeamIdentities) -> list[dict]:
    """Every spelling of a club becomes its most recent spelling (per competition).

    The original spelling stays in ``home_source`` / ``away_source``.
    """
    latest: dict[tuple, tuple[str, str]] = {}
    for row in rows:
        for side in ("home", "away"):
            name = identities.renames.get((row["competition"], row[side]), row[side])
            key = (row["competition"], identities.key(row["competition"], row["sport"], name))
            if key not in latest or row["match_date"] >= latest[key][0]:
                latest[key] = (row["match_date"], name)
    out = []
    for row in rows:
        renamed = dict(row)
        for side in ("home", "away"):
            key = (row["competition"], identities.key(row["competition"], row["sport"], row[side]))
            renamed[f"{side}_source"] = row[side]
            renamed[side] = latest[key][1]
        out.append(renamed)
    return out


def known_names(rows: Sequence[dict], sport: str, normalizer: NameNormalizer) -> dict[str, str]:
    """Normalised key of every spelling seen (current and historical) → current spelling."""
    known: dict[str, str] = {}
    for row in sorted(rows, key=lambda r: r["match_date"], reverse=True):
        for side in ("home", "away"):
            for name in (row[side], row.get(f"{side}_source")):
                if name:
                    known.setdefault(participant_key(name, sport, normalizer), row[side])
    return known


def conflicts(store: Store, *, until: datetime, competitions: Sequence[str] | None = None):
    """Matches whose providers disagree, with every source's newest score."""
    rows = effective_results(store, sport=None, until=until, competitions=competitions)
    found = []
    for row in rows:
        if not row["conflict"]:
            continue
        variants = store.rows(
            """SELECT h.*, d.source_name FROM historical_results h
            JOIN data_imports d ON d.id=h.import_id WHERE h.competition=? AND h.match_date=?
            AND h.home=? AND h.away=? AND h.available_at<=?""",
            (row["competition"], row["match_date"], row["home"], row["away"], iso(until)),
        )
        found.append(
            {
                "competition": row["competition"],
                "match_date": row["match_date"],
                "match": f"{row['home']} - {row['away']}",
                "chosen": f"{row['home_score']}:{row['away_score']} ({row['provider_resolved']})",
                "variants": sorted(
                    {f"{v['home_score']}:{v['away_score']} ({provider_of(v)})" for v in variants}
                ),
                "rule": "hierarchia źródeł: " + " > ".join(SOURCE_PRIORITY),
            }
        )
    return found


def league_freshness(store: Store, competitions: Sequence[str], *, now: datetime) -> list[dict]:
    """Last effective result per league and its age against the stale-data threshold."""
    rows = effective_results(store, sport=None, until=now, competitions=competitions)
    out = []
    for code in competitions:
        league = COMPETITIONS.get(code)
        mine = [r for r in rows if r["competition"] == code]
        last = max((r["match_date"] for r in mine), default=None)
        today = now.astimezone(ZoneInfo(league.zone if league else "UTC")).date()
        age = (today - date.fromisoformat(last)).days if last else None
        out.append(
            {
                "competition": code,
                "sport": league.sport if league else None,
                "name": league.name if league else code,
                "matches": len(mine),
                "last_result": last,
                "days_since": age,
                "fresh": age is not None and age <= STALE_AFTER_DAYS,
                "providers": dict(Counter(r["provider_resolved"] for r in mine)),
                "availability_basis": sorted({r["available_at_basis"] for r in mine}),
                "conflicts": sum(r["conflict"] for r in mine),
            }
        )
    return out


def update_results(
    store: Store,
    providers: Sequence[SportsResultsProvider],
    *,
    competitions: Sequence[str],
    now: datetime,
    normalizer: NameNormalizer,
    aliases: dict[tuple[str, str], str],
    offline: bool,
    raw_dir: Path,
    progress: Callable[[str], None] | None = None,
    identities: TeamIdentities | None = None,
) -> dict:
    """Fetch only the missing window of every competition from the best active provider.

    When the preferred provider fails (network, quota, plan), the next active legal provider
    for the same competition is tried; limits and licences are never worked around.
    """
    if len({p.id for p in providers}) != len(providers):
        raise ValueError("Każdy dostawca wyników musi mieć unikalny identyfikator")
    statuses = {p.id: p.status(offline=offline) for p in providers}
    active = sorted(
        (p for p in providers if statuses[p.id].active), key=lambda p: priority(source_family(p))
    )
    report: dict = {
        "started_at": iso(now),
        "providers": {
            p.id: {"active": s.active, "reason": s.reason}
            for p, s in ((p, statuses[p.id]) for p in providers)
        },
        "competitions": {},
        "inserted": 0,
        "duplicates": 0,
        "confirmations": 0,
        "corrections": [],
        "conflicts": [],
        "unmatched": [],
        "incomplete": [],
        "status_records": [],
        "errors": [],
        "requests": 0,
    }
    known_rows = effective_results(
        store, sport=None, until=now, competitions=competitions, identities=identities
    )
    for code in competitions:
        mine = [r for r in known_rows if r["competition"] == code]
        candidates = [p for p in active if code in p.competitions]
        provider = candidates[0] if candidates else None
        last = max((r["match_date"] for r in mine), default=None)
        league = COMPETITIONS.get(code)
        today = now.astimezone(ZoneInfo(league.zone if league else "UTC")).date()
        date_from = (
            date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS)
            if last
            else today - timedelta(days=FIRST_WINDOW_DAYS)
        )
        date_to = today + timedelta(days=1)
        entry = {
            "last_before": last,
            "window": [date_from.isoformat(), (date_to - timedelta(days=1)).isoformat()],
            "provider": provider.id if provider else None,
        }
        report["competitions"][code] = entry
        if provider is None:
            entry["status"] = "brak aktywnego źródła"
            continue
        outcome, failures = None, []
        for candidate in candidates:
            if progress:
                progress(
                    f"{code}: {candidate.name}, okres {entry['window'][0]} – {entry['window'][1]}"
                )
            try:
                outcome = candidate.fetch(code, date_from, date_to, now=now)
            except SourceBlocked as error:
                failures.append(f"{candidate.name}: {error}")
                report["errors"].append(f"{code}: {candidate.name}: {error}")
                continue  # fallback to the next legal provider for this competition
            provider = candidate
            break
        entry["provider"] = provider.id
        if failures:
            entry["fallback"] = failures
        if outcome is None:
            entry["status"] = "błąd: " + " | ".join(failures)
            continue  # earlier competitions stay saved; a rerun resumes from their new state
        report["requests"] += outcome.requests
        applied = _apply(
            store,
            provider,
            code,
            outcome.records,
            outcome.payloads,
            existing=mine,
            sport=league.sport if league else "football",
            now=now,
            normalizer=normalizer,
            aliases=aliases,
            raw_dir=raw_dir,
        )
        entry.update(applied["summary"])
        for key in ("inserted", "duplicates", "confirmations"):
            report[key] += applied["summary"][key]
        for key in ("corrections", "conflicts", "unmatched", "incomplete", "status_records"):
            report[key].extend(applied[key])
    report["freshness"] = league_freshness(store, competitions, now=now)
    report["finished_at"] = iso(datetime.now(UTC))
    return report


def backfill_results(
    store: Store,
    provider: SportsResultsProvider,
    competition: str,
    windows: Sequence[tuple[date, date]],
    *,
    now: datetime,
    normalizer: NameNormalizer,
    aliases: dict[tuple[str, str], str],
    raw_dir: Path,
    identities: TeamIdentities | None = None,
    progress: Callable[[str], None] | None = None,
    accept_new_clubs: bool = False,
) -> dict:
    """Past seasons of one competition from one legal source (TASK 006).

    Each window (usually one season) is one request batch and goes through the same
    verification as a regular update: name matching, duplicates, corrections, conflicts and
    the availability rule. Rows are only added, never changed.
    """
    league = COMPETITIONS[competition]
    report: dict = {
        "competition": competition,
        "provider": provider.id,
        "inserted": 0,
        "duplicates": 0,
        "confirmations": 0,
        "unmatched": [],
        "conflicts": [],
        "errors": [],
        "requests": 0,
        "windows": [],
    }
    for date_from, date_to in windows:
        if progress:
            progress(f"{competition}: {provider.name}, {date_from} – {date_to - timedelta(days=1)}")
        existing = effective_results(
            store, sport=league.sport, until=now, competitions=(competition,), identities=identities
        )
        try:
            outcome = provider.fetch(competition, date_from, date_to, now=now)
        except SourceBlocked as error:
            report["errors"].append(f"{date_from}: {error}")
            continue
        report["requests"] += outcome.requests
        applied = _apply(
            store,
            provider,
            competition,
            outcome.records,
            outcome.payloads,
            existing=existing,
            sport=league.sport,
            now=now,
            normalizer=normalizer,
            aliases=aliases,
            raw_dir=raw_dir,
            accept_new_clubs=accept_new_clubs,
        )
        summary = applied["summary"]
        report["windows"].append(
            {"from": date_from.isoformat(), "to": date_to.isoformat(), **summary}
        )
        for key in ("inserted", "duplicates", "confirmations"):
            report[key] += summary[key]
        report["unmatched"].extend(applied["unmatched"])
        report["conflicts"].extend(applied["conflicts"])
    return report


def apply_fetched(
    store: Store,
    provider: SportsResultsProvider,
    competition: str,
    records: list[ResultRecord],
    payloads: list[tuple[str, bytes]],
    **kwargs,
) -> dict:
    """Public entry of the verification and storage step (TASK 009 importers)."""
    return _apply(store, provider, competition, records, payloads, **kwargs)


def _apply(
    store: Store,
    provider: SportsResultsProvider,
    code: str,
    records: list[ResultRecord],
    payloads: list[tuple[str, bytes]],
    *,
    existing: list[dict],
    sport: str,
    now: datetime,
    normalizer: NameNormalizer,
    aliases: dict[tuple[str, str], str],
    raw_dir: Path,
    accept_new_clubs: bool = False,
) -> dict:
    """Verify, map and store one provider response for one competition.

    Without any history in the competition (e.g. Ekstraklasa, NBA) the first source's names
    become the canonical names ("source" confidence). Afterwards the source that defines the
    competition's names (most rows) may introduce new clubs — promoted teams — with names
    unlike any known one; every other source must match the names or use a reviewed alias.
    ``accept_new_clubs`` extends that to a reviewed history import of another source.
    """
    family = source_family(provider)
    body = b"\n".join(data for _url, data in payloads)
    sha = hashlib.sha256(body).hexdigest()
    import_id = digest(["data_import", sha, "results_api"])
    out: dict = {
        "corrections": [],
        "conflicts": [],
        "unmatched": [],
        "incomplete": [],
        "status_records": [],
    }
    summary = {
        "status": "ok",
        "records": len(records),
        "inserted": 0,
        "duplicates": 0,
        "confirmations": 0,
    }
    out["summary"] = summary
    # An identical payload is re-processed record by record (row ids deduplicate): records
    # skipped earlier, e.g. before the user confirmed an alias, can still be imported.
    seen_payload = store.get("data_imports", import_id) is not None
    known = known_names(existing, sport, normalizer)
    countries = _countries(existing)
    bootstrap = not existing
    owner = Counter(provider_of(r) for r in existing).most_common(1)[0][0] if existing else family
    introduces = accept_new_clubs or owner == family
    by_match = {(r["match_date"], r["home"], r["away"]): r for r in existing}
    by_source = {
        r["source_event_id"]: r
        for r in existing
        if r.get("source_event_id") and provider_of(r) == family
    }
    rows = []
    for record in records:
        label = f"{record.home} - {record.away} ({record.match_date})"
        if not record.complete and record.status not in {"postponed", "cancelled", "suspended"}:
            if record.status in {"finished", "awarded"}:
                why = record.details.get("inconsistent") or "status zakończony bez pełnego wyniku"
                out["incomplete"].append(f"{label}: {why}")
            continue
        if not record.match_date:
            out["incomplete"].append(f"{label}: brak daty meczu")
            continue
        if record.complete and record.start_at and record.start_at > now:
            out["incomplete"].append(f"{label}: wynik dla meczu, który jeszcze się nie zaczął")
            continue
        if bootstrap:
            home = ParticipantMatch(record.home, "source", record.home, 1.0)
            away = ParticipantMatch(record.away, "source", record.away, 1.0)
        else:
            home = match_participant(record.home, sport, known, normalizer, aliases)
            away = match_participant(record.away, sport, known, normalizer, aliases)
            home = _other_country(home, record.details.get("home_country"), countries)
            away = _other_country(away, record.details.get("away_country"), countries)
            if introduces:  # a club unlike every known name is a new club of this source
                if home.status == "none":
                    home = ParticipantMatch(record.home, "source", record.home, 1.0)
                if away.status == "none":
                    away = ParticipantMatch(record.away, "source", record.away, 1.0)
        if not record.complete:  # postponed / cancelled / suspended: event status only
            if home.status in ACCEPTED and away.status in ACCEPTED:
                out["status_records"].append(
                    {
                        "competition": code,
                        "home": home.target,
                        "away": away.target,
                        "start_at": iso(record.start_at) if record.start_at else None,
                        "match_date": record.match_date,
                        "status": record.status,
                        "provider": family,
                        "source_event_id": record.source_event_id,
                    }
                )
            continue
        if home.status not in ACCEPTED or away.status not in ACCEPTED:
            for side, found in (("home", home), ("away", away)):
                if found.status not in ACCEPTED:
                    out["unmatched"].append(
                        {
                            "source": getattr(record, side),
                            "competition": code,
                            "status": found.status,
                            "suggestion": found.target,
                            "score": found.score,
                        }
                    )
            continue
        available, basis = _availability(record)
        if available > now:
            out["incomplete"].append(f"{label}: wynik niedostępny według reguły dostępności")
            continue
        identity = [
            "hist",
            family,
            record.source_event_id or [record.match_date, home.target, away.target],
            record.home_score,
            record.away_score,
            record.status,
        ]
        if "regulation_certain" in record.details:  # TASK 009: a re-assessed cup score is new
            identity.append(["regulation", record.details["regulation_certain"]])
        row_id = digest(identity)
        if store.get("historical_results", row_id) is not None:
            summary["duplicates"] += 1
            continue
        score = (record.home_score, record.away_score)
        same = by_source.get(record.source_event_id) if record.source_event_id else None
        other = by_match.get((record.match_date, home.target, away.target))
        if same is not None and (same["home_score"], same["away_score"]) != score:
            out["corrections"].append(
                f"{label}: {same['home_score']}:{same['away_score']} → {score[0]}:{score[1]}"
            )
        elif other is not None and provider_of(other) != family:
            if (other["home_score"], other["away_score"]) == score:
                summary["confirmations"] += 1
            elif record.details.get("regulation_certain") is False or not other.get(
                "regulation_certain", True
            ):
                pass  # a score that may include extra time is no evidence against the 90'
            else:
                out["conflicts"].append(
                    {
                        "match": f"{home.target} - {away.target}",
                        "match_date": record.match_date,
                        "existing": f"{other['home_score']}:{other['away_score']} "
                        f"({provider_of(other)})",
                        "new": f"{score[0]}:{score[1]} ({family})",
                    }
                )
        rows.append(
            {
                "id": row_id,
                "import_id": import_id,
                "sport": sport,
                "competition": code,
                "season": record.season,
                "match_date": record.match_date,
                "home": home.target,
                "away": away.target,
                "home_score": record.home_score,
                "away_score": record.away_score,
                "available_at": iso(available),
                "available_at_basis": basis,
                "start_at": iso(record.start_at) if record.start_at else None,
                "details_json": canonical(record.details),
                "provider": family,
                "source_event_id": record.source_event_id,
                "status": record.status,
                "home_raw": record.home,
                "away_raw": record.away,
                "match_confidence": f"{home.status}/{away.status}",
                "published_at": iso(record.published_at) if record.published_at else None,
                "observed_at": iso(record.observed_at or now),
            }
        )
    summary["inserted"] = len(rows)
    summary.update(
        corrections=len(out["corrections"]),
        conflicts=len(out["conflicts"]),
        unmatched=len(out["unmatched"]),
        incomplete=len(out["incomplete"]),
    )
    if seen_payload:
        summary["status"] = (
            "identyczna odpowiedź źródła — przetworzono ponownie" if rows else "bez zmian"
        )
        summary["files"] = []
    else:
        summary["files"] = _save_payloads(raw_dir, provider.id, code, payloads, now)
    with store.transaction():
        if not seen_payload:
            store.insert(
                "data_imports",
                id=import_id,
                source_name=provider.name,
                license_note=provider.license,
                file_sha256=sha,
                file_name=";".join(summary["files"]),
                kind="results_api",
                rows=len(rows),
                ingested_at=iso(now),
                summary_json=canonical({"competition": code, **summary}),
            )
        for row in rows:
            store.insert("historical_results", **row)
    return out


def _countries(rows: list[dict]) -> dict[str, str]:
    """Country of every club spelling whose source prints it (openfootball cups, TASK 009)."""
    out: dict[str, str] = {}
    for row in rows:
        text = row.get("details_json") or ""
        if "_country" not in text:
            continue
        details = json.loads(text)
        for side in ("home", "away"):
            if details.get(f"{side}_country"):
                out.setdefault(row[side], details[f"{side}_country"])
    return out


def _other_country(found: ParticipantMatch, country: str | None, countries: dict[str, str]):
    """A similar spelling of a club from ANOTHER country is a different club (FK Partizani,
    Albania ≠ FK Partizan, Serbia) — never a candidate for the same identity."""
    if found.status == "candidate" and country and countries.get(found.target, country) != country:
        return ParticipantMatch(found.name, "none", None, found.score)
    return found


def _availability(record: ResultRecord) -> tuple[datetime, str]:
    """Source timestamp if any; else the 36-hour assumption, but never later than the moment
    this application itself observed the final result (an observed fact, not a guess)."""
    if record.published_at is not None:
        return record.published_at, "source_last_updated"
    midnight = datetime.combine(date.fromisoformat(record.match_date), time(0), UTC)
    assumed = max(midnight, record.start_at or midnight) + AVAILABILITY_LAG
    if record.observed_at is not None and record.observed_at < assumed:
        return record.observed_at, "observed_at_fetch"
    return assumed, "assumed_lag_36h"


def _save_payloads(
    raw_dir: Path, provider: str, code: str, payloads: list[tuple[str, bytes]], now: datetime
) -> list[str]:
    folder = raw_dir / provider
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for index, (_url, data) in enumerate(payloads):
        # the content hash keeps several responses of one second (season by season) apart
        short = hashlib.sha256(data).hexdigest()[:10]
        path = folder / f"{now:%Y-%m-%d_%H-%M-%S}_{code}_{index}_{short}.json"
        path.write_bytes(data)
        names.append(path.name)
    return names
