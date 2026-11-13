"""Modele i statystyki: plain-language view, offer coverage and the advanced research view."""

import json
from datetime import timedelta

import pandas as pd
import streamlit as st

from sports_edge import insights as ins
from sports_edge import learning
from sports_edge import prospective_eval as pe
from sports_edge.app import common as c
from sports_edge.app import international_ui as iui
from sports_edge.app import ui
from sports_edge.app.page_decisions import VIEW as DECISIONS_VIEW
from sports_edge.app.page_decisions import decisions_panel
from sports_edge.app.page_learning import gate_text, learning_panel
from sports_edge.app.pages_models import _data, _forecasts, _models, _results
from sports_edge.datasources.competitions import COMPETITIONS
from sports_edge.models.registry import model_overview
from sports_edge.services import (
    ELO_FAMILY,
    GOALS_FAMILY,
    MODEL_SCOPES,
    SCOPE_LABELS,
    active_versions,
    challenger_versions,
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
        "Modele i statystyki", "Jakie modele działają, na jakich rynkach i z jaką jakością"
    )
    # a view switch instead of tabs: only the chosen view is computed (tabs run all bodies)
    views = {
        "Podstawowy": _basic,
        DECISIONS_VIEW: decisions_panel,  # TASK 015 §3: the decisions waiting for the user
        "Uczenie AI": learning_panel,
        "Pokrycie oferty": _coverage,
        "Puchary i reprezentacje": lambda: iui.models_block(c.store()),
        "Zaawansowany": _advanced,
    }
    # TASK 015: the Dashboard links to ?widok=Decyzje do podjęcia (opens the view once)
    wanted = st.query_params.get("widok")
    if wanted in views:
        st.session_state["models_view"] = wanted
        del st.query_params["widok"]
    view = st.segmented_control("Widok", list(views), default="Podstawowy", key="models_view")
    views[view or "Podstawowy"]()


# ---------------------------------------------------------------- basic


def _basic() -> None:
    db = c.store()
    served = active_versions(db)
    preference = prediction_preference(db)
    gate_label, gate_tone = gate_text(db)  # TASK 013 §6: the state of the v3 gate
    ui.pills([(gate_label, gate_tone)])
    if not any(pair["elo"] or pair["goals"] for pair in served.values()):
        ui.empty_state("Brak modeli", "Uruchom analizę albo eksperyment w trybie zaawansowanym.")
        return
    waiting = challenger_versions(db)
    benchmark = {(r["scope"], r["family"], r["market"]): r for r in c.evaluation()["market"]}
    st.caption(
        "„Zwalidowany” oznacza potwierdzoną trafność i kalibrację na nowych meczach (okno "
        "selekcji protokołu v2). To nie jest gwarancja trafienia ani zysku."
    )
    st.caption(
        "Decyzję o bramce v3 zapisujesz w widoku „Decyzje do podjęcia” albo „Uczenie AI” "
        "(sekcja Bramka protokołu v3)."
    )
    for name, scope in MODEL_SCOPES.items():
        pair = served[name]
        if not (pair["elo"] or pair["goals"]):
            continue
        report = json.loads(pair["goals"]["validation_json"]) if pair["goals"] else {}
        leagues = ", ".join(COMPETITIONS[code].name for code in scope if code in COMPETITIONS)
        one_x_two = preference.get(("1x2", scope[0]), ELO_FAMILY)
        suspended = report.get("protocol") not in ("v2", "v3")
        ui.section(SCOPE_LABELS[name].capitalize(), leagues)
        if suspended:
            ui.explanation(
                "Statusy zawieszone (protokół v1)",
                [learning.HOLDOUT_NOTE, "Nowe wersje (protokół v2) czekają w panelu Uczenie AI."],
                tone="warn",
            )
        rows, chart, details = [], [], []
        for family, label in FAMILIES.items():
            info, model = _family_info(report, family, one_x_two)
            if info is None:
                continue
            metrics = (report.get("elo_families" if model == "elo" else "families") or {}).get(
                family
            ) or {}
            cal = metrics.get("calibrated") or {}
            validated = info.get("status") == "validated_predictive" and not suspended
            skill = info.get("skill_vs_baseline")
            challenger = (waiting.get(name) or {}).get("elo" if model == "elo" else "goals")
            versus = benchmark.get(
                (
                    name,
                    ELO_FAMILY if model == "elo" else GOALS_FAMILY,
                    family.replace("total_", "total "),
                )
            )
            rows.append(
                [
                    label,
                    "Elo" if model == "elo" else "Dixon–Coles",
                    "zwalidowany" if validated else "eksperymentalny",
                    str(cal.get("n", "—")),
                    f"{skill * 100:+.1f}%".replace(".", ",") if skill is not None else None,
                    ins.num(cal.get("ece"), 3) if cal.get("ece") is not None else None,
                    "czeka na decyzję" if challenger else "—",
                    versus["state_label"] if versus else pe.state_label("unknown"),
                ]
            )
            if skill is not None:
                chart.append(
                    {
                        "Rynek": label,
                        "Przewaga nad częstością (%)": round(skill * 100, 2),
                        "Status": "zwalidowany" if validated else "eksperymentalny",
                    }
                )
            details.append((label, model, cal, metrics, info))
        st.markdown(
            ui.table(
                [
                    "Rynek",
                    "Aktywny model",
                    "Status walidacji",
                    "Obserwacje",
                    "Przewaga nad częstością",
                    "Kalibracja (ECE)",
                    "Challenger",
                    "Porównanie z rynkiem STS",
                ],
                rows,
                numeric=(3, 4, 5),
            ),
            unsafe_allow_html=True,
        )
        if chart:
            _skill_chart(chart)
        st.caption(
            "Porównanie z rynkiem STS: prospektywnie, na tych samych rozliczonych meczach, z "
            "kursem sprzed meczu bez marży (szczegóły: widok Uczenie AI)."
        )
        with ui.details(f"Szczegółowe miary i kalibracja — {SCOPE_LABELS[name]}"):
            st.caption(  # TASK 019 §4.5: the technical field only in the details
                "Raporty walidacji historycznej nie mają porównania z rynkiem (brak legalnych "
                "historycznych kursów) — pole „beats_market: false” to wartość domyślna, nie wynik."
            )
            _details(details)
    ui.section("Dyscypliny")
    ui.explanation(
        "Zakres modeli",
        [
            "Piłka nożna: 12 lig (5 czołowych + Championship, Eredivisie, Liga Portugal, Pro "
            "League, 2. Bundesliga, Süper Lig, Ekstraklasa).",
            "Tenis, koszykówka, siatkówka, hokej: brak modelu — brak legalnego, bezpłatnego "
            "źródła bieżących wyników.",
            "Wysoka trafność nie oznacza zysku: kursy STS uwzględniają marżę i podatek.",
        ],
    )


