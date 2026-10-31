"""Shared state, formatting and styling for the Streamlit pages."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from sports_edge.demo import demo_documents
from sports_edge.repository import ensure_reference_data
from sports_edge.services import import_pdf_bytes
from sports_edge.settings import AppPaths, AppSettings, load_settings, project_root
from sports_edge.storage import Store

WARSAW = ZoneInfo("Europe/Warsaw")
BOOKMAKER_NAMES = {"sts": "STS", "superbet": "Superbet", "unknown": "Inny"}
MARKETS = {
    "1x2": "1X2",
    "double_chance": "Podwójna szansa",
    "match_winner": "Zwycięzca",
    "draw_no_bet": "Zakład bez remisu",
    "total": "Suma poniżej/powyżej",
    "team_total_home": "Suma gospodarzy",
    "team_total_away": "Suma gości",
    "btts": "Obie drużyny strzelą",
    "home_to_score": "Gospodarze strzelą",
    "away_to_score": "Goście strzelą",
    "ht_or_ft": "1. połowa lub wynik",
    "ht_ft": "Połowa / koniec",
    "handicap_1x2": "Handicap 1X2",
    "handicap_unsigned": "Handicap (znak niezweryfikowany)",
    "red_card": "Czerwona kartka",
    "correct_score": "Dokładny wynik",
    "set_winner": "Zwycięzca seta",
    "unknown": "Nieskatalogowany",
}
PERIODS = {
    "FT": "czas regulaminowy",
    "FT_OT": "z dogrywką",
    "MATCH": "cały mecz",
    "H1": "1. połowa",
    "H2": "2. połowa",
    "S1": "1. set",
    "S2": "2. set",
    "S3": "3. set",
    "S4": "4. set",
    "S5": "5. set",
}
SELECTIONS = {
    "home": "1",
    "draw": "X",
    "away": "2",
    "home_draw": "1X",
    "draw_away": "X2",
    "home_away": "12",
    "under": "Poniżej",
    "over": "Powyżej",
    "yes": "Tak",
    "no": "Nie",
}
FRESHNESS = {
    "fresh": "🟢 aktualny",
    "stale": "🟠 nieaktualny",
    "expired": "🔴 wygasł",
    "unknown": "⚪ brak czasu",
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
    """One connection per browser session and database file."""
    target = paths().demo_database if demo_mode() else paths().database
    key = f"store::{target}"
    if key not in st.session_state:
        db = Store(target)
        ensure_reference_data(db)
        st.session_state[key] = db
    return st.session_state[key]


def load_demo_data() -> dict:
    """Synthetic DEMO offers for today+1, stored only in data/demo/demo.db."""
    day = today_local() + timedelta(days=1)
    generated = now().astimezone(WARSAW).replace(tzinfo=None)
    sts_pdf, superbet_pdf = demo_documents(day, generated)
    db = store()
    out = {}
    for name, data, bookmaker in (
        ("DEMO_sts.pdf", sts_pdf, "sts"),
        ("DEMO_superbet.pdf", superbet_pdf, "superbet"),
    ):
        outcome = import_pdf_bytes(
            db,
            paths(),
            settings(),
            data,
            file_name=name,
            bookmaker=bookmaker,
            now=now(),
            source_id="fixture",
            data_kind="demo",
        )
        out[name] = outcome.summary
    return out


def setup_page() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    if demo_mode():
        banner(
            "TRYB DEMO — dane syntetyczne z fikcyjnymi drużynami i kursami. "
            "Nie są to oferty STS ani Superbet i nie wolno ich analizować jak danych rzeczywistych.",
            "demo",
        )


def odds_text(selections: dict) -> str:
    order = list(SELECTIONS)
    keys = sorted(selections, key=lambda k: order.index(k) if k in order else 99)
    return " · ".join(f"{SELECTIONS.get(k, k)} {selections[k]['price']}" for k in keys)


def offers_frame(
    rows: list[dict], *, wide: bool, fresh: dict[str, str] | None = None
) -> pd.DataFrame:
    records = []
    for row in rows:
        record = {
            "Start": local_time(row["start_at"]),
            "Sport": (row["sport_raw"] or row["sport"]).capitalize(),
            "Liga": row["league"],
            "Wydarzenie": " - ".join(row["participants"]) or row["name_raw"],
            "Rynek": MARKETS.get(row["market_key"], row["market_key"]),
            "Okres": PERIODS.get(row["period"], row["period"]),
            "Linia": row["line"] or "",
        }
        if wide:
            for key, quote in row["selections"].items():
                record[SELECTIONS.get(key, key)] = float(quote["price"])
        else:
            record["Kursy"] = odds_text(row["selections"])
        record.update(
            {
                "Jakość": row["quality"],
                "Status": "✅ ok" if row["status"] == "ok" else "⚠️ weryfikacja",
                "Aktualność": FRESHNESS.get((fresh or {}).get(row["quote_set_id"], ""), "—"),
                "Kod": row["offer_code"] or "",
                "Strona": row["page"],
            }
        )
        records.append(record)
    return pd.DataFrame.from_records(records)
