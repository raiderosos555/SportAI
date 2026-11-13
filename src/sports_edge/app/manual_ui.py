"""TASK 019 §1.3 / §1.5: "Wpisz wynik ręcznie (ze źródłem)" and the coupons waiting for a
result — one form and one list used by Wydarzenia i wyniki, the coupon details and the
Dashboard. The form writes append-only rows (``manual_results.record_manual``)."""

from datetime import timedelta

import streamlit as st

from sports_edge import manual_results as mr
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.services import normalizer, team_aliases, team_identities

FORM_TITLE = "Wpisz wynik ręcznie (ze źródłem)"


def _context() -> dict:
    paths = c.paths()
    return {
        "normalizer": normalizer(paths),
        "team_aliases": team_aliases(paths),
        "identities": team_identities(paths),
    }


def pending(db) -> list[dict]:
    """Coupons waiting for a result more than 24 h after their last match."""
    return mr.pending_coupons(db, now=c.now(), **_context())


def event_label(event: dict) -> str:
    return (
        f"{c.local_time(event['start_at'], '%d.%m %H:%M')} · {event['home']} - {event['away']}"
        f" · {event['league']}"
    )


def form(db, events: list[dict], *, key: str) -> None:
    """The manual result of one finished STS event (90 minutes, a source, the entry date)."""
    if not events:
        st.caption("Brak zakończonych meczów bez wyniku — nic do wpisania.")
        return
    labels = {e["id"]: event_label(e) for e in events}
    with st.form(f"manual_{key}"):
        st.markdown(f"**{FORM_TITLE}**")
        chosen = st.selectbox(
            "Mecz", list(labels), format_func=labels.get, key=f"manual_event_{key}"
        )
        cols = st.columns([1, 1, 2, 1])
        home = cols[0].number_input("Gospodarze (90 min)", 0, 99, 0, key=f"manual_h_{key}")
        away = cols[1].number_input("Goście (90 min)", 0, 99, 0, key=f"manual_a_{key}")
        source = cols[2].text_input(
            "Źródło (nazwa strony lub link)",
            key=f"manual_src_{key}",
            placeholder="np. uefa.com — raport meczowy",
        )
        entered = cols[3].date_input(
            "Data wpisu", value=c.today_local(), key=f"manual_d_{key}", format="DD.MM.YYYY"
        )
        st.caption(
            "Wynik po 90 minutach (bez dogrywki i karnych). Zapis jest trwały i oznaczony jako "
            "ręczny; późniejszy wynik od dostawcy ma pierwszeństwo, a różnica tworzy alert."
        )
        if st.form_submit_button("Zapisz wynik ręczny", type="primary"):
            try:
                found = mr.record_manual(
                    db,
                    event_id=chosen,
                    home_score=int(home),
                    away_score=int(away),
                    source=source,
                    entered_on=entered,
                    now=c.now(),
                    **_context(),
                )
            except ValueError as error:
                st.error(str(error))
                return
            if found["duplicate"]:
                st.info("Ten wynik z tym źródłem jest już zapisany — nic nie dodano.")
                return
            from sports_edge.collection import settle_predictions
            from sports_edge.services import settle_coupons

            settle_predictions(db, now=c.now())
            counts = settle_coupons(db, c.paths(), now=c.now())
            st.success(
                "Zapisano wynik ręczny. Rozliczenia: "
                + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
                + (
                    " · model zna ten mecz od teraz"
                    if found["history"]
                    else f" · {found['history_note']}"
                )
            )


def finished_events(db) -> list[dict]:
    return mr.manual_candidates(db, now=c.now())


def pending_block(db, *, key: str, with_form: bool = True) -> None:
    """The list "Kupony czekające na wynik": the missing leg, the expected result and
    source, the reason and the last attempt — and the manual form for those legs."""
    found = pending(db)
    if not found:
        st.caption("Brak kuponów czekających na wynik dłużej niż 24 h po ostatnim meczu.")
        return
    for coupon in found:
        lines = []
        for leg in coupon["missing"]:
            attempt = (
                f"{c.local_time(leg['last_attempt'], '%d.%m %H:%M')} ({_status(leg['last_status'])})"
                if leg["last_attempt"]
                else "brak próby"
            )
            lines.append(
                f"{leg['event']} ({c.local_time(leg['start_at'], '%d.%m %H:%M')}): brak — "
                f"{leg['expected']} {leg['source']}. Powód: {leg['reason']}. Ostatnia próba: "
                f"{attempt}."
            )
        ui.explanation(
            f"{coupon['label']} z {c.local_time(coupon['first_start_at'], '%d.%m')} — czeka "
            f"{_hours(coupon['hours_waiting'])} po ostatnim meczu",
            lines,
            tone="warn",
        )
    if with_form:
        events = {
            leg["event_id"]: leg
            for coupon in found
            for leg in coupon["missing"]
            if leg["manual_possible"]
        }
        rows = [db.get("events", eid) for eid in events]
        form(db, [r for r in rows if r], key=key)


def _hours(hours: float) -> str:
    if hours >= 48:
        return f"{hours / 24:.0f} dni"
    return f"{hours:.0f} h"


def _status(value: str | None) -> str:
    return c.JOB_STATUS.get(value or "", value or "—")


def history_form(db, *, key: str) -> None:
    """A national-team match outside the STS archive (e.g. September–October 2026)."""
    from sports_edge.datasources.competitions import COMPETITIONS, by_category
    from sports_edge.results_engine import effective_results

    codes = [code for code in by_category("national") if COMPETITIONS[code].nations]
    teams = sorted(
        {
            r[side]
            for r in effective_results(
                db, sport="football", until=c.now(), competitions=by_category("national")
            )
            for side in ("home", "away")
        }
    )
    if not teams:
        st.caption("Brak historii reprezentacji — wpis spoza oferty niedostępny.")
        return
    with st.form(f"manual_hist_{key}"):
        st.markdown("**Mecz reprezentacji spoza archiwum oferty (np. wrzesień–październik)**")
        cols = st.columns(3)
        competition = cols[0].selectbox(
            "Rozgrywki", codes, format_func=lambda k: COMPETITIONS[k].name, key=f"mh_c_{key}"
        )
        day = cols[1].date_input(
            "Data meczu",
            value=c.today_local() - timedelta(days=1),
            key=f"mh_d_{key}",
            format="DD.MM.YYYY",
        )
        source = cols[2].text_input("Źródło (nazwa strony lub link)", key=f"mh_s_{key}")
        cols = st.columns(4)
        home = cols[0].selectbox("Gospodarze", teams, key=f"mh_home_{key}")
        away = cols[1].selectbox(
            "Goście", teams, index=min(1, len(teams) - 1), key=f"mh_away_{key}"
        )
        home_score = cols[2].number_input("Gole gospodarzy (90 min)", 0, 99, 0, key=f"mh_hs_{key}")
        away_score = cols[3].number_input("Gole gości (90 min)", 0, 99, 0, key=f"mh_as_{key}")
        st.caption(
            "Drużyny wybierasz z historii wyników (nazwy jak w źródle). Miejsce meczu nie jest "
            "wpisywane — model liczy taki mecz bez przewagi gospodarza."
        )
        if st.form_submit_button("Zapisz mecz w historii"):
            try:
                mr.record_manual_history(
                    db,
                    competition=competition,
                    match_date=day,
                    home=home,
                    away=away,
                    home_score=int(home_score),
                    away_score=int(away_score),
                    source=source,
                    now=c.now(),
                )
            except ValueError as error:
                st.error(str(error))
                return
            st.success("Zapisano. Model uwzględni ten mecz przy następnej prognozie.")
