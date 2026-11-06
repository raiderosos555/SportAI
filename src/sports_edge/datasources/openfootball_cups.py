"""openfootball/champions-league — European club cups under CC0-1.0 (TASK 009).

Verified 2026-11-02: seasons 2011-12 to 2025-26 (last push 2026-07-02, so the current season
2026-27 is not there); ``cl.txt`` every season, ``el.txt`` and ``conf.txt`` in some seasons,
qualifying rounds (``clq.txt``, ``elq.txt``, ``confq.txt``) only in 2025-26. The format is
Football.TXT:

* stages ``▪ Group A`` / ``▪ Gruppe H`` / ``▪ League, Matchday 3`` / ``▪ Playoffs`` /
  ``▪ Finals, Round of 16`` / ``▪ Final`` / ``▪ 1. Round`` (qualifying);
* clubs with their country: ``Real Madrid CF (ESP)``;
* scores: ``2-0 (1-0)`` — 90 minutes (half time); ``3-2 a.e.t. (2-2, 0-2)`` — after extra
  time (90 minutes, half time); ``4-2 pen. 1-0 a.e.t. (1-0, 1-0)`` — penalties first.

The stored score is ALWAYS the 90-minute score (1X2 and goal markets are settled on regular
time); extra time and penalties are kept separately. Kick-off times are Central European
times; there are no publication timestamps, so availability follows the 36-hour assumption.
"""

import hashlib
import json
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_edge.datasources.base import FetchOutcome, ProviderStatus, ResultRecord, http_get
from sports_edge.datasources.competitions import with_source
from sports_edge.offers.sources import USER_AGENT, SourceBlocked

BASE = "https://raw.githubusercontent.com/openfootball/champions-league/master"
LICENSE = "CC0-1.0 — public domain dedication (github.com/openfootball/champions-league)"
FIRST_SEASON, LAST_SEASON = 2011, 2025  # 2011-12 … 2025-26 (verified 2026-11-02)
ZONE = ZoneInfo("Europe/Paris")  # kick-off times of the files are Central European times
FILES = {"cl": ("cl", "clq"), "el": ("el", "elq"), "conf": ("conf", "confq")}
MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}
DAY = re.compile(
    r"^\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})"
    r"(?:\s+(?P<year>\d{4}))?\s*$"
)
MATCH = re.compile(
    r"^\s*(?:(?P<time>\d{1,2}[:.]\d{2})\s+)?(?P<home>\S.*?)\s+v\s+(?P<away>\S.*?)"
    r"\s{2,}(?P<score>\d.*?)\s*$"
)
SCORE = re.compile(
    r"^(?:(?P<pens>\d+-\d+)\s+pen\.\s+)?(?:(?P<aet>\d+-\d+)\s+a\.e\.t\.\s*)?"
    r"(?:\((?P<inner>\d+-\d+)(?:,\s*(?P<inner_ht>\d+-\d+))?\)"
    r"|(?P<ft>\d+-\d+)(?:\s+\((?P<ht>\d+-\d+)\))?)?$"
)
TEAM = re.compile(r"^(?P<name>.+?)\s+\((?P<country>[A-Z]{3})\)$")


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[2:]}"


def season_of(day: date) -> int:
    return day.year if day.month >= 7 else day.year - 1


def stage_of(label: str, *, qualifying: bool) -> str:
    """Normalised stage of a ``▪`` line (the raw label is kept as well)."""
    text = label.lower()
    if qualifying:
        if "play" in text:
            return "qualifying_playoff"
        number = re.search(r"(\d)\.", text)
        return f"qualifying_round_{number.group(1)}" if number else "qualifying"
    if "group" in text or "gruppe" in text:
        return "group"
    if text.startswith("league"):
        return "league_phase"
    if "round of 32" in text:
        return "round_of_32"
    if "round of 16" in text or "last 16" in text:
        return "round_of_16"
    if "quarter" in text:
        return "quarterfinal"
    if "semi" in text:
        return "semifinal"
    if "play" in text or "knockout" in text:
        return "knockout_playoff"
    if text.endswith("final"):
        return "final"
    return "other"


def _pair(text: str | None) -> list[int] | None:
    if not text:
        return None
    first, second = text.split("-")
    return [int(first), int(second)]


def parse_score(text: str) -> dict | None:
    """90-minute score, half time, score after extra time and penalties of one score text.

    ``3-2 a.e.t. (2-2, 0-2)``: 3-2 after extra time, 2-2 after 90 minutes, 0-2 at half time.
    A score after extra time without the 90-minute score is rejected (never guessed).
    """
    found = SCORE.match(text.strip())
    if not found:
        return None
    aet, pens = _pair(found.group("aet")), _pair(found.group("pens"))
    if aet is not None:
        ft, ht = _pair(found.group("inner")), _pair(found.group("inner_ht"))
    else:
        ft = _pair(found.group("ft"))
        ht = _pair(found.group("ht")) or _pair(found.group("inner"))
    if ft is None or (pens is not None and aet is None):
        return None
    duration = "PENALTY_SHOOTOUT" if pens else "EXTRA_TIME" if aet else "REGULAR"
    return {"ft": ft, "ht": ht, "aet": aet, "pens": pens, "duration": duration}


