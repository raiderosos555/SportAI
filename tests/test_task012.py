"""TASK 012 — models that learn: rolling windows (protocol v3), automatic forecasts, STS prices
closer to the start, the risk of the day from the score matrix.

Every team, price, forecast and result below is SYNTHETIC test data, not a real fact.
"""

import json
import math
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import ROOT, SRC
from test_models_data import load, synthetic
from test_task004 import synthetic_history
from test_task011 import NOW, POLICY, _event, _forecast, _import, _market, _quote, _version

from sports_edge import alerts, automation, insights, learning, rolling, services
from sports_edge import prospective_eval as pe
from sports_edge.collection import record_job
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.models.goals import markets, score_matrix
from sports_edge.results_engine import effective_results
from sports_edge.sportsdata import _scoped, _to_match, import_results_csv

FAR = datetime(2030, 1, 1, tzinfo=UTC)
SMALL = rolling.RollingPolicy(min_window_matches=60, min_fit_matches=60)


def _midnight(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC)


# 1 ---------------------------------------------------------------- protocol v3 windows


def test_v3_windows_move_with_the_training_date_and_respect_availability():
    matches = synthetic(1800)  # 2020-01-01 … 2024-12-05, results known 36 h after the day
    seen = [(m.match_date, m.available_at) for m in matches]
    early = rolling.windows_at(datetime(2024, 6, 1, tzinfo=UTC), seen, SMALL)
    later = rolling.windows_at(datetime(2024, 9, 15, tzinfo=UTC), seen, SMALL)
    assert (early.calibration_from, early.selection_from) == ("2022-06-01", "2023-06-01")
    assert (later.calibration_from, later.selection_from) == ("2022-09-15", "2023-09-15")
    assert early.rule == later.rule == "12 + 12 mies."
    assert early.as_dict()["protocol"] == "v3" and later.counts["selection"] > 300
    for windows in (early, later):
        ends = {
            "fit": _midnight(windows.calibration_from),
            "calibration": _midnight(windows.selection_from),
        }
        for m in matches:
            name = windows.window(m.match_date, m.available_at)
            if name in ends:
                assert m.available_at < ends[name]  # known before the end of its window
            elif name == "selection":
                assert m.available_at <= windows.trained_at
        # the match of the last calibration day is known only after the window ended:
        last = next(m for m in matches if m.match_date == "2023-05-31")
        assert early.window(last.match_date, last.available_at) is None
    # shorter history: half seasons; too little even then: an explicit error, no model
    short = [(m.match_date, m.available_at) for m in synthetic(500)]
    found = rolling.windows_at(datetime(2021, 5, 15, tzinfo=UTC), short, SMALL)
    assert found.rule.startswith("półsezony 6 + 6") and found.selection_from == "2020-11-15"
    with pytest.raises(ValueError, match="Za mało meczów w oknach protokołu v3"):
        rolling.windows_at(datetime(2021, 5, 15, tzinfo=UTC), short, rolling.RollingPolicy())


# 2 ---------------------------------------------------------------- v3 challenger vs v2 champion


@pytest.fixture(scope="module")
def learned(tmp_path_factory):
    """A v2 champion trained on 1 June 2025, then 17 months of new SYNTHETIC matches."""
    import os
    import shutil

    from sports_edge.settings import AppPaths

    home = tmp_path_factory.mktemp("task012")
    shutil.copytree(ROOT / "config", home / "config")
    previous = os.environ.get("SPORTS_EDGE_HOME")
    os.environ["SPORTS_EDGE_HOME"] = str(home)
    paths = AppPaths(home)
    store = services.open_store(paths)
    load(store, synthetic(2490))  # one match a day, 2020-01-01 … 2026-10-24
    trained = datetime(2025, 6, 1, tzinfo=UTC)
    v2 = services.run_protocol_v2(store, paths, now=trained, scope="core")
    events_before = {e["id"] for e in learning.events(store)}
    v3 = services.run_protocol_v3(store, paths, now=NOW, scope="core")
    yield {"store": store, "paths": paths, "v2": v2, "v3": v3, "events_before": events_before}
    store.close()
    if previous is None:
        os.environ.pop("SPORTS_EDGE_HOME", None)
    else:
        os.environ["SPORTS_EDGE_HOME"] = previous


