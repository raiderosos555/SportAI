"""API-Sports adapters and the win-probability ranking (TASK 004 extension).

Payloads are labelled SYNTHETIC fixtures in the documented API-Sports response shape; the
network is never used. Team names of the Ekstraklasa fixture match the synthetic STS PDF.
"""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from sports_edge.analysis.coupon import build_daily_coupon
from sports_edge.analysis.edge_engine import break_even_probability, evaluate
from sports_edge.analysis.policy import load_policy
from sports_edge.datasources.api_sports import (
    ApiSportsFootballProvider,
    ApiSportsGamesProvider,
    DailyQuota,
    parse_football,
    parse_games,
)
from sports_edge.models.registry import latest_predictions, register_version, save_prediction
from sports_edge.offers.sources import SourceBlocked
from sports_edge.offers.text import load_normalizer
from sports_edge.paper import import_source_results
from sports_edge.repository import offer_table
from sports_edge.results_engine import update_results

pytest.importorskip("pymupdf")

ROOT = Path(__file__).resolve().parents[1]
NAMES = load_normalizer(ROOT / "config" / "aliases.toml")
NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)


class _Response:
    def __init__(self, data: bytes, headers: dict | None = None):
        self.data = data
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self.data


def fx(fid, utc, home, away, short, *, ft=None, ht=None, goals=None, season=2026):
    """One SYNTHETIC API-Football v3 fixture."""
    goals = goals or ft or (None, None)
    return {
        "fixture": {"id": fid, "date": utc, "timezone": "UTC", "status": {"short": short}},
        "league": {"id": 106, "season": season, "round": "Regular Season - 12"},
        "teams": {
            "home": {"id": fid * 10, "name": home},
            "away": {"id": fid * 10 + 1, "name": away},
        },
        "goals": {"home": goals[0], "away": goals[1]},
        "score": {
            "halftime": {"home": ht[0] if ht else None, "away": ht[1] if ht else None},
            "fulltime": {"home": ft[0] if ft else None, "away": ft[1] if ft else None},
            "extratime": {"home": None, "away": None},
            "penalty": {"home": None, "away": None},
        },
    }


def body(response, *, errors=None, total=1) -> bytes:
    return json.dumps(
        {
            "fixture": "SYNTHETIC",
            "errors": errors if errors is not None else [],
            "paging": {"current": 1, "total": total},
            "response": response,
        }
    ).encode()


def test_api_sports_football_parser_keeps_regular_time():
    payload = json.loads(
        body(
            [
                fx(
                    1001,
                    "2026-10-25T15:00:00+00:00",
                    "Legia Warszawa",
                    "Lech Poznań",
                    "FT",
                    ft=(2, 1),
                    ht=(1, 0),
                ),
                fx(1002, "2026-10-26T18:00:00+00:00", "Pogoń Szczecin", "Raków Częstochowa", "PST"),
                fx(
                    1003,
                    "2026-10-27T18:00:00+00:00",
                    "Cracovia",
                    "Piast Gliwice",
                    "AET",
                    ft=(1, 1),
                    goals=(2, 1),
                ),
            ]
        )
    )
    records = {
        r.source_event_id: r for r in parse_football(payload, competition="pl.1", observed_at=NOW)
    }
    first = records["1001"]
    assert (first.status, first.home_score, first.away_score) == ("finished", 2, 1)
    assert first.details["ht"] == [1, 0] and first.match_date == "2026-10-25"
    assert first.season == "2026-27" and first.published_at is None and first.observed_at == NOW
    assert records["1002"].status == "postponed" and not records["1002"].complete
    extra = records["1003"]
    assert (extra.home_score, extra.away_score) == (1, 1)  # regular time, not 2:1 after ET
    assert extra.details["duration"] == "EXTRA_TIME" and extra.details["goals"] == [2, 1]


