"""Streamlit entry point: streamlit run src/sports_edge/app/main.py (see start.bat)."""

import streamlit as st

from sports_edge.app import common as c
from sports_edge.app.pages_analysis import analysis_page, coupon_page
from sports_edge.app.pages_history import history_page
from sports_edge.app.pages_offers import comparison_page, dashboard, sts_page, superbet_page
from sports_edge.app.pages_settings import settings_page

st.set_page_config(page_title="Sports Edge AI", page_icon="📊", layout="wide")

with st.sidebar:
    st.markdown("### 📊 Sports Edge AI")
    st.caption("Lokalnie · bez obstawiania · paper trading")
    demo = st.toggle("Tryb DEMO (dane syntetyczne)", value=c.demo_mode())
    if demo != c.demo_mode():
        st.session_state["demo_mode"] = demo
        st.rerun()
    if c.demo_mode() and st.button("Załaduj dane DEMO"):
        c.load_demo_data()
        st.rerun()
    settings = c.settings()
    st.caption("🔌 Tryb offline" if settings.offline_mode else "🌐 Sieć tylko na żądanie")

pages = [
    st.Page(dashboard, title="Dashboard", icon="🏠", url_path="dashboard", default=True),
    st.Page(sts_page, title="STS", icon="🟥", url_path="sts"),
    st.Page(superbet_page, title="Superbet", icon="🟨", url_path="superbet"),
    st.Page(comparison_page, title="Porównanie", icon="⚖️", url_path="porownanie"),
    st.Page(analysis_page, title="Analiza AI", icon="🧠", url_path="analiza"),
    st.Page(coupon_page, title="Kupon dnia", icon="🎯", url_path="kupon"),
    st.Page(history_page, title="Historia", icon="📜", url_path="historia"),
    st.Page(settings_page, title="Ustawienia", icon="⚙️", url_path="ustawienia"),
]
st.navigation(pages).run()