def test_a_v3_challenger_differs_from_the_v2_champion_which_stays_served(learned):
    store, v2, v3 = learned["store"], learned["v2"], learned["v3"]
    core = services.CORE_COMPETITIONS
    assert v2["summary"]["protocol"] == "v2" and v3["summary"]["protocol"] == "v3"
    # the champion v2 stays served; the v3 versions wait as challengers (shadow mode)
    assert services.scope_version(store, core)["version_id"] == v2["elo"]["version_id"]
    goals_champion = services.scope_version(store, core, services.GOALS_FAMILY)
    assert goals_champion["version_id"] == v2["goals"]["version_id"]
    roles = {r["version_id"]: r["role"] for r in services.model_versions_of(store, "core")}
    assert roles[v3["elo"]["version_id"]] == roles[v3["goals"]["version_id"]] == "challenger"
    new = [e for e in learning.events(store) if e["id"] not in learned["events_before"]]
    assert {e["kind"] for e in new} == {"challenger_ready"}  # never an automatic activation
    # the windows moved with the training date: the retraining is not a copy of the champion
    split = v3["elo"]["split"]
    assert split["protocol"] == "v3" and split["selection_from"] == "2025-11-06"
    assert v3["goals"]["holdout"] == {} and "trybie cienia" in v3["goals"]["decision_gate"]
    changed = v3["differences"]["goals"]
    assert not changed["identical"] and any(not v["same"] for v in changed["calibrators"].values())
    assert v3["summary"]["differs_from_champion"]["goals"] is True
    # ... and it forecasts the NEW matches differently from the champion
    rows = effective_results(store, sport="football", until=FAR, competitions=("es.1",))
    prepared = rolling.prepare(rows)
    fresh = {r["id"] for r in rows if r["match_date"] >= "2026-06-01"}
    old_report = json.loads(goals_champion["validation_json"])
    new_report = v3["goals"]
    old = rolling.dc_forecasts(prepared, half_life=old_report["half_life_days"], wanted=fresh)
    mine = rolling.dc_forecasts(prepared, half_life=new_report["half_life_days"], wanted=fresh)
    gaps = [
        abs(
            pe_value(rolling.served_view(mine[k], new_report["calibrators"]))
            - pe_value(rolling.served_view(old[k], old_report["calibrators"]))
        )
        for k in fresh
        if k in old and k in mine
    ]
    assert gaps and max(gaps) > 1e-4


def pe_value(view: dict) -> float:
    return view[("total", 2.5)]["over"] + view[("1x2", None)]["home"]


def test_v3_refuses_the_same_data_and_never_trains_without_a_champion(learned, store, app_paths):
    with pytest.raises(ValueError, match="Brak nowych meczów"):
        services.run_protocol_v3(
            learned["store"], learned["paths"], now=NOW + timedelta(hours=1), scope="core"
        )
    load(store, synthetic(1800))
    with pytest.raises(ValueError, match="trenuje wyłącznie challengery"):
        services.run_protocol_v3(
            store, app_paths, now=datetime(2024, 12, 6, tzinfo=UTC), scope="core"
        )
    assert learning.events(store) == []  # nothing registered as a role, nothing activated


# 3 ---------------------------------------------------------------- walk-forward