def test_api_sports_errors_paging_and_daily_quota(tmp_path):
    seen = []

    def paged(request, timeout=30):
        seen.append(request.full_url)
        page = 2 if "&page=2" in request.full_url else 1
        rows = [
            fx(
                2000 + page,
                f"2026-10-2{page}T15:00:00+00:00",
                "Widzew Łódź",
                "Motor Lublin",
                "FT",
                ft=(1, 0),
            )
        ]
        return _Response(body(rows, total=2), {"x-ratelimit-requests-remaining": "77"})

    quota = tmp_path / "quota.json"
    provider = ApiSportsFootballProvider(
        "synthetic-key", opener=paged, quota_path=quota, sleep=lambda s: None
    )
    outcome = provider.fetch("pl.1", date(2026, 10, 20), date(2026, 10, 31), now=NOW)
    assert outcome.requests == 2 and len(outcome.records) == 2 and "&page=2" in seen[1]
    assert "league=106&season=2026&from=2026-10-20&to=2026-10-30" in seen[0]
    assert DailyQuota(quota).remaining("football", NOW.date()) == 77  # server header wins

    def refused(request, timeout=30):
        return _Response(body([], errors={"plan": "Free plans do not have access to this season."}))

    blocked = ApiSportsFootballProvider(
        "synthetic-key", opener=refused, quota_path=None, sleep=lambda s: None
    )
    with pytest.raises(SourceBlocked, match="Free plans do not have access"):
        blocked.fetch("pl.1", date(2026, 10, 20), date(2026, 10, 31), now=NOW)
    nearly = DailyQuota(None)
    for _ in range(90):
        nearly.record("football", NOW.date(), server_remaining=None)
    assert not nearly.allows("football", NOW.date())  # the last 10 requests stay in reserve
    assert not ApiSportsFootballProvider(None).status(offline=False).active


def test_ekstraklasa_from_api_sports_settles_sts_events(store, import_sts, tmp_path):
    payload = body(
        [
            fx(
                3001,
                "2026-10-24T17:00:00+00:00",
                "Legia Warszawa",
                "Górnik Zabrze",
                "FT",
                ft=(1, 1),
            ),
            fx(
                3002,
                "2026-10-31T19:30:00+00:00",
                "Legia Warszawa",
                "Lech Poznań",
                "FT",
                ft=(2, 1),
                ht=(1, 1),
            ),
            fx(
                3003, "2026-10-31T17:00:00+00:00", "Górnik Zabrze", "Śląsk Wrocław", "FT", ft=(0, 0)
            ),
        ]
    )
    provider = ApiSportsFootballProvider(
        "synthetic-key", opener=lambda request, timeout=30: _Response(payload), sleep=lambda s: None
    )
    report = update_results(
        store,
        [provider],
        competitions=("pl.1",),
        now=NOW,
        normalizer=NAMES,
        aliases={},
        offline=False,
        raw_dir=tmp_path,
    )
    assert report["inserted"] == 3 and not report["unmatched"]  # first source sets the names
    rows = {r["source_event_id"]: r for r in store.rows("SELECT * FROM historical_results")}
    assert rows["3002"]["match_confidence"] == "source/source"
    assert rows["3002"]["available_at_basis"] == "observed_at_fetch"  # +36 h lies in the future
    assert rows["3001"]["available_at_basis"] == "assumed_lag_36h"
    out = import_source_results(
        store, now=NOW, normalizer=NAMES, team_aliases={}, only_coupons=False
    )
    assert out["recorded"] == 2  # Legia - Lech and Górnik - Śląsk from the STS fixture
    legia = next(
        r
        for r in offer_table(store, day="2026-10-31")
        if r["offer_code"] == "4201" and r["market_key"] == "1x2"
    )
    assert legia["result_status"] == "completed"


