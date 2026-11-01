"""Modele i eksperymenty: independent data, model versions, validation and forecasts."""

import json

import pandas as pd
import streamlit as st

from sports_edge.app import common as c
from sports_edge.datasources import openfootball
from sports_edge.matching import confirm_alias
from sports_edge.models.registry import model_overview
from sports_edge.offers.sources import SourceBlocked
from sports_edge.services import (
    generate_predictions,
    import_openfootball_files,
    normalizer,
    run_research_experiment,
)
from sports_edge.sportsdata import coverage, import_results_csv

RESEARCH_AREAS = [
    {
        "Obszar": "Piłka nożna — 1X2, 5 czołowych lig (EPL, Bundesliga, LaLiga, Serie A, Ligue 1)",
        "Dane": "openfootball CC0, 2012–2026, wynik + do przerwy + godzina",
        "Oferta STS": "co tydzień",
        "Kursy historyczne": "brak legalnych bezpłatnych",
        "Wybór": "TAK — pierwszy eksperyment",
    },
    {
        "Obszar": "Piłka nożna — niższe ligi i inne kraje z openfootball",
        "Dane": "CC0, mniej sezonów",
        "Oferta STS": "częściowo",
        "Kursy historyczne": "brak",
        "Wybór": "później",
    },
    {
        "Obszar": "Tenis ATP/WTA",
        "Dane": "repozytoria J. Sackmanna usunięte (404); tennis-data.co.uk tylko do użytku prywatnego, zakaz treningu AI",
        "Oferta STS": "tak",
        "Kursy historyczne": "tylko tennis-data (zastrzeżenia)",
        "Wybór": "zablokowane licencyjnie",
    },
    {
        "Obszar": "Ekstraklasa",
        "Dane": "brak w openfootball; football-data.org wymaga klucza",
        "Oferta STS": "tak",
        "Kursy historyczne": "brak",
        "Wybór": "wymaga źródła",
    },
    {
        "Obszar": "Koszykówka / hokej",
        "Dane": "brak zweryfikowanego legalnego źródła zbiorczego",
        "Oferta STS": "tak",
        "Kursy historyczne": "brak",
        "Wybór": "wymaga źródła",
    },
]


def models_page() -> None:
    c.setup_page()
    st.title("Modele i eksperymenty")
    tabs = st.tabs(["Dane niezależne", "Modele i walidacja", "Prognozy i dopasowanie nazw"])
    with tabs[0]:
        _data()
    with tabs[1]:
        _models()
    with tabs[2]:
        _forecasts()


def _data() -> None:
    db = c.store()
    st.subheader("Ranking obszarów badawczych")
    st.dataframe(pd.DataFrame(RESEARCH_AREAS), hide_index=True, width="stretch")
    st.subheader("Pokrycie danych lokalnych")
    rows = coverage(db)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "Dostępność wyniku: brak znaczników publikacji w źródle — założenie 36 h po meczu "
            "(assumed_lag_36h). Backtest używa wyłącznie wyników z dni wcześniejszych."
        )
    else:
        st.info("Brak danych historycznych.")
    settings = c.settings()
    st.write(
        f"**openfootball** — {openfootball.LICENSE}. Ostatnia aktualizacja repozytorium: 22.09.2026."
    )
    if st.button(
        "Pobierz brakujące pliki openfootball (5 lig, 2012-13 – 2026-27)",
        disabled=settings.offline_mode,
    ):
        try:
            out = openfootball.fetch(
                c.paths().data / "sources" / "openfootball",
                leagues=list(openfootball.LEAGUES),
                seasons=openfootball.season_range("2012-13", "2026-27"),
                offline=settings.offline_mode,
            )
            st.json({k: len(v) for k, v in out.items()})
        except SourceBlocked as error:
            st.error(str(error))
    if st.button("Importuj pobrane pliki do bazy"):
        try:
            st.json(import_openfootball_files(db, c.paths(), now=c.now()))
        except ValueError as error:
            st.error(str(error))
    with st.form("csv"):
        st.write("Import własnego CSV z wynikami (wymagana licencja)")
        uploaded = st.file_uploader(
            "CSV: date, home, away, home_score, away_score[, available_at]", type=["csv"]
        )
        cols = st.columns(2)
        sport = cols[0].selectbox(
            "Sport", ["football", "ice_hockey", "basketball", "handball", "volleyball"]
        )
        competition = cols[1].text_input("Rozgrywki (kod)")
        source = st.text_input("Źródło danych")
        license_note = st.text_area("Podstawa prawna / licencja (wymagane)")
        if st.form_submit_button("Importuj") and uploaded is not None:
            try:
                st.json(
                    import_results_csv(
                        db,
                        uploaded.getvalue(),
                        file_name=uploaded.name,
                        source_name=source,
                        license_note=license_note,
                        sport=sport,
                        competition=competition,
                        now=c.now(),
                    )
                )
            except ValueError as error:
                st.error(str(error))


