"""TASK 009 — International Football Intelligence: UEFA cups and national teams.

Synthetic data only (no network, never the private database). One test per required area.
"""

import json
import urllib.error
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest
from conftest import ROOT

from sports_edge import clubs, cups, international
from sports_edge.datasources import football_data, openfootball_cups, openfootball_int
from sports_edge.datasources.base import ResultRecord
from sports_edge.datasources.competitions import COMPETITIONS, by_category, for_sts_league
from sports_edge.datasources.pitchapi import PitchApiProvider
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.matching import TeamIdentities, alias_map, match_participant, participant_key
from sports_edge.offers.text import load_normalizer
from sports_edge.results_engine import apply_fetched, effective_results, priority

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
NAMES = load_normalizer(ROOT / "config" / "aliases.toml")

CL_TXT = """= UEFA Champions League 2024/25

▪ League, Matchday 1
  Tue Sep 17 2024
    18:45  Juventus (ITA)              v PSV (NED)                     3-1 (1-0)
▪ Finals, Round of 16
  Tue Mar 4 2025
    21:00  Club Brugge KV (BEL)        v Aston Villa FC (ENG)          1-3 (1-1)
  Tue Mar 11
    21:00  Liverpool FC (ENG)  v Paris Saint-Germain FC (FRA)  1-4 pen. 0-1 a.e.t. (0-1, 0-1)
▪ Finals, Final
  Sat May 31
    21:00  Paris Saint-Germain FC (FRA) v FC Internazionale Milano (ITA)  5-0 (2-0)
"""


class Provider:
    def __init__(self, family: str):
        self.id = self.family = family
        self.name = family
        self.license = "test"


def _record(provider, competition, day, home, away, h, a, **details) -> ResultRecord:
    return ResultRecord(
        provider=provider,
        sport="football",
        competition=competition,
        season=details.pop("season", None),
        source_event_id=details.pop("source_event_id", None),
        home=home,
        away=away,
        start_at=details.pop("start_at", None),
        match_date=day,
        status="finished",
        home_score=h,
        away_score=a,
        details=details,
        published_at=details.pop("published_at", None),
        observed_at=NOW,
    )


def _store_records(store, family, competition, records, tmp_path, *, aliases=None, new=False):
    existing = effective_results(store, sport="football", until=NOW, competitions=(competition,))
    return apply_fetched(
        store,
        Provider(family),
        competition,
        records,
        [(f"u-{family}-{competition}", canonical([r.home for r in records]).encode())],
        existing=existing,
        sport="football",
        now=NOW,
        normalizer=NAMES,
        aliases=aliases if aliases is not None else alias_map(store),
        raw_dir=tmp_path,
        accept_new_clubs=new,
    )


class _Response:
    def __init__(self, data: bytes):
        self.data, self.headers = data, {}

    def read(self, _n=-1):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _opener(routes: dict[str, bytes]):
    def open_url(request, timeout=30):
        for key, data in routes.items():
            if key in request.full_url:
                return _Response(data)
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    return open_url


# 1 ---------------------------------------------------------------- Champions League parsing


def test_champions_league_file_keeps_90_minutes_stage_country_and_kick_off():
    records = openfootball_cups.parse(
        CL_TXT.encode(), competition="uefa.cl", season="2024-25", qualifying=False, observed_at=NOW
    )
    assert [r.details["stage"] for r in records] == [
        "league_phase",
        "round_of_16",
        "round_of_16",
        "final",
    ]
    first, _, shootout, final = records
    assert first.start_at == datetime(2024, 9, 17, 16, 45, tzinfo=UTC)  # 18:45 CEST
    assert (first.home, first.details["home_country"]) == ("Juventus", "ITA")
    assert shootout.match_date == "2025-03-11"  # the year is carried over
    assert (shootout.home_score, shootout.away_score) == (0, 1)  # 90 minutes, never a.e.t.
    assert shootout.details["aet"] == [0, 1] and shootout.details["pens"] == [1, 4]
    assert final.details["duration"] == "REGULAR" and final.details["regulation_certain"]
    assert cups.STAGE_LABELS[final.details["stage"]] == "finał"


# 2 ---------------------------------------------------------------- Europa / Conference League


