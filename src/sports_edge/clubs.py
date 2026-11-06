"""One club across competitions (TASK 009): Real Madrid of LaLiga = Real Madrid of the CL.

Layers are kept apart:

* the RESULTS keep every source spelling (append-only, ``home_raw``);
* a club's GLOBAL identity links a cup spelling to the canonical spelling of the domestic
  league (``Link``) — only with evidence, never by similarity:
  1. the same provider's own team id in a domestic match and in a cup match
     (football-data.org, PitchAPI ids are global),
  2. the same normalised name AND the same country (openfootball/champions-league prints
     the country of every club: ``Real Madrid CF (ESP)``),
  3. a reviewed or fixture-evidenced alias (``aliases``) and the same country;
* season participation stays in the results of each competition; ratings and model
  parameters stay per pool — a domestic Elo is never moved into a cup model without the
  calibration tested in ``international``;
* national teams live in their own namespace (``nation:``) and are never linked to clubs.

``learn_fixture_aliases`` turns the overlap of two sources into evidence: when a source
reports a match on the same day, with the same score and the same (already identified)
opponent on the same side, the unknown spelling is that club. Contradicting evidence
discards the pair.
"""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from sports_edge.datasources.competitions import COMPETITIONS
from sports_edge.ingestion.types import digest, iso
from sports_edge.matching import (
    TeamIdentities,
    alias_targets,
    match_participant,
    participant_key,
)
from sports_edge.offers.text import NameNormalizer
from sports_edge.results_engine import known_names
from sports_edge.storage import Store

ID_PROVIDERS = ("football_data_org", "pitchapi")
EVIDENCE = {
    "provider_id": "ten sam identyfikator drużyny u dostawcy",
    "name_country": "ta sama nazwa i ten sam kraj",
    "alias_country": "alias (zweryfikowany lub z meczu w dwóch źródłach) i ten sam kraj",
}


@dataclass(frozen=True)
class Link:
    competition: str  # international competition
    name: str  # canonical spelling in that competition
    domestic: str  # domestic competition code
    domestic_name: str  # canonical spelling in the domestic league
    country: str | None
    evidence: str


def namespace(competition: str) -> str:
    league = COMPETITIONS.get(competition)
    return "nation" if league is not None and league.nations else "club"


def _domestic_codes() -> list[str]:
    return [
        code
        for code, c in COMPETITIONS.items()
        if c.sport == "football" and c.category == "domestic" and c.country
    ]


def _cup_codes() -> list[str]:
    return [c for c, comp in COMPETITIONS.items() if comp.category == "european"]


def links(
    store: Store,
    *,
    normalizer: NameNormalizer,
    aliases: dict[tuple[str, str], str],
    identities: TeamIdentities,
    until: datetime,
) -> dict[tuple[str, str], list[Link]]:
    """{(cup competition, cup spelling): domestic identities with their evidence}."""
    cache_key = (
        id(store),
        store.scalar("SELECT COUNT(*) FROM historical_results"),
        store.scalar("SELECT COUNT(*) FROM aliases"),
        iso(until)[:10],
    )
    if _CACHE.get("key") == cache_key:
        return _CACHE["value"]
    domestic = _domestic_codes()
    by_key: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    by_id: dict[tuple[str, str], tuple[str, str, str]] = {}
    recent: dict[tuple[str, str], str] = {}
    marks = ",".join("?" for _ in domestic)
    for row in store.rows(
        f"""SELECT competition, provider, home, away, match_date, details_json
        FROM historical_results WHERE competition IN ({marks})""",
        tuple(domestic),
    ):
        code = row["competition"]
        country = COMPETITIONS[code].country
        details = json.loads(row["details_json"] or "{}")
        for side in ("home", "away"):
            name = identities.renames.get((code, row[side]), row[side])
            key = (code, name)
            if row["match_date"] > recent.get(key, ""):
                recent[key] = row["match_date"]
            team_id = details.get(f"{side}_id")
            if row["provider"] in ID_PROVIDERS and team_id is not None:
                by_id[(row["provider"], str(team_id))] = (code, name, country)
    for code, name in recent:
        country = COMPETITIONS[code].country
        by_key[(country, participant_key(name, "football", normalizer))].append(
            (code, name, country)
        )
    reverse = defaultdict(set)
    for sport, alias_key in aliases:
        if sport == "football":
            for canonical_key in alias_targets(aliases, (sport, alias_key)):
                reverse[canonical_key].add(alias_key)
    cups = _cup_codes()
    marks = ",".join("?" for _ in cups)
    evidence: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"countries": Counter(), "ids": set()}
    )
    for row in store.rows(
        f"""SELECT id, competition, provider, home, away, details_json FROM historical_results
        WHERE competition IN ({marks})""",
        tuple(cups),
    ):
        details = json.loads(row["details_json"] or "{}")
        for side in ("home", "away"):
            entry = evidence[(row["competition"], row[side])]
            if details.get(f"{side}_country"):
                entry["countries"][details[f"{side}_country"]] += 1
            if row["provider"] in ID_PROVIDERS and details.get(f"{side}_id") is not None:
                entry["ids"].add((row["provider"], str(details[f"{side}_id"])))
    out: dict[tuple[str, str], list[Link]] = {}
    for (code, name), entry in evidence.items():
        found: dict[tuple[str, str], Link] = {}
        for pair in sorted(entry["ids"]):
            target = by_id.get(pair)
            if target:
                found.setdefault(
                    (target[0], target[1]),
                    Link(code, name, target[0], target[1], target[2], "provider_id"),
                )
        country = entry["countries"].most_common(1)[0][0] if entry["countries"] else None
        if country is not None:
            key = participant_key(name, "football", normalizer)
            for target in by_key.get((country, key), []):
                found.setdefault((target[0], target[1]), Link(code, name, *target, "name_country"))
            candidates = set(alias_targets(aliases, ("football", key))) | reverse.get(key, set())
            for other in candidates - {key}:
                for target in by_key.get((country, other), []):
                    found.setdefault(
                        (target[0], target[1]), Link(code, name, *target, "alias_country")
                    )
        if found:
            out[(code, name)] = sorted(
                found.values(),
                key=lambda link: recent.get((link.domestic, link.domestic_name), ""),
                reverse=True,
            )
    _CACHE.update(key=cache_key, value=out)
    return out


