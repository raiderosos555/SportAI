"""Settings: import, data sources, models, tax profiles, local data and schedule."""

from dataclasses import asdict

import pandas as pd
import streamlit as st

from sports_edge.app import common as c
from sports_edge.backup import create_backup, list_backups
from sports_edge.bookmakers import load_profiles
from sports_edge.offers.sources import API_SOURCES, ArbiScanAdapter
from sports_edge.pdf.engine import ParseError
from sports_edge.repository import counts, imports
from sports_edge.services import import_pdf_bytes, policy
from sports_edge.settings import save_settings
from sports_edge.sportsdata import coverage, import_results_csv, train_baseline


def settings_page() -> None:
    c.setup_page()
    st.title("Ustawienia")
    tabs = st.tabs(
        ["Import PDF", "Źródła i API", "Modele", "Profile podatkowe", "Dane lokalne", "Harmonogram"]
    )
    with tabs[0]:
        _import()
    with tabs[1]:
        _sources()
    with tabs[2]:
        _models()
    with tabs[3]:
        _profiles()
    with tabs[4]:
        _local_data()
    with tabs[5]:
        st.info(
            "Harmonogram automatycznego pobierania jest wyłączony. STS udostępnia linki PDF dla "
            "użytkowników (sts.pl/oferta-pdf), ale dopuszczalność cyklicznych, automatycznych odczytów "
            "nie została potwierdzona w regulaminie; Superbet nie publikuje PDF. Pobieranie odbywa się "
            "wyłącznie ręcznie, przyciskiem, z limitem 1 pobrania / 10 min na plik."
        )


def _import() -> None:
    uploaded = st.file_uploader(
        "PDF z ofertą (STS, Superbet lub inny)", type=["pdf"], key="settings_upload"
    )
    bookmaker = st.selectbox(
        "Bukmacher",
        ["auto", "sts", "superbet"],
        format_func=lambda b: {"auto": "Wykryj automatycznie"}.get(b, b),
    )
    if uploaded is not None and st.button("Importuj plik"):
        try:
            outcome = import_pdf_bytes(
                c.store(),
                c.paths(),
                c.settings(),
                uploaded.getvalue(),
                file_name=uploaded.name,
                bookmaker=None if bookmaker == "auto" else bookmaker,
                now=c.now(),
                data_kind="demo" if c.demo_mode() else "prospective",
                source_id="fixture" if c.demo_mode() else None,
            )
        except ParseError as error:
            st.error(f"Import odrzucony: {error}")
        else:
            st.success("Duplikat — pominięto." if outcome.duplicate else "Zaimportowano.")
            st.json(outcome.summary)
    rows = imports(c.store())
    if rows:
        st.dataframe(
            pd.DataFrame(rows)[
                [
                    "ingested_at",
                    "bookmaker_id",
                    "file_name",
                    "offer_kind",
                    "generated_at",
                    "rows_total",
                    "rows_ok",
                    "rows_review",
                    "unparsed",
                    "quality",
                    "parser",
                    "parser_verified",
                    "data_kind",
                    "raw_sha256",
                ]
            ],
            hide_index=True,
            width="stretch",
        )


