"""Dashboard, bookmaker offer views and the STS / Superbet comparison."""

import json
from datetime import timedelta

import pandas as pd
import streamlit as st

from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.matching import find_matches, record_decision
from sports_edge.models.registry import model_overview
from sports_edge.offers.sources import SourceBlocked
from sports_edge.pdf.engine import ParseError
from sports_edge.repository import counts, freshness, imports, offer_days, offer_table
from sports_edge.services import analyse_day, fetch_sts_offer, import_pdf_bytes, normalizer


def dashboard() -> None:
    c.setup_page()
    st.title("Sports Edge AI")
    st.caption(
        "Lokalna analiza ofert STS i Superbet · tryb analityczny i paper trading · bez obstawiania"
    )
    db = c.store()
    settings = c.settings()
    stats = counts(db)
    all_imports = [i for i in imports(db) if i["data_kind"] in c.data_kinds()]
    cols = st.columns(5)
    cols[0].metric("Importy", len(all_imports))
    cols[1].metric("Wydarzenia", stats["events"])
    cols[2].metric("Rynki", stats["markets"])
    cols[3].metric("Kursy (wszystkie snapshoty)", stats["odds"])
    analyses = db.rows("SELECT * FROM analyses ORDER BY created_at DESC LIMIT 1")
    analysed = json.loads(analyses[0]["summary_json"])["analysed"] if analyses else 0
    cols[4].metric("Przeanalizowane selekcje (ostatnia analiza)", analysed)
    c.pills(
        "🔌 Offline" if settings.offline_mode else "🌐 Online (tylko na żądanie)",
        "📄 STS: ręczny + oficjalny link"
        if settings.sts_download_enabled
        else "📄 STS: import ręczny",
        "📄 Superbet: import ręczny (parser niezweryfikowany)",
    )
    st.subheader("Status importu")
    for bookmaker in ("sts", "superbet"):
        last = next((i for i in all_imports if i["bookmaker_id"] == bookmaker), None)
        if last is None:
            st.write(
                f"**{c.BOOKMAKER_NAMES[bookmaker]}** — brak importów. Wgraj PDF w zakładce operatora."
            )
            continue
        st.write(
            f"**{c.BOOKMAKER_NAMES[bookmaker]}** — ostatni import {c.local_time(last['ingested_at'])}, "
            f"plik `{last['file_name']}`, dokument z {c.local_time(last['generated_at'])}, "
            f"wiersze OK {last['rows_ok']}/{last['rows_total']}, jakość {last['quality']:.3f}"
        )
    st.subheader("Status modeli")
    models = model_overview(db)
    validated = [m for m in models if m["status"] == "validated"]
    if not models:
        st.info(
            "Brak zarejestrowanych modeli. Bez niezależnej, zwalidowanej prognozy aplikacja "
            "nie tworzy kuponu dnia (patrz Ustawienia → Modele)."
        )
    else:
        st.write(f"Wersje modeli: {len(models)}, zwalidowane: {len(validated)}")
    st.subheader("Alerty jakości danych")
    alerts = []
    review_rows = db.scalar("SELECT COUNT(*) FROM quote_sets WHERE status='review'")
    if review_rows:
        alerts.append(
            f"{review_rows} wierszy oferty wymaga ręcznej weryfikacji (nie są analizowane)."
        )
    unparsed = db.scalar("SELECT COUNT(*) FROM review_items")
    if unparsed:
        alerts.append(f"{unparsed} linii PDF nie przypisano do rynku (kwarantanna).")
    if any(not i["parser_verified"] for i in all_imports):
        alerts.append(
            "Część importów pochodzi z parsera niezweryfikowanego na autentycznym dokumencie."
        )
    for profile in load_profiles(c.paths().config).values():
        if not profile.cost_valid_on(c.today_local()):
            alerts.append(f"Profil podatkowy {profile.name} wygasł — EV netto zablokowane.")
        elif not profile.tax_verified:
            alerts.append(
                f"Profil podatkowy {profile.name} oparty na założeniu (niezweryfikowany)."
            )
    for alert in alerts or ["Brak alertów."]:
        st.write("• " + alert)


