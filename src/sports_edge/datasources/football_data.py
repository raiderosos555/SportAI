"""football-data.org API v4 — current results of the five leagues (TASK 004).

Checked 2026-11-01 (docs.football-data.org/general/v4, football-data.org/about, /coverage,
/client/register):
* Free Tier after the user's own registration: 12 competitions including PL, BL1, PD, SA and
  FL1; 10 requests per minute; token in the ``X-Auth-Token`` header. Without a token only
  area and competition lists (100 requests / 24 h) are available.
* Terms (football-data.org/about, "last updated" 2018-06-01): visible attribution
  (``ATTRIBUTION``), one key per application, keys never in public repositories; after a
  cancellation the data may no longer be shown. Local retention and model training are not
  mentioned — a documented risk, acceptable only for private, non-commercial analysis.
* Match: id, utcDate, status (SCHEDULED, TIMED, IN_PLAY, PAUSED, FINISHED, SUSPENDED,
  POSTPONED, CANCELLED, AWARDED), score.fullTime / halfTime / duration, lastUpdated.

Availability: ``lastUpdated`` is the source's timestamp of the record's last change, so the
final result existed no later than that moment (basis ``source_last_updated``). Without it
the conservative 36-hour assumption is used and labelled as such.

TASK 009 (verified 2026-11-02 with the user's key): the free plan also returns the UEFA
Champions League ``CL`` (seasons 2023/24–2026/27; older seasons HTTP 403), the European
Championship ``EC`` (2024) and the World Cup ``WC`` (2026; 2022: 403). ``EC`` is the EURO of
national teams, NOT the Europa League (not available on the free plan). Cup scores:
``duration`` REGULAR / EXTRA_TIME / PENALTY_SHOOTOUT; ``fullTime`` then contains extra time
AND the shoot-out, so the stored score is ``regularTime`` and extra time and penalties are
kept separately. There is no leg field: legs follow from the stage, the clubs and the date.
"""

import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_edge.cups import FOOTBALL_DATA_STAGES
from sports_edge.datasources.base import (
    FetchOutcome,
    ProviderStatus,
    RateLimiter,
    ResultRecord,
    http_get,
)
from sports_edge.datasources.competitions import COMPETITIONS as CATALOGUE
from sports_edge.datasources.competitions import with_source
from sports_edge.offers.sources import SourceBlocked
from sports_edge.settings import local_secret

BASE = "https://api.football-data.org/v4"
ENV_KEY = "FOOTBALL_DATA_API_KEY"
ATTRIBUTION = "Football data provided by the Football-Data.org API"
LICENSE = (
    "football-data.org Free Tier (football-data.org/about): atrybucja wymagana, klucz na jedną "
    "aplikację; użycie prywatne, niekomercyjne"
)
COMPETITIONS = with_source("football_data")  # PL BL1 PD SA FL1 + CL, EC, WC (TASK 009)
STATUS_MAP = {
    "SCHEDULED": "scheduled",
    "TIMED": "scheduled",
    "IN_PLAY": "in_play",
    "PAUSED": "in_play",
    "LIVE": "in_play",
    "FINISHED": "finished",
    "AWARDED": "awarded",
    "POSTPONED": "postponed",
    "SUSPENDED": "suspended",
    "CANCELLED": "cancelled",
}
MAX_WINDOW_DAYS = 10  # longer gaps use one season request instead of many date windows
MIN_MATCH_MINUTES = 95  # a final record cannot be older than kick-off plus regular time


def api_key(root: Path | None = None) -> str | None:
    """Environment variable first, then the git-ignored ``.env`` of the project."""
    return local_secret(ENV_KEY, root)


def season_start(day: date) -> int:
    return day.year if day.month >= 7 else day.year - 1


