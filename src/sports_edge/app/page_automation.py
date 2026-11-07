"""AUTOMATYZACJA AI (TASK 009): the control panel of the background worker, in five tabs.

PRZEGLĄD — status, last and next task, the main buttons, the most important errors;
HARMONOGRAM — calendar, fixed times and weekdays, one-off tasks, intervals and the checks
relative to kick-off; PROCESY — which automations run and how often; HISTORIA — finished
tasks with duration, results and error reasons; ALERTY — important new events, system
information and the history of notifications.

Every button writes a real command or setting to SQLite or starts the real worker process;
the status comes from the worker's lock and heartbeat. No Windows Task Scheduler is used.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st

from sports_edge import alerts, automation
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.services import prematch_targets

STATE_TONE = {"aktywna": "ok", "wstrzymana": "warn", "zatrzymana": "neutral"}
EVERY_LABEL = {
    5: "co 5 min",
    10: "co 10 min",
    15: "co 15 min",
    30: "co 30 min",
    60: "co godzinę",
    180: "co 3 godziny",
    360: "co 6 godzin",
    720: "co 12 godzin",
    1440: "raz dziennie",
    4320: "co 3 dni",
    10080: "co 7 dni",
    20160: "co 14 dni",
    0: "tylko ręcznie",
}
EVENT_LABEL = {
    "worker_started": "Worker uruchomiony",
    "worker_stopped": "Worker zatrzymany",
    "catch_up": "Nadrobienie zaległych zadań",
    "command_processed": "Polecenie wykonane",
    "server_gone": "Serwer aplikacji zakończył pracę",
    "worker_crashed": "Awaria workera",
    "one_off_done": "Zadanie jednorazowe wykonane",
}
TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]


def automation_page() -> None:
    c.setup_page()
    ui.app_header(
        "Automatyzacja AI",
        "Wyniki, rozliczenia, składy przed meczem, kontrola kuponów i przeglądy modeli — "
        "sterowane z tej strony",
    )
    if c.demo_mode():
        st.info(
            "Tryb DEMO: worker automatyzacji pracuje wyłącznie na danych rzeczywistych — "
            "wyłącz DEMO na pasku bocznym, aby nim sterować."
        )
        return
    db, paths, now = c.store(), c.paths(), c.now()
    found = automation.status(paths, db, now=now)
    settings = automation.load_settings(db)
    tabs = st.tabs(["Przegląd", "Harmonogram", "Procesy", "Historia", "Alerty"])
    with tabs[0]:
        _overview(db, paths, found, now)
    with tabs[1]:
        _schedule(db, settings, now)
    with tabs[2]:
        _processes(db, settings)
    with tabs[3]:
        _history(db)
    with tabs[4]:
        _alerts(db)
    st.caption(
        "Worker pracuje, dopóki działa serwer aplikacji — także po zamknięciu karty przeglądarki. "
        "Po wyłączeniu serwera lub komputera praca ustaje; po ponownym starcie (start.bat) worker "
        "wznawia pracę, jeśli włączono automatyczne wznowienie. Harmonogram zadań Windows nie jest "
        "potrzebny."
    )


def _ago(value: str | None, now: datetime) -> str:
    if not value:
        return "—"
    seconds = (now - datetime.fromisoformat(value)).total_seconds()
    if seconds < 90:
        return f"{seconds:.0f} s temu"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min temu"
    return c.local_time(value, "%d.%m %H:%M")


def _label(job: str) -> str:
    return automation.JOB_LABELS.get(job, job)


# ---------------------------------------------------------------- PRZEGLĄD


def _overview(db, paths, found: dict, now: datetime) -> None:
    history = automation.history(db, limit=50)
    last = history[0] if history else None
    nexts = sorted(
        ((when, job) for job, when in (found.get("next_runs") or {}).items()), key=lambda x: x[0]
    )
    ui.status_strip(
        [
            (
                "Automatyzacja",
                found["label"],
                STATE_TONE.get(found["label"], "bad"),
                "z blokady i heartbeat workera",
            ),
            (
                "Worker",
                "działa" if found["alive"] else "nie działa",
                "ok" if found["alive"] else "neutral",
                f"heartbeat {_ago(found.get('heartbeat_at'), now)}" if found["alive"] else "—",
            ),
            (
                "Ostatnie zadanie",
                _label(last["job"][5:]) if last else "—",
                {"ok": "ok", "failed": "bad", "partial": "warn"}.get(
                    last["status"] if last else "", "neutral"
                ),
                f"{last['status']} · {c.local_time(last['started_at'], '%d.%m %H:%M')}"
                if last
                else "brak uruchomień",
            ),
            (
                "Następne zadanie",
                _label(nexts[0][1]) if nexts else "—",
                "info" if nexts else "neutral",
                c.local_time(nexts[0][0], "%d.%m %H:%M") if nexts else "po uruchomieniu workera",
            ),
        ]
    )
    _controls(db, paths, found)
    if found.get("catch_up"):
        st.info(
            "Po starcie nadrobiono zaległe zadania: "
            + ", ".join(_label(j) for j in found["catch_up"])
        )
    ui.section("Najważniejsze błędy", "ostatnie zadania zakończone błędem")
    errors = [r for r in history if r["status"] in ("failed", "partial")][:3]
    if errors:
        ui.rows(
            [
                ui.list_row(
                    title=r["label"],
                    meta=f"{c.local_time(r['started_at'], '%d.%m %H:%M')} · "
                    + (r["reason"] or r["status"]),
                    badges=[ui.pill(r["status"], "bad" if r["status"] == "failed" else "warn")],
                    tone="bad" if r["status"] == "failed" else "warn",
                )
                for r in errors
            ]
        )
    else:
        st.caption("Brak błędów w ostatnich zadaniach.")
    with ui.details("Szczegóły pracy workera i monitoring danych kadrowych"):
        ui.metric_cards(
            [
                (
                    "Start workera",
                    c.local_time(found.get("started_at"), "%d.%m %H:%M")
                    if found.get("started_at")
                    else "—",
                    f"PID {found.get('pid', '—')}",
                ),
                ("Bieżące zadanie", found.get("current_task") or "—", "teraz"),
                ("Ostatni sukces", _ago(found.get("last_success_at"), now), "zadanie zakończone"),
                (
                    "Ostatni błąd",
                    _ago(found.get("last_error_at"), now),
                    (found.get("last_error") or "—")[:60],
                ),
                ("Oczekujące polecenia", str(found["pending"]), "w kolejce"),
                (
                    "Błędy / zdarzenia",
                    f"{found.get('errors', 0)} / {found.get('processed_events', 0)}",
                    "od startu workera",
                ),
            ]
        )
        _quality(db)


def _controls(db, paths, found: dict) -> None:
    """Start and an immediate check first; pause, resume and stop in the second row."""
    now = c.now()
    with st.container(key="row-controls-main"):
        start, check = st.columns(2)
    with st.container(key="row-controls-more"):
        pause, resume, stop = st.columns(3)
    if start.button(
        "URUCHOM AUTOMATYZACJĘ",
        type="primary",
        icon=":material/play_arrow:",
        width="stretch",
        disabled=found["alive"] and found["label"] == "aktywna",
    ):
        automation.command(db, "start", now=now)
        automation.start_worker(paths)
        st.toast("Uruchamianie workera…")
        st.rerun()
    if check.button("URUCHOM KONTROLĘ TERAZ", icon=":material/bolt:", width="stretch"):
        automation.command(db, "run_now", now=now)
        if not found["alive"]:
            automation.start_worker(paths, once=True)  # one-off run, then the process ends
        st.toast("Kontrola zlecona workerowi — wyniki pojawią się w historii zadań.")
        st.rerun()
    if pause.button(
        "WSTRZYMAJ AUTOMATYZACJĘ",
        icon=":material/pause:",
        width="stretch",
        disabled=not found["alive"] or found["label"] == "wstrzymana",
    ):
        automation.command(db, "pause", now=now)
        st.rerun()
    if resume.button(
        "WZNÓW AUTOMATYZACJĘ",
        icon=":material/resume:",
        width="stretch",
        disabled=found["label"] not in ("wstrzymana",) and found["alive"],
    ):
        automation.command(db, "resume", now=now)
        automation.start_worker(paths)
        st.rerun()
    if stop.button(
        "ZATRZYMAJ AUTOMATYZACJĘ",
        icon=":material/stop:",
        width="stretch",
        disabled=not found["alive"],
    ):
        automation.command(db, "stop", now=now)
        st.toast("Worker zakończy bieżącą operację i zatrzyma się.")
        st.rerun()


def _quality(db) -> None:
    checks = db.rows("SELECT status, requests FROM prematch_checks")
    lineups = db.rows("SELECT event_id, status FROM lineup_observations")
    confirmed = {r["event_id"] for r in lineups if r["status"] in ("confirmed", "official")}
    ui.metric_cards(
        [
            ("Mecze z obserwacją składu", str(len({r["event_id"] for r in lineups})), "łącznie"),
            ("Potwierdzone składy", str(len(confirmed)), "mecze"),
            ("Kontrole", str(len(checks)), f"zapytania API: {sum(r['requests'] for r in checks)}"),
            (
                "Pominięte / błędy",
                f"{sum(r['status'] == 'missed' for r in checks)} / "
                f"{sum(r['status'] == 'error' for r in checks)}",
                "kontrole",
            ),
        ]
    )


# ---------------------------------------------------------------- HARMONOGRAM


def _summary(cfg: dict) -> str:
    if not cfg.get("enabled"):
        return "wyłączone"
    if cfg.get("mode") == "daily":
        days = cfg.get("weekdays")
        when = (
            "codziennie"
            if days is None or len(days) == 7
            else ", ".join(automation.WEEKDAYS[d] for d in sorted(days))
        )
        return f"o {', '.join(sorted(cfg.get('times') or [])) or '—'} · {when}"
    every = int(cfg.get("every_minutes") or 0)
    return EVERY_LABEL.get(every, f"co {every} min") if every else "razem z innym zadaniem"


def _schedule(db, settings: dict, now: datetime) -> None:
    ui.section("Kalendarz", "najbliższe 7 dni · godziny w czasie polskim")
    short = [
        f"{_label(job)} — {_summary(cfg)}"
        for job, cfg in settings["jobs"].items()
        if job in automation.JOBS
        and cfg.get("enabled")
        and cfg.get("mode") != "daily"
        and 0 < int(cfg.get("every_minutes") or 0) < 360
    ]
    if short:
        st.caption("Stale w tle: " + " · ".join(short))
    plan = automation.planned(db, settings, now=now, days=7)
    by_day: dict[date, list[dict]] = defaultdict(list)
    for item in plan:
        local = c.local_time(item["at"].isoformat(), "%Y-%m-%d")
        by_day[date.fromisoformat(local)].append(item)
    cards = []
    for offset in range(7):
        day = (now.astimezone(c.WARSAW) + timedelta(days=offset)).date()
        items = by_day.get(day, [])
        body = (
            "".join(
                f'<div class="se-side-row"><span class="se-num">'
                f"{ui.esc(c.local_time(i['at'].isoformat(), '%H:%M'))}</span>"
                f"<b>{ui.esc(_label(i['job']))}</b></div>"
                for i in items[:8]
            )
            or '<div class="se-muted" style="font-size:.85rem">bez zaplanowanych zadań</div>'
        )
        more = (
            f'<div class="se-muted" style="font-size:.8rem">+ {len(items) - 8}</div>'
            if (len(items) > 8)
            else ""
        )
        cards.append(ui.card(f"<b>{ui.esc(c.day_label(day.isoformat()))}</b>" + body + more))
    ui.card_grid(cards, wide=False)
    _kickoff(db, settings, now)
    _matchday(db, settings, now)
    _editor(db, settings)
    _one_off(db, settings, now)


def _kickoff(db, settings: dict, now: datetime) -> None:
    ui.section("Względem rozpoczęcia meczów", "kontrole składów T-48 h … T-15 min")
    names = automation.CHECKPOINT_NAMES
    chosen = st.multiselect(
        "Punkty kontroli przed meczem",
        list(names),
        default=[m for m in settings["checkpoints_minutes"] if m in names],
        format_func=names.get,
        key="auto_checkpoints",
        placeholder="wybierz…",
    )
    if st.button("Zapisz punkty kontroli", icon=":material/save:", key="auto_save_checkpoints"):
        settings["checkpoints_minutes"] = sorted(chosen, reverse=True)
        automation.save_settings(db, settings, now=c.now())
        st.success("Punkty kontroli zapisane.")
    targets = prematch_targets(db, now=now, max_candidates=int(settings.get("max_candidates", 30)))
    rows = []
    for event in targets[:12]:
        start = datetime.fromisoformat(event["start_at"])
        upcoming = [
            (names[m], start - timedelta(minutes=m))
            for m in sorted(settings["checkpoints_minutes"], reverse=True)
            if m in names and start - timedelta(minutes=m) > now
        ]
        rows.append(
            {
                "Mecz": f"{event['home']} - {event['away']}",
                "Start": c.local_time(event["start_at"], "%d.%m %H:%M"),
                "Następna kontrola": (
                    f"{upcoming[0][0]} · {c.local_time(upcoming[0][1].isoformat(), '%d.%m %H:%M')}"
                    if upcoming
                    else "—"
                ),
                "Pozostałe": len(upcoming),
                "Z kuponu": "tak" if event["priority"] == 1 else "nie",
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption("Brak meczów do monitorowania w najbliższych 48 godzinach.")


MATCHDAY_LEADS = [180, 120, 90, 60, 45, 30]


def _matchday(db, settings: dict, now: datetime) -> None:
    """TASK 012 §4.1: extra downloads of today's STS offer before groups of matches."""
    ui.section(
        "Kursy STS w dniu meczu",
        "dodatkowe pobrania oferty na dziś przed grupami meczów 12 lig z modelem",
    )
    cfg = automation.matchday_config(settings)
    job = settings["jobs"].get("sts_offer") or {}
    allowed = automation.download_allowed(c.paths())
    used = len(automation.matchday_runs_today(db, now=now))
    if not allowed:
        state = "nieaktywny — brak zgody na pobieranie oferty STS (Źródła i ustawienia) lub tryb offline"
    elif not job.get("enabled"):
        state = "nieaktywny — zadanie „Sprawdzanie nowych kursów STS” jest wyłączone"
    elif not cfg.get("enabled"):
        state = "wyłączony"
    else:
        state = "aktywny (zgoda na pobieranie oferty STS — bez dodatkowej zgody)"
    st.caption(
        f"Stan: {state} · pobrania dziś: {used}/{cfg['daily_cap']} · każdy plik STS najwyżej "
        "raz na 10 minut"
    )
    rows = [
        {
            "Pobranie": c.local_time(slot["at"].isoformat(), "%H:%M"),
            "Pierwszy mecz grupy": c.local_time(slot["group"].isoformat(), "%H:%M"),
            "Wyprzedzenie": f"{slot['lead']} min",
            "Stan": slot["status"]
            + (
                f" ({c.local_time(slot['download'].isoformat(), '%H:%M')})"
                if slot["download"]
                else ""
            ),
        }
        for slot in automation.matchday_status(db, settings, now=now)
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption("Brak meczów 12 lig w dzisiejszej ofercie STS — brak pobrań w dniu meczu.")
    with st.container(key="row-matchday"):
        cols = st.columns([1, 2, 1], vertical_alignment="bottom")
        enabled = cols[0].toggle(
            "Tryb w dniu meczu", value=bool(cfg.get("enabled")), key="auto_matchday_on"
        )
        leads = cols[1].multiselect(
            "Minuty przed pierwszym meczem grupy",
            MATCHDAY_LEADS,
            default=[m for m in cfg.get("leads_minutes") or [] if m in MATCHDAY_LEADS],
            key="auto_matchday_leads",
            placeholder="wybierz…",
        )
        cap = cols[2].number_input(
            "Dzienny limit",
            1,
            12,
            min(12, int(cfg.get("daily_cap") or 12)),
            key="auto_matchday_cap",
        )
    if st.button("Zapisz tryb w dniu meczu", icon=":material/save:", key="auto_save_matchday"):
        settings["jobs"].setdefault("sts_offer", {})["matchday"] = {
            "enabled": bool(enabled),
            "leads_minutes": sorted({int(m) for m in leads}, reverse=True) or [45],
            "daily_cap": int(cap),
        }
        automation.save_settings(db, settings, now=c.now())
        st.success("Tryb w dniu meczu zapisany.")


def _editor(db, settings: dict) -> None:
    ui.section("Harmonogram zadania", "co określony czas albo o stałych godzinach w wybrane dni")
    jobs = [j for j in automation.JOBS if j not in ("absences", "training")]
    job = st.selectbox("Zadanie", jobs, format_func=_label, key="auto_edit_job")
    cfg = dict(settings["jobs"].get(job) or {})
    st.caption(f"Teraz: {_summary(cfg)}")
    mode = st.radio(
        "Tryb",
        list(automation.MODES),
        index=list(automation.MODES).index(cfg.get("mode", "interval")),
        format_func=automation.MODES.get,
        horizontal=True,
        key=f"auto_mode_{job}",
    )
    if mode == "interval":
        options = automation.FREQUENCIES.get(job) or [60]
        current = int(cfg.get("every_minutes") or options[0])
        if current not in options:
            options = [*options, current]
        every = st.selectbox(
            "Częstotliwość",
            options,
            index=options.index(current),
            format_func=lambda m: EVERY_LABEL.get(m, f"co {m} min"),
            key=f"auto_edit_every_{job}",
        )
        changes = {"mode": "interval", "every_minutes": every}
    else:
        times = st.multiselect(
            "Godziny (czas polski)",
            TIMES,
            default=[t for t in cfg.get("times") or ["07:00"] if t in TIMES],
            key=f"auto_edit_times_{job}",
            placeholder="wybierz…",
        )
        days = st.pills(
            "Dni tygodnia",
            list(range(7)),
            selection_mode="multi",
            default=cfg.get("weekdays") if cfg.get("weekdays") is not None else list(range(7)),
            format_func=lambda d: automation.WEEKDAYS[d],
            key=f"auto_edit_days_{job}",
        )
        changes = {"mode": "daily", "times": sorted(times), "weekdays": sorted(days or [])}
    if st.button("Zapisz harmonogram zadania", icon=":material/event:", key="auto_save_job"):
        if changes["mode"] == "daily" and (not changes["times"] or not changes["weekdays"]):
            st.error("Wybierz co najmniej jedną godzinę i jeden dzień tygodnia.")
        else:
            settings["jobs"].setdefault(job, {}).update(changes, enabled=True)
            automation.save_settings(db, settings, now=c.now())
            st.success(f"Harmonogram zapisany: {_label(job)} — {_summary(settings['jobs'][job])}.")


def _one_off(db, settings: dict, now: datetime) -> None:
    ui.section("Zadania jednorazowe", "uruchamiane raz o wskazanej godzinie")
    with st.container(key="row-oneoff"):
        cols = st.columns([2, 1, 1])
        job = cols[0].selectbox(
            "Zadanie", list(automation.JOBS), format_func=_label, key="auto_once_job"
        )
        local = now.astimezone(c.WARSAW) + timedelta(hours=1)
        day = cols[1].date_input(
            "Dzień", value=local.date(), key="auto_once_day", format="DD.MM.YYYY"
        )
        moment = cols[2].time_input(
            "Godzina", value=time(local.hour, 0), key="auto_once_time", step=900
        )
    if st.button("Zaplanuj jednorazowo", icon=":material/schedule:", key="auto_once_add"):
        at = datetime.combine(day, moment, tzinfo=c.WARSAW)
        automation.add_one_off(db, job=job, at=at, now=c.now())
        st.success(f"Zaplanowano: {_label(job)} — {c.local_time(at.isoformat(), '%d.%m %H:%M')}.")
        st.rerun()
    done = automation.done_one_offs(db)
    waiting = [t for t in settings.get("one_off") or [] if t["id"] not in done]
    for task in waiting:
        with st.container(key=f"row-once-{task['id']}"):
            left, right = st.columns([4, 1], vertical_alignment="center")
            left.markdown(
                ui.list_row(
                    title=_label(task["job"]),
                    meta=f"jednorazowo · {c.local_time(task['at'], '%d.%m.%Y %H:%M')}",
                    badges=[ui.pill("zaplanowane", "info")],
                ),
                unsafe_allow_html=True,
            )
            if right.button(
                "Anuluj", key=f"auto_once_cancel_{task['id']}", icon=":material/close:"
            ):
                automation.cancel_one_off(db, task["id"], now=c.now())
                st.rerun()
    if not waiting:
        st.caption("Brak zaplanowanych zadań jednorazowych.")


# ---------------------------------------------------------------- PROCESY


def _processes(db, settings: dict) -> None:
    ui.section("Procesy", "włącz lub wyłącz automatyzacje i ustaw ich częstotliwość")
    jobs = settings["jobs"]
    for job, label in automation.JOB_LABELS.items():
        if job in automation.JOBS_EXTRA:  # on-demand jobs (buttons), not scheduled
            continue
        cfg = jobs.setdefault(job, {"enabled": False})
        with st.container(border=True, key=f"row-proc-{job}"):
            cols = st.columns([3, 2], vertical_alignment="center")
            cfg["enabled"] = cols[0].toggle(
                label, value=bool(cfg.get("enabled")), key=f"auto_on_{job}"
            )
            options = automation.FREQUENCIES.get(job)
            if options and cfg.get("mode") != "daily":
                current = cfg.get("every_minutes", options[0])
                if current not in options:
                    options = [*options, current]
                cfg["every_minutes"] = cols[1].selectbox(
                    "Częstotliwość",
                    options,
                    index=options.index(current),
                    format_func=lambda m: EVERY_LABEL.get(m, f"co {m} min"),
                    key=f"auto_every_{job}",
                    label_visibility="collapsed",
                )
            elif cfg.get("mode") == "daily":
                cols[1].caption(f"{_summary(cfg)} (zmień w Harmonogramie)")
            elif job == "absences":
                cols[1].caption("razem z kontrolą składów (brak zweryfikowanego źródła)")
            elif job == "training":
                cols[1].caption("tylko challenger — aktywacja zawsze należy do Ciebie")
            if job == "forecasts" and cfg.get("enabled"):  # TASK 012
                st.caption(
                    f"Także po każdym nowym dokumencie STS i codziennie o {cfg.get('morning') or '—'} "
                    "(awaryjnie); bez kuponów, bez pobierania danych, pomijane w trybie offline."
                )
            elif job == "sts_offer":
                st.caption("Tryb „w dniu meczu”: Harmonogram → Kursy STS w dniu meczu.")
    ui.section("Konfiguracja")
    with st.container(key="row-config"):
        cols = st.columns(2)
        settings["max_candidates"] = int(
            cols[0].number_input(
                "Maks. liczba kandydatów monitorowanych poza kuponami",
                0,
                200,
                int(settings.get("max_candidates", 30)),
                help="Mecze z kuponów są zawsze monitorowane w pierwszej kolejności; limit "
                "chroni przed nadmiarem zapytań API.",
            )
        )
        settings["auto_resume"] = cols[1].toggle(
            "AUTOMATYCZNIE WZNÓW PO STARCIE SERWERA",
            value=bool(settings.get("auto_resume")),
            help="start.bat uruchomi workera razem z serwerem — bez otwierania przeglądarki.",
        )
    if st.button("ZAPISZ USTAWIENIA", icon=":material/save:", type="primary"):
        automation.save_settings(db, settings, now=c.now())
        st.success(
            "Ustawienia zapisane w bazie — przetrwają odświeżenie, zamknięcie przeglądarki "
            "i restart."
        )


# ---------------------------------------------------------------- HISTORIA


def _duration(row: dict) -> str:
    if not row["finished_at"]:
        return "—"
    seconds = (
        datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])
    ).total_seconds()
    return f"{seconds:.1f} s" if seconds < 90 else f"{seconds / 60:.1f} min"


