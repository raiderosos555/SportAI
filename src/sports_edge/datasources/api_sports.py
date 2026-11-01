"""API-Sports (api-football.com and its sister APIs) — free plan with the user's own key.

Checked 2026-11-01. What is known and what is not:
* The website, pricing and terms sit behind a Cloudflare challenge and could not be read
  automatically (not worked around). The user accepts the terms when registering at
  dashboard.api-football.com; until they are confirmed, use is private and non-commercial.
* Vendor guide (api-football.com/news, via search): free plan = 100 requests per day per API,
  every endpoint, recent seasons only (paid plans unlock deeper history).
* Authentication: header ``x-apisports-key`` (one key for all API-Sports APIs). Errors arrive
  with HTTP 200 in the ``errors`` field (plan does not cover a season, daily limit, key) and
  stop the adapter with the provider's own message.
* Football v3: ``/fixtures?league&season&from&to`` (dates inclusive); ``score.fulltime`` is the
  regular-time score, ``score.halftime`` the first half. There is no record update timestamp,
  so availability falls back to the moment the result was observed (``results_engine``).
* Basketball and hockey v1 (``/games?league&season``): schema and league ids VERIFIED on
  2026-11-01 with the user's key on allowed seasons — NBA (12, seasons "2023-2024"),
  Euroleague (120, seasons 2023), NHL (57, seasons 2023): quarters / periods, totals, FT / AOT
  / AP statuses. Records whose periods do not add up to the final score (seen in real NHL
  data) are refused. Volleyball stays unverified.
* FREE PLAN LIMIT (verified 2026-11-01, every football league): "Free plans do not have access
  to this season, try from 2022 to 2024." — no current-season results on the free plan. The
  refusal is remembered for ``PLAN_RECHECK_DAYS`` so updates do not waste the daily quota.
"""

import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_edge.datasources.base import (
    FetchOutcome,
    ProviderStatus,
    RateLimiter,
    ResultRecord,
    http_get,
)
from sports_edge.datasources.competitions import COMPETITIONS
from sports_edge.offers.sources import SourceBlocked

ENV_KEY = "API_SPORTS_KEY"
REGISTER_URL = "https://dashboard.api-football.com/register"
LICENSE = (
    "API-Sports — plan darmowy z kluczem użytkownika; regulamin akceptowany przy rejestracji "
    "(niezweryfikowany automatycznie); użycie prywatne, niekomercyjne"
)
HOSTS = {
    "football": "https://v3.football.api-sports.io",
    "basketball": "https://v1.basketball.api-sports.io",
    "ice_hockey": "https://v1.hockey.api-sports.io",
    "volleyball": "https://v1.volleyball.api-sports.io",
}
DAILY_LIMIT = 100
RESERVE = 10  # requests kept for settlement checks and manual verification
FOOTBALL_LEAGUES = {"en.1": 39, "de.1": 78, "es.1": 140, "it.1": 135, "fr.1": 61, "pl.1": 106}
# (league id, season format) — confirm with /leagues before activating these sports.
GAME_LEAGUES = {
    "basketball.nba": (12, "split"),
    "basketball.euroleague": (120, "year"),
    "ice_hockey.nhl": (57, "year"),
}
VERIFIED_SPORTS: frozenset[str] = frozenset({"football", "basketball", "ice_hockey"})
PLAN_RECHECK_DAYS = 7
FOOTBALL_STATUS = {
    "TBD": "scheduled",
    "NS": "scheduled",
    "1H": "in_play",
    "HT": "in_play",
    "2H": "in_play",
    "ET": "in_play",
    "BT": "in_play",
    "P": "in_play",
    "LIVE": "in_play",
    "SUSP": "suspended",
    "INT": "suspended",
    "FT": "finished",
    "AET": "finished",
    "PEN": "finished",
    "PST": "postponed",
    "CANC": "cancelled",
    "ABD": "cancelled",
    "AWD": "awarded",
    "WO": "awarded",
}
GAME_STATUS = {
    "NS": "scheduled",
    "TBD": "scheduled",
    "Q1": "in_play",
    "Q2": "in_play",
    "Q3": "in_play",
    "Q4": "in_play",
    "P1": "in_play",
    "P2": "in_play",
    "P3": "in_play",
    "S1": "in_play",
    "S2": "in_play",
    "S3": "in_play",
    "S4": "in_play",
    "S5": "in_play",
    "OT": "in_play",
    "PT": "in_play",
    "BT": "in_play",
    "HT": "in_play",
    "LIVE": "in_play",
    "FT": "finished",
    "AOT": "finished",
    "AP": "finished",
    "POST": "postponed",
    "CANC": "cancelled",
    "INTR": "suspended",
    "SUSP": "suspended",
    "ABD": "cancelled",
    "AW": "awarded",
    "AWD": "awarded",
}


