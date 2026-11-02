"""Wydarzenia i wyniki: upcoming STS events, finished matches with results, the full offer."""

from collections import defaultdict

import pandas as pd
import streamlit as st

from sports_edge.app import cart, ui
from sports_edge.app import common as c
from sports_edge.app.page_my_coupon import event_panel
from sports_edge.app.pages_offers import offer_content
from sports_edge.repository import offer_days


def events_page() -> None:
    c.setup_page()
    ui.app_header("Wydarzenia i wyniki", "Terminarz oferty STS, zakończone mecze i pełna oferta")
    tabs = st.tabs(["Terminarz", "Zakończone i wyniki", "Oferta i kursy STS"])
    with tabs[0]:
        _schedule()
    with tabs[1]:
        _finished()
    with tabs[2]:
        offer_content()


def _schedule() -> None:
    db = c.store()
    days = offer_days(db)
    if not days:
        ui.empty_state("Brak oferty STS", "Zaimportuj PDF z ofertą STS.")
        return
    cols = st.columns([2, 1, 2])
    with cols[0]:
        day = c.offer_day_choice(days, key="schedule_day")
    _report, candidates = c.day_candidates(day)
    events = defaultdict(list)
    for x in candidates:
        events[x.event_id].append(x)
    sports = sorted({rows[0].sport for rows in events.values()})
    sport = cols[1].selectbox("Dyscyplina", ["wszystkie", *sports], key="schedule_sport")
    leagues = sorted(
        {rows[0].league for rows in events.values() if sport in ("wszystkie", rows[0].sport)}
    )
    league = cols[2].selectbox("Liga", ["wszystkie", *leagues], key="schedule_league")
    table = []
    for eid, rows in events.items():
        head = rows[0]
        if sport != "wszystkie" and head.sport != sport:
            continue
        if league != "wszystkie" and head.league != league:
            continue
        table.append(
            {
                "Godzina": c.local_time(head.start_at, "%H:%M"),
                "Wydarzenie": head.event,
                "Liga": head.league,
                "Dyscyplina": head.sport,
                "Status": c.EVENT_STATUS.get(head.event_status, head.event_status),
                "Prognoza modelu": "tak" if any(r.p_model is not None for r in rows) else "nie",
                "Rynki": len({r.market_id for r in rows}),
                "Kurs STS": "potwierdzony" if head.freshness == "fresh" else "niepotwierdzony",
                "_start": head.start_at or "",
                "_id": eid,
            }
        )
    if not table:
        ui.empty_state("Brak wydarzeń", "Zmień filtry albo dzień.")
        return
    upcoming = [r for r in table if r["Status"] == c.EVENT_STATUS["before_start"]]
    ui.metric_cards(
        [
            ("Wydarzenia", str(len(table)), c.day_label(day)),
            ("Przed rozpoczęciem", str(len(upcoming)), "kandydaci do analizy"),
            (
                "Z prognozą modelu",
                str(sum(r["Prognoza modelu"] == "tak" for r in table)),
                "obsługiwane ligi",
            ),
        ]
    )
    ordered = sorted(table, key=lambda r: r["_start"])
    frame = pd.DataFrame(ordered).drop(columns=["_start", "_id"])
    cart.flash()
    picked = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=min(38 * (len(frame) + 1) + 4, 560),
        on_select="rerun",
        selection_mode="single-row",
        key=f"schedule_table_{day}",
    )
    st.caption("Kliknij wiersz, aby zobaczyć rynki i dodać typ do Mojego kuponu.")
    if picked.selection.rows:
        chosen = ordered[picked.selection.rows[0]]["_id"]
        ui.section("Rynki wydarzenia", events[chosen][0].event)
        event_panel(events[chosen], day)


def _finished() -> None:
    db = c.store()
    rows = db.rows(
        """SELECT e.id event_id, e.home, e.away, e.league, e.sport, e.start_at, r.home_score,
        r.away_score, r.ht_home, r.ht_away, r.status, r.source, r.verified, r.recorded_at
        FROM events e JOIN results r ON r.event_id=e.id WHERE e.bookmaker_id='sts'
        ORDER BY r.recorded_at DESC"""
    )
    latest = {}
    for row in rows:
        latest.setdefault(row["event_id"], row)
    if not latest:
        ui.empty_state(
            "Brak zapisanych wyników",
            "Wyniki pojawiają się po aktualizacji danych sportowych (Kupon dnia → Analizuj) "
            "albo po ręcznym wpisie w Historii.",
        )
        return
    status = {
        "completed": "zakończony",
        "postponed": "przełożony",
        "cancelled": "odwołany",
        "void": "unieważniony",
        "abandoned": "przerwany",
    }
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Data": c.local_time(r["start_at"], "%d.%m %H:%M"),
                    "Mecz": f"{r['home']} – {r['away']}",
                    "Wynik": f"{r['home_score']}:{r['away_score']}"
                    if r["home_score"] is not None
                    else "—",
                    "Do przerwy": f"{r['ht_home']}:{r['ht_away']}"
                    if r["ht_home"] is not None
                    else "—",
                    "Status": status.get(r["status"], r["status"]),
                    "Liga": r["league"],
                    "Źródło": r["source"],
                    "Zweryfikowany": "tak" if r["verified"] else "nie",
                }
                for r in sorted(latest.values(), key=lambda r: r["start_at"] or "", reverse=True)[
                    :300
                ]
            ]
        ),
        hide_index=True,
        width="stretch",
        height=520,
    )
    st.caption(
        "Wynik w kolejności gospodarze – goście (czas regulaminowy). Korekty wyników tworzą nowe "
        "rekordy; prognozy sprzed meczu nigdy się nie zmieniają."
    )
