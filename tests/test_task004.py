"""TASK 004: results providers, the update engine, statuses, market periods, the one-button flow.

Every record below is a labelled SYNTHETIC fixture (invented teams, scores and odds). The
football-data.org adapter runs through a fake HTTP opener, never the network. A passing flow
is a functional check, not evidence of predictive skill or profit.
"""

import json
import random
import sqlite3
import urllib.error
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from sports_edge.analysis.policy import Policy
from sports_edge.datasources.base import RateLimiter, http_get
from sports_edge.datasources.bookmaker_results import StsResultsProvider, SuperbetResultsProvider
from sports_edge.datasources.football_data import FootballDataOrgProvider, api_key, parse
from sports_edge.matching import alias_map, confirm_alias
from sports_edge.offers.sources import SourceBlocked
from sports_edge.offers.text import load_normalizer
from sports_edge.paper import import_source_results, record_status_results, unresolved_reason
from sports_edge.repository import event_status, import_offer, odds_freshness, offer_table
from sports_edge.results_engine import conflicts, effective_results, update_results
from sports_edge.sportsdata import (
    Split,
    history,
    import_results,
    import_results_csv,
    predict_offer,
    run_elo_experiment,
)

pytest.importorskip("pymupdf")

from sports_edge.pdf.engine import parse_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 31, 11, 30, tzinfo=UTC)
NAMES = load_normalizer(ROOT / "config" / "aliases.toml")
TEAMS = ["Orły Testowe", "Sokoły Testowe", "Wilki Testowe", "Lwy Testowe"]  # SYNTHETIC
SAMPLES = ROOT / "data" / "samples" / "sts" / "2026-10-31"


def noop(_seconds: float) -> None:
    return None


class _Response:
    def __init__(self, data: bytes):
        self.data = data
        self.headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self.data


def opener(payloads: dict[str, bytes], seen: list | None = None, fail: frozenset = frozenset()):
    """Fake football-data.org: one payload per competition code; ``fail`` simulates outages."""

    def open_url(request, timeout=30):
        url = request.full_url
        if seen is not None:
            seen.append(url)
        code = url.split("/competitions/")[1].split("/")[0]
        if code in fail:
            raise urllib.error.URLError("synthetic network failure")
        return _Response(payloads.get(code, fd([])))

    return open_url


