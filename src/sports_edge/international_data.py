"""International results, fixtures and coverage (TASK 009, stage 1).

Import order (each step verified by the next one):

1. openfootball/champions-league (CC0) defines the club spellings of the three UEFA cups
   for 2011/12–2025/26, with the country of every club;
2. football-data.org CL 2023/24–2025/26 and PitchAPI CL/EL/ECL 2021/22–2024/25 must match
   these spellings; a different spelling becomes an alias only with FIXTURE EVIDENCE (the
   same day, score and identified opponent in both sources, ``clubs.learn_fixture_aliases``);
3. the seasons covered by one source only (football-data CL 2026/27, PitchAPI EL/ECL
   2025/26–2026/27) may introduce new clubs after that learning step;
4. national teams: openfootball/internationals (calendar years) and football-data.org EURO
   2024 / World Cup 2026, in their own identity space.

Nothing is ever overwritten (append-only results with provenance). Scheduled matches of the
current season are kept as fixture observations (``stat_observations`` kind
``fixture_list``) for the schedule of every club.
"""

import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta

from sports_edge.clubs import learn_fixture_aliases
from sports_edge.datasources.base import SportsResultsProvider
from sports_edge.datasources.competitions import COMPETITIONS, UNAVAILABLE, by_category
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.matching import alias_map
from sports_edge.offers.sources import SourceBlocked
from sports_edge.results_engine import apply_fetched, effective_results, source_family
from sports_edge.storage import Store

EUROPEAN = by_category("european")
NATIONAL = by_category("national")
# (competition, provider id, first and last season/year, new clubs allowed, learn aliases)
CUP_PLAN: tuple[tuple[str, str, int, int, bool, bool], ...] = (
    ("uefa.cl", "openfootball_cups", 2011, 2025, False, False),
    ("uefa.el", "openfootball_cups", 2011, 2025, False, False),
    ("uefa.ecl", "openfootball_cups", 2021, 2025, False, False),
    ("uefa.cl", "football_data_org", 2023, 2025, False, True),
    ("uefa.cl", "football_data_org", 2026, 2026, True, True),
    ("uefa.cl", "pitchapi", 2021, 2026, False, True),
    ("uefa.el", "pitchapi", 2021, 2024, False, True),
    ("uefa.el", "pitchapi", 2025, 2026, True, True),
    ("uefa.ecl", "pitchapi", 2021, 2024, False, True),
    ("uefa.ecl", "pitchapi", 2025, 2026, True, True),
)
NATIONAL_PLAN: tuple[tuple[str, str, int, int, bool, bool], ...] = (
    ("uefa.nl", "openfootball_int", 2018, 2026, False, False),
    ("uefa.euro_q", "openfootball_int", 2010, 2026, False, False),
    ("uefa.euro", "openfootball_int", 2012, 2024, False, False),
    ("uefa.euro", "football_data_org", 2024, 2024, False, True),
    ("fifa.wc", "openfootball_int", 2010, 2026, False, False),
    ("fifa.wc", "football_data_org", 2026, 2026, True, True),
    ("fifa.wc_q", "openfootball_int", 2010, 2026, False, False),
    ("fifa.friendly", "openfootball_int", 2010, 2026, False, False),
)
FIXTURE_KIND = "fixture_list"


def season_window(competition: str, year: int, today: date) -> tuple[date, date]:
    """Club cups: July–June seasons; national teams: calendar years. The whole season is
    requested: played matches become results, scheduled ones the fixture list (a result is
    never accepted for a match that has not started)."""
    del today
    if COMPETITIONS[competition].nations:
        return date(year, 1, 1), date(year + 1, 1, 1)
    return date(year, 7, 1), date(year + 1, 7, 1)


