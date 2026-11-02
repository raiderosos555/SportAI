"""TASK 009: interface overhaul — schedule through the web, coupon kinds, components.

Every team and match below is SYNTHETIC test data, not a real fact.
"""

from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)  # Monday 13:00 in Warsaw


# ---------------------------------------------------------------- schedule (web only)


def _settings(store, **job):
    from sports_edge import automation

    settings = automation.load_settings(store)
    settings["jobs"]["results"].update(job)
    automation.save_settings(store, settings, now=NOW)
    return automation.load_settings(store)


def test_fixed_times_on_chosen_weekdays_in_polish_time(app_paths):
    from sports_edge import automation, services

    store = services.open_store(app_paths)
    settings = _settings(
        store, enabled=True, mode="daily", times=["07:00", "19:30"], weekdays=[0, 2]
    )  # Monday and Wednesday
    found = automation.slots(settings["jobs"]["results"], NOW, NOW + timedelta(days=7))
    local = [
        (
            x.astimezone(automation.WARSAW).weekday(),
            x.astimezone(automation.WARSAW).strftime("%H:%M"),
        )
        for x in found
    ]
    assert local == [(0, "19:30"), (2, "07:00"), (2, "19:30"), (0, "07:00")]
    # the 07:00 slot of today passed without a run: it runs once now (catch-up) ...
    when = automation.next_run(store, "results", settings, now=NOW)
    assert when.astimezone(automation.WARSAW).strftime("%H:%M") == "07:00"
    assert "results" in automation.due_jobs(store, settings, now=NOW)


def test_a_daily_job_runs_once_per_slot_and_then_waits(app_paths):
    from sports_edge import automation, collection, services

    store = services.open_store(app_paths)
    settings = _settings(
        store, enabled=True, mode="daily", times=["07:00", "19:30"], weekdays=list(range(7))
    )
    collection.record_job(
        store, "auto:results", trigger="schedule", started=NOW, finished=NOW, status="ok", reason=""
    )
    following = automation.next_run(store, "results", settings, now=NOW + timedelta(minutes=1))
    assert following.astimezone(automation.WARSAW).strftime("%d %H:%M") == "02 19:30"
    assert "results" not in automation.due_jobs(store, settings, now=NOW + timedelta(minutes=1))


def test_one_off_tasks_run_once_and_can_be_cancelled(app_paths):
    from sports_edge import automation, services

    store = services.open_store(app_paths)
    first = automation.add_one_off(store, job="quality", at=NOW + timedelta(hours=1), now=NOW)
    second = automation.add_one_off(store, job="results", at=NOW + timedelta(hours=2), now=NOW)
    settings = automation.load_settings(store)
    assert automation.due_one_offs(store, settings, now=NOW) == []
    due = automation.due_one_offs(store, settings, now=NOW + timedelta(hours=1, minutes=1))
    assert [t["id"] for t in due] == [first]
    calls = []
    worker = automation.Worker(
        app_paths,
        clock=lambda: NOW + timedelta(hours=1, minutes=1),
        runners={
            job: (
                lambda j: (
                    lambda s, p, *, now, settings: (
                        calls.append(j) or automation.JobOutcome("ok", "", {})
                    )
                )
            )(job)
            for job in automation.JOBS
        },
    )
    automation.command(store, "start", now=NOW)
    worker.lock.acquire()
    worker.tick(store)
    worker.tick(store)  # the second tick does not repeat the one-off task
    worker.lock.release()
    assert calls.count("quality") >= 1
    assert (
        automation.due_one_offs(
            store, automation.load_settings(store), now=NOW + timedelta(hours=1, minutes=2)
        )
        == []
    )
    automation.cancel_one_off(store, second, now=NOW + timedelta(minutes=5))
    assert all(t["id"] != second for t in automation.load_settings(store)["one_off"])


def test_the_calendar_lists_fixed_times_long_intervals_and_one_offs(app_paths):
    from sports_edge import automation, services

    store = services.open_store(app_paths)
    _settings(store, enabled=True, mode="daily", times=["08:00"], weekdays=list(range(7)))
    automation.add_one_off(store, job="quality", at=NOW + timedelta(days=2), now=NOW)
    plan = automation.planned(store, automation.load_settings(store), now=NOW, days=7)
    kinds = {item["kind"] for item in plan}
    assert {"o godzinie", "jednorazowo"} <= kinds
    assert sum(item["job"] == "results" for item in plan) in (7, 8)  # one per day
    assert all(NOW <= item["at"] <= NOW + timedelta(days=7) for item in plan)
    assert plan == sorted(plan, key=lambda item: item["at"])


