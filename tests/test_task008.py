"""TASK 008: P0 fixes, pre-match intelligence, automation worker and coupon reassessment.

Every team, player, lineup and absence below is SYNTHETIC test data, not a real fact.
"""

from datetime import UTC, datetime, timedelta

import pytest
from test_models_data import csv_bytes, synthetic

from sports_edge import collection, retraining
from sports_edge import forecast_quality as fq
from sports_edge.sportsdata import import_results

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


# ---------------------------------------------------------------- P0 after TASK 007


def test_a_review_that_skipped_training_counts_as_done(store):
    assert retraining.due(store, now=NOW)
    collection.record_job(
        store,
        "retraining_review",
        trigger="automation",
        started=NOW,
        finished=NOW,
        status="skipped",
        reason="za mało danych do treningu",
    )
    assert not retraining.due(store, now=NOW + timedelta(days=1))  # no repeat on refresh
    assert retraining.due(store, now=NOW + timedelta(days=7))


def test_new_matches_count_each_match_once_however_many_sources_confirm_it(store):
    matches = synthetic(40, start=datetime(2026, 9, 1).date())
    rows = [
        {
            "match_date": m.match_date,
            "home": m.home,
            "away": m.away,
            "home_score": m.home_score,
            "away_score": m.away_score,
            "available_at": m.available_at,
        }
        for m in matches
    ]
    for provider in ("football_data_org", "pitchapi"):  # two sources confirm every match
        import_results(
            store,
            rows,
            file_bytes=csv_bytes(matches) + provider.encode(),
            file_name=f"{provider}.json",
            source_name=provider,
            license_note="dane syntetyczne",
            sport="football",
            competition="es.1",
            season=None,
            now=NOW,
            availability_basis="source_timestamp",
            provider=provider,
        )
    assert store.scalar("SELECT COUNT(*) FROM historical_results") == 80
    assert retraining.new_matches(store, ("es.1",), since=None, now=NOW) == 40


def _row(match, market, selection, p, y, line=None):
    return {
        "event": f"{match}-{market}-{line}",
        "match": match,
        "group": fq.market_group(market),
        "p": p,
        "y": y,
        "market": market,
        "line": line,
        "selection": selection,
        "family": market,
        "competition": "es.1",
        "scope": "core",
        "version": "v",
        "start_at": "2026-10-31T19:00:00+00:00",
    }


def test_metrics_are_reported_per_market_with_independent_matches():
    rows = []
    for i in range(40):
        rows += [
            _row(f"m{i}", "1x2", "home", 0.5, int(i % 2 == 0)),
            _row(f"m{i}", "1x2", "draw", 0.3, int(i % 4 == 1)),
            _row(f"m{i}", "1x2", "away", 0.2, int(i % 4 == 3)),
            _row(f"m{i}", "total", "over", 0.8, int(i % 5 != 0), "1.5"),
            _row(f"m{i}", "total", "under", 0.2, int(i % 5 == 0), "1.5"),
        ]
    found = fq.by_market(rows)
    assert set(found) == {"1X2", "Over/Under", "Remisy"}
    assert found["1X2"]["matches"] == 40 and found["Over/Under"]["matches"] == 40
    assert found["1X2"]["accuracy"] == 0.5  # home picked every time, home won 20 of 40
    assert found["Over/Under"]["accuracy"] == 0.8  # a different, easier question
    assert found["Remisy"]["predicted"] == 0.3 and found["Remisy"]["observed"] == 0.25
    mixed = fq.metrics(rows)
    assert mixed["matches"] == 40 and mixed["events"] == 80  # 2 forecasts per match


def test_coupons_are_split_into_solo_ako_and_profiles():
    def coupon(kind, profile, outcome):
        return {
            "kind": kind,
            "profile": profile,
            "proposal": {"label": profile, "probability": {"calibrated": 0.6}},
            "outcome": outcome,
            "stake": "10.00",
            "net_profit": 7 if outcome == "win" else -10,
        }

    split = fq.coupon_split(
        [coupon("solo", "A", "win"), coupon("ako", "A", "loss"), coupon("ako", "B", None)]
    )
    by_group = {r["grupa"]: r for r in split}
    assert by_group["rodzaj: SOLO"]["trafność"] == 1.0
    assert by_group["rodzaj: AKO"]["rozliczone"] == 1 and by_group["rodzaj: AKO"]["kupony"] == 2
    assert by_group["profil: A"]["roi"] == pytest.approx(-0.15)


# ---------------------------------------------------------------- lineups and absences