def match(mid, utc, home, away, *, ft=None, ht=None, status="FINISHED", updated="auto", **extra):
    """One SYNTHETIC match in the documented football-data.org v4 shape."""
    start = datetime.fromisoformat(utc.replace("Z", "+00:00"))
    if updated == "auto":
        updated = (start + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    score = {
        "winner": None,
        "duration": extra.get("duration", "REGULAR"),
        "fullTime": {"home": ft[0], "away": ft[1]} if ft else {"home": None, "away": None},
        "halfTime": {"home": ht[0], "away": ht[1]} if ht else {"home": None, "away": None},
    }
    if "regular" in extra:
        score["regularTime"] = {"home": extra["regular"][0], "away": extra["regular"][1]}
    return {
        "id": mid,
        "utcDate": utc,
        "status": status,
        "matchday": 9,
        "lastUpdated": updated,
        "season": {"startDate": "2026-08-14"},
        "homeTeam": {"id": 100 + mid, "name": home, "shortName": home},
        "awayTeam": {"id": 200 + mid, "name": away, "shortName": away},
        "score": score,
    }


def fd(matches: list[dict]) -> bytes:
    return json.dumps({"fixture": "SYNTHETIC", "matches": matches}).encode()


def provider(payloads: dict[str, bytes], **kwargs) -> FootballDataOrgProvider:
    return FootballDataOrgProvider(
        "synthetic-test-key", opener=opener(payloads, **kwargs), sleep=noop
    )


def run(store, source, raw_dir, *, competitions=("es.1",), now=NOW, aliases=None):
    return update_results(
        store,
        [source],
        competitions=competitions,
        now=now,
        normalizer=NAMES,
        aliases=aliases or {},
        offline=False,
        raw_dir=raw_dir,
    )


def seed(store, *, code="es.1", provider_id=None, rows=None):
    """Two SYNTHETIC historical matches ending 2026-09-20 (assumed 36 h availability)."""
    rows = rows or [
        _row("2026-09-19", "Orły Testowe", "Sokoły Testowe", 1, 0),
        _row("2026-09-20", "Wilki Testowe", "Lwy Testowe", 2, 2),
    ]
    return import_results(
        store,
        rows,
        file_bytes=f"{code}:{provider_id}:{rows}".encode(),
        file_name="synthetic.json",
        source_name="openfootball/football.json" if provider_id else "SYNTHETIC FIXTURE",
        license_note="dane syntetyczne (test)",
        sport="football",
        competition=code,
        season="2026-27",
        now=NOW,
        availability_basis="assumed_lag_36h",
        provider=provider_id,
    )


def _row(day: str, home: str, away: str, h: int, a: int) -> dict:
    start = datetime.fromisoformat(f"{day}T18:00:00+00:00")
    return {
        "match_date": day,
        "start_at": start,
        "home": home,
        "away": away,
        "home_score": h,
        "away_score": a,
        "available_at": start + timedelta(hours=36),
    }


def synthetic_history(end: date, n: int, seed: int = 5) -> bytes:
    """SYNTHETIC league (Real Madrid / Barcelona names only to meet the STS fixture)."""
    rng = random.Random(seed)
    teams = ["Real Madrid CF", "FC Barcelona"] + [
        f"Klub Syntetyczny {chr(65 + i)} CF" for i in range(18)
    ]
    strength = {t: rng.gauss(0, 0.6) for t in teams}
    lines = ["date;home;away;home_score;away_score"]
    for k in range(n):
        home, away = rng.sample(teams, 2)
        lam_h = 1.45 * 2.718 ** (strength[home] - strength[away] + 0.1)
        lam_a = 1.10 * 2.718 ** (strength[away] - strength[home])
        hs = sum(rng.random() < lam_h / 6 for _ in range(6))
        as_ = sum(rng.random() < lam_a / 6 for _ in range(6))
        lines.append(f"{(end - timedelta(days=n - 1 - k)).isoformat()};{home};{away};{hs};{as_}")
    return "\n".join(lines).encode()


def recent_matches(first_id: int = 70) -> list[dict]:
    """SYNTHETIC results after the history gap (4, 11, 18 and 25 October 2026)."""
    pairs = [
        ("Klub Syntetyczny A CF", "Klub Syntetyczny B CF"),
        ("Real Madrid CF", "Klub Syntetyczny C CF"),
        ("FC Barcelona", "Klub Syntetyczny D CF"),
        ("Klub Syntetyczny E CF", "Klub Syntetyczny F CF"),
    ]
    return [
        match(first_id + i, f"2026-10-{day:02d}T19:00:00Z", *pair, ft=(1 + i % 2, i % 3), ht=(1, 0))
        for i, (day, pair) in enumerate(zip((4, 11, 18, 25), pairs, strict=True))
    ]


# ---------------------------------------------------------------- providers


def test_football_data_parser_standardises_records():
    data = fd(
        [
            match(
                1, "2026-09-26T19:00:00Z", "Orły Testowe", "Sokoły Testowe", ft=(2, 1), ht=(1, 0)
            ),
            match(
                2,
                "2026-09-27T14:00:00Z",
                "Wilki Testowe",
                "Lwy Testowe",
                status="POSTPONED",
                updated=None,
            ),
            match(
                3,
                "2026-11-08T20:00:00Z",
                "Lwy Testowe",
                "Orły Testowe",
                status="TIMED",
                updated=None,
            ),
            match(
                4,
                "2026-09-28T19:00:00Z",
                "Sokoły Testowe",
                "Wilki Testowe",
                ft=(1, 1),
                updated="2026-09-28T19:30:00Z",
            ),
            match(
                5,
                "2026-09-29T19:00:00Z",
                "Lwy Testowe",
                "Sokoły Testowe",
                ft=(3, 3),
                duration="EXTRA_TIME",
                regular=(2, 2),
            ),
        ]
    )
    records = {r.source_event_id: r for r in parse(data, competition="es.1", observed_at=NOW)}
    first = records["1"]
    assert (first.status, first.home_score, first.away_score) == ("finished", 2, 1)
    assert first.details["ht"] == [1, 0] and first.match_date == "2026-09-26"
    assert first.published_at == datetime(2026, 9, 26, 21, tzinfo=UTC) and first.complete
    assert first.observed_at == NOW and first.details["attribution"].startswith("Football data")
    assert records["2"].status == "postponed" and not records["2"].complete
    assert records["3"].status == "scheduled"
    assert records["4"].published_at is None  # updated before the final whistle: not trusted
    assert (records["5"].home_score, records["5"].away_score) == (2, 2)  # regular time only


def test_http_layer_retries_limits_and_never_retries_refusals():
    calls, sleeps = [], []

    def flaky(request, timeout=30):
        calls.append(request.full_url)
        if len(calls) < 3:
            raise urllib.error.URLError("synthetic outage")
        return _Response(b"{}")

    data, _ = http_get("https://example.test/x", headers={}, opener=flaky, sleep=sleeps.append)
    assert data == b"{}" and len(calls) == 3 and sleeps == [2.0, 4.0]

    def refused(request, timeout=30):
        calls.append("403")
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    calls.clear()
    with pytest.raises(SourceBlocked, match="403"):
        http_get("https://example.test/y", headers={}, opener=refused, sleep=sleeps.append)
    assert calls == ["403"]  # access refusals are final, never worked around
    clock, waits = [0.0], []

    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    limiter = RateLimiter(2, clock=lambda: clock[0], sleep=sleep)
    limiter.wait()
    limiter.wait()
    limiter.wait()  # third request inside the same minute must wait
    assert len(waits) == 1 and 59 < waits[0] <= 61


def test_keys_stay_local_and_bookmaker_websites_stay_inactive(tmp_path, monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)
    assert api_key(tmp_path) is None
    (tmp_path / ".env").write_text("# local\nFOOTBALL_DATA_API_KEY=synthetic-key\n", "utf-8")
    assert api_key(tmp_path) == "synthetic-key"
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "from-environment")
    assert api_key(tmp_path) == "from-environment"
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    for inactive in (StsResultsProvider(), SuperbetResultsProvider()):
        status = inactive.status(offline=False)
        assert not status.active and "robots.txt" in status.reason
        with pytest.raises(SourceBlocked):
            inactive.fetch("es.1", date(2026, 10, 1), date(2026, 10, 2), now=NOW)
    missing = FootballDataOrgProvider(None).status(offline=False)
    assert not missing.active and "FOOTBALL_DATA_API_KEY" in missing.reason
    assert not FootballDataOrgProvider("k").status(offline=True).active


