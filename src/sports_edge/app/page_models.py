"""Modele i statystyki: plain-language view, offer coverage and the advanced research view."""

import json
from datetime import timedelta

import pandas as pd
import streamlit as st

from sports_edge import insights as ins
from sports_edge import learning
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.app.page_learning import learning_panel
from sports_edge.app.pages_models import _data, _forecasts, _models, _results
from sports_edge.datasources.competitions import COMPETITIONS
from sports_edge.models.registry import model_overview
from sports_edge.services import (
    ELO_FAMILY,
    MODEL_SCOPES,
    SCOPE_LABELS,
    active_versions,
    prediction_preference,
)

FAMILIES = {
    "1x2": "Wynik meczu (1X2)",
    "double_chance": "Podwójna szansa",
    "total_1.5": "Liczba goli — linia 1,5",
    "total_2.5": "Liczba goli — linia 2,5",
    "total_3.5": "Liczba goli — linia 3,5",
    "btts": "Obie drużyny strzelą",
}


def models_page() -> None:
    c.setup_page()
    ui.app_header(
        "Modele i statystyki", "Jakie modele działają, dla kogo i jak bardzo można im ufać"
    )
    # a view switch instead of tabs: only the chosen view is computed (tabs run all bodies)
    views = {
        "Podstawowy": _basic,
        "Uczenie AI": learning_panel,
        "Pokrycie oferty": _coverage,
        "Zaawansowany": _advanced,
    }
    view = st.segmented_control("Widok", list(views), default="Podstawowy", key="models_view")
    views[view or "Podstawowy"]()


# ---------------------------------------------------------------- basic


def _basic() -> None:
    db = c.store()
    served = active_versions(db)
    preference = prediction_preference(db)
    if not any(pair["elo"] or pair["goals"] for pair in served.values()):
        ui.empty_state("Brak modeli", "Uruchom analizę albo eksperyment w trybie zaawansowanym.")
        return
    for name, scope in MODEL_SCOPES.items():
        pair = served[name]
        if not (pair["elo"] or pair["goals"]):
            continue
        report = json.loads(pair["goals"]["validation_json"]) if pair["goals"] else {}
        leagues = ", ".join(COMPETITIONS[code].name for code in scope if code in COMPETITIONS)
        one_x_two = preference.get(("1x2", scope[0]), ELO_FAMILY)
        ui.section(SCOPE_LABELS[name].capitalize(), leagues)
        suspended = report.get("protocol") != "v2"
        if suspended:
            ui.explanation(
                "Statusy zawieszone (protokół v1)",
                [learning.HOLDOUT_NOTE, "Nowe wersje (protokół v2) czekają w panelu Uczenie AI."],
                tone="warn",
            )
        cards = []
        for family, label in FAMILIES.items():
            info, model = _family_info(report, family, one_x_two)
            if info is None:
                continue
            validated = info.get("status") == "validated_predictive" and not suspended
            ranges = _ranges(report, family, model)
            text = [
                "Model: " + ("Elo (siła drużyn)" if model == "elo" else "Dixon–Coles (gole)"),
                (
                    "Trafność i kalibracja potwierdzone na nowych meczach"
                    if validated
                    else "Nie przeszedł walidacji — prognozy tylko eksperymentalne"
                ),
            ]
            if info.get("note"):
                text.append("Przewaga nad prostą częstością jest minimalna")
            text += ranges
            cards.append(
                ui.card(
                    f"<b>{ui.esc(label)}</b><br>"
                    + ui.pill(
                        "Można ufać (zwalidowany)" if validated else "Eksperymentalny",
                        "ok" if validated else "warn",
                    )
                    + ui.bullets(text),
                    tone="accent" if validated else "warn",
                )
            )
        ui.card_grid(cards)
    ui.section("Dyscypliny")
    ui.explanation(
        "Zakres modeli",
        [
            "Piłka nożna: 12 lig (5 czołowych + Championship, Eredivisie, Liga Portugal, Pro "
            "League, 2. Bundesliga, Süper Lig, Ekstraklasa).",
            "Tenis, koszykówka, siatkówka, hokej: brak modelu — brak legalnego, bezpłatnego "
            "źródła bieżących wyników (szczegóły w raporcie TASK 006).",
            "Wysoka trafność nie oznacza zysku: kursy STS uwzględniają marżę i podatek.",
        ],
    )


def _family_info(report: dict, family: str, one_x_two: str):
    elo_first = family in ("1x2", "double_chance") and one_x_two == ELO_FAMILY
    if elo_first:
        info = (report.get("elo_predictive") or {}).get(family)
        if info:
            return info, "elo"
    info = (report.get("predictive") or {}).get(family)
    return (info, "dc") if info else (None, None)


def _ranges(report: dict, family: str, model: str) -> list[str]:
    source = report.get("elo_families" if model == "elo" else "families") or {}
    rows = (source.get(family) or {}).get("ranges") or []
    out = []
    for row in rows:
        if row.get("n", 0) >= 100 and row.get("freq") is not None:
            out.append(
                f"Typy z szansą {row['range']}: trafiło {ins.pct(row['freq'])} (n = {row['n']})"
            )
    return out[-2:]


# ---------------------------------------------------------------- coverage