class DailyQuota:
    """Requests used per UTC day and API, persisted so separate runs share the limit."""

    def __init__(self, path: Path | None, *, limit: int = DAILY_LIMIT, reserve: int = RESERVE):
        self.path, self.limit, self.reserve = path, limit, reserve
        self.memory: dict = {}

    def _load(self) -> dict:
        if self.path is None:
            return self.memory
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        if self.path is None:
            self.memory = data
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")

    def remaining(self, api: str, day: date) -> int:
        entry = self._load().get(day.isoformat(), {}).get(api, {})
        left = self.limit - entry.get("used", 0)
        if entry.get("server_remaining") is not None:
            left = min(left, entry["server_remaining"])
        return left

    def allows(self, api: str, day: date) -> bool:
        return self.remaining(api, day) > self.reserve

    def record(self, api: str, day: date, *, server_remaining: int | None) -> None:
        data = self._load()
        entry = data.setdefault(day.isoformat(), {}).setdefault(api, {"used": 0})
        entry["used"] += 1
        if server_remaining is not None:
            entry["server_remaining"] = server_remaining
        self._save(data)

    def record_plan_limit(self, api: str, day: date, message: str) -> None:
        data = self._load()
        data.setdefault("plan_limits", {})[api] = {"since": day.isoformat(), "message": message}
        self._save(data)

    def plan_limit(self, api: str, day: date) -> str | None:
        """Plan refusal remembered for a few days (then checked again with one request)."""
        found = self._load().get("plan_limits", {}).get(api)
        if not found:
            return None
        if (day - date.fromisoformat(found["since"])).days >= PLAN_RECHECK_DAYS:
            return None
        return found["message"]