# ---------------------------------------------------------------- update engine


def test_update_fetches_only_the_missing_window_and_is_idempotent(store, tmp_path):
    seed(store)
    seen = []
    payload = fd(
        [
            match(10 + i, f"2026-09-2{6 + i}T19:00:00Z", TEAMS[i], TEAMS[(i + 1) % 4], ft=(i, 1))
            for i in range(3)
        ]
    )
    source = provider({"PD": payload}, seen=seen)
    report = run(store, source, tmp_path)
    entry = report["competitions"]["es.1"]
    assert entry["last_before"] == "2026-09-20"
    assert entry["window"] == ["2026-09-17", "2026-10-31"]  # 3-day overlap for corrections
    assert seen == ["https://api.football-data.org/v4/competitions/PD/matches?season=2026"]
    assert report["inserted"] == 3 and report["requests"] == 1
    assert (tmp_path / "football_data_org" / entry["files"][0]).read_bytes() == payload
    imported = store.rows("SELECT * FROM data_imports WHERE kind='results_api'")
    assert len(imported) == 1 and json.loads(imported[0]["summary_json"])["inserted"] == 3
    rows = store.rows("SELECT * FROM historical_results WHERE provider='football_data_org'")
    assert {r["available_at_basis"] for r in rows} == {"source_last_updated"}
    assert {r["match_confidence"] for r in rows} == {"exact/exact"} and rows[0]["observed_at"]
    again = run(store, source, tmp_path)
    assert again["inserted"] == 0 and again["competitions"]["es.1"]["status"] == "bez zmian"
    assert again["competitions"]["es.1"]["duplicates"] == 3
    assert store.scalar("SELECT COUNT(*) FROM historical_results") == 5


def test_second_source_confirms_or_conflicts_and_the_hierarchy_decides(store, tmp_path):
    seed(store, provider_id="openfootball")
    payload = fd(
        [
            match(20, "2026-09-19T18:00:00Z", "Orły Testowe", "Sokoły Testowe", ft=(1, 0)),
            match(21, "2026-09-20T18:00:00Z", "Wilki Testowe", "Lwy Testowe", ft=(2, 1)),
        ]
    )
    report = run(store, provider({"PD": payload}), tmp_path)
    assert report["confirmations"] == 1 and len(report["conflicts"]) == 1
    rows = {
        (r["home"], r["away"]): r for r in effective_results(store, sport="football", until=NOW)
    }
    agreed = rows[("Orły Testowe", "Sokoły Testowe")]
    assert agreed["providers"] == ["football_data_org", "openfootball"] and not agreed["conflict"]
    disputed = rows[("Wilki Testowe", "Lwy Testowe")]
    assert disputed["conflict"] and disputed["provider_resolved"] == "football_data_org"
    assert (disputed["home_score"], disputed["away_score"]) == (2, 1)
    found = conflicts(store, until=NOW)
    assert len(found) == 1 and found[0]["variants"] == [
        "2:1 (football_data_org)",
        "2:2 (openfootball)",
    ]
    assert len(history(store, sport="football", until=NOW)) == 2  # one match per fixture
    assert store.scalar("SELECT COUNT(*) FROM historical_results") == 4  # nothing overwritten


