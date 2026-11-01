"""Kupon dnia: the coupon builder for today, tomorrow, 3 or 7 days — every day separately."""

import json

import pandas as pd
import streamlit as st

from sports_edge import insights as ins
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.repository import offer_days
from sports_edge.services import (
    RANGES,
    analysis_days,
    offer_status,
    probability_policy,
    proposal_payload,
    refresh_offers,
    run_analysis,
    saved_proposals,
)

PROFILE_ORDER = ("safe", "balanced", "aggressive")
PROFILE_TEXT = {
    "safe": "Priorytet: możliwie najwyższa szansa trafienia, mało selekcji, dopuszczalne SOLO.",
    "balanced": "Kompromis między szansą trafienia a wypłatą, niewielka liczba selekcji.",
    "aggressive": "Wyższy kurs i niższa szansa trafienia — wyraźnie zwiększone ryzyko.",
}
PHASES = (
    "Pobieranie danych",
    "Analizowanie wydarzeń",
    "Obliczanie prawdopodobieństw",
    "Wybór najlepszych typów",
    "Przygotowywanie kuponów",
)
STAGE_PHASE = {0: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 2, 6: 2, 7: 3}
REFRESH_TONE = {
    "new": "ok",
    "duplicate": "warn",
    "empty": "bad",
    "no_day_events": "warn",
    "blocked": "bad",
    "skipped": "neutral",
}
REFRESH_LABEL = {
    "new": "Nowy dokument",
    "duplicate": "Ten sam dokument",
    "empty": "Pusty plik",
    "no_day_events": "Bez wydarzeń tych dni",
    "blocked": "Zablokowane",
    "skipped": "Nie wykonano",
}


def coupon_page() -> None:
    c.setup_page()
    ui.app_header("KUPON DNIA", "Najbardziej prawdopodobne propozycje według modeli Sports Edge AI")
    db = c.store()
    if not offer_days(db):
        ui.empty_state(
            "Brak oferty STS", "Zaimportuj PDF z ofertą STS albo pobierz oficjalny plik."
        )
        return
    settings = c.settings()
    rules = probability_policy(c.paths())
    choice = (
        st.segmented_control("Zakres analizy", list(RANGES), default="Dziś", key="coupon_range")
        or "Dziś"
    )
    days = analysis_days(choice, c.today_local())
    online = not settings.offline_mode and not c.demo_mode()
    with st.expander("Opcje analizy", expanded=False):
        refresh_offer = st.checkbox(
            "Sprawdź oficjalną ofertę STS przed analizą",
            value=online and settings.sts_download_enabled,
            disabled=not (online and settings.sts_download_enabled),
            help="Oficjalne pliki PDF STS (maks. raz na 10 minut na plik); wymaga zgody w Ustawieniach.",
        )
        refresh_results = st.checkbox(
            "Uzupełnij wyniki i statystyki (bezpłatne źródła)",
            value=online,
            disabled=not online,
            help="Tylko brakujący okres i limit zapytań; płatne źródła nigdy nie są używane.",
        )
        st.caption(
            f"Polityka prawdopodobieństwa {rules.version} · stawka {rules.builder['stake']} zł · "
            "daty i godziny w strefie Europe/Warsaw."
        )
    if st.button("ANALIZUJ MECZE I GENERUJ KUPONY", type="primary", width="stretch"):
        run = _run(db, settings, days, refresh_offer, refresh_results)
        if run is not None:
            st.session_state["coupon_run"] = run
    run = st.session_state.get("coupon_run")
    current = run if run is not None and run.days == days else None
    if current is not None and current.refresh:
        _refresh_panel(current.refresh)
    for day in days:
        _day(db, settings, rules, day, current.runs.get(day) if current else None, online)
    ui.section("Najlepsze typy do rozważenia", "także gdy nie powstał kupon")
    _pool(days, rules)
    if current is not None:
        with st.expander("Szczegóły techniczne (pełny log analizy)"):
            _technical(current)
    st.caption(
        "Szansa kuponu to iloczyn szans selekcji z różnych meczów (bez dwóch rynków jednego "
        "meczu); „ostrożnie” to dolna granica przedziału kalibracji. Kupony są wirtualne — "
        "aplikacja nie obstawia i nie gwarantuje wygranej."
    )


# ---------------------------------------------------------------- run


