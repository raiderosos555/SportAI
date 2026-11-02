"""UCZENIE AI: how the models learn, how good they are and who decides about a new model.

Champion (served) vs challenger (shadow mode) with a plain conclusion, controlled promotion
by the user (AKTYWUJ NOWY MODEL / POZOSTAW POPRZEDNI MODEL), rollback to an older version,
the weekly review with its minimum conditions, prospective quality of settled forecasts,
error analysis, coupon results, the feature ablation and the full learning history.
"""

import pandas as pd
import streamlit as st

from sports_edge import forecast_quality as fq
from sports_edge import insights as ins
from sports_edge import learning, retraining
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.collection import completeness, last_success
from sports_edge.performance import builder_coupons, coupon_performance
from sports_edge.services import (
    ELO_FAMILY,
    GOALS_FAMILY,
    MODEL_SCOPES,
    SCOPE_LABELS,
    ablation_reports,
    challenger_versions,
)

STATUS_TONE = {
    "Oczekuje na nowe dane": "neutral",
    "Dane wystarczające": "info",
    "Trening w toku": "info",
    "Walidacja w toku": "info",
    "Challenger gotowy": "warn",
    "Odrzucony": "neutral",
    "Aktywowany": "ok",
    "Przywrócono starszą wersję": "info",
}
FAMILY_LABEL = {"elo": "Elo (1X2, podwójna szansa)", "goals": "Dixon–Coles (gole i rynki)"}


def learning_page() -> None:
    c.setup_page()
    ui.app_header("Uczenie AI", "Jak modele się uczą, jak są oceniane i kto decyduje o zmianie")
    learning_panel()


def learning_panel() -> None:
    db = c.store()
    now = c.now()
    ui.explanation(
        "Zasady",
        [
            "Nowy model (challenger) nigdy nie zastępuje aktywnego sam: działa w trybie cienia, "
            "a o aktywacji decydujesz Ty.",
            "Wybór modelu i statusy: okno selekcji 2023/24. Holdout końcowy od 2024-07-01 jest "
            "tylko raportowany (był używany do decyzji w TASK 005–006).",
            "Najważniejsza ocena jest prospektywna: prognozy zapisane przed meczem i rozliczone "
            "po wyniku.",
        ],
    )
    for scope in MODEL_SCOPES:
        _scope(db, scope, now)
    _review(db, now)
    _quality(db, now)
    _coupons(db)
    _ablation()
    ui.section("Historia uczenia", "każda decyzja jest zapisana i nie może być zmieniona")
    rows = retraining.history(db)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=320)
    else:
        st.caption("Brak zapisanych zdarzeń modeli.")