def _history(db) -> None:
    rows = automation.history(db, limit=300)
    ui.section("Historia zadań", "każde uruchomienie jest zapisane")
    if not rows:
        st.caption("Brak uruchomień — kliknij URUCHOM AUTOMATYZACJĘ albo URUCHOM KONTROLĘ TERAZ.")
        return
    with st.container(key="row-history-filters"):
        cols = st.columns(2)
        labels = sorted({r["label"] for r in rows})
        job = cols[0].selectbox("Zadanie", ["wszystkie", *labels], key="auto_hist_job")
        status = cols[1].selectbox(
            "Wynik", ["wszystkie", "ok", "partial", "skipped", "failed"], key="auto_hist_status"
        )
    shown = [
        r
        for r in rows
        if (job == "wszystkie" or r["label"] == job)
        and (status == "wszystkie" or r["status"] == status)
    ]
    frame = pd.DataFrame(
        [
            {
                "Zadanie": r["label"],
                "Start": c.local_time(r["started_at"], "%d.%m %H:%M:%S"),
                "Czas": _duration(r),
                "Wynik": r["status"],
                "Przetworzone": (r["payload"] or {}).get("processed"),
                "Nowe": (r["payload"] or {}).get("new"),
                "Przyczyna": r["reason"] or "—",
                "Wyzwalacz": r["trigger"],
            }
            for r in shown
        ]
    )
    picked = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=360,
        on_select="rerun",
        selection_mode="single-row",
        key="auto_history",
    )
    if picked.selection.rows:
        row = shown[picked.selection.rows[0]]
        cols = st.columns(2)
        if cols[0].button("POKAŻ SZCZEGÓŁY", icon=":material/info:"):
            st.json(row["payload"], expanded=True)
        if cols[1].button("URUCHOM PONOWNIE", icon=":material/replay:"):
            automation.command(c.store(), f"run_job:{row['job'][5:]}", now=c.now())
            st.toast("Ponowne uruchomienie zlecone workerowi (bez duplikowania danych).")
    st.caption("Zaznacz wiersz, aby zobaczyć szczegóły albo uruchomić zadanie ponownie.")


