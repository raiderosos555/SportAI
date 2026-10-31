"""Cross-bookmaker event matching and market compatibility.

Name similarity alone never links two events: sport and start time must agree, and only
exact keys after verified normalisation are linked automatically. Everything else is a
candidate that the user confirms or rejects; decisions are appended, never overwritten.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.offers.text import NameNormalizer, fold
from sports_edge.storage import Store

KNOWN_COUNTRIES = frozenset(
    fold(c)
    for c in (
        "Anglia Argentyna Australia Austria Belgia Boliwia Brazylia Bułgaria Chile Chiny "
        "Chorwacja Czechy Dania Ekwador Estonia Finlandia Francja Grecja Hiszpania Holandia "
        "Irlandia Islandia Izrael Japonia Kanada Kolumbia Korea Litwa Łotwa Meksyk Niemcy "
        "Norwegia Paragwaj Peru Polska Portugalia Rumunia Serbia Słowacja Słowenia Szkocja "
        "Szwajcaria Szwecja Turcja Ukraina Urugwaj USA Walia Wenezuela Węgry Włochy"
    ).split()
)


@dataclass(frozen=True)
class Match:
    event_a: dict
    event_b: dict
    status: str  # auto | candidate | confirmed | rejected
    confidence: float
    method: str
    evidence: dict


def alias_map(store: Store) -> dict[tuple[str, str], str]:
    return {
        (r["sport"], r["alias_key"]): r["canonical_key"]
        for r in store.rows("SELECT sport, alias_key, canonical_key FROM aliases")
    }


def _key(name: str | None, sport: str, normalizer: NameNormalizer, aliases: dict) -> str:
    key = normalizer.key(name or "")
    return aliases.get((sport, key), key)


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    tokens_a, tokens_b = set(a.split()), set(b.split())
    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    return max(jaccard, SequenceMatcher(None, a, b).ratio())


def _country(event: dict) -> str | None:
    value = fold(event.get("country") or "")
    return value if value in KNOWN_COUNTRIES else None


def latest_decisions(store: Store) -> dict[tuple[str, str], dict]:
    decisions: dict[tuple[str, str], dict] = {}
    for row in store.rows("SELECT * FROM event_links ORDER BY decided_at, id"):
        decisions[(row["event_a"], row["event_b"])] = row
    return decisions


def find_matches(
    store: Store,
    *,
    day: str,
    normalizer: NameNormalizer,
    bookmaker_a: str = "sts",
    bookmaker_b: str = "superbet",
    tolerance_minutes: int = 10,
) -> list[Match]:
    events = store.rows(
        """SELECT * FROM events WHERE offer_date=? AND bookmaker_id IN (?,?)
        AND home IS NOT NULL AND away IS NOT NULL AND start_at IS NOT NULL""",
        (day, bookmaker_a, bookmaker_b),
    )
    side_a = [e for e in events if e["bookmaker_id"] == bookmaker_a]
    side_b = [e for e in events if e["bookmaker_id"] == bookmaker_b]
    aliases = alias_map(store)
    decisions = latest_decisions(store)
    found: list[Match] = []
    for a in side_a:
        start_a = datetime.fromisoformat(a["start_at"])
        home_a = _key(a["home"], a["sport"], normalizer, aliases)
        away_a = _key(a["away"], a["sport"], normalizer, aliases)
        for b in side_b:
            if a["sport"] != b["sport"]:
                continue
            minutes = abs((datetime.fromisoformat(b["start_at"]) - start_a).total_seconds()) / 60
            if minutes > tolerance_minutes:
                continue
            country_a, country_b = _country(a), _country(b)
            if country_a and country_b and country_a != country_b:
                continue
            home_b = _key(b["home"], b["sport"], normalizer, aliases)
            away_b = _key(b["away"], b["sport"], normalizer, aliases)
            evidence = {
                "sport": a["sport"],
                "minutes_apart": round(minutes, 1),
                "keys_a": [home_a, away_a],
                "keys_b": [home_b, away_b],
                "country": [country_a, country_b],
                "league": [a["league"], b["league"]],
            }
            exact = home_a == home_b and away_a == away_b
            swapped = home_a == away_b and away_a == home_b
            score = min(_similarity(home_a, home_b), _similarity(away_a, away_b))
            evidence["name_score"] = round(score, 3)
            if exact and minutes <= 5:
                status, method = "auto", "exact_keys+sport+time"
                confidence = 0.97 if minutes == 0 else 0.9
            elif exact or swapped or score >= 0.75:
                status, method = "candidate", "swapped_order" if swapped else "similar_names"
                confidence = round(0.4 + 0.4 * score + (0.1 if minutes == 0 else 0), 3)
            else:
                continue
            decision = decisions.get((a["id"], b["id"]))
            if decision is not None:
                status, method = decision["status"], f"{decision['status']}:{decision['method']}"
                confidence = decision["confidence"]
            found.append(Match(a, b, status, confidence, method, evidence))
    # An event with several automatic partners is ambiguous: require a human decision.
    for side in ("event_a", "event_b"):
        counts: dict[str, int] = {}
        for m in found:
            if m.status == "auto":
                counts[getattr(m, side)["id"]] = counts.get(getattr(m, side)["id"], 0) + 1
        found = [
            Match(
                m.event_a, m.event_b, "candidate", m.confidence, m.method + "+ambiguous", m.evidence
            )
            if m.status == "auto" and counts.get(getattr(m, side)["id"], 0) > 1
            else m
            for m in found
        ]
    return [m for m in found if m.status != "rejected"]


def record_decision(store: Store, match: Match, status: str, *, at: datetime) -> str:
    if status not in {"confirmed", "rejected"}:
        raise ValueError("Only explicit confirmation or rejection is recorded")
    key = digest(["link", match.event_a["id"], match.event_b["id"], status, iso(at)])
    with store.transaction():
        store.insert(
            "event_links",
            id=key,
            event_a=match.event_a["id"],
            event_b=match.event_b["id"],
            status=status,
            confidence=1.0 if status == "confirmed" else 0.0,
            method="user",
            evidence_json=canonical(match.evidence),
            decided_at=iso(at),
        )
        if status == "confirmed":
            keys_a, keys_b = match.evidence["keys_a"], match.evidence["keys_b"]
            for alias, canonical_key in zip(keys_b, keys_a, strict=True):
                if alias != canonical_key:
                    store.insert(
                        "aliases",
                        id=digest(["alias", match.event_a["sport"], alias, canonical_key]),
                        sport=match.event_a["sport"],
                        alias_key=alias,
                        canonical_key=canonical_key,
                        origin="user_confirmed_match",
                        created_at=iso(at),
                    )
    return key


def linked_pairs(matches: list[Match]) -> list[Match]:
    return [m for m in matches if m.status in {"auto", "confirmed"}]


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


def evidence_text(match: Match) -> str:
    return json.dumps(match.evidence, ensure_ascii=False)