T0 = datetime(2026, 10, 31, 12, 40, tzinfo=UTC)  # the fixture offer arrived at 12:30


def _side(players, *, confirmed, formation="4-4-2", goalkeeper=("p_gk1", "Bramkarz Jeden")):
    starters = [
        {
            "player_id": goalkeeper[0],
            "name": goalkeeper[1],
            "shirt_number": "1",
            "position_id": 11,
            "is_captain": False,
        }
    ]
    starters += [
        {
            "player_id": pid,
            "name": name,
            "shirt_number": str(i + 2),
            "position_id": 21 + i,
            "is_captain": i == 0,
        }
        for i, (pid, name) in enumerate(players)
    ]
    subs = [
        {
            "player_id": "p_sub1",
            "name": "Rezerwowy Jeden",
            "shirt_number": "30",
            "position_id": 2,
            "is_captain": False,
        }
    ]
    return {
        "formation": formation,
        "confirmed": confirmed,
        "coach": {"name": "Trener Testowy"},
        "starters": starters,
        "subs": subs,
    }


TEN = [(f"p_{i}", f"Zawodnik {i}") for i in range(10)]


def _payload(
    home=TEN, *, confirmed=False, formation="4-4-2", goalkeeper=("p_gk1", "Bramkarz Jeden")
):
    return {
        "data": {
            "home": _side(home, confirmed=confirmed, formation=formation, goalkeeper=goalkeeper),
            "away": _side(TEN, confirmed=confirmed),
            "home_team": {"name": "Legia Warszawa"},
            "away_team": {"name": "Lech Poznań"},
        }
    }


def _event(store):
    return store.rows("SELECT * FROM events WHERE home='Legia Warszawa'")[0]


def _check(store, event, payload, when, checkpoint="T-6h", match_id="m_test"):
    from sports_edge import prematch

    return prematch.check_event(
        store,
        event,
        fetch_lineup=lambda e: (match_id, payload, 1),
        absence_provider=prematch.NoVerifiedAbsenceSource(),
        checkpoint=checkpoint,
        now=when,
    )


def test_predicted_then_confirmed_lineup_with_changes_and_alerts(store, import_sts):
    from sports_edge import alerts, prematch

    event = _event(store)
    first = _check(store, event, _payload(), T0)
    assert first.lineup_status == "predicted" and first.changes == []
    side = prematch.latest_side(store, event["id"], "home")
    assert side["status"] == "predicted" and side["known_before_start"] == 1
    assert prematch.goalkeeper(side)["name"] == "Bramkarz Jeden"
    swapped = [("p_new", "Nowy Napastnik"), *TEN[1:]]
    second = _check(
        store,
        event,
        _payload(swapped, confirmed=True, formation="4-3-3", goalkeeper=("p_gk2", "Bramkarz Dwa")),
        T0 + timedelta(hours=1),
        checkpoint="T-90min",
    )
    kinds = {c["kind"] for c in second.changes}
    assert {
        "confirmed_published",
        "goalkeeper_change",
        "new_starter",
        "removed_starter",
        "formation_change",
    } <= kinds
    assert second.lineup_status == "confirmed"
    alerts.from_check(store, event, second, now=T0 + timedelta(hours=1))
    assert alerts.from_check(store, event, second, now=T0 + timedelta(hours=2)) == 0  # no repeat
    # TASK 016 §4.1: the lineup changes of one match and check are ONE alert with the list
    # of changes; it keeps the highest priority of its changes (the goalkeeper: high)
    high = [a for a in alerts.recent(store) if a["kind"] == "lineup_changes"]
    assert len(high) == 1 and high[0]["priority"] == "high"
    assert "goalkeeper_change" in {c["kind"] for c in high[0]["payload"]["changes"]}


def test_no_lineup_no_data_incomplete_and_duplicates(store, import_sts):
    from sports_edge import prematch

    event = _event(store)
    assert _check(store, event, None, T0, match_id="m_test").lineup_status == "not_published"
    assert _check(store, event, None, T0, checkpoint="T-24h", match_id=None).lineup_status == (
        "no_data"
    )
    short = prematch.parse_side(_side(TEN[:9], confirmed=False))
    assert not short["complete"] and len(short["starters"]) == 10
    _check(store, event, _payload(), T0 + timedelta(minutes=5), checkpoint="T-90min")
    _check(store, event, _payload(), T0 + timedelta(minutes=10), checkpoint="T-60min")
    rows = store.scalar("SELECT COUNT(*) FROM lineup_observations WHERE event_id=?", (event["id"],))
    assert rows == 2  # identical lineups are not stored twice (one row per side)