def test_pitchapi_cup_scores_that_may_include_extra_time_are_marked_uncertain():
    def match(i, day, home, away, h, a):
        return {
            "id": f"m{i}",
            "time_utc": f"{day}T20:00:00Z",
            "status": "finished",
            "score_home": h,
            "score_away": a,
            "home_team": {"name": home, "id": f"t-{home}"},
            "away_team": {"name": away, "id": f"t-{away}"},
        }

    payload = {
        "data": {
            "matches": [
                match(1, "2024-10-03", "Alpha", "Beta", 2, 2),  # league phase: no extra time
                match(2, "2025-02-13", "Gamma", "Delta", 3, 0),  # first leg
                match(3, "2025-02-20", "Delta", "Gamma", 1, 0),  # 1:0 cannot level 0:3 → certain
                match(4, "2025-02-13", "Eta", "Theta", 1, 0),
                match(
                    5, "2025-02-20", "Theta", "Eta", 2, 1
                ),  # level after 90' possible → uncertain
                match(
                    6, "2025-05-21", "Iota", "Kappa", 2, 1
                ),  # a final: extra time cannot be excluded
            ]
        }
    }
    provider = PitchApiProvider(
        "key", opener=_opener({"l_38d9HA": json.dumps(payload).encode()}), sleep=lambda _s: None
    )
    records = {
        r.source_event_id: r
        for r in provider.fetch("uefa.el", date(2024, 7, 1), date(2025, 7, 1), now=NOW).records
    }
    assert records["m1"].details["regulation_certain"] is True
    assert records["m2"].details == records["m2"].details | {"leg": 1, "regulation_certain": True}
    assert records["m3"].details["regulation_certain"] is True
    assert records["m5"].details["regulation_certain"] is False
    assert records["m6"].details["regulation_certain"] is False
    assert cups.ninety_candidates((3, 2), single=False, first_leg=(2, 5)) == {(3, 2), (3, 0)}
    assert "uefa.ecl" in PitchApiProvider("k").competitions  # Conference League adapter


# 3 ---------------------------------------------------------------- STS headers


def test_only_observed_sts_headers_map_and_sport_and_suffix_keep_competitions_apart():
    assert for_sts_league("KLUBOWE - LIGA MISTRZÓW", "football").code == "uefa.cl"
    assert for_sts_league("KLUBOWE - LIGA MISTRZÓW", "basketball") is None  # another sport
    assert for_sts_league("KLUBOWE - LIGA MISTRZÓW AFC", "football") is None  # Asia
    assert for_sts_league("KLUBOWE - LIGA EUROPY", "football").code == "uefa.el"
    assert for_sts_league("KLUBOWE - LIGA KONFERENCJI", "football").code == "uefa.ecl"
    assert for_sts_league("FRANCJA - PUCHAR", "football") is None  # no verified source
    for code in by_category("national"):
        assert COMPETITIONS[code].sts_leagues == ()  # nothing observed, nothing guessed


# 4 ---------------------------------------------------------------- club identity


def test_cup_club_is_the_league_club_only_with_evidence(store, tmp_path):
    _store_records(
        store,
        "openfootball",
        "es.1",
        [
            _record("openfootball", "es.1", "2025-03-01", "Real Madrid CF", "Getafe CF", 2, 0),
        ],
        tmp_path,
    )
    _store_records(
        store,
        "openfootball_cups",
        "uefa.cl",
        [
            _record(
                "openfootball_cups",
                "uefa.cl",
                "2025-03-05",
                "Real Madrid CF",
                "FK Partizan",
                1,
                0,
                home_country="ESP",
                away_country="SRB",
                regulation_certain=True,
            ),
        ],
        tmp_path,
    )
    # a look-alike from ANOTHER country is a new club, never the same identity
    report = _store_records(
        store,
        "openfootball_cups",
        "uefa.cl",
        [
            _record(
                "openfootball_cups",
                "uefa.cl",
                "2025-03-12",
                "FK Partizani",
                "Real Madrid CF",
                0,
                2,
                home_country="ALB",
                away_country="ESP",
                regulation_certain=True,
            ),
        ],
        tmp_path,
    )
    assert report["summary"]["inserted"] == 1 and not report["unmatched"]
    found = clubs.links(
        store,
        normalizer=NAMES,
        aliases=alias_map(store),
        identities=TeamIdentities(NAMES, {}),
        until=NOW,
    )
    link = found[("uefa.cl", "Real Madrid CF")][0]
    assert (link.domestic, link.domestic_name, link.evidence) == (
        "es.1",
        "Real Madrid CF",
        "name_country",
    )
    assert ("uefa.cl", "FK Partizan") not in found and ("uefa.cl", "FK Partizani") not in found
    renamed = clubs.with_links(TeamIdentities(NAMES, {}), {("uefa.cl", "R. Madrid"): [link]})
    assert renamed.renames[("uefa.cl", "R. Madrid")] == "Real Madrid CF"