_CACHE: dict = {}


def with_links(
    identities: TeamIdentities, found: dict[tuple[str, str], list[Link]]
) -> TeamIdentities:
    """Cup spellings renamed to the club's domestic canonical spelling (most recent league).

    Two cup spellings of one club (``Atlético Madrid`` / ``Club Atlético de Madrid``) become
    one identity; clubs without evidence keep their own spelling."""
    renames = dict(identities.renames)
    for (code, name), items in found.items():
        if items and items[0].domestic_name != name:
            renames[(code, name)] = items[0].domestic_name
    return TeamIdentities(identities.normalizer, renames)


def learn_fixture_aliases(
    store: Store,
    records: list,
    existing: list[dict],
    *,
    sport: str,
    normalizer: NameNormalizer,
    aliases: dict[tuple[str, str], str],
    now: datetime,
    origin: str,
) -> list[dict]:
    """New aliases from matches reported by two sources (same day, score and opponent)."""
    known = known_names(existing, sport, normalizer)
    index: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in existing:
        if row.get("regulation_certain", True):
            index[(row["match_date"], "home", row["home"])].append(row)
            index[(row["match_date"], "away", row["away"])].append(row)
    votes: dict[str, Counter] = defaultdict(Counter)
    for record in records:
        if not record.complete or record.details.get("regulation_certain") is False:
            continue
        sides = {
            side: match_participant(getattr(record, side), sport, known, normalizer, aliases)
            for side in ("home", "away")
        }
        accepted = {s for s, m in sides.items() if m.status in {"exact", "alias"}}
        if len(accepted) != 1:
            continue
        side = accepted.pop()
        other = "away" if side == "home" else "home"
        rows = [
            r
            for r in index.get((record.match_date, side, sides[side].target), [])
            if (r["home_score"], r["away_score"]) == (record.home_score, record.away_score)
        ]
        if len(rows) == 1:
            votes[getattr(record, other)][rows[0][other]] += 1
    learned = []
    for name, counter in votes.items():
        if len(counter) != 1:
            continue  # contradicting evidence: never linked
        target, count = counter.most_common(1)[0]
        alias_key = participant_key(name, sport, normalizer)
        canonical_key = participant_key(target, sport, normalizer)
        if alias_key == canonical_key or canonical_key in alias_targets(
            aliases, (sport, alias_key)
        ):
            continue
        key = digest(["alias", sport, alias_key, canonical_key])
        if store.get("aliases", key) is None:
            with store.transaction():
                store.insert(
                    "aliases",
                    id=key,
                    sport=sport,
                    alias_key=alias_key,
                    canonical_key=canonical_key,
                    origin=origin,
                    created_at=iso(now),
                )
        known_targets = alias_targets(aliases, (sport, alias_key))
        aliases[(sport, alias_key)] = (
            (*known_targets, canonical_key) if known_targets else canonical_key
        )
        learned.append({"source": name, "canonical": target, "fixtures": count})
    return learned


def schedule(rows: list[dict], team: str, *, competitions: set[str], before: str) -> list[dict]:
    """Every identified match of one club before a day (league + linked cups), newest last."""
    return sorted(
        (
            r
            for r in rows
            if r["competition"] in competitions
            and team in (r["home"], r["away"])
            and r["match_date"] < before
        ),
        key=lambda r: (r["match_date"], r.get("start_at") or ""),
    )
