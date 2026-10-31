"""Event analysis and the Daily Coupon (SOLO / AKO) with justifications."""

from decimal import Decimal

import pandas as pd
import streamlit as st

from sports_edge.analysis.coupon import NO_EDGE, save_analysis
from sports_edge.analysis.edge_engine import devig
from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.models.registry import latest_predictions
from sports_edge.paper import create_coupon
from sports_edge.repository import offer_days, offer_table
from sports_edge.services import analyse_day, policy


def analysis_page() -> None:
    c.setup_page()
    st.title("Analiza AI")
    st.caption(
        "Szczegóły wydarzenia: kursy obu bukmacherów, prawdopodobieństwa rynkowe (bez marży), "
        "prognozy modeli i ich niepewność. Kurs bukmachera nie jest niezależną prognozą."
    )
    db = c.store()
    days = offer_days(db)
    if not days:
        st.info("Brak danych. Zaimportuj ofertę.")
        return
    day = st.selectbox("Dzień", days, index=len(days) - 1)
    rows = offer_table(db, day=day, data_kinds=c.data_kinds())
    events: dict[str, dict] = {}
    for row in rows:
        events.setdefault(row["event_id"], row)
    if not events:
        st.info("Brak wydarzeń w tym dniu.")
        return
    labels = {
        eid: f"{c.local_time(r['start_at'], '%H:%M')} · {c.BOOKMAKER_NAMES.get(r['bookmaker_id'])} · "
        f"{' - '.join(r['participants']) or r['name_raw']} ({r['league']})"
        for eid, r in events.items()
    }
    choice = st.selectbox("Wydarzenie", list(labels), format_func=labels.get)
    event_rows = [r for r in rows if r["event_id"] == choice]
    head = event_rows[0]
    c.pills(head["sport"], head["league"], f"start {c.local_time(head['start_at'])}")
    predictions = latest_predictions(db, as_of=c.now())
    records = []
    for row in event_rows:
        fair, margin = devig(row)
        prediction = predictions.get(
            (row["event_id"], row["market_key"], row["period"], row["line"])
        )
        for key, quote in row["selections"].items():
            p_model = prediction["probabilities"].get(key) if prediction else None
            interval = (
                prediction["uncertainty"].get("interval", {}).get(key) if prediction else None
            )
            records.append(
                {
                    "Rynek": c.MARKETS.get(row["market_key"], row["market_key"]),
                    "Okres": c.PERIODS.get(row["period"], row["period"]),
                    "Linia": row["line"] or "",
                    "Typ": c.SELECTIONS.get(key, key),
                    "Kurs": float(quote["price"]),
                    "P implikowane": round(1 / float(quote["price"]), 4),
                    "P rynek bez marży": round(fair[key], 4) if fair else None,
                    "Marża": round(margin, 4) if margin is not None else None,
                    "P model": round(p_model, 4) if p_model is not None else None,
                    "Niepewność": f"[{interval[0]:.3f}, {interval[1]:.3f}]" if interval else "—",
                    "Model": f"{prediction['family']} {prediction['version']} ({prediction['version_status']})"
                    if prediction
                    else "brak",
                }
            )
    st.dataframe(pd.DataFrame(records), hide_index=True, width="stretch")
    st.subheader("Źródła")
    for row in event_rows[:1]:
        st.write(
            f"Import `{row['import_id'][:12]}…`, dokument z {c.local_time(row['valid_at'])}, "
            f"pobrany {c.local_time(row['ingested_at'])}, strona PDF {row['page']}, kod {row['offer_code']}."
        )
        st.code(row["source_text"], language=None)
    st.subheader("Statystyki niezależne")
    history = db.scalar("SELECT COUNT(*) FROM historical_results WHERE sport=?", (head["sport"],))
    if history:
        st.write(f"W bazie {history} historycznych wyników dla sportu `{head['sport']}`.")
    else:
        st.warning(
            "Brak niezależnych danych statystycznych dla tej dyscypliny — wydarzenie nie kwalifikuje "
            "się do analizy EV. Zaimportuj licencjonowane wyniki historyczne (Ustawienia → Modele)."
        )
    st.subheader("LLM / Astra")
    st.info(
        "Adapter LLM nie jest skonfigurowany: brak klucza i weryfikacji dostępności modelu "
        "(np. Astra). Odpowiedzi LLM nie są używane jako prognozy."
    )


