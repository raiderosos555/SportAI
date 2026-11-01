"""Dashboard and the STS offer view with price history (STS ONLY)."""

import json
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from sports_edge import history
from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.datasources import openfootball
from sports_edge.models.registry import model_overview
from sports_edge.offers.sources import SourceBlocked
from sports_edge.pdf.engine import ParseError
from sports_edge.repository import counts, freshness, imports, offer_days, offer_table
from sports_edge.services import fetch_sts_offer, import_pdf_bytes
from sports_edge.sportsdata import coverage


def dashboard() -> None:
    c.setup_page()
    st.title("Sports Edge AI — STS")
    st.caption("Analiza oferty STS · prognozy niezależne · paper trading · bez obstawiania")
    db = c.store()
    settings = c.settings()
    stats = counts(db)
    sts_imports = [i for i in imports(db) if i["data_kind"] in c.data_kinds()]
    analyses = db.rows("SELECT * FROM analyses ORDER BY created_at DESC LIMIT 1")
    summary = json.loads(analyses[0]["summary_json"]) if analyses else {}
    covered = {s for lg in openfootball.LEAGUES.values() for s in lg.sts_leagues}
    with_data = db.scalar(
        f"""SELECT COUNT(*) FROM events WHERE bookmaker_id='sts' AND sport='football'
        AND league IN ({",".join("?" for _ in covered)})""",
        tuple(covered),
    )
    row1 = st.columns(4)
    row1[0].metric("Wydarzenia STS", stats["events"])
    row1[1].metric("Rynki", stats["markets"])
    row1[2].metric("Kursy (wszystkie migawki)", stats["odds"])
    row1[3].metric("Wydarzenia w ligach z danymi niezależnymi", with_data)
    row2 = st.columns(4)
    row2[0].metric("Prognozy", stats["predictions"])
    counts_last = summary.get("counts", {})
    row2[1].metric("Kandydaci eksperymentalni (ost. analiza)", counts_last.get("experimental", 0))
    row2[2].metric("Selekcje zwalidowane (ost. analiza)", counts_last.get("validated", 0))
    row2[3].metric("Przeanalizowane selekcje (ost. analiza)", summary.get("analysed", 0))
    c.pills(
        "🔌 Offline" if settings.offline_mode else "🌐 Sieć tylko na żądanie",
        "📄 STS: import ręczny"
        + (" + oficjalny link na żądanie" if settings.sts_download_enabled else ""),
        "📊 Dane niezależne: openfootball (CC0)",
    )
    st.subheader("Status źródeł danych")
    last = sts_imports[0] if sts_imports else None
    if last:
        empty = " — PUSTY DOKUMENT" if last["rows_total"] == 0 else ""
        st.write(
            f"**STS** — ostatni import {c.local_time(last['ingested_at'])} "
            f"(`{last['file_name']}`, rodzaj {last['offer_kind']}, dokument z "
            f"{c.local_time(last['generated_at'])}), wiersze OK {last['rows_ok']}/"
            f"{last['rows_total']}{empty}"
        )
    else:
        st.write("**STS** — brak importów. Wgraj PDF w zakładce Oferta STS.")
    data = coverage(db)
    if data:
        newest = max(r["last"] for r in data)
        st.write(
            f"**Wyniki historyczne** — {sum(r['matches'] for r in data)} meczów, "
            f"{len(data)} rozgrywek, ostatni wynik {newest} "
            f"(dostępność: {', '.join(sorted({r['availability_basis'] for r in data}))})"
        )
    else:
        st.write("**Wyniki historyczne** — brak (Modele i eksperymenty → Dane).")
    st.subheader("Status modeli i ostatniej analizy")
    models = model_overview(db)
    if models:
        top = models[0]
        st.write(
            f"Najnowszy model: **{top['family']} {top['version']}** — status `{top['status']}`"
        )
    else:
        st.info("Brak modeli — bez zwalidowanej prognozy aplikacja nie tworzy kuponu dnia.")
    if analyses:
        st.write(
            f"Ostatnia analiza {c.local_time(analyses[0]['created_at'])} dla dnia "
            f"{analyses[0]['analysis_date']}: **{summary.get('headline')}**"
        )
    st.subheader("Alerty jakości danych")
    alerts = []
    review_rows = db.scalar(
        "SELECT COUNT(*) FROM quote_sets q JOIN imports i ON i.id=q.import_id "
        "WHERE q.status='review' AND i.bookmaker_id='sts'"
    )
    if review_rows:
        alerts.append(f"{review_rows} wierszy oferty STS wymaga ręcznej weryfikacji.")
    empty_docs = sum(1 for i in sts_imports if i["rows_total"] == 0)
    if empty_docs:
        alerts.append(f"{empty_docs} zaimportowanych plików STS nie zawierało żadnych wydarzeń.")
    if (
        data
        and (c.today_local() - datetime.fromisoformat(max(r["last"] for r in data)).date()).days
        > 21
    ):
        alerts.append("Dane niezależne są nieaktualne (> 21 dni) — prognozy są wstrzymane.")
    for profile in load_profiles(c.paths().config).values():
        if not profile.cost_valid_on(c.today_local()):
            alerts.append(f"Profil podatkowy {profile.name} wygasł — EV netto zablokowane.")
    for alert in alerts or ["Brak alertów."]:
        st.write("• " + alert)


