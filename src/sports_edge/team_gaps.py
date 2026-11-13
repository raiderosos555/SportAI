"""Completeness of a national team's recent results (TASK 019 §1.4).

The freshness rule of the national pool (newest result ≤ 150 days, ``national_scope.toml``)
cannot see a missing FIFA window: in November 2026 the pool knows nothing of September and
October, yet every team "has" a result younger than 150 days. Per team this module measures:

* ``last_match`` — the date of the team's newest result in the database;
* ``missing`` — matches that certainly took place and have no result: an STS event of a
  national competition (the offer archive) or a fixture of a stored fixture list, after the
  team's last result and finished by the moment of the measurement, without a postponed /
  cancelled status — never a guess;
* ``uncovered_days`` — the period between the team's last result and the first day covered
  by the STS archive or a fixture list: nothing is known about it (``brak danych``, not 0).

The status is ``missing`` (N ≥ 1), ``unknown`` (uncovered longer than
``[completeness] unknown_gap_days``) or ``complete``. It is a warning only; whether such
selections reach the profiles that require validation is the USER's decision
(``decisions.NATIONAL_GAPS``) — until it is taken nothing changes.
"""

import json
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sports_edge.datasources.competitions import COMPETITIONS
from sports_edge.ingestion.types import iso
from sports_edge.pools import NATIONAL_CODES, national_config
from sports_edge.repository import EVENT_DURATION
from sports_edge.results_engine import effective_results, known_names
from sports_edge.storage import Store

WARSAW = ZoneInfo("Europe/Warsaw")
ALLOW, EXCLUDE = "allow", "exclude"
RULES = {
    ALLOW: "dopuszczać (jak dotąd) — tylko ostrzeżenie",
    EXCLUDE: "nie dopuszczać do profili wymagających walidacji (typ jak eksperymentalny)",
}
DEFAULT_UNKNOWN_GAP_DAYS = 45


def unknown_gap_days() -> int:
    """Uncovered period after which the team's recent matches are "unknown" (config)."""
    found = national_config().get("completeness") or {}
    return int(found.get("unknown_gap_days", DEFAULT_UNKNOWN_GAP_DAYS))


def _headers() -> dict[str, str]:
    return {h: code for code in NATIONAL_CODES for h in COMPETITIONS[code].sts_leagues}


def _local_day(moment: str) -> str:
    return datetime.fromisoformat(moment).astimezone(WARSAW).date().isoformat()


def measure(
    store: Store, *, now: datetime, normalizer, aliases: dict, identities=None
) -> dict[str, dict]:
    """{history name of a team: completeness} for every national team of the pool."""
    from sports_edge.international_data import FIXTURE_KIND
    from sports_edge.matching import match_participant

    rows = effective_results(
        store, sport="football", until=now, competitions=NATIONAL_CODES, identities=identities
    )
    last: dict[str, str] = {}
    played: set[tuple[str, str]] = set()
    for row in rows:
        for side in ("home", "away"):
            team = row[side]
            played.add((team, row["match_date"]))
            if row["match_date"] > last.get(team, ""):
                last[team] = row["match_date"]
    known = known_names(rows, "football", normalizer)
    headers = _headers()
    marks = ",".join("?" for _ in headers) or "''"
    events = store.rows(
        f"""SELECT e.*, (SELECT r.status FROM results r WHERE r.event_id=e.id
        ORDER BY r.recorded_at DESC, r.id DESC LIMIT 1) result_status
        FROM events e WHERE e.bookmaker_id='sts' AND e.sport='football'
        AND e.league IN ({marks}) AND e.start_at IS NOT NULL""",
        tuple(headers),
    )
    duration = EVENT_DURATION["football"]
    coverage: list[str] = [_local_day(e["start_at"]) for e in events]
    finished: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()
    for event in events:
        if datetime.fromisoformat(event["start_at"]) + duration > now:
            continue
        if event["result_status"] in {"postponed", "cancelled", "void", "abandoned"}:
            continue
        day = _local_day(event["start_at"])
        for side in ("home", "away"):
            found = match_participant(event[side] or "", "football", known, normalizer, aliases)
            if found.status not in {"exact", "alias"} or (found.target, day) in seen:
                continue
            seen.add((found.target, day))
            finished.setdefault(found.target, []).append(
                {
                    "date": day,
                    "event": f"{event['home']} - {event['away']}",
                    "basis": "archiwum oferty STS",
                }
            )
    today = now.astimezone(WARSAW).date().isoformat()
    for code in NATIONAL_CODES:
        for row in store.rows(
            "SELECT payload_json FROM stat_observations WHERE kind=? AND competition=? "
            "AND available_at<=?",
            (FIXTURE_KIND, code, iso(now)),
        ):
            for item in json.loads(row["payload_json"]):
                coverage.append(item["date"])
                if item["date"] >= today:
                    continue
                for side in ("home", "away"):
                    team = item[side]
                    if (team, item["date"]) in seen:
                        continue
                    seen.add((team, item["date"]))
                    finished.setdefault(team, []).append(
                        {
                            "date": item["date"],
                            "event": f"{item['home']} - {item['away']}",
                            "basis": "terminarz",
                        }
                    )
    covered_from = min(coverage) if coverage else None
    limit = unknown_gap_days()
    out: dict[str, dict] = {}
    for team in sorted(set(last) | set(finished)):
        newest = last.get(team)
        missing = sorted(
            (
                m
                for m in finished.get(team, [])
                if (newest is None or m["date"] > newest) and (team, m["date"]) not in played
            ),
            key=lambda m: m["date"],
        )
        uncovered = None
        if newest and covered_from and covered_from > newest:
            uncovered = (date.fromisoformat(covered_from) - date.fromisoformat(newest)).days
        elif newest and not covered_from:
            uncovered = (now.astimezone(WARSAW).date() - date.fromisoformat(newest)).days
        status = (
            "missing"
            if missing
            else "unknown"
            if uncovered is not None and uncovered > limit
            else "complete"
        )
        out[team] = {
            "team": team,
            "last_match": newest,
            "missing": len(missing),
            "missing_matches": missing,
            "covered_from": covered_from,
            "uncovered_days": uncovered,
            "status": status,
        }
    return out