def test_walk_forward_month_model_uses_only_results_available_before_the_month(store):
    load(store, synthetic(1800))
    rows = effective_results(store, sport="football", until=FAR, competitions=("es.1",))
    start = datetime(2024, 6, 1, tzinfo=UTC)
    policy = rolling.RollingPolicy(
        calibration_months=4, selection_months=4, min_window_matches=60, min_fit_matches=60
    )

    def model(found):
        return rolling.train_at(
            start,
            matches=_scoped([_to_match(r) for r in found]),
            prepared=rolling.prepare(found),
            rows_by_id={r["id"]: r for r in found},
            rolling=policy,
            cache={},
        )

    def flipped(row):
        return {**row, "home_score": row["away_score"] + 3, "away_score": row["home_score"]}

    base = model(rows)
    later = [flipped(r) if datetime.fromisoformat(r["available_at"]) > start else r for r in rows]
    same = model(later)  # every result known only after the start of the month changed
    assert same["elo"] == base["elo"] and same["half_life"] == base["half_life"]
    assert same["calibrators"] == base["calibrators"]
    # control: changing results known BEFORE the month changes the month's model
    inside = [flipped(r) if "2024-03-01" <= r["match_date"] < "2024-05-25" else r for r in rows]
    other = model(inside)
    assert other["elo"] != base["elo"] or other["calibrators"] != base["calibrators"]


def test_walk_forward_compares_v3_and_v2_on_the_same_matches(learned):
    store, paths = learned["store"], learned["paths"]
    found = services.run_walk_forward_v3(
        store, paths, now=datetime(2025, 9, 1, tzinfo=UTC), scope="core", first_month="2025-07"
    )
    months = {m["month"]: m for m in found["months"]}
    assert set(months) == {"2025-07", "2025-08"}
    for month, info in months.items():
        assert info["selection_from"] < f"{month}-01"  # trained before the month
    rows = found["rows"]
    assert rows and {r["month"] for r in rows} <= set(months)
    for family in (rolling.ELO_FAMILY, rolling.FAMILY):
        keys = {
            role: {
                (r["market"], r["match"])
                for r in rows
                if r["family"] == family and r["role"] == role
            }
            for role in ("v3", "v2")
        }
        assert keys["v3"] and keys["v3"] == keys["v2"]  # the same matches for both variants
    for row in found["comparison"]:
        assert row["matches"] > 0 and row["verdict"] in {
            "v3 lepszy",
            "v3 gorszy",
            "brak istotnej różnicy",
        }
        assert math.isclose(
            row["difference"], row["v3_log_loss"] - row["v2_log_loss"], abs_tol=0.05
        )
    assert found["zone"] and {z["role"] for z in found["zone"]} == {"v2", "v3"}
    assert "przed M" in found["leakage"]


# 4 ---------------------------------------------------------------- automatic forecasts


def _offer_world(app_paths, sts_fixture_pdf):
    from sports_edge.settings import AppSettings
    from sports_edge.sportsdata import Split, run_elo_experiment

    store = services.open_store(app_paths)
    settings = AppSettings()
    moment = datetime(2026, 10, 31, 11, 30, tzinfo=UTC)
    services.import_pdf_bytes(
        store, app_paths, settings, sts_fixture_pdf, file_name="fixture.pdf", now=moment
    )
    import_results_csv(
        store,
        synthetic_history(date(2026, 10, 25), 1800),
        file_name="synthetic.csv",
        source_name="SYNTHETIC FIXTURE",
        license_note="dane syntetyczne (test)",
        sport="football",
        competition="es.1",
        now=moment,
    )
    run_elo_experiment(
        store,
        competitions=("es.1",),
        split=Split("2024-09-01", "2025-09-01"),
        now=moment,
        policy=services.policy(app_paths),
    )
    return store, settings, moment