def import_window(
    store: Store,
    provider: SportsResultsProvider,
    competition: str,
    window: tuple[date, date],
    *,
    now: datetime,
    normalizer,
    extra_aliases: dict,
    raw_dir,
    identities=None,
    accept_new_clubs: bool = False,
    learn: bool = False,
) -> dict:
    """One fetch → verification → (fixture-evidence aliases → second pass) → fixtures."""
    family = source_family(provider)
    sport = COMPETITIONS[competition].sport
    # one identity pool per category: a club keeps one spelling in the CL, EL and ECL, a
    # national team one spelling in every national competition (a team plays one match a
    # day, so the pool never mixes two matches)
    pool = by_category(COMPETITIONS[competition].category) or (competition,)
    existing = effective_results(
        store, sport=sport, until=now, competitions=pool, identities=identities
    )
    outcome = provider.fetch(competition, *window, now=now)
    kwargs = {
        "existing": existing,
        "sport": sport,
        "now": now,
        "normalizer": normalizer,
        "raw_dir": raw_dir,
    }
    applied = apply_fetched(
        store,
        provider,
        competition,
        outcome.records,
        outcome.payloads,
        aliases=alias_map(store, extra_aliases),
        accept_new_clubs=False if learn else accept_new_clubs,
        **kwargs,
    )
    learned: list[dict] = []
    if learn and existing and (applied["unmatched"] or accept_new_clubs):
        aliases = alias_map(store, extra_aliases)
        learned = learn_fixture_aliases(
            store,
            outcome.records,
            existing,
            sport=sport,
            normalizer=normalizer,
            aliases=aliases,
            now=now,
            origin=f"fixture_evidence:{family}:{competition}",
        )
    if learn and (learned or accept_new_clubs):  # second pass with the new evidence
        again = apply_fetched(
            store,
            provider,
            competition,
            outcome.records,
            outcome.payloads,
            aliases=alias_map(store, extra_aliases),
            accept_new_clubs=accept_new_clubs,
            **kwargs,
        )
        for key in ("inserted", "duplicates", "confirmations"):
            applied["summary"][key] += again["summary"][key]
        applied["unmatched"] = again["unmatched"]
        applied["conflicts"] += again["conflicts"]
    fixtures = save_fixtures(store, family, competition, outcome.records, now=now)
    return {
        "competition": competition,
        "provider": family,
        "window": [window[0].isoformat(), (window[1] - timedelta(days=1)).isoformat()],
        "requests": outcome.requests,
        "records": len(outcome.records),
        "inserted": applied["summary"]["inserted"],
        "duplicates": applied["summary"]["duplicates"],
        "confirmations": applied["summary"]["confirmations"],
        "conflicts": applied["conflicts"],
        "unmatched": sorted({u["source"] for u in applied["unmatched"]}),
        "incomplete": len(applied["incomplete"]),
        "learned_aliases": learned,
        "uncertain_90": sum(
            1 for r in outcome.records if r.details.get("regulation_certain") is False
        ),
        "fixtures": fixtures,
    }


def save_fixtures(store: Store, family: str, competition: str, records, *, now: datetime) -> int:
    """Scheduled matches of a response as one append-only fixture observation."""
    scheduled = sorted(
        (
            {
                "date": r.match_date,
                "start_at": iso(r.start_at) if r.start_at else None,
                "home": r.home,
                "away": r.away,
                "status": r.status,
                "source_event_id": r.source_event_id,
                "stage": r.details.get("stage"),
                "venue_country": r.details.get("venue_country"),
            }
            for r in records
            if r.status == "scheduled" and r.start_at is not None and r.start_at > now
        ),
        key=lambda f: (f["start_at"], f["home"]),
    )
    if not scheduled:
        return 0
    body = canonical(scheduled)
    key = digest(["fixtures", family, competition, body])
    if store.get("stat_observations", key) is None:
        with store.transaction():
            store.insert(
                "stat_observations",
                id=key,
                kind=FIXTURE_KIND,
                sport="football",
                competition=competition,
                season=None,
                entity_type="competition",
                entity_key=f"{family}:{competition}",
                event_date=scheduled[0]["date"],
                source=family,
                source_ref=None,
                payload_json=body,
                data_version="fixtures-v1",
                quality="complete",
                published_at=None,
                fetched_at=iso(now),
                observed_at=iso(now),
                available_at=iso(now),
                available_basis="observed_at_fetch",
                import_id=None,
            )
    return len(scheduled)


def fixtures(store: Store, competition: str, *, until: datetime) -> list[dict]:
    """Newest fixture list of every source of one competition known by ``until``."""
    out: dict[tuple, dict] = {}
    for row in store.rows(
        """SELECT * FROM stat_observations WHERE kind=? AND competition=? AND available_at<=?
        ORDER BY observed_at""",
        (FIXTURE_KIND, competition, iso(until)),
    ):
        for item in json.loads(row["payload_json"]):
            out[(item["date"], item["home"], item["away"])] = {**item, "source": row["source"]}
    return sorted(out.values(), key=lambda f: (f["start_at"] or f["date"], f["home"]))


