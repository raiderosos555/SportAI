"""openfootball/internationals — national-team matches under CC0-1.0 (TASK 009).

Verified 2026-11-02 (last push 2026-08-24): one Football.TXT file per competition and
calendar year, e.g. ``uefa_nations_league/2024_uefa_nations_league.txt``; Nations League
2018–2025, EURO 2012/2016/2021/2024, EURO qualifying, World Cup 2010–2022 (the 2026 file
holds fixtures only), World Cup qualifying of every confederation and friendlies.

Format: ``  Portugal  2-1 Croatia  @ Lisbon, Portugal`` followed by scorer lines
``(Diogo Dalot 7' Cristiano Ronaldo 34'; Diogo Dalot 41'(og))`` — goals listed per team that
benefited. The printed score is the FINAL score: after extra time when there was one, with
no "a.e.t." marker (EURO 2024 quarter-final ``Germany 1-2 Spain`` was 1-1 after 90 minutes),
and a shoot-out only as a note ``[France wins on penalties]``. The 90-minute score is
therefore taken from the scorer minutes and only when it is unambiguous:

* group or league stage (``▪ Group A`` headers): no extra time, the score is the 90 minutes;
* otherwise every goal must be listed; goals after the 99th minute are extra time; a goal in
  minutes 91–99 may be stoppage time or extra time — the match stays regulation-uncertain;
* 0-0 is certain either way.

World Cup qualifying and friendlies cover every confederation; the importer keeps matches of
UEFA members only (the members are the teams of the Nations League and EURO qualifying files
— derived from the data, never typed in).
"""

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sports_edge.datasources.base import FetchOutcome, ProviderStatus, ResultRecord, http_get
from sports_edge.datasources.competitions import with_source
from sports_edge.offers.sources import USER_AGENT, SourceBlocked

BASE = "https://raw.githubusercontent.com/openfootball/internationals/master"
LICENSE = "CC0-1.0 — public domain dedication (github.com/openfootball/internationals)"
FIRST_YEAR = 2010
MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}
DAY = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})"
    r"(?:\s+(?P<year>\d{4}))?\s*$"
)
MATCH = re.compile(
    r"^\s+(?P<home>\S.*?)\s+(?P<h>\d+)-(?P<a>\d+)\s+(?P<away>\S.*?)"
    r"(?:\s+@\s+(?P<venue>[^\[]*?))?\s*(?:\[(?P<note>[^\]]*)\])?\s*$"
)
MINUTE = re.compile(r"(\d+)(\+\d+)?'")
GROUP_STAGE = re.compile(r"^(group|league|gruppe)", re.IGNORECASE)
STAGES = {
    "round of 16": "round_of_16",
    "quarter": "quarterfinal",
    "semi": "semifinal",
    "third": "third_place",
    "match for third": "third_place",
    "final": "final",
}
EXTRA_TIME_FROM = 100  # a goal after minute 99 cannot be stoppage time of the second half
NO_EXTRA_TIME = frozenset({"fifa.friendly"})


def stage_of(label: str) -> str:
    text = label.lower()
    if GROUP_STAGE.match(text):
        return "group"
    for key, stage in STAGES.items():
        if key in text:
            return stage
    return "other"


def _minutes(text: str) -> list[tuple[int, bool]]:
    """(minute, written with "+") of every goal of one team's scorer list."""
    return [(int(m.group(1)), bool(m.group(2))) for m in MINUTE.finditer(text)]


def ninety(
    score: tuple[int, int], goals: tuple[list, list] | None, *, group_stage: bool
) -> tuple[tuple[int, int] | None, bool, bool]:
    """(90-minute score or None, extra time known to be played, certain)."""
    if group_stage or score == (0, 0):
        return score, False, True
    if goals is None or (len(goals[0]), len(goals[1])) != score:
        return None, False, False  # scorers incomplete: extra time cannot be excluded
    late = [m for side in goals for m, plus in side if m > 90 and not plus]
    if any(m < EXTRA_TIME_FROM for m in late):
        return None, False, False  # 91'–99': stoppage time or extra time — not knowable
    regular = tuple(sum(1 for m, plus in side if m <= 90 or plus) for side in goals)
    return (regular[0], regular[1]), bool(late), True