def test_forecasts_job_fills_the_offer_without_coupons_and_without_duplicates(
    app_paths, sts_fixture_pdf
):
    store, settings, moment = _offer_world(app_paths, sts_fixture_pdf)
    days = ["2026-10-31"]
    before = services.forecast_coverage(store, now=moment, days=days)
    assert before["events"] >= 1 and before["with_forecast"] == 0
    found = services.run_forecasts(store, app_paths, settings, now=moment, days=days)
    assert (
        found["saved"] >= 1 and found["with_forecast"] >= 1 and found["events"] == before["events"]
    )
    missing = " ".join(found["without_forecast"])
    assert "Real Madrid" not in missing  # the match of a scope with a model has its forecast
    assert "EKSTRAKLASA" in missing  # no model of that scope here: said plainly, not hidden
    counts = {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("predictions", "coupons")}
    assert counts["coupons"] == 0 and store.scalar("SELECT COUNT(*) FROM analyses") == 0
    again = services.run_forecasts(store, app_paths, settings, now=moment + timedelta(minutes=5))
    assert again["saved"] == 0 and again["kept"] >= 1  # same model and data: kept, not repeated
    assert store.scalar("SELECT COUNT(*) FROM predictions") == counts["predictions"]
    # the worker job: skipped offline (the default), and it never builds a coupon
    outcome = automation.job_forecasts(
        store, app_paths, now=moment, settings=automation.load_settings(store)
    )
    assert outcome.status == "skipped" and "offline" in outcome.reason
    assert store.scalar("SELECT COUNT(*) FROM coupons") == 0
    store.close()


def test_new_jobs_are_scheduled_and_the_forecasts_follow_a_new_sts_document(
    store, app_paths, monkeypatch
):
    from sports_edge.settings import AppSettings, save_settings

    settings = automation.load_settings(store)
    for job in ("forecasts", "calibration_report"):
        assert job in automation.JOBS and settings["jobs"][job]["enabled"]
        assert settings["jobs"][job]["every_minutes"] in automation.FREQUENCIES[job]
    assert settings["jobs"]["forecasts"]["morning"] == "07:30"
    now = datetime(2026, 11, 7, 9, tzinfo=UTC)  # 10:00 in Warsaw
    earlier = now - timedelta(minutes=1)
    record_job(
        store, "auto:forecasts", trigger="test", started=earlier, finished=earlier, status="ok"
    )
    # the fixed morning run is the fallback: tomorrow 07:30 Warsaw = 06:30 UTC
    assert automation.morning_next(store, settings, now=now) == datetime(
        2026, 11, 8, 6, 30, tzinfo=UTC
    )
    save_settings(app_paths, AppSettings(offline_mode=False, sts_download_enabled=True))
    calls = []
    monkeypatch.setattr(
        services,
        "refresh_offers",
        lambda s, p, a, days, *, now: [{"status": "new", "days": days}],
    )
    monkeypatch.setitem(
        automation.JOBS,
        "forecasts",
        lambda s, p, *, now, settings: calls.append(now) or automation.JobOutcome("ok", "", {}),
    )
    settings["jobs"]["sts_offer"]["enabled"] = True
    outcome = automation.job_sts_offer(store, app_paths, now=now, settings=settings)
    assert outcome.payload["new"] == 1 and outcome.payload["forecasts"] == "ok" and calls == [now]
    runs = store.rows("SELECT trigger FROM job_runs WHERE job='auto:forecasts' ORDER BY started_at")
    assert runs[-1]["trigger"] == "sts_offer"


# 5 ---------------------------------------------------------------- weekly report and alerts


def _flagged_world(store, matches: int = 30, hits: int = 18):
    goals = _version(store, pe.GOALS, "dc-weekly")
    for n in range(matches):
        start = NOW - timedelta(days=3, hours=n)
        event = _event(store, 100 + n, start, _import(store, f"w{n}", start - timedelta(hours=6)))
        _forecast(
            store,
            goals,
            event,
            "total",
            "1.5",
            {"over": 0.85, "under": 0.15},
            {"over": int(n < hits), "under": int(n >= hits)},
            start - timedelta(hours=5),
        )
    return goals


