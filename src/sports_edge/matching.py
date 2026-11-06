"""Participant matching (STS events ↔ independent sports data) and market identity.

Name similarity alone never links records: sport and date must agree, and only exact keys
after verified normalisation (token aliases, user-confirmed aliases) link automatically.
Everything else is a candidate for manual confirmation; confirmations are appended.
"""

import tomllib
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from sports_edge.ingestion.types import digest, iso
from sports_edge.offers.text import NameNormalizer
from sports_edge.storage import Store

# Individual sports list people as "Surname Name" in STS and "Name Surname" elsewhere.
ORDER_FREE_SPORTS = frozenset({"tennis", "darts", "snooker", "boxing", "mma", "table_tennis"})


@dataclass(frozen=True)
class ParticipantMatch:
    name: str
    status: str  # exact | alias | candidate | none
    target: str | None
    score: float


def alias_map(
    store: Store, extra: dict[tuple[str, str], str] | None = None
) -> dict[tuple[str, str], str | tuple[str, ...]]:
    """Reviewed pairs from config plus pairs the user confirmed in the application.

    A user confirmation replaces an earlier pair of the same key. Pairs learned from fixture
    evidence (TASK 009) are added: one source spelling may denote a club whose canonical
    spelling differs between competitions (``Braga`` = ``Sporting Braga`` in the Europa
    League and ``Sporting Clube de Braga`` in the Champions League)."""
    found: dict[tuple[str, str], str | tuple[str, ...]] = dict(extra or {})
    for r in store.rows(
        "SELECT sport, alias_key, canonical_key, origin FROM aliases ORDER BY created_at, rowid"
    ):
        key = (r["sport"], r["alias_key"])
        if key in found and str(r["origin"]).startswith("fixture_evidence"):
            if r["canonical_key"] not in alias_targets(found, key):
                found[key] = (*alias_targets(found, key), r["canonical_key"])
        else:
            found[key] = r["canonical_key"]
    return found


def alias_targets(aliases: dict, key: tuple[str, str]) -> tuple[str, ...]:
    """Canonical keys of one alias key (none, one or several)."""
    value = aliases.get(key)
    if value is None:
        return ()
    return (value,) if isinstance(value, str) else tuple(value)


def load_team_aliases(path: Path, normalizer: NameNormalizer) -> dict[tuple[str, str], str]:
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return {
        (sport, participant_key(alias, sport, normalizer)): participant_key(name, sport, normalizer)
        for sport, pairs in data.items()
        for alias, name in pairs.items()
    }


def participant_key(name: str, sport: str, normalizer: NameNormalizer) -> str:
    key = normalizer.key(name)
    return " ".join(sorted(key.split())) if sport in ORDER_FREE_SPORTS else key


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    tokens_a, tokens_b = set(a.split()), set(b.split())
    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    return max(jaccard, SequenceMatcher(None, a, b).ratio())


def match_participant(
    name: str,
    sport: str,
    known: dict[str, str],
    normalizer: NameNormalizer,
    aliases: dict[tuple[str, str], str],
    *,
    candidate_threshold: float = 0.85,
) -> ParticipantMatch:
    """``known`` maps participant keys of the data source to their original names."""
    key = participant_key(name, sport, normalizer)
    if key in known:
        return ParticipantMatch(name, "exact", known[key], 1.0)
    for alias in alias_targets(aliases, (sport, key)):
        if alias in known:
            return ParticipantMatch(name, "alias", known[alias], 1.0)
    best, score = None, 0.0
    for other in known:
        value = similarity(key, other)
        if value > score:
            best, score = other, value
    if best is not None and score >= candidate_threshold:
        return ParticipantMatch(name, "candidate", known[best], round(score, 3))
    return ParticipantMatch(name, "none", None, round(score, 3))


def confirm_alias(
    store: Store,
    *,
    sport: str,
    alias: str,
    canonical: str,
    normalizer: NameNormalizer,
    at: datetime,
) -> str:
    """User confirmation that an STS name and a data-source name denote the same participant."""
    alias_key = participant_key(alias, sport, normalizer)
    canonical_key = participant_key(canonical, sport, normalizer)
    if alias_key == canonical_key:
        raise ValueError("Names already share the same key")
    key = digest(["alias", sport, alias_key, canonical_key])
    with store.transaction():
        store.insert(
            "aliases",
            id=key,
            sport=sport,
            alias_key=alias_key,
            canonical_key=canonical_key,
            origin="user_confirmed",
            created_at=iso(at),
        )
    return key


@dataclass(frozen=True)
class TeamIdentities:
    """One club, one identity across the spellings of the results history.

    Spellings with the same normalised key are the same club; reviewed renames (config
    ``team_identities.toml``) join spellings whose keys differ.
    """

    normalizer: NameNormalizer
    renames: dict[tuple[str, str], str]

    def key(self, competition: str, sport: str, name: str) -> str:
        return participant_key(self.renames.get((competition, name), name), sport, self.normalizer)


def load_team_identities(path: Path, normalizer: NameNormalizer) -> TeamIdentities:
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    renames = {
        (competition, old): new for competition, pairs in data.items() for old, new in pairs.items()
    }
    return TeamIdentities(normalizer, renames)


def market_identity(market: dict) -> tuple:
    return (market["market_key"], market["period"], market["overtime"], market["line"])


def compatible(market_a: dict, market_b: dict) -> tuple[bool, str]:
    """Markets are compared only when every settlement-defining field is identical."""
    if market_a["market_key"] in {"unknown", "handicap_unsigned"}:
        return False, "Semantyka rynku niezweryfikowana (np. znak handicapu)"
    for field, label in (
        ("market_key", "typ rynku"),
        ("period", "okres rozliczenia"),
        ("overtime", "dogrywka"),
        ("line", "linia"),
    ):
        if market_a[field] != market_b[field]:
            return False, f"Różny {label}: {market_a[field]} ≠ {market_b[field]}"
    keys_a, keys_b = set(market_a["selections"]), set(market_b["selections"])
    if not keys_a & keys_b:
        return False, "Brak wspólnych selekcji"
    if "unverified" in market_a["rules_ref"] or "unverified" in market_b["rules_ref"]:
        return True, "Zgodne według etykiet; regulaminy rozliczeń niezweryfikowane"
    return True, "Zgodne"
