"""Modele i eksperymenty: independent data, model versions, validation and forecasts."""

import json

import pandas as pd
import streamlit as st

from sports_edge.app import common as c
from sports_edge.datasources import openfootball
from sports_edge.datasources.football_data import ATTRIBUTION
from sports_edge.matching import confirm_alias
from sports_edge.models.registry import model_overview
from sports_edge.offers.sources import SourceBlocked
from sports_edge.results_engine import SOURCE_LABELS, SOURCE_PRIORITY, conflicts, league_freshness
from sports_edge.services import (
    RESULTS_COMPETITIONS,
    generate_predictions,
    import_openfootball_files,
    normalizer,
    results_providers,
    run_research_experiment,
    update_results_data,
)
from sports_edge.sportsdata import coverage, import_results_csv

# Checked 2026-11-01; see docs/task_004_report.md section D for the sources of each claim.
RESULTS_SOURCES = [
    {
        "Źródło": "football-data.org API v4 (Free Tier)",
        "Legalność": "warunki publiczne; atrybucja; klucz użytkownika",
        "Aktualność": "bieżące wyniki (lastUpdated)",
        "Zakres": "12 rozgrywek piłkarskich (5 lig TOP, bez Ekstraklasy)",
        "Automatyzacja": "API, 10 zapytań/min",
        "Koszt": "0 zł",
        "Rola": "AKTYWNE — bieżący sezon",
    },
    {
        "Źródło": "openfootball (CC0)",
        "Legalność": "domena publiczna",
        "Aktualność": "nieregularna (repozytorium bez wyników po 20.09.2026)",
        "Zakres": "piłka, długa historia",
        "Automatyzacja": "pliki JSON",
        "Koszt": "0 zł",
        "Rola": "AKTYWNE — historia od 2012",
    },
    {
        "Źródło": "API-Sports (API-Football i inne)",
        "Legalność": "regulamin akceptowany przy rejestracji (niedostępny do odczytu automatycznego)",
        "Aktualność": "bieżące (brak znacznika aktualizacji — czas obserwacji)",
        "Zakres": "Ekstraklasa i inne ligi; koszykówka, hokej, siatkówka; darmowy plan: ostatnie sezony",
        "Automatyzacja": "API, 100 zapytań/dzień na API, limit z rezerwą",
        "Koszt": "0 zł (plan darmowy)",
        "Rola": "WDROŻONE — Ekstraklasa; inne dyscypliny po weryfikacji na kluczu",
    },
    {
        "Źródło": "STS — wyniki na stronie",
        "Legalność": "brak API; robots.txt blokuje resultDate",
        "Aktualność": "po rozliczeniu",
        "Zakres": "rynki STS",
        "Automatyzacja": "niedozwolona",
        "Koszt": "—",
        "Rola": "NIEAKTYWNE — wpis ręczny",
    },
    {
        "Źródło": "Superbet — wyniki",
        "Legalność": "robots.txt blokuje /wyniki",
        "Aktualność": "—",
        "Zakres": "—",
        "Automatyzacja": "niedozwolona",
        "Koszt": "—",
        "Rola": "NIEAKTYWNE",
    },
    {
        "Źródło": "Flashscore",
        "Legalność": "regulamin zakazuje pobierania i kopiowania bazy",
        "Aktualność": "na żywo",
        "Zakres": "szeroki",
        "Automatyzacja": "niedozwolona",
        "Koszt": "licencja B2B",
        "Rola": "WYKLUCZONE",
    },
]

