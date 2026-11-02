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
from sports_edge.app import advanced_ui, ui
from sports_edge.app import common as c
from sports_edge.collection import completeness
from sports_edge.performance import builder_coupons, coupon_performance
from sports_edge.services import (
    ELO_FAMILY,
    GOALS_FAMILY,
    MODEL_SCOPES,
    ROLE_LABELS,
    SCOPE_LABELS,
    ablation_reports,
    challenger_versions,
    model_versions_of,
    switch_models,
    train_now,
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
    advanced_ui.engine_panel()
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
            _switch(scope, versions, "Decyzja użytkownika w panelu Uczenie AI (aktywacja)")
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
    _versions(db, scope)
    _train(scope)


def _switch(scope: str, versions: dict[str, str], reason: str) -> None:
    with st.spinner("Zmiana modelu i prognozy nadchodzących meczów…"):
        switch_models(
            c.store(), c.paths(), scope=scope, versions=versions, now=c.now(), reason=reason
        )
    st.cache_data.clear()
    st.rerun()


def _versions(db, scope: str) -> None:
    """Every version of the scope; any of them can be served again (also a rejected one)."""
    rows = model_versions_of(db, scope)
    with st.expander("Wszystkie wersje modeli i przywracanie"):
        if not rows:
            st.caption("Brak wersji w tym zakresie.")
            return
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Model": "Elo" if r["family"] == ELO_FAMILY else "Dixon–Coles",
                        "Wersja": r["version"],
                        "Utworzona": c.local_time(r["created_at"], "%d.%m.%Y %H:%M"),
                        "Protokół": r["protocol"],
                        "Rola": ROLE_LABELS[r["role"]],
                        "Log loss (okno selekcji)": r["log_loss"],
                        "Zwalidowane rynki": ", ".join(r["validated"]) or "—",
                    }
                    for r in rows
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "Wersje protokołu v1 mają statusy zawieszone (holdout był użyty do decyzji) — "
            "ich typy są zawsze eksperymentalne."
        )
        chosen = {}
        cols = st.columns(2)
        for col, family, label in (
            (cols[0], ELO_FAMILY, "Elo (1X2, podwójna szansa)"),
            (cols[1], GOALS_FAMILY, "Dixon–Coles (gole i rynki)"),
        ):
            options = [r for r in rows if r["family"] == family]
            if not options:
                continue
            active = next((i for i, r in enumerate(options) if r["role"] == "active"), 0)
            pick = col.selectbox(
                label,
                options,
                index=active,
                key=f"learn_pick_{scope}_{family}",
                format_func=lambda r: (
                    f"{r['version']} · {r['protocol']} · {ROLE_LABELS[r['role']]}"
                ),
            )
            if pick["role"] != "active":
                chosen[family] = pick["version_id"]
        sure = st.checkbox(
            "Rozumiem: wybrane wersje zastąpią aktywne modele tego zakresu",
            key=f"learn_pick_sure_{scope}",
        )
        if st.button(
            "Ustaw wybrane jako aktywne",
            key=f"learn_pick_go_{scope}",
            icon=":material/restore:",
            disabled=not (sure and chosen),
        ):
            _switch(scope, chosen, "Wybór wersji przez użytkownika w panelu Uczenie AI")


def _train(scope: str) -> None:
    """Training on demand: new versions become challengers; activation stays with the user."""
    if st.button(
        f"Trenuj teraz — {SCOPE_LABELS[scope]}",
        key=f"learn_train_{scope}",
        icon=":material/model_training:",
        help="Protokół v2 na aktualnych danych (1–2 min). Nowe wersje trafiają do trybu cienia "
        "jako challengery — aktywujesz je sam po porównaniu.",
    ):
        with st.status("Trening w toku…", expanded=True) as box:
            try:
                train_now(c.store(), c.paths(), now=c.now(), scope=scope, progress=box.write)
            except ValueError as error:
                box.update(label="Trening nieudany", state="error")
                st.error(str(error))
                return
            box.update(label="Walidacja zakończona — challengery gotowe", state="complete")
        st.cache_data.clear()
        st.rerun()


