"""The competition catalogue: code → sport, name, zone, type, STS headers and source ids.

Football codes follow openfootball ("en.1"); international competitions use "uefa.*" and
"fifa.*". Every adapter derives its competition list from this one catalogue, so a new
competition is added here once (TASK 009).

Rules:

* ``sts_leagues`` are STS PDF headers OBSERVED in real offers (exact text) — never guessed;
  "KLUBOWE - LIGA MISTRZÓW AFC" (Asia) is a different competition than
  "KLUBOWE - LIGA MISTRZÓW" (UEFA) and exact matching keeps them apart;
* ``sources`` are identifiers verified against the provider's own catalogue on the date in
  ``verified`` — an unverified id is never written here;
* ``kind`` separates domestic leagues, domestic cups, international club cups and national
  team competitions; ``country`` (codes as in openfootball: ENG, ESP, ...) links the clubs
  of a domestic league with the same clubs in European cups.
"""

from dataclasses import dataclass

from sports_edge.datasources import openfootball

KINDS = {
    "domestic_league": "Liga krajowa",
    "domestic_cup": "Puchar krajowy",
    "international_club_cup": "Puchar międzynarodowy (kluby)",
    "national_tournament": "Turniej reprezentacyjny",
    "national_qualifiers": "Eliminacje reprezentacji",
    "national_league": "Liga Narodów",
    "national_friendly": "Mecz towarzyski reprezentacji",
}
CATEGORY_OF_KIND = {
    "domestic_league": "domestic",
    "domestic_cup": "domestic",
    "international_club_cup": "european",
    "national_tournament": "national",
    "national_qualifiers": "national",
    "national_league": "national",
    "national_friendly": "national",
}
CATEGORIES = {
    "domestic": "Ligi krajowe",
    "european": "Europejskie puchary",
    "national": "Reprezentacje",
}


@dataclass(frozen=True)
class Competition:
    code: str
    sport: str
    name: str
    zone: str
    sts_leagues: tuple[str, ...]
    kind: str = "domestic_league"
    country: str | None = None  # domestic competitions only
    sources: tuple[tuple[str, str], ...] = ()  # (provider, its verified identifier)
    verified: str = ""

    @property
    def category(self) -> str:
        return CATEGORY_OF_KIND.get(self.kind, "domestic")

    @property
    def nations(self) -> bool:
        """National teams play, not clubs (a separate identity space and rating pool)."""
        return self.category == "national"

    def source(self, provider: str) -> str | None:
        return dict(self.sources).get(provider)


COUNTRY = {
    "en.1": "ENG",
    "de.1": "GER",
    "es.1": "ESP",
    "it.1": "ITA",
    "fr.1": "FRA",
}

