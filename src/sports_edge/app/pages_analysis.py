"""Match analysis centre (7 panels) and the daily coupon builder view (STS ONLY)."""

import json
from decimal import Decimal

import pandas as pd
import streamlit as st

from sports_edge.analysis.coupon import NO_EDGE
from sports_edge.analysis.coupon_builder import eligibility
from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.paper import create_coupon
from sports_edge.repository import offer_days
from sports_edge.results_engine import SOURCE_LABELS
from sports_edge.services import (
    STAGES,
    analyse_day,
    event_context,
    event_features,
    event_lineups,
    policy,
    probability_policy,
    proposal_payload,
    run_daily_analysis,
    saved_proposals,
)
from sports_edge.sports_intel import latest_lineups


def freshness_text(x) -> str:
    if x.freshness == "unconfirmed" and x.odds_age_hours is not None:
        return f"🟠 Kurs niepotwierdzony od {x.odds_age_hours:.0f} h"
    return c.FRESHNESS.get(x.freshness, x.freshness)


def _probable_rows(items) -> pd.DataFrame:
    def note(x) -> str:
        if x.break_even is not None and x.break_even >= 1:
            return "⛔ strata nawet przy trafieniu (kurs × 0,88 < 1)"
        if x.ev_net is not None and x.ev_net < 0:
            return "⚠ oczekiwana strata po podatku"
        return "EV netto dodatnie (model eksperymentalny)"

    return pd.DataFrame(
        [
            {
                "Wydarzenie": x.event,
                "Liga": x.league,
                "Godzina": c.local_time(x.start_at, "%m-%d %H:%M"),
                "Rynek": x.market_label,
                "Typ": c.SELECTIONS.get(x.selection, x.selection),
                "Nr zakładu STS": x.offer_code or "",
                "Kurs STS": float(x.odds),
                "P wygranej (model)": round(x.p_model, 3),
                "P rynek STS (bez marży)": round(x.p_market, 3) if x.p_market is not None else None,
                "Próg opłacalności": round(x.break_even, 3) if x.break_even is not None else None,
                "EV netto": float(x.ev_net) if x.ev_net is not None else None,
                "Kalibracja modelu": x.calibration,
                "Świeżość kursu": freshness_text(x),
                "Ocena": note(x),
            }
            for x in items
        ]
    )


def _candidate_rows(items) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Wydarzenie": x.event,
                "Dyscyplina": x.sport,
                "Liga": x.league,
                "Godzina": c.local_time(x.start_at, "%m-%d %H:%M"),
                "Rynek": x.market_label,
                "Typ": c.SELECTIONS.get(x.selection, x.selection),
                "Nr zakładu STS": x.offer_code or "",
                "Kurs STS": float(x.odds),
                "P model": round(x.p_model, 4) if x.p_model is not None else None,
                "P rynek STS (bez marży)": round(x.p_market, 4) if x.p_market is not None else None,
                "EV netto": float(x.ev_net) if x.ev_net is not None else None,
                "EV netto (dolna)": float(x.ev_net_low) if x.ev_net_low is not None else None,
                "Niepewność p": f"[{x.p_low:.3f}, {x.p_high:.3f}]" if x.p_low is not None else "—",
                "Status modelu": x.model_status,
                "Uzasadnienie": x.explanation.get("Decyzja", ""),
                "Świeżość kursu": freshness_text(x),
                "Status wydarzenia": c.EVENT_STATUS.get(x.event_status, x.event_status),
            }
            for x in items
        ]
    )


# ---------------------------------------------------------------- Analiza AI