def coupon_page() -> None:
    c.setup_page()
    st.title("Kupon dnia")
    db = c.store()
    days = offer_days(db)
    if not days:
        st.info("Brak danych. Zaimportuj ofertę.")
        return
    today = str(c.today_local())
    day = st.selectbox(
        "Dzień analizy", days, index=days.index(today) if today in days else len(days) - 1
    )
    rules = policy(c.paths())
    st.caption(
        f"Polityka {rules.version}: jakość ≥ {rules.min_data_quality}, zweryfikowany parser, aktualny kurs, "
        f"zwalidowany model (n ≥ {rules.min_validation_events}, ECE ≤ {rules.max_calibration_ece}, "
        f"lepszy od rynku), dolna granica EV netto > {rules.min_net_ev_lower_bound}, kurs ≤ {rules.max_odds}."
    )
    if st.button("▶ Uruchom analizę", type="primary"):
        settings = c.settings()
        report, candidates, _comparison, _ = analyse_day(
            db, c.paths(), settings, day, now=c.now(), data_kinds=c.data_kinds()
        )
        analysis_id = save_analysis(db, report, candidates, rules)
        st.session_state["coupon"] = (report, candidates, analysis_id)
    if "coupon" not in st.session_state:
        st.info(
            "Kliknij „Uruchom analizę”. Każde uruchomienie jest zapisywane w historii bez nadpisywania."
        )
        return
    report, candidates, analysis_id = st.session_state["coupon"]
    if report.day != day:
        st.warning("Wyniki dotyczą innego dnia — uruchom analizę ponownie.")
    if report.headline == NO_EDGE:
        c.banner(NO_EDGE, "noedge")
    else:
        c.banner(report.headline + " — analiza, nie rekomendacja gry", "ok")
    m1, m2, m3 = st.columns(3)
    m1.metric("Przeanalizowane selekcje", report.analysed)
    m2.metric("Przyjęte SOLO", len(report.solo))
    m3.metric("Analiza", analysis_id[:10] + "…")
    st.subheader("Ranking SOLO")
    if report.solo:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Bukmacher": c.BOOKMAKER_NAMES.get(s.bookmaker),
                        "Wydarzenie": s.event,
                        "Rynek": c.MARKETS.get(s.market_key),
                        "Typ": c.SELECTIONS.get(s.selection, s.selection),
                        "Kurs": float(s.odds),
                        "P model": s.p_model,
                        "Przedział p": f"[{s.p_low:.3f}, {s.p_high:.3f}]",
                        "EV brutto": round(s.ev_gross, 4),
                        "EV netto": float(s.ev_net),
                        "EV netto (dolna)": float(s.ev_net_low),
                        "Inny bukmacher": f"{s.other.get('bookmaker', '')} {s.other.get('odds', '')}",
                    }
                    for s in report.solo
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        profiles = load_profiles(c.paths().config)
        for s in report.solo:
            if st.button(
                f"Zapisz jako wirtualny kupon: {s.event} / {s.selection}", key=f"paper_{s.key}"
            ):
                try:
                    create_coupon(
                        db,
                        odds_ids=[s.odds_id],
                        stake=Decimal(c.settings().nominal_stake),
                        mode="paper_system",
                        profile=profiles[s.bookmaker],
                        now=c.now(),
                        analysis_id=analysis_id,
                    )
                    st.success("Zapisano w historii (paper trading).")
                except ValueError as error:
                    st.error(str(error))
    else:
        st.write(
            "Brak selekcji spełniających wszystkie warunki. Ranking nie jest uzupełniany przypadkowymi typami."
        )
    st.subheader("Analiza AKO")
    st.write(("✅ " if report.ako.created else "⛔ ") + report.ako.reason)
    st.subheader("Dlaczego odrzucono")
    if report.rejection_reasons:
        st.dataframe(
            pd.DataFrame(
                [
                    {"Powód": k, "Liczba selekcji": v}
                    for k, v in list(report.rejection_reasons.items())[:20]
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with st.expander("Uzasadnienie pojedynczych selekcji"):
        query = st.text_input("Filtr wydarzenia").strip().lower()
        shown = (
            [x for x in candidates if query in x.event.lower()][:30] if query else candidates[:10]
        )
        for candidate in shown:
            st.markdown(
                f"**{candidate.event}** · {c.MARKETS.get(candidate.market_key)} · "
                f"{c.SELECTIONS.get(candidate.selection, candidate.selection)} @ {candidate.odds}"
            )
            st.json(candidate.explanation, expanded=False)
    if report.warnings:
        st.subheader("Ostrzeżenia")
        for warning in report.warnings:
            st.warning(warning)
    st.caption(
        "Aplikacja nie obstawia i nie gwarantuje wygranej. Dodatnie EV z modelu bez walidacji nie jest "
        "potwierdzoną przewagą; wynik pojedynczego dnia nie jest dowodem skuteczności."
    )
