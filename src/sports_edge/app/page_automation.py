"""AUTOMATYZACJA AI: the control panel of the background worker.

Every button writes a real command to SQLite or starts the real worker process; the status
comes from the worker's lock and heartbeat — never from a switch remembered by the page.
No Windows Task Scheduler is used or needed.
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from sports_edge import alerts, automation
from sports_edge.app import common as c
from sports_edge.app import ui

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
    _status(found, now)
    _controls(db, paths, found)
    settings = automation.load_settings(db)
    _settings(db, settings)
    _history(db)
    _alerts(db)
    _quality(db, now)
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


def _status(found: dict, now: datetime) -> None:
    ui.section("Status pracy", "z blokady i heartbeat workera — nie z zapamiętanego przełącznika")
    tone = STATE_TONE.get(found["label"], "bad")
    ui.pills(
        [
            (f"Automatyzacja: {found['label']}", tone),
            (
                "Worker działa" if found["alive"] else "Worker nie działa",
                "ok" if found["alive"] else "neutral",
            ),
        ]
    )
    nexts = sorted((found.get("next_runs") or {}).values())
    ui.metric_cards(
        [
            (
                "Ostatni heartbeat",
                _ago(found.get("heartbeat_at"), now),
                f"PID {found.get('pid', '—')}",
            ),
            (
                "Start workera",
                c.local_time(found.get("started_at"), "%d.%m %H:%M")
                if found.get("started_at")
                else "—",
                "czas uruchomienia",
            ),
            ("Bieżące zadanie", found.get("current_task") or "—", "teraz"),
            ("Ostatni sukces", _ago(found.get("last_success_at"), now), "zadanie zakończone"),
            (
                "Ostatni błąd",
                _ago(found.get("last_error_at"), now),
                (found.get("last_error") or "—")[:60],
            ),
            (
                "Najbliższe zadanie",
                c.local_time(nexts[0], "%d.%m %H:%M") if nexts else "—",
                "według harmonogramu",
            ),
            ("Oczekujące polecenia", str(found["pending"]), "w kolejce"),
            (
                "Błędy / zdarzenia",
                f"{found.get('errors', 0)} / {found.get('processed_events', 0)}",
                "od startu workera",
            ),
        ]
    )
    if found.get("catch_up"):
        st.info(
            "Po starcie nadrobiono zaległe zadania: "
            + ", ".join(automation.JOB_LABELS.get(j, j) for j in found["catch_up"])
        )


def _controls(db, paths, found: dict) -> None:
    ui.section("Sterowanie")
    cols = st.columns(5)
    now = c.now()
    if cols[0].button(
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
    if cols[1].button(
        "WSTRZYMAJ AUTOMATYZACJĘ",
        icon=":material/pause:",
        width="stretch",
        disabled=not found["alive"] or found["label"] == "wstrzymana",
    ):
        automation.command(db, "pause", now=now)
        st.rerun()
    if cols[2].button(
        "WZNÓW AUTOMATYZACJĘ",
        icon=":material/resume:",
        width="stretch",
        disabled=found["label"] not in ("wstrzymana",) and found["alive"],
    ):
        automation.command(db, "resume", now=now)
        automation.start_worker(paths)
        st.rerun()
    if cols[3].button(
        "ZATRZYMAJ AUTOMATYZACJĘ",
        icon=":material/stop:",
        width="stretch",
        disabled=not found["alive"],
    ):
        automation.command(db, "stop", now=now)
        st.toast("Worker zakończy bieżącą operację i zatrzyma się.")
        st.rerun()
    if cols[4].button("URUCHOM KONTROLĘ TERAZ", icon=":material/bolt:", width="stretch"):
        automation.command(db, "run_now", now=now)
        if not found["alive"]:
            automation.start_worker(paths, once=True)  # one-off run, then the process ends
        st.toast("Kontrola zlecona workerowi — wyniki pojawią się w historii zadań.")
        st.rerun()


def _settings(db, settings: dict) -> None:
    ui.section("Procesy i częstotliwość", "zmiany obowiązują po zapisaniu ustawień")
    jobs = settings["jobs"]
    for job, label in automation.JOB_LABELS.items():
        if job in automation.JOBS_EXTRA:  # on-demand jobs (buttons), not scheduled
            continue
        cfg = jobs.setdefault(job, {"enabled": False})
        cols = st.columns([3, 2])
        cfg["enabled"] = cols[0].toggle(label, value=bool(cfg.get("enabled")), key=f"auto_on_{job}")
        options = automation.FREQUENCIES.get(job)
        if options:
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
        elif job == "absences":
            cols[1].caption("razem z kontrolą składów (brak zweryfikowanego źródła)")
        elif job == "training":
            cols[1].caption("tylko challenger — aktywacja zawsze należy do Ciebie")
    names = automation.CHECKPOINT_NAMES
    chosen = st.multiselect(
        "Kontrole składów względem godziny rozpoczęcia meczu",
        list(names),
        default=[m for m in settings["checkpoints_minutes"] if m in names],
        format_func=names.get,
    )
    cols = st.columns(2)
    settings["max_candidates"] = int(
        cols[0].number_input(
            "Maks. liczba kandydatów monitorowanych poza kuponami",
            0,
            200,
            int(settings.get("max_candidates", 30)),
            help="Mecze z kuponów są zawsze monitorowane "
            "w pierwszej kolejności; limit chroni przed nadmiarem zapytań API.",
        )
    )
    settings["auto_resume"] = cols[1].toggle(
        "AUTOMATYCZNIE WZNÓW PO STARCIE SERWERA",
        value=bool(settings.get("auto_resume")),
        help="start.bat uruchomi workera razem z serwerem — bez otwierania przeglądarki.",
    )
    settings["checkpoints_minutes"] = sorted(chosen, reverse=True)
    if st.button("ZAPISZ USTAWIENIA", icon=":material/save:"):
        automation.save_settings(db, settings, now=c.now())
        st.success(
            "Ustawienia zapisane w bazie — przetrwają odświeżenie, zamknięcie przeglądarki "
            "i restart."
        )


def _history(db) -> None:
    ui.section("Historia zadań", "każde uruchomienie jest zapisane")
    rows = automation.history(db, limit=100)
    if not rows:
        st.caption("Brak uruchomień — kliknij URUCHOM AUTOMATYZACJĘ albo URUCHOM KONTROLĘ TERAZ.")
        return
    frame = pd.DataFrame(
        [
            {
                "Zadanie": r["label"],
                "Start": c.local_time(r["started_at"], "%d.%m %H:%M:%S"),
                "Koniec": c.local_time(r["finished_at"], "%H:%M:%S"),
                "Status": r["status"],
                "Przetworzone": (r["payload"] or {}).get("processed"),
                "Nowe": (r["payload"] or {}).get("new"),
                "Błędy": (r["payload"] or {}).get("errors"),
                "Powód": r["reason"] or "—",
                "Wyzwalacz": r["trigger"],
            }
            for r in rows
        ]
    )
    picked = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=320,
        on_select="rerun",
        selection_mode="single-row",
        key="auto_history",
    )
    if picked.selection.rows:
        row = rows[picked.selection.rows[0]]
        cols = st.columns(2)
        if cols[0].button("POKAŻ SZCZEGÓŁY", icon=":material/info:"):
            st.json(row["payload"], expanded=True)
        if cols[1].button("URUCHOM PONOWNIE", icon=":material/replay:"):
            automation.command(c.store(), f"run_job:{row['job'][5:]}", now=c.now())
            st.toast("Ponowne uruchomienie zlecone workerowi (bez duplikowania danych).")
    errors = [r for r in rows if r["status"] in ("failed", "partial")]
    with st.expander(f"Błędy i ostrzeżenia ({len(errors)})"):
        for r in errors[:20]:
            st.write(
                f"• {c.local_time(r['started_at'], '%d.%m %H:%M')} — {r['label']}: "
                f"{r['reason'] or r['status']}"
            )


def _alerts(db) -> None:
    unread = alerts.recent(db, limit=30, unread_only=True)
    ui.section("Alerty", f"nieprzeczytane: {len(unread)}")
    if not unread:
        st.caption("Brak nowych alertów.")
        return
    for a in unread[:15]:
        tone = {"high": "bad", "medium": "warn", "low": "info"}[a["priority"]]
        st.markdown(
            ui.pill(a["label"], tone) + f" {ui.esc(a['message'])} · "
            f"{ui.esc(c.local_time(a['created_at'], '%d.%m %H:%M'))} · {ui.esc(a['source'])}",
            unsafe_allow_html=True,
        )
    if st.button("Oznacz wszystkie jako przeczytane", icon=":material/done_all:"):
        alerts.mark_read(db, [a["id"] for a in unread], now=c.now())
        st.rerun()


def _quality(db, now) -> None:
    ui.section(
        "Monitoring jakości danych kadrowych", "liczba pobrań nie jest miarą jakości prognoz"
    )
    checks = db.rows("SELECT status, requests, executed_at FROM prematch_checks")
    lineups = db.rows("SELECT event_id, status FROM lineup_observations")
    confirmed = {r["event_id"] for r in lineups if r["status"] in ("confirmed", "official")}
    ui.metric_cards(
        [
            (
                "Monitorowane mecze",
                str(len({r["event_id"] for r in lineups} | set()) or 0),
                "z obserwacją składu",
            ),
            ("Potwierdzone składy", str(len(confirmed)), "mecze"),
            ("Kontrole", str(len(checks)), f"zapytania API: {sum(r['requests'] for r in checks)}"),
            (
                "Pominięte / błędy",
                f"{sum(r['status'] == 'missed' for r in checks)} / "
                f"{sum(r['status'] == 'error' for r in checks)}",
                "kontrole",
            ),
            (
                "Zweryfikowane absencje",
                str(
                    db.scalar(
                        "SELECT COUNT(*) FROM player_absence_observations "
                        "WHERE match_status='verified'"
                    )
                ),
                "brak automatycznego źródła",
            ),
            (
                "Ponowne oceny kuponów",
                str(db.scalar("SELECT COUNT(*) FROM coupon_reassessments")),
                "zapisane zmiany",
            ),
        ]
    )