def analysis_page() -> None:
    c.setup_page()
    st.title("Centrum analizy meczu")
    st.caption(
        "Analiza statystyczna wydarzenia: niezależne wyniki i statystyki, modele Elo i "
        "Dixon–Coles z kalibracją, oferta STS. To nie jest analiza językowa (LLM)."
    )
    db = c.store()
    days = offer_days(db)
    if not days:
        st.info("Brak danych. Zaimportuj ofertę STS.")
        return
    today = str(c.today_local())
    day = st.selectbox("Dzień", days, index=days.index(today) if today in days else len(days) - 1)
    _report, candidates = analyse_day(
        db, c.paths(), c.settings(), day, now=c.now(), data_kinds=c.data_kinds()
    )
    events: dict[str, list] = {}
    for x in candidates:
        events.setdefault(x.event_id, []).append(x)
    if not events:
        st.info("Brak wydarzeń w tym dniu.")
        return
    with_forecast = {
        eid for eid, rows in events.items() if any(r.p_model is not None for r in rows)
    }
    only = st.toggle(
        f"Tylko wydarzenia z prognozą modelu ({len(with_forecast)})", value=bool(with_forecast)
    )
    shown = [eid for eid in events if not only or eid in with_forecast] or list(events)
    labels = {
        eid: f"{c.local_time(events[eid][0].start_at, '%H:%M')} · {events[eid][0].event} "
        f"({events[eid][0].league})"
        for eid in shown
    }
    choice = st.selectbox("Wydarzenie", shown, format_func=labels.get)
    rows = events[choice]
    head = rows[0]
    now = c.now()
    context = event_context(db, c.paths(), event_id=choice, now=now)
    features = event_features(db, c.paths(), context["event"], decision_time=now, save=False)
    teams = (features or {}).get("features", {}).get("teams", {})

    st.subheader("1. Mecz i kursy STS")
    c.pills(head.sport, head.league, head.event)
    cols = st.columns(3)
    cols[0].metric("Start (czas polski)", c.local_time(head.start_at, "%d.%m %H:%M"))
    cols[1].metric("Status wydarzenia", c.EVENT_STATUS.get(head.event_status, head.event_status))
    cols[2].metric("Świeżość kursu", freshness_text(head))
    sources = [
        f"Oferta STS: dokument z {c.local_time(head.document_at)}"
        + (f", pobrano {c.local_time(head.fetched_at)}" if head.fetched_at else ", plik ręczny")
    ]
    if context["freshness"]:
        f = context["freshness"]
        sources.append(
            f"Wyniki {f['name']}: ostatni {f['last_result']} ({f['days_since']} dni temu), "
            f"źródła: {', '.join(SOURCE_LABELS.get(p, p) for p in f['providers'])}"
        )
    else:
        sources.append("Brak niezależnych wyników dla tej ligi/dyscypliny — brak modelu.")
    for line in sources:
        st.write("• " + line)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Rynek": x.market_label,
                    "Opis w PDF": x.label_raw,
                    "Nr zakładu": x.offer_code or "",
                    "Typ": c.SELECTIONS.get(x.selection, x.selection),
                    "Kurs": float(x.odds),
                    "P implikowane": round(x.implied, 4),
                    "P bez marży": round(x.p_market, 4) if x.p_market is not None else None,
                    "Próg opłacalności": round(x.break_even, 4)
                    if x.break_even is not None
                    else None,
                }
                for x in rows
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    st.subheader("2. Forma i terminarz")
    _form_panel(context, teams)

    st.subheader("3. Statystyki meczowe (średnie z ostatnich meczów)")
    _stats_panel(teams)

    st.subheader("4. Składy i kontuzje")
    _lineups_panel(db, context, choice, now)

    st.subheader("5. Prawdopodobieństwa AI")
    _probabilities_panel(rows, context)

    st.subheader("6. Ryzyko")
    _risk_panel(rows)

    st.subheader("7. Wyjaśnienie")
    for line in _explain(rows, context, teams):
        st.write("• " + line)
    with st.expander("Diagnostyka (JSON)"):
        st.json(
            {
                "kontekst": {k: v for k, v in context.items() if k != "event"},
                "cechy": features,
                "selekcje": [x.explanation for x in rows[:12]],
            },
            expanded=False,
        )
    st.caption(
        "Moduł LLM jest opcjonalnym przyszłym rozszerzeniem; nie jest skonfigurowany i nie "
        "wpływa na analizę statystyczną. Reguły rynków: config/sts_rules.toml."
    )


