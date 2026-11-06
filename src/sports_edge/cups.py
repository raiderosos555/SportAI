"""Cup semantics (TASK 009): stages, two-legged ties, extra time, penalties and formats.

The stored score of a cup match is ALWAYS the score after 90 minutes with stoppage time —
what STS settles "Mecz" (1X2), double chance, goals and BTTS on (Z 15.1.1). The score after
extra time, the penalty shoot-out, the aggregate of a two-legged tie and the team that
advanced are SEPARATE facts in ``details`` and never replace the 90-minute score.

A source that reports one number for a match that may have gone to extra time (PitchAPI
counts extra-time goals, openfootball/internationals prints the final score) is resolved
by arithmetic, never by guessing: every 90-minute score compatible with the reported score
and the rules of the tie is enumerated; a single candidate is certain, several are
"regulation uncertain" and such a match never feeds a 90-minute model or a settlement.

Formats are taken from the competition rules of each season (no outdated rule is applied):

* club cups: league phase of 36 clubs from 2024/25, group stage before; the away goals
  rule was abolished from 2021/22 (UEFA decision of 24 June 2021);
* two-legged rounds: qualifying rounds, knockout play-offs, round of 32 / 16, quarter- and
  semi-finals; the final is a single match; extra time (2 × 15 minutes) and penalties
  follow a level tie after the second leg.
"""

import json
from collections import defaultdict
from dataclasses import replace
from datetime import date

STAGE_LABELS = {
    "qualifying": "kwalifikacje",
    "qualifying_round_1": "1. runda kwalifikacji",
    "qualifying_round_2": "2. runda kwalifikacji",
    "qualifying_round_3": "3. runda kwalifikacji",
    "qualifying_round_4": "4. runda kwalifikacji",
    "qualifying_playoff": "runda play-off kwalifikacji",
    "group": "faza grupowa",
    "league_phase": "faza ligowa",
    "league_or_group": "faza ligowa/grupowa (wg daty)",
    "knockout_playoff": "play-off fazy pucharowej",
    "round_of_32": "1/16 finału",
    "round_of_16": "1/8 finału",
    "quarterfinal": "ćwierćfinał",
    "semifinal": "półfinał",
    "third_place": "mecz o 3. miejsce",
    "final": "finał",
    "knockout": "faza pucharowa (runda wg daty)",
    "other": "etap nieustalony",
}
QUALIFYING = frozenset(s for s in STAGE_LABELS if s.startswith("qualifying"))
NO_EXTRA_TIME = frozenset({"group", "league_phase", "league_or_group"})
CLUB_TWO_LEGGED = QUALIFYING | {
    "knockout_playoff",
    "round_of_32",
    "round_of_16",
    "quarterfinal",
    "semifinal",
    "knockout",
}
SINGLE_KNOCKOUT = frozenset({"final", "third_place"})
FOOTBALL_DATA_STAGES = {
    "LEAGUE_STAGE": "league_phase",
    "GROUP_STAGE": "group",
    "PLAYOFFS": "knockout_playoff",
    "PLAYOFF_ROUND": "qualifying_playoff",
    "QUALIFICATION": "qualifying",
    "QUALIFICATION_ROUND_1": "qualifying_round_1",
    "QUALIFICATION_ROUND_2": "qualifying_round_2",
    "QUALIFICATION_ROUND_3": "qualifying_round_3",
    "ROUND_OF_32": "round_of_32",
    "LAST_32": "round_of_32",
    "LAST_16": "round_of_16",
    "ROUND_OF_16": "round_of_16",
    "QUARTER_FINALS": "quarterfinal",
    "SEMI_FINALS": "semifinal",
    "THIRD_PLACE": "third_place",
    "FINAL": "final",
}
TIE_WINDOW_DAYS = 35  # both legs of one round are at most five weeks apart
LEAGUE_PHASE_FROM = 2024  # season start year of the 36-club league phase
AWAY_GOALS_UNTIL = 2020  # last season start year with the away goals rule (2020/21)


def phase(stage: str) -> str:
    """Kwalifikacje / faza ligowa / faza grupowa / faza pucharowa / finał."""
    if stage in QUALIFYING:
        return "kwalifikacje"
    if stage in {"league_phase", "group", "league_or_group"}:
        return STAGE_LABELS[stage]
    if stage == "final":
        return "finał"
    if stage == "other":
        return "etap nieustalony"
    return "faza pucharowa"


def season_start(season: str | None, day: str) -> int:
    if season and season[:4].isdigit():
        return int(season[:4])
    when = date.fromisoformat(day)
    return when.year if when.month >= 7 else when.year - 1


def season_format(competition: str, season_year: int) -> dict:
    """Rules of one season of a European club cup (documented UEFA formats)."""
    league = season_year >= LEAGUE_PHASE_FROM
    return {
        "season": f"{season_year}/{str(season_year + 1)[2:]}",
        "main_phase": "faza ligowa (36 drużyn)" if league else "faza grupowa",
        "away_goals": season_year <= AWAY_GOALS_UNTIL,
        "knockout_playoff": league or competition in {"uefa.el", "uefa.ecl"},
        "extra_time": "2 × 15 min, potem rzuty karne (dwumecz: po rewanżu przy remisie)",
    }