def test_weekly_report_records_history_and_alerts_once_per_fact(store):
    _flagged_world(store)
    first = pe.weekly_report(store, now=NOW, policy=POLICY)
    assert first["recorded"] and first["flags"] >= 1 and first["alerts"] == 1
    found = [a for a in alerts.recent(store) if a["kind"] == "calibration_flag"]
    assert len(found) == 1 and "zbyt optymistyczna" in found[0]["message"]
    assert "Gole 1,5" in found[0]["message"] and "modele i progi bez zmian" in found[0]["message"]
    assert "średnia szansa 85,0%, trafność 60,0%" in found[0]["message"]  # Polish decimals
    entry = pe.latest_weekly_report(store)
    assert entry["flags"] == first["flags"] and entry["payload"]["flags"]
    again = pe.weekly_report(store, now=NOW + timedelta(days=7), policy=POLICY)
    assert again["alerts"] == 0 and not again["recorded"]  # the same fact, the same data
    assert store.scalar("SELECT COUNT(*) FROM prematch_alerts") == 1
    assert store.scalar("SELECT COUNT(*) FROM calibration_reports") == 1
    assert store.scalar("SELECT COUNT(*) FROM model_events") == 0  # nothing in the models


# 6 ---------------------------------------------------------------- match-day downloads


def _matchday_world(store, first_kickoffs):
    imported = _import(store, "matchday", datetime(2026, 11, 7, 6, tzinfo=UTC))
    for n, start in enumerate(first_kickoffs):
        _event(store, 500 + n, start, imported)


def _run(store, at: datetime, *, matchday: bool) -> None:
    record_job(
        store,
        "auto:sts_offer",
        trigger="schedule",
        started=at,
        finished=at,
        status="ok",
        payload={"matchday": matchday},
    )


def test_matchday_plan_follows_kickoffs_and_respects_the_limits(store, app_paths, monkeypatch):
    from sports_edge.settings import AppSettings, save_settings

    utc = lambda h, m: datetime(2026, 11, 7, h, m, tzinfo=UTC)  # noqa: E731
    # first kick-offs of the hours 13:00 and 14:00 in Warsaw (+ one more in the 13:00 hour)
    _matchday_world(store, [utc(12, 0), utc(12, 40), utc(13, 10)])
    settings = automation.load_settings(store)
    settings["jobs"]["sts_offer"]["enabled"] = True
    plan = automation.matchday_plan(store, settings, now=utc(8, 0))
    assert [(s["at"], s["lead"]) for s in plan] == [
        (utc(10, 0), 120),
        (utc(11, 15), 45),  # 11:10 (120 min before 13:10) and 11:15 are one download
        (utc(12, 25), 45),
    ]
    assert all(
        b["at"] - a["at"] >= timedelta(minutes=10) for a, b in zip(plan, plan[1:], strict=False)
    )
    assert plan[1]["group"] == utc(12, 0)  # still before the earlier group
    # no consent: nothing is ever downloaded on match days
    assert automation.matchday_next(store, settings, now=utc(9, 0), allowed=False) is None
    assert automation.matchday_next(store, settings, now=utc(9, 0)) is None  # offline default
    off = json.loads(json.dumps(settings))
    off["jobs"]["sts_offer"]["enabled"] = False
    assert automation.matchday_next(store, off, now=utc(9, 0), allowed=True) is None
    # with the user's consent: the next slot drives the schedule of sts_offer
    save_settings(app_paths, AppSettings(offline_mode=False, sts_download_enabled=True))
    _run(store, utc(8, 0), matchday=False)  # regular run: next one at 14:00
    assert automation.next_run(store, "sts_offer", settings, now=utc(9, 0)) == utc(10, 0)
    _run(store, utc(10, 1), matchday=True)
    assert automation.matchday_next(store, settings, now=utc(10, 5))["at"] == utc(11, 15)
    _run(store, utc(11, 8), matchday=False)  # a run 7 min before a slot covers it (10 min/file)
    assert automation.matchday_next(store, settings, now=utc(11, 20))["at"] == utc(12, 25)
    late = automation.matchday_next(store, settings, now=utc(12, 30))
    assert late["at"] == utc(12, 25) and late["group"] == utc(13, 10)  # 12:00 group started
    # the panel: a later download never marks an earlier slot as done
    states = {
        s["at"]: s["status"] for s in automation.matchday_status(store, settings, now=utc(12, 30))
    }
    assert states == {utc(10, 0): "wykonane", utc(11, 15): "wykonane", utc(12, 25): "zaległe"}
    early = automation.matchday_status(store, settings, now=utc(9, 0))
    assert [s["status"] for s in early] == ["oczekuje", "oczekuje", "oczekuje"]
    # the daily cap
    capped = json.loads(json.dumps(settings))
    capped["jobs"]["sts_offer"]["matchday"]["daily_cap"] = 1
    assert automation.matchday_next(store, capped, now=utc(12, 30)) is None
    # a due slot fetches TODAY's offer only, and says so in the job history
    seen = []
    monkeypatch.setattr(
        services,
        "refresh_offers",
        lambda s, p, a, days, *, now: seen.append(days) or [{"status": "duplicate"}],
    )
    outcome = automation.job_sts_offer(store, app_paths, now=utc(12, 26), settings=settings)
    assert seen == [["2026-11-07"]] and outcome.payload["matchday"] is True
    assert outcome.payload["lead_minutes"] == 45
    save_settings(app_paths, AppSettings(offline_mode=False, sts_download_enabled=False))
    blocked = automation.job_sts_offer(store, app_paths, now=utc(12, 27), settings=settings)
    assert blocked.status == "skipped" and len(seen) == 1