def test_corrections_are_new_rows_and_history_stays_as_of(store, tmp_path):
    seed(store)
    first = fd([match(30, "2026-09-26T19:00:00Z", "Orły Testowe", "Lwy Testowe", ft=(1, 0))])
    run(store, provider({"PD": first}), tmp_path)
    fixed = fd(
        [
            match(
                30,
                "2026-09-26T19:00:00Z",
                "Orły Testowe",
                "Lwy Testowe",
                ft=(1, 1),
                updated="2026-09-27T08:00:00Z",
            )
        ]
    )
    later = NOW + timedelta(hours=1)
    report = run(store, provider({"PD": fixed}), tmp_path, now=later)
    assert report["corrections"] == ["Orły Testowe - Lwy Testowe (2026-09-26): 1:0 → 1:1"]

    def score(until):
        row = next(
            r
            for r in effective_results(store, sport="football", until=until)
            if r["home"] == "Orły Testowe" and r["match_date"] == "2026-09-26"
        )
        return row["home_score"], row["away_score"], row["corrections"]

    assert score(later) == (1, 1, 1)
    assert score(datetime(2026, 9, 27, tzinfo=UTC)) == (1, 0, 0)  # the future is not visible
    assert store.scalar("SELECT COUNT(*) FROM historical_results WHERE source_event_id='30'") == 2
    with pytest.raises(sqlite3.IntegrityError, match="append_only"):
        store.connection.execute("UPDATE historical_results SET home_score=9")


def test_interrupted_fetch_keeps_finished_competitions_and_resumes(store, tmp_path):
    seed(store)
    seed(store, code="en.1", rows=[_row("2026-09-20", "Orły Testowe", "Wilki Testowe", 0, 0)])
    payloads = {
        "PL": fd([match(40, "2026-09-26T14:00:00Z", "Wilki Testowe", "Orły Testowe", ft=(3, 0))]),
        "PD": fd([match(41, "2026-09-26T19:00:00Z", "Sokoły Testowe", "Lwy Testowe", ft=(0, 1))]),
    }
    broken = run(
        store, provider(payloads, fail=frozenset({"PL"})), tmp_path, competitions=("en.1", "es.1")
    )
    assert (
        broken["errors"][0].startswith("en.1:")
        and "błąd" in broken["competitions"]["en.1"]["status"]
    )
    assert broken["competitions"]["es.1"]["inserted"] == 1
    resumed = run(store, provider(payloads), tmp_path, competitions=("en.1", "es.1"))
    assert resumed["competitions"]["en.1"]["inserted"] == 1
    assert resumed["competitions"]["es.1"]["status"] == "bez zmian"


def test_incomplete_and_future_records_are_refused_observed_ones_kept(store, tmp_path):
    seed(store)
    payload = fd(
        [
            match(50, "2026-09-26T19:00:00Z", "Orły Testowe", "Sokoły Testowe", ft=None),
            match(51, "2026-10-31T19:00:00Z", "Wilki Testowe", "Lwy Testowe", ft=(1, 0)),
            match(
                52, "2026-10-31T08:30:00Z", "Lwy Testowe", "Orły Testowe", ft=(0, 0), updated=None
            ),
            match(
                53,
                "2026-10-04T14:00:00Z",
                "Sokoły Testowe",
                "Wilki Testowe",
                status="POSTPONED",
                updated=None,
            ),
        ]
    )
    report = run(store, provider({"PD": payload}), tmp_path)
    messages = " | ".join(report["incomplete"])
    assert len(report["incomplete"]) == 2
    assert "bez pełnego wyniku" in messages and "jeszcze się nie zaczął" in messages
    assert [s["status"] for s in report["status_records"]] == ["postponed"]
    # No source timestamp and the +36 h assumption lies in the future: the result is usable
    # from the moment this application observed the final status (an observed fact).
    assert report["inserted"] == 1
    row = store.rows("SELECT * FROM historical_results WHERE source_event_id='52'")[0]
    assert (row["available_at"], row["available_at_basis"]) == (
        "2026-10-31T11:30:00.000000+00:00",
        "observed_at_fetch",
    )


def test_unknown_names_wait_for_a_confirmed_alias(store, tmp_path):
    seed(store)
    payload = fd(
        [match(60, "2026-09-26T19:00:00Z", "Orły Testowe Miasto", "Sokoły Testowe", ft=(2, 0))]
    )
    source = provider({"PD": payload})
    first = run(store, source, tmp_path)
    assert first["inserted"] == 0 and first["unmatched"][0]["source"] == "Orły Testowe Miasto"
    confirm_alias(
        store,
        sport="football",
        alias="Orły Testowe Miasto",
        canonical="Orły Testowe",
        normalizer=NAMES,
        at=NOW,
    )
    second = run(store, source, tmp_path, aliases=alias_map(store))
    assert second["inserted"] == 1  # the identical payload is re-processed record by record
    assert second["competitions"]["es.1"]["status"].startswith("identyczna odpowiedź")
    row = store.rows("SELECT * FROM historical_results WHERE provider='football_data_org'")[0]
    assert (row["home"], row["home_raw"], row["match_confidence"]) == (
        "Orły Testowe",
        "Orły Testowe Miasto",
        "alias/exact",
    )


