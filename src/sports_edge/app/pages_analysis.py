"""Analiza AI (single event, statistical model) and the one-button Daily Coupon (STS ONLY)."""

import json
from decimal import Decimal

import pandas as pd
import streamlit as st

from sports_edge.analysis.coupon import NO_EDGE
from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.paper import create_coupon
from sports_edge.repository import offer_days
from sports_edge.results_engine import SOURCE_LABELS
from sports_edge.services import STAGES, analyse_day, event_context, policy, run_daily_analysis


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


def analysis_page() -> None:
    c.setup_page()
    st.title("Analiza AI")
    st.caption(
        "Analiza statystyczna wydarzenia: niezależne wyniki, model Elo, porównanie z kursem STS. "
        "To nie jest analiza językowa (LLM)."
    )
    db = c.store()
    days = offer_days(db)
    if not days:
        st.info("Brak danych. Zaimportuj ofertę STS.")
        return
    today = str(c.today_local())
    day = st.selectbox("Dzień", days, index=days.index(today) if today in days else len(days) - 1)
    _report, candidates = analyse_day(
        db, c.paths(), c.settings(), day, now=c.now(), data_kinds=c.data_kinds()
    )
    events: dict[str, list] = {}
    for x in candidates:
        events.setdefault(x.event_id, []).append(x)
    if not events:
        st.info("Brak wydarzeń w tym dniu.")
        return
    with_forecast = {
        eid for eid, rows in events.items() if any(r.p_model is not None for r in rows)
    }
    only = st.toggle(
        f"Tylko wydarzenia z prognozą modelu ({len(with_forecast)})", value=bool(with_forecast)
    )
    shown = [eid for eid in events if not only or eid in with_forecast] or list(events)
    labels = {
        eid: f"{c.local_time(events[eid][0].start_at, '%H:%M')} · {events[eid][0].event} "
        f"({events[eid][0].league})"
        for eid in shown
    }
    choice = st.selectbox("Wydarzenie", shown, format_func=labels.get)
    rows = events[choice]
    head = rows[0]
    context = event_context(db, c.paths(), event_id=choice, now=c.now())
    prediction = context["prediction"]

    st.subheader("A. Wydarzenie")
    c.pills(head.sport, head.league, head.event)
    cols = st.columns(3)
    cols[0].metric("Start (czas polski)", c.local_time(head.start_at, "%d.%m %H:%M"))
    cols[1].metric("Status wydarzenia", c.EVENT_STATUS.get(head.event_status, head.event_status))
    cols[2].metric(
        "Status modelu",
        prediction["version_status"] if prediction else "brak prognozy",
    )
    sources = [
        f"Oferta STS: dokument z {c.local_time(head.document_at)}"
        + (f", pobrano {c.local_time(head.fetched_at)}" if head.fetched_at else ", plik ręczny"),
        f"Świeżość kursu: {freshness_text(head)}",
    ]
    if context["freshness"]:
        f = context["freshness"]
        sources.append(
            f"Wyniki {f['name']}: ostatni {f['last_result']} ({f['days_since']} dni temu), "
            f"źródła: {', '.join(SOURCE_LABELS.get(p, p) for p in f['providers'])}"
        )
    else:
        sources.append("Brak niezależnych wyników dla tej ligi/dyscypliny — brak modelu.")
    for line in sources:
        st.write("• " + line)

    st.subheader("B. Ostatnie wyniki uczestników")
    if context["teams"]:
        team_cols = st.columns(2)
        for col, side in zip(team_cols, ("home", "away"), strict=True):
            team = context["teams"][side]
            with col:
                if not team["canonical"]:
                    st.warning(
                        f"{team['sts']}: brak zweryfikowanego dopasowania nazwy "
                        f"({team['status']}"
                        + (f", propozycja: {team['suggestion']}" if team["suggestion"] else "")
                        + ") — wyników nie zgadujemy. Potwierdź alias w zakładce Modele."
                    )
                    continue
                st.markdown(f"**{team['sts']}** → {team['canonical']} ({team['games']} meczów)")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Data": m["date"],
                                "Miejsce": m["venue"],
                                "Przeciwnik": m["opponent"],
                                "Wynik": m["score"],
                                "W/R/P": m["result"],
                                "Znany przed prognozą": "tak"
                                if m["known_before_forecast"]
                                else "nie",
                                "Źródło": SOURCE_LABELS.get(m["source"], m["source"])
                                + (" ⚠ konflikt" if m["conflict"] else ""),
                            }
                            for m in team["matches"]
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )
    else:
        st.info("Ta liga nie ma niezależnego źródła wyników w aplikacji.")

    st.subheader("C. Prawdopodobieństwa 1X2 (czas regulaminowy)")
    fair = {x.selection: x.p_market for x in rows if x.market_key == "1x2" and x.period == "FT"}
    if prediction:
        probs = prediction["probabilities"]
        interval = prediction["uncertainty"].get("interval", {})
        chart = pd.DataFrame(
            {
                "Model": [probs.get(k) for k in ("home", "draw", "away")],
                "Rynek STS bez marży": [fair.get(k) for k in ("home", "draw", "away")],
            },
            index=["1 — gospodarze", "X — remis", "2 — goście"],
        )
        st.bar_chart(chart, stack=False)
        st.caption(
            f"Model {prediction['family']} {prediction['version']} — status "
            f"{prediction['version_status']}; prognoza z {c.local_time(prediction['created_at'])}; "
            "niepewność: "
            + ", ".join(
                f"{c.SELECTIONS[k]} [{v[0]:.3f}; {v[1]:.3f}]"
                for k, v in interval.items()
                if k in c.SELECTIONS
            )
        )
    else:
        st.info(
            "Brak prognozy modelu dla rynku 1X2 tego wydarzenia (liga bez danych, nieaktualne "
            "dane, za krótka historia drużyny albo wydarzenie już się rozpoczęło)."
        )

    st.subheader("D. Porównanie z ofertą STS")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Rynek": x.market_label,
                    "Opis w PDF": x.label_raw,
                    "Nr zakładu": x.offer_code or "",
                    "Typ": c.SELECTIONS.get(x.selection, x.selection),
                    "Kurs": float(x.odds),
                    "P implikowane": round(x.implied, 4),
                    "P bez marży": round(x.p_market, 4) if x.p_market is not None else None,
                    "P model": round(x.p_model, 4) if x.p_model is not None else None,
                    "Próg opłacalności": round(x.break_even, 4)
                    if x.break_even is not None
                    else None,
                    "EV brutto": round(x.ev_gross, 4) if x.ev_gross is not None else None,
                    "EV netto": float(x.ev_net) if x.ev_net is not None else None,
                    "EV netto (dolna)": float(x.ev_net_low) if x.ev_net_low is not None else None,
                    "Status": c.TIERS[x.tier],
                }
                for x in rows
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    st.subheader("E. Wyjaśnienie")
    for line in _explain(rows, context):
        st.write("• " + line)
    with st.expander("Diagnostyka (JSON)"):
        st.json(
            {
                "kontekst": {k: v for k, v in context.items() if k != "event"},
                "selekcje": [x.explanation for x in rows[:12]],
            },
            expanded=False,
        )
    st.caption(
        "Moduł LLM (Astra) jest opcjonalnym przyszłym rozszerzeniem; nie jest skonfigurowany i "
        "nie wpływa na analizę statystyczną. Reguły rynków: config/sts_rules.toml."
    )


