"""Ustawienia: STS download, import, data sources, tax profile and rules, local data."""

from dataclasses import asdict

import pandas as pd
import streamlit as st

from sports_edge.app import common as c
from sports_edge.backup import create_backup, list_backups
from sports_edge.bookmakers import load_profiles, load_rules
from sports_edge.datasources.football_data import api_key
from sports_edge.export import export_csv, export_json
from sports_edge.offers.sources import API_SOURCES
from sports_edge.pdf.engine import ParseError
from sports_edge.repository import archived_summary, counts, imports
from sports_edge.services import import_pdf_bytes
from sports_edge.settings import local_secret, save_settings


def settings_page() -> None:
    c.setup_page()
    st.title("Ustawienia")
    tabs = st.tabs(
        [
            "Pobieranie STS",
            "Import PDF",
            "Źródła danych",
            "Profil podatkowy i reguły",
            "Dane lokalne",
        ]
    )
    with tabs[0]:
        _sts()
    with tabs[1]:
        _import()
    with tabs[2]:
        _sources()
    with tabs[3]:
        _profile()
    with tabs[4]:
        _local_data()


def _sts() -> None:
    settings = c.settings()
    offline = st.toggle(
        "Tryb offline (brak jakichkolwiek połączeń sieciowych)", value=settings.offline_mode
    )
    sts = st.toggle(
        "Zezwól na pobieranie oficjalnych PDF STS na żądanie (retail-api.sts.pl)",
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
        "Tolerancja godziny przy dopasowaniu (min)",
        min_value=0,
        max_value=60,
        value=settings.match_tolerance_minutes,
    )
    st.markdown("**Źródła wyników sportowych** (bezpłatne; płatne nigdy automatycznie)")
    football_data = st.toggle(
        "football-data.org — bieżące wyniki 5 lig (wymaga własnego bezpłatnego klucza)",
        value=settings.football_data_enabled,
    )
    st.caption(
        "Klucz FOOTBALL_DATA_API_KEY: "
        + ("ustawiony (zmienna środowiskowa lub .env)" if api_key(c.paths().root) else "brak")
        + ". Aplikacja nigdy nie wyświetla ani nie zapisuje klucza w repozytorium."
    )
    api_sports = st.toggle(
        "API-Sports — Ekstraklasa (piłka); koszykówka i hokej po weryfikacji schematu "
        "(własny bezpłatny klucz, 100 zapytań/dzień na API)",
        value=settings.api_sports_enabled,
    )
    st.caption(
        "Klucz API_SPORTS_KEY: "
        + ("ustawiony" if local_secret("API_SPORTS_KEY", c.paths().root) else "brak")
        + " — rejestracja: dashboard.api-football.com (przeczytaj regulamin przy rejestracji)."
    )
    openfootball_updates = st.toggle(
        "openfootball — ponowny odczyt plików bieżącego sezonu (CC0)",
        value=settings.openfootball_update_enabled,
    )
    if st.button("Zapisz ustawienia"):
        settings.offline_mode, settings.sts_download_enabled = offline, sts
        settings.retain_raw_pdf, settings.nominal_stake = retain, stake
        settings.max_quote_age_minutes, settings.match_tolerance_minutes = int(age), int(tolerance)
        settings.football_data_enabled = football_data
        settings.openfootball_update_enabled = openfootball_updates
        settings.api_sports_enabled = api_sports
        save_settings(c.paths(), settings)
        st.success("Zapisano w data/settings.json")
    st.info(
        "Harmonogram automatycznego pobierania jest wyłączony: STS publikuje linki PDF dla "
        "użytkowników (sts.pl/oferta-pdf), ale dopuszczalności cyklicznych odczytów nie "
        "potwierdzono. Pobieranie tylko przyciskiem, maks. raz na 10 minut na plik."
    )


def _import() -> None:
    uploaded = st.file_uploader("PDF z ofertą STS", type=["pdf"], key="settings_upload")
    if uploaded is not None and st.button("Importuj plik"):
        try:
            outcome = import_pdf_bytes(
                c.store(),
                c.paths(),
                c.settings(),
                uploaded.getvalue(),
                file_name=uploaded.name,
                now=c.now(),
                data_kind="demo" if c.demo_mode() else "prospective",
                source_id="fixture" if c.demo_mode() else "sts_pdf_manual",
            )
        except (ParseError, ValueError) as error:
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
                    "fetched_at",
                    "file_name",
                    "offer_kind",
                    "generated_at",
                    "rows_total",
                    "rows_ok",
                    "rows_review",
                    "unparsed",
                    "quality",
                    "parser",
                    "data_kind",
                    "raw_sha256",
                    "source_url",
                ]
            ],
            hide_index=True,
            width="stretch",
        )


def _sources() -> None:
    st.write("Niezależne dane sportowe i ich ocena: zakładka **Modele i eksperymenty → Dane**.")
    st.subheader("Zewnętrzne API kursów (opcjonalne, płatne — bez zgody nie są wywoływane)")
    st.dataframe(pd.DataFrame(API_SOURCES), hide_index=True, width="stretch")
    st.caption(
        "Klucze tylko w zmiennych środowiskowych. Oficjalnego publicznego API STS nie znaleziono."
    )


def _profile() -> None:
    profile = load_profiles(c.paths().config)["sts"]
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
    rules = load_rules(c.paths().config)
    st.subheader("Zweryfikowane reguły rynków STS")
    st.dataframe(
        pd.DataFrame([asdict(r) for r in rules.rules.values()]), hide_index=True, width="stretch"
    )
    st.json({"dokumenty": rules.documents, "zasady ogólne": rules.general}, expanded=False)
    st.caption("Rynki spoza tej listy są niezweryfikowane i nie mogą dać kandydata zwalidowanego.")


def _local_data() -> None:
    paths = c.paths()
    db = c.store()
    st.write(f"Katalog projektu: `{paths.root}` · baza: `{db.path}`")
    st.json(counts(db))
    archived = archived_summary(db)
    if archived:
        st.write("**Archiwum (nieaktywne w analizach)**")
        st.dataframe(pd.DataFrame(archived), hide_index=True, width="stretch")
    migrations = db.rows(
        "SELECT version, applied_at, description, backup_path FROM schema_migrations"
    )
    if migrations:
        st.write("**Migracje schematu**")
        st.dataframe(pd.DataFrame(migrations), hide_index=True, width="stretch")
    col1, col2 = st.columns(2)
    if col1.button("Eksport JSON"):
        target = export_json(
            db, paths.exports / f"export_{c.now():%Y%m%d_%H%M%S}.json", now=c.now()
        )
        st.success(f"Zapisano {target}")
        st.download_button(
            "Pobierz JSON", target.read_bytes(), file_name=target.name, mime="application/json"
        )
    if col2.button("Eksport CSV"):
        files = export_csv(db, paths.exports / f"csv_{c.now():%Y%m%d_%H%M%S}")
        st.success(f"Zapisano {len(files)} plików w {files[0].parent}")
    include_raw = st.checkbox("Dołącz kopie PDF (data/raw) — mogą być duże")
    if st.button("Utwórz kopię zapasową"):
        target = create_backup(
            paths, now=c.now().astimezone(c.WARSAW).replace(tzinfo=None), include_raw=include_raw
        )
        st.success(f"Utworzono {target}")
    backups = list_backups(paths)
    if backups:
        st.dataframe(pd.DataFrame(backups), hide_index=True, width="stretch")