def _import_box() -> None:
    settings = c.settings()
    with st.expander("Import oferty STS (PDF)", expanded=False):
        uploaded = st.file_uploader(
            "Plik PDF STS (oferta na dziś, na jutro lub tygodniowa)", type=["pdf"], key="upload_sts"
        )
        if uploaded is not None and st.button("Importuj", key="import_sts"):
            try:
                outcome = import_pdf_bytes(
                    c.store(),
                    c.paths(),
                    settings,
                    uploaded.getvalue(),
                    file_name=uploaded.name,
                    now=c.now(),
                    data_kind="demo" if c.demo_mode() else "prospective",
                    source_id="fixture" if c.demo_mode() else "sts_pdf_manual",
                )
            except (ParseError, ValueError) as error:
                st.error(f"Import odrzucony: {error}")
            else:
                if outcome.duplicate:
                    st.warning("Duplikat — pominięto.")
                else:
                    st.success("Zaimportowano.")
                st.json(outcome.summary)
        st.caption(
            "Oficjalne linki STS (sts.pl/oferta-pdf). Pobranie wyłącznie po kliknięciu, maks. raz "
            "na 10 minut na plik; wymaga wyłączenia trybu offline i zgody w Ustawieniach. "
            "Plik „na dziś” bywa rano pusty (01.11.2026 07:45)."
        )
        cols = st.columns(3)
        for col, (day, label) in zip(
            cols, (("today", "Dziś"), ("tomorrow", "Jutro"), ("week", "Tydzień")), strict=True
        ):
            disabled = settings.offline_mode or not settings.sts_download_enabled or c.demo_mode()
            if col.button(f"Pobierz: {label}", key=f"fetch_{day}", disabled=disabled):
                try:
                    outcome = fetch_sts_offer(c.store(), c.paths(), settings, day, now=c.now())
                except (SourceBlocked, ParseError) as error:
                    st.error(str(error))
                else:
                    st.success(
                        "Zaimportowano." if not outcome.duplicate else "Bez zmian (duplikat)."
                    )
                    st.json(outcome.summary)


