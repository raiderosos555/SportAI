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
    "fresh": "potwierdzony",
    "unconfirmed": "niepotwierdzony",
    "unknown": "nieznany czas obserwacji",
}
EVENT_STATUS = {
    "before_start": "przed rozpoczęciem",
    "in_play": "w trakcie",
    "finished": "zakończony",
    "postponed": "przełożony",
    "cancelled": "odwołany",
    "unknown": "status nieznany",
}
EVENT_TONE = {
    "before_start": "info",
    "in_play": "warn",
    "finished": "neutral",
    "postponed": "warn",
    "cancelled": "bad",
    "unknown": "neutral",
}
PRESENCE = {
    "current": "potwierdzony w najnowszej ofercie",
    "withdrawn": "wycofany",
    "unconfirmed": "niepotwierdzony",
}
__all__ = ["MARKETS", "PERIODS", "SELECTIONS", "market_label"]
TIERS = {
    "validated": "zwalidowany",
    "experimental": "eksperymentalny",
    "rejected": "odrzucony",
}


def banner(text: str, kind: str = "info") -> None:
    """Highlighted message in the design system (status colour only when it means something)."""
    from sports_edge.app import ui

    tone = {"noedge": "bad", "ok": "accent", "demo": "warn"}.get(kind, "")
    st.markdown(ui.card(f"<b>{ui.esc(text)}</b>", tone=tone), unsafe_allow_html=True)


def pills(*items: str) -> None:
    from sports_edge.app import ui

    ui.pills([(item, "neutral") for item in items])


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
    from sports_edge.app import ui

    ui.inject()
    st.sidebar.caption(f"Wyniki bieżącego sezonu: {ATTRIBUTION}.")  # required attribution
    if demo_mode():
        ui.empty_state(
            "Tryb DEMO",
            "Dane syntetyczne z fikcyjnymi drużynami i kursami. To nie jest oferta STS i nie "
            "wolno jej analizować jak danych rzeczywistych.",
            tone="warn",
        )


# ---------------------------------------------------------------- caching (TASK 006)


def data_version(db: Store) -> tuple:
    """Cheap fingerprint of the database: cached views are rebuilt when any data changes."""
    return tuple(
        db.scalar(sql) or ""
        for sql in (
            "SELECT MAX(ingested_at) FROM imports",
            "SELECT MAX(observed_at) FROM historical_results",
            "SELECT MAX(created_at) FROM predictions",
            "SELECT COUNT(*) FROM coupons",
            "SELECT COUNT(*) FROM settlements",
            "SELECT COUNT(*) FROM results",
            "SELECT MAX(created_at) FROM model_versions",
            "SELECT COUNT(*) FROM stat_observations",
        )
    )


def minute() -> datetime:
    """Current time rounded down to the minute (one cache entry per minute)."""
    return now().replace(second=0, microsecond=0)


@st.cache_data(ttl=600, show_spinner=False, max_entries=64)
def cached_day(_db: Store, version: tuple, day: str, at: datetime, kinds: tuple[str, ...]):
    from sports_edge.services import analyse_day

    return analyse_day(_db, paths(), settings(), day, now=at, data_kinds=kinds)


@st.cache_data(ttl=600, show_spinner=False, max_entries=16)
def cached_freshness(_db: Store, version: tuple, at: datetime) -> list[dict]:
    from sports_edge.results_engine import league_freshness
    from sports_edge.services import RESEARCH_COMPETITIONS

    return league_freshness(_db, RESEARCH_COMPETITIONS, now=at)


@st.cache_data(ttl=600, show_spinner=False, max_entries=16)
def cached_coverage(_db: Store, version: tuple, days: tuple[str, ...], at: datetime) -> dict:
    from sports_edge.coverage import coverage_report

    return coverage_report(_db, paths(), settings(), list(days), now=at, data_kinds=data_kinds())


def day_candidates(day: str):
    """(report, candidates) of an offer day — cached per minute and database state."""
    db = store()
    return cached_day(db, data_version(db), day, minute(), data_kinds())


def upcoming_days(days: list[str]) -> list[str]:
    """Offer days that still have at least one event before its start."""
    found = {
        r["offer_date"]
        for r in store().rows(
            "SELECT DISTINCT offer_date FROM events WHERE bookmaker_id='sts' AND start_at>?",
            (now().isoformat(),),
        )
    }
    return [d for d in days if d in found]


def offer_day_choice(
    days: list[str], label: str = "Dzień", key: str | None = None, *, upcoming: bool = False
) -> str:
    """Day selector; ``upcoming``: start on the first day with matches still to play."""
    today = str(today_local())
    future = [d for d in days if d >= today]
    default = today if today in days else (future[0] if future else days[-1])
    if upcoming:
        ahead = upcoming_days(days)
        default = ahead[0] if ahead else default
    return st.selectbox(label, days, index=days.index(default), key=key, format_func=day_label)


WEEKDAYS = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")
MONTHS = (
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "września",
    "października",
    "listopada",
    "grudnia",
)


def day_label(day: str) -> str:
    """'2026-11-02' → 'poniedziałek, 2 listopada (jutro)' in the Warsaw calendar."""
    value = date.fromisoformat(day)
    offset = (value - today_local()).days
    rel = {0: " (dziś)", 1: " (jutro)", -1: " (wczoraj)"}.get(offset, "")
    return f"{WEEKDAYS[value.weekday()]}, {value.day} {MONTHS[value.month - 1]}{rel}"


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


def page_link(key: str, label: str) -> None:
    """Link to another page of the navigation (registered by main.py)."""
    page = st.session_state.get("pages", {}).get(key)
    if page is not None:
        st.page_link(page, label=label, icon=":material/arrow_forward:")
