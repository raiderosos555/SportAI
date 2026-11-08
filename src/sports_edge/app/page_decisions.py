"""DECYZJE DO PODJĘCIA (TASK 015 §3): the five decisions waiting for the user in one place.

Modele i statystyki → widok „Decyzje do podjęcia” (the Dashboard shows the number of open
decisions and a link here). Every decision: what it changes, the evidence in numbers, the
risk and a button behind a confirmation; every click is a new row of the decision history —
nothing changes without the user's click.
"""

import pandas as pd
import streamlit as st

from sports_edge import decisions as dec
from sports_edge.analysis.edge_engine import BOUND_LABELS, BOUND_SINGLE, BOUND_SUM
from sports_edge.app import common as c
from sports_edge.app import ui

VIEW = "Decyzje do podjęcia"


@st.cache_data(ttl=600, show_spinner=False, max_entries=8)
def cached_option_d(_db, version: tuple, day: str, at) -> dict | None:
    try:
        return dec.option_d_today(_db, c.paths(), c.settings(), day, now=at)
    except (KeyError, ValueError):
        return None


@st.cache_data(ttl=600, show_spinner=False, max_entries=8)
def cached_panel(_db, version: tuple, at, live: dict | None) -> list[dict]:
    return dec.panel(_db, c.paths(), c.settings(), now=at, live_option_d=live)


def ten_minutes():
    """The current time rounded down to 10 minutes: the panel is recomputed when the data,
    the settings or the decisions change, otherwise at most every 10 minutes."""
    moment = c.minute()
    return moment.replace(minute=moment.minute - moment.minute % 10)


def settings_version() -> tuple:
    s = c.settings()
    return (s.elo_dc_bound, tuple(s.enabled_profiles or ()), s.max_profiles_per_selection)


def items(*, live: bool = True) -> list[dict]:
    """The five decisions (cached per data state, settings and minute)."""
    db = c.store()
    version = (
        c.evaluation_version(db)
        + settings_version()
        + (db.scalar("SELECT COUNT(*) FROM user_decisions"),)
    )
    found = None
    if live:
        from sports_edge.repository import offer_days

        today = str(c.today_local())
        days = [d for d in offer_days(db) if d >= today]
        if days:
            found = cached_option_d(db, version, days[0], ten_minutes())
    return cached_panel(db, version, ten_minutes(), found)


def dashboard_block() -> None:
    """The Dashboard: the number of open decisions and a link to the full panel."""
    found = items(live=False)
    waiting = [i for i in found if i["open"]]
    ui.section(
        "Decyzje do podjęcia",
        f"otwarte: {len(waiting)} z {len(found)} · system nie podejmuje ich za Ciebie",
    )
    if waiting:
        ui.pills([(i["title"], "warn") for i in waiting])
    else:
        st.caption("Brak otwartych decyzji — każdą możesz zmienić w panelu decyzji.")
    c.page_link("modele", "Przejdź do panelu decyzji", query_params={"widok": VIEW})


def decisions_panel() -> None:
    db = c.store()
    found = items()
    by_key = {i["key"]: i for i in found}
    ui.section(
        VIEW,
        f"otwarte: {dec.open_count(found)} z {len(found)} · każda decyzja to nowy wpis historii",
    )
    ui.explanation(
        "Zasady",
        [
            "Nic nie zmienia się bez Twojego kliknięcia — system nigdy nie podejmuje tych decyzji "
            "sam.",
            "Każda decyzja i jej cofnięcie to nowy wpis historii (kto, kiedy, co, poprzedni i "
            "nowy stan); nic nie jest nadpisywane.",
            "Dowody to liczby z pomiarów aplikacji (na żywo) i z raportów TASK 011–014 (z "
            "podanym źródłem). To nie jest rekomendacja zysku.",
        ],
    )
    for key in dec.KEYS:
        _item(db, by_key[key])
    ui.section("Historia decyzji", "każdy wpis zostaje — cofnięcie to kolejny wpis")
    rows = dec.history(db)
    if rows:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Kiedy": c.local_time(r["created_at"], "%d.%m.%Y %H:%M"),
                        "Decyzja": dec.TITLES.get(r["decision"], r["decision"]),
                        "Kto": "użytkownik",
                        "Co": r["summary"],
                        "Poprzednio": _state(r["previous"]),
                        "Teraz": _state(r["new"]),
                        "Gdzie": r["source"],
                    }
                    for r in reversed(rows)
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Brak zapisanych decyzji.")


