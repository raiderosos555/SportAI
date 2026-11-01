"""Competitions known to the results engine: code → sport, name, time zone, STS league headers.

Football codes follow openfootball ("en.1"); Ekstraklasa ("pl.1") and other sports have no
openfootball history and are served by API-Sports. The time zone defines the local match
date used to link an STS event with a result (the same zone is applied to both sides).
"""

from dataclasses import dataclass

from sports_edge.datasources import openfootball


@dataclass(frozen=True)
class Competition:
    code: str
    sport: str
    name: str
    zone: str
    sts_leagues: tuple[str, ...]


COMPETITIONS: dict[str, Competition] = {
    **{
        code: Competition(code, "football", league.name, league.zone, league.sts_leagues)
        for code, league in openfootball.LEAGUES.items()
    },
    "pl.1": Competition(
        "pl.1", "football", "Ekstraklasa", "Europe/Warsaw", ("POLSKA - EKSTRAKLASA",)
    ),
    # TASK 006: leagues with 5-6 seasons of free PitchAPI history and many STS events.
    "en.2": Competition(
        "en.2", "football", "Championship", "Europe/London", ("ANGLIA - CHAMPIONSHIP",)
    ),
    "nl.1": Competition(
        "nl.1", "football", "Eredivisie", "Europe/Amsterdam", ("HOLANDIA - EREDIVISIE",)
    ),
    "pt.1": Competition(
        "pt.1", "football", "Liga Portugal", "Europe/Lisbon", ("PORTUGALIA - PRIMEIRA LIGA",)
    ),
    "be.1": Competition(
        "be.1", "football", "Pro League (Belgia)", "Europe/Brussels", ("BELGIA - PRO LEAGUE",)
    ),
    "de.2": Competition(
        "de.2", "football", "2. Bundesliga", "Europe/Berlin", ("NIEMCY - 2. BUNDESLIGA",)
    ),
    "tr.1": Competition(
        "tr.1", "football", "Süper Lig", "Europe/Istanbul", ("TURCJA - SUPER LIG",)
    ),
    "basketball.nba": Competition(
        "basketball.nba", "basketball", "NBA", "America/New_York", ("USA - NBA",)
    ),
    "basketball.euroleague": Competition(
        "basketball.euroleague", "basketball", "Euroliga", "Europe/Berlin", ("KLUBOWE - EUROLIGA",)
    ),
    "ice_hockey.nhl": Competition(
        "ice_hockey.nhl", "ice_hockey", "NHL", "America/New_York", ("USA - NHL",)
    ),
}


def for_sts_league(header: str, sport: str | None = None) -> Competition | None:
    """Competition of an STS league header (and sport, when given)."""
    for competition in COMPETITIONS.values():
        if header in competition.sts_leagues and (sport is None or competition.sport == sport):
            return competition
    return None