def _matches(n: int) -> str:
    return "ostatniego meczu" if n == 1 else "ostatnich meczów"


def badge(info: dict | None, name: str | None = None) -> str | None:
    """The warning shown next to a selection and in Analiza meczu (None: nothing to say)."""
    if not info or info["status"] == "complete":
        return None
    who = f" ({name})" if name else ""
    if info["status"] == "missing":
        text = (
            f"Model nie zna {info['missing']} {_matches(info['missing'])} drużyny{who} — "
            f"ostatni mecz w bazie: {_day(info['last_match'])}"
        )
        if (info.get("uncovered_days") or 0) > unknown_gap_days():
            text += (
                f"; o okresie do {_day(info['covered_from'])} brak danych "
                f"({info['uncovered_days']} dni)"
            )
        return text
    return (
        f"Model może nie znać ostatnich meczów drużyny{who}: ostatni mecz w bazie "
        f"{_day(info['last_match'])}, brak terminarza i archiwum oferty do "
        f"{_day(info['covered_from'])} (brak danych o {info['uncovered_days']} dniach)"
    )


def _day(value: str | None) -> str:
    return date.fromisoformat(value).strftime("%d.%m.%Y") if value else "brak danych"


def flagged(info: dict | None) -> bool:
    return bool(info) and info["status"] in {"missing", "unknown"}


def context(store: Store, paths, *, now: datetime) -> dict:
    """Everything the warnings need, computed once (completeness, names, Polish names)."""
    from sports_edge.international import polish_names
    from sports_edge.matching import alias_map
    from sports_edge.services import normalizer, team_aliases

    names = normalizer(paths)
    aliases = alias_map(store, team_aliases(paths))
    rows = effective_results(store, sport="football", until=now, competitions=NATIONAL_CODES)
    return {
        "gaps": measure(store, now=now, normalizer=names, aliases=aliases),
        "known": known_names(rows, "football", names),
        "names": names,
        "aliases": aliases,
        "polish": polish_names(store, paths, now=now),
    }


def event_teams(event: dict, ctx: dict) -> list:
    """History names of the two teams of an STS national event (None when unmatched)."""
    from sports_edge.matching import match_participant

    out = []
    for side in ("home", "away"):
        found = match_participant(
            event.get(side) or "", "football", ctx["known"], ctx["names"], ctx["aliases"]
        )
        out.append(found.target if found.status in {"exact", "alias"} else None)
    return out


def event_gaps(event: dict, ctx: dict) -> dict:
    """{"home"/"away": completeness of the team or None, "text": warnings, "flagged"}."""
    teams = event_teams(event, ctx)
    out: dict = {"texts": [], "flagged": False}
    for side, team in zip(("home", "away"), teams, strict=True):
        info = ctx["gaps"].get(team) if team else None
        out[side] = info
        if flagged(info):
            out["flagged"] = True
            out["texts"].append(badge(info, ctx["polish"].get(team, team)))
    out["text"] = "; ".join(t for t in out["texts"] if t)
    return out


def apply_rule(store: Store, paths, settings, candidates: list, *, now: datetime) -> list:
    """Every national selection gets the team-data warning; under the user's rule "exclude"
    a VALIDATED selection of a flagged team becomes experimental (only profiles without the
    validation requirement). Default ("allow"): the warning only — nothing else changes."""
    national = {
        x.event_id
        for x in candidates
        if x.p_model is not None and x.league in _headers() and x.sport == "football"
    }
    if not national:
        return candidates
    ctx = context(store, paths, now=now)
    rule = getattr(settings, "national_gap_rule", ALLOW)
    notes: dict[str, tuple[str, bool]] = {}
    for event_id in national:
        event = store.get("events", event_id) or {}
        found = event_gaps(event, ctx)
        notes[event_id] = (found["text"], found["flagged"])
    out = []
    for x in candidates:
        text, flag = notes.get(x.event_id, ("", False))
        if not text or x.p_model is None:
            out.append(x)
            continue
        changed = replace(x, data_gaps=text)
        if rule == EXCLUDE and flag and x.predictive_status == "validated_predictive":
            changed = replace(
                changed,
                predictive_status="experimental",
                status_note="decyzja użytkownika: reprezentacja z brakującymi wynikami "
                "ostatniego okna — tylko profile bez wymogu walidacji",
            )
        out.append(changed)
    return out