def parse(data: bytes, *, competition: str, year: int, observed_at: datetime) -> list[ResultRecord]:
    lines = data.decode("utf-8").splitlines()
    out: list[ResultRecord] = []
    label, stage, current = "", "other", None
    pending: list[tuple[dict, list[str]]] = []
    for line in lines:
        stripped = line.strip()
        if line.startswith("▪"):
            label = line.lstrip("▪ ").strip()
            stage = stage_of(label)
            continue
        day = DAY.match(stripped) if not line.startswith(" ") else None
        if day:
            current = date(
                int(day.group("year") or year), MONTHS[day.group("month")], int(day.group("day"))
            )
            continue
        found = MATCH.match(line)
        if found is None:
            if stripped.startswith("(") and pending and not pending[-1][1]:
                pending[-1][1].append(stripped)
            elif pending and pending[-1][1] and not pending[-1][1][-1].endswith(")"):
                pending[-1][1].append(stripped)  # scorer list continued on the next line
            continue
        if current is not None:
            pending.append(
                (
                    {
                        "home": found.group("home"),
                        "away": found.group("away"),
                        "score": (int(found.group("h")), int(found.group("a"))),
                        "note": (found.group("note") or "").strip(),
                        "venue": (found.group("venue") or "").strip() or None,
                        "day": current,
                        "stage": stage,
                        "label": label,
                    },
                    [],
                )
            )
    for match, scorer_lines in pending:
        goals = None
        if scorer_lines:
            text = " ".join(scorer_lines).strip().removeprefix("(").removesuffix(")")
            if ";" in text:
                home_text, _, away_text = text.partition(";")
                goals = (_minutes(home_text), _minutes(away_text))
            elif match["score"][0] == 0:  # only one team scored: no separator is printed
                goals = ([], _minutes(text))
            elif match["score"][1] == 0:
                goals = (_minutes(text), [])
        # friendlies have no extra time; group and league stages neither
        group_stage = match["stage"] == "group" or competition in NO_EXTRA_TIME
        regular, extra, certain = ninety(match["score"], goals, group_stage=group_stage)
        winner = None
        if "penalties" in match["note"].lower():
            winner = match["note"].split(" wins")[0].strip()
            extra = True
        score = regular or match["score"]
        out.append(
            ResultRecord(
                provider=OpenfootballIntProvider.id,
                sport="football",
                competition=competition,
                season=str(year),
                source_event_id=None,
                home=match["home"],
                away=match["away"],
                start_at=None,
                match_date=match["day"].isoformat(),
                status="finished",
                home_score=score[0],
                away_score=score[1],
                details={
                    "stage": match["stage"],
                    "stage_label": match["label"],
                    "final_score": list(match["score"]),
                    "aet": list(match["score"]) if extra and certain else None,
                    "duration": "PENALTY_SHOOTOUT" if winner else "EXTRA_TIME" if extra else None,
                    "penalties_winner": winner,
                    "regulation_certain": certain,
                    "score_basis": "minuty bramek" if goals else "wynik końcowy",
                    "note": match["note"] or None,
                    # host country of the venue: a neutral ground when neither team is the host
                    "venue_country": match["venue"].split(",")[-1].strip()
                    if match["venue"]
                    else None,
                    # a national team IS its country: a similar name of another country
                    # (Australia / Austria) is never the same team
                    "home_country": match["home"],
                    "away_country": match["away"],
                    "source": "openfootball/internationals",
                },
                observed_at=observed_at,
                license=LICENSE,
            )
        )
    return out


class OpenfootballIntProvider:
    id = "openfootball_int"
    name = "openfootball/internationals (CC0)"
    license = LICENSE
    sports = ("football",)
    competitions = with_source("openfootball_int")
    requires_network = True
    paid = False

    def __init__(self, raw_dir: Path | None = None, *, opener=None, members=None):
        self.raw_dir = raw_dir
        self.opener = opener
        self.members = members  # UEFA members: qualifiers and friendlies are filtered by them

    def status(self, *, offline: bool) -> ProviderStatus:
        if offline:
            return ProviderStatus(False, "Tryb offline jest włączony")
        return ProviderStatus(True, "Aktywny (repozytorium publiczne CC0, pliki roczne)")

    def _download(self, url: str) -> bytes | None:
        try:
            data, _headers = http_get(url, headers={"User-Agent": USER_AGENT}, opener=self.opener)
        except SourceBlocked as error:
            if "404" in str(error):
                return None
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
        if competition not in self.competitions:
            raise SourceBlocked(f"Rozgrywki {competition} poza zakresem openfootball")
        folder = self.competitions[competition]
        records, payloads, requests = [], [], 0
        last = (date_to - timedelta(days=1)).year
        for year in range(max(FIRST_YEAR, date_from.year), last + 1):
            url = f"{BASE}/{folder}/{year}_{folder}.txt"
            requests += 1
            data = self._download(url)
            if data is None:
                continue
            payloads.append((url, data))
            for record in parse(data, competition=competition, year=year, observed_at=now):
                if not date_from.isoformat() <= record.match_date < date_to.isoformat():
                    continue
                if (
                    self.members is not None
                    and competition in FILTERED
                    and not (record.home in self.members and record.away in self.members)
                ):
                    continue
                records.append(record)
        return FetchOutcome(records=records, payloads=payloads, requests=requests)


FILTERED = frozenset({"fifa.wc_q", "fifa.friendly"})  # all confederations in the source