def test_stale_elo_history_blocks_forecasts_until_the_gap_is_filled(store, import_sts, tmp_path):
    import_results_csv(
        store,
        synthetic_history(date(2026, 9, 20), 1800),
        file_name="synthetic.csv",
        source_name="SYNTHETIC FIXTURE",
        license_note="dane syntetyczne (test)",
        sport="football",
        competition="es.1",
        now=NOW,
    )
    model = run_elo_experiment(
        store,
        competitions=("es.1",),
        split=Split("2024-09-01", "2025-09-01"),
        now=NOW,
        policy=Policy(min_validation_events=300),
    )
    events = store.rows("SELECT * FROM events WHERE league='HISZPANIA - LALIGA'")

    def forecast():
        return predict_offer(
            store, version_id=model["version_id"], events=events, normalizer=NAMES, now=NOW
        )

    stale = forecast()
    assert stale["saved"] == 0 and any("nieaktualne dane" in s for s in stale["skipped"])
    assert run(store, provider({"PD": fd(recent_matches())}), tmp_path)["inserted"] == 4
    fresh = forecast()
    assert fresh["saved"] == 1 and fresh["last_result"]["es.1"] == "2026-10-25"
    again = forecast()
    assert again["saved"] == 0 and again["kept"] == 1  # same model and data: no duplicate
    stored = store.rows("SELECT inputs_json FROM predictions")
    assert len(stored) == 1 and "football_data_org" in json.loads(stored[0]["inputs_json"])["data"]


# ---------------------------------------------------------------- statuses and markets


def test_event_status_odds_age_and_market_presence_are_independent(store, sts_fixture_pdf):
    start, document = "2026-10-31T20:00:00+00:00", "2026-10-31T07:00:00+00:00"
    afternoon = datetime(2026, 10, 31, 17, tzinfo=UTC)
    assert event_status(start, afternoon, sport="football") == "before_start"
    assert odds_freshness(document, afternoon, timedelta(minutes=180)) == ("unconfirmed", 10.0)
    assert odds_freshness(None, afternoon, timedelta(minutes=180)) == ("unknown", None)
    assert (
        event_status(start, datetime(2026, 10, 31, 21, tzinfo=UTC), sport="football") == "in_play"
    )
    assert event_status(start, datetime(2026, 11, 1, 9, tzinfo=UTC), sport="football") == "unknown"
    assert event_status(start, afternoon, result_status="postponed") == "postponed"
    assert event_status(start, afternoon, result_status="completed") == "finished"
    week, parsed = parse_pdf(sts_fixture_pdf, file_name="oferta-nastepne-dni-www.pdf")
    import_offer(
        store,
        week,
        sha256=parsed.sha256,
        file_name="oferta-nastepne-dni-www.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=datetime(2026, 10, 31, 11, 5, tzinfo=UTC),
    )
    today, _ = parse_pdf(sts_fixture_pdf, file_name="oferta-dzisiaj-www.pdf")
    today = replace(
        today,
        rows=tuple(r for r in today.rows if r.offer_code != "4105"),
        document_generated_at=datetime(2026, 10, 31, 13, tzinfo=UTC),
    )
    import_offer(
        store,
        today,
        sha256="e" * 64,
        file_name="oferta-dzisiaj-www.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=datetime(2026, 10, 31, 13, 5, tzinfo=UTC),
    )
    rows = {(r["offer_code"], r["market_key"]): r for r in offer_table(store, day="2026-10-31")}
    assert rows[("4105", "draw_no_bet")]["presence"] == "unconfirmed"  # other kind, not withdrawn
    assert rows[("4101", "1x2")]["presence"] == "current"