# 5 ---------------------------------------------------------------- first leg and return leg


def _row(i, day, home, away, h, a, *, season="2024-25", competition="uefa.cl", **details):
    return {
        "id": f"r{i}",
        "match_date": day,
        "home": home,
        "away": away,
        "home_score": h,
        "away_score": a,
        "season": season,
        "details_json": canonical(details),
        "competition": competition,
    }


def test_legs_of_a_tie_are_paired_but_two_group_meetings_never_are():
    rows = [
        _row(1, "2024-10-01", "A", "B", 1, 1, stage="group"),
        _row(2, "2024-10-22", "B", "A", 2, 0, stage="group"),  # group: not a tie
        _row(3, "2025-03-04", "C", "D", 1, 0, stage="round_of_16"),
        _row(4, "2025-03-11", "D", "C", 2, 0, stage="round_of_16"),
    ]
    notes = cups.annotate(rows, "uefa.cl")
    assert notes["r1"]["leg"] is None and notes["r2"]["leg"] is None
    assert (notes["r3"]["leg"], notes["r4"]["leg"]) == (1, 2)
    assert notes["r4"]["aggregate"] == {"D": 2, "C": 1}
    assert (notes["r4"]["advanced"], notes["r4"]["advanced_basis"]) == ("D", "dwumecz")
    tie = cups.tie_situation(
        rows[:3], cups.annotate(rows[:3], "uefa.cl"), home="D", away="C", day="2025-03-11"
    )
    assert tie["leg"] == 2 and tie["aggregate_before"] == [0, 1]
    # the fixture in the first leg's orientation is never a return leg
    assert (
        cups.tie_situation(
            rows[:3], cups.annotate(rows[:3], "uefa.cl"), home="C", away="D", day="2025-03-11"
        )
        is None
    )


# 6 ---------------------------------------------------------------- regular time / extra time


def test_one_x_two_uses_the_90_minutes_and_an_extra_time_score_never_wins(store, tmp_path):
    payload = {
        "matches": [
            {
                "id": 7,
                "utcDate": "2025-03-11T20:00:00Z",
                "status": "FINISHED",
                "stage": "LAST_16",
                "lastUpdated": "2025-03-11T23:00:00Z",
                "season": {"startDate": "2024-09-17"},
                "homeTeam": {"name": "Liverpool FC", "id": 64},
                "awayTeam": {"name": "PSG", "id": 524},
                "score": {
                    "duration": "EXTRA_TIME",
                    "fullTime": {"home": 2, "away": 1},
                    "regularTime": {"home": 1, "away": 1},
                    "extraTime": {"home": 1, "away": 0},
                    "halfTime": {"home": 0, "away": 1},
                },
            }
        ]
    }
    (record,) = football_data.parse(
        json.dumps(payload).encode(), competition="uefa.cl", observed_at=NOW
    )
    assert (record.home_score, record.away_score) == (1, 1)
    assert record.details["aet"] == [2, 1] and record.details["stage"] == "round_of_16"
    # PitchAPI-like uncertain 2:1 and a certain 1:1 of the same match: the 1:1 is chosen
    _store_records(
        store,
        "pitchapi",
        "uefa.cl",
        [
            _record(
                "pitchapi",
                "uefa.cl",
                "2025-03-11",
                "Liverpool FC",
                "PSG",
                2,
                1,
                regulation_certain=False,
            )
        ],
        tmp_path,
        new=True,
    )
    _store_records(
        store,
        "openfootball_cups",
        "uefa.cl",
        [
            _record(
                "openfootball_cups",
                "uefa.cl",
                "2025-03-11",
                "Liverpool FC",
                "PSG",
                1,
                1,
                regulation_certain=True,
            )
        ],
        tmp_path,
    )
    (row,) = effective_results(store, sport="football", until=NOW, competitions=("uefa.cl",))
    assert (row["home_score"], row["away_score"]) == (1, 1)
    assert row["regulation_certain"] and not row["conflict"]
    assert row["provider_resolved"] == "openfootball_cups"