@st.cache_data(ttl=600, show_spinner=False, max_entries=8)
def _conditions(_db, version: tuple, at) -> dict:
    """Training conditions of every scope (unique matches since training) — cached."""
    return {
        scope: retraining.conditions(_db, scope, competitions, now=at)
        for scope, competitions in MODEL_SCOPES.items()
    }


def _review(db, now) -> None:
    ui.section(
        "Przegląd modeli",
        "w panelu Automatyzacja AI według harmonogramu; na żądanie — Trenuj teraz wyżej",
    )
    due = retraining.due(db, now=now)
    ui.pills([("Przegląd zalecany" if due else "Przegląd aktualny", "warn" if due else "ok")])
    found = _conditions(db, c.data_version(db), c.minute())
    rows = []
    for scope in MODEL_SCOPES:
        ready, reasons = found[scope]
        moments = retraining.timeline(db, scope)
        rows.append(
            {
                "Zakres": SCOPE_LABELS[scope],
                "Ostatni przegląd": c.local_time(moments["review"], "%d.%m %H:%M"),
                "Ostatni trening": c.local_time(moments["training"], "%d.%m %H:%M"),
                "Ostatnia walidacja": c.local_time(moments["validation"], "%d.%m %H:%M"),
                "Ostatnia aktywacja": c.local_time(moments["activation"], "%d.%m %H:%M"),
                "Warunki treningu": "spełnione" if ready else "niespełnione",
                "Powód": "; ".join(reasons) or "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "Przegląd zakończony decyzją „za mało danych” jest zapisany jako wykonany — kolejny "
        "nastąpi po upływie okresu przeglądu. Walidacja w protokole v2 jest częścią treningu."
    )
    if st.button("Uruchom przegląd teraz", icon=":material/fact_check:"):
        with st.spinner("Przegląd modeli…"):
            result = retraining.review(db, c.paths(), now=c.now(), trigger="manual")
        st.cache_data.clear()
        st.json(result, expanded=False)


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
    markets = fq.by_market(rows)
    table = []
    for group, m in markets.items():
        low, high = m["log_loss_ci95"]
        table.append(
            {
                "Rynek": group,
                "Mecze (niezależne)": m["matches"],
                "Prognozy": m["events"],
                "Log loss": m["log_loss"],
                "95% log loss": f"{ins.num(low, 3)}–{ins.num(high, 3)}",
                "Brier": m["brier"],
                "ECE": m["ece"],
                "Trafność": ins.pct(m["accuracy"])
                if group != "Remisy"
                else f"prognoza {ins.pct(m['predicted'])} · wynik {ins.pct(m['observed'])}",
                "Co znaczy trafność": m["accuracy_basis"],
                "Próba": "mała" if m["small_sample"] else "wystarczająca",
            }
        )
    st.markdown("**Każdy rynek osobno** — bez mieszania łatwych i trudnych rynków")
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    if any(m["small_sample"] for m in markets.values()):
        st.warning(
            "Mała próba: mniej niż 30 niezależnych meczów w części rynków — wnioski są wstępne. "
            "Kilka rynków jednego meczu to nie są niezależne dowody."
        )
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Kalibracja wg przedziału szansy (wszystkie rynki)**")
        st.dataframe(pd.DataFrame(fq.calibration(rows)), hide_index=True, width="stretch")
    with right:
        st.markdown("**Kupony: SOLO, AKO i profile**")
        split = fq.coupon_split(builder_coupons(db))
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Grupa": r["grupa"],
                        "Kupony": r["kupony"],
                        "Rozliczone": r["rozliczone"],
                        "Trafność": ins.pct(r["trafność"]),
                        "95%": f"{ins.pct(r['trafność_ci95'][0])}–{ins.pct(r['trafność_ci95'][1])}"
                        if r["trafność_ci95"]
                        else "—",
                        "Prognoza (śr.)": ins.pct(r["prognoza_średnio"]),
                        "ROI": ins.pct(r["roi"]),
                    }
                    for r in split
                ]
            ),
            hide_index=True,
            width="stretch",
        )
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