def run_plan(
    store: Store,
    providers: dict[str, SportsResultsProvider],
    plan: Sequence[tuple[str, str, int, int, bool, bool]],
    *,
    now: datetime,
    normalizer,
    extra_aliases: dict,
    raw_dir,
    identities=None,
    progress: Callable[[str], None] | None = None,
    refresh_current: bool = True,
) -> list[dict]:
    """Every season of the plan; finished seasons already imported from a source are skipped."""
    today = now.astimezone(UTC).date()
    out = []
    for competition, source, first, last, new_clubs, learn in plan:
        provider = providers.get(source)
        if provider is None or not getattr(provider, "key", True):
            out.append({"competition": competition, "provider": source, "error": "brak klucza"})
            continue
        family = source_family(provider)
        for year in range(first, last + 1):
            window = season_window(competition, year, today)
            if window[0] > today:
                continue
            finished = window[1] <= today
            stored = store.scalar(
                """SELECT COUNT(*) FROM historical_results WHERE competition=? AND provider=?
                AND match_date>=? AND match_date<?""",
                (competition, family, window[0].isoformat(), window[1].isoformat()),
            )
            if stored and (finished or not refresh_current):
                continue
            if progress:
                progress(f"{competition}: {provider.name}, {year}")
            try:
                out.append(
                    import_window(
                        store,
                        provider,
                        competition,
                        window,
                        now=now,
                        normalizer=normalizer,
                        extra_aliases=extra_aliases,
                        raw_dir=raw_dir,
                        identities=identities,
                        accept_new_clubs=new_clubs,
                        learn=learn,
                    )
                )
            except SourceBlocked as error:
                out.append(
                    {
                        "competition": competition,
                        "provider": family,
                        "year": year,
                        "error": str(error),
                    }
                )
    return out


def current_plan(now: datetime) -> list[tuple[str, str, int, int, bool, bool]]:
    """Current seasons only (scheduler): football-data CL, PitchAPI EL/ECL, national year."""
    season = now.year if now.month >= 7 else now.year - 1
    return [
        ("uefa.cl", "football_data_org", season, season, True, True),
        ("uefa.el", "pitchapi", season, season, True, True),
        ("uefa.ecl", "pitchapi", season, season, True, True),
    ]


def national_current_plan(now: datetime) -> list[tuple[str, str, int, int, bool, bool]]:
    year = now.year
    return [
        (code, "openfootball_int", year, year, False, False)
        for code in ("uefa.nl", "uefa.euro_q", "fifa.wc_q", "fifa.friendly")
    ]


def members(store: Store, *, until: datetime) -> set[str]:
    """UEFA members = teams of the Nations League and EURO qualifying (from the data)."""
    rows = effective_results(
        store, sport="football", until=until, competitions=("uefa.nl", "uefa.euro_q")
    )
    return {r[side] for r in rows for side in ("home", "away")}


def coverage(store: Store, *, now: datetime, identities=None) -> list[dict]:
    """Per international competition: sources, seasons, results, current season, STS."""
    out = []
    for code in (*EUROPEAN, *NATIONAL):
        league = COMPETITIONS[code]
        rows = effective_results(
            store, sport="football", until=now, competitions=(code,), identities=identities
        )
        seasons = sorted({r["season"] for r in rows if r.get("season")})
        current = (
            str(now.year)
            if league.nations
            else f"{now.year if now.month >= 7 else now.year - 1}-"
            f"{str((now.year if now.month >= 7 else now.year - 1) + 1)[2:]}"
        )
        events = store.rows(
            f"""SELECT id, home, away, start_at FROM events WHERE bookmaker_id='sts'
            AND sport=? AND league IN ({",".join("?" for _ in league.sts_leagues) or "''"})""",
            (league.sport, *league.sts_leagues),
        )
        out.append(
            {
                "competition": code,
                "name": league.name,
                "kind": league.kind,
                "category": league.category,
                "sources": [p for p, _id in league.sources],
                "verified": league.verified,
                "seasons": seasons,
                "results": len(rows),
                "uncertain_90": sum(1 for r in rows if not r.get("regulation_certain", True)),
                "conflicts": sum(1 for r in rows if r["conflict"]),
                "current_season": current,
                "current_results": sum(1 for r in rows if r.get("season") == current),
                "last_result": max((r["match_date"] for r in rows), default=None),
                "providers": sorted({r["provider_resolved"] for r in rows}),
                "sts_headers": list(league.sts_leagues),
                "sts_events": len(events),
                "fixtures": len(fixtures(store, code, until=now)),
            }
        )
    return out