def _coverage() -> None:
    db = c.store()
    today = c.today_local()
    horizon = (
        st.segmented_control("Okres", ["Dziś", "3 dni", "7 dni"], default="7 dni", key="cov_range")
        or "7 dni"
    )
    span = {"Dziś": 1, "3 dni": 3, "7 dni": 7}[horizon]
    days = tuple((today + timedelta(days=i)).isoformat() for i in range(span))
    report = c.cached_coverage(db, c.data_version(db), days, c.minute())
    totals = report["totals"]
    ui.metric_cards(
        [
            ("Wydarzenia w ofercie", str(report["funnel"][0]["events"]), "wszystkie dyscypliny"),
            (
                "Z prognozą modelu",
                str(totals["events_with_forecast"]),
                "mecze (także rozpoczęte), nie typy",
            ),
            ("Rynki w ofercie", str(totals["markets"]), f"selekcje: {totals['selections']}"),
            (
                "Selekcje z oszacowaniem",
                str(totals["with_model"]),
                f"ostrożnym: {totals['with_conservative']}",
            ),
            (
                "Selekcje dla kreatora",
                str(totals["eligible"]),
                f"blokowane tylko kursem: {totals['stale_only']}",
            ),
        ]
    )
    ui.section("Lejek pokrycia", "każde wydarzenie odpada na pierwszym niespełnionym warunku")
    top = report["funnel"][0]["events"] or 1
    st.markdown(
        ui.card(
            ui.probability_bars(
                [(f["label"], f["events"] / top, str(f["events"])) for f in report["funnel"]]
            )
        ),
        unsafe_allow_html=True,
    )
    left, right = st.columns(2, gap="medium")
    with left:
        ui.section("Powody braku prognozy")
        st.dataframe(
            pd.DataFrame(
                [{"Powód": r["reason"], "Wydarzenia": r["events"]} for r in report["by_reason"]]
            ),
            hide_index=True,
            width="stretch",
        )
        ui.section("Według dyscypliny")
        st.dataframe(
            _group_frame(report["by_sport"], "Dyscyplina"), hide_index=True, width="stretch"
        )
    with right:
        ui.section("Według daty")
        st.dataframe(_group_frame(report["by_date"], "Data"), hide_index=True, width="stretch")
        ui.section("Według modelu i źródła wyników")
        st.dataframe(_group_frame(report["by_model"], "Model"), hide_index=True, width="stretch")
        st.dataframe(
            _group_frame(report["by_source"], "Źródło wyników"), hide_index=True, width="stretch"
        )
    ui.section("Według ligi")
    leagues = report["by_league"]
    only = st.toggle("Tylko ligi z prognozami", value=False, key="cov_only")
    if only:
        leagues = [r for r in leagues if r["with_forecast"]]
    if not leagues:
        st.caption("Brak lig w wybranym okresie.")
        return
    st.dataframe(_group_frame(leagues[:150], "Liga"), hide_index=True, width="stretch", height=420)


def _group_frame(rows: list[dict], label: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                label: r.get("league", r["group"]) if label == "Liga" else r["group"],
                **({"Dyscyplina": r.get("sport", "")} if label == "Liga" else {}),
                "Wydarzenia": r["events"],
                "Z prognozą": r["with_forecast"],
                "Selekcje z modelem": r["with_model"],
                "Dla kreatora": r["eligible"],
                "Tylko kurs blokuje": r["stale_only"],
                "Główny powód braku": r["main_reason"],
            }
            for r in rows
        ]
    )


# ---------------------------------------------------------------- advanced


def _advanced() -> None:
    db = c.store()
    ui.section(
        "Bramki jakości według lig", "holdout od 2024-07-01; warunek dodatkowy do walidacji łącznej"
    )
    rows = []
    for name, pair in active_versions(db).items():
        if not pair["goals"]:
            continue
        report = json.loads(pair["goals"]["validation_json"])
        for code, entry in (report.get("by_league") or {}).items():
            for model, families in entry.items():
                for family, gate in families.items():
                    rows.append(
                        {
                            "Zakres": SCOPE_LABELS[name],
                            "Liga": COMPETITIONS[code].name if code in COMPETITIONS else code,
                            "Model": "Elo" if model == "elo" else "Dixon–Coles",
                            "Rynek": FAMILIES.get(family, family),
                            "n": gate["n"],
                            "Log loss": gate["log_loss"],
                            "Bazowy": gate["baseline_log_loss"],
                            "ECE": gate["ece"],
                            "Wynik": "OK" if gate["ok"] else "; ".join(gate["reasons"]),
                        }
                    )
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=360)
    else:
        st.caption("Bramki per liga są liczone w eksperymentach od TASK 006.")
    ui.section("Wersje modeli", "aktywne wersje i pełna historia eksperymentów")
    active = {
        row["version_id"]: name
        for name, pair in active_versions(db).items()
        for row in pair.values()
        if row
    }
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Utworzono": c.local_time(v["created_at"]),
                    "Rodzina": v["family"],
                    "Wersja": v["version"],
                    "Status ekonomiczny": v["status"],
                    "Aktywna": SCOPE_LABELS.get(active.get(v["version_id"], ""), "—"),
                }
                for v in model_overview(db)
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    parts = {
        "Modele i walidacja": _models,
        "Wyniki sportowe": _results,
        "Dane historyczne": _data,
        "Prognozy i nazwy": _forecasts,
    }
    part = st.segmented_control(
        "Sekcja badawcza", list(parts), default="Modele i walidacja", key="models_research"
    )
    parts[part or "Modele i walidacja"]()
