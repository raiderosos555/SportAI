"""PitchAPI v1 — results and detailed match statistics (TASK 005), the user's own key.

Verified 2026-11-01 with the user's key (``X-API-KEY``):
* 77 leagues, every one flagged ``is_free``; Premier League, Bundesliga, LaLiga, Serie A,
  Ligue 1 (seasons 2021/2022–2026/2027) and Ekstraklasa (2025/2026–2026/2027). Ekstraklasa
  2026/2027 had 112 finished matches up to 31.10.2026.
* ``/v1/leagues/{id}/matches?season=YYYY/YYYY&status=all`` — fixtures and results;
  ``/v1/matches/{id}/stats`` — statistics for All / FirstHalf / SecondHalf (xG, shots, shots on
  target, corners, cards, fouls, possession, big chances, passes, duels);
  ``/v1/matches/{id}/lineups`` — formations, starters, ``confirmed`` flag.
* No fixed request allowance (fair-use burst guard, 429 + Retry-After); requests are paced.
* Terms of service were not found publicly (the SDK says the data is governed by PitchAPI's
  terms): private, non-commercial analysis only; nothing is redistributed. There are no
  publication timestamps, so availability is the moment this application observed the data.

TASK 009 (verified 2026-11-02 in /v1/leagues and season lists): Champions League
``l_0bfbkO``, Europa League ``l_38d9HA`` and Conference League ``l_2WZ2tt`` (seasons
2021/2022–2026/2027, league or group phase and knockout rounds, no qualifying rounds). The
list has no stage and its score COUNTS EXTRA-TIME GOALS (penalties excluded), e.g. a 3:0
after 90 minutes and 0:2 in extra time is listed as 3:2; such a score is resolved with
``cups.regulation_from_reported`` and otherwise marked as regulation-uncertain. The World Cup
id (``l_1XLtP0``) is in the catalogue, but its season format was not verified, so this
adapter does not fetch it.
"""

import json
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sports_edge.cups import regulation_from_reported
from sports_edge.datasources.base import (
    FetchOutcome,
    ProviderStatus,
    RateLimiter,
    ResultRecord,
    http_get,
)
from sports_edge.datasources.competitions import COMPETITIONS, with_source
from sports_edge.offers.sources import SourceBlocked

ENV_KEY = "PITCHAPI_KEY"
BASE = "https://api.pitchapi.dev"
LICENSE = (
    "PitchAPI — plan darmowy z kluczem użytkownika; regulamin nieopublikowany "
    "(użycie prywatne, bez redystrybucji)"
)
LEAGUES = {
    "en.1": "l_4WFCIZ",
    "de.1": "l_1Isor4",
    "es.1": "l_0ErfuF",
    "it.1": "l_0ALvwF",
    "fr.1": "l_3FJFUl",
    "pl.1": "l_214sqK",
    # TASK 006 (seasons verified in /v1/leagues on 01.11.2026)
    "en.2": "l_4DWyTT",  # Championship 2021/22-2026/27
    "nl.1": "l_4H43wr",  # Eredivisie 2021/22-2026/27
    "pt.1": "l_4QexZg",  # Liga Portugal 2021/22-2026/27
    "be.1": "l_2L6d1F",  # First Division A 2021/22-2026/27
    "de.2": "l_3hvsne",  # 2. Bundesliga 2021/22-2026/27
    "tr.1": "l_0S1uaf",  # Super Lig 2022/23-2026/27
    # TASK 009: European club cups from the catalogue (identifiers verified 02.11.2026)
    **{
        code: league
        for code, league in with_source("pitchapi").items()
        if COMPETITIONS[code].category == "european"
    },
}
STATUS = {
    "finished": "finished",
    "not_started": "scheduled",
    "postponed": "postponed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "live": "in_play",
    "in_progress": "in_play",
}


def season_label(day: date) -> str:
    start = day.year if day.month >= 7 else day.year - 1
    return f"{start}/{start + 1}"


def _number(value) -> float | None:
    """'13 (39%)' → 13.0; '2.61' → 2.61; missing → None."""
    if value is None:
        return None
    text = str(value).split(" ")[0].strip().rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def parse_stats(payload: dict) -> dict[str, dict[str, list[float | None]]]:
    """{period: {stat key: [home, away]}} from the stats endpoint."""
    out: dict[str, dict[str, list[float | None]]] = {}
    for period in (payload.get("data") or {}).get("periods") or []:
        values: dict[str, list[float | None]] = {}
        for group in period.get("groups") or []:
            for item in group.get("items") or []:
                values.setdefault(
                    item["key"], [_number(item.get("home")), _number(item.get("away"))]
                )
        out[period.get("period", "All")] = values
    return out


