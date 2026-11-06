"""EV diagnostics of a day (TASK 004 view, kept for research): probability ranking,
economic (EV) rankings, AKO analysis and rejection reasons. It never decides a coupon."""

from decimal import Decimal

import pandas as pd
import streamlit as st

from sports_edge.analysis.coupon import NO_EDGE
from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.coupon_store import DUPLICATE_MESSAGE, save_manual
from sports_edge.services import policy


def freshness_text(x) -> str:
    if x.freshness == "unconfirmed" and x.odds_age_hours is not None:
        return f"🟠 Kurs niepotwierdzony od {x.odds_age_hours:.0f} h"
    return c.FRESHNESS.get(x.freshness, x.freshness)


def _probable_rows(items) -> pd.DataFrame:
    def note(x) -> str:
        if x.break_even is not None and x.break_even >= 1:
            return "⛔ strata nawet przy trafieniu (kurs × 0,88 < 1)"
        if x.ev_net is not None and x.ev_net < 0:
            return "⚠ oczekiwana strata po podatku"
        return "EV netto dodatnie (model eksperymentalny)"

    return pd.DataFrame(
        [
            {
                "Wydarzenie": x.event,
                "Liga": x.league,
                "Godzina": c.local_time(x.start_at, "%m-%d %H:%M"),
                "Rynek": x.market_label,
                "Typ": c.SELECTIONS.get(x.selection, x.selection),
                "Nr zakładu STS": x.offer_code or "",
                "Kurs STS": float(x.odds),
                "P wygranej (model)": round(x.p_model, 3),
                "P rynek STS (bez marży)": round(x.p_market, 3) if x.p_market is not None else None,
                "Próg opłacalności": round(x.break_even, 3) if x.break_even is not None else None,
                "EV netto": float(x.ev_net) if x.ev_net is not None else None,
                "Kalibracja modelu": x.calibration,
                "Świeżość kursu": freshness_text(x),
                "Ocena": note(x),
            }
            for x in items
        ]
    )


def _candidate_rows(items) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Wydarzenie": x.event,
                "Dyscyplina": x.sport,
                "Liga": x.league,
                "Godzina": c.local_time(x.start_at, "%m-%d %H:%M"),
                "Rynek": x.market_label,
                "Typ": c.SELECTIONS.get(x.selection, x.selection),
                "Nr zakładu STS": x.offer_code or "",
                "Kurs STS": float(x.odds),
                "P model": round(x.p_model, 4) if x.p_model is not None else None,
                "P rynek STS (bez marży)": round(x.p_market, 4) if x.p_market is not None else None,
                "EV netto": float(x.ev_net) if x.ev_net is not None else None,
                "EV netto (dolna)": float(x.ev_net_low) if x.ev_net_low is not None else None,
                "Niepewność p": f"[{x.p_low:.3f}, {x.p_high:.3f}]" if x.p_low is not None else "—",
                "Status modelu": x.model_status,
                "Uzasadnienie": x.explanation.get("Decyzja", ""),
                "Świeżość kursu": freshness_text(x),
                "Status wydarzenia": c.EVENT_STATUS.get(x.event_status, x.event_status),
            }
            for x in items
        ]
    )


# ---------------------------------------------------------------- Analiza AI


def ev_analysis(db, run) -> None:
    """TASK 004 view kept as diagnostics: probability ranking, EV rankings, rejections."""
    report = run.report
    rules = policy(c.paths())
    if report.headline == NO_EDGE:
        c.banner(NO_EDGE, "noedge")
    else:
        c.banner(report.headline + " — analiza, nie rekomendacja gry", "ok")
    for line in report.diagnosis:
        st.write("• " + line)
    stages = {s["stage"]: s for s in run.stages}
    counts = report.counts
    row1 = st.columns(4)
    row1[0].metric("Wydarzenia w ofercie", counts.get("events", 0))
    row1[1].metric("Poprawnie odczytane rynki", counts.get("markets_ok", 0))
    row1[2].metric(
        "Wydarzenia z aktualnymi danymi", stages.get("Dopasowanie", {}).get("considered", 0)
    )
    model_stage = stages.get("Modele", {})
    row1[3].metric(
        "Prognozy (nowe + aktualne)",
        model_stage.get("forecasts_saved", 0) + model_stage.get("forecasts_kept", 0),
    )
    row2 = st.columns(5)
    row2[0].metric("Rynki obsługiwane przez model", counts.get("model_markets", 0))
    row2[1].metric("Kandydaci EV+", counts.get("ev_positive", 0))
    row2[2].metric("Eksperymentalni", counts.get("experimental", 0))
    row2[3].metric("Zwalidowani (EV)", counts.get("validated", 0))
    row2[4].metric(f"Typy z P ≥ {rules.probable_min_probability:.0%}", counts.get("probable", 0))
    st.subheader(
        f"Najwyższe prawdopodobieństwo wygranej (P ≥ {rules.probable_min_probability:.0%})"
    )
    st.caption(
        "Ranking według prawdopodobieństwa modelu. W STS wygrana to 0,88 × stawka × kurs, więc "
        "„Próg opłacalności” = 1 / (0,88 × kurs); kurs poniżej 1,14 przynosi stratę nawet przy "
        "trafieniu."
    )
    if report.probable:
        st.dataframe(_probable_rows(report.probable), hide_index=True, width="stretch")
    else:
        st.write("Brak selekcji z prognozą i potwierdzonym (świeżym) kursem na tym poziomie.")
    if report.probable_unconfirmed:
        st.markdown(
            f"**Kursy niepotwierdzone ({len(report.probable_unconfirmed)})** — ceny ze starszej "
            "migawki STS; pobierz aktualną ofertę, zanim potraktujesz je jako dostępne."
        )
        frame = _probable_rows(report.probable_unconfirmed)
        frame.insert(
            7,
            "Ostatnia obserwacja kursu",
            [c.local_time(x.document_at) for x in report.probable_unconfirmed],
        )
        st.dataframe(frame, hide_index=True, width="stretch")
    st.subheader("Ranking SOLO — kandydaci zwalidowani ekonomicznie (EV)")
    if report.solo:
        st.dataframe(_candidate_rows(report.solo), hide_index=True, width="stretch")
        profile = load_profiles(c.paths().config)["sts"]
        for x in report.solo:
            if st.button(
                f"Zapisz jako wirtualny kupon: {x.event} / {x.selection}", key=f"paper_{x.key}"
            ):
                try:
                    result = save_manual(
                        db,
                        odds_ids=[x.odds_id],
                        stake=Decimal(c.settings().nominal_stake),
                        mode="paper_system",
                        sts=profile,
                        now=c.now(),
                        analysis_id=run.analysis_id,
                    )
                    if result.relation == "duplicate":
                        st.info(DUPLICATE_MESSAGE)
                    else:
                        st.success("Zapisano w historii (paper trading).")
                except ValueError as error:
                    st.error(str(error))
    else:
        st.write("Brak selekcji spełniających warunki polityki EV.")
    st.subheader("Ranking eksperymentalny (EV) — przewaga niepotwierdzona")
    if report.experimental:
        st.dataframe(_candidate_rows(report.experimental[:50]), hide_index=True, width="stretch")
    else:
        st.write("Brak kandydatów eksperymentalnych (brak prognoz z dodatnim EV netto).")
    st.subheader("AKO (polityka EV)")
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
    for warning in report.warnings:
        st.warning(warning)