RESEARCH_AREAS = [
    {
        "Obszar": "Piłka nożna — 1X2, 5 czołowych lig (EPL, Bundesliga, LaLiga, Serie A, Ligue 1)",
        "Dane": "openfootball CC0 (2012–2026) + football-data.org (bieżący sezon)",
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
        "Dane": "brak w openfootball i w Free Tier football-data.org",
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
    tabs = st.tabs(
        ["Wyniki sportowe", "Dane historyczne", "Modele i walidacja", "Prognozy i nazwy"]
    )
    with tabs[0]:
        _results()
    with tabs[1]:
        _data()
    with tabs[2]:
        _models()
    with tabs[3]:
        _forecasts()


def _results() -> None:
    db = c.store()
    settings = c.settings()
    st.subheader("Źródła wyników (hierarchia przy rozbieżnościach)")
    st.caption(
        " > ".join(SOURCE_LABELS[p] for p in sorted(SOURCE_PRIORITY, key=SOURCE_PRIORITY.get))
        + " — zgodnie z zasadą STS R 14.6 (najpierw oficjalny organizator)."
    )
    providers = results_providers(c.paths(), settings)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Źródło": p.name,
                    "Status": "✅ aktywne" if s.active else "⛔ nieaktywne",
                    "Opis": s.reason,
                    "Licencja": p.license,
                }
                for p, s in ((p, p.status(offline=settings.offline_mode)) for p in providers)
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.subheader("Świeżość historii lig")
    fresh = league_freshness(db, RESULTS_COMPETITIONS, now=c.now())
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Liga": f["name"],
                    "Sport": f["sport"],
                    "Mecze": f["matches"],
                    "Ostatni wynik": f["last_result"],
                    "Dni od ostatniego": f["days_since"],
                    "Aktualna (≤ 21 dni)": "tak" if f["fresh"] else "NIE",
                    "Źródła (mecze)": ", ".join(f"{k}: {v}" for k, v in f["providers"].items()),
                    "Dostępność": ", ".join(f["availability_basis"]),
                    "Konflikty": f["conflicts"],
                }
                for f in fresh
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    if st.button(
        "Uzupełnij brakujące wyniki (bezpłatne źródła)",
        disabled=settings.offline_mode or c.demo_mode(),
        help="Pobiera tylko okres od ostatniego wyniku (z 3-dniowym zakładem na korekty).",
    ):
        with st.spinner("Aktualizacja wyników…"):
            st.session_state["results_update"] = update_results_data(
                db, c.paths(), settings, now=c.now()
            )
    report = st.session_state.get("results_update")
    if report:
        cols = st.columns(5)
        cols[0].metric("Nowe obserwacje", report["inserted"])
        cols[1].metric("Potwierdzenia drugim źródłem", report["confirmations"])
        cols[2].metric("Korekty", len(report["corrections"]))
        cols[3].metric("Konflikty", len(report["conflicts"]))
        cols[4].metric("Zapytania", report["requests"])
        for line in report["errors"] + report["incomplete"][:20]:
            st.write("• " + line)
        if report["unmatched"]:
            st.subheader("Nazwy drużyn do potwierdzenia")
            for i, item in enumerate(report["unmatched"][:30]):
                row = st.columns([4, 4, 1])
                row[0].write(f"Źródło: **{item['source']}** ({item['competition']})")
                row[1].write(f"Propozycja: {item['suggestion'] or '—'} ({item['score']})")
                if item["suggestion"] and row[2].button("✓", key=f"result_alias_{i}"):
                    confirm_alias(
                        db,
                        sport="football",
                        alias=item["source"],
                        canonical=item["suggestion"],
                        normalizer=normalizer(c.paths()),
                        at=c.now(),
                    )
                    st.success("Alias zapisany — uruchom aktualizację ponownie.")
    found = conflicts(db, until=c.now(), competitions=RESULTS_COMPETITIONS)
    if found:
        st.subheader("Rozbieżne wyniki między źródłami")
        st.dataframe(pd.DataFrame(found), hide_index=True, width="stretch")
    st.subheader("Ranking dostawców wyników")
    st.dataframe(pd.DataFrame(RESULTS_SOURCES), hide_index=True, width="stretch")
    st.caption(f"{ATTRIBUTION}. Klucz API wyłącznie w zmiennej środowiskowej lub w pliku .env.")


def _data() -> None:
    db = c.store()
    st.subheader("Ranking obszarów badawczych")
    st.dataframe(pd.DataFrame(RESEARCH_AREAS), hide_index=True, width="stretch")
    st.subheader("Pokrycie danych lokalnych")
    rows = coverage(db)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "Dostępność wyniku: openfootball bez znaczników publikacji — założenie 36 h po meczu "
            "(assumed_lag_36h); football-data.org — znacznik lastUpdated źródła "
            "(source_last_updated). Backtest używa wyłącznie wyników z dni wcześniejszych."
        )
    else:
        st.info("Brak danych historycznych.")
    settings = c.settings()
    st.write(
        f"**openfootball** — {openfootball.LICENSE}. Repozytorium nie publikuje bieżących "
        "wyników (sprawdzono 01.11.2026) — bieżący sezon uzupełnia football-data.org."
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
        official = st.checkbox(
            "Wyniki przepisane z oficjalnej strony organizatora (najwyższy priorytet przy "
            "rozbieżnościach)"
        )
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
                        provider="manual_official" if official else None,
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