def _skill_chart(rows: list[dict]) -> None:
    """Improvement of the log loss over the base rate, one bar per market (one axis)."""
    import altair as alt

    frame = pd.DataFrame(rows)
    chart = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=4, height=18)
        .encode(
            x=alt.X("Przewaga nad częstością (%):Q", title="Przewaga log loss nad częstością (%)"),
            y=alt.Y(
                "Rynek:N",
                sort="-x",
                title=None,
                axis=alt.Axis(labelLimit=320, labelOverlap=False, labelFontSize=12),
            ),
            color=alt.Color(
                "Status:N",
                scale=alt.Scale(
                    domain=["zwalidowany", "eksperymentalny"], range=["#22C55E", "#F59E0B"]
                ),
                legend=alt.Legend(orient="bottom", title=None),
            ),
            tooltip=["Rynek", "Przewaga nad częstością (%)", "Status"],
        )
        .properties(height=44 * len(frame) + 50)
    )
    st.altair_chart(chart, width="stretch")


def _details(details: list) -> None:
    body = []
    for label, model, cal, metrics, _info in details:
        body.append(
            [
                label,
                "Elo" if model == "elo" else "Dixon–Coles",
                ins.num(cal.get("log_loss"), 4) if cal.get("log_loss") is not None else None,
                ins.num(metrics.get("baseline_log_loss"), 4)
                if metrics.get("baseline_log_loss") is not None
                else None,
                ins.num(cal.get("brier"), 4) if cal.get("brier") is not None else None,
                ins.num(cal.get("ece"), 4) if cal.get("ece") is not None else None,
                str(cal.get("n", "—")),
            ]
        )
    st.markdown(
        ui.table(
            ["Rynek", "Model", "Log loss", "Log loss bazowy", "Brier", "ECE", "n"],
            body,
            numeric=(2, 3, 4, 5, 6),
        ),
        unsafe_allow_html=True,
    )
    for label, model, _cal, _metrics, info in details:
        lines = _ranges_for(label, model, _metrics)
        reasons = info.get("reasons") or []
        if lines or reasons:
            st.markdown(f"**{label}**")
            for line in lines:
                st.caption(line)
            for reason in reasons[:3]:
                st.caption(f"Powód statusu: {reason}")


def _ranges_for(label: str, model: str, metrics: dict) -> list[str]:
    out = []
    for row in metrics.get("ranges") or []:
        if row.get("n", 0) >= 100 and row.get("freq") is not None:
            out.append(
                f"Typy z szansą {row['range']}: trafiło {ins.pct(row['freq'])} (n = {row['n']})"
            )
    return out


def _family_info(report: dict, family: str, one_x_two: str):
    elo_first = family in ("1x2", "double_chance") and one_x_two == ELO_FAMILY
    if elo_first:
        info = (report.get("elo_predictive") or {}).get(family)
        if info:
            return info, "elo"
    info = (report.get("predictive") or {}).get(family)
    return (info, "dc") if info else (None, None)


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
        st.caption("Brak eksperymentów z bramkami per liga.")
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