def _halves_pdf() -> bytes:
    """SYNTHETIC document modelled on the STS layout of Getafe - Sevilla (01.11.2026)."""
    from sports_edge.demo import build_sts_pdf

    six = ["1", "X", "2", "1X", "X2", "12"]
    name = "Drużyna Testowa A - Drużyna Testowa B"
    blocks = [
        {"day": "NIEDZIELA 2026-11-01"},
        {"sport": "PIŁKA NOŻNA"},
        {"league": "HISZPANIA - LALIGA"},
        {"market": "Mecz", "labels": six},
        {"event": ("9001", name, ["3.10", "2.90", "2.40", "1.50", "1.33", "1.40"], "14:00")},
        {"market": "1. połowa lub wynik końcowy", "labels": ["1", "X", "2"]},
        {"event": ("9002", name, ["2.40", "1.60", "2.00"], "14:00")},
        {"market": "1. połowa", "labels": six},
        {"event": ("9003", name, ["3.80", "1.90", "3.20", "1.30", "1.25", "1.75"], "14:00")},
        {"market": "2. połowa", "labels": six},
        {"event": ("9004", name, ["3.40", "2.20", "2.80", "1.38", "1.30", "1.55"], "14:00")},
        {"league": "TESTLANDIA - LIGA WYPEŁNIACZY"},
        {"market": "Mecz", "labels": six},
    ]
    blocks += [
        {
            "event": (
                f"{7000 + i}",
                f"Klub {i:02d} - Klub {i + 100:02d}",
                ["2.20", "3.20", "3.10", "1.33", "1.58", "1.30"],
                "16:00",
            )
        }
        for i in range(60)
    ]
    return build_sts_pdf(blocks, generated="2026-11-01 08:00")


def test_match_halves_and_ht_or_ft_never_share_the_full_time_forecast(store, profiles, rules):
    from sports_edge.analysis.edge_engine import evaluate
    from sports_edge.analysis.policy import load_policy
    from sports_edge.models.registry import latest_predictions, register_version, save_prediction

    result, document = parse_pdf(_halves_pdf(), file_name="oferta-dzisiaj-www.pdf")
    found = {
        (r.offer_code, r.market_key, r.period, r.market_raw)
        for r in result.rows
        if r.offer_code in {"9001", "9002", "9003", "9004"}
    }
    assert found == {
        ("9001", "1x2", "FT", "Mecz"),
        ("9001", "double_chance", "FT", "Mecz"),
        ("9002", "ht_or_ft", "FT", "1. połowa lub wynik końcowy"),
        ("9003", "1x2", "H1", "1. połowa"),
        ("9003", "double_chance", "H1", "1. połowa"),
        ("9004", "1x2", "H2", "2. połowa"),
        ("9004", "double_chance", "H2", "2. połowa"),
    }
    import_offer(
        store,
        result,
        sha256=document.sha256,
        file_name="oferta-dzisiaj-www.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=datetime(2026, 11, 1, 7, 5, tzinfo=UTC),
    )
    policy = load_policy(ROOT / "config/coupon_policy.toml")
    version_id, _, _ = register_version(
        store,
        family="synthetic_test_model",
        sport="football",
        description="test only",
        version="halves",
        created_at=datetime(2026, 11, 1, 6, tzinfo=UTC),
        trained_through=None,
        training_data={"synthetic": True},
        validation={"holdout": {"n": 10}},
        policy=policy,
    )
    event = store.rows("SELECT id FROM events WHERE home='Drużyna Testowa A'")[0]["id"]
    save_prediction(
        store,
        model_version_id=version_id,
        event_id=event,
        market_key="1x2",
        period="FT",
        line=None,
        probabilities={"home": 0.40, "draw": 0.30, "away": 0.30},
        uncertainty={"interval": {"home": [0.35, 0.45]}},
        inputs={
            "info_cutoff": "2026-11-01T06:00:00+00:00",
            "data_snapshot": "s",
            "code_state": "t",
        },
        kind="prospective",
        created_at=datetime(2026, 11, 1, 7, 30, tzinfo=UTC),
    )
    now = datetime(2026, 11, 1, 9, tzinfo=UTC)
    candidates = evaluate(
        offer_table(store, day="2026-11-01"),
        predictions=latest_predictions(store, as_of=now),
        profile=profiles["sts"],
        market_rules=rules,
        policy=policy,
        now=now,
        max_age=timedelta(minutes=180),
        stake=Decimal("10.00"),
    )
    by_code = {}
    for c in candidates:
        if c.offer_code in {"9001", "9002", "9003", "9004"}:
            by_code.setdefault((c.offer_code, c.market_key), []).append(c)
    assert all(c.p_model is not None for c in by_code[("9001", "1x2")])
    for key in (("9002", "ht_or_ft"), ("9003", "1x2"), ("9004", "1x2")):
        assert all(c.p_model is None for c in by_code[key]), key  # no FT forecast for halves
    labels = {key: group[0].market_label for key, group in by_code.items()}
    assert labels[("9001", "1x2")] == "1X2 — czas regulaminowy"
    assert labels[("9003", "1x2")] == "1X2 — 1. połowa"
    assert labels[("9004", "1x2")] == "1X2 — 2. połowa"
    assert by_code[("9003", "1x2")][0].label_raw == "1. połowa"
    assert by_code[("9001", "1x2")][0].source_text.startswith("9001 Drużyna Testowa A")