def _explain(rows, context) -> list[str]:
    prediction = context["prediction"]
    out = []
    if context["freshness"]:
        f = context["freshness"]
        out.append(
            f"Dane: wyniki ligi {f['name']} do {f['last_result']} "
            f"({sum(f['providers'].values())} meczów; dostępność: "
            f"{', '.join(f['availability_basis'])})."
        )
    else:
        out.append("Dane: brak niezależnych wyników dla tego wydarzenia.")
    if prediction:
        p = prediction["probabilities"]
        out.append(
            f"Model przewiduje: 1 — {p['home']:.1%}, X — {p['draw']:.1%}, 2 — {p['away']:.1%}."
        )
        games = [t["games"] for t in context["teams"].values()]
        out.append(
            "Historia drużyn: " + " / ".join(str(g) for g in games) + " meczów — wystarczająca."
            if games and min(games) >= 10
            else "Historia drużyn jest krótka — prognoza mniej pewna."
        )
    else:
        out.append("Model nie przygotował prognozy — EV nie jest liczone.")
    best = max(
        (x for x in rows if x.ev_net is not None),
        key=lambda x: x.ev_net,
        default=None,
    )
    if best is not None and best.ev_net > 0:
        gap = (best.p_model - best.p_market) if best.p_market is not None else None
        out.append(
            f"Potencjalnie interesujące: {best.market_label} · "
            f"{c.SELECTIONS.get(best.selection, best.selection)} @ {best.odds} — EV netto "
            f"{float(best.ev_net):+.3f}"
            + (f", model o {gap * 100:+.1f} pp od rynku bez marży." if gap is not None else ".")
        )
        if best.reasons:
            out.append("Dlaczego może zostać odrzucone: " + "; ".join(best.reasons) + ".")
    elif prediction:
        out.append("Brak selekcji z dodatnim EV netto po podatku STS.")
    out.append(
        "Ograniczenia: Elo opiera się wyłącznie na wynikach (bez składów, kontuzji, terminarza i "
        "motywacji); model nie ma potwierdzonej przewagi nad rynkiem; różnica względem kursu "
        "częściej oznacza błąd modelu niż przewagę."
    )
    return out