class _ApiSportsBase:
    id = "api_sports_football"  # unique per adapter (status, raw files)
    family = "api_sports"  # one source in the hierarchy and in stored rows
    license = LICENSE
    requires_network = True
    paid = False
    sport = "football"

    def __init__(
        self,
        key: str | None,
        *,
        quota_path: Path | None = None,
        opener=None,
        per_minute: int = 10,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.key = key
        self.opener = opener
        self.sleep = sleep
        self.limiter = RateLimiter(per_minute, sleep=sleep)
        self.quota = DailyQuota(quota_path)

    def status(self, *, offline: bool) -> ProviderStatus:
        if offline:
            return ProviderStatus(False, "Tryb offline jest włączony")
        if not self.key:
            return ProviderStatus(
                False, f"Brak klucza {ENV_KEY} — bezpłatna rejestracja: {REGISTER_URL}"
            )
        if self.sport not in VERIFIED_SPORTS:
            return ProviderStatus(
                False,
                "Schemat odpowiedzi i identyfikatory lig niezweryfikowane na prawdziwym kluczu "
                "— adapter nieaktywny",
            )
        today = datetime.now(UTC).date()
        if limit := self.quota.plan_limit(self.sport, today):
            return ProviderStatus(
                False,
                f"Plan darmowy nie obejmuje bieżącego sezonu ({limit}) — bieżące wyniki wymagają "
                "płatnego planu (decyzja użytkownika)",
            )
        if not self.quota.allows(self.sport, today):
            return ProviderStatus(False, "Dzienny limit zapytań API-Sports wyczerpany (z rezerwą)")
        left = self.quota.remaining(self.sport, today)
        return ProviderStatus(True, f"Aktywny (plan darmowy; pozostało dziś ok. {left} zapytań)")

    def _get(self, path: str, *, now: datetime) -> tuple[dict, bytes, str]:
        if not self.key:
            raise SourceBlocked(f"Brak klucza {ENV_KEY}")
        day = now.astimezone(UTC).date()
        if not self.quota.allows(self.sport, day):
            raise SourceBlocked("Dzienny limit zapytań API-Sports wyczerpany (z rezerwą)")
        url = f"{HOSTS[self.sport]}{path}"
        data, headers = http_get(
            url,
            headers={"x-apisports-key": self.key},
            opener=self.opener,
            limiter=self.limiter,
            sleep=self.sleep,
        )
        lower = {k.lower(): v for k, v in headers.items()}
        remaining = lower.get("x-ratelimit-requests-remaining")
        self.quota.record(
            self.sport,
            day,
            server_remaining=int(remaining) if str(remaining or "").isdigit() else None,
        )
        try:
            payload = json.loads(data)
        except ValueError as error:
            raise SourceBlocked("API-Sports: odpowiedź nie jest JSON") from error
        errors = payload.get("errors")
        if errors:
            messages = errors.values() if isinstance(errors, dict) else errors
            text = "; ".join(str(m) for m in messages)
            if isinstance(errors, dict) and "plan" in errors:
                self.quota.record_plan_limit(self.sport, day, str(errors["plan"]))
            raise SourceBlocked("API-Sports: " + text)
        return payload, data, url


class ApiSportsFootballProvider(_ApiSportsBase):
    """Football results, incl. Ekstraklasa (not covered by football-data.org Free Tier)."""

    name = "API-Sports Football v3"
    sports = ("football",)
    competitions = FOOTBALL_LEAGUES
    sport = "football"

    def fetch(
        self, competition: str, date_from: date, date_to: date, *, now: datetime
    ) -> FetchOutcome:
        if competition not in FOOTBALL_LEAGUES:
            raise SourceBlocked(f"Rozgrywki {competition} poza zakresem adaptera")
        last_day = date_to - timedelta(days=1)
        seasons = sorted({_season_year(date_from), _season_year(last_day)})
        records, payloads, requests = [], [], 0
        for season in seasons:
            page, pages = 1, 1
            while page <= pages:
                query = (
                    f"/fixtures?league={FOOTBALL_LEAGUES[competition]}&season={season}"
                    f"&from={date_from.isoformat()}&to={last_day.isoformat()}&timezone=UTC"
                    + (f"&page={page}" if page > 1 else "")
                )
                payload, data, url = self._get(query, now=now)
                requests += 1
                payloads.append((url, data))
                records.extend(parse_football(payload, competition=competition, observed_at=now))
                pages = int((payload.get("paging") or {}).get("total") or 1)
                page += 1
        window = [r for r in records if date_from.isoformat() <= r.match_date < date_to.isoformat()]
        return FetchOutcome(records=window, payloads=payloads, requests=requests)


class ApiSportsGamesProvider(_ApiSportsBase):
    """Basketball / ice hockey / volleyball games — inactive until verified with a real key."""

    def __init__(self, key: str | None, *, sport: str, **kwargs):
        super().__init__(key, **kwargs)
        self.sport = sport
        self.id = f"api_sports_{sport}"
        self.sports = (sport,)
        self.name = f"API-Sports {sport} v1"
        self.competitions = {
            code: league
            for code, league in GAME_LEAGUES.items()
            if COMPETITIONS[code].sport == sport
        }

    def fetch(
        self, competition: str, date_from: date, date_to: date, *, now: datetime
    ) -> FetchOutcome:
        if competition not in self.competitions:
            raise SourceBlocked(f"Rozgrywki {competition} poza zakresem adaptera")
        league, season_format = self.competitions[competition]
        last_day = date_to - timedelta(days=1)
        records, payloads = [], []
        seasons = sorted({_season_year(date_from), _season_year(last_day)})
        for year in seasons:
            season = f"{year}-{year + 1}" if season_format == "split" else str(year)
            payload, data, url = self._get(
                f"/games?league={league}&season={season}&timezone=UTC", now=now
            )
            payloads.append((url, data))
            records.extend(
                parse_games(payload, competition=competition, sport=self.sport, observed_at=now)
            )
        window = [r for r in records if date_from.isoformat() <= r.match_date < date_to.isoformat()]
        return FetchOutcome(records=window, payloads=payloads, requests=len(seasons))


def parse_football(payload: dict, *, competition: str, observed_at: datetime) -> list:
    zone = ZoneInfo(COMPETITIONS[competition].zone)
    out = []
    for item in payload.get("response", []):
        fixture, teams = item.get("fixture") or {}, item.get("teams") or {}
        score = item.get("score") or {}
        short = str((fixture.get("status") or {}).get("short", "")).upper()
        status = FOOTBALL_STATUS.get(short, "unknown")
        start = _instant(fixture.get("date"))
        full, half = score.get("fulltime") or {}, score.get("halftime") or {}
        home_team, away_team = teams.get("home") or {}, teams.get("away") or {}
        out.append(
            ResultRecord(
                provider="api_sports",
                sport="football",
                competition=competition,
                season=_season_label((item.get("league") or {}).get("season")),
                source_event_id=str(fixture["id"]) if fixture.get("id") is not None else None,
                home=home_team.get("name") or "",
                away=away_team.get("name") or "",
                start_at=start,
                match_date=start.astimezone(zone).date().isoformat() if start else "",
                status=status,
                home_score=full.get("home") if status in {"finished", "awarded"} else None,
                away_score=full.get("away") if status in {"finished", "awarded"} else None,
                details={
                    "ht": [half.get("home"), half.get("away")]
                    if half.get("home") is not None
                    else None,
                    "status_short": short,
                    "duration": {"AET": "EXTRA_TIME", "PEN": "PENALTY_SHOOTOUT"}.get(
                        short, "REGULAR"
                    ),
                    "goals": [(item.get("goals") or {}).get(k) for k in ("home", "away")],
                    "round": (item.get("league") or {}).get("round"),
                    "home_id": home_team.get("id"),
                    "away_id": away_team.get("id"),
                    "source": "API-Sports",
                },
                published_at=None,
                observed_at=observed_at,
                license=LICENSE,
            )
        )
    return out


def parse_games(payload: dict, *, competition: str, sport: str, observed_at: datetime) -> list:
    """Regular-time score in home/away (OT and shoot-outs in details) — documented shape."""
    zone = ZoneInfo(COMPETITIONS[competition].zone)
    out = []
    for item in payload.get("response", []):
        short = str((item.get("status") or {}).get("short", "")).upper()
        status = GAME_STATUS.get(short, "unknown")
        start = _instant(item.get("date"))
        teams = item.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        regular, final, periods = _game_scores(item, sport)
        problem = _inconsistency(sport, short, regular, final)
        if problem:
            regular = (None, None)  # never settle or model from contradictory numbers
        out.append(
            ResultRecord(
                provider="api_sports",
                sport=sport,
                competition=competition,
                season=str((item.get("league") or {}).get("season") or "") or None,
                source_event_id=str(item["id"]) if item.get("id") is not None else None,
                home=home.get("name") or "",
                away=away.get("name") or "",
                start_at=start,
                match_date=start.astimezone(zone).date().isoformat() if start else "",
                status=status,
                home_score=regular[0] if status in {"finished", "awarded"} else None,
                away_score=regular[1] if status in {"finished", "awarded"} else None,
                details={
                    "status_short": short,
                    "periods": periods,
                    "after_ot": list(final)
                    if final != regular and None not in final and None not in regular
                    else None,
                    "inconsistent": problem,
                    "source": "API-Sports",
                },
                published_at=None,
                observed_at=observed_at,
                license=LICENSE,
            )
        )
    return out


def _game_scores(item: dict, sport: str) -> tuple[tuple, tuple, dict]:
    scores = item.get("scores") or {}
    if sport == "basketball":
        home, away = scores.get("home") or {}, scores.get("away") or {}
        quarters = [f"quarter_{i}" for i in range(1, 5)]
        regular = tuple(
            sum(side[q] for q in quarters)
            if all(side.get(q) is not None for q in quarters)
            else None
            for side in (home, away)
        )
        final = (home.get("total"), away.get("total"))
        periods = {q: [home.get(q), away.get(q)] for q in quarters + ["over_time"]}
        return regular, final, periods
    if sport == "ice_hockey":
        raw = item.get("periods") or {}
        parts = [_pair(raw.get(name)) for name in ("first", "second", "third")]
        regular = (
            tuple(sum(p[i] for p in parts) for i in (0, 1))
            if all(p is not None for p in parts)
            else (None, None)
        )
        final = (scores.get("home"), scores.get("away"))
        return regular, final, {k: v for k, v in raw.items()}
    # volleyball: sets won is the match score
    final = (scores.get("home"), scores.get("away"))
    return final, final, item.get("periods") or {}


def _inconsistency(sport: str, short: str, regular: tuple, final: tuple) -> str | None:
    """Periods must add up: a regular-time finish equals the total; after OT or a shoot-out
    the regular time is a draw. Real NHL data contained FT records breaking both rules."""
    if sport not in {"basketball", "ice_hockey"} or None in regular or None in final:
        return None
    if short == "FT" and regular != final:
        return f"suma okresów {regular[0]}:{regular[1]} ≠ wynik {final[0]}:{final[1]}"
    if short in {"AOT", "AP"} and regular[0] != regular[1]:
        return f"po dogrywce, ale czas regulaminowy {regular[0]}:{regular[1]} nie jest remisem"
    return None


def _pair(value) -> tuple[int, int] | None:
    if not isinstance(value, str) or "-" not in value:
        return None
    left, right = value.split("-", 1)
    return (int(left), int(right)) if left.strip().isdigit() and right.strip().isdigit() else None


def _season_year(day: date) -> int:
    return day.year if day.month >= 7 else day.year - 1


def _season_label(value) -> str | None:
    if value is None:
        return None
    year = int(str(value)[:4])
    return f"{year}-{str(year + 1)[2:]}"


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