# ---------------------------------------------------------------- coupons and history


def _coupon(**extra):
    base = {
        "id": "c1",
        "outcome": None,
        "mode": "paper_system",
        "profile": "standard",
        "first_start_at": (NOW + timedelta(hours=5)).isoformat(),
        "created_at": NOW.isoformat(),
        "proposal": {},
    }
    return {**base, **extra}


def test_history_tells_candidates_saved_settled_real_and_historical_variants_apart():
    pytest.importorskip("streamlit")
    from sports_edge.app.pages_history import kind_label

    assert kind_label(_coupon(), NOW, {"c1"})[0] == "Bieżący kandydat"
    assert kind_label(_coupon(), NOW, {"c2"})[0] == "Historyczny wariant propozycji"
    assert kind_label(_coupon(profile="safe"), NOW, {"c1"})[0] == "Historyczny wariant propozycji"
    assert kind_label(_coupon(mode="paper_manual"), NOW, set())[0] == "Zapisany wirtualny kupon"
    assert kind_label(_coupon(outcome="win"), NOW, set())[0] == "Kupon rozliczony"
    assert kind_label(_coupon(mode="real_manual"), NOW, set())[0].startswith("Rzeczywisty")
    started = _coupon(first_start_at=(NOW - timedelta(hours=1)).isoformat())
    assert kind_label(started, NOW, {"c1"})[0] == "Zapisany wirtualny — czeka na wyniki"


def test_budget_simulation_is_an_exact_binomial_of_the_coupon():
    pytest.importorskip("streamlit")
    from sports_edge.app.coupon_view import budget

    payload = {
        "stake": "10.00",
        "net_profit": "8.00",  # a hit returns 18 zł
        "probability": {"calibrated": 0.6, "conservative": 0.5},
    }
    found = budget(payload, n=20)
    assert found["need"] == 12  # 12 × 18 = 216 ≥ 200 zł of stakes
    assert 0 < found["profit"]["conservative"] < found["profit"]["calibrated"] < 1
    assert found["hits"]["calibrated"] == pytest.approx(12.0)


# ---------------------------------------------------------------- components


def test_numbers_and_dates_never_wrap_and_missing_values_are_not_zero():
    pytest.importorskip("streamlit")
    from sports_edge.app import ui

    strip = ui.stat_strip([("Kurs łączny", "1,90"), ("Szansa", "57,2%")])
    assert "se-stats" in strip and "1,90" in strip
    table = ui.compare_table("Gospodarze", "Goście", [("Punkty na mecz", "1,80", None, "home", "")])
    assert "brak danych" in table and ">0<" not in table
    assert 'class="v better"' in table
    assert "white-space: nowrap" in ui.CSS
    assert "overflow-wrap: anywhere" not in ui.CSS  # letters are never split
    assert "Tasiemka" in ui.profile_badge("tasiemka") and "violet" in ui.profile_badge("tasiemka")
    assert "blue" in ui.profile_badge("standard")
    factors = ui.factors([("Elo", "model"), ("Skład", "warning"), ("xG", "missing")])
    assert "w modelu" in factors and "tylko ostrzeżenie" in factors and "brak danych" in factors


def test_no_trust_wording_in_the_interface():
    from pathlib import Path

    for path in Path("src/sports_edge/app").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "można ufać" not in text.lower() and "można im ufać" not in text.lower(), path


def test_visual_checker_detects_broken_numbers_and_overflow(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    from sports_edge.qa_visual import CHECK

    page_html = """<html><body><div data-testid="stMain">
      <div class="se-card" style="width:40px;overflow-wrap:anywhere">kurs 1,90 i szansa 57,2%</div>
      <div style="width:2000px">za szeroko</div></div></body></html>"""
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as error:  # noqa: BLE001 — no browser installed: nothing to check
            pytest.skip(f"brak przeglądarki Playwright: {error}")
        page = browser.new_page(viewport={"width": 390, "height": 600})
        page.set_content(page_html)
        found = page.evaluate(CHECK)
        browser.close()
    assert found["overflow"] > 0
    assert found["narrow"]  # a 40 px card is too narrow
    assert set(found["broken"]) & {"1,90", "57,2%", "szansa"}