def unmapped_headers(store: Store) -> list[dict]:
    """STS headers of international or cup football competitions without a catalogue entry —
    observed text only (a candidate for a future mapping, never mapped by guessing)."""
    from sports_edge.datasources.competitions import for_sts_league

    rows = store.rows(
        """SELECT league, COUNT(*) AS n, MIN(start_at) AS first, MAX(start_at) AS last
        FROM events WHERE bookmaker_id='sts' AND sport='football' GROUP BY league"""
    )
    markers = (
        "KLUBOWE",
        "PUCHAR",
        "LIGA NARODÓW",
        "ELIMINACJE",
        "MISTRZOSTWA",
        "EURO",
        "REPREZENTAC",
        "TOWARZYSKIE",
        "MIĘDZYNARODOWE",
        "KING'S CUP",
        "SAFF",
        "CONCACAF",
        "AFRYKI",
    )
    return [
        {"header": r["league"], "events": r["n"], "first": r["first"], "last": r["last"]}
        for r in rows
        if any(m in r["league"].upper() for m in markers)
        and for_sts_league(r["league"], "football") is None
    ]


def p3_status() -> list[dict]:
    """Other cups (P3): checked availability, no pretended integration."""
    return [{"competition": name, "status": reason} for name, reason in UNAVAILABLE.items()]


def cup_monitor(store: Store, paths, *, now: datetime, days: int = 7) -> dict:
    """New STS matches of the mapped cup headers: identification against the cup results and
    the observed international headers without a mapping (observed text only)."""
    from sports_edge.matching import match_participant
    from sports_edge.results_engine import known_names
    from sports_edge.services import club_identities, normalizer, team_aliases

    headers = [h for code in EUROPEAN for h in COMPETITIONS[code].sts_leagues]
    marks = ",".join("?" for _ in headers) or "''"
    events = store.rows(
        f"""SELECT * FROM events WHERE bookmaker_id='sts' AND sport='football'
        AND league IN ({marks}) AND start_at > ? AND start_at <= ?""",
        (*headers, iso(now), iso(now + timedelta(days=days))),
    )
    names, aliases = normalizer(paths), alias_map(store, team_aliases(paths))
    pool = effective_results(
        store,
        sport="football",
        until=now,
        competitions=EUROPEAN,
        identities=club_identities(store, paths, now=now),
    )
    known = known_names(pool, "football", names)
    identified, unmatched = 0, set()
    for event in events:
        found = [
            match_participant(event[s] or "", "football", known, names, aliases)
            for s in ("home", "away")
        ]
        if all(m.status in {"exact", "alias"} for m in found):
            identified += 1
        unmatched |= {
            event[s]
            for s, m in zip(("home", "away"), found, strict=True)
            if m.status not in {"exact", "alias"} and event[s]
        }
    unmapped = unmapped_headers(store)
    return {
        "events": len(events),
        "identified": identified,
        "unmatched": sorted(unmatched),
        "unmapped_headers": [u["header"] for u in unmapped][:20],
    }


EXPECTED = {  # matches per season of the main competition (documented formats)
    ("uefa.cl", "league"): 189,
    ("uefa.cl", "group"): 125,
    ("uefa.el", "league"): 189,
    ("uefa.ecl", "league"): 153,
}
# 2019/20: single quarter- and semi-finals of the Lisbon final tournament (COVID-19)
EXPECTED_SEASON = {("uefa.cl", "2019-20"): 119}