class FootballDataOrgProvider:
    id = "football_data_org"
    name = "football-data.org API v4"
    license = LICENSE
    sports = ("football",)
    competitions = COMPETITIONS
    requires_network = True
    paid = False

    def __init__(
        self,
        key: str | None,
        *,
        opener=None,
        per_minute: int = 10,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.key = key
        self.opener = opener
        self.sleep = sleep
        self.limiter = RateLimiter(per_minute, sleep=sleep)

    def status(self, *, offline: bool) -> ProviderStatus:
        if offline:
            return ProviderStatus(False, "Tryb offline jest włączony")
        if not self.key:
            return ProviderStatus(
                False,
                f"Brak klucza {ENV_KEY} — bezpłatna rejestracja użytkownika na "
                "football-data.org/client/register; klucz w zmiennej środowiskowej lub w .env",
            )
        return ProviderStatus(True, "Aktywny (Free Tier, 10 zapytań/min)")

    def fetch(
        self, competition: str, date_from: date, date_to: date, *, now: datetime
    ) -> FetchOutcome:
        """Matches with ``date_from <= local date < date_to`` for one competition."""
        if competition not in COMPETITIONS:
            raise SourceBlocked(f"Rozgrywki {competition} poza zakresem Free Tier adaptera")
        if not self.key:
            raise SourceBlocked(f"Brak klucza {ENV_KEY}")
        code = COMPETITIONS[competition]
        if (date_to - date_from).days > MAX_WINDOW_DAYS:
            last_day = date_to - timedelta(days=1)
            if CATALOGUE[competition].kind == "national_tournament":  # seasons = calendar years
                seasons = list(range(date_from.year, last_day.year + 1))
            else:
                seasons = sorted({season_start(date_from), season_start(last_day)})
            queries = [f"season={year}" for year in seasons]
        else:  # the API excludes matches of the dateTo day itself
            queries = [f"dateFrom={date_from.isoformat()}&dateTo={date_to.isoformat()}"]
        records, payloads = [], []
        for query in queries:
            url = f"{BASE}/competitions/{code}/matches?{query}"
            data, _headers = http_get(
                url,
                headers={"X-Auth-Token": self.key},
                opener=self.opener,
                limiter=self.limiter,
                sleep=self.sleep,
            )
            payloads.append((url, data))
            records.extend(
                r
                for r in parse(data, competition=competition, observed_at=now)
                if date_from.isoformat() <= r.match_date < date_to.isoformat()
            )
        return FetchOutcome(records=records, payloads=payloads, requests=len(queries))


def parse(data: bytes, *, competition: str, observed_at: datetime) -> list[ResultRecord]:
    """Standardised records; unknown statuses stay ``unknown`` (never guessed)."""
    league = CATALOGUE[competition]
    zone = ZoneInfo(league.zone)
    cup = league.category != "domestic"
    payload = json.loads(data)
    out = []
    for match in payload.get("matches", []):
        start = _instant(match.get("utcDate"))
        status = STATUS_MAP.get(str(match.get("status", "")).upper(), "unknown")
        score = match.get("score") or {}
        full = score.get("fullTime") or {}
        half = score.get("halfTime") or {}
        duration = score.get("duration")
        home_score, away_score = full.get("home"), full.get("away")
        aet = pens = None
        if duration not in (None, "REGULAR"):
            regular = score.get("regularTime") or {}
            extra = score.get("extraTime") or {}
            home_score, away_score = regular.get("home"), regular.get("away")
            if None not in (home_score, away_score, extra.get("home"), extra.get("away")):
                aet = [home_score + extra["home"], away_score + extra["away"]]
            shootout = score.get("penalties") or {}
            if duration == "PENALTY_SHOOTOUT" and shootout.get("home") is not None:
                pens = [shootout.get("home"), shootout.get("away")]
        updated = _instant(match.get("lastUpdated"))
        if updated and start and updated < start + timedelta(minutes=MIN_MATCH_MINUTES):
            updated = None  # inconsistent with a final record: fall back to the assumption
        season = match.get("season") or {}
        home, away = match.get("homeTeam") or {}, match.get("awayTeam") or {}
        extra_details = {}
        if cup:  # TASK 009: stage, extra time and penalties of cups and tournaments
            raw_stage = str(match.get("stage") or "")
            extra_details = {
                "stage": FOOTBALL_DATA_STAGES.get(raw_stage, "other"),
                "stage_label": raw_stage,
                "group": match.get("group"),
                "aet": aet,
                "pens": pens,
                "regulation_certain": True,
            }
        out.append(
            ResultRecord(
                provider=FootballDataOrgProvider.id,
                sport="football",
                competition=competition,
                season=(
                    season.get("startDate", "")[:4] or None
                    if league.kind == "national_tournament"
                    else _season_label(season.get("startDate"))
                ),
                source_event_id=str(match["id"]) if match.get("id") is not None else None,
                home=home.get("name") or "",
                away=away.get("name") or "",
                start_at=start,
                match_date=(start.astimezone(zone).date().isoformat() if start else ""),
                status=status,
                home_score=home_score,
                away_score=away_score,
                details={
                    "ht": [half.get("home"), half.get("away")]
                    if half.get("home") is not None
                    else None,
                    "duration": duration,
                    "winner": score.get("winner"),
                    "matchday": match.get("matchday"),
                    "home_id": home.get("id"),
                    "away_id": away.get("id"),
                    "home_short": home.get("shortName"),
                    "away_short": away.get("shortName"),
                    "source": FootballDataOrgProvider.name,
                    "attribution": ATTRIBUTION,
                    **extra_details,
                },
                published_at=updated,
                observed_at=observed_at,
                license=LICENSE,
            )
        )
    return out


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _season_label(start: str | None) -> str | None:
    if not start:
        return None
    year = int(start[:4])
    return f"{year}-{str(year + 1)[2:]}"