def test_moved_to_bench_and_correction_keeps_history(store, import_sts):
    from sports_edge import prematch

    event = _event(store)
    _check(store, event, _payload(), T0)
    benched = [*TEN[:9], ("p_x", "Inny Zawodnik")]
    payload = _payload(benched)
    payload["data"]["home"]["subs"].append(
        {
            "player_id": "p_9",
            "name": "Zawodnik 9",
            "shirt_number": "11",
            "position_id": 3,
            "is_captain": False,
        }
    )
    result = _check(store, event, payload, T0 + timedelta(hours=1), checkpoint="T-90min")
    assert any(
        c["kind"] == "moved_to_bench" and c["player"] == "Zawodnik 9" for c in result.changes
    )
    latest = prematch.latest_side(store, event["id"], "home")
    assert latest["previous_id"] is not None  # the correction points to the earlier version


def test_no_leakage_started_match_is_not_checked_and_as_of_reads(store, import_sts):
    from sports_edge import prematch

    event = _event(store)
    start = datetime.fromisoformat(event["start_at"])
    _check(store, event, _payload(), T0)
    late = _check(
        store, event, _payload(confirmed=True), start + timedelta(minutes=5), checkpoint="T-15min"
    )
    assert late.status == "skipped"  # nothing is fetched after the kick-off
    assert (
        prematch.latest_side(store, event["id"], "home", before=start.isoformat())["status"]
        == "predicted"
    )


def test_absences_are_matched_safely_and_never_invented(store, import_sts):
    from sports_edge import prematch
    from sports_edge.prematch import AbsenceRecord

    event = _event(store)
    _check(store, event, _payload(), T0)
    nothing = prematch.NoVerifiedAbsenceSource().fetch(event, now=T0)
    assert nothing.status == "no_source" and nothing.records == []

    def record(name, reason, source="klub (oficjalny komunikat)", team="Legia Warszawa", pid=None):
        return prematch.record_absence(
            store,
            event,
            AbsenceRecord(
                team=team,
                player=name,
                reason=reason,
                source=source,
                observed_at=T0.isoformat(),
                player_id=pid,
            ),
            entered_by="user",
            now=T0,
        )

    assert record("Zawodnik 3", "injury_confirmed")[1] == "verified"
    assert record("Zawodnik 33", "suspension")[1] == "needs_review"  # no such player
    assert record("Zawodnik 4", "doubtful")[1] == "verified"
    assert record("Zawodnik 5", "return")[1] == "verified"
    # a namesake in the other team (or after a transfer) is never matched to this team
    assert record("Zawodnik 6", "injury_reported", team="Lech Poznań")[1] == "verified"
    assert record("Zawodnik 6", "injury_reported", team="Inny Klub")[1] == "needs_review"
    record("Zawodnik 3", "doubtful", source="portal sportowy")
    conflicts = prematch.conflicts(store, event["id"])
    assert conflicts and conflicts[0]["player"] == "Zawodnik 3"
    first, _ = record("Zawodnik 4", "doubtful")
    again, _ = record("Zawodnik 4", "doubtful")
    assert first == again  # the same report is stored once
    assert prematch.importance(store, "Legia Warszawa", "p_3") == "niewystarczające dane"


def test_due_checkpoints_run_only_the_latest_and_record_missed(store, import_sts):
    from sports_edge import prematch

    event = _event(store)
    start = datetime.fromisoformat(event["start_at"])
    due, missed = prematch.due_checkpoints(store, event, now=start - timedelta(minutes=50))
    assert due[0] == "T-60min"
    assert [name for name, _ in missed] == ["T-48h", "T-24h", "T-6h", "T-90min"]
    assert prematch.due_checkpoints(store, event, now=start + timedelta(minutes=1)) == (None, [])