def _models() -> None:
    db = c.store()
    if st.button("▶ Uruchom eksperyment Elo (walk-forward + holdout, lokalnie)"):
        with st.spinner("Trening i walidacja…"):
            try:
                report = run_research_experiment(db, c.paths(), now=c.now())
                st.success(
                    f"Zarejestrowano wersję {report['version_id'][:12]}… — status {report['status']}"
                )
            except ValueError as error:
                st.error(str(error))
    versions = model_overview(db)
    if not versions:
        st.info("Brak modeli.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Utworzono": c.local_time(v["created_at"]),
                    "Model": f"{v['family']} {v['version']}",
                    "Status": v["status"],
                    "Dane do": c.local_time(v["trained_through"]),
                    "Log loss (holdout)": json.loads(v["validation_json"])
                    .get("holdout", {})
                    .get("log_loss"),
                }
                for v in versions
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    version = versions[0]
    validation = json.loads(version["validation_json"])
    st.subheader(f"Najnowszy: {version['family']} {version['version']} — {version['status']}")
    for reason in validation.get("status_reasons", []):
        st.warning(reason)
    holdout = validation.get("holdout", {})
    base = validation.get("baseline_climatology", {})
    cols = st.columns(4)
    cols[0].metric("Holdout n", holdout.get("n"))
    cols[1].metric(
        "Log loss",
        holdout.get("log_loss"),
        delta=round(holdout.get("log_loss", 0) - base.get("log_loss", 0), 4) if base else None,
        delta_color="inverse",
    )
    cols[2].metric("Brier", holdout.get("brier"))
    cols[3].metric("ECE", holdout.get("ece"))
    st.caption(
        f"Benchmark klimatologiczny (częstości z treningu): log loss {base.get('log_loss')}, Brier {base.get('brier')}."
    )
    st.write(
        "**Benchmark rynku**: "
        + (
            json.dumps(validation["market_benchmark"], ensure_ascii=False)
            if validation.get("market_benchmark")
            else "brak — nie ma legalnych historycznych kursów tych samych meczów; status „validated” jest niemożliwy."
        )
    )
    reliability = [r for r in holdout.get("reliability", []) if r["n"]]
    if reliability:
        st.subheader("Kalibracja (holdout, wszystkie stany 1/X/2)")
        frame = pd.DataFrame(
            {
                "Prognoza (średnia w koszyku)": [r["mean_p"] for r in reliability],
                "Częstość": [r["freq"] for r in reliability],
                "Idealna": [r["mean_p"] for r in reliability],
            }
        ).set_index("Prognoza (średnia w koszyku)")
        st.line_chart(frame)
        st.dataframe(pd.DataFrame(reliability), hide_index=True, width="stretch")
    st.subheader("Walk-forward według sezonów (walidacja + holdout)")
    st.dataframe(pd.DataFrame(validation.get("walk_forward_by_season", {})).T, width="stretch")
    st.subheader("Holdout według lig")
    st.dataframe(pd.DataFrame(validation.get("holdout_by_league", {})).T, width="stretch")
    st.subheader("Dobór hiperparametrów (tylko walidacja)")
    st.dataframe(pd.DataFrame(validation.get("selection", [])), hide_index=True, width="stretch")
    st.caption(
        f"Snapshot danych {validation.get('data_snapshot')}, stan kodu {validation.get('code_state')}, "
        f"reguła kolejności: {validation.get('ordering_rule')}."
    )


def _forecasts() -> None:
    db = c.store()
    if st.button("Generuj prognozy przedmeczowe dla nadchodzących wydarzeń STS"):
        try:
            st.session_state["forecasts"] = generate_predictions(db, c.paths(), now=c.now())
        except ValueError as error:
            st.error(str(error))
    out = st.session_state.get("forecasts")
    if not out:
        st.info(
            "Prognozy powstają tylko przed startem wydarzenia i tylko przy aktualnych danych niezależnych."
        )
        return
    st.write(f"Zapisane prognozy: **{out['saved']}**, pominięte: {len(out['skipped'])}")
    for line in out["skipped"][:30]:
        st.write("• " + line)
    if out["unmatched"]:
        st.subheader("Nazwy wymagające potwierdzenia")
        for i, item in enumerate(out["unmatched"][:30]):
            cols = st.columns([4, 4, 1])
            cols[0].write(f"STS: **{item['sts']}** ({item['league']})")
            cols[1].write(f"Propozycja: {item['suggestion'] or '—'} ({item['score']})")
            if item["suggestion"] and cols[2].button("✓", key=f"alias_{i}"):
                confirm_alias(
                    db,
                    sport="football",
                    alias=item["sts"],
                    canonical=item["suggestion"],
                    normalizer=normalizer(c.paths()),
                    at=c.now(),
                )
                st.success("Alias zapisany (potwierdzenie użytkownika).")
