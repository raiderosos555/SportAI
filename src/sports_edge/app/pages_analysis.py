"""Event analysis and the Daily Coupon (SOLO / AKO) for the STS offer."""

from decimal import Decimal

import pandas as pd
import streamlit as st

from sports_edge.analysis.coupon import NO_EDGE, save_analysis
from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.paper import create_coupon
from sports_edge.repository import offer_days
from sports_edge.services import analyse_day, policy


def _candidate_rows(items) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Dyscyplina": x.sport,
                "Liga": x.league,
                "Wydarzenie": x.event,
                "Godzina": c.local_time(x.start_at, "%m-%d %H:%M"),
                "Rynek": c.MARKETS.get(x.market_key, x.market_key),
                "Nr zakładu STS": x.offer_code or "",
                "Typ": c.SELECTIONS.get(x.selection, x.selection),
                "Kurs": float(x.odds),
                "P model": round(x.p_model, 4) if x.p_model is not None else None,
                "P rynek STS (bez marży)": round(x.p_market, 4) if x.p_market is not None else None,
                "EV brutto": round(x.ev_gross, 4) if x.ev_gross is not None else None,
                "EV netto": float(x.ev_net) if x.ev_net is not None else None,
                "EV netto (dolna)": float(x.ev_net_low) if x.ev_net_low is not None else None,
                "Niepewność p": f"[{x.p_low:.3f}, {x.p_high:.3f}]" if x.p_low is not None else "—",
                "Model": x.model or "—",
                "Walidacja": x.validation,
                "Status": c.TIERS[x.tier],
            }
            for x in items
        ]
    )


def analysis_page() -> None:
    c.setup_page()
    st.title("Analiza AI")
    st.caption(
        "Statystyki niezależne, prognoza modelu, prawdopodobieństwo rynkowe STS, EV i "
        "niepewność. Kurs STS nie jest niezależną prognozą przeciwko STS."
    )
    db = c.store()
    days = offer_days(db)
    if not days:
        st.info("Brak danych. Zaimportuj ofertę STS.")
        return
    day = st.selectbox("Dzień", days, index=len(days) - 1)
    _report, candidates = analyse_day(
        db, c.paths(), c.settings(), day, now=c.now(), data_kinds=c.data_kinds()
    )
    events = {}
    for x in candidates:
        events.setdefault(x.event_id, x)
    if not events:
        st.info("Brak wydarzeń w tym dniu.")
        return
    labels = {
        eid: f"{c.local_time(x.start_at, '%H:%M')} · {x.event} ({x.league})"
        for eid, x in events.items()
    }
    choice = st.selectbox("Wydarzenie", list(labels), format_func=labels.get)
    rows = [x for x in candidates if x.event_id == choice]
    head = rows[0]
    c.pills(head.sport, head.league, f"start {c.local_time(head.start_at)}")
    st.subheader("Prognoza i EV")
    st.dataframe(_candidate_rows(rows), hide_index=True, width="stretch")
    st.subheader("Statystyki niezależne (as-of)")
    teams = head.event.split(" - ")
    history_rows = []
    for team in teams:
        history_rows.extend(
            db.rows(
                """SELECT match_date, competition, home, away, home_score, away_score,
                available_at_basis FROM historical_results
                WHERE (home LIKE ? OR away LIKE ?) AND available_at <= ?
                ORDER BY match_date DESC LIMIT 5""",
                (f"%{team}%", f"%{team}%", c.now().isoformat()),
            )
        )
    if history_rows:
        st.dataframe(pd.DataFrame(history_rows), hide_index=True, width="stretch")
        st.caption(
            "Wyszukiwanie po fragmencie nazwy — do modelu używane są tylko zweryfikowane dopasowania."
        )
    else:
        st.warning(
            "Brak niezależnych danych dla tego wydarzenia — nie kwalifikuje się do analizy EV."
        )
    st.subheader("Uzasadnienie")
    for x in rows[:6]:
        with st.expander(
            f"{c.MARKETS.get(x.market_key)} · {c.SELECTIONS.get(x.selection, x.selection)} @ {x.odds}"
        ):
            st.json(x.explanation, expanded=True)
    st.subheader("Źródła")
    st.write(
        "Oferta: STS PDF (oficjalny wydruk), migawka użyta w analizie; wyniki: openfootball "
        "(CC0); reguły rynków: config/sts_rules.toml (regulamin STS 2025-07-08, zasady 2026-09-08)."
    )
    st.info("LLM (np. Astra) nie jest skonfigurowany i nie jest źródłem prognoz.")