def _run(db, settings, days, refresh_offer, refresh_results):
    bar = st.progress(0.0, text=PHASES[0])
    box = st.status("Analiza meczów i budowa kuponów", expanded=True)
    total, done, seen = 7 + 7 * len(days), [0], set()

    def progress(index: int, name: str, detail: str) -> None:
        done[0] += 1
        phase = PHASES[STAGE_PHASE.get(index, 4)]
        bar.progress(min(1.0, done[0] / total), text=phase)
        if phase not in seen:
            seen.add(phase)
            box.write(phase)

    try:
        run = run_analysis(
            db,
            c.paths(),
            settings,
            days,
            now=c.now(),
            refresh_offer=refresh_offer,
            refresh_results=refresh_results,
            data_kinds=c.data_kinds(),
            progress=progress,
        )
    except Exception as error:  # noqa: BLE001 — any failure must be explained, not raised
        box.update(label="Analiza przerwana", state="error", expanded=True)
        ui.error_state("Analiza nie powiodła się", f"{type(error).__name__}: {error}")
        return None
    made = sum(
        1
        for r in run.runs.values()
        for p in (r.coupons.proposals if r.coupons else {}).values()
        if p
    )
    bar.progress(1.0, text="Gotowe")
    box.update(
        label=f"Analiza zakończona — propozycje kuponów: {made}"
        if made
        else "Analiza zakończona — nie powstał żaden kupon (powód poniżej)",
        state="complete",
        expanded=False,
    )
    return run


def _refresh_panel(attempts: list[dict]) -> None:
    ui.section("Oferta STS — wynik sprawdzenia")
    cards = []
    for a in attempts:
        cards.append(
            ui.card(
                f"<b>{ui.esc(a['label'].capitalize())}</b><br>"
                + ui.pill(
                    REFRESH_LABEL.get(a["status"], a["status"]),
                    REFRESH_TONE.get(a["status"], "neutral"),
                )
                + f"<div style='color:#94A3B8;font-size:.86rem'>{ui.esc(a['message'])}</div>",
                tone="warn" if REFRESH_TONE.get(a["status"]) in {"warn", "bad"} else "",
            )
        )
    ui.card_grid(cards)


# ---------------------------------------------------------------- one day


def _day(db, settings, rules, day, run, online) -> None:
    now = c.now()
    offer = (
        run.offer
        if run is not None and run.offer
        else offer_status(db, settings, day, now=now, data_kinds=c.data_kinds())
    )
    ui.section(c.day_label(day).capitalize())
    badges = [ui.freshness_pill(offer["age_hours"], offer["limit_hours"])]
    days_ahead = (pd.Timestamp(day).date() - c.today_local()).days
    if days_ahead >= 2:
        badges.append(ui.pill("Mecze za kilka dni — wyższa niepewność (składy nieznane)", "warn"))
    st.markdown("".join(badges), unsafe_allow_html=True)
    max_odds = float(rules.builder["max_odds_per_leg"])
    if run is not None:
        builder = run.coupons
        proposals = {
            name: proposal_payload(p, policy_version=rules.version) if p else None
            for name, p in builder.proposals.items()
        }
        refusals = builder.refusals
        ids = run.coupon_ids
        alternatives = builder.alternatives
        candidates = run.candidates
    else:
        saved = saved_proposals(db, day)
        proposals = {name: saved.get(name) for name in rules.profiles}
        refusals, alternatives, builder = {}, {}, None
        ids = {name: v["coupon_id"] for name, v in saved.items()}
        _report, candidates = c.day_candidates(day)
        if saved:
            st.caption(
                "Ostatnio zapisane propozycje dla tego dnia — kliknij „Analizuj”, aby odświeżyć."
            )
    if not any(proposals.values()):
        explanation = ins.explain_no_coupon(candidates, builder, offer, now=now, max_odds=max_odds)
        _no_coupon(explanation, day, run is not None, online)
        return
    columns = st.container(key=f"cards-{day}").columns(len(rules.profiles))
    for col, name in zip(columns, rules.profiles, strict=True):
        with col:
            _card(
                name,
                rules.profiles[name],
                proposals.get(name),
                refusals.get(name),
                ids.get(name),
                [
                    proposal_payload(a, policy_version=rules.version)
                    for a in alternatives.get(name, ())
                ],
            )


def _no_coupon(explanation: dict | None, day: str, analysed: bool, online: bool) -> None:
    if explanation is None:
        return
    title = (
        "Brak kuponu — " + explanation["label"]
        if analysed
        else "Brak zapisanego kuponu — " + explanation["label"]
    )
    points = [explanation["headline"]]
    points.append(
        f"Typy, których dotyczy wyjaśnienie: {explanation['count']}; odrzucone z innych "
        f"powodów: {explanation['rejected']}."
    )
    for reason, n in explanation["reasons"][:5]:
        points.append(f"{reason}: {n}")
    points.append("Co możesz zrobić: " + explanation["action"])
    ui.explanation(title, points, tone="warn")
    if (
        explanation["case"] == "A"
        and online
        and st.button("Sprawdź ponownie oficjalną ofertę STS", key=f"refresh_{day}")
    ):
        attempts = refresh_offers(c.store(), c.paths(), c.settings(), [day], now=c.now())
        _refresh_panel(attempts)


