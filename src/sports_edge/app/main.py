"""Streamlit entry point: streamlit run src/sports_edge/app/main.py (see start.bat). STS ONLY."""

import streamlit as st

from sports_edge import insights as ins
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.app.page_coupon import coupon_page
from sports_edge.app.page_dashboard import dashboard_page
from sports_edge.app.page_events import events_page
from sports_edge.app.page_match import match_page
from sports_edge.app.page_models import models_page
from sports_edge.app.page_sources import sources_page
from sports_edge.app.pages_history import history_page
from sports_edge.repository import imports
from sports_edge.services import active_versions

st.set_page_config(
    page_title="Sports Edge AI",
    page_icon=":material/insights:",
    layout="wide",
    initial_sidebar_state="auto",
)

pages = {
    "dashboard": st.Page(
        dashboard_page,
        title="Dashboard",
        icon=":material/space_dashboard:",
        url_path="dashboard",
        default=True,
    ),
    "kupon": st.Page(
        coupon_page, title="Kupon dnia", icon=":material/receipt_long:", url_path="kupon"
    ),
    "analiza": st.Page(
        match_page, title="Analiza meczów", icon=":material/query_stats:", url_path="analiza"
    ),
    "wydarzenia": st.Page(
        events_page, title="Wydarzenia i wyniki", icon=":material/event:", url_path="wydarzenia"
    ),
    "historia": st.Page(
        history_page, title="Historia i skuteczność", icon=":material/history:", url_path="historia"
    ),
    "modele": st.Page(
        models_page, title="Modele i statystyki", icon=":material/monitoring:", url_path="modele"
    ),
    "zrodla": st.Page(
        sources_page, title="Źródła i ustawienia", icon=":material/tune:", url_path="zrodla"
    ),
}
st.session_state["pages"] = pages


def sidebar() -> None:
    with st.sidebar:
        ui.inject()
        ui.brand()
        db = c.store()
        settings = c.settings()
        rows = [i for i in imports(db) if i["data_kind"] in c.data_kinds()]
        newest = max((i["generated_at"] or "" for i in rows if i["rows_total"]), default="")
        freshness = c.cached_freshness(db, c.data_version(db), c.minute())
        served = active_versions(db)
        last_run = db.scalar("SELECT MAX(created_at) FROM analyses")
        age = None
        if newest:
            from datetime import datetime

            age = (c.now() - datetime.fromisoformat(newest)).total_seconds() / 3600
        ui.side_rows(
            [
                ("Oferta STS", f"{ins.num(age, 1)} h temu" if age is not None else "brak"),
                ("Wyniki lig", f"aktualne {sum(f['fresh'] for f in freshness)}/{len(freshness)}"),
                ("Modele", f"{sum(1 for p in served.values() if p['elo'] or p['goals'])} zakresy"),
                ("Ostatnia analiza", c.local_time(last_run, "%d.%m %H:%M") if last_run else "—"),
                ("Tryb", "offline" if settings.offline_mode else "online (na żądanie)"),
            ]
        )
        if age is not None and age > settings.max_quote_age_minutes / 60:
            ui.pills([("Kursy STS wymagają odświeżenia", "warn")])
        demo = st.toggle("Tryb DEMO (dane syntetyczne)", value=c.demo_mode())
        if demo != c.demo_mode():
            st.session_state["demo_mode"] = demo
            st.rerun()
        if c.demo_mode() and st.button("Załaduj dane DEMO"):
            c.load_demo_data()
            st.rerun()


try:
    sidebar()
except Exception as error:  # noqa: BLE001 — a broken status must never hide the pages
    st.sidebar.warning(f"Status danych niedostępny: {error}")
st.navigation(list(pages.values())).run()