# 7 ---------------------------------------------------------------- age of the STS price


def test_market_benchmark_records_the_document_age_in_three_bands(store):
    elo = _version(store, pe.ELO, "elo-age")
    for n, lead in enumerate((2.0, 6.0, 15.0)):
        start = NOW - timedelta(days=1, hours=n)
        doc = _import(store, f"age{n}", start - timedelta(hours=lead))
        event = _event(store, 700 + n, start, doc)
        market, selections = _market(store, event, "1x2", None, ("home", "draw", "away"))
        _quote(
            store,
            market,
            selections,
            doc,
            start - timedelta(hours=lead),
            {"home": "2.00", "draw": "3.40", "away": "3.80"},
        )
        _forecast(
            store,
            elo,
            event,
            "1x2",
            None,
            {"home": 0.5, "draw": 0.27, "away": 0.23},
            {"home": 1, "draw": 0, "away": 0},
            start - timedelta(hours=lead + 1),
        )
    row = next(
        r
        for r in pe.market_comparison(store, policy=POLICY)
        if r["scope"] == "all" and r["family"] == pe.ELO and r["market"] == "1x2"
    )
    age = row["document_age_hours"]
    assert age["matches"] == 3 and age["median"] == 6.0
    assert age["bands"] == {"≤ 3 h": 0.3333, "3–12 h": 0.3333, "> 12 h": 0.3333}
    assert {k: v["matches"] for k, v in row["by_document_age"].items()} == {
        "≤ 3 h": 1,
        "3–12 h": 1,
        "> 12 h": 1,
    }
    assert row["by_document_age"]["≤ 3 h"]["state_label"] == "za mało (1 mecz < 300)"
    pe.record_market_benchmarks(store, now=NOW, policy=POLICY)
    stored = pe.latest_benchmarks(store)[("all", pe.ELO, "1x2")]["payload"]
    assert stored["document_age_hours"]["median"] == 6.0 and "by_document_age" in stored
    assert pe.age_bands((3.0, 12.0))[0][0] == "≤ 3 h"


# 8 ---------------------------------------------------------------- risk of the day


def _leg(event, market_key, line, selection, q, version="dc-v"):
    return {
        "event_id": event,
        "event": f"Mecz {event}",
        "market_key": market_key,
        "line": line,
        "selection": selection,
        "p_conservative": q,
        "model_version_id": version,
    }


RATES = {"lambda": 1.5, "mu": 1.1, "rho": -0.05}


