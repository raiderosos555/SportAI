"""Pre-match information in the interface: the Dashboard control panel, the lineups panel of
a match and the short status of a coupon leg. Everything is read from stored observations;
the only network actions are the explicit buttons (a worker command or one match check)."""

from datetime import datetime

import pandas as pd
import streamlit as st

from sports_edge import alerts, automation, prematch
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.services import prematch_overview, run_prematch

LEVEL = {
    "confirmed": "ok",
    "official": "ok",
    "predicted": "warn",
    "not_published": "neutral",
    "no_data": "neutral",
    "error": "bad",
}
MODEL_NOTE = (
    "Czy model wykorzystał tę informację? Nie — Elo i Dixon–Coles liczą szanse z wyników "
    "meczów. Składy i absencje są pokazywane jako ostrzeżenie (Etap A) i nie zmieniają procentów."
)


def lineup_pill(info: dict) -> str:
    return ui.pill("Skład: " + info["lineup_label"], LEVEL.get(info["lineup_status"], "neutral"))


def event_of_odds(db, odds_id: str | None) -> str | None:
    """Event of a stored coupon leg (proposals saved before TASK 008 have no event id)."""
    if not odds_id:
        return None
    rows = db.rows(
        """SELECT m.event_id FROM odds o JOIN selections s ON s.id=o.selection_id
        JOIN markets m ON m.id=s.market_id WHERE o.id=?""",
        (odds_id,),
    )
    return rows[0]["event_id"] if rows else None


def leg_status(db, event_id: str | None) -> str:
    """One line for a coupon leg: lineup status, changes, absences and the last check."""
    if not event_id:
        return "składy: brak danych"
    info = prematch.summary(db, event_id)
    check = info["last_check"]
    verified = [a for a in info["absences"] if a["match_status"] == "verified"]
    parts = [f"skład: {info['lineup_label']}"]
    if info["changes"]:
        parts.append("zmiany: " + "; ".join(x["detail"] for x in info["changes"][:3]))
    parts.append(
        f"absencje: {len(verified)} zweryfikowane" if verified else "absencje: brak źródła"
    )
    parts.append(
        f"kontrola: {c.local_time(check['executed_at'], '%d.%m %H:%M')}"
        if check
        else "kontrola: jeszcze nie wykonana"
    )
    return " · ".join(parts)


PRIORITY_TONE = {"high": "bad", "medium": "warn"}


def attention_block(db, now) -> None:
    """Dashboard level 1: coupons that need attention, new alerts, one check button."""
    found = prematch_overview(db, now=now)
    rows = []
    for item in found["attention"][:4]:
        coupon, last = item["coupon"], item["reassessment"]
        rows.append(
            ui.list_row(
                title=coupon["note"] or coupon["kind"].upper(),
                meta="; ".join(last["reasons"][:2]),
                badges=[
                    ui.pill(last["result"], "warn" if last["result"] == "wymaga uwagi" else "bad")
                ],
                tone="warn",
            )
        )
    # TASK 016 §4: important first (coupons, then lineups of coupon matches); the rest and
    # the alerts of matches that already started are one line with a link
    overview = alerts.overview(db, now=now)
    unread = [a for a in overview["items"] if not a["read"]]
    important = [a for a in unread if a["rank"] <= 1 and not a["stale"]]
    minor = [a for a in unread if a["rank"] > 1 and not a["stale"]]
    stale = [a for a in unread if a["stale"]]
    for a in important[:5]:
        rows.append(
            ui.list_row(
                title=a["message"],
                meta=c.local_time(a["created_at"], "%d.%m %H:%M") + f" · źródło: {a['source']}",
                badges=[ui.pill(a["label"], PRIORITY_TONE.get(a["priority"], "info"))],
                tone=PRIORITY_TONE.get(a["priority"], "info"),
            )
        )
    if len(important) > 5:
        rows.append(
            ui.list_row(
                title=f"Kolejne ważne alerty: {len(important) - 5}",
                meta="pełna lista w Automatyzacji AI → Alerty",
                badges=[ui.pill("ważne", "warn")],
            )
        )
    if minor or stale:
        kinds = sorted({a["label"] for a in minor})
        rows.append(
            ui.list_row(
                title=f"Pozostałe alerty: {len(minor)} · nieaktualne (mecz już się rozpoczął): "
                f"{len(stale)}",
                meta=(", ".join(kinds[:3]) + " — " if kinds else "")
                + "lista i „Oznacz wszystkie jako przeczytane” w Automatyzacji AI → Alerty",
                badges=[ui.pill("informacja", "info")],
            )
        )
    if rows:
        ui.rows(rows)
    else:
        ui.empty_state(
            "Nic nie wymaga uwagi",
            "Brak nowych alertów i kuponów ze zmianami w składach. Kontrole przed meczem "
            "(T-48 h … T-15 min) wykonuje worker Automatyzacji AI.",
        )
    if st.button(
        "SPRAWDŹ WSZYSTKIE MOJE KUPONY TERAZ",
        type="primary",
        icon=":material/fact_check:",
        width="stretch",
        disabled=c.demo_mode(),
        help="Zleca workerowi kontrolę składów meczów z niezakończonych kuponów i ich "
        "ponowną ocenę (PitchAPI: do 2 zapytań na mecz).",
    ):
        st.success(automation.request(c.paths(), db, "check_coupons", now=now))
    c.page_link("automatyzacja", "Harmonogram kontroli w Automatyzacji AI")


