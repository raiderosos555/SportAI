"""Advanced Football Prediction Engine in the interface: the experiment panel of Uczenie AI
and the three parts of "Dlaczego model tak ocenia ten mecz?" on the match page.

The match page never mixes the forecasts: what the ACTIVE model used, what it does not use
yet, and — separately, with its own version and status — the experimental challenger."""

import pandas as pd
import streamlit as st

from sports_edge import advanced, advanced_lab, prematch
from sports_edge import insights as ins
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.services import MODEL_SCOPES, SCOPE_LABELS

MAIN = tuple(v.key for v in advanced.VARIANTS)
WINDOW = {"selection": "selekcja 2023/24 (wybór)", "holdout": "holdout (tylko raport)"}


def _pct(value) -> str:
    return ins.pct(value) if value is not None else "—"


def _ci(ci) -> str:
    return f"[{ci[0]:+.4f}; {ci[1]:+.4f}]" if ci else "—"


# ---------------------------------------------------------------- Uczenie AI


def engine_panel() -> None:
    db = c.store()
    ui.section(
        "Model rozszerzony — wpływ dodatkowych informacji",
        "Advanced Football Prediction Engine: czy forma, siła rywali, dom/wyjazd i odpoczynek "
        "poprawiają prognozy?",
    )
    ui.explanation(
        "Jak to działa",
        [
            "Cechy liczone wyłącznie z meczów rozegranych przed dniem meczu; brak danych nie jest "
            "zastępowany zerem — mecz bez kompletu cech nie wchodzi do porównania.",
            "Wszystkie warianty oceniane na tych samych meczach; wybór w oknie selekcji, holdout "
            "tylko raportowany.",
            "Challenger nie zastępuje Elo ani Dixona–Colesa. Do oceny prospektywnej (tryb "
            "cienia) trafia tylko po spełnieniu kryteriów i Twojej decyzji.",
        ],
    )
    for scope in MODEL_SCOPES:
        _scope(db, scope)


def _scope(db, scope: str) -> None:
    found = advanced_lab.reports(db, scope)
    report = found[-1] if found else None
    st.markdown(f"#### {SCOPE_LABELS[scope].capitalize()}")
    if st.button(
        f"Uruchom eksperyment cech — {SCOPE_LABELS[scope]}",
        key=f"advanced_run_{scope}",
        icon=":material/science:",
        disabled=c.demo_mode(),
        help="Liczy cechy as-of, dopasowuje warianty A–E i ablację (może potrwać kilka minut). "
        "Nie zmienia aktywnych modeli.",
    ):
        with st.status("Eksperyment cech…", expanded=True) as status:
            try:
                report = advanced_lab.run(db, c.paths(), now=c.now(), scope=scope)
                status.update(label="Eksperyment zakończony", state="complete")
            except ValueError as error:
                status.update(label=f"Eksperyment niemożliwy: {error}", state="error")
    if report is None:
        st.caption("Eksperyment nie był jeszcze uruchomiony dla tego zakresu.")
        _challengers(db, scope)
        return
    sample = report["sample"]["complete"]
    ui.metric_cards(
        [
            (
                "Ostatni eksperyment",
                c.local_time(report["ran_at"], "%d.%m %H:%M"),
                f"dane do {report['data_through']}",
            ),
            ("Mecze do dopasowania", str(report["sample"]["fit"]), "okna walidacji i kalibracji"),
            ("Okno selekcji", str(sample.get("selection", 0)), "mecze z kompletem cech"),
            ("Holdout", str(sample.get("holdout", 0)), "tylko raportowany"),
            (
                "Kontrola wycieku",
                "OK" if report["leakage"]["ok"] else "BŁĄD",
                f"{report['leakage']['checked']} meczów przeliczonych ponownie",
            ),
        ]
    )
    ui.explanation(
        "Wniosek",
        [report["conclusion"]],
        tone="accent" if not report.get("challengers") else "warn",
    )
    tabs = st.tabs(["Rynki i decyzja", "Ablacja cech", "Cechy i dane", "Pogorszenia"])
    with tabs[0]:
        _markets(report)
    with tabs[1]:
        _ablation(report)
    with tabs[2]:
        _features(report)
    with tabs[3]:
        worse = report.get("worse") or []
        if worse:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Rynek": advanced.MARKET_LABELS[w["market"]],
                            "Wariant": w["label"],
                            "Δ log loss": w["delta_log_loss"],
                        }
                        for w in worse
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        st.caption("Eksperymenty, które pogorszyły wynik, są zapisywane tak samo jak udane.")
    _challengers(db, scope)