@pytest.mark.skipif(not SAMPLES.is_dir(), reason="no local authentic STS samples")
def test_real_getafe_sevilla_markets_keep_their_periods():
    data = (SAMPLES / "oferta-jutro-www.pdf").read_bytes()
    result, _ = parse_pdf(data, file_name="oferta-jutro-www.pdf")
    found = {
        (r.offer_code, r.market_key, r.period, r.market_raw)
        for r in result.rows
        if r.offer_code in {"1848", "61614", "45799", "45798"}
    }
    assert ("1848", "1x2", "FT", "Mecz") in found
    assert ("61614", "1x2", "H1", "1. połowa") in found
    assert ("45799", "1x2", "H2", "2. połowa") in found
    assert ("45798", "ht_or_ft", "FT", "1. połowa lub wynik końcowy") in found


# ---------------------------------------------------------------- results of STS events


def test_settlement_waits_when_the_final_score_cannot_decide():
    completed = {"status": "completed", "verified": 1, "ht_home": None, "ht_away": None}
    red_card = {"market_key": "red_card", "period": "FT", "line": None}
    assert unresolved_reason(red_card, completed).startswith("Wynik końcowy nie wystarcza")
    first_half = {"market_key": "1x2", "period": "H1", "line": None}
    assert (
        unresolved_reason(first_half, completed)
        == "Brak wyniku do przerwy — oczekuje na dodatkowe dane"
    )
    assert unresolved_reason(red_card, None) == "Brak wyniku wydarzenia — oczekuje na wynik"
    push = {"market_key": "total", "period": "FT", "line": "2"}
    assert "możliwy zwrot" in unresolved_reason(push, completed)


def test_provider_postponement_becomes_the_sts_event_status(store, import_sts, profiles, rules):
    from sports_edge.analysis.edge_engine import evaluate
    from sports_edge.analysis.policy import load_policy

    out = record_status_results(
        store,
        [
            {
                "competition": "es.1",
                "home": "Real Madrid CF",
                "away": "FC Barcelona",
                "start_at": "2026-10-31T20:00:00+00:00",
                "match_date": "2026-10-31",
                "status": "postponed",
                "provider": "football_data_org",
                "source_event_id": "99",
            }
        ],
        now=NOW,
        normalizer=NAMES,
        team_aliases={},
    )
    assert out["recorded"] == 1
    rows = offer_table(store, day="2026-10-31")
    real = [r for r in rows if r["offer_code"] == "4101" and r["market_key"] == "1x2"]
    assert real[0]["result_status"] == "postponed"
    candidates = evaluate(
        real,
        predictions={},
        profile=profiles["sts"],
        market_rules=rules,
        policy=load_policy(ROOT / "config/coupon_policy.toml"),
        now=NOW,
        max_age=timedelta(minutes=180),
        stake=Decimal("10.00"),
    )
    assert all("Wydarzenie przełożone — kurs nieaktualny" in c.reasons for c in candidates)
    assert all(c.event_status == "postponed" and c.tier == "rejected" for c in candidates)


def test_conflicting_sources_block_automatic_results(store, import_sts):
    later = datetime(2026, 11, 2, tzinfo=UTC)

    def add(provider_id, h, a):
        import_results(
            store,
            [
                {
                    "match_date": "2026-10-31",
                    "start_at": datetime(2026, 10, 31, 20, tzinfo=UTC),
                    "home": "Real Madrid CF",
                    "away": "FC Barcelona",
                    "home_score": h,
                    "away_score": a,
                    "available_at": datetime(2026, 11, 1, 10, tzinfo=UTC),
                }
            ],
            file_bytes=f"synthetic-{provider_id}".encode(),
            file_name="synthetic.json",
            source_name=f"SYNTHETIC {provider_id}",
            license_note="dane syntetyczne (test)",
            sport="football",
            competition="es.1",
            season="2026-27",
            now=later,
            availability_basis="assumed_lag_36h",
            provider=provider_id,
        )

    add("openfootball", 2, 1)
    add("football_data_org", 2, 2)
    out = import_source_results(
        store, now=later, normalizer=NAMES, team_aliases={}, only_coupons=False
    )
    assert out["recorded"] == 0
    assert any("różne wyniki" in line for line in out["unresolved"])


# ---------------------------------------------------------------- one button