def offer_page() -> None:
    c.setup_page()
    st.title("Oferta STS")
    _import_box()
    db = c.store()
    days = offer_days(db)
    if not days:
        st.info("Brak zaimportowanej oferty.")
        return
    default = str(c.today_local()) if str(c.today_local()) in days else days[-1]
    cols = st.columns(4)
    day = cols[0].selectbox("Dzień", days, index=days.index(default))
    rows = offer_table(db, day=day, data_kinds=c.data_kinds())
    sports = sorted({r["sport"] for r in rows})
    sport = cols[1].selectbox("Dyscyplina", ["(wszystkie)", *sports])
    if sport != "(wszystkie)":
        rows = [r for r in rows if r["sport"] == sport]
    leagues = sorted({r["league"] for r in rows})
    league = cols[2].selectbox("Liga", ["(wszystkie)", *leagues])
    if league != "(wszystkie)":
        rows = [r for r in rows if r["league"] == league]
    markets = sorted({r["market_key"] for r in rows})
    market = cols[3].selectbox(
        "Rynek", ["(wszystkie)", *markets], format_func=lambda m: c.MARKETS.get(m, m)
    )
    if market != "(wszystkie)":
        rows = [r for r in rows if r["market_key"] == market]
    query = st.text_input("Szukaj (drużyna / liga)").strip().lower()
    if query:
        rows = [r for r in rows if query in (r["league"] + " " + r["name_raw"]).lower()]
    max_age = timedelta(minutes=c.settings().max_quote_age_minutes)
    fresh = {
        r["quote_set_id"]: freshness(r["valid_at"], r["start_at"], c.now(), max_age) for r in rows
    }
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Wydarzenia", len({r["event_id"] for r in rows}))
    m2.metric("Rynki", len(rows))
    m3.metric("Do weryfikacji", sum(r["status"] != "ok" for r in rows))
    m4.metric("Wycofane rynki", sum(r.get("presence") == "withdrawn" for r in rows))
    frame = c.offers_frame(rows[:5000], wide=market != "(wszystkie)", fresh=fresh)
    st.dataframe(frame, hide_index=True, width="stretch", height=480)
    if len(rows) > 5000:
        st.caption(f"Pokazano 5000 z {len(rows)} rynków — zawęź filtry.")
    _price_history(rows)
    review = [r for r in rows if r["status"] != "ok"]
    if review:
        with st.expander(f"Wiersze do ręcznej weryfikacji ({len(review)})"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Tekst źródłowy": r["source_text"],
                            "Problemy": ", ".join(r["issues"]),
                            "Strona": r["page"],
                        }
                        for r in review
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


def _price_history(rows: list[dict]) -> None:
    st.subheader("Historia kursów")
    events = {r["event_id"]: r for r in rows}
    if not events:
        return
    labels = {
        eid: f"{c.local_time(r['start_at'], '%m-%d %H:%M')} · {' - '.join(r['participants']) or r['name_raw']}"
        for eid, r in events.items()
    }
    event_id = st.selectbox("Wydarzenie", list(labels), format_func=labels.get, key="hist_event")
    markets = [r for r in rows if r["event_id"] == event_id]
    names = {
        r["market_id"]: f"{c.MARKETS.get(r['market_key'], r['market_key'])} "
        f"{c.PERIODS.get(r['period'], r['period'])} {r['line'] or ''}"
        for r in markets
    }
    market_id = st.selectbox("Rynek", list(names), format_func=names.get, key="hist_market")
    row = next(r for r in markets if r["market_id"] == market_id)
    start = datetime.fromisoformat(row["start_at"]) if row["start_at"] else None
    records, moves = [], []
    for key, quote in row["selections"].items():
        observations = history.selection_history(c.store(), quote["selection_id"])
        for o in observations:
            records.append(
                {
                    "Typ": c.SELECTIONS.get(key, key),
                    "Dokument z": c.local_time(o.observed_at.isoformat()),
                    "Pobrano": c.local_time(o.fetched_at.isoformat())
                    if o.fetched_at
                    else "plik ręczny",
                    "Import": c.local_time(o.ingested_at.isoformat()),
                    "Rodzaj oferty": o.offer_kind,
                    "Kurs": float(o.price),
                }
            )
        for change in history.changes(observations):
            moves.append(
                {
                    "Typ": c.SELECTIONS.get(key, key),
                    "Z": float(change["from"]),
                    "Na": float(change["to"]),
                    "Kierunek": "↑" if change["direction"] == "up" else "↓",
                    "Zmiana %": round(change["pct"] * 100, 2),
                    "Kiedy": c.local_time(change["at"].isoformat()),
                }
            )
        if start:
            snaps = history.horizon_snapshots(observations, start)
            st.caption(
                f"{c.SELECTIONS.get(key, key)}: "
                + " · ".join(
                    f"{label}: {snap['price'] if snap else '—'}" for label, snap in snaps.items()
                )
            )
    st.dataframe(pd.DataFrame(records), hide_index=True, width="stretch")
    if moves:
        st.dataframe(pd.DataFrame(moves), hide_index=True, width="stretch")
    else:
        st.caption("Brak zmian kursu między zaobserwowanymi migawkami.")