COMPETITIONS: dict[str, Competition] = {
    **{
        code: Competition(
            code,
            "football",
            league.name,
            league.zone,
            league.sts_leagues,
            country=COUNTRY.get(code),
            sources=(
                (
                    "football_data",
                    {"en.1": "PL", "de.1": "BL1", "es.1": "PD", "it.1": "SA", "fr.1": "FL1"}[code],
                ),
            ),
        )
        for code, league in openfootball.LEAGUES.items()
    },
    "pl.1": Competition(
        "pl.1",
        "football",
        "Ekstraklasa",
        "Europe/Warsaw",
        ("POLSKA - EKSTRAKLASA",),
        country="POL",
    ),
    # TASK 006: leagues with 5-6 seasons of free PitchAPI history and many STS events.
    "en.2": Competition(
        "en.2",
        "football",
        "Championship",
        "Europe/London",
        ("ANGLIA - CHAMPIONSHIP",),
        country="ENG",
    ),
    "nl.1": Competition(
        "nl.1",
        "football",
        "Eredivisie",
        "Europe/Amsterdam",
        ("HOLANDIA - EREDIVISIE",),
        country="NED",
    ),
    "pt.1": Competition(
        "pt.1",
        "football",
        "Liga Portugal",
        "Europe/Lisbon",
        ("PORTUGALIA - PRIMEIRA LIGA",),
        country="POR",
    ),
    "be.1": Competition(
        "be.1",
        "football",
        "Pro League (Belgia)",
        "Europe/Brussels",
        ("BELGIA - PRO LEAGUE",),
        country="BEL",
    ),
    "de.2": Competition(
        "de.2",
        "football",
        "2. Bundesliga",
        "Europe/Berlin",
        ("NIEMCY - 2. BUNDESLIGA",),
        country="GER",
    ),
    "tr.1": Competition(
        "tr.1",
        "football",
        "Süper Lig",
        "Europe/Istanbul",
        ("TURCJA - SUPER LIG",),
        country="TUR",
    ),
    # TASK 009: European club cups. Identifiers verified on 2026-11-02 with the user's keys:
    # football-data.org CL (seasons 2023/24-2026/27 on the free plan; older ones: 403),
    # PitchAPI catalogue (2021/22-2026/27), openfootball/champions-league (CC0, 2011/12-
    # 2025/26; Europa League and Conference League files only in some seasons).
    "uefa.cl": Competition(
        "uefa.cl",
        "football",
        "Liga Mistrzów UEFA",
        "Europe/Warsaw",
        ("KLUBOWE - LIGA MISTRZÓW",),  # observed in the STS offer of 2026-11-03
        kind="international_club_cup",
        sources=(("football_data", "CL"), ("pitchapi", "l_0bfbkO"), ("openfootball_cups", "cl")),
        verified="2026-11-02",
    ),
    "uefa.el": Competition(
        "uefa.el",
        "football",
        "Liga Europy UEFA",
        "Europe/Warsaw",
        ("KLUBOWE - LIGA EUROPY",),  # observed in the STS offers of 04–05.11.2026 (football)
        kind="international_club_cup",
        sources=(("pitchapi", "l_38d9HA"), ("openfootball_cups", "el")),
        verified="2026-11-02",
    ),
    "uefa.ecl": Competition(
        "uefa.ecl",
        "football",
        "Liga Konferencji UEFA",
        "Europe/Warsaw",
        ("KLUBOWE - LIGA KONFERENCJI",),  # observed in the STS offer of 05.11.2026 (football)
        kind="international_club_cup",
        sources=(("pitchapi", "l_2WZ2tt"), ("openfootball_cups", "conf")),
        verified="2026-11-02",
    ),
    # National teams: a separate identity space and rating pool (never mixed with clubs).
    "fifa.wc": Competition(
        "fifa.wc",
        "football",
        "Mistrzostwa świata",
        "UTC",
        (),
        kind="national_tournament",
        sources=(
            ("football_data", "WC"),  # 2026 only on the free plan (2022: 403)
            ("pitchapi", "l_1XLtP0"),  # 2022, 2026
            ("openfootball_int", "fifa_world_cup"),
        ),
        verified="2026-11-02",
    ),
    "uefa.euro": Competition(
        "uefa.euro",
        "football",
        "Mistrzostwa Europy",
        "UTC",
        (),
        kind="national_tournament",
        sources=(("football_data", "EC"), ("openfootball_int", "uefa_euro")),
        verified="2026-11-02",
    ),
    "uefa.nl": Competition(
        "uefa.nl",
        "football",
        "Liga Narodów UEFA",
        "UTC",
        ("MIĘDZYNARODOWE - LIGA NARODÓW UEFA",),
        kind="national_league",
        sources=(("openfootball_int", "uefa_nations_league"),),
        verified="2026-11-02",
    ),
    "uefa.euro_q": Competition(
        "uefa.euro_q",
        "football",
        "Eliminacje mistrzostw Europy",
        "UTC",
        (),
        kind="national_qualifiers",
        sources=(("openfootball_int", "uefa_euro_qualification"),),
        verified="2026-11-02",
    ),
    "fifa.wc_q": Competition(
        "fifa.wc_q",
        "football",
        "Eliminacje mistrzostw świata",
        "UTC",
        (),
        kind="national_qualifiers",
        sources=(("openfootball_int", "fifa_world_cup_qualification"),),
        verified="2026-11-02",
    ),
    # Friendlies: rating input only (a lower weight than official matches is tested), never
    # an STS competition of their own; matches between UEFA members only.
    "fifa.friendly": Competition(
        "fifa.friendly",
        "football",
        "Mecze towarzyskie reprezentacji",
        "UTC",
        ("MIĘDZYNARODOWE - MECZE TOWARZYSKIE",),
        kind="national_friendly",
        sources=(("openfootball_int", "friendly"),),
        verified="2026-11-02",
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
# Checked 2026-11-02 and NOT available from a verified free source (no integration is
# pretended): UEFA Super Cup, FA Cup, EFL Cup, DFB-Pokal, Copa del Rey, Coppa Italia, Puchar
# Polski (no football-data.org free plan, no PitchAPI catalogue entry; openfootball country
# repositories hold league files only). openfootball/club-worldcup exists (Club World Cup).
UNAVAILABLE = {
    "UEFA Super Cup": "brak w football-data.org (plan darmowy), PitchAPI i openfootball",
    "FA Cup": "brak w sprawdzonych bezpłatnych źródłach",
    "EFL Cup": "brak w sprawdzonych bezpłatnych źródłach",
    "DFB-Pokal": "brak w sprawdzonych bezpłatnych źródłach",
    "Copa del Rey": "brak w sprawdzonych bezpłatnych źródłach",
    "Coppa Italia": "brak w sprawdzonych bezpłatnych źródłach",
    "Puchar Polski": "brak w sprawdzonych bezpłatnych źródłach",
    "Klubowe mistrzostwa świata": "tylko openfootball/club-worldcup (CC0) — niewdrożone",
}


def for_sts_league(header: str, sport: str | None = None) -> Competition | None:
    """Competition of an STS league header (and sport, when given). Exact text only."""
    for competition in COMPETITIONS.values():
        if header in competition.sts_leagues and (sport is None or competition.sport == sport):
            return competition
    return None


def by_category(category: str | None) -> tuple[str, ...]:
    """Codes of one category (all football competitions when ``category`` is None)."""
    return tuple(
        code
        for code, c in COMPETITIONS.items()
        if c.sport == "football" and (category is None or c.category == category)
    )


def with_source(provider: str) -> dict[str, str]:
    """{competition code: the provider's identifier} — what one adapter may fetch."""
    return {code: c.source(provider) for code, c in COMPETITIONS.items() if c.source(provider)}