def team(text: str) -> tuple[str, str | None]:
    """``Real Madrid CF (ESP)`` → (``Real Madrid CF``, ``ESP``)."""
    found = TEAM.match(text.strip())
    return (found.group("name"), found.group("country")) if found else (text.strip(), None)


def parse(
    data: bytes, *, competition: str, season: str, qualifying: bool, observed_at: datetime
) -> list[ResultRecord]:
    """Every played match of one file (fixtures without a score are skipped)."""
    out: list[ResultRecord] = []
    start_year = int(season[:4])
    stage, label, current = "other", "", None
    for line in data.decode("utf-8").splitlines():
        if line.startswith("▪"):
            label = line.lstrip("▪ ").strip()
            stage = stage_of(label, qualifying=qualifying)
            continue
        day = DAY.match(line)
        if day:
            month = MONTHS[day.group("month")]
            year = int(day.group("year") or (start_year if month >= 7 else start_year + 1))
            current = date(year, month, int(day.group("day")))
            continue
        found = MATCH.match(line.split(" @ ")[0])
        if not found or current is None:
            continue
        score = parse_score(found.group("score"))
        if score is None:
            continue
        home, home_country = team(found.group("home"))
        away, away_country = team(found.group("away"))
        start = None
        if found.group("time"):
            hour, minute = (int(x) for x in re.split(r"[:.]", found.group("time")))
            start = datetime.combine(current, time(hour, minute), ZONE).astimezone(UTC)
        out.append(
            ResultRecord(
                provider=OpenfootballCupsProvider.id,
                sport="football",
                competition=competition,
                season=season,
                source_event_id=None,
                home=home,
                away=away,
                start_at=start,
                match_date=current.isoformat(),
                status="finished",
                home_score=score["ft"][0],
                away_score=score["ft"][1],
                details={
                    "stage": stage,
                    "stage_label": label,
                    "ht": score["ht"],
                    "aet": score["aet"],
                    "pens": score["pens"],
                    "duration": score["duration"],
                    "regulation_certain": True,
                    "home_country": home_country,
                    "away_country": away_country,
                    "source": "openfootball/champions-league",
                },
                observed_at=observed_at,
                license=LICENSE,
            )
        )
    return out


class OpenfootballCupsProvider:
    id = "openfootball_cups"
    name = "openfootball/champions-league (CC0)"
    license = LICENSE
    sports = ("football",)
    competitions = with_source("openfootball_cups")
    requires_network = True
    paid = False

    def __init__(self, raw_dir: Path | None = None, *, opener=None, qualifying: bool = True):
        self.raw_dir = raw_dir
        self.opener = opener
        self.qualifying = qualifying

    def status(self, *, offline: bool) -> ProviderStatus:
        if offline:
            return ProviderStatus(False, "Tryb offline jest włączony")
        return ProviderStatus(True, "Aktywny (repozytorium publiczne CC0; sezony 2011/12–2025/26)")

    def _download(self, url: str) -> bytes | None:
        try:
            data, _headers = http_get(url, headers={"User-Agent": USER_AGENT}, opener=self.opener)
        except SourceBlocked as error:
            if "404" in str(error):
                return None  # this season has no such file
            raise
        if self.raw_dir is not None:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            name = url.removeprefix(BASE + "/").replace("/", "_")
            (self.raw_dir / name).write_bytes(data)
            manifest = self.raw_dir / "manifest.json"
            found = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
            found[name] = {
                "url": url,
                "sha256": hashlib.sha256(data).hexdigest(),
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            manifest.write_text(json.dumps(found, indent=1), encoding="utf-8")
        return data

    def fetch(
        self, competition: str, date_from: date, date_to: date, *, now: datetime
    ) -> FetchOutcome:
        """Every season file overlapping ``[date_from, date_to)``; records inside the window."""
        if competition not in self.competitions:
            raise SourceBlocked(f"Rozgrywki {competition} poza zakresem openfootball")
        main, qualifiers = FILES[self.competitions[competition]]
        first = max(FIRST_SEASON, season_of(date_from))
        last = min(LAST_SEASON, season_of(date_to - timedelta(days=1)))
        records, payloads, requests = [], [], 0
        for year in range(first, last + 1):
            season = season_label(year)
            for name, is_qualifying in ((main, False), (qualifiers, True)):
                if is_qualifying and not self.qualifying:
                    continue
                url = f"{BASE}/{season}/{name}.txt"
                requests += 1
                data = self._download(url)
                if data is None:
                    continue
                payloads.append((url, data))
                records.extend(
                    record
                    for record in parse(
                        data,
                        competition=competition,
                        season=season,
                        qualifying=is_qualifying,
                        observed_at=now,
                    )
                    if date_from.isoformat() <= record.match_date < date_to.isoformat()
                )
        return FetchOutcome(records=records, payloads=payloads, requests=requests)
