"""Cross-bookmaker event matching and market compatibility."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sports_edge.matching import compatible, find_matches, latest_decisions, record_decision
from sports_edge.offers.text import NameNormalizer, load_normalizer

pytest.importorskip("pymupdf")

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-10-31"


@pytest.fixture
def normalizer() -> NameNormalizer:
    return load_normalizer(ROOT / "config/aliases.toml")


def by_home(matches):
    return {m.event_a["home"]: m for m in matches}


def test_real_madrid_matches_real_madryt_fc_barcelona(store, import_both, normalizer):
    matches = by_home(find_matches(store, day=DAY, normalizer=normalizer))
    real = matches["Real Madrid"]
    assert real.event_b["home"] == "Real Madryt" and real.event_b["away"] == "FC Barcelona"
    assert real.status == "auto" and real.method == "exact_keys+sport+time"
    assert real.evidence["keys_a"] == real.evidence["keys_b"] == ["real madrid", "barcelona"]
    assert matches["Legia Warszawa"].status == "auto"
    # Five minutes apart: still linked, with lower confidence than an identical start.
    assert matches["Górnik Zabrze"].status == "auto"
    assert matches["Górnik Zabrze"].confidence < real.confidence
    assert "Zagłębie Lubin" not in {m.event_b["home"] for m in matches.values()}


def test_names_alone_never_link_events(store, import_both, normalizer):
    strict = find_matches(store, day=DAY, normalizer=normalizer, tolerance_minutes=0)
    linked = {m.event_a["home"] for m in strict}
    assert "Real Madrid" in linked
    assert "Górnik Zabrze" not in linked  # same names, different start time
    # Women's teams keep a distinct key and never merge with the men's team.
    assert NameNormalizer().key("Lech Poznań [K]") != NameNormalizer().key("Lech Poznań")


def test_similar_names_are_candidates_until_confirmed(store, import_both):
    weak = NameNormalizer()  # no token aliases: "Madrid" and "Madryt" differ
    real = by_home(find_matches(store, day=DAY, normalizer=weak))["Real Madrid"]
    assert real.status == "candidate"
    at = datetime(2026, 10, 31, 13, tzinfo=UTC)
    record_decision(store, real, "confirmed", at=at)
    again = by_home(find_matches(store, day=DAY, normalizer=weak))["Real Madrid"]
    assert again.status == "confirmed"
    aliases = store.rows("SELECT alias_key, canonical_key, origin FROM aliases")
    assert {
        "alias_key": "real madryt",
        "canonical_key": "real madrid",
        "origin": "user_confirmed_match",
    } in aliases
    with pytest.raises(ValueError):
        record_decision(store, real, "auto", at=at)


def test_rejection_is_appended_not_overwritten(store, import_both, normalizer):
    real = by_home(find_matches(store, day=DAY, normalizer=normalizer))["Real Madrid"]
    record_decision(store, real, "rejected", at=datetime(2026, 10, 31, 13, tzinfo=UTC))
    assert "Real Madrid" not in by_home(find_matches(store, day=DAY, normalizer=normalizer))
    record_decision(store, real, "confirmed", at=datetime(2026, 10, 31, 14, tzinfo=UTC))
    assert (
        by_home(find_matches(store, day=DAY, normalizer=normalizer))["Real Madrid"].status
        == "confirmed"
    )
    assert store.scalar("SELECT COUNT(*) FROM event_links") == 2
    assert len(latest_decisions(store)) == 1


def market(**overrides):
    base = {
        "market_key": "total",
        "period": "FT",
        "overtime": "false",
        "line": "2.5",
        "selections": {"under": {}, "over": {}},
        "rules_ref": "sts:pdf:total:FT:unverified",
    }
    return {**base, **overrides}


@pytest.mark.parametrize(
    ("other", "reason"),
    [
        (market(line="3.5"), "linia"),
        (market(period="H1"), "okres"),
        (market(overtime="true"), "dogrywka"),
        (market(market_key="team_total_home"), "typ rynku"),
        (market(selections={"yes": {}}), "wspólnych"),
    ],
)
def test_markets_with_different_terms_are_not_compared(other, reason):
    ok, note = compatible(market(), other)
    assert not ok and reason in note


def test_identical_markets_are_compared_with_rules_caveat():
    assert compatible(market(), market()) == (
        True,
        "Zgodne według etykiet; regulaminy rozliczeń niezweryfikowane",
    )
    verified = market(rules_ref="sts:verified")
    assert compatible(verified, market(rules_ref="superbet:verified")) == (True, "Zgodne")
    ok, note = compatible(
        market(market_key="handicap_unsigned"), market(market_key="handicap_unsigned")
    )
    assert not ok and "niezweryfikowana" in note
