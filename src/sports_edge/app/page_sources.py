"""Źródła i ustawienia: data sources at a glance, STS files, settings and diagnostics."""

import streamlit as st

from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.app.pages_offers import data_diagnostics
from sports_edge.app.pages_settings import _local_data, _profile, _sources, _sts
from sports_edge.datasources.football_data import api_key
from sports_edge.repository import imports
from sports_edge.results_engine import SOURCE_LABELS, provider_of
from sports_edge.services import RESEARCH_COMPETITIONS, results_providers
from sports_edge.settings import local_secret

SOURCES = (
    {
        "id": "football_data_org",
        "name": "football-data.org",
        "scope": "bieżące wyniki 5 czołowych lig",
        "key": "FOOTBALL_DATA_API_KEY",
        "limits": "plan darmowy: 10 zapytań na minutę; atrybucja wymagana",
    },
    {
        "id": "pitchapi",
        "name": "PitchAPI",
        "scope": "wyniki, statystyki meczowe i składy: 5 lig, Championship, Eredivisie, "
        "Liga Portugal, Pro League, 2. Bundesliga, Süper Lig, Ekstraklasa",
        "key": "PITCHAPI_KEY",
        "limits": "plan darmowy, uczciwe korzystanie; regulamin nieopublikowany — użytek prywatny",
    },
    {
        "id": "openfootball",
        "name": "openfootball (CC0)",
        "scope": "historia 5 czołowych lig od 2012/13",
        "key": None,
        "limits": "repozytorium aktualizowane nieregularnie",
    },
    {
        "id": "api_sports",
        "name": "API-Sports",
        "scope": "historia Ekstraklasy 2022/23–2024/25; piłka, koszykówka, hokej",
        "key": "API_SPORTS_KEY",
        "limits": "plan darmowy: tylko sezony 2022–2024, 100 zapytań na dobę",
    },
)


def sources_page() -> None:
    c.setup_page()
    ui.app_header(
        "Źródła i ustawienia", "Dane STS, wyniki, statystyki, synchronizacja i konfiguracja"
    )
    # a view switch instead of tabs: only the chosen view is computed (tabs run all bodies)
    views = {
        "Źródła danych": _panel,
        "Oferta STS": _sts_files,
        "Ustawienia": _sts,
        "Podatki i reguły": _profile,
        "Dane lokalne": _local_data,
        "Zbieranie danych i monitoring": _monitoring,
        "Diagnostyka": _diagnostics,
    }
    view = st.segmented_control("Widok", list(views), default="Źródła danych", key="sources_view")
    views[view or "Źródła danych"]()


def _diagnostics() -> None:
    data_diagnostics()
    _sources()


def _monitoring() -> None:
    """Data collection engine, job runs, storage, integrity, backup with a restore test."""
    import pandas as pd

    from sports_edge import collection, maintenance
    from sports_edge.backup import create_backup
    from sports_edge.services import update_results_data

    db, now = c.store(), c.now()
    ui.section("Raport monitoringu", "źródła, zadania, rozliczenia, modele i baza")
    st.dataframe(
        pd.DataFrame(maintenance.monitoring(db, c.paths(), c.settings(), now=now)),
        hide_index=True,
        width="stretch",
    )
    report = collection.completeness(db, now=now)
    ui.metric_cards(
        [
            ("Prognozy zapisane", str(report["predictions"]), "wszystkie prospektywne"),
            ("Rozliczone", str(sum(report["settlements"].values())), "z wersjonowaniem korekt"),
            ("Oczekujące", str(report["pending"]), "mecz rozpoczęty, brak wyniku"),
            ("Bez wyniku > 48 h", str(report["missing_results_count"]), "nierozstrzygnięte"),
        ]
    )
    if report["settlements"]:
        st.caption(
            "Statusy rozliczeń: "
            + ", ".join(
                f"{collection.STATUS_LABELS.get(k, k)}: {v}"
                for k, v in report["settlements"].items()
            )
        )
    online = not c.settings().offline_mode
    if st.button(
        "Uzupełnij po przerwie",
        icon=":material/sync:",
        help="Rozlicza zaległe "
        "prognozy; w trybie online także aktualizuje wyniki (maks. 14 dni przerwy)",
    ):
        found = collection.catch_up(
            db,
            now=c.now(),
            update=(lambda: update_results_data(db, c.paths(), c.settings(), now=c.now()))
            if online
            else None,
        )
        st.json(found, expanded=False)
    ui.section("Baza danych", "rozmiar, przyrost, integralność i kopia zapasowa")
    disk = maintenance.storage_report(db, c.paths(), now=now)
    ui.metric_cards(
        [
            ("Rozmiar bazy", f"{disk['bytes'] / 1e6:.1f} MB", f"schemat {disk['schema']}"),
            (
                "Przyrost / miesiąc",
                f"~{disk['projected_bytes_per_month'] / 1e6:.1f} MB",
                "szacunek z 7 dni",
            ),
            ("Pliki surowe", f"{disk['raw_bytes'] / 1e6:.1f} MB", "data/sources, data/raw"),
            ("Kopie zapasowe", f"{disk['backups_bytes'] / 1e6:.1f} MB", "folder backups/"),
            (
                "Integralność",
                "OK" if disk["integrity"]["ok"] else "BŁĄD",
                f"klucze obce: {disk['integrity']['foreign_key_errors']} błędów",
            ),
        ]
    )
    ui.explanation("Plan archiwizacji (nic nie jest usuwane automatycznie)", disk["archive_plan"])
    if st.button("Utwórz kopię zapasową i sprawdź odtworzenie", icon=":material/backup:"):
        folder = create_backup(c.paths(), now=c.now())
        test = maintenance.restore_test(folder / "sports_edge.db", db)
        (st.success if test["ok"] else st.error)(
            f"Kopia {folder.name}: test odtworzenia {'udany' if test['ok'] else 'nieudany'} "
            f"(integralność {test['integrity']}, tabel {test['tables']})"
            + (": " + "; ".join(test["problems"]) if test["problems"] else "")
        )