def _scope(db, scope: str, now) -> None:
    status = retraining.scope_status(db, scope, now=now)
    ui.section(SCOPE_LABELS[scope].capitalize(), "champion i challenger")
    ui.pills([(status, STATUS_TONE.get(status, "neutral"))])
    compared = retraining.comparison(db, scope)
    cards = []
    for key, entry in compared.items():
        champion, challenger = entry["champion"], entry["challenger"]
        lines = [
            f"Aktywna: {champion.get('version', '—')} (protokół {champion.get('protocol', '—')})",
            f"Challenger: {challenger.get('version', 'brak')}"
            + (f" (protokół {challenger['protocol']})" if challenger else ""),
            entry["conclusion"],
        ]
        cards.append(
            ui.card(
                f"<b>{ui.esc(FAMILY_LABEL[key])}</b>" + ui.bullets(lines),
                tone="warn" if challenger else "",
            )
        )
    ui.card_grid(cards)
    waiting = challenger_versions(db).get(scope, {})
    if waiting:
        sure = st.checkbox(
            "Rozumiem: zmiana modelu wpływa na wszystkie przyszłe prognozy i kupony tego zakresu",
            key=f"learn_sure_{scope}",
        )
        cols = st.columns(2)
        versions = {
            {"elo": ELO_FAMILY, "goals": GOALS_FAMILY}[k]: row["version_id"]
            for k, row in waiting.items()
        }
        if cols[0].button(
            "AKTYWUJ NOWY MODEL",
            key=f"learn_on_{scope}",
            type="primary",
            disabled=not sure,
            icon=":material/rocket_launch:",
        ):
            learning.activate(
                db,
                scope=scope,
                versions=versions,
                now=c.now(),
                reason="Decyzja użytkownika w panelu Uczenie AI",
            )
            st.cache_data.clear()
            st.rerun()
        if cols[1].button(
            "POZOSTAW POPRZEDNI MODEL",
            key=f"learn_keep_{scope}",
            disabled=not sure,
            icon=":material/undo:",
        ):
            learning.keep_previous(
                db,
                scope=scope,
                versions=versions,
                now=c.now(),
                reason="Decyzja użytkownika w panelu Uczenie AI",
            )
            st.rerun()
    older = learning.previous_champions(db, scope, ELO_FAMILY)
    if older:
        with st.expander("Przywróć starszą wersję"):
            choice = st.selectbox(
                "Wersja Elo", older, key=f"learn_old_{scope}", format_func=lambda v: v[:12]
            )
            if st.button("Przywróć wybraną wersję", key=f"learn_back_{scope}"):
                learning.rollback(
                    db,
                    scope=scope,
                    family=ELO_FAMILY,
                    version_id=choice,
                    now=c.now(),
                    reason="Przywrócenie przez użytkownika",
                )
                st.cache_data.clear()
                st.rerun()


