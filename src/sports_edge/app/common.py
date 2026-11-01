"""Shared state, formatting and styling for the Streamlit pages."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from sports_edge.datasources.football_data import ATTRIBUTION
from sports_edge.demo import demo_document
from sports_edge.offers.labels import MARKETS, PERIODS, SELECTIONS, market_label
from sports_edge.repository import ensure_reference_data, event_status, odds_freshness
from sports_edge.services import import_pdf_bytes, open_store
from sports_edge.settings import AppPaths, AppSettings, load_settings, project_root
from sports_edge.storage import Store

WARSAW = ZoneInfo("Europe/Warsaw")
FRESHNESS = {
    "fresh": "🟢 świeża migawka",
    "unconfirmed": "🟠 kurs niepotwierdzony",
    "unknown": "⚪ nieznany czas obserwacji",
}
EVENT_STATUS = {
    "before_start": "⏳ przed rozpoczęciem",
    "in_play": "▶ w trakcie",
    "finished": "🏁 zakończone",
    "postponed": "↪ przełożone",
    "cancelled": "✖ anulowane",
    "unknown": "❔ nieznany",
}
PRESENCE = {
    "current": "potwierdzony w najnowszej ofercie",
    "withdrawn": "⛔ wycofany",
    "unconfirmed": "❔ niepotwierdzony",
}
__all__ = ["MARKETS", "PERIODS", "SELECTIONS", "market_label"]
TIERS = {
    "validated": "✅ zwalidowany",
    "experimental": "🧪 eksperymentalny",
    "rejected": "odrzucony",
}

CSS = """
<style>
:root { --se-accent: #22c55e; --se-warn: #f59e0b; --se-bad: #ef4444; --se-card: #111a2e; }
.block-container { padding-top: 1.6rem; max-width: 1500px; }
div[data-testid="stMetric"] { background: var(--se-card); border: 1px solid #1f2a44;
  border-radius: 12px; padding: 12px 16px; }
.se-banner { border-radius: 12px; padding: 16px 20px; margin: 8px 0 16px 0; font-weight: 600; }
.se-noedge { background: #1f1220; border: 1px solid #7f1d1d; color: #fecaca; font-size: 1.25rem; }
.se-demo { background: #2a1f05; border: 1px solid #a16207; color: #fde68a; }
.se-info { background: #0f1f33; border: 1px solid #1e3a5f; color: #bfdbfe; }
.se-ok { background: #0c2416; border: 1px solid #166534; color: #bbf7d0; }
.se-pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.8rem;
  margin-right: 6px; border: 1px solid #334155; background: #0f172a; }
</style>
"""


def banner(text: str, kind: str = "info") -> None:
    st.markdown(f'<div class="se-banner se-{kind}">{text}</div>', unsafe_allow_html=True)


def pills(*items: str) -> None:
    st.markdown("".join(f'<span class="se-pill">{i}</span>' for i in items), unsafe_allow_html=True)


def now() -> datetime:
    return datetime.now(UTC)


def local_time(value: str | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(WARSAW).strftime(fmt)


def today_local() -> date:
    return now().astimezone(WARSAW).date()


def paths() -> AppPaths:
    return AppPaths(project_root())


def settings() -> AppSettings:
    return load_settings(paths())


def demo_mode() -> bool:
    return bool(st.session_state.get("demo_mode", False))


def data_kinds() -> tuple[str, ...]:
    return ("demo",) if demo_mode() else ("prospective", "archive")


def store() -> Store:
    """One connection per browser session and database file (migration backs up first)."""
    demo = demo_mode()
    target = paths().demo_database if demo else paths().database
    key = f"store::{target}"
    if key not in st.session_state:
        if demo:
            db = Store(target)
            ensure_reference_data(db)
        else:
            db = open_store(paths())
        st.session_state[key] = db
    return st.session_state[key]


def load_demo_data() -> dict:
    """Synthetic DEMO offer (STS-like layout) stored only in data/demo/demo.db."""
    day = today_local() + timedelta(days=1)
    generated = now().astimezone(WARSAW).replace(tzinfo=None)
    outcome = import_pdf_bytes(
        store(),
        paths(),
        settings(),
        demo_document(day, generated),
        file_name="DEMO_sts.pdf",
        now=now(),
        source_id="fixture",
        data_kind="demo",
    )
    return outcome.summary


def setup_page() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.sidebar.caption(f"Wyniki bieżącego sezonu: {ATTRIBUTION}.")
    if demo_mode():
        banner(
            "TRYB DEMO — dane syntetyczne z fikcyjnymi drużynami i kursami. "
            "To nie jest oferta STS i nie wolno jej analizować jak danych rzeczywistych.",
            "demo",
        )


def odds_text(selections: dict) -> str:
    order = list(SELECTIONS)
    keys = sorted(selections, key=lambda k: order.index(k) if k in order else 99)
    return " · ".join(f"{SELECTIONS.get(k, k)} {selections[k]['price']}" for k in keys)


def freshness_label(state: str, age_hours: float | None) -> str:
    if state == "unconfirmed" and age_hours is not None:
        return f"🟠 Kurs niepotwierdzony od {age_hours:.0f} h"
    return FRESHNESS.get(state, state)


def offers_frame(
    rows: list[dict], *, wide: bool, now: datetime, max_age: timedelta
) -> pd.DataFrame:
    """Offer rows with the market's period and line, the PDF description and three separate
    statuses: event, price snapshot age and presence in the newest relevant offer."""
    records = []
    for row in rows:
        state, age = odds_freshness(row["valid_at"], now, max_age)
        event = event_status(
            row["start_at"], now, sport=row["sport"], result_status=row.get("result_status")
        )
        record = {
            "Start": local_time(row["start_at"]),
            "Sport": (row["sport_raw"] or row["sport"]).capitalize(),
            "Liga": row["league"],
            "Wydarzenie": " - ".join(row["participants"]) or row["name_raw"],
            "Rynek": market_label(row["market_key"], row["period"], row["line"]),
            "Opis w PDF": row["label_raw"],
            "Nr zakładu": row["offer_code"] or "",
        }
        if wide:
            for key, quote in row["selections"].items():
                record[SELECTIONS.get(key, key)] = float(quote["price"])
        else:
            record["Kursy"] = odds_text(row["selections"])
        record.update(
            {
                "Jakość": row["quality"],
                "Odczyt": "✅ ok" if row["status"] == "ok" else "⚠️ weryfikacja",
                "Status wydarzenia": EVENT_STATUS.get(event, event),
                "Świeżość kursu": freshness_label(state, age),
                "Dokument z": local_time(row["valid_at"]),
                "Pobrano": local_time(row["fetched_at"])
                if row.get("fetched_at")
                else "plik ręczny",
                "Rynek w ofercie": PRESENCE.get(row.get("presence", "current"), "—"),
                "Strona": row["page"],
            }
        )
        records.append(record)
    return pd.DataFrame.from_records(records)