def overview_metrics(db, now) -> None:
    """Level 3: numbers of the pre-match monitoring (next 48 hours)."""
    found = prematch_overview(db, now=now)
    ui.metric_cards(
        [
            (
                "Monitorowane mecze",
                str(found["monitored"]),
                f"w tym z Twoich kuponów: {found['on_coupons']}",
            ),
            ("Składy potwierdzone", str(found["confirmed"]), "przez dostawcę danych"),
            ("Składy przewidywane", str(found["predicted"]), "prognoza dostawcy, nie pewność"),
            ("Bez danych o składzie", str(found["without_data"]), "jeszcze nieopublikowane"),
            ("Nowe zmiany w składach", str(found["changes"]), "od poprzedniej kontroli"),
            (
                "Ostatnia kontrola",
                c.local_time(found["last_update"], "%d.%m %H:%M") if found["last_update"] else "—",
                f"kupony wymagające uwagi: {len(found['attention'])}",
            ),
        ]
    )


def _side_table(side: dict) -> pd.DataFrame:
    rows = [
        {"Zawodnik": p.get("name") or "?", "Rola": p.get("role") or "—", "Skład": "wyjściowy"}
        for p in side["starters"]
    ] + [
        {"Zawodnik": p.get("name") or "?", "Rola": p.get("role") or "—", "Skład": "ławka"}
        for p in side["subs"]
    ]
    return pd.DataFrame(rows)


def lineups_panel(db, event_id: str, *, start_at: str | None, now, started: bool) -> bool:
    """The "Składy i absencje" tab; returns False when nothing new-engine is stored."""
    if not started and st.button(
        "Sprawdź składy teraz (PitchAPI, do 2 zapytań)",
        key=f"prematch_{event_id}",
        icon=":material/groups:",
        disabled=c.demo_mode(),
    ):
        out = run_prematch(
            db, c.paths(), c.settings(), now=now, trigger="user", event_ids=[event_id]
        )
        if out.get("blocked"):
            st.info(f"Źródło składów niedostępne: {out['blocked']}")
        else:
            st.toast(
                f"Sprawdzono — zmiany: {out['changes']}, zapytania: {out['requests']}",
                icon=":material/groups:",
            )
    info = prematch.summary(db, event_id)
    check = info["last_check"]
    st.markdown(lineup_pill(info), unsafe_allow_html=True)
    if check:
        st.caption(
            f"Ostatnia kontrola: {c.local_time(check['executed_at'])} ({check['checkpoint']}, "
            f"{check['trigger']}) · status: {check['status']}"
            + (f" · {check['detail']}" if check["detail"] else "")
        )
    sides = [s for s in ("home", "away") if info["sides"][s]]
    if sides:
        start = datetime.fromisoformat(start_at) if start_at else None
        cols = st.columns(2)
        for col, side in zip(cols, ("home", "away"), strict=True):
            row = info["sides"][side]
            with col:
                if not row:
                    st.caption("Brak składu tej drużyny.")
                    continue
                keeper = (prematch.goalkeeper(row) or {}).get("name") or "—"
                st.markdown(
                    f"**{row['team_name']}** · {row['formation'] or '—'} · "
                    f"{prematch.LINEUP_STATUS.get(row['status'], row['status'])}"
                )
                st.caption(
                    f"Bramkarz: {keeper} · trener: {row['coach'] or '—'} · źródło: "
                    f"{row['provider']} · obserwacja {c.local_time(row['observed_at'])}"
                    + (
                        " (po rozpoczęciu — informacja pomeczowa)"
                        if start is not None and datetime.fromisoformat(row["observed_at"]) >= start
                        else ""
                    )
                )
                st.dataframe(_side_table(row), hide_index=True, width="stretch", height=280)
    if info["changes"]:
        ui.explanation(
            "Zmiany od poprzedniej obserwacji",
            [x["detail"] for x in info["changes"]],
            tone="warn",
        )
    absences = info["absences"]
    if absences:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Zawodnik": a["player_name"],
                        "Drużyna": a["team_name"],
                        "Powód": prematch.ABSENCE_REASON.get(a["reason"], a["reason"]),
                        "Źródło": a["source"]
                        + (f" ({a['source_url']})" if a.get("source_url") else ""),
                        "Data informacji": a.get("published_at") or "—",
                        "Weryfikacja": prematch.verification_label(a),
                        "Znane przed meczem": "tak" if a["known_before_start"] else "nie",
                    }
                    for a in absences
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption(
            "Kontuzje i zawieszenia: brak zweryfikowanego, bezpłatnego źródła — aplikacja nie "
            "zgaduje absencji. Możesz wpisać absencję ręcznie, podając źródło."
        )
    absence_form(db, event_id, absences, now=now)
    st.info(MODEL_NOTE)
    return bool(sides or check)