def _variant_rows(report: dict, market: str, keys) -> list[dict]:
    block = report["markets"][market]
    selection = block["windows"].get("selection", {})
    holdout = block["windows"].get("holdout", {})
    out = []
    for key in keys:
        row = selection.get(key)
        if row is None:
            continue
        hold = holdout.get(key) or {}
        out.append(
            {
                "Wariant": report["variants"][key]["label"],
                "Log loss": row["log_loss"],
                "Brier": row["brier"],
                "ECE": row["ece"],
                "Δ log loss": row.get("delta_log_loss"),
                "95% CI Δ": _ci(row.get("ci95")),
                "Δ holdout": hold.get("delta_log_loss"),
                "Mecze": row["n"],
            }
        )
    return out


def _markets(report: dict) -> None:
    st.caption(
        "Δ = wariant − aktywny model (ujemna wartość = lepiej). Okno selekcji decyduje; "
        "holdout jest tylko raportowany."
    )
    for market in advanced.MARKETS:
        block = report["markets"][market]
        decision = report["decision"][market]
        base = report["variants"][block["baseline"]]["label"]
        st.markdown(f"**{advanced.MARKET_LABELS[market]}** · punkt odniesienia: {base}")
        st.dataframe(
            pd.DataFrame(_variant_rows(report, market, MAIN)),
            hide_index=True,
            width="stretch",
        )
        level = "ok" if decision.get("passed") else "neutral"
        st.markdown(
            ui.pill(decision.get("verdict", "—"), level)
            + f" najlepszy wariant: {ui.esc(decision.get('label') or '—')}",
            unsafe_allow_html=True,
        )
        with st.expander("Kryteria, ligi, okresy i kalibracja"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Kryterium": item["name"],
                            "Spełnione": "tak"
                            if item["ok"]
                            else "—"
                            if item["ok"] is None
                            else "nie",
                            "Szczegóły": item["detail"],
                        }
                        for item in decision.get("criteria", [])
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            candidate = decision.get("candidate")
            for title, key in (("Ligi (okno selekcji)", "leagues"), ("Półrocza", "periods")):
                segments = (block.get(key) or {}).get(candidate) or {}
                if segments:
                    st.markdown(f"_{title}_")
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "Segment": name,
                                    "Mecze": seg["n"],
                                    "Log loss": seg["log_loss"],
                                    "Δ log loss": seg.get("delta_log_loss"),
                                    "95% CI Δ": _ci(seg.get("ci95")),
                                }
                                for name, seg in segments.items()
                            ]
                        ),
                        hide_index=True,
                        width="stretch",
                    )
            curve = decision.get("reliability") or {}
            if curve.get("candidate"):
                st.markdown("_Kalibracja (prognoza vs częstość, okno selekcji)_")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Przedział": row["range"],
                                "Wariant: prognoza": row["mean_p"],
                                "Wariant: częstość": row["freq"],
                                "Prognozy": row["n"],
                            }
                            for row in curve["candidate"]
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )


def _ablation(report: dict) -> None:
    keys = [v.key for v in advanced.ABLATION]
    for market in ("1x2", "total_2.5", "btts"):
        rows = _variant_rows(report, market, keys)
        if rows:
            st.markdown(f"**{advanced.MARKET_LABELS[market]}**")
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for skipped in report.get("skipped") or []:
        st.info(f"{skipped['label']}: nie wykonano — {skipped['reason']}.")
    redundancy = report.get("redundancy") or []
    if redundancy:
        st.markdown("**Powielanie informacji Elo** — korelacja cechy (gospodarz − gość) z Elo")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Cecha": r["label"], "Korelacja z Elo": r["correlation_with_elo"]}
                    for r in redundancy
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "Wysoka korelacja oznacza, że cecha w dużej mierze powtarza to, co Elo już wie — "
            "wtedy jej dodanie rzadko poprawia prognozę."
        )