def test_one_button_flow_from_pdf_to_settlement(app_paths, sts_fixture_pdf):
    """STS PDF → results update → SQLite → Elo → forecast → EV → ranking → paper → result →
    settlement → JSON report, on SYNTHETIC data (functional, not a skill test)."""
    from sports_edge import services
    from sports_edge.analysis.coupon import NO_EDGE
    from sports_edge.bookmakers import load_profiles
    from sports_edge.export import export_json
    from sports_edge.paper import create_coupon, performance, settle_coupon
    from sports_edge.settings import AppSettings

    store = services.open_store(app_paths)
    settings = AppSettings(offline_mode=False)
    services.import_pdf_bytes(
        store, app_paths, settings, sts_fixture_pdf, file_name="fixture_flow.pdf", now=NOW
    )
    import_results_csv(
        store,
        synthetic_history(date(2026, 9, 20), 1800),
        file_name="synthetic.csv",
        source_name="SYNTHETIC FIXTURE",
        license_note="dane syntetyczne (test)",
        sport="football",
        competition="es.1",
        now=NOW,
    )
    run_elo_experiment(
        store,
        competitions=("es.1",),
        split=Split("2024-09-01", "2025-09-01"),
        now=NOW,
        policy=services.policy(app_paths),
    )
    payloads = {"PD": fd(recent_matches())}
    stages_seen = []
    flow = services.run_daily_analysis(
        store,
        app_paths,
        settings,
        "2026-10-31",
        now=NOW,
        providers=[provider(payloads)],
        progress=lambda index, name, detail: stages_seen.append(name),
    )
    assert stages_seen == list(services.STAGES)
    stages = {s["stage"]: s for s in flow.stages}
    assert stages["Dane sportowe"]["update"]["inserted"] == 4
    assert stages["Modele"]["forecasts_saved"] == 1 and not stages["Modele"]["trained_now"]
    real = [
        c for c in flow.candidates if c.event == "Real Madrid - Barcelona" and c.market_key == "1x2"
    ]
    assert len(real) == 3 and all(c.p_model is not None for c in real)
    assert all(c.tier in {"experimental", "rejected"} for c in real)  # model not validated
    assert (
        flow.report.headline == NO_EDGE and "Brak potwierdzonej przewagi" in flow.report.diagnosis
    )
    saved = json.loads(store.get("analyses", flow.analysis_id)["summary_json"])
    assert [s["stage"] for s in saved["pipeline"]["stages"]] == list(services.STAGES)[:7]
    home = next(c for c in real if c.selection == "home")
    coupon = create_coupon(
        store,
        odds_ids=[home.odds_id],
        stake=Decimal("10.00"),
        mode="paper_manual",
        profile=load_profiles(app_paths.config)["sts"],
        now=NOW,
        analysis_id=flow.analysis_id,
    )
    payloads["PD"] = fd(
        recent_matches()
        + [
            match(
                99,
                "2026-10-31T20:00:00Z",
                "Real Madrid CF",
                "FC Barcelona",
                ft=(2, 1),
                ht=(1, 0),
                updated="2026-10-31T22:05:00Z",
            )
        ]
    )
    later = datetime(2026, 11, 2, 9, tzinfo=UTC)
    update = services.update_results_data(
        store, app_paths, settings, now=later, providers=[provider(payloads)]
    )
    assert update["inserted"] == 1 and update["sts_results"]["recorded"] == 1
    settled = settle_coupon(store, coupon, load_profiles(app_paths.config), now=later)
    assert settled["net_profit"] == "8.48"  # 10 × 0.88 × 2.10 − 10
    assert performance(store)["paper_manual"]["settled"] == 1
    text = export_json(store, app_paths.exports / "flow.json", now=later).read_text(
        encoding="utf-8"
    )
    assert '"source": "football_data_org"' in text and '"settlements"' in text


def test_without_current_data_the_flow_explains_why_there_is_no_coupon(app_paths, sts_fixture_pdf):
    from sports_edge import services
    from sports_edge.analysis.coupon import NO_EDGE
    from sports_edge.settings import AppSettings

    store = services.open_store(app_paths)
    settings = AppSettings(offline_mode=False)
    services.import_pdf_bytes(
        store, app_paths, settings, sts_fixture_pdf, file_name="fixture_flow.pdf", now=NOW
    )
    flow = services.run_daily_analysis(
        store, app_paths, settings, "2026-10-31", now=NOW, providers=[FootballDataOrgProvider(None)]
    )
    stages = {s["stage"]: s for s in flow.stages}
    assert stages["Dane sportowe"]["status"] == "dane nieaktualne"
    reason = stages["Dane sportowe"]["update"]["providers"]["football_data_org"]["reason"]
    assert "FOOTBALL_DATA_API_KEY" in reason
    assert stages["Modele"]["status"] == "brak modelu"
    assert flow.report.diagnosis[:2] == (
        "Brak aktualnych danych sportowych — prognozy wstrzymane (próg 21 dni)",
        "Brak prognoz modelu dla tej oferty",
    )
    assert flow.report.headline == NO_EDGE and not flow.report.solo