def _state(value: dict) -> str:
    if "accepted" in value:
        return "zaakceptowana" if value["accepted"] else "niezaakceptowana"
    if "elo_dc_bound" in value:
        return BOUND_LABELS.get(value["elo_dc_bound"], value["elo_dc_bound"])
    if "enabled" in value:
        return "włączony" if value["enabled"] else "wyłączony"
    if "max_profiles_per_selection" in value:
        n = value["max_profiles_per_selection"]
        return "bez limitu" if not n else f"najwyżej {n}"
    return str(value)


def _item(db, item: dict) -> None:
    ui.section(item["title"], item["state"])
    ui.pills(
        [
            ("czeka na Twoją decyzję", "warn")
            if item["open"]
            else ("decyzja zapisana albo nic do decyzji", "ok")
        ]
    )
    st.markdown(
        ui.card(
            "<b>Co zmienia</b>"
            + ui.bullets([item["changes"]])
            + "<b>Dowody</b>"
            + ui.bullets(item["evidence"])
            + "<b>Ryzyko</b>"
            + ui.bullets([item["risk"]]),
            tone="warn" if item["open"] else "",
        ),
        unsafe_allow_html=True,
    )
    key = item["key"]
    if key == dec.GATE:
        _gate(db, item)
    elif key == dec.OPTION_D:
        _option_d(db, item)
    elif key == dec.SAFE_SOLO:
        _safe_solo(db, item)
    elif key == dec.LIMIT:
        _limit(db, item)
    else:
        st.caption(
            "Bez przycisku aktywacji przed progiem 300 wspólnych meczów — aktywacja (po progu) "
            "i szczegóły w widoku „Uczenie AI”."
        )
        c.page_link(
            "modele", "Panel challengerów (Uczenie AI)", query_params={"widok": "Uczenie AI"}
        )


def _done() -> None:
    st.cache_data.clear()
    st.rerun()


def _gate(db, item: dict) -> None:
    accepted = item["value"]
    choice = st.radio(
        "Twoja decyzja",
        ["Akceptuję bramkę v3", "Nie akceptuję (aktywacja v3 zablokowana)"],
        index=0 if accepted else 1,
        key="decide_gate_choice",
        horizontal=True,
    )
    sure = st.checkbox("Potwierdzam decyzję o bramce v3", key="decide_gate_sure")
    if st.button(
        "Zapisz decyzję o bramce v3",
        key="decide_gate_go",
        disabled=not sure,
        icon=":material/verified:",
    ):
        dec.decide_gate(db, c.paths(), accept=choice.startswith("Akceptuję"), now=c.now())
        _done()


def _option_d(db, item: dict) -> None:
    options = [BOUND_SUM, BOUND_SINGLE]
    choice = st.radio(
        "Definicja granicy „ostrożnie” dla podwójnej szansy Elo",
        options,
        index=options.index(item["value"]),
        format_func=lambda v: BOUND_LABELS[v],
        key="decide_option_d_choice",
    )
    sure = st.checkbox(
        "Potwierdzam definicję (zmiana w ciągu dnia daje rewizję, kupon oficjalny zostaje)",
        key="decide_option_d_sure",
    )
    if st.button(
        "Zapisz decyzję o opcji D",
        key="decide_option_d_go",
        disabled=not sure,
        icon=":material/rule:",
    ):
        dec.decide_option_d(db, c.paths(), c.settings(), value=choice, now=c.now())
        _done()


def _safe_solo(db, item: dict) -> None:
    on = st.toggle("Profil PEWNY SOLO włączony", value=item["value"], key="decide_safe_choice")
    sure = st.checkbox("Potwierdzam decyzję o profilu PEWNY SOLO", key="decide_safe_sure")
    if st.button(
        "Zapisz decyzję o PEWNYM SOLO",
        key="decide_safe_go",
        disabled=not sure,
        icon=":material/looks_one:",
    ):
        dec.decide_safe_solo(db, c.paths(), c.settings(), enabled=on, now=c.now())
        _done()


def _limit(db, item: dict) -> None:
    value = st.number_input(
        "Ta sama selekcja najwyżej w N kuponach dnia (0 = bez limitu)",
        min_value=0,
        max_value=4,
        value=int(item["value"]),
        key="decide_limit_choice",
    )
    sure = st.checkbox("Potwierdzam limit różnicowania", key="decide_limit_sure")
    if st.button(
        "Zapisz decyzję o limicie",
        key="decide_limit_go",
        disabled=not sure,
        icon=":material/filter_alt:",
    ):
        dec.decide_limit(db, c.paths(), c.settings(), value=int(value), now=c.now())
        _done()