# ---------------------------------------------------------------- ALERTY


def _alerts(db) -> None:
    unread = alerts.recent(db, limit=100, unread_only=True)
    important = [a for a in unread if a["priority"] in ("high", "medium")]
    ui.section("Ważne nowe zdarzenia", f"nieprzeczytane: {len(important)}")
    if important:
        ui.rows(
            [
                ui.list_row(
                    title=a["message"],
                    meta=f"{c.local_time(a['created_at'], '%d.%m %H:%M')} · {a['source']}",
                    badges=[ui.pill(a["label"], "bad" if a["priority"] == "high" else "warn")],
                    tone="bad" if a["priority"] == "high" else "warn",
                )
                for a in important[:15]
            ]
        )
    else:
        st.caption("Brak ważnych nowych zdarzeń.")
    if unread and st.button("Oznacz wszystkie jako przeczytane", icon=":material/done_all:"):
        alerts.mark_read(db, [a["id"] for a in unread], now=c.now())
        st.rerun()
    minor = len(unread) - len(important)
    if minor:
        st.caption(f"Informacje o niższym priorytecie: {minor} — w historii powiadomień poniżej.")
    ui.section("Informacje systemowe", "praca workera")
    events = db.rows(
        "SELECT created_at, kind, detail FROM automation_events WHERE kind!='command_processed' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 25"
    )
    if events:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Czas": c.local_time(e["created_at"], "%d.%m %H:%M:%S"),
                        "Zdarzenie": EVENT_LABEL.get(e["kind"], e["kind"]),
                        "Szczegóły": e["detail"] or "—",
                    }
                    for e in events
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Brak zdarzeń systemowych.")
    with ui.details("Historia powiadomień"):
        every = alerts.recent(db, limit=100)
        if every:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Czas": c.local_time(a["created_at"], "%d.%m %H:%M"),
                            "Rodzaj": a["label"],
                            "Treść": a["message"],
                            "Priorytet": a["priority"],
                            "Przeczytane": "tak" if a["read_at"] else "nie",
                        }
                        for a in every
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("Brak powiadomień.")