def _form_panel(context, teams) -> None:
    if not context["teams"]:
        st.info("Ta liga nie ma niezależnego źródła wyników w aplikacji.")
        return
    cols = st.columns(2)
    for col, side in zip(cols, ("home", "away"), strict=True):
        team = context["teams"][side]
        info = teams.get(side) or {}
        with col:
            if not team["canonical"]:
                st.warning(
                    f"{team['sts']}: brak zweryfikowanego dopasowania nazwy ({team['status']}"
                    + (f", propozycja: {team['suggestion']}" if team["suggestion"] else "")
                    + ") — wyników nie zgadujemy. Potwierdź alias w zakładce Modele."
                )
                continue
            st.markdown(f"**{team['sts']}** → {team['canonical']} ({team['games']} meczów)")
            form = info.get("form5")
            if form:
                metrics = st.columns(3)
                metrics[0].metric("Forma (5)", form["sequence"])
                metrics[1].metric("Pkt/mecz (5)", form["points_per_game"])
                vs_elo = form.get("points_vs_elo_expectation")
                metrics[2].metric(
                    "Pkt ponad oczekiwania Elo",
                    f"{vs_elo:+.1f}" if vs_elo is not None else "—",
                    help="Punkty z 5 meczów minus punkty oczekiwane z siły rywali (Elo as-of).",
                )
                ten = info.get("form10") or {}
                venue = info.get("venue_form5") or {}
                st.caption(
                    f"Bramki (5): {form['goals_for']} : {form['goals_against']} na mecz · "
                    f"BTTS {form['btts_rate']:.0%} · powyżej 2,5 {form['over_2_5_rate']:.0%} · "
                    f"forma (10): {ten.get('sequence', '—')} · "
                    f"{'u siebie' if side == 'home' else 'na wyjeździe'} (5): "
                    f"{venue.get('sequence', '—')} · odpoczynek: "
                    f"{info.get('rest_days', '—')} dni · mecze w 7/14 dniach: "
                    f"{info.get('matches_last_7', '—')}/{info.get('matches_last_14', '—')}"
                )
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Data": m["date"],
                            "Miejsce": m["venue"],
                            "Przeciwnik": m["opponent"],
                            "Wynik": m["score"],
                            "W/R/P": m["result"],
                            "Znany przed prognozą": "tak" if m["known_before_forecast"] else "nie",
                            "Źródło": SOURCE_LABELS.get(m["source"], m["source"])
                            + (" ⚠ konflikt" if m["conflict"] else ""),
                        }
                        for m in team["matches"]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


def _stats_panel(teams) -> None:
    lines = []
    for side in ("home", "away"):
        for key, item in ((teams.get(side) or {}).get("stats5") or {}).items():
            lines.append((key, item["label"], side, item))
    if not lines:
        st.info(
            "Brak zapisanych statystyk meczowych tych drużyn (PitchAPI: top 5 lig i Ekstraklasa; "
            "pobierane podczas generowania kuponów, w limicie zapytań na uruchomienie)."
        )
        return
    table: dict[str, dict] = {}
    for key, label, side, item in lines:
        row = table.setdefault(key, {"Statystyka": label})
        name = "Gospodarze" if side == "home" else "Goście"
        row[f"{name}: za"] = item["for"]
        row[f"{name}: przeciw"] = item["against"]
        row[f"{name}: mecze"] = item["matches"]
    st.dataframe(pd.DataFrame(list(table.values())), hide_index=True, width="stretch")
    st.caption(
        "Średnie z ostatnich do 5 meczów z dostępnymi statystykami, znanych przed chwilą analizy. "
        "Statystyki są opisowe: modele z nich jeszcze nie korzystają (brak walidacji ich wpływu)."
    )


def _lineups_panel(db, context, event_id, now) -> None:
    teams = context["teams"]
    canonical = [teams[s]["canonical"] for s in ("home", "away")] if teams else []
    stored = None
    if canonical and all(canonical):
        stored = latest_lineups(
            db, home=canonical[0], away=canonical[1], day=context["event"]["start_at"][:10]
        )
    if st.button("Sprawdź składy (PitchAPI, 2 zapytania)", key=f"lineups_{event_id}"):
        out = event_lineups(db, c.paths(), c.settings(), event_id=event_id, now=now)
        if out["status"] == "ok":
            stored = {"lineups": out["lineups"], "observed_at": out["observed_at"]}
        else:
            st.info(out["status"])
    if stored:
        cols = st.columns(2)
        for col, side in zip(cols, ("home", "away"), strict=True):
            part = stored["lineups"][side]
            with col:
                st.markdown(
                    f"**{part['team']}** · {part['formation'] or '—'} · "
                    + ("✅ potwierdzony" if part["confirmed"] else "❔ przewidywany")
                    + (f" · trener {part['coach']}" if part["coach"] else "")
                )
                st.write(", ".join(p["name"] or "?" for p in part["starters"]) or "—")
        st.caption(f"Obserwacja z {c.local_time(stored['observed_at'])} (PitchAPI).")
    else:
        st.caption("Składy publikowane są zwykle około godziny przed meczem.")
    st.caption(
        "Kontuzje i zawieszenia: brak bezpłatnego, zweryfikowanego źródła w aplikacji. Modele nie "
        "uwzględniają składów ani kontuzji — sprawdź je samodzielnie przed decyzją."
    )


