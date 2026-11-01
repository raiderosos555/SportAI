"""Participant matching (STS ↔ independent data) and market compatibility."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sports_edge.matching import (
    alias_map,
    compatible,
    confirm_alias,
    load_team_aliases,
    match_participant,
    participant_key,
)
from sports_edge.offers.text import NameNormalizer, load_normalizer

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def normalizer() -> NameNormalizer:
    return load_normalizer(ROOT / "config/aliases.toml")


def index(names, sport, normalizer):
    return {participant_key(n, sport, normalizer): n for n in names}


def test_sts_spellings_match_data_source_spellings(normalizer):
    known = index(
        [
            "Real Madrid CF",
            "FC Barcelona",
            "FC Bayern München",
            "1. FSV Mainz 05",
            "RB Leipzig",
            "SV Werder Bremen",
            "Como 1907",
            "SSC Napoli",
            "Olympique de Marseille",
        ],
        "football",
        normalizer,
    )
    for sts, expected in (
        ("Real Madryt", "Real Madrid CF"),
        ("Barcelona", "FC Barcelona"),
        ("Bayern Monachium", "FC Bayern München"),
        ("FSV Mainz", "1. FSV Mainz 05"),
        ("RB Lipsk", "RB Leipzig"),
        ("Werder Brema", "SV Werder Bremen"),
        ("Como", "Como 1907"),
        ("Napoli", "SSC Napoli"),
        ("Olympique Marsylia", "Olympique de Marseille"),
    ):
        found = match_participant(sts, "football", known, normalizer, {})
        assert (found.status, found.target) == ("exact", expected), sts


def test_reviewed_aliases_and_order_free_individual_names(normalizer):
    aliases = load_team_aliases(ROOT / "config/team_aliases.toml", normalizer)
    known = index(["Paris Saint-Germain FC", "Tottenham Hotspur FC"], "football", normalizer)
    assert match_participant("PSG", "football", known, normalizer, aliases).status == "alias"
    tennis = index(["Hubert Hurkacz"], "tennis", normalizer)
    assert match_participant("Hurkacz Hubert", "tennis", tennis, normalizer, {}).status == "exact"


def test_similar_names_are_only_candidates(normalizer, store):
    known = index(["Olympique Lyonnais"], "football", normalizer)
    found = match_participant("Olympique Lyon", "football", known, normalizer, {})
    assert found.status == "candidate" and found.target == "Olympique Lyonnais"
    confirm_alias(
        store,
        sport="football",
        alias="Olympique Lyon",
        canonical="Olympique Lyonnais",
        normalizer=normalizer,
        at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    again = match_participant("Olympique Lyon", "football", known, normalizer, alias_map(store))
    assert again.status == "alias"
    assert store.rows("SELECT origin FROM aliases") == [{"origin": "user_confirmed"}]
    with pytest.raises(ValueError):
        confirm_alias(
            store,
            sport="football",
            alias="Napoli",
            canonical="SSC Napoli",
            normalizer=normalizer,
            at=datetime(2026, 11, 1, tzinfo=UTC),
        )


def test_women_teams_never_merge_with_men(normalizer):
    assert NameNormalizer().key("Lech Poznań [K]") != NameNormalizer().key("Lech Poznań")
    known = index(["Lech Poznań"], "football", normalizer)
    assert match_participant("Lech Poznań [K]", "football", known, normalizer, {}).status != "exact"


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


def test_identical_markets_and_unsigned_handicap():
    assert compatible(market(), market())[0] is True
    ok, note = compatible(
        market(market_key="handicap_unsigned"), market(market_key="handicap_unsigned")
    )
    assert not ok and "niezweryfikowana" in note