def _panel() -> None:
    db = c.store()
    settings = c.settings()
    providers = {p.id: p for p in results_providers(c.paths(), settings)}
    stats = {}
    for row in db.rows(
        """SELECT h.provider, d.source_name, COUNT(*) n, MAX(h.observed_at) last
        FROM historical_results h LEFT JOIN data_imports d ON d.id=h.import_id
        GROUP BY h.provider, d.source_name"""
    ):
        family = provider_of(row)
        found = stats.setdefault(family, {"n": 0, "last": ""})
        found["n"] += row["n"]
        found["last"] = max(found["last"], row["last"] or "")
    leagues = {}
    freshness = c.cached_freshness(db, c.data_version(db), c.minute())
    for league in freshness:
        for family in league["providers"]:
            leagues.setdefault(family, []).append(league["name"])
    cards = [_sts_card(db, settings)]
    for source in SOURCES:
        family = source["id"]
        adapters = [p for p in providers.values() if getattr(p, "family", p.id) == family]
        status = [p.status(offline=settings.offline_mode) for p in adapters]
        active = any(s.active for s in status)
        has_key = source["key"] is None or bool(
            api_key(c.paths().root)
            if family == "football_data_org"
            else local_secret(source["key"], c.paths().root)
        )
        found = stats.get(family) or stats.get(
            "openfootball" if family == "openfootball" else family, {}
        )
        tone = "ok" if active else "warn" if has_key else "neutral"
        cards.append(
            ui.card(
                f"<b>{ui.esc(source['name'])}</b><br>"
                + ui.pill("aktywne" if active else "nieaktywne", tone)
                + ui.pill(
                    "klucz: jest"
                    if source["key"] and has_key
                    else "bez klucza"
                    if not source["key"]
                    else "brak klucza",
                    "neutral" if has_key else "warn",
                )
                + ui.key_values(
                    [
                        ("Zakres", source["scope"]),
                        ("Status", "; ".join(s.reason for s in status) or "—"),
                        ("Rekordy", f"{found.get('n', 0)} wyników"),
                        (
                            "Ostatnia aktualizacja",
                            c.local_time(found.get("last")) if found.get("last") else "—",
                        ),
                        ("Ligi z danymi", ", ".join(leagues.get(family, [])) or "—"),
                        ("Ograniczenia", source["limits"]),
                    ]
                ),
                tone="" if active else "warn",
            )
        )
    cards.append(
        ui.card(
            "<b>OpenScores (MCP)</b><br>"
            + ui.pill("sprawdzone — niewdrożone", "neutral")
            + ui.key_values(
                [
                    ("Zakres", "top 5 lig, wyłącznie sezon 2025/26"),
                    ("Powód", "brak bieżącego sezonu — nie nadaje się do prognoz"),
                ]
            )
        )
    )
    ui.card_grid(cards)
    st.caption(
        f"Ligi z modelem: {len(RESEARCH_COMPETITIONS)}. Dostawca jest pokazany jako źródło ligi "
        "tylko wtedy, gdy w bazie są jego wyniki — sam adapter nie oznacza pokrycia. Źródła, które "
        "odmówiły dostępu do bieżącego sezonu, nie są odpytywane ponownie przez 7 dni. "
        + " · ".join(f"{k}: {v}" for k, v in SOURCE_LABELS.items())
    )


def _sts_card(db, settings) -> str:
    rows = [i for i in imports(db) if i["data_kind"] in c.data_kinds()]
    last = rows[0] if rows else None
    newest = max((i["generated_at"] or "" for i in rows if i["rows_total"]), default="")
    empty = sum(1 for i in rows if i["rows_total"] == 0)
    allowed = not settings.offline_mode and settings.sts_download_enabled
    return ui.card(
        "<b>STS — oferta PDF</b><br>"
        + ui.pill(
            "pobieranie na żądanie" if allowed else "tylko import ręczny",
            "ok" if allowed else "neutral",
        )
        + ui.key_values(
            [
                ("Zakres", "oficjalne pliki: na dziś, na jutro, kolejne dni"),
                ("Najnowszy dokument z kursami", c.local_time(newest) if newest else "—"),
                (
                    "Ostatni import",
                    f"{c.local_time(last['ingested_at'])} ({last['rows_total']} wierszy)"
                    if last
                    else "—",
                ),
                ("Puste pliki", str(empty)),
                ("Ograniczenia", "maks. raz na 10 minut na plik; plik „na dziś” bywa pusty"),
            ]
        ),
        tone="accent",
    )


def _sts_files() -> None:
    ui.section("Oficjalne pliki STS", "wynik każdego pobrania jest pokazany wprost")
    from sports_edge.app.pages_offers import _import_box

    _import_box()