def stage_from_date(day: str, season_year: int) -> str:
    """Stage inferred from the calendar of a club cup season when a source has no stage
    (PitchAPI lists): July–August qualifying, September–January league or group phase,
    February–June knockout rounds. Used only to decide whether extra time was possible."""
    month = date.fromisoformat(day).month
    if month in (7, 8):
        return "qualifying"
    if month >= 9 or month == 1:
        return "league_or_group"
    return "knockout"


def ninety_candidates(
    reported: tuple[int, int],
    *,
    single: bool,
    first_leg: tuple[int, int] | None = None,
    away_goals: bool = False,
) -> set[tuple[int, int]]:
    """Every 90-minute score compatible with a reported score that MAY include extra time.

    ``first_leg`` = (goals of this match's home team, goals of its away team) in the first
    leg. Extra time is played only if the tie (or a single knockout match) is level after 90
    minutes — with the away goals rule, also on away goals. Goals of extra time are any
    non-negative split of the remainder."""
    if not single and first_leg is None:
        return {reported}  # a first leg or a match without extra time
    x, y = reported
    out: set[tuple[int, int]] = set()
    for h in range(x + 1):
        for a in range(y + 1):
            if single:
                level = h == a
            else:
                level = first_leg[0] + h == first_leg[1] + a and (
                    not away_goals or first_leg[0] == a
                )
            if level or (h, a) == (x, y):
                # level after 90 minutes: extra time produced the rest; otherwise the
                # reported score itself is the 90-minute score (no extra time)
                out.add((h, a))
    return out


def _details(row: dict) -> dict:
    if isinstance(row.get("details"), dict):
        return row["details"]
    return json.loads(row.get("details_json") or "{}")


def annotate(rows: list[dict], competition: str) -> dict[str, dict]:
    """{row id: stage, leg, tie, first leg, aggregate, extra time, penalties, who advanced}.

    Legs of a two-legged round: the same two clubs with home and away reversed in the same
    season and stage, at most ``TIE_WINDOW_DAYS`` apart; the earlier match is the first leg.
    Two meetings of the same clubs in a league phase or a group are never paired."""
    out: dict[str, dict] = {}
    groups: dict[tuple, list[dict]] = defaultdict(list)
    ordered = sorted(rows, key=lambda r: (r["match_date"], r.get("start_at") or "", r["id"]))
    for row in ordered:
        details = _details(row)
        year = season_start(row.get("season"), row["match_date"])
        stage = details.get("stage") or stage_from_date(row["match_date"], year)
        out[row["id"]] = {
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage, stage),
            "phase": phase(stage),
            "season_year": year,
            "leg": None,
            "tie_id": None,
            "extra_time": details.get("duration") in {"EXTRA_TIME", "PENALTY_SHOOTOUT"}
            if details.get("duration")
            else None,
            "aet": details.get("aet"),
            "pens": details.get("pens"),
            "regulation_certain": details.get("regulation_certain", True),
            "advanced": None,
            "advanced_basis": None,
        }
        if stage in CLUB_TWO_LEGGED:
            groups[(year, stage, frozenset((row["home"], row["away"])))].append(row)
        elif stage in SINGLE_KNOCKOUT:
            _single(out[row["id"]], row)
    for (year, _stage, _pair), legs in groups.items():
        if len(legs) != 2:
            continue
        first, second = legs
        gap = date.fromisoformat(second["match_date"]) - date.fromisoformat(first["match_date"])
        if second["home"] != first["away"] or gap.days > TIE_WINDOW_DAYS or gap.days <= 0:
            continue
        tie = f"{competition}:{year}:{first['id'][:12]}"
        rules = season_format(competition, year)
        a1, b1 = out[first["id"]], out[second["id"]]
        a1.update(leg=1, tie_id=tie)
        b1.update(leg=2, tie_id=tie)
        # leg 1 from the point of view of the second leg's home team
        first_for_home = (first["away_score"], first["home_score"])
        b1["first_leg"] = {
            "home": first["home"],
            "away": first["away"],
            "score": [first["home_score"], first["away_score"]],
        }
        after = b1["aet"] or [second["home_score"], second["away_score"]]
        agg_home = first_for_home[0] + after[0]
        agg_away = first_for_home[1] + after[1]
        b1["aggregate"] = {second["home"]: agg_home, second["away"]: agg_away}
        b1["aggregate_90"] = {
            second["home"]: first_for_home[0] + second["home_score"],
            second["away"]: first_for_home[1] + second["away_score"],
        }
        a1["aggregate"] = None
        winner, basis = None, None
        if b1["pens"]:
            winner = second["home"] if b1["pens"][0] > b1["pens"][1] else second["away"]
            basis = "rzuty karne"
        elif agg_home != agg_away and b1["regulation_certain"]:
            winner = second["home"] if agg_home > agg_away else second["away"]
            basis = "dogrywka" if b1["aet"] else "dwumecz"
        elif agg_home == agg_away and rules["away_goals"]:
            away_home, away_away = first_for_home[0], after[1]
            if away_home != away_away:
                winner = second["home"] if away_home > away_away else second["away"]
                basis = "bramki na wyjeździe"
        b1["advanced"], b1["advanced_basis"] = winner, basis
    return out