# ---------------------------------------------------------------- Kupon dnia


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
    settings = c.settings()
    st.caption(
        f"Polityka {rules.version}: jakość ≥ {rules.min_data_quality}, świeży i potwierdzony "
        f"kurs, zwalidowany model (holdout n ≥ {rules.min_validation_events}, ECE ≤ "
        f"{rules.max_calibration_ece}, lepszy od rynku), zweryfikowane reguły rynku i podatek, "
        f"dolna granica EV netto > {rules.min_net_ev_lower_bound}, kurs ≤ {rules.max_odds}."
    )
    online = not settings.offline_mode and not c.demo_mode()
    cols = st.columns(2)
    refresh_offer = cols[0].checkbox(
        "Pobierz aktualną ofertę STS przed analizą",
        value=online and settings.sts_download_enabled,
        disabled=not (online and settings.sts_download_enabled),
        help="Oficjalny PDF STS, maks. raz na 10 minut na plik; wymaga zgody w Ustawieniach.",
    )
    refresh_results = cols[1].checkbox(
        "Uzupełnij wyniki sportowe (bezpłatne źródła)",
        value=online,
        disabled=not online,
        help="Tylko brakujący okres; płatne źródła nigdy nie są używane automatycznie.",
    )
    if st.button("▶ Uruchom analizę", type="primary"):
        bar = st.progress(0.0, text="Start analizy…")
        status = st.status("Analiza dnia — 8 etapów", expanded=True)

        def progress(index: int, name: str, detail: str) -> None:
            bar.progress((index + 1) / len(STAGES), text=f"Etap {index + 1}/{len(STAGES)}: {name}")
            status.write(f"**{index + 1}. {name}** — {detail}")

        run = run_daily_analysis(
            db,
            c.paths(),
            settings,
            day,
            now=c.now(),
            refresh_offer=refresh_offer,
            refresh_results=refresh_results,
            data_kinds=c.data_kinds(),
            progress=progress,
        )
        status.update(label="Analiza zakończona i zapisana", state="complete", expanded=False)
        st.session_state["coupon"] = run
    run = st.session_state.get("coupon")
    if run is None:
        st.info(
            "„Uruchom analizę” sprawdza ofertę STS, uzupełnia wyniki, generuje brakujące "
            "prognozy, liczy EV i zapisuje analizę (bez nadpisywania poprzednich)."
        )
        return
    report, candidates = run.report, run.candidates
    if report.day != day:
        st.warning("Wyniki dotyczą innego dnia — uruchom analizę ponownie.")
    if report.headline == NO_EDGE:
        c.banner(NO_EDGE, "noedge")
    else:
        c.banner(report.headline + " — analiza, nie rekomendacja gry", "ok")
    for line in report.diagnosis:
        st.write("• " + line)
    stages = {s["stage"]: s for s in run.stages}
    counts = report.counts
    st.subheader("A. Podsumowanie")
    row1 = st.columns(4)
    row1[0].metric("Wydarzenia w ofercie", counts.get("events", 0))
    row1[1].metric("Poprawnie odczytane rynki", counts.get("markets_ok", 0))
    row1[2].metric(
        "Wydarzenia z aktualnymi statystykami", stages.get("Dopasowanie", {}).get("considered", 0)
    )
    model_stage = stages.get("Modele", {})
    row1[3].metric(
        "Prognozy (nowe + aktualne)",
        model_stage.get("forecasts_saved", 0) + model_stage.get("forecasts_kept", 0),
    )
    row2 = st.columns(5)
    row2[4].metric(f"Typy z P ≥ {rules.probable_min_probability:.0%}", counts.get("probable", 0))
    row2[0].metric("Rynki obsługiwane przez model", counts.get("model_markets", 0))
    row2[1].metric("Kandydaci EV+", counts.get("ev_positive", 0))
    row2[2].metric("Eksperymentalni", counts.get("experimental", 0))
    row2[3].metric("Zwalidowani", counts.get("validated", 0))
    st.subheader(
        f"Najwyższe prawdopodobieństwo wygranej (model ≥ {rules.probable_min_probability:.0%})"
    )
    st.caption(
        "Ranking według prawdopodobieństwa z modelu (1X2 i podwójna szansa z tego samego "
        "modelu). Wysokie prawdopodobieństwo nie oznacza zysku: w STS wygrana to 0,88 × stawka × "
        "kurs, więc „Próg opłacalności” = 1 / (0,88 × kurs) — przy kursie 1,25 trzeba trafiać "
        "ponad 91% takich zakładów, a kurs poniżej 1,14 przynosi stratę nawet przy trafieniu. "
        "Łączenie typów w AKO mnoży ryzyko (3 typy po 80% to 51%). To informacja, nie rekomendacja."
    )
    if report.probable:
        st.dataframe(_probable_rows(report.probable), hide_index=True, width="stretch")
    else:
        st.write(
            "Brak selekcji z prognozą modelu na tym poziomie prawdopodobieństwa (model obejmuje "
            "1X2 i podwójną szansę w 5 ligach z aktualnymi wynikami)."
        )
    with st.expander("Etapy analizy"):
        st.dataframe(
            pd.DataFrame(
                [
                    {"Etap": s["stage"], "Status": s["status"], "Szczegóły": s["detail"]}
                    for s in run.stages
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    st.subheader("B. Ranking SOLO — kandydaci zwalidowani")
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
                        analysis_id=run.analysis_id,
                    )
                    st.success("Zapisano w historii (paper trading).")
                except ValueError as error:
                    st.error(str(error))
    else:
        st.write(
            "Brak selekcji spełniających wszystkie warunki. Ranking nie jest uzupełniany typami."
        )
    st.subheader("Ranking eksperymentalny — przewaga niepotwierdzona")
    if report.experimental:
        st.warning(
            "To nie są typy do gry: model nie ma walidacji rynkowej albo kurs wymaga "
            "potwierdzenia. Dodatnie EV niezwalidowanego modelu nie jest przewagą."
        )
        st.dataframe(_candidate_rows(report.experimental[:50]), hide_index=True, width="stretch")
    else:
        st.write("Brak kandydatów eksperymentalnych (brak prognoz z dodatnim EV netto).")
    st.subheader("C. AKO")
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
    with st.expander("Diagnostyka — uzasadnienia selekcji (JSON)"):
        query = st.text_input("Filtr wydarzenia").strip().lower()
        shown = (
            [x for x in candidates if query in x.event.lower()][:30] if query else candidates[:10]
        )
        for x in shown:
            st.markdown(
                f"**{x.event}** · {x.market_label} · "
                f"{c.SELECTIONS.get(x.selection, x.selection)} @ {x.odds} — {c.TIERS[x.tier]}"
            )
            st.json(x.explanation, expanded=False)
        st.code(json.dumps(run.stages, ensure_ascii=False, indent=1, default=str)[:20000])
    for warning in report.warnings:
        st.warning(warning)
    st.caption(
        "Aplikacja nie obstawia i nie gwarantuje wygranej. Wynik jednego dnia nie jest dowodem "
        "skuteczności; poprawa log loss nie oznacza zrealizowanej rentowności."
    )
