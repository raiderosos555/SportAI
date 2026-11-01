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