def _probabilities_panel(rows, context) -> None:
    modelled = [x for x in rows if x.p_model is not None]
    prediction = context["prediction"]
    if prediction:
        probs = prediction["probabilities"]
        fair = {x.selection: x.p_market for x in rows if x.market_key == "1x2" and x.period == "FT"}
        chart = pd.DataFrame(
            {
                "Model": [probs.get(k) for k in ("home", "draw", "away")],
                "Rynek STS bez marży": [fair.get(k) for k in ("home", "draw", "away")],
            },
            index=["1 — gospodarze", "X — remis", "2 — goście"],
        )
        st.bar_chart(chart, stack=False)
    if not modelled:
        st.info(
            "Brak prognozy modelu dla tego wydarzenia (liga bez danych, nieaktualne dane, za "
            "krótka historia drużyny albo wydarzenie już się rozpoczęło)."
        )
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Rynek": x.market_label,
                    "Typ": c.SELECTIONS.get(x.selection, x.selection),
                    "Kurs STS": float(x.odds),
                    "P model (skalibrowane)": round(x.p_model, 3),
                    "P ostrożnie": round(x.p_low, 3) if x.p_low is not None else None,
                    "P rynek bez marży": round(x.p_market, 3) if x.p_market is not None else None,
                    "Status predykcyjny": PREDICTIVE.get(x.predictive_status, x.predictive_status),
                    "Kalibracja (holdout)": x.calibration,
                    "Model": x.model or "—",
                }
                for x in sorted(modelled, key=lambda x: -(x.p_low or 0))
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Status predykcyjny dotyczy trafności i kalibracji prognoz na nietkniętym holdoucie — nie "
        "oznacza przewagi ekonomicznej nad STS (ta ma osobny status polityki EV)."
    )


def _risk_panel(rows) -> None:
    max_odds = float(probability_policy(c.paths()).builder["max_odds_per_leg"])
    modelled = [x for x in rows if x.p_model is not None]
    if not modelled:
        st.write("Bez prognozy modelu żadna selekcja tego meczu nie trafi do kuponu.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Rynek": x.market_label,
                    "Typ": c.SELECTIONS.get(x.selection, x.selection),
                    "Kurs": float(x.odds),
                    "P ostrożnie": round(x.p_low, 3) if x.p_low is not None else None,
                    "Próg opłacalności": round(x.break_even, 3)
                    if x.break_even is not None
                    else None,
                    "Do kuponu": "⛔ " + why if why else "✅ może wejść",
                }
                for x, why in ((x, eligibility(x, max_odds)) for x in modelled)
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _explain(rows, context, teams) -> list[str]:
    prediction = context["prediction"]
    out = []
    if context["freshness"]:
        f = context["freshness"]
        out.append(
            f"Dane: wyniki ligi {f['name']} do {f['last_result']} "
            f"({sum(f['providers'].values())} meczów; dostępność: "
            f"{', '.join(f['availability_basis'])})."
        )
    else:
        out.append("Dane: brak niezależnych wyników dla tego wydarzenia.")
    if prediction:
        p = prediction["probabilities"]
        out.append(
            f"Model 1X2 ({prediction['family']}): 1 — {p['home']:.1%}, X — {p['draw']:.1%}, "
            f"2 — {p['away']:.1%}."
        )
    best = max(
        (x for x in rows if x.p_low is not None),
        key=lambda x: x.p_low,
        default=None,
    )
    if best is not None:
        out.append(
            f"Najwyższa ostrożna szansa: {best.market_label} · "
            f"{c.SELECTIONS.get(best.selection, best.selection)} @ {best.odds} — "
            f"{best.p_model:.0%} (ostrożnie {best.p_low:.0%}); {best.calibration}."
        )
        if best.break_even is not None and best.break_even >= 1:
            out.append(
                "Ten kurs po współczynniku 0,88 nie zwraca nawet stawki — do kuponu nie wejdzie."
            )
    elif not prediction:
        out.append("Model nie przygotował prognozy — prawdopodobieństwa nie są liczone.")
    for side in ("home", "away"):
        form = (teams.get(side) or {}).get("form5")
        if form:
            out.append(
                f"{teams[side]['team']}: forma {form['sequence']}, {form['points_per_game']} pkt/mecz"
                f", bramki {form['goals_for']}:{form['goals_against']}."
            )
    out.append(
        "Ograniczenia: modele opierają się na wynikach (Elo dla 1X2, Dixon–Coles dla bramek) — bez "
        "składów, kontuzji i motywacji; statystyki meczowe są pokazywane, ale jeszcze nie "
        "wchodzą do modeli. Wysoka szansa nie oznacza zysku po podatku."
    )
    return out