def _sources() -> None:
    settings = c.settings()
    st.subheader("Tryb pracy")
    offline = st.toggle(
        "Tryb offline (brak jakichkolwiek połączeń sieciowych)", value=settings.offline_mode
    )
    sts = st.toggle(
        "Zezwól na ręczne pobieranie oficjalnych PDF STS (retail-api.sts.pl)",
        value=settings.sts_download_enabled,
    )
    retain = st.toggle(
        "Przechowuj kopię źródłowego PDF (tylko jeśli warunki źródła na to pozwalają)",
        value=settings.retain_raw_pdf,
    )
    cols = st.columns(3)
    stake = cols[0].text_input("Stawka nominalna do EV (PLN)", value=settings.nominal_stake)
    age = cols[1].number_input(
        "Maks. wiek kursu (min)", min_value=5, value=settings.max_quote_age_minutes
    )
    tolerance = cols[2].number_input(
        "Tolerancja czasu startu przy dopasowaniu (min)",
        min_value=0,
        max_value=60,
        value=settings.match_tolerance_minutes,
    )
    if st.button("Zapisz ustawienia"):
        settings.offline_mode, settings.sts_download_enabled, settings.retain_raw_pdf = (
            offline,
            sts,
            retain,
        )
        settings.nominal_stake, settings.max_quote_age_minutes = stake, int(age)
        settings.match_tolerance_minutes = int(tolerance)
        save_settings(c.paths(), settings)
        st.success("Zapisano w data/settings.json")
    st.subheader("Zewnętrzne API (opcjonalne, płatne — bez zgody nie są wywoływane)")
    st.dataframe(pd.DataFrame(API_SOURCES), hide_index=True, width="stretch")
    st.caption(
        "Klucze tylko w zmiennych środowiskowych (ARBISCAN_API_KEY, PROPLINE_API_KEY), nigdy w plikach projektu."
    )
    if st.button(
        "Sprawdź ArbiScan /health (bezpłatne, bez klucza)", disabled=settings.offline_mode
    ):
        try:
            st.json(ArbiScanAdapter().health(offline=settings.offline_mode))
        except Exception as error:  # surface any network failure to the user
            st.error(f"Błąd: {error}")
    st.caption(
        "Oficjalnie dokumentowanych API dla zewnętrznych deweloperów STS i Superbet nie znaleziono. "
        "Wewnętrzne endpointy aplikacji internetowych nie są traktowane jako publiczne API."
    )


def _models() -> None:
    db = c.store()
    st.write(
        "Baseline: **Elo z marginesem remisu** dla 1X2 (piłka nożna). Wymaga licencjonowanych wyników "
        "historycznych (CSV: date, home, away, home_score, away_score[, season, available_at])."
    )
    st.dataframe(pd.DataFrame(coverage(db)), hide_index=True, width="stretch")
    with st.form("history"):
        uploaded = st.file_uploader("CSV z wynikami historycznymi", type=["csv"])
        cols = st.columns(2)
        sport = cols[0].selectbox(
            "Sport", ["football", "ice_hockey", "basketball", "handball", "volleyball"]
        )
        competition = cols[1].text_input("Rozgrywki (np. HISZPANIA - LALIGA)")
        source = st.text_input("Nazwa źródła danych")
        license_note = st.text_area("Podstawa prawna / licencja użycia danych (wymagane)")
        if st.form_submit_button("Importuj wyniki") and uploaded is not None:
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
    pairs = [(r["sport"], r["competition"]) for r in coverage(db)]
    if pairs:
        choice = st.selectbox(
            "Trenuj baseline dla", pairs, format_func=lambda p: f"{p[0]} / {p[1]}"
        )
        if st.button("Trenuj i waliduj (walk-forward)"):
            try:
                result = train_baseline(
                    db,
                    sport=choice[0],
                    competition=choice[1],
                    now=c.now(),
                    policy=policy(c.paths()),
                )
                st.json({k: v for k, v in result.items() if k != "reliability"})
            except ValueError as error:
                st.error(str(error))


def _profiles() -> None:
    for profile in load_profiles(c.paths().config).values():
        st.subheader(profile.name)
        valid = profile.cost_valid_on(c.today_local())
        c.pills(
            f"profil {profile.cost.id}",
            f"ważny {profile.cost.valid_from} – {profile.cost.valid_until}"
            + ("" if valid else " (WYGASŁ)"),
            "podatki zweryfikowane" if profile.tax_verified else "podatki: założenie",
        )
        cost = {k: str(v) for k, v in asdict(profile.cost).items()}
        st.json(
            {"koszty": cost, **{k: v for k, v in profile.sections.items() if k != "bookmaker"}},
            expanded=False,
        )


def _local_data() -> None:
    paths = c.paths()
    st.write(f"Katalog projektu: `{paths.root}`")
    st.write(f"Baza: `{c.store().path}`")
    st.json(counts(c.store()))
    include_raw = st.checkbox("Dołącz kopie PDF (data/raw) — mogą być duże")
    if st.button("Utwórz kopię zapasową"):
        target = create_backup(
            paths, now=c.now().astimezone(c.WARSAW).replace(tzinfo=None), include_raw=include_raw
        )
        st.success(f"Utworzono {target}")
    backups = list_backups(paths)
    if backups:
        st.dataframe(pd.DataFrame(backups), hide_index=True, width="stretch")
    st.caption(
        "Dane (baza, PDF, eksporty, kopie) zostają tylko na tym komputerze — katalogi data/ i "
        "backups/ są wyłączone z repozytorium Git."
    )