def test_coupon_is_reassessed_after_a_lineup_change(store, import_sts, profiles):
    from decimal import Decimal

    from sports_edge import reassessment
    from sports_edge.paper import create_coupon

    event = _event(store)
    leg = store.rows(
        """SELECT o.id FROM odds o JOIN selections s ON s.id=o.selection_id
        JOIN markets m ON m.id=s.market_id WHERE m.event_id=? AND m.market_key='1x2'
        AND s.key='home'""",
        (event["id"],),
    )[0]["id"]
    coupon_id = create_coupon(
        store,
        odds_ids=[leg],
        stake=Decimal("10.00"),
        mode="real_manual",
        profile=profiles["sts"],
        now=T0,
        note="kupon testowy",
    )
    first = reassessment.reassess_all(store, now=T0 + timedelta(minutes=1))
    assert first["coupons"] == 1 and first["bez zmian"] == 1
    _check(store, event, _payload(), T0 + timedelta(minutes=2))
    _check(
        store,
        event,
        _payload(confirmed=True, goalkeeper=("p_gk2", "Bramkarz Dwa")),
        T0 + timedelta(hours=1),
        checkpoint="T-90min",
    )
    later = reassessment.reassess_all(store, now=T0 + timedelta(hours=1, minutes=1))
    assert later["wymaga uwagi"] == 1
    found = reassessment.latest(store, coupon_id)
    assert any("bramkarz" in r for r in found["reasons"]) and found["previous_result"] == (
        "bez zmian"
    )
    assert store.get("coupons", coupon_id)["note"] == "kupon testowy"  # the coupon is untouched
    again = reassessment.reassess_all(store, now=T0 + timedelta(hours=1, minutes=2))
    assert again["wymaga uwagi"] == 1
    assert store.scalar("SELECT COUNT(*) FROM coupon_reassessments") == 2  # no duplicate


# ---------------------------------------------------------------- automation worker


class _Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


def _fake_runners(calls, fail=()):
    from sports_edge.automation import JOBS, JobOutcome

    def make(job):
        def run(store, paths, *, now, settings):
            calls.append(job)
            if job in fail:
                raise ConnectionError("źródło niedostępne")
            return JobOutcome("ok", "", {"processed": 1})

        return run

    return {job: make(job) for job in [*JOBS, "lineups_coupons"]}


def test_settings_and_commands_are_persistent(app_paths):
    from sports_edge import automation, services

    store = services.open_store(app_paths)
    assert automation.desired_state(store) == "stopped"
    settings = automation.load_settings(store)
    assert settings["auto_resume"] is False and settings["jobs"]["training"]["enabled"] is False
    settings["auto_resume"] = True
    settings["jobs"]["results"]["every_minutes"] = 60
    automation.save_settings(store, settings, now=NOW)
    again = automation.load_settings(services.open_store(app_paths))  # a new connection
    assert again["auto_resume"] is True and again["jobs"]["results"]["every_minutes"] == 60
    for name, state in (
        ("start", "running"),
        ("pause", "paused"),
        ("resume", "running"),
        ("stop", "stopped"),
    ):
        automation.command(store, name, now=NOW)
        assert automation.desired_state(store) == state


def test_worker_runs_due_jobs_once_and_records_history(app_paths):
    from sports_edge import automation, services

    store = services.open_store(app_paths)
    calls = []
    clock = _Clock(NOW)
    worker = automation.Worker(app_paths, clock=clock, runners=_fake_runners(calls))
    assert worker.run(max_ticks=1, sleep=lambda s: None) == "zatrzymany"
    enabled = [
        j
        for j, cfg in automation.DEFAULTS["jobs"].items()
        if cfg.get("enabled") and cfg.get("every_minutes") and j in automation.JOBS
    ]
    assert sorted(calls) == sorted(enabled)  # sts_offer is off by default
    history = automation.history(store)
    assert {h["job"] for h in history} == {f"auto:{j}" for j in enabled}
    runtime = automation.read_runtime(app_paths)
    assert runtime["state"] == "stopped" and runtime["next_runs"]
    # restart one minute later: nothing is due again (no duplicate jobs)
    calls.clear()
    clock.now = NOW + timedelta(minutes=1)
    automation.Worker(app_paths, clock=clock, runners=_fake_runners(calls)).run(
        max_ticks=1, sleep=lambda s: None
    )
    assert calls == []
    # after a long break every overdue job runs ONCE (bounded catch-up)
    clock.now = NOW + timedelta(days=3)
    automation.Worker(app_paths, clock=clock, runners=_fake_runners(calls)).run(
        max_ticks=1, sleep=lambda s: None
    )
    within = [j for j in enabled if automation.DEFAULTS["jobs"][j]["every_minutes"] <= 3 * 1440]
    assert sorted(calls) == sorted(within)  # the weekly review is not due yet
    assert automation.next_run(
        store, "results", automation.load_settings(store), now=clock.now
    ) == clock.now + timedelta(minutes=180)