def _import_box(bookmaker: str) -> None:
    settings = c.settings()
    with st.expander(f"Import oferty {c.BOOKMAKER_NAMES[bookmaker]} (PDF)", expanded=False):
        uploaded = st.file_uploader(
            "Plik PDF (oferta dzienna, na jutro lub tygodniowa)",
            type=["pdf"],
            key=f"upload_{bookmaker}",
        )
        if uploaded is not None and st.button("Importuj", key=f"import_{bookmaker}"):
            try:
                outcome = import_pdf_bytes(
                    c.store(),
                    c.paths(),
                    settings,
                    uploaded.getvalue(),
                    file_name=uploaded.name,
                    bookmaker=bookmaker,
                    now=c.now(),
                    data_kind="demo" if c.demo_mode() else "prospective",
                    source_id="fixture" if c.demo_mode() else None,
                )
            except ParseError as error:
                st.error(f"Import odrzucony: {error}")
            else:
                if outcome.duplicate:
                    st.warning("Ten plik był już zaimportowany tą wersją parsera — pominięto.")
                else:
                    st.success("Zaimportowano.")
                st.json(outcome.summary)
        if bookmaker == "sts":
            st.caption(
                "Oficjalne linki STS (sts.pl/oferta-pdf). Pobranie wyłącznie po kliknięciu, "
                "maks. raz na 10 minut na plik; wymaga wyłączenia trybu offline i zgody w Ustawieniach."
            )
            cols = st.columns(3)
            for col, (day, label) in zip(
                cols, (("today", "Dziś"), ("tomorrow", "Jutro"), ("week", "Tydzień")), strict=True
            ):
                disabled = (
                    settings.offline_mode or not settings.sts_download_enabled or c.demo_mode()
                )
                if col.button(f"Pobierz: {label}", key=f"fetch_{day}", disabled=disabled):
                    try:
                        outcome = fetch_sts_offer(c.store(), c.paths(), settings, day, now=c.now())
                    except (SourceBlocked, ParseError) as error:
                        st.error(str(error))
                    else:
                        st.success(
                            "Pobrano i zaimportowano."
                            if not outcome.duplicate
                            else "Bez zmian (duplikat)."
                        )
                        st.json(outcome.summary)
        else:
            st.caption(
                "31.10.2026 Superbet PL nie publikował oferty PDF na superbet.pl. Parser ogólny jest "
                "niezweryfikowany — wszystkie wiersze mają obniżoną jakość i nie trafiają do kuponu."
            )


def offer_page(bookmaker: str) -> None:
    c.setup_page()
    st.title(f"Oferta {c.BOOKMAKER_NAMES[bookmaker]}")
    _import_box(bookmaker)
    db = c.store()
    days = [d for d in offer_days(db, bookmaker)]
    if not days:
        st.info("Brak zaimportowanej oferty.")
        return
    default = str(c.today_local()) if str(c.today_local()) in days else days[-1]
    cols = st.columns([1, 1, 1, 1])
    day = cols[0].selectbox("Dzień", days, index=days.index(default), key=f"day_{bookmaker}")
    rows = offer_table(db, bookmaker=bookmaker, day=day, data_kinds=c.data_kinds())
    sports = sorted({r["sport"] for r in rows})
    sport = cols[1].selectbox("Sport", ["(wszystkie)", *sports], key=f"sport_{bookmaker}")
    markets = sorted({r["market_key"] for r in rows})
    market = cols[2].selectbox(
        "Rynek",
        ["(wszystkie)", *markets],
        format_func=lambda m: c.MARKETS.get(m, m),
        key=f"market_{bookmaker}",
    )
    status = cols[3].selectbox(
        "Status", ["wszystkie", "ok", "weryfikacja"], key=f"status_{bookmaker}"
    )
    query = st.text_input("Szukaj (drużyna / liga)", key=f"q_{bookmaker}").strip().lower()
    if sport != "(wszystkie)":
        rows = [r for r in rows if r["sport"] == sport]
    if market != "(wszystkie)":
        rows = [r for r in rows if r["market_key"] == market]
    if status != "wszystkie":
        rows = [r for r in rows if (r["status"] == "ok") == (status == "ok")]
    if query:
        rows = [r for r in rows if query in (r["league"] + " " + r["name_raw"]).lower()]
    max_age = timedelta(minutes=c.settings().max_quote_age_minutes)
    fresh = {
        r["quote_set_id"]: freshness(r["valid_at"], r["start_at"], c.now(), max_age) for r in rows
    }
    events = {r["event_id"] for r in rows}
    m1, m2, m3 = st.columns(3)
    m1.metric("Wydarzenia", len(events))
    m2.metric("Rynki", len(rows))
    m3.metric("Wymaga weryfikacji", sum(r["status"] != "ok" for r in rows))
    frame = c.offers_frame(rows[:5000], wide=market != "(wszystkie)", fresh=fresh)
    st.dataframe(frame, hide_index=True, width="stretch", height=520)
    if len(rows) > 5000:
        st.caption(f"Pokazano 5000 z {len(rows)} rynków — zawęź filtry.")
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
    quarantine = db.rows(
        "SELECT r.* FROM review_items r JOIN imports i ON i.id=r.import_id WHERE i.bookmaker_id=?",
        (bookmaker,),
    )
    if quarantine:
        with st.expander(f"Linie PDF nieprzypisane do rynku ({len(quarantine)})"):
            st.dataframe(
                pd.DataFrame(quarantine)[["page", "reason", "source_text", "context"]],
                hide_index=True,
                width="stretch",
            )


