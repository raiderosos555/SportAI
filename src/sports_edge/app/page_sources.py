"""Źródła i ustawienia: data sources at a glance, STS files, settings and diagnostics."""

import streamlit as st

from sports_edge.app import common as c
from sports_edge.app import international_ui as iui
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
        "Puchary i reprezentacje": lambda: iui.coverage_block(c.store(), c.now()),
        "Oferta STS": _sts_files,
        "Ustawienia": _sts,
        "Podatki i reguły": _profile,
        "Dane lokalne": _local_data,
        "Zbieranie danych i monitoring": _monitoring,
        "Diagnostyka": _diagnostics,
    }
    # TASK 014 §4.2: a direct link (?widok=Ustawienia) opens the chosen view once
    wanted = st.query_params.get("widok")
    if wanted in views:
        st.session_state["sources_view"] = wanted
        del st.query_params["widok"]
    view = st.segmented_control("Widok", list(views), default="Źródła danych", key="sources_view")
    views[view or "Źródła danych"]()


def _diagnostics() -> None:
    data_diagnostics()
    _sources()


def _monitoring() -> None:
    """Data collection engine, job runs, storage, integrity, backup with a restore test."""
    import pandas as pd

    from sports_edge import backup as backups
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
    # TASK 016 §2.4: the size, the growth over 7 days (from a known earlier size) and the
    # free disk space; §3: the cyclic backups of the worker
    size = maintenance.size_lines(maintenance.size_report(db, c.paths(), now=now))
    ui.metric_cards(
        [
            ("Rozmiar bazy", size["size"], f"schemat {disk['schema']} · {size['size_note']}"),
            ("Przyrost z 7 dni", size["growth"], size["growth_note"]),
            ("Wolne miejsce na dysku", size["free"], "dysk z bazą danych"),
            ("Pliki surowe", f"{disk['raw_bytes'] / 1e6:.1f} MB", "data/sources, data/raw"),
            ("Kopie zapasowe", f"{disk['backups_bytes'] / 1e6:.1f} MB", "folder backups/"),
            (
                "Integralność",
                "OK" if disk["integrity"]["ok"] else "BŁĄD",
                f"klucze obce: {disk['integrity']['foreign_key_errors']} błędów",
            ),
        ]
    )
    cyclic = [] if c.demo_mode() else backups.cyclic_backups(c.paths())
    rule = backups.backup_config(c.paths())
    ui.section(
        "Kopie cykliczne",
        f"zadanie workera „Kopia zapasowa bazy” · retencja: {rule['keep_daily']} dziennych + "
        f"{rule['keep_weekly']} tygodniowe",
    )
    if cyclic:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Kopia": b["folder"],
                        "Utworzono": b["created"].strftime("%d.%m.%Y %H:%M"),
                        "Rozmiar": f"{b['bytes'] / 1e6:.1f} MB",
                        "Kontrola integralności": "OK" if b["ok"] else (b["integrity"] or "brak"),
                    }
                    for b in cyclic
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption(
            "Brak kopii cyklicznych — pierwszą wykona worker (domyślnie codziennie o 03:30; "
            "pominięty termin nadrabia po uruchomieniu). Harmonogram: Automatyzacja AI."
        )
    st.caption(
        "Retencja usuwa wyłącznie starsze kopie cykliczne (foldery backups/auto-…). Kopie "
        "sprzed migracji (pre-migration-…), kopie ręczne i rekordy bazy zostają nietknięte. "
        "Przed kopią wymagane jest wolne miejsce: "
        f"{rule['min_free_factor']:g}× rozmiar bazy; liczby w config/app.toml [backup]."
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
    """Basic view: one readable row per provider; licences, limits and coverage on demand."""
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
    rows, details = [_sts_row(db, settings)], []
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
        found = stats.get(family) or {}
        key_text = (
            "bez klucza" if not source["key"] else "klucz: jest" if has_key else "brak klucza"
        )
        rows.append(
            ui.list_row(
                title=source["name"],
                meta=f"{source['scope']} · ostatnia aktualizacja "
                + (c.local_time(found["last"], "%d.%m.%Y %H:%M") if found.get("last") else "—"),
                value=f"{found.get('n', 0):,}".replace(",", " "),
                value_hint="rekordów",
                badges=[
                    ui.pill("aktywne" if active else "nieaktywne", "ok" if active else "neutral"),
                    ui.pill(key_text, "neutral" if has_key else "warn"),
                ],
                tone="ok" if active else "",
            )
        )
        details.append(
            (
                source["name"],
                [
                    ("Status", "; ".join(s.reason for s in status) or "—"),
                    ("Zmienna klucza", source["key"] or "—"),
                    ("Limity i licencja", source["limits"]),
                    ("Ligi z danymi", ", ".join(leagues.get(family, [])) or "—"),
                ],
            )
        )
    rows.append(
        ui.list_row(
            title="OpenScores (MCP)",
            meta="top 5 lig, wyłącznie sezon 2025/26 — brak bieżącego sezonu",
            badges=[ui.pill("sprawdzone — niewdrożone", "neutral")],
        )
    )
    ui.rows(rows)
    with ui.details("Szczegóły źródeł: status, licencje, limity i pokrycie lig"):
        st.markdown(
            '<div class="se-grid two">'
            + "".join(
                ui.card(f"<b>{ui.esc(name)}</b>" + ui.key_values(pairs)) for name, pairs in details
            )
            + "</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"Ligi z modelem: {len(RESEARCH_COMPETITIONS)}. Dostawca jest pokazany jako źródło "
            "ligi tylko wtedy, gdy w bazie są jego wyniki — sam adapter nie oznacza pokrycia. "
            "Źródła, które odmówiły dostępu do bieżącego sezonu, nie są odpytywane ponownie przez "
            "7 dni. " + " · ".join(f"{k}: {v}" for k, v in SOURCE_LABELS.items())
        )


def _sts_row(db, settings) -> str:
    rows = [i for i in imports(db) if i["data_kind"] in c.data_kinds()]
    last = rows[0] if rows else None
    newest = max((i["generated_at"] or "" for i in rows if i["rows_total"]), default="")
    allowed = not settings.offline_mode and settings.sts_download_enabled
    return ui.list_row(
        title="STS — oferta PDF",
        meta="oficjalne pliki na dziś, jutro i kolejne dni · najnowszy dokument "
        + (c.local_time(newest, "%d.%m.%Y %H:%M") if newest else "—")
        + " · maks. raz na 10 minut na plik",
        value=str(last["rows_total"]) if last else "—",
        value_hint="wierszy w ostatnim imporcie",
        badges=[
            ui.pill(
                "pobieranie na żądanie" if allowed else "tylko import ręczny",
                "ok" if allowed else "neutral",
            ),
            ui.pill("bez klucza", "neutral"),
        ],
        tone="ok" if newest else "warn",
    )


def _sts_files() -> None:
    ui.section("Oficjalne pliki STS", "wynik każdego pobrania jest pokazany wprost")
    from sports_edge.app.pages_offers import _import_box

    _import_box()