# 7 ---------------------------------------------------------------- penalties


def test_a_shoot_out_decides_who_advances_but_never_the_score():
    score = openfootball_cups.parse_score("4-2 pen. 1-1 a.e.t. (1-1, 0-1)")
    assert score == {
        "ft": [1, 1],
        "ht": [0, 1],
        "aet": [1, 1],
        "pens": [4, 2],
        "duration": "PENALTY_SHOOTOUT",
    }
    assert openfootball_cups.parse_score("2-1 a.e.t.") is None  # no 90 minutes: rejected
    rows = [
        _row(1, "2025-04-08", "C", "D", 1, 0, stage="quarterfinal"),
        _row(
            2,
            "2025-04-15",
            "D",
            "C",
            1,
            0,
            stage="quarterfinal",
            aet=[1, 0],
            pens=[5, 4],
            duration="PENALTY_SHOOTOUT",
        ),
    ]
    note = cups.annotate(rows, "uefa.cl")["r2"]
    assert (note["advanced"], note["advanced_basis"]) == ("D", "rzuty karne")
    assert note["extra_time"] is True


# 8 ---------------------------------------------------------------- format changes


def test_formats_of_each_season_are_applied_without_outdated_rules():
    assert "faza ligowa" in cups.season_format("uefa.cl", 2024)["main_phase"]
    assert cups.season_format("uefa.cl", 2023)["main_phase"] == "faza grupowa"
    assert cups.season_format("uefa.cl", 2020)["away_goals"]
    assert not cups.season_format("uefa.cl", 2021)["away_goals"]

    def tie(season):  # the season label decides the rules of the tie
        rows = [
            _row(1, "2020-02-18", "C", "D", 2, 1, stage="round_of_16", season=season),
            _row(2, "2020-03-10", "D", "C", 1, 0, stage="round_of_16", season=season),
        ]
        return cups.annotate(rows, "uefa.cl")["r2"]

    assert tie("2019-20")["advanced_basis"] == "bramki na wyjeździe"  # away goals: D scored 1
    assert tie("2021-22")["advanced"] is None  # level tie without the shoot-out score


# 9 ---------------------------------------------------------------- availability


def test_results_become_visible_only_at_their_availability_time(store, tmp_path):
    start = datetime(2025, 3, 11, 20, tzinfo=UTC)
    _store_records(
        store,
        "openfootball_cups",
        "uefa.cl",
        [
            _record(
                "openfootball_cups",
                "uefa.cl",
                "2025-03-11",
                "A",
                "B",
                1,
                0,
                start_at=start,
                regulation_certain=True,
            )
        ],
        tmp_path,
    )
    row = store.rows("SELECT available_at, available_at_basis FROM historical_results")[0]
    assert row["available_at_basis"] == "assumed_lag_36h"
    assert row["available_at"] == iso(start + timedelta(hours=36))
    early = effective_results(
        store, sport="football", until=start + timedelta(hours=35), competitions=("uefa.cl",)
    )
    late = effective_results(
        store, sport="football", until=start + timedelta(hours=37), competitions=("uefa.cl",)
    )
    assert (len(early), len(late)) == (0, 1)


# 10 --------------------------------------------------------------- no data leakage


def test_ratings_use_only_results_of_earlier_days():
    matches = [
        {
            "id": "a",
            "match_date": "2025-01-01",
            "home": "X",
            "away": "Y",
            "home_score": 3,
            "away_score": 0,
        },
        {
            "id": "b",
            "match_date": "2025-01-08",
            "home": "X",
            "away": "Z",
            "home_score": 1,
            "away_score": 1,
        },
        {
            "id": "c",
            "match_date": "2025-01-08",
            "home": "Y",
            "away": "Z",
            "home_score": 0,
            "away_score": 5,
        },
    ]
    kwargs = {"k_of": lambda _m: 30.0, "home_of": lambda _m: 50.0}
    before = international.elo_walk(matches, **kwargs)
    changed = [*matches[:2], {**matches[2], "home_score": 9, "away_score": 0}]
    after = international.elo_walk(changed, **kwargs)
    assert before["b"] == after["b"] and before["c"] == after["c"]  # same day: no access
    assert before["a"] == 50 / 400  # first day: only the home advantage
    windows = international.CUP_WINDOWS
    assert (
        windows["calibration"][1]
        < windows["selection"][0]
        <= windows["selection"][1]
        < (windows["holdout"][0])
    )


