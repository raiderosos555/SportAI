"""openfootball/football.json — historical football results under CC0-1.0 (no odds).

Checked 2026-11-01: seasons 2010-11 to 2026-27, top leagues of England, Germany, Spain,
Italy, France and more (no Poland). Kick-off times are local times of the league country
without a time zone; they are converted with the league's IANA zone (documented
assumption). The repository was last pushed on 2026-09-22, so the current season is not a
reliable source of new results. There are no publication timestamps: result availability
is an assumption (see ``AVAILABILITY_BASIS``).
"""

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_edge.datasources.base import FetchOutcome, ProviderStatus, ResultRecord, http_get
from sports_edge.offers.sources import USER_AGENT, SourceBlocked

BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"
LICENSE = "CC0-1.0 — public domain dedication (github.com/openfootball/football.json)"
AVAILABILITY_BASIS = "assumed_lag_36h"
AVAILABILITY_LAG = timedelta(hours=36)


@dataclass(frozen=True)
class League:
    code: str
    name: str
    zone: str
    sts_leagues: tuple[str, ...]  # STS league headers covering the same competition


LEAGUES = {
    "en.1": League("en.1", "Premier League", "Europe/London", ("ANGLIA - PREMIER LEAGUE",)),
    "de.1": League("de.1", "Bundesliga", "Europe/Berlin", ("NIEMCY - BUNDESLIGA",)),
    "es.1": League("es.1", "LaLiga", "Europe/Madrid", ("HISZPANIA - LALIGA",)),
    "it.1": League("it.1", "Serie A", "Europe/Rome", ("WŁOCHY - SERIE A",)),
    "fr.1": League("fr.1", "Ligue 1", "Europe/Paris", ("FRANCJA - LIGUE 1",)),
}


def season_range(first: str, last: str) -> list[str]:
    start, end = int(first[:4]), int(last[:4])
    return [f"{y}-{str(y + 1)[2:]}" for y in range(start, end + 1)]


def fetch(
    target: Path, *, leagues: list[str], seasons: list[str], offline: bool, opener=None
) -> dict:
    """Download missing files into ``target`` and record URL, SHA-256 and time in a manifest."""
    if offline:
        raise SourceBlocked("Tryb offline jest włączony")
    manifest_path = target / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    downloaded, skipped, missing = [], [], []
    for season in seasons:
        for code in leagues:
            relative = f"{season}/{code}.json"
            path = target / relative
            if path.exists():
                skipped.append(relative)
                continue
            request = urllib.request.Request(
                f"{BASE}/{relative}", headers={"User-Agent": USER_AGENT}
            )
            try:
                with (opener or urllib.request.urlopen)(request, timeout=60) as response:
                    data = response.read(5_000_000)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    missing.append(relative)
                    continue
                raise SourceBlocked(f"HTTP {error.code} dla {relative}") from error
            json.loads(data)  # refuse anything that is not JSON
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            manifest[relative] = {
                "url": f"{BASE}/{relative}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "license": LICENSE,
            }
            downloaded.append(relative)
    target.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return {"downloaded": downloaded, "cached": skipped, "missing": missing}


class OpenfootballProvider:
    """Results provider over the CC0 files: re-reads the season files covering a window.

    Kept as the long-history source (priority below football-data.org); its current
    season is only as fresh as the repository (last push 2026-09-22 when checked).
    """

    id = "openfootball"
    name = "openfootball/football.json"
    license = LICENSE
    sports = ("football",)
    competitions = {code: code for code in LEAGUES}
    requires_network = True
    paid = False

    def __init__(self, *, opener=None):
        self.opener = opener

    def status(self, *, offline: bool) -> ProviderStatus:
        if offline:
            return ProviderStatus(False, "Tryb offline jest włączony")
        return ProviderStatus(True, "Aktywny (CC0); repozytorium nie publikuje wyników na bieżąco")

    def fetch(
        self, competition: str, date_from: date, date_to: date, *, now: datetime
    ) -> FetchOutcome:
        if competition not in LEAGUES:
            raise SourceBlocked(f"Rozgrywki {competition} poza zakresem openfootball")
        league = LEAGUES[competition]
        seasons = sorted({_season(date_from), _season(date_to - timedelta(days=1))})
        records, payloads = [], []
        for season in seasons:
            url = f"{BASE}/{season}/{competition}.json"
            data, _headers = http_get(url, headers={}, opener=self.opener)
            payloads.append((url, data))
            for match in parse(data, league=league, season=season):
                if not date_from.isoformat() <= match["match_date"] < date_to.isoformat():
                    continue
                records.append(
                    ResultRecord(
                        provider=self.id,
                        sport="football",
                        competition=competition,
                        season=season,
                        source_event_id=None,
                        home=match["home"],
                        away=match["away"],
                        start_at=match["start_at"],
                        match_date=match["match_date"],
                        status="finished",
                        home_score=match["home_score"],
                        away_score=match["away_score"],
                        details=match["details"],
                        published_at=None,
                        observed_at=now,
                        license=LICENSE,
                    )
                )
        return FetchOutcome(records=records, payloads=payloads, requests=len(seasons))


def _season(day: date) -> str:
    year = day.year if day.month >= 7 else day.year - 1
    return f"{year}-{str(year + 1)[2:]}"


def parse(data: bytes, *, league: League, season: str) -> list[dict]:
    """Completed matches only; fixtures without a full-time score are skipped."""
    zone = ZoneInfo(league.zone)
    out = []
    for match in json.loads(data)["matches"]:
        score = match.get("score")
        ft = score.get("ft") if isinstance(score, dict) else score
        if not (isinstance(ft, list) and len(ft) == 2 and all(isinstance(v, int) for v in ft)):
            continue
        ht = score.get("ht") if isinstance(score, dict) else None
        played = date.fromisoformat(match["date"])
        start = None
        if match.get("time"):
            hour, minute = (int(x) for x in match["time"].split(":")[:2])
            start = datetime.combine(played, time(hour, minute), zone).astimezone(UTC)
        midnight = datetime.combine(played, time(0), UTC)
        out.append(
            {
                "match_date": played.isoformat(),
                "start_at": start,
                "home": match["team1"],
                "away": match["team2"],
                "home_score": ft[0],
                "away_score": ft[1],
                "available_at": max(midnight, start or midnight) + AVAILABILITY_LAG,
                "details": {
                    "round": match.get("round"),
                    "ht": ht if isinstance(ht, list) else None,
                    "source": "openfootball",
                    "league": league.code,
                    "season": season,
                },
            }
        )
    return out