def _card(name, profile, payload, refusal, coupon_id, alternatives) -> None:
    with st.container(border=True):
        st.markdown(
            f'<div class="se-title">{ui.esc(profile["label"])}</div>'
            f'<p class="se-desc">{ui.esc(PROFILE_TEXT.get(name, ""))}</p>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"do {profile['max_legs']} selekcji · każda ≥ "
            f"{ins.pct(profile['min_leg_probability'], 0)} ostrożnie · kurs ≥ "
            f"{ins.num(profile['min_total_odds'])} · zysk ≥ "
            f"{ins.pct(profile['min_net_profit_share'], 0)} stawki"
        )
        if payload is None:
            st.markdown(ui.pill("Brak propozycji", "neutral"), unsafe_allow_html=True)
            st.write(refusal or "Profil nie znalazł kombinacji spełniającej warunki.")
            return
        validated = payload["level"] == "validated_predictive"
        st.markdown(
            ui.pill("Zwalidowany predykcyjnie", "ok")
            if validated
            else ui.pill("SYMULACJA — model eksperymentalny", "warn"),
            unsafe_allow_html=True,
        )
        p = payload["probability"]
        st.markdown(
            ui.probability_bars(
                [
                    ("Szansa kuponu", p["calibrated"], ins.pct(p["calibrated"])),
                    ("Ostrożnie", p["conservative"], ins.pct(p["conservative"])),
                ]
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            ui.key_values(
                [
                    ("Kurs łączny", ins.num(payload["total_odds"])),
                    ("Stawka", ins.money(payload.get("stake", "10.00"))),
                    ("Wypłata", ins.money(payload["payout"])),
                    ("Zysk netto", ins.money(payload["net_profit"])),
                ]
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            ui.legs_html(
                [
                    {
                        "event": leg["event"].replace(" - ", " – "),
                        "when": f"{c.local_time(leg['start_at'], '%d.%m %H:%M')} · "
                        f"{leg.get('league', '')} · nr STS {leg['offer_code'] or '—'}",
                        "pick": ins.pick_label(
                            leg.get("market_key", ""),
                            leg.get("line"),
                            leg["selection"],
                            leg["event"],
                            leg["market"],
                        ),
                        "odds": ins.num(leg["odds"]),
                        "detail": f"szansa {ins.pct(leg['p_model'])} (ostrożnie "
                        f"{ins.pct(leg['p_conservative'])}) · "
                        + (
                            "zwalidowany"
                            if leg["predictive_status"] == "validated_predictive"
                            else "eksperymentalny"
                        ),
                    }
                    for leg in payload["legs"]
                ]
            ),
            unsafe_allow_html=True,
        )
        with st.expander("Dlaczego ten kupon?"):
            for line in payload["reasons"]:
                st.write("• " + line)
            st.write(
                f"• Niepewność: szansa całego kuponu od {ins.pct(p['conservative'])} (ostrożnie) "
                f"do {ins.pct(p['calibrated'])} (model); {p['basis']}."
            )
            st.write(
                f"• Opłacalność (diagnostyka): EV netto {ins.num(payload['ev_net'], 3)} zł na 1 zł "
                "— wysoka szansa trafienia nie oznacza zysku."
            )
            for warning in payload["warnings"]:
                st.warning(warning)
        if alternatives:
            with st.expander(f"Alternatywne propozycje ({len(alternatives)})"):
                for alt in alternatives:
                    q = alt["probability"]
                    legs = " + ".join(
                        ins.pick_label(
                            leg.get("market_key", ""),
                            leg.get("line"),
                            leg["selection"],
                            leg["event"],
                            leg["market"],
                        )
                        + f" ({leg['event']})"
                        for leg in alt["legs"]
                    )
                    st.write(
                        f"• {legs}"
                        f" — kurs {ins.num(alt['total_odds'])}, szansa {ins.pct(q['calibrated'])} "
                        f"(ostrożnie {ins.pct(q['conservative'])})"
                    )
        if coupon_id and not str(coupon_id).startswith("nie zapisano"):
            st.caption(
                f"Zapisano jako kupon wirtualny {str(coupon_id)[:10]}… — rozliczy się automatycznie."
            )
        elif coupon_id:
            st.caption(str(coupon_id))
        if payload.get("outcome"):
            label, tone = {"win": ("Trafiony", "ok"), "loss": ("Nietrafiony", "bad")}.get(
                payload["outcome"], (payload["outcome"], "neutral")
            )
            st.markdown(ui.pill(label, tone), unsafe_allow_html=True)


# ---------------------------------------------------------------- candidate pool


def _pool(days: list[str], rules) -> None:
    now = c.now()
    max_odds = float(rules.builder["max_odds_per_leg"])
    rows = []
    for day in days:
        _report, candidates = c.day_candidates(day)
        rows += ins.candidate_groups(candidates, now=now, max_odds=max_odds)
    rows = [r for r in rows if r["candidate"].event_status == "before_start" or r["group"] == "C"]
    if not rows:
        ui.empty_state("Brak typów z prognozą", "Dla wybranych dni modele nie mają prognoz.")
        return
    cols = st.columns(5)
    sports = sorted({r["sport"] for r in rows})
    leagues = sorted({r["league"] for r in rows})
    sport = cols[0].selectbox("Dyscyplina", ["wszystkie", *sports], key="pool_sport")
    league = cols[1].selectbox("Liga", ["wszystkie", *leagues], key="pool_league")
    day = cols[2].selectbox(
        "Data",
        ["wszystkie", *days],
        key="pool_day",
        format_func=lambda d: d if d == "wszystkie" else c.day_label(d),
    )
    markets = sorted({r["market_key"] for r in rows})
    market = cols[3].selectbox("Rodzaj rynku", ["wszystkie", *markets], key="pool_market")
    status = cols[4].selectbox(
        "Status modelu", ["wszystkie", "zwalidowany", "eksperymentalny"], key="pool_status"
    )
    minimum = (
        st.slider("Minimalna szansa (model)", 0, 100, 50, 5, key="pool_min", format="%d%%") / 100
    )
    chosen = [
        r
        for r in rows
        if (sport == "wszystkie" or r["sport"] == sport)
        and (league == "wszystkie" or r["league"] == league)
        and (day == "wszystkie" or r["day"] == day)
        and (market == "wszystkie" or r["market_key"] == market)
        and (status == "wszystkie" or r["model_status"] == status)
        and r["p"] >= minimum
    ]
    tabs = st.tabs(
        [
            f"{key}. {label} ({sum(r['group'] == key for r in chosen)})"
            for key, label in ins.GROUPS.items()
        ]
    )
    for tab, key in zip(tabs, ins.GROUPS, strict=True):
        with tab:
            subset = [r for r in chosen if r["group"] == key]
            if key == "B":
                st.caption(
                    "Eksperymentalne lub z kursem historycznym: można je rozważyć tylko jako "
                    "symulację — nie trafiają do zwalidowanych kuponów."
                )
            if not subset:
                st.caption("Brak typów w tej grupie przy wybranych filtrach.")
                continue
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Data": c.local_time(r["candidate"].start_at, "%d.%m %H:%M"),
                            "Wydarzenie": r["event"],
                            "Typ": r["pick"],
                            "Szansa": round(r["p"] * 100, 1),
                            "Ostrożnie": round(r["p_low"] * 100, 1)
                            if r["p_low"] is not None
                            else None,
                            "Kurs": float(r["odds"]),
                            "Wiarygodność": ins.RELIABILITY[r["reliability"]],
                            "Kurs STS": r["price_status"],
                            "Model": r["model_status"],
                            "Powód": r["reason"],
                            "Rynek STS": r["market"],
                        }
                        for r in subset[:200]
                    ]
                ),
                hide_index=True,
                width="stretch",
                height=min(38 * (len(subset[:200]) + 1) + 4, 460),
                column_config={
                    "Szansa": st.column_config.ProgressColumn(
                        format="%.1f%%", min_value=0, max_value=100
                    ),
                    "Ostrożnie": st.column_config.NumberColumn(format="%.1f%%"),
                    "Kurs": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            if len(subset) > 200:
                st.caption(f"Pokazano 200 z {len(subset)} — zawęź filtry.")


# ---------------------------------------------------------------- technical details


def _technical(run) -> None:
    rows = [
        {"Etap": s["stage"], "Status": s["status"], "Szczegóły": s["detail"]} for s in run.stages
    ]
    for day, one in run.runs.items():
        rows += [
            {"Etap": f"{s['stage']} ({day})", "Status": s["status"], "Szczegóły": s["detail"]}
            for s in one.stages[len(run.stages) :]
        ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for day, one in run.runs.items():
        st.markdown(f"**{c.day_label(day)}** — analiza `{one.analysis_id[:12]}…`")
        if one.coupons and one.coupons.excluded:
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Powód wykluczenia z puli": k, "Selekcje": v}
                        for k, v in one.coupons.excluded.items()
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        report = one.report
        st.caption(
            "Diagnostyka EV (opłacalność względem kursu; nie decyduje o kuponie): "
            f"{report.headline}; EV+ {report.counts.get('ev_positive', 0)}, "
            f"zwalidowane ekonomicznie {report.counts.get('validated', 0)}."
        )
    st.code(json.dumps(run.stages, ensure_ascii=False, indent=1, default=str)[:20000])