# 11 --------------------------------------------------------------- cups in the league schedule


def test_rest_and_load_count_every_identified_competition(store):
    rows = [
        _row(1, "2025-03-01", "Real Madrid CF", "Getafe CF", 2, 0, competition="es.1"),
        _row(
            2,
            "2025-03-04",
            "Real Madrid CF",
            "Club X",
            1,
            1,
            competition="uefa.cl",
            duration="EXTRA_TIME",
            aet=[2, 1],
        ),
    ]
    plan = international._schedule(rows, "Real Madrid CF", "2025-03-08")
    assert plan["rest_days"] == 4 and plan["matches_14"] == 2 and plan["european_14"] == 1
    assert plan["last_extra_time"] is True
    assert plan["recent"][0]["competition"] == "Liga Mistrzów UEFA"
    assert plan["recent"][0]["score_90"] == "1:1"  # the 90 minutes, extra time apart
    from sports_edge import international_data

    upcoming = ResultRecord(
        provider="football_data_org",
        sport="football",
        competition="uefa.cl",
        season="2026-27",
        source_event_id="f1",
        home="Real Madrid CF",
        away="Club X",
        start_at=NOW + timedelta(days=5),
        match_date=(NOW + timedelta(days=5)).date().isoformat(),
        status="scheduled",
    )
    assert (
        international_data.save_fixtures(store, "football_data_org", "uefa.cl", [upcoming], now=NOW)
        == 1
    )
    (fixture,) = international_data.fixtures(store, "uefa.cl", until=NOW)
    assert fixture["home"] == "Real Madrid CF" and fixture["source"] == "football_data_org"
    assert international_data.season_window("uefa.cl", 2026, NOW.date())[1] == date(2027, 7, 1)


# 12 --------------------------------------------------------------- separate ratings


def test_pools_statuses_and_source_order_are_separate_per_category():
    assert clubs.namespace("uefa.nl") == "nation" and clubs.namespace("uefa.cl") == "club"
    assert not set(by_category("european")) & set(by_category("national"))
    assert priority("pitchapi", "uefa.cl") > priority("openfootball_cups", "uefa.cl")
    assert priority("pitchapi", "pl.1") < priority("openfootball", "pl.1")
    report = {
        "european": {"markets": {"1x2": {"decision": {"validated": True, "best": "C"}}}},
        "national": {"markets": {"1x2": {"decision": {"validated": False}}}},
    }
    assert international.market_status(report, "european")["1x2"]["validated"]
    assert not international.market_status(report, "national")["1x2"]["validated"]
    k = []
    international.elo_walk(
        [
            {
                "id": "a",
                "match_date": "2025-01-01",
                "home": "X",
                "away": "Y",
                "home_score": 1,
                "away_score": 0,
                "_cup": True,
            }
        ],
        k_of=lambda m: k.append(m["_cup"]) or (40.0 if m["_cup"] else 20.0),
        home_of=lambda _m: 0.0,
    )
    assert k == [True]  # the cup K is chosen per match (level control of the pool)


# 13 --------------------------------------------------------------- no coupon without validation


@dataclass(frozen=True)
class _Candidate:
    league: str
    sport: str
    market_key: str
    p_model: float | None
    p_low: float | None = None
    p_high: float | None = None
    model: str | None = "elo"
    model_version_id: str | None = "v"
    warnings: tuple = field(default_factory=tuple)