ABSENCE_REASONS = ("kontuzja", "zawieszenie", "inne")


def absence_form(db, event_id: str, absences: list[dict], *, now) -> None:
    """TASK 016 §5.3: a manual absence — team, player, reason, obligatory source, date. The
    entry is append-only; withdrawing it adds another row. A warning, never a model input."""
    event = db.get("events", event_id)
    if event is None or not event.get("home") or not event.get("away"):
        return
    with ui.details("Dodaj absencję ręcznie (ze źródłem)", icon=":material/person_off:"):
        with st.form(f"absence_form_{event_id}", clear_on_submit=True):
            cols = st.columns(2)
            team = cols[0].selectbox(
                "Drużyna", [event["home"], event["away"]], key=f"absence_team_{event_id}"
            )
            player = cols[1].text_input("Zawodnik", key=f"absence_player_{event_id}")
            cols = st.columns(2)
            reason = cols[0].selectbox("Powód", ABSENCE_REASONS, key=f"absence_reason_{event_id}")
            day = cols[1].date_input(
                "Data informacji",
                value=now.astimezone(c.WARSAW).date(),
                format="DD.MM.YYYY",
                key=f"absence_date_{event_id}",
            )
            source = st.text_input(
                "Źródło (obowiązkowe)",
                key=f"absence_source_{event_id}",
                placeholder="np. komunikat klubu, konferencja trenera, oficjalna strona ligi",
            )
            link = st.text_input(
                "Adres źródła (opcjonalnie)", key=f"absence_url_{event_id}", placeholder="https://…"
            )
            sent = st.form_submit_button("Zapisz absencję", icon=":material/save:")
        if sent:
            try:
                prematch.add_user_absence(
                    db,
                    event,
                    team=team,
                    player=player,
                    reason=reason,
                    source=source,
                    source_url=link,
                    information_date=day.isoformat() if day else None,
                    now=now,
                )
            except ValueError as error:
                st.error(str(error))
            else:
                st.cache_data.clear()
                st.success(
                    "Absencja zapisana — widoczna w kontroli przedmeczowej i w ocenie kuponu "
                    "jako zweryfikowana przez użytkownika. Nie zmienia żadnej prognozy."
                )
                st.rerun()
        mine = [a for a in absences if a["entered_by"] == "user"]
        for row in mine:
            cols = st.columns([4, 1], vertical_alignment="center")
            cols[0].caption(
                f"{row['player_name']} ({row['team_name']}) — "
                f"{prematch.ABSENCE_REASON.get(row['reason'], row['reason'])} · źródło: "
                f"{row['source']} · wpis {c.local_time(row['observed_at'], '%d.%m %H:%M')}"
            )
            if cols[1].button("Wycofaj", key=f"absence_withdraw_{row['id']}"):
                prematch.withdraw_absence(db, row["id"], now=now)
                st.cache_data.clear()
                st.rerun()
        st.caption(
            "Wpis jest dopisywany (nic nie jest nadpisywane); wycofanie to kolejny wpis. "
            "Absencje są ostrzeżeniem — nie wchodzą do modelu."
        )