def coupon_page() -> None:
    c.setup_page()
    st.title("Kupon dnia")
    db = c.store()
    days = offer_days(db)
    if not days:
        st.info("Brak danych. Zaimportuj ofertę STS.")
        return
    today = str(c.today_local())
    day = st.selectbox(
        "Dzień analizy", days, index=days.index(today) if today in days else len(days) - 1
    )
    rules = policy(c.paths())
    st.caption(
        f"Polityka {rules.version}: jakość ≥ {rules.min_data_quality}, aktualny i obecny w ofercie "
        f"kurs, zwalidowany model (holdout n ≥ {rules.min_validation_events}, ECE ≤ "
        f"{rules.max_calibration_ece}, lepszy od rynku), zweryfikowane reguły rynku i podatek, "
        f"dolna granica EV netto > {rules.min_net_ev_lower_bound}, kurs ≤ {rules.max_odds}."
    )
    if st.button("▶ Uruchom analizę", type="primary"):
        report, candidates = analyse_day(
            db, c.paths(), c.settings(), day, now=c.now(), data_kinds=c.data_kinds()
        )
        analysis_id = save_analysis(db, report, candidates, rules)
        st.session_state["coupon"] = (report, candidates, analysis_id)
    if "coupon" not in st.session_state:
        st.info("Kliknij „Uruchom analizę”. Każde uruchomienie jest zapisywane bez nadpisywania.")
        return
    report, candidates, analysis_id = st.session_state["coupon"]
    if report.day != day:
        st.warning("Wyniki dotyczą innego dnia — uruchom analizę ponownie.")
    if report.headline == NO_EDGE:
        c.banner(NO_EDGE, "noedge")
    else:
        c.banner(report.headline + " — analiza, nie rekomendacja gry", "ok")
    counts = report.counts
    cols = st.columns(5)
    cols[0].metric("Przeanalizowane selekcje", counts.get("analysed", 0))
    cols[1].metric("Z prognozą modelu", counts.get("with_forecast", 0))
    cols[2].metric("EV netto > 0", counts.get("ev_positive", 0))
    cols[3].metric("Eksperymentalne", counts.get("experimental", 0))
    cols[4].metric("Zwalidowane", counts.get("validated", 0))
    st.subheader("B. Kandydaci zwalidowani — ranking SOLO")
    if report.solo:
        st.dataframe(_candidate_rows(report.solo), hide_index=True, width="stretch")
        profile = load_profiles(c.paths().config)["sts"]
        for x in report.solo:
            if st.button(
                f"Zapisz jako wirtualny kupon: {x.event} / {x.selection}", key=f"paper_{x.key}"
            ):
                try:
                    create_coupon(
                        db,
                        odds_ids=[x.odds_id],
                        stake=Decimal(c.settings().nominal_stake),
                        mode="paper_system",
                        profile=profile,
                        now=c.now(),
                        analysis_id=analysis_id,
                    )
                    st.success("Zapisano w historii (paper trading).")
                except ValueError as error:
                    st.error(str(error))
    else:
        st.write(
            "Brak selekcji spełniających wszystkie warunki. Ranking nie jest uzupełniany typami."
        )
    st.subheader("A. Kandydaci eksperymentalni — przewaga niepotwierdzona")
    if report.experimental:
        st.warning("To nie są typy do gry: model nie przeszedł walidacji lub brakuje dowodów.")
        st.dataframe(_candidate_rows(report.experimental[:50]), hide_index=True, width="stretch")
    else:
        st.write("Brak kandydatów eksperymentalnych (brak prognoz z dodatnim EV netto).")
    st.subheader("AKO")
    st.write(("✅ " if report.ako.created else "⛔ ") + report.ako.reason)
    st.subheader("Powody odrzuceń")
    if report.rejection_reasons:
        st.dataframe(
            pd.DataFrame(
                [
                    {"Powód": k, "Liczba selekcji": v}
                    for k, v in list(report.rejection_reasons.items())[:25]
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
        for x in shown:
            st.markdown(
                f"**{x.event}** · {c.MARKETS.get(x.market_key)} · "
                f"{c.SELECTIONS.get(x.selection, x.selection)} @ {x.odds} — {c.TIERS[x.tier]}"
            )
            st.json(x.explanation, expanded=False)
    for warning in report.warnings:
        st.warning(warning)
    st.caption(
        "Aplikacja nie obstawia i nie gwarantuje wygranej. Dodatnie EV z niezwalidowanego modelu "
        "nie jest przewagą; wynik jednego dnia nie jest dowodem skuteczności."
    )