def sts_page() -> None:
    offer_page("sts")


def superbet_page() -> None:
    offer_page("superbet")


def comparison_page() -> None:
    c.setup_page()
    st.title("Porównanie STS ↔ Superbet")
    st.caption(
        "Porównywane są wyłącznie te same wydarzenia (sport + czas startu + uczestnicy) i identyczne "
        "rynki (typ, okres, dogrywka, linia). Kurs netto = kurs × (1 − podatek od stawki) według profilu."
    )
    db = c.store()
    days = sorted(set(offer_days(db, "sts")) & set(offer_days(db, "superbet")))
    if not days:
        st.info("Brak dnia z ofertą obu bukmacherów. Zaimportuj oferty STS i Superbet.")
        return
    day = st.selectbox("Dzień", days, index=len(days) - 1)
    settings = c.settings()
    matches = find_matches(
        db,
        day=day,
        normalizer=normalizer(c.paths()),
        tolerance_minutes=settings.match_tolerance_minutes,
    )
    linked = [m for m in matches if m.status in {"auto", "confirmed"}]
    candidates = [m for m in matches if m.status == "candidate"]
    st.write(
        f"Dopasowane wydarzenia: **{len(linked)}**, kandydaci do potwierdzenia: **{len(candidates)}**"
    )
    if candidates:
        with st.expander(
            "Kandydaci — wymagają decyzji (nazwy podobne, ale nie identyczne)", expanded=True
        ):
            for i, match in enumerate(candidates[:50]):
                a, b = match.event_a, match.event_b
                cols = st.columns([5, 5, 2, 1, 1])
                cols[0].write(
                    f"STS: **{a['home']} - {a['away']}** · {c.local_time(a['start_at'])} · {a['league']}"
                )
                cols[1].write(
                    f"Superbet: **{b['home']} - {b['away']}** · {c.local_time(b['start_at'])} · {b['league']}"
                )
                cols[2].write(f"{match.method} ({match.confidence:.2f})")
                if cols[3].button("✓", key=f"ok_{i}", help="Potwierdź — zapisze alias"):
                    record_decision(db, match, "confirmed", at=c.now())
                    st.rerun()
                if cols[4].button("✗", key=f"no_{i}", help="Odrzuć"):
                    record_decision(db, match, "rejected", at=c.now())
                    st.rerun()
    _report, _candidates, comparison, _ = analyse_day(
        db, c.paths(), settings, day, now=c.now(), data_kinds=c.data_kinds()
    )
    if not comparison:
        st.info("Brak identycznych rynków w dopasowanych wydarzeniach.")
        return
    frame = pd.DataFrame(
        [
            {
                "Start": c.local_time(r["start_at"]),
                "Wydarzenie (STS)": r["event_a"],
                "Wydarzenie (Superbet)": r["event_b"],
                "Rynek": c.MARKETS.get(r["market"], r["market"]),
                "Okres": c.PERIODS.get(r["period"], r["period"]),
                "Linia": r["line"] or "",
                "Typ": c.SELECTIONS.get(r["selection"], r["selection"]),
                "Kurs STS": float(r["odds_a"]),
                "Kurs Superbet": float(r["odds_b"]),
                "Różnica %": round(r["odds_diff_pct"], 2),
                "Netto STS": float(r["net_a"]) if r["net_a"] is not None else None,
                "Netto Superbet": float(r["net_b"]) if r["net_b"] is not None else None,
                "Lepszy netto": c.BOOKMAKER_NAMES.get(r["better_net"], "=")
                if r["better_net"]
                else "=",
                "Aktualność STS": c.FRESHNESS[r["fresh_a"]],
                "Aktualność SB": c.FRESHNESS[r["fresh_b"]],
                "Zgodność": r["compatibility"],
            }
            for r in comparison
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch", height=520)
    st.caption(
        "Ocena kursu ≠ wartość oczekiwana. EV netto wymaga niezależnej prognozy modelu (zakładka Kupon dnia). "
        "Profil Superbet jest założeniem podatkowym — patrz Ustawienia → Profile podatkowe."
    )