def test_cup_selections_never_reach_the_generator_without_validation_and_decision(store):
    cup = _Candidate("KLUBOWE - LIGA MISTRZÓW", "football", "1x2", 0.61, 0.55, 0.67)
    league = _Candidate("HISZPANIA - LALIGA", "football", "1x2", 0.61, 0.55, 0.67)
    gated = international.gate_candidates(store, [cup, league])
    assert gated[0].p_model is None and international.GENERATOR_MESSAGE in gated[0].warnings
    assert gated[1] == league
    report = {
        "european": {"markets": {"1x2": {"decision": {"validated": True, "best": "C_combined"}}}}
    }
    store.insert(
        "experiments",
        id=digest(["x"]),
        hypothesis=international.HYPOTHESIS,
        registered_at=iso(NOW),
        kind="backtest",
        config_json=canonical(report),
        status="completed",
    )
    allowed, reason = international.generator_allowed(store, "uefa.cl", "1x2")
    assert not allowed and "decyzję" in reason  # validated, but the user has not decided
    assert international.gate_candidates(store, [cup])[0].p_model is None


# 14 --------------------------------------------------------------- national teams vs clubs

NATIONS = """= UEFA Nations League 2025

▪ Group A
Thu Sep 4
  Spain                  2-0 Portugal                 @ Madrid, Spain
     (Pedri 10' Morata 90+2')
Sun Mar 23
  Spain                  3-3 Netherlands              @ Valencia, Spain
     (Oyarzabal 8',67' Yamal 103';
      Depay 54' Maatsen 79' Simons 109')
  Portugal               5-2 Denmark                  @ Lisbon, Portugal
     (Ronaldo 72' Trincao 86',91' Ramos 115' Mendes 30';
      Kristensen 56' Eriksen 76')
  Croatia                0-2 France                   @ Split, Croatia   [France wins on penalties]
     (Olise 52' Dembele 80')
"""


def test_national_teams_have_their_own_rules_namespace_and_members():
    records = openfootball_int.parse(
        NATIONS.encode(), competition="uefa.nl", year=2025, observed_at=NOW
    )
    group, extra, _ambiguous, shootout = records
    assert group.details["regulation_certain"] and group.details["venue_country"] == "Spain"
    assert (extra.home_score, extra.away_score) == (3, 3)  # in the group block: no extra time
    plain = openfootball_int.ninety(
        (3, 3),
        ([(8, False), (67, False), (103, False)], [(54, False), (79, False), (109, False)]),
        group_stage=False,
    )
    assert plain == ((2, 2), True, True)  # 103' and 109' are extra time
    assert (
        openfootball_int.ninety(
            (5, 2),
            (
                [(72, False), (86, False), (91, False), (115, False), (30, False)],
                [(56, False), (76, False)],
            ),
            group_stage=False,
        )[2]
        is False
    )  # 91': not knowable
    assert shootout.details["penalties_winner"] == "France"
    provider = openfootball_int.OpenfootballIntProvider(
        opener=_opener(
            {
                "2025_fifa_world_cup_qualification": (
                    b"= WCQ 2025\n\nSat Mar 22\n"
                    b"  Spain  1-0 Poland  @ Madrid, Spain\n     (Pedri 10')\n"
                    b"  Brazil  2-0 Chile  @ Rio, Brazil\n     (Raphinha 3' Vinicius 9')\n"
                )
            }
        ),
        members={"Spain", "Poland"},
    )
    found = provider.fetch("fifa.wc_q", date(2025, 1, 1), date(2026, 1, 1), now=NOW).records
    assert [(r.home, r.away) for r in found] == [("Spain", "Poland")]  # UEFA members only
    assert COMPETITIONS["uefa.nl"].nations and not COMPETITIONS["uefa.cl"].nations


# 15 --------------------------------------------------------------- scheduler


def test_international_jobs_are_in_the_web_scheduler_with_limits(store, app_paths, monkeypatch):
    from sports_edge import automation

    jobs = ("uefa_results", "national_results", "cup_monitor", "intl_completeness", "cup_training")
    for job in jobs:
        assert job in automation.JOBS and job in automation.JOB_LABELS
        assert automation.DEFAULTS["jobs"][job]["every_minutes"] in automation.FREQUENCIES[job]

    class Offline:
        offline_mode = True

    monkeypatch.setattr(automation, "_app", lambda _paths: Offline())
    assert automation.job_uefa_results(store, app_paths, now=NOW, settings={}).status == "skipped"
    outcome = automation.job_cup_training(store, app_paths, now=NOW, settings={})
    assert outcome.status == "skipped" and "za mało" in outcome.reason