def parse_lineups(payload: dict) -> dict[str, dict]:
    """``/v1/matches/{id}/lineups`` → per side: formation, confirmed flag, coach, players."""
    data = payload.get("data") or {}
    out = {}
    for side in ("home", "away"):
        part = data.get(side) or {}
        out[side] = {
            "team": (data.get(f"{side}_team") or {}).get("name"),
            "formation": part.get("formation"),
            "confirmed": bool(part.get("confirmed")),
            "coach": (part.get("coach") or {}).get("name"),
            "starters": [
                {"name": p.get("name"), "number": p.get("shirt_number")}
                for p in part.get("starters") or []
            ],
            "subs": [
                {"name": p.get("name"), "number": p.get("shirt_number")}
                for p in part.get("subs") or []
            ],
        }
    return out


class PitchApiProvider:
    id = "pitchapi"
    family = "pitchapi"
    name = "PitchAPI v1"
    license = LICENSE
    sports = ("football",)
    competitions = LEAGUES
    requires_network = True
    paid = False

    def __init__(
        self,
        key: str | None,
        *,
        opener=None,
        per_minute: int = 30,
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
            return ProviderStatus(False, f"Brak klucza {ENV_KEY} (bezpłatna rejestracja)")
        return ProviderStatus(True, "Aktywny (plan darmowy, uczciwe korzystanie)")

    def get(self, path: str) -> tuple[dict, bytes, str]:
        if not self.key:
            raise SourceBlocked(f"Brak klucza {ENV_KEY}")
        url = f"{BASE}{path}"
        data, _headers = http_get(
            url,
            headers={"X-API-KEY": self.key},
            opener=self.opener,
            limiter=self.limiter,
            sleep=self.sleep,
        )
        try:
            return json.loads(data), data, url
        except ValueError as error:
            raise SourceBlocked("PitchAPI: odpowiedź nie jest JSON") from error

    def season_matches(self, competition: str, season: str) -> tuple[list[dict], bytes, str]:
        payload, data, url = self.get(
            f"/v1/leagues/{LEAGUES[competition]}/matches?season={quote(season, safe='')}&status=all"
        )
        return (payload.get("data") or {}).get("matches") or [], data, url

    def fetch(
        self, competition: str, date_from: date, date_to: date, *, now: datetime
    ) -> FetchOutcome:
        if competition not in LEAGUES:
            raise SourceBlocked(f"Rozgrywki {competition} poza zakresem PitchAPI")
        zone = ZoneInfo(COMPETITIONS[competition].zone)
        cup = COMPETITIONS[competition].category != "domestic"
        seasons = sorted({season_label(date_from), season_label(date_to - timedelta(days=1))})
        records, payloads = [], []
        for season in seasons:
            matches, data, url = self.season_matches(competition, season)
            payloads.append((url, data))
            season_records = []
            for match in matches:
                start = _instant(match.get("time_utc"))
                local = (
                    start.astimezone(zone).date().isoformat() if start else match.get("date", "")
                )
                status = STATUS.get(str(match.get("status", "")).lower(), "unknown")
                done = status == "finished"
                season_records.append(
                    ResultRecord(
                        provider=self.family,
                        sport="football",
                        competition=competition,
                        season=season.replace("/", "-")[:5] + season[-2:],
                        source_event_id=match.get("id"),
                        home=(match.get("home_team") or {}).get("name") or "",
                        away=(match.get("away_team") or {}).get("name") or "",
                        start_at=start,
                        match_date=local,
                        status=status,
                        home_score=match.get("score_home") if done else None,
                        away_score=match.get("score_away") if done else None,
                        details={
                            "pitch_match_id": match.get("id"),
                            "home_id": (match.get("home_team") or {}).get("id"),
                            "away_id": (match.get("away_team") or {}).get("id"),
                            "source": self.name,
                        },
                        published_at=None,
                        observed_at=now,
                        license=LICENSE,
                    )
                )
            if cup:  # the whole season decides legs and extra time (TASK 009)
                season_records = regulation_from_reported(season_records, competition=competition)
            window = (date_from.isoformat(), date_to.isoformat())
            records += [r for r in season_records if window[0] <= r.match_date < window[1]]
        return FetchOutcome(records=records, payloads=payloads, requests=len(seasons))


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