def test_other_sports_parse_regular_time_and_stay_inactive_until_verified():
    basketball = {
        "response": [
            {
                "id": 1,
                "date": "2026-11-01T00:30:00+00:00",
                "status": {"short": "AOT"},
                "league": {"season": "2026-2027"},
                "teams": {
                    "home": {"name": "Zespół Testowy A"},
                    "away": {"name": "Zespół Testowy B"},
                },
                "scores": {
                    "home": {
                        "quarter_1": 25,
                        "quarter_2": 25,
                        "quarter_3": 25,
                        "quarter_4": 25,
                        "over_time": 10,
                        "total": 110,
                    },
                    "away": {
                        "quarter_1": 30,
                        "quarter_2": 20,
                        "quarter_3": 25,
                        "quarter_4": 25,
                        "over_time": 8,
                        "total": 108,
                    },
                },
            }
        ]
    }
    record = parse_games(
        basketball, competition="basketball.nba", sport="basketball", observed_at=NOW
    )[0]
    assert (record.status, record.home_score, record.away_score) == ("finished", 100, 100)
    assert record.details["after_ot"] == [110, 108]
    hockey = {
        "response": [
            {
                "id": 2,
                "date": "2026-11-01T23:00:00+00:00",
                "status": {"short": "FT"},
                "league": {"season": 2026},
                "teams": {
                    "home": {"name": "Drużyna Lodowa A"},
                    "away": {"name": "Drużyna Lodowa B"},
                },
                "scores": {"home": 3, "away": 2},
                "periods": {"first": "1-0", "second": "1-1", "third": "1-1", "overtime": None},
            }
        ]
    }
    record = parse_games(hockey, competition="ice_hockey.nhl", sport="ice_hockey", observed_at=NOW)[
        0
    ]
    assert (record.home_score, record.away_score) == (3, 2) and record.details["after_ot"] is None
    for sport in ("basketball", "ice_hockey"):
        status = ApiSportsGamesProvider("synthetic-key", sport=sport).status(offline=False)
        assert not status.active and "niezweryfikowane" in status.reason


def test_probability_ranking_shows_break_even_and_derived_double_chance(
    store, import_sts, profiles, rules
):
    policy = load_policy(ROOT / "config/coupon_policy.toml")
    version_id, _, _ = register_version(
        store,
        family="synthetic_test_model",
        sport="football",
        description="test only",
        version="probable",
        created_at=datetime(2026, 10, 30, tzinfo=UTC),
        trained_through=None,
        training_data={"synthetic": True},
        validation={
            "holdout": {"n": 10, "reliability": [{"low": 0.5, "high": 0.6, "n": 40, "freq": 0.56}]}
        },
        policy=policy,
    )
    event = store.rows("SELECT id FROM events WHERE home='Legia Warszawa'")[0]["id"]
    save_prediction(
        store,
        model_version_id=version_id,
        event_id=event,
        market_key="1x2",
        period="FT",
        line=None,
        probabilities={"home": 0.55, "draw": 0.25, "away": 0.20},
        uncertainty={
            "interval": {"home": [0.50, 0.60], "draw": [0.21, 0.29], "away": [0.16, 0.24]}
        },
        inputs={
            "info_cutoff": "2026-10-30T00:00:00+00:00",
            "data_snapshot": "s",
            "code_state": "t",
        },
        kind="prospective",
        created_at=datetime(2026, 10, 31, 11, tzinfo=UTC),
    )
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    found = evaluate(
        offer_table(store, day="2026-10-31"),
        predictions=latest_predictions(store, as_of=now),
        profile=profiles["sts"],
        market_rules=rules,
        policy=policy,
        now=now,
        max_age=timedelta(minutes=180),
        stake=Decimal("10.00"),
    )
    legia = {(c.market_key, c.selection): c for c in found if c.event.startswith("Legia")}
    one_x = legia[("double_chance", "home_draw")]  # odds 1.40 in the synthetic PDF
    assert one_x.p_model == pytest.approx(0.80) and one_x.p_low == pytest.approx(0.71)
    assert one_x.break_even == pytest.approx(1 / (1.40 * 0.88))
    assert float(one_x.ev_net) == pytest.approx(0.80 * 1.40 * 0.88 - 1, abs=1e-4)  # a loss
    assert one_x.p_market is not None and "pochodna" in one_x.calibration
    assert "holdout 50%–60%" in legia[("1x2", "home")].calibration
    report = build_daily_coupon(found, policy, day="2026-10-31", now=now)
    probable = [(c.market_key, c.selection) for c in report.probable]
    assert probable[:2] == [("double_chance", "home_draw"), ("double_chance", "home_away")]
    assert ("1x2", "home") not in probable  # 55% is below the 65% threshold
    assert report.counts["probable"] == len(report.probable) and not report.solo
    assert break_even_probability(Decimal("1.10"), Decimal("0.12")) > 1  # a win still loses
