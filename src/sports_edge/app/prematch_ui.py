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


def _alerts_list(db) -> None:
    unread = alerts.recent(db, limit=6, unread_only=True)
    if not unread:
        st.caption("Brak nowych alertów przedmeczowych.")
        return
    for a in unread:
        level = {"high": "bad", "medium": "warn"}.get(a["priority"], "info")
        when = c.local_time(a["created_at"], "%d.%m %H:%M")
        st.markdown(
            ui.pill(a["label"], level) + f" {ui.esc(a['message'])} · {ui.esc(when)}",
            unsafe_allow_html=True,
        )


def control_panel() -> None:
    """KONTROLA PRZED MECZEM on the Dashboard."""
    db, now = c.store(), c.now()
    found = prematch_overview(db, now=now)
    ui.section("Kontrola przed meczem", "składy i absencje meczów najbliższych 48 godzin")
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
                "Kupony wymagające uwagi",
                str(len(found["attention"])),
                "ostatnia kontrola: "
                + (
                    c.local_time(found["last_update"], "%d.%m %H:%M")
                    if found["last_update"]
                    else "—"
                ),
            ),
        ]
    )
    left, right = st.columns([3, 2], gap="large")
    with left:
        for item in found["attention"][:5]:
            coupon, last = item["coupon"], item["reassessment"]
            st.markdown(
                ui.pill(last["result"], "warn" if last["result"] == "wymaga uwagi" else "bad")
                + f" {ui.esc(coupon['note'] or coupon['kind'].upper())} — "
                + ui.esc("; ".join(last["reasons"][:2])),
                unsafe_allow_html=True,
            )
        _alerts_list(db)
    with right:
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
        st.caption(
            "Automatyczne kontrole (T-48 h … T-15 min) ustawisz w Automatyzacji AI. "
            "Prognozy nie uwzględniają składów — to informacja i ostrzeżenia."
        )
        c.page_link("automatyzacja", "Przejdź do Automatyzacji AI")


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
                        "Źródło": a["source"],
                        "Weryfikacja": "zweryfikowany"
                        if a["match_status"] == "verified"
                        else "do sprawdzenia",
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
            "zgaduje absencji."
        )
    st.info(MODEL_NOTE)
    return bool(sides or check)