def test_pause_resume_stop_run_now_and_check_coupons(app_paths):
    from sports_edge import automation, services

    store = services.open_store(app_paths)
    calls = []
    clock = _Clock(NOW)
    worker = automation.Worker(app_paths, clock=clock, runners=_fake_runners(calls))
    automation.command(store, "pause", now=NOW)
    worker.lock.acquire()
    assert worker.tick(store) is True and calls == [] and worker.state == "paused"
    automation.command(store, "run_now", now=NOW + timedelta(seconds=1))
    worker.tick(store)
    assert set(calls) >= {"results", "lineups", "coupon_reassessment"}  # run now ignores pause
    calls.clear()
    automation.command(store, "check_coupons", now=NOW + timedelta(seconds=2))
    worker.tick(store)
    assert calls == ["lineups_coupons", "coupon_reassessment"]
    automation.command(store, "resume", now=NOW + timedelta(seconds=3))
    worker.tick(store)
    assert worker.state == "running"
    automation.command(store, "stop", now=NOW + timedelta(seconds=4))
    assert worker.tick(store) is False  # controlled stop after the current operation
    worker.lock.release()
    assert automation.pending_commands(store) == []  # every command processed once


def test_failures_back_off_and_errors_are_recorded_without_tracebacks(app_paths):
    from sports_edge import automation, services

    store = services.open_store(app_paths)
    calls = []
    clock = _Clock(NOW)
    automation.Worker(app_paths, clock=clock, runners=_fake_runners(calls, fail=("results",))).run(
        max_ticks=1, sleep=lambda s: None
    )
    [failed] = [h for h in automation.history(store) if h["job"] == "auto:results"]
    assert failed["status"] == "failed" and failed["reason"].startswith("ConnectionError")
    assert "Traceback" not in failed["reason"]
    settings = automation.load_settings(store)
    assert automation.next_run(store, "results", settings, now=NOW) == datetime.fromisoformat(
        failed["finished_at"]
    ) + timedelta(minutes=5)  # backoff, not a hammering retry


def test_one_worker_only_stale_heartbeat_and_server_gone(app_paths):
    from sports_edge import automation, services

    store = services.open_store(app_paths)
    first = automation.Worker(app_paths, runners=_fake_runners([]))
    assert first.lock.acquire()
    second = automation.Worker(app_paths, runners=_fake_runners([]))
    assert second.run(max_ticks=1, sleep=lambda s: None) == "inny worker już działa"
    first.lock.release()
    automation.command(store, "start", now=NOW)
    automation.write_runtime(
        app_paths, {"state": "running", "heartbeat_at": (NOW - timedelta(minutes=5)).isoformat()}
    )
    found = automation.status(app_paths, store, now=NOW)
    assert not found["alive"] and found["label"].startswith("błąd")  # never a false "active"
    gone = automation.Worker(app_paths, parent=999_999_999, runners=_fake_runners([]))
    assert gone.run(max_ticks=5, sleep=lambda s: None) == "zatrzymany"
    kinds = [r["kind"] for r in store.rows("SELECT kind FROM automation_events")]
    assert "server_gone" in kinds


def test_real_worker_process_start_heartbeat_no_duplicate_and_stop(app_paths):
    """The real background process: started like the panel does, independent of any page."""
    import time as _time

    from sports_edge import automation, services

    store = services.open_store(app_paths)
    automation.command(store, "start", now=datetime.now(UTC))
    pid = automation.start_worker(app_paths)
    assert pid
    deadline = _time.time() + 60
    found = {}
    while _time.time() < deadline:
        found = automation.status(app_paths, store, now=datetime.now(UTC))
        if found["alive"]:
            break
        _time.sleep(0.5)
    assert found["alive"] and found["label"] == "aktywna" and found["pid"]  # worker's own PID
    assert automation.start_worker(app_paths) is None  # a second start never duplicates
    automation.command(store, "stop", now=datetime.now(UTC))
    deadline = _time.time() + 60
    while _time.time() < deadline and automation.lock_held(app_paths):
        _time.sleep(0.5)
    assert not automation.lock_held(app_paths)
    assert automation.status(app_paths, store, now=datetime.now(UTC))["label"] == "zatrzymana"


def test_autostart_follows_the_saved_setting(app_paths, monkeypatch):
    from sports_edge import automation, services

    store = services.open_store(app_paths)
    started = []
    monkeypatch.setattr(
        automation, "start_worker", lambda paths, parent=None: started.append(1) or 4242
    )
    assert "wyłączone" in automation.autostart(app_paths, store)
    settings = automation.load_settings(store)
    settings["auto_resume"] = True
    automation.save_settings(store, settings, now=NOW)
    assert "zatrzymana" in automation.autostart(app_paths, store)  # user stopped it before
    automation.command(store, "start", now=NOW)
    assert "4242" in automation.autostart(app_paths, store) and started == [1]