def test_risk_of_the_day_reads_two_selections_of_one_match_from_the_score_matrix():
    matrix = score_matrix(RATES["lambda"], RATES["mu"], RATES["rho"])
    raw = markets(matrix)
    p_over, p_home = raw[("total", 1.5)]["over"], raw[("home_to_score", None)]["yes"]
    proposals = {
        "a": {"label": "A", "legs": [_leg("m1", "total", "1.5", "over", p_over)]},
        "b": {"label": "B", "legs": [_leg("m1", "home_to_score", None, "yes", p_home)]},
    }
    found = insights.day_risk(proposals, {("m1", "dc-v"): RATES})
    joint = float(matrix[1:, :].sum() - matrix[1, 0])  # home scores AND 2+ goals
    assert found["joint"]["m1"]["method"] == "matrix" and found["matrix_matches"] == 1
    assert math.isclose(found["p_all_win"], joint, rel_tol=1e-9)
    assert not math.isclose(joint, p_over * p_home, rel_tol=1e-3)  # not the product
    assert math.isclose(found["joint"]["m1"]["product"], p_over * p_home)
    # conservative chances shrink the joint chance exactly like the single chances
    cautious = {
        k: {**v, "legs": [{**v["legs"][0], "p_conservative": v["legs"][0]["p_conservative"] * 0.9}]}
        for k, v in proposals.items()
    }
    shrunk = insights.day_risk(cautious, {("m1", "dc-v"): RATES})
    assert math.isclose(shrunk["p_all_win"], joint * 0.81, rel_tol=1e-9)
    # fallback: no saved parameters (old forecast) or a market outside the matrix (Elo 1X2)
    old = insights.day_risk(proposals)
    assert old["joint"]["m1"]["method"] == "independent" and old["independent_matches"] == 1
    assert "bez zapisanych parametrów" in old["joint"]["m1"]["reason"]
    assert math.isclose(old["p_all_win"], p_over * p_home)
    elo = {
        "a": proposals["a"],
        "b": {"legs": [_leg("m1", "1x2", None, "home", 0.5, version="elo-v")]},
    }
    mixed = insights.day_risk(elo, {("m1", "dc-v"): RATES})
    assert mixed["joint"]["m1"]["method"] == "independent"
    assert mixed["joint"]["m1"]["reason"] == "typy z różnych modeli"


def test_old_legs_without_an_event_id_are_still_different_matches():
    """Legs saved before the event id was stored: two "over 1.5" of two matches are two
    selections (TASK 011 counted them as one)."""

    def old(name, q):
        return {
            "event": name,
            "start_at": "2026-11-02T19:00:00+00:00",
            "market_key": "total",
            "line": "1.5",
            "selection": "over",
            "p_conservative": q,
        }

    proposals = {"a": {"legs": [old("A - B", 0.8)]}, "b": {"legs": [old("C - D", 0.7)]}}
    found = insights.day_risk(proposals)
    assert found["selections"] == 2 and found["same_match_pairs"] == 0 and found["joint"] == {}
    assert math.isclose(found["p_all_win"], 0.56)
    assert insights.matrix_rates(None, proposals) == {}  # no event id: nothing to look up


def test_kupon_dnia_names_the_method_of_the_risk_of_the_day(store):
    from sports_edge.app.page_coupon import risk_numbers

    proposals = {
        "a": {"legs": [_leg("m1", "total", "1.5", "over", 0.7)]},
        "b": {
            "legs": [_leg("m1", "btts", None, "yes", 0.55), _leg("m2", "1x2", None, "home", 0.8)]
        },
    }
    text = " ".join(risk_numbers(proposals, {("m1", "dc-v"): RATES}))
    assert "z macierzy wyników Dixon–Coles" in text and "metoda dla typów jednego meczu" in text
    old = " ".join(risk_numbers(proposals))
    assert "założenie niezależności" in old and "bez zapisanych parametrów macierzy" in old
    # the parameters saved with a NEW forecast are found again for the coupon legs
    version = _version(store, pe.GOALS, "dc-v")
    start = NOW + timedelta(hours=6)
    event = _event(store, 900, start, _import(store, "risk", NOW))
    store.insert(
        "predictions",
        id=digest(["risk-prediction"]),
        model_version_id=version,
        event_id=event,
        market_key="total",
        period="FT",
        line="1.5",
        created_at=iso(NOW),
        probabilities_json=canonical({"over": 0.7, "under": 0.3}),
        uncertainty_json="{}",
        inputs_json=canonical({"score_matrix": RATES, "competition": "en.1"}),
        kind="prospective",
    )
    legs = {
        "a": {"legs": [_leg(event, "total", "1.5", "over", 0.7, version=version)]},
        "b": {"legs": [_leg(event, "btts", None, "yes", 0.55, version=version)]},
    }
    assert insights.matrix_rates(store, legs) == {(event, version): RATES}


