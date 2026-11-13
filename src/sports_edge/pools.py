"""Rating pools of the model scopes (TASK 018).

A club league is its own pool: ratings, Dixon–Coles fits, team names and the freshness of
the data are per league, exactly as before. National teams play across several
competitions (Nations League, qualifiers, finals tournaments, friendlies), so all national
competitions form ONE pool — a team keeps one rating whatever the competition.

National matches are often played on a neutral ground. A result or an STS event gets home
advantage only when its venue is known and lies in the home team's country; a neutral or
unknown venue gets none (the TASK 017 rule). Club matches always have a home team.
"""

import json
import re
import tomllib
import unicodedata
from collections import defaultdict
from datetime import datetime
from functools import cache

NATIONAL_POOL = "national"
# senior national-team competitions with results history (TASK 009/017 catalogue)
NATIONAL_CODES = ("uefa.nl", "uefa.euro_q", "fifa.wc_q", "uefa.euro", "fifa.wc", "fifa.friendly")
# Frequency-dependent rules of the national scope live in config/national_scope.toml (the
# user's decision of 12.11.2026: national teams do not play as often as a league). These are
# the defaults when the file is missing.
NATIONAL_DEFAULTS = {
    "freshness": {"max_gap_days": 150},
    "elo": {"k": [20.0, 30.0, 40.0, 50.0, 60.0], "home_advantage": [40.0, 60.0, 80.0, 100.0]},
    "dixon_coles": {"half_life_days": [240.0, 480.0, 720.0, 1080.0], "window_days": 1460},
    "retraining": {"min_new_results": 60, "min_days_between_trainings": 28},
    # TASK 019 §1.4: a period without any fixture list or STS archive longer than this may
    # hide a whole FIFA window — the team's recent matches are "unknown" (a warning only)
    "completeness": {"unknown_gap_days": 45},
}


@cache
def national_config() -> dict:
    from sports_edge.settings import project_root

    path = project_root() / "config" / "national_scope.toml"
    found = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return {key: {**value, **found.get(key, {})} for key, value in NATIONAL_DEFAULTS.items()}


NATIONAL_MAX_GAP_DAYS = NATIONAL_DEFAULTS["freshness"]["max_gap_days"]


def is_national(competitions) -> bool:
    """True for a scope made only of national competitions."""
    found = set(competitions or ())
    return bool(found) and found <= set(NATIONAL_CODES)


def pool_of(competition: str | None) -> str:
    """Rating pool of a competition: the national pool or the league itself."""
    return NATIONAL_POOL if competition in NATIONAL_CODES else str(competition or "")


def pools(competitions) -> list[str]:
    """Distinct pools of the competitions, in their order."""
    out: list[str] = []
    for code in competitions:
        pool = pool_of(code)
        if pool not in out:
            out.append(pool)
    return out


def group_by_pool(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        out[pool_of(row["competition"])].append(row)
    return out


def max_gap_days(pool: str, default: int) -> int:
    """Freshness limit of a pool (leagues: ``default``, 21 days)."""
    if pool != NATIONAL_POOL:
        return default
    return int(national_config()["freshness"]["max_gap_days"])


def elo_grid(competitions, default: dict) -> dict:
    """Elo hyperparameter grid of a scope (leagues: ``default``)."""
    if not is_national(competitions):
        return default
    elo = national_config()["elo"]
    return {
        "k": tuple(float(x) for x in elo["k"]),
        "home_advantage": tuple(float(x) for x in elo["home_advantage"]),
    }


def half_lives(competitions, default: tuple) -> tuple:
    """Dixon–Coles half-life grid of a scope (leagues: ``default``)."""
    if not is_national(competitions):
        return default
    return tuple(float(x) for x in national_config()["dixon_coles"]["half_life_days"])


def dc_window_days(pool: str, default: int = 730) -> int:
    """Dixon–Coles fitting window of a pool in days (leagues: ``default``)."""
    if pool != NATIONAL_POOL:
        return default
    return int(national_config()["dixon_coles"]["window_days"])


def retraining_rules(scope: str, min_new: int, min_days: int) -> tuple[int, int]:
    """(new matches, days between trainings) of a scope (leagues: the given values)."""
    if scope != NATIONAL_POOL:
        return min_new, min_days
    rules = national_config()["retraining"]
    return int(rules["min_new_results"]), int(rules["min_days_between_trainings"])


def country_key(value: str | None) -> str:
    plain = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", plain.lower())


def neutral_of(row: dict) -> bool:
    """True when a historical result gets no home advantage: a national match whose venue is
    unknown or outside the home team's country. Club results: always False."""
    if row.get("competition") not in NATIONAL_CODES:
        return False
    details = row.get("details_json")
    details = json.loads(details) if isinstance(details, str) else (details or {})
    venue = details.get("venue_country")
    if not venue:
        return True
    return country_key(venue) != country_key(details.get("home_country") or row.get("home"))


def event_venue(store, competition: str, event: dict, home: str, away: str, *, now: datetime):
    """(neutral, venue_country, venue_status) of an upcoming STS event.

    Home advantage only when the stored fixture list of the competition shows exactly one
    fixture of these teams that day, with a venue in the home team's country; otherwise the
    venue is "nieznane" and no home advantage is used. Club events: (False, None, "liga")."""
    if competition not in NATIONAL_CODES:
        return False, None, "liga"
    from sports_edge.international_data import fixtures

    day = event.get("offer_date") or str(event.get("start_at") or "")[:10]
    found = [
        f
        for f in fixtures(store, competition, until=now)
        if f.get("date") == day and f.get("home") == home and f.get("away") == away
    ]
    if len(found) != 1 or not found[0].get("venue_country"):
        return True, None, "nieznane — bez przewagi gospodarza"
    venue = found[0]["venue_country"]
    if country_key(venue) == country_key(found[0].get("home_country") or home):
        return False, venue, "gospodarz z terminarza"
    return True, venue, "neutralne — bez przewagi gospodarza"