def _review(db, now) -> None:
    ui.section("Przegląd modeli", "co tydzień; trening tylko przy spełnionych warunkach")
    last = last_success(db, "retraining_review")
    due = retraining.due(db, now=now)
    ui.pills(
        [
            (f"Ostatni przegląd: {c.local_time(last.isoformat()) if last else 'nigdy'}", "neutral"),
            ("Przegląd zalecany" if due else "Przegląd aktualny", "warn" if due else "ok"),
        ]
    )
    rows = []
    for scope, competitions in MODEL_SCOPES.items():
        ready, reasons = retraining.conditions(db, scope, competitions, now=now)
        rows.append(
            {
                "Zakres": SCOPE_LABELS[scope],
                "Warunki treningu": "spełnione" if ready else "niespełnione",
                "Powód": "; ".join(reasons) or "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if st.button("Uruchom przegląd teraz", icon=":material/model_training:"):
        with st.spinner("Trening w toku — protokół v2 (kilka minut)…"):
            found = retraining.review(db, c.paths(), now=c.now(), trigger="manual")
        st.json(found, expanded=False)
    st.caption(
        "Harmonogram w Windows (opcjonalnie, ustawiasz sam): Harmonogram zadań → Utwórz zadanie "
        "co tydzień z poleceniem: .venv\\Scripts\\sports-edge.exe learning review "
        "(w folderze projektu). Aplikacja nie tworzy zadań systemowych sama."
    )


def _quality(db, now) -> None:
    ui.section("Jakość prognoz", "prospektywnie: zapisane przed meczem, rozliczone po wyniku")
    rows = fq.items(db)
    report = completeness(db, now=now)
    ui.metric_cards(
        [
            ("Prognozy", str(report["predictions"]), f"przyszłe: {report['upcoming']}"),
            (
                "Rozliczone",
                str(sum(report["settlements"].values())),
                f"oczekujące: {report['pending']}",
            ),
            ("Bez wyniku > 48 h", str(report["missing_results_count"]), "do uzupełnienia"),
        ]
    )
    if not rows:
        ui.empty_state("Brak rozliczonych prognoz", "Pojawią się po wynikach meczów z prognozą.")
        return
    pooled = fq.metrics(rows)
    ui.metric_cards(
        [
            ("Rozliczone prognozy", str(pooled["events"]), f"{pooled['n']} selekcji"),
            (
                "Log loss",
                ins.num(pooled["log_loss"], 3),
                f"95%: {ins.num(pooled['log_loss_ci95'][0], 3)}–{ins.num(pooled['log_loss_ci95'][1], 3)}",
            ),
            ("Brier", ins.num(pooled["brier"], 3), "niżej = lepiej"),
            ("ECE", ins.num(pooled["ece"], 3), "błąd kalibracji"),
            (
                "Trafność faworyta",
                ins.pct(pooled["accuracy"]),
                f"95%: {ins.pct(pooled['accuracy_ci95'][0])}–{ins.pct(pooled['accuracy_ci95'][1])}"
                if pooled["accuracy_ci95"]
                else "—",
            ),
        ]
    )
    if pooled["small_sample"]:
        st.warning("Mała próba (mniej niż 30 rozliczonych prognoz) — wnioski są wstępne.")
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Kalibracja wg przedziału szansy**")
        st.dataframe(pd.DataFrame(fq.calibration(rows)), hide_index=True, width="stretch")
    with right:
        st.markdown("**Wg rynku**")
        frame = pd.DataFrame(
            [
                {"rynek": k, **{m: v.get(m) for m in ("events", "log_loss", "brier", "ece")}}
                for k, v in fq.by(rows, "family").items()
            ]
        )
        st.dataframe(frame, hide_index=True, width="stretch")
    with st.expander("Wg ligi, wersji modelu i tygodnia (stabilność)"):
        for title, key in (("Liga", "competition"), ("Wersja modelu", "version")):
            st.markdown(f"**{title}**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            title: k[:12] if key == "version" else k,
                            **{m: v.get(m) for m in ("events", "log_loss", "ece", "small_sample")},
                        }
                        for k, v in fq.by(rows, key).items()
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        st.markdown("**Tydzień**")
        st.dataframe(
            pd.DataFrame(
                [
                    {"tydzień": k, **{m: v.get(m) for m in ("events", "log_loss", "ece")}}
                    for k, v in fq.stability(rows).items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    legs = [
        leg.get("league", "")
        for coupon in builder_coupons(db)
        for leg in coupon["proposal"]["legs"]
    ]
    findings = fq.error_analysis(rows, leg_leagues=legs or None)
    ui.section("Analiza błędów prognoz", "gdzie modele mylą się najczęściej")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Obszar": f["topic"],
                    "Wniosek": f["finding"],
                    "n": f["n"],
                    "Próba": "mała" if f["small_sample"] else "wystarczająca",
                }
                for f in findings
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _coupons(db) -> None:
    ui.section("Wyniki kuponów", "trafność wobec prognozy, ROI, obsunięcia i serie")
    report = coupon_performance(db)
    if not report:
        st.caption("Brak zapisanych kuponów.")
        return
    rows = []
    for r in report.values():
        ci = r["hit_rate_ci95"]
        rows.append(
            {
                "Profil": r["label"],
                "Kupony": r["coupons"],
                "Rozliczone": r["settled"],
                "Nierozliczone": r["pending"],
                "Wygrane": r["wins"],
                "Trafność": ins.pct(r["hit_rate"]),
                "95%": f"{ins.pct(ci[0])}–{ins.pct(ci[1])}" if ci else "—",
                "Prognoza (śr.)": ins.pct(r["avg_predicted"]),
                "ROI": ins.pct(float(r["roi"])) if r["roi"] is not None else "—",
                "Maks. obsunięcie": ins.money(r["max_drawdown"]),
                "Seria porażek": r.get("longest_losing", "—"),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("ROI i trafność są wiarygodne dopiero przy dziesiątkach rozliczonych kuponów.")


def _ablation() -> None:
    reports = ablation_reports(c.store())
    ui.section("Eksperyment ablacyjny", "A Elo · B Dixon–Coles · C DC + cechy · D prosty ML")
    if not reports:
        st.caption("Eksperyment nie był jeszcze uruchomiony (CLI: sports-edge research ablation).")
        return
    for report in reports[-2:]:
        st.markdown(f"**{SCOPE_LABELS.get(report.get('scope'), report.get('scope'))}**")
        rows = []
        for window, models in report["windows"].items():
            for model, m in models.items():
                rows.append({"okno": window, "model": model, **m})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(report.get("conclusion", ""))