def _single(entry: dict, row: dict) -> None:
    score = entry["aet"] or [row["home_score"], row["away_score"]]
    if entry["pens"]:
        entry["advanced"] = row["home"] if entry["pens"][0] > entry["pens"][1] else row["away"]
        entry["advanced_basis"] = "rzuty karne"
    elif score[0] != score[1] and entry["regulation_certain"]:
        entry["advanced"] = row["home"] if score[0] > score[1] else row["away"]
        entry["advanced_basis"] = "dogrywka" if entry["aet"] else "mecz"


def regulation_from_reported(records: list, *, competition: str) -> list:
    """Records whose reported score may include extra-time goals, resolved (a new list).

    For sources without a stage and without a separate 90-minute score (PitchAPI): the
    stage follows from the calendar; a first leg and league or group matches are certain; a
    second leg or a single knockout match keeps the reported score only when exactly one
    90-minute score is possible, otherwise ``regulation_certain`` is False."""
    replaced: dict[int, object] = {}
    by_pair: dict[tuple, list] = defaultdict(list)
    for record in records:
        if not record.complete:
            continue
        year = season_start(record.season, record.match_date)
        stage = record.details.get("stage") or stage_from_date(record.match_date, year)
        record.details.setdefault("stage", stage)
        record.details.setdefault("stage_basis", "kalendarz sezonu (źródło bez etapu)")
        if stage in NO_EXTRA_TIME:
            record.details["regulation_certain"] = True
            continue
        by_pair[(year, frozenset((record.home, record.away)))].append(record)
    for (year, _pair), group in by_pair.items():
        group.sort(key=lambda r: (r.match_date, r.start_at.isoformat() if r.start_at else ""))
        rules = season_format(competition, year)
        gap = (
            (date.fromisoformat(group[1].match_date) - date.fromisoformat(group[0].match_date)).days
            if len(group) == 2
            else 0
        )
        if len(group) == 2 and group[1].home == group[0].away and 0 < gap <= TIE_WINDOW_DAYS:
            first, second = group
            first.details.update(regulation_certain=True, leg=1)
            options = ninety_candidates(
                (second.home_score, second.away_score),
                single=False,
                first_leg=(first.away_score, first.home_score),
                away_goals=rules["away_goals"],
            )
            replaced[id(second)] = _resolve(second, options, leg=2)
            continue
        for record in group:
            if len(group) == 1:  # a final, or a first leg whose return leg is still ahead
                options = ninety_candidates((record.home_score, record.away_score), single=True)
            else:
                options = set()  # several meetings that do not form one tie: never guessed
            replaced[id(record)] = _resolve(record, options, leg=None)
    return [replaced.get(id(r), r) for r in records]


def _resolve(record, options: set[tuple[int, int]], *, leg: int | None):
    reported = [record.home_score, record.away_score]
    details = dict(record.details, reported_score=reported)
    if leg:
        details["leg"] = leg
    if len(options) == 1:
        ninety = next(iter(options))
        details["regulation_certain"] = True
        if list(ninety) != reported:
            details.update(aet=reported, duration="EXTRA_TIME")
        return replace(record, home_score=ninety[0], away_score=ninety[1], details=details)
    details["regulation_certain"] = False
    details["score_basis"] = "wynik źródła może zawierać gole z dogrywki"
    return replace(record, details=details)


def tie_situation(
    rows: list[dict], annotations: dict[str, dict], *, home: str, away: str, day: str
) -> dict | None:
    """The tie of an upcoming match: a return leg when the reversed fixture was played in a
    two-legged round of the same season within the tie window; otherwise None (a first leg
    and a single match cannot be told apart before the draw is known — never guessed)."""
    when = date.fromisoformat(day)
    year = when.year if when.month >= 7 else when.year - 1
    for row in sorted(rows, key=lambda r: r["match_date"], reverse=True):
        note = annotations.get(row["id"]) or {}
        if note.get("season_year") != year or note.get("stage") not in CLUB_TWO_LEGGED:
            continue
        if row["home"] == away and row["away"] == home:
            gap = (when - date.fromisoformat(row["match_date"])).days
            if 0 < gap <= TIE_WINDOW_DAYS and note.get("leg") in (None, 1):
                return {
                    "leg": 2,
                    "stage": note["stage"],
                    "stage_label": note["stage_label"],
                    "first_leg": {
                        "date": row["match_date"],
                        "home": row["home"],
                        "away": row["away"],
                        "score": [row["home_score"], row["away_score"]],
                    },
                    # goals of this match's home team first
                    "aggregate_before": [row["away_score"], row["home_score"]],
                    "away_goals_rule": season_format("", year)["away_goals"],
                }
    return None