def test_new_goals_forecasts_carry_the_score_matrix_parameters():
    """predict_offer_goals writes λ, μ, ρ into the inputs of NEW rows only."""
    from sports_edge import probability_lab

    source = (SRC / "sports_edge" / "probability_lab.py").read_text(encoding="utf-8")
    assert '"score_matrix": rates' in source and probability_lab.MAX_GOALS == 10


# 9 ---------------------------------------------------------------- migration v9 → v10


def test_migration_to_v10_backs_up_and_keeps_every_row(tmp_path):
    from sports_edge.repository import ensure_reference_data
    from sports_edge.storage import SCHEMA_VERSION, Store

    path = tmp_path / "old.db"
    store = Store(path)
    ensure_reference_data(store)
    _flagged_world(store, matches=3, hits=2)
    store.close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE calibration_reports")
    raw.execute("PRAGMA user_version=9")
    tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {t: raw.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    raw.commit()
    raw.close()
    migrated = Store(path, backup_dir=tmp_path / "backups")
    assert SCHEMA_VERSION >= 10  # TASK 015: v11 adds user_decisions on top of v10
    assert migrated.migration_backup.name.startswith(f"pre-migration-v{SCHEMA_VERSION}_")
    assert migrated.migration_backup.exists()
    for table, n in counts.items():
        if table != "schema_migrations":
            assert migrated.scalar(f"SELECT COUNT(*) FROM {table}") == n, table
    assert "calibration_reports" in migrated.tables
    query = "SELECT name FROM sqlite_master WHERE type='trigger'"
    triggers = {r[0] for r in migrated.connection.execute(query)}
    assert {
        "immutable_calibration_reports_UPDATE",
        "immutable_calibration_reports_DELETE",
    } <= triggers
    note = migrated.rows(
        "SELECT description FROM schema_migrations WHERE version=?", (SCHEMA_VERSION,)
    )
    assert note and "v10 TASK 012" in note[-1]["description"]
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        migrated.connection.execute("DELETE FROM predictions")
    migrated.close()


# 10 --------------------------------------------------------------- tests import their own code


def test_conftest_imports_sports_edge_from_this_checkout():
    import os

    import sports_edge

    assert sports_edge.__file__.startswith(str(SRC))
    assert os.environ["PYTHONPATH"].split(os.pathsep)[0] == str(SRC)
    assert str(SRC) in sys.path


# 11 --------------------------------------------------------------- interface


def test_automation_panel_shows_the_new_jobs_and_the_matchday_mode(app_paths):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    services.open_store(app_paths).close()
    app = AppTest.from_string(
        "from sports_edge.app.page_automation import automation_page\nautomation_page()\n",
        default_timeout=120,
    )
    app.session_state["demo_mode"] = False
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    labels = {t.label for t in app.toggle}
    assert automation.JOB_LABELS["forecasts"] in labels
    assert automation.JOB_LABELS["calibration_report"] in labels
    text = " ".join(m.value for m in app.markdown) + " ".join(c.value for c in app.caption)
    assert "Kursy STS w dniu meczu" in text and "brak zgody na pobieranie oferty STS" in text
    assert "Tryb w dniu meczu" in labels