def completeness(store: Store, *, now: datetime) -> list[dict]:
    """Per competition and season: results, 90-minute uncertainty, conflicts, sources."""
    out = []
    for code in (*EUROPEAN, *NATIONAL):
        rows = effective_results(store, sport="football", until=now, competitions=(code,))
        by_season: dict[str, list[dict]] = {}
        for row in rows:
            by_season.setdefault(row.get("season") or "?", []).append(row)
        for season, items in sorted(by_season.items()):
            year = int(season[:4]) if season[:4].isdigit() else None
            fmt = "league" if year and year >= 2024 else "group"
            expected = EXPECTED_SEASON.get((code, season), EXPECTED.get((code, fmt)))
            main = [
                r
                for r in items
                if not str(json.loads(r.get("details_json") or "{}").get("stage", "")).startswith(
                    "qualifying"
                )
            ]
            finished = year is not None and date(year + 1, 7, 1) <= now.date()
            status = "ok"
            if expected and finished and len(main) < expected:
                status = f"braki: {len(main)}/{expected} meczów fazy głównej"
            out.append(
                {
                    "competition": code,
                    "season": season,
                    "results": len(items),
                    "main_phase": len(main),
                    "expected_main": expected,
                    "uncertain_90": sum(1 for r in items if not r.get("regulation_certain", True)),
                    "conflicts": sum(1 for r in items if r["conflict"]),
                    "providers": sorted({r["provider_resolved"] for r in items}),
                    "status": status,
                }
            )
    return out


def sts_suggestions(
    store: Store, paths, *, now: datetime, days_back: int = 14, days_ahead: int = 7
) -> list[dict]:
    """Fixture evidence for STS spellings of cup clubs — a SUGGESTION the user confirms.

    The STS event and a match of the cup sources share the competition, the day, the
    kick-off (±15 minutes when both are known) and the identified opponent on the same side;
    exactly one such match is required, and one STS spelling must always point to the same
    club. STS names are never linked without the user's confirmation."""
    from sports_edge.matching import match_participant
    from sports_edge.results_engine import known_names
    from sports_edge.services import club_identities, normalizer, team_aliases

    names, aliases = normalizer(paths), alias_map(store, team_aliases(paths))
    identities = club_identities(store, paths, now=now)
    pool = effective_results(
        store, sport="football", until=now, competitions=EUROPEAN, identities=identities
    )
    known = known_names(pool, "football", names)

    def canonical(name: str) -> str | None:
        found = match_participant(name or "", "football", known, names, aliases)
        return found.target if found.status in {"exact", "alias"} else None

    votes: dict[str, dict[str, dict]] = {}
    for code in EUROPEAN:
        league = COMPETITIONS[code]
        if not league.sts_leagues:
            continue
        marks = ",".join("?" for _ in league.sts_leagues)
        events = store.rows(
            f"""SELECT * FROM events WHERE bookmaker_id='sts' AND sport='football'
            AND league IN ({marks}) AND start_at >= ? AND start_at <= ?""",
            (
                *league.sts_leagues,
                iso(now - timedelta(days=days_back)),
                iso(now + timedelta(days=days_ahead)),
            ),
        )
        matches = [
            {
                "date": r["match_date"],
                "start_at": r.get("start_at"),
                "home": r["home"],
                "away": r["away"],
                "source": r["provider_resolved"],
            }
            for r in pool
            if r["competition"] == code
        ]
        for item in fixtures(store, code, until=now):
            home, away = canonical(item["home"]), canonical(item["away"])
            if home or away:
                matches.append({**item, "home": home or item["home"], "away": away or item["away"]})
        for event in events:
            if not event["home"] or not event["away"] or not event["start_at"]:
                continue
            sides = {s: canonical(event[s]) for s in ("home", "away")}
            if sum(v is not None for v in sides.values()) != 1:
                continue
            side = next(s for s, v in sides.items() if v)
            other = "away" if side == "home" else "home"
            start = datetime.fromisoformat(event["start_at"])
            found = [
                m
                for m in matches
                if m[side] == sides[side]
                and m["date"] == start.date().isoformat()
                and (
                    not m.get("start_at")
                    or abs((datetime.fromisoformat(m["start_at"]) - start).total_seconds()) <= 900
                )
            ]
            if len({m[other] for m in found}) != 1:
                continue
            target = found[0][other]
            votes.setdefault(event[other], {}).setdefault(
                target,
                {
                    "sts": event[other],
                    "canonical": target,
                    "competition": code,
                    "evidence": f"{event['start_at'][:16]} · przeciwnik {sides[side]} · "
                    f"źródło {found[0].get('source')}",
                },
            )
    return [next(iter(v.values())) for v in votes.values() if len(v) == 1]