# ---------------------------------------------------------------- Kupon dnia

PROFILE_ICONS = {"safe": "🛡️", "balanced": "⚖️", "aggressive": "🔥"}
LEVELS = {
    "validated_predictive": "✅ Zwalidowany predykcyjnie (bez potwierdzonej przewagi nad STS)",
    "experimental": "🧪 Eksperymentalny — tylko symulacja",
}
PREDICTIVE = {
    "validated_predictive": "✅ zwalidowany predykcyjnie",
    "experimental": "🧪 eksperymentalny",
    "brak prognozy": "brak prognozy",
}


def coupon_page() -> None:
    c.setup_page()
    st.title("SPORTS EDGE AI — NAJLEPSZE KUPONY DNIA")
    db = c.store()
    days = offer_days(db)
    if not days:
        st.info("Brak danych. Zaimportuj ofertę STS.")
        return
    today = str(c.today_local())
    day = st.selectbox(
        "Dzień analizy", days, index=days.index(today) if today in days else len(days) - 1
    )
    rules = probability_policy(c.paths())
    settings = c.settings()
    st.caption(
        "Kreator szuka kuponów o najwyższej OSTROŻNIE oszacowanej szansie trafienia, które po "
        "współczynniku 0,88 i podatku od wygranej dają realny zysk. Selekcje pochodzą z różnych "
        "meczów; EV jest wyłącznie diagnostyką. "
        f"Polityka prawdopodobieństwa {rules.version} (config/probability_policy.toml)."
    )
    online = not settings.offline_mode and not c.demo_mode()
    cols = st.columns(2)
    refresh_offer = cols[0].checkbox(
        "Pobierz aktualną ofertę STS przed analizą",
        value=online and settings.sts_download_enabled,
        disabled=not (online and settings.sts_download_enabled),
        help="Oficjalny PDF STS, maks. raz na 10 minut na plik; wymaga zgody w Ustawieniach.",
    )
    refresh_results = cols[1].checkbox(
        "Uzupełnij wyniki i statystyki (bezpłatne źródła)",
        value=online,
        disabled=not online,
        help="Tylko brakujący okres i limit zapytań na uruchomienie; płatne źródła nigdy.",
    )
    if st.button("GENERUJ KUPONY DNIA", type="primary", width="stretch"):
        bar = st.progress(0.0, text="Start analizy…")
        status = st.status(f"Analiza dnia — {len(STAGES)} etapów", expanded=True)

        def progress(index: int, name: str, detail: str) -> None:
            bar.progress((index + 1) / len(STAGES), text=f"Etap {index + 1}/{len(STAGES)}: {name}")
            status.write(f"**{index + 1}. {name}** — {detail}")

        run = run_daily_analysis(
            db,
            c.paths(),
            settings,
            day,
            now=c.now(),
            refresh_offer=refresh_offer,
            refresh_results=refresh_results,
            data_kinds=c.data_kinds(),
            progress=progress,
        )
        status.update(label="Analiza zakończona i zapisana", state="complete", expanded=False)
        st.session_state["coupon"] = run
    run = st.session_state.get("coupon")
    current = run is not None and run.report.day == day and run.coupons is not None
    if current:
        proposals = {
            name: proposal_payload(p, policy_version=rules.version) if p else None
            for name, p in run.coupons.proposals.items()
        }
        refusals, ids = run.coupons.refusals, run.coupon_ids
    else:
        saved = saved_proposals(db, day)
        if not saved:
            st.info(
                "„GENERUJ KUPONY DNIA” sprawdza ofertę STS, uzupełnia wyniki i statystyki, "
                "liczy prawdopodobieństwa modeli, buduje kupony trzech profili i zapisuje je "
                "jako wirtualne (bez nadpisywania poprzednich analiz)."
            )
            return
        st.caption("Ostatnio zapisane propozycje dla tego dnia (kliknij przycisk, aby odświeżyć).")
        proposals = {name: saved.get(name) for name in rules.profiles}
        refusals = {
            name: "Brak zapisanej propozycji — wygeneruj ponownie, aby zobaczyć powód."
            for name in rules.profiles
            if name not in saved
        }
        ids = {name: v["coupon_id"] for name, v in saved.items()}
    offer = run.stages[0] if current else {}
    age = offer.get("document_age_hours")
    if age is not None and age > settings.max_quote_age_minutes / 60:
        st.warning(
            f"Najnowsza oferta STS ma {age:.1f} h (próg świeżości "
            f"{settings.max_quote_age_minutes / 60:.0f} h): kursy niepotwierdzone nie wchodzą do "
            "kuponów. Pobierz aktualny PDF STS i wygeneruj kupony ponownie."
        )
    if any(proposals.values()):
        c.banner("Propozycje kuponów — szansa trafienia to oszacowanie modelu, nie gwarancja", "ok")
    else:
        c.banner(
            "BRAK KUPONU SPEŁNIAJĄCEGO WARUNKI — żaden profil nie ma bezpiecznej kombinacji",
            "noedge",
        )
    cards = st.columns(len(rules.profiles))
    for col, (name, profile) in zip(cards, rules.profiles.items(), strict=True):
        with col:
            _coupon_card(name, profile, proposals.get(name), refusals.get(name), ids.get(name))
    st.caption(
        "Łączna szansa kuponu = iloczyn szans selekcji z różnych meczów (założenie niezależności); "
        "dwóch rynków jednego meczu nigdy nie mnożymy. „Ostrożnie” to dolna granica 90% "
        "przedziału kalibracji. Aplikacja nie obstawia; wynik jednego dnia niczego nie dowodzi."
    )
    if not current:
        return
    with st.expander(f"Etapy analizy ({len(run.stages)})"):
        st.dataframe(
            pd.DataFrame(
                [
                    {"Etap": s["stage"], "Status": s["status"], "Szczegóły": s["detail"]}
                    for s in run.stages
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with st.expander("Pula selekcji kreatora i wykluczenia"):
        if run.coupons.pool:
            st.dataframe(_pool_rows(run.coupons.pool), hide_index=True, width="stretch")
        st.dataframe(
            pd.DataFrame(
                [{"Powód wykluczenia": k, "Selekcje": v} for k, v in run.coupons.excluded.items()]
            ),
            hide_index=True,
            width="stretch",
        )
    with st.expander("Analiza EV i ranking selekcji (diagnostyka)"):
        _ev_analysis(db, run)
    with st.expander("Diagnostyka — uzasadnienia selekcji (JSON)"):
        query = st.text_input("Filtr wydarzenia").strip().lower()
        shown = (
            [x for x in run.candidates if query in x.event.lower()][:30]
            if query
            else run.candidates[:10]
        )
        for x in shown:
            st.markdown(
                f"**{x.event}** · {x.market_label} · "
                f"{c.SELECTIONS.get(x.selection, x.selection)} @ {x.odds} — {c.TIERS[x.tier]}"
            )
            st.json(x.explanation, expanded=False)
        st.code(json.dumps(run.stages, ensure_ascii=False, indent=1, default=str)[:20000])


def _coupon_card(name: str, profile: dict, payload: dict | None, refusal, coupon_id) -> None:
    with st.container(border=True):
        st.markdown(f"### {PROFILE_ICONS.get(name, '')} {profile['label'].upper()}")
        st.caption(
            f"do {profile['max_legs']} selekcji · każda ≥ {profile['min_leg_probability']:.0%} "
            f"(ostrożnie) · kurs ≥ {profile['min_total_odds']} · zysk netto ≥ "
            f"{profile['min_net_profit_share']:.0%} stawki"
        )
        if payload is None:
            st.error("BRAK KUPONU")
            st.write(refusal or "—")
            return
        st.markdown(LEVELS.get(payload["level"], payload["level"]))
        p = payload["probability"]
        top = st.columns(2)
        top[0].metric("Szansa (model)", f"{p['calibrated']:.0%}")
        top[1].metric("Ostrożnie", f"{p['conservative']:.0%}")
        st.markdown(
            "| Kurs łączny | Wypłata | Zysk netto |\n|:-:|:-:|:-:|\n"
            f"| **{float(payload['total_odds']):.2f}** | **{payload['payout']} zł** "
            f"| **{payload['net_profit']} zł** |"
        )
        st.caption(
            f"Stawka {payload.get('stake', '10.00')} zł · wypłata po współczynniku 0,88 "
            "i podatku od wygranej"
        )
        for i, leg in enumerate(payload["legs"], 1):
            age = leg.get("odds_age_hours")
            st.markdown(
                f"**{i}. {leg['event']}**  \n"
                f"{c.local_time(leg['start_at'], '%d.%m %H:%M')} · {leg.get('league', '')}  \n"
                f"{leg['market']} → **{c.SELECTIONS.get(leg['selection'], leg['selection'])}** "
                f"@ **{leg['odds']}** · nr STS {leg['offer_code'] or '—'}  \n"
                f"P {leg['p_model']:.0%} (ostrożnie {leg['p_conservative']:.0%}) · "
                f"{PREDICTIVE.get(leg['predictive_status'], leg['predictive_status'])}"
            )
            st.caption(
                f"Kurs z dokumentu STS {c.local_time(leg['document_at'])}"
                + (f" ({age:.1f} h temu)" if age is not None else "")
            )
        with st.expander("DLACZEGO WYBRALIŚMY TEN KUPON?"):
            for line in payload["reasons"]:
                st.write("• " + line)
            st.write(
                f"• Niepewność: łączna szansa od {p['conservative']:.0%} (ostrożnie) do "
                f"{p['calibrated']:.0%} (model); {p['basis']}."
            )
            st.write(
                f"• EV netto (diagnostyka): {float(payload['ev_net']):+.3f} zł na 1 zł stawki."
            )
            for warning in payload["warnings"]:
                st.warning(warning)
        if coupon_id and coupon_id.startswith("nie zapisano"):
            st.caption(coupon_id)
        elif coupon_id:
            st.caption(
                f"Zapisano jako wirtualny kupon `{coupon_id[:10]}…`; rozliczy się automatycznie "
                "po pobraniu wyników."
            )
        if payload.get("outcome"):
            st.info(
                f"Rozliczenie: {payload['outcome']} · zysk netto {payload['settled_net_profit']} zł"
            )


def _pool_rows(pool) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Wydarzenie": x.event,
                "Godzina": c.local_time(x.start_at, "%m-%d %H:%M"),
                "Rynek": x.market_label,
                "Typ": c.SELECTIONS.get(x.selection, x.selection),
                "Kurs STS": float(x.odds),
                "P model": round(x.p_model, 3),
                "P ostrożnie": round(x.p_low, 3),
                "Próg opłacalności": round(x.break_even, 3),
                "Status predykcyjny": PREDICTIVE.get(x.predictive_status, x.predictive_status),
                "Kalibracja": x.calibration,
            }
            for x in pool
        ]
    )