# 16 --------------------------------------------------------------- deduplication


def test_two_sources_of_one_match_give_one_result_and_fixture_evidence_links_names(store, tmp_path):
    base = [
        _record(
            "openfootball_cups",
            "uefa.cl",
            "2025-02-11",
            "Club X",
            "Club Y",
            2,
            1,
            regulation_certain=True,
            home_country="AAA",
            away_country="BBB",
        ),
        _record(
            "openfootball_cups",
            "uefa.cl",
            "2025-02-12",
            "Club Z",
            "Club W",
            0,
            0,
            regulation_certain=True,
            home_country="CCC",
            away_country="DDD",
        ),
    ]
    _store_records(store, "openfootball_cups", "uefa.cl", base, tmp_path)
    existing = effective_results(store, sport="football", until=NOW, competitions=("uefa.cl",))
    other = [
        _record(
            "football_data_org",
            "uefa.cl",
            "2025-02-11",
            "X Club Official",
            "Club Y",
            2,
            1,
            regulation_certain=True,
        ),
        _record(
            "football_data_org",
            "uefa.cl",
            "2025-02-12",
            "Club Z",
            "Club W",
            0,
            0,
            regulation_certain=True,
        ),
    ]
    aliases = alias_map(store)
    first = _store_records(store, "football_data_org", "uefa.cl", other, tmp_path, aliases=aliases)
    assert first["summary"]["confirmations"] == 1 and first["unmatched"]
    learned = clubs.learn_fixture_aliases(
        store,
        other,
        existing,
        sport="football",
        normalizer=NAMES,
        aliases=aliases,
        now=NOW,
        origin="fixture_evidence:test",
    )
    assert learned == [{"source": "X Club Official", "canonical": "Club X", "fixtures": 1}]
    again = _store_records(store, "football_data_org", "uefa.cl", other, tmp_path)
    assert again["summary"]["confirmations"] >= 1 and not again["unmatched"]
    rows = effective_results(store, sport="football", until=NOW, competitions=("uefa.cl",))
    assert len(rows) == 2 and all(len(r["providers"]) == 2 for r in rows)
    found = match_participant(
        "X Club Official",
        "football",
        {participant_key("Club X", "football", NAMES): "Club X"},
        NAMES,
        alias_map(store),
    )
    assert found.status == "alias"
    # contradicting evidence (one spelling, two different clubs) is never linked
    contradicting = [
        _record("p2", "uefa.cl", "2025-02-11", "Mystery", "Club Y", 2, 1, regulation_certain=True),
        _record("p2", "uefa.cl", "2025-02-12", "Mystery", "Club W", 0, 0, regulation_certain=True),
    ]
    assert (
        clubs.learn_fixture_aliases(
            store,
            contradicting,
            rows,
            sport="football",
            normalizer=NAMES,
            aliases=alias_map(store),
            now=NOW,
            origin="fixture_evidence:test",
        )
        == []
    )


# 17 --------------------------------------------------------------- UI categories


def test_categories_of_the_interface_and_the_browser_scenario():
    pytest.importorskip("streamlit")
    from sports_edge import qa_visual
    from sports_edge.app import international_ui as iui

    assert iui.category("KLUBOWE - LIGA MISTRZÓW", "football") == "european"
    assert iui.category("KLUBOWE - LIGA MISTRZÓW", "basketball") == "other"
    assert iui.category("KLUBOWE - LIGA MISTRZÓW AFC", "football") == "other"
    assert iui.category("FRANCJA - PUCHAR", "football") == "domestic"
    assert iui.category("HISZPANIA - LALIGA", "football") == "domestic"
    assert list(iui.CHOICES.values()) == [
        "Wszystkie",
        "Ligi krajowe",
        "Europejskie puchary",
        "Reprezentacje",
    ]
    assert iui.matches("all", "KLUBOWE - LIGA MISTRZÓW AFC", "football")
    assert not iui.matches("european", "HISZPANIA - LALIGA", "football")
    assert "puchary_kategoria" in {name for name, _label, _run in qa_visual.SCENARIOS}
    assert np.isclose(sum(international.elo_probabilities(np.array([0.1]), 0.2)[0]), 1.0, atol=0.05)