def _features(report: dict) -> None:
    rows = []
    for f in report["features"]:
        sel = f["windows"].get("selection", {})
        rows.append(
            {
                "Cecha": f["label"],
                "Grupa": f["group"],
                "Źródło": f["source"],
                "Obserwacja": f["observed"],
                "Dostępność (as-of)": f["available"],
                "Wymagana historia": f"{f['history']} mecz.",
                "Dostępne / braki (selekcja)": f"{sel.get('available', 0)} / {sel.get('missing', 0)}",
                "Wersja definicji": f["version"],
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for skipped in report.get("skipped") or []:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Cecha": f["label"],
                        "Źródło": f["source"],
                        "Dostępność (as-of)": f["available"],
                        "Status": "niewystarczające dane historyczne",
                    }
                    for f in skipped["features"]
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    st.caption(
        "Statystyki meczowe (xG, strzały, duże okazje) i składy są zapisywane od jesieni 2026 — "
        "do oceny w oknach protokołu potrzebna jest ich historia sprzed meczów."
    )


def _challengers(db, scope: str) -> None:
    found = advanced_lab.versions(db, scope)
    if not found:
        st.caption("Brak challengera modelu rozszerzonego w tym zakresie.")
        return
    for version in found:
        data = version["training"]
        with st.container(border=True):
            st.markdown(
                f"**{advanced_lab.FAMILY_LABELS[version['family']]}** · {data['label']} · "
                f"wersja {version['version'][:40]} · status "
                + ui.pill("eksperymentalny", "warn")
                + f" · {version['role']}",
                unsafe_allow_html=True,
            )
            st.caption(
                "Rynki z potwierdzoną poprawą offline: "
                + ", ".join(advanced.MARKET_LABELS[m] for m in data["markets"])
            )
            now = c.now()
            if version["role"] == "czeka na decyzję":
                left, right = st.columns(2)
                if left.button(
                    "Dopuść do oceny prospektywnej (tryb cienia)",
                    key=f"admit_{version['version_id']}",
                    icon=":material/visibility:",
                    disabled=c.demo_mode(),
                ):
                    advanced_lab.admit(db, version_id=version["version_id"], now=now)
                    st.rerun()
                if right.button(
                    "Odrzuć",
                    key=f"reject_{version['version_id']}",
                    icon=":material/block:",
                    disabled=c.demo_mode(),
                ):
                    advanced_lab.reject(db, version_id=version["version_id"], now=now)
                    st.rerun()
            elif version["role"] == "tryb cienia":
                result = advanced_lab.prospective(db, version)
                for group, label in (("1x2", "1X2 vs Elo"), ("goals", "gole vs Dixon–Coles")):
                    row = result.get(group)
                    if row and row.get("n"):
                        st.write(
                            f"{label}: {row['n']} wspólnych rozliczonych prognoz, Δ log loss "
                            f"{row['difference']:+.4f}, 95% CI {_ci(row['ci95'])}"
                        )
                    elif row is not None:
                        st.write(f"{label}: brak jeszcze rozliczonych wspólnych prognoz.")
                st.caption("Promocja: " + result["promotion"]["reason"])


# ---------------------------------------------------------------- match page


def why(db, rows, context: dict, teams: dict, original=None) -> None:
    """The three parts of "Dlaczego model tak ocenia ten mecz?"."""
    prediction = original or context.get("prediction") or {}
    _used(rows, context, prediction)
    _not_used(db, context, teams)
    _experimental(db, rows, context, prediction)


def _used(rows, context: dict, prediction: dict) -> None:
    inputs = prediction.get("inputs") or {}
    family = str(prediction.get("family") or "")
    params = (prediction.get("validation") or {}).get("hyperparameters") or {}
    games = inputs.get("games") or []
    points = []
    if family.startswith("elo"):
        points.append(
            f"Elo (wersja {prediction.get('version', '—')}): siła drużyn z wyników ich meczów"
            + (f" — {games[0]} i {games[1]} meczów" if len(games) == 2 else "")
            + (
                f"; K = {params['k']}, przewaga własnego boiska {params['home_advantage']} pkt Elo"
                if "k" in params
                else ""
            )
            + "."
        )
    if any(r.model and "dixon" in r.model for r in rows if r.p_model is not None):
        points.append(
            "Dixon–Coles: średnie gole zdobyte i stracone obu drużyn z ostatnich 2 lat (starsze "
            "mecze ważą mniej) — z nich rozkład wyników, a z niego Over/Under i BTTS."
        )
    if points:
        points.append("Kalibracja prawdopodobieństw na meczach sprzed okna selekcji (protokół v2).")
    fresh = context.get("freshness") or {}
    if fresh.get("last_result"):
        points.append(f"Wyniki ligi znane do {fresh['last_result']}.")
    ui.explanation(
        "Dane faktycznie użyte przez aktywny model",
        points or ["Brak aktywnego modelu dla tego wydarzenia."],
        tone="accent",
    )


def _not_used(db, context: dict, teams: dict) -> None:
    points = []
    for side, label in (("home", "Gospodarze"), ("away", "Goście")):
        for point in ins.form_points(teams.get(side)):
            points.append(f"{label}: {point}")
    event_id = (context.get("event") or {}).get("id")
    if event_id:
        info = prematch.summary(db, event_id)
        points.append(
            f"Składy: {info['lineup_label']}; absencje: "
            + (
                f"{len(info['absences'])} zapisanych"
                if info["absences"]
                else "brak zweryfikowanego źródła"
            )
        )
    points.append(
        "Statystyki meczowe (xG, strzały, duże okazje): tylko opis — za krótka historia do "
        "oceny ich wpływu."
    )
    ui.explanation(
        "Dodatkowe dane, których model jeszcze nie uwzględnia",
        points + ["Te informacje NIE wpłynęły na prawdopodobieństwa aktywnego modelu."],
    )


def _experimental(db, rows, context: dict, prediction: dict) -> None:
    event_id = (context.get("event") or {}).get("id")
    shadow = advanced_lab.event_view(db, event_id) if event_id else []
    title = "Eksperymentalny model rozszerzony"
    if not shadow:
        found = advanced_lab.reports(db)
        latest = found[-1] if found else None
        text = (
            [
                f"Ostatni eksperyment ({c.local_time(latest['ran_at'], '%d.%m.%Y')}): "
                + latest["conclusion"],
                "Brak challengera w ocenie prospektywnej dla tego meczu — wyświetlany jest tylko "
                "aktywny model.",
            ]
            if latest
            else ["Eksperyment cech nie był jeszcze uruchomiony (Uczenie AI)."]
        )
        ui.explanation(title, text)
        return
    active = prediction.get("probabilities") or {}
    by_market = {}
    for row in shadow:
        if row["market_key"] == "1x2":
            by_market["1x2"] = row
        elif row["market_key"] == "total":
            by_market[f"total_{row['line']}"] = row
        elif row["market_key"] == "btts":
            by_market["btts"] = row
    table = []
    one = by_market.get("1x2")
    if one:
        for sel, label in (("home", "1"), ("draw", "X"), ("away", "2")):
            mine = one["probabilities"].get(sel)
            base = active.get(sel)
            table.append(
                {
                    "Rynek": f"1X2 — {label}",
                    "Aktywny model": _pct(base),
                    "Model rozszerzony": _pct(mine),
                    "Różnica": f"{(mine - base) * 100:+.1f} pp" if base is not None else "—",
                }
            )
    served = {
        (x.market_key, float(x.line) if x.line else None, x.selection): x.p_model
        for x in rows
        if x.p_model is not None
    }
    for market, sel, label in (
        ("total_1.5", "over", "Powyżej 1,5"),
        ("total_2.5", "over", "Powyżej 2,5"),
        ("btts", "yes", "BTTS — tak"),
    ):
        row = by_market.get(market)
        if row is None:
            continue
        mine = row["probabilities"].get(sel)
        key = ("btts", None, sel) if market == "btts" else ("total", float(row["line"]), sel)
        base = served.get(key)
        table.append(
            {
                "Rynek": label,
                "Aktywny model": _pct(base),
                "Model rozszerzony": _pct(mine),
                "Różnica": f"{(mine - base) * 100:+.1f} pp" if base is not None else "—",
            }
        )
    first = shadow[0]
    with st.container(border=True):
        st.markdown(
            f"**{title}** · wersja {first['version'][:40]} · "
            + ui.pill("eksperymentalny — niezwalidowany", "warn")
            + f" · prognoza z {c.local_time(first['created_at'])}",
            unsafe_allow_html=True,
        )
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
        st.caption(
            "Osobna, zamrożona przed meczem prognoza challengera (tryb cienia). Nie wpływa na "
            "kupony ani na procenty aktywnego modelu; służy wyłącznie do porównania prospektywnego."
        )