def _ev_analysis(db, run) -> None:
    """TASK 004 view kept as diagnostics: probability ranking, EV rankings, rejections."""
    report = run.report
    rules = policy(c.paths())
    if report.headline == NO_EDGE:
        c.banner(NO_EDGE, "noedge")
    else:
        c.banner(report.headline + " — analiza, nie rekomendacja gry", "ok")
    for line in report.diagnosis:
        st.write("• " + line)
    stages = {s["stage"]: s for s in run.stages}
    counts = report.counts
    row1 = st.columns(4)
    row1[0].metric("Wydarzenia w ofercie", counts.get("events", 0))
    row1[1].metric("Poprawnie odczytane rynki", counts.get("markets_ok", 0))
    row1[2].metric(
        "Wydarzenia z aktualnymi danymi", stages.get("Dopasowanie", {}).get("considered", 0)
    )
    model_stage = stages.get("Modele", {})
    row1[3].metric(
        "Prognozy (nowe + aktualne)",
        model_stage.get("forecasts_saved", 0) + model_stage.get("forecasts_kept", 0),
    )
    row2 = st.columns(5)
    row2[0].metric("Rynki obsługiwane przez model", counts.get("model_markets", 0))
    row2[1].metric("Kandydaci EV+", counts.get("ev_positive", 0))
    row2[2].metric("Eksperymentalni", counts.get("experimental", 0))
    row2[3].metric("Zwalidowani (EV)", counts.get("validated", 0))
    row2[4].metric(f"Typy z P ≥ {rules.probable_min_probability:.0%}", counts.get("probable", 0))
    st.subheader(
        f"Najwyższe prawdopodobieństwo wygranej (P ≥ {rules.probable_min_probability:.0%})"
    )
    st.caption(
        "Ranking według prawdopodobieństwa modelu. W STS wygrana to 0,88 × stawka × kurs, więc "
        "„Próg opłacalności” = 1 / (0,88 × kurs); kurs poniżej 1,14 przynosi stratę nawet przy "
        "trafieniu."
    )
    if report.probable:
        st.dataframe(_probable_rows(report.probable), hide_index=True, width="stretch")
    else:
        st.write("Brak selekcji z prognozą i potwierdzonym (świeżym) kursem na tym poziomie.")
    if report.probable_unconfirmed:
        st.markdown(
            f"**Kursy niepotwierdzone ({len(report.probable_unconfirmed)})** — ceny ze starszej "
            "migawki STS; pobierz aktualną ofertę, zanim potraktujesz je jako dostępne."
        )
        frame = _probable_rows(report.probable_unconfirmed)
        frame.insert(
            7,
            "Ostatnia obserwacja kursu",
            [c.local_time(x.document_at) for x in report.probable_unconfirmed],
        )
        st.dataframe(frame, hide_index=True, width="stretch")
    st.subheader("Ranking SOLO — kandydaci zwalidowani ekonomicznie (EV)")
    if report.solo:
        st.dataframe(_candidate_rows(report.solo), hide_index=True, width="stretch")
        profile = load_profiles(c.paths().config)["sts"]
        for x in report.solo:
            if st.button(
                f"Zapisz jako wirtualny kupon: {x.event} / {x.selection}", key=f"paper_{x.key}"
            ):
                try:
                    create_coupon(
                        db,
                        odds_ids=[x.odds_id],
                        stake=Decimal(c.settings().nominal_stake),
                        mode="paper_system",
                        profile=profile,
                        now=c.now(),
                        analysis_id=run.analysis_id,
                    )
                    st.success("Zapisano w historii (paper trading).")
                except ValueError as error:
                    st.error(str(error))
    else:
        st.write("Brak selekcji spełniających warunki polityki EV.")
    st.subheader("Ranking eksperymentalny (EV) — przewaga niepotwierdzona")
    if report.experimental:
        st.dataframe(_candidate_rows(report.experimental[:50]), hide_index=True, width="stretch")
    else:
        st.write("Brak kandydatów eksperymentalnych (brak prognoz z dodatnim EV netto).")
    st.subheader("AKO (polityka EV)")
    st.write(("✅ " if report.ako.created else "⛔ ") + report.ako.reason)
    st.subheader("Powody odrzuceń")
    if report.rejection_reasons:
        st.dataframe(
            pd.DataFrame(
                [
                    {"Powód": k, "Liczba selekcji": v}
                    for k, v in list(report.rejection_reasons.items())[:25]
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    for warning in report.warnings:
        st.warning(warning)
