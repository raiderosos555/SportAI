"""Kupon dnia: the coupon builder for today, tomorrow, 3 or 7 days — every day separately."""

import json
from collections import Counter

import pandas as pd
import streamlit as st

from sports_edge import insights as ins
from sports_edge.analysis.edge_engine import BOUND_LABELS, bound_of
from sports_edge.app import cart, coupon_view, ui
from sports_edge.app import common as c
from sports_edge.repository import offer_days
from sports_edge.services import (
    RANGES,
    analysis_days,
    coupon_rules,
    offer_status,
    proposal_payload,
    refresh_offers,
    run_analysis,
    saved_proposals,
)

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


HIGH_HIT = "high_hit"  # the WYSOKA TRAFNOŚĆ profile of probability_policy.toml


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
    rules = coupon_rules(c.paths(), settings)  # TASK 014: + optional profiles switched on
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
            "granica „ostrożnie” podwójnej szansy Elo: "
            f"{BOUND_LABELS[bound_of(settings.elo_dc_bound)]} · "
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
            name: proposal_payload(p, policy_version=rules.version, bound=settings.elo_dc_bound)
            if p
            else None
            for name, p in builder.proposals.items()
        }
        refusals = builder.refusals
        ids = run.coupon_ids
        alternatives = builder.alternatives
        candidates = run.candidates
        statuses = {
            name: {
                **result,
                "created_at": (db.get("coupons", result["coupon_id"]) or {}).get("created_at"),
            }
            for name, result in run.coupon_saves.items()
            if "coupon_id" in result
        }
    else:
        saved = saved_proposals(db, day)
        proposals = {name: saved.get(name) for name in rules.profiles}
        refusals, alternatives, builder = {}, {}, None
        ids = {name: v["coupon_id"] for name, v in saved.items()}
        statuses = {
            name: {
                "relation": "official",
                "official_created_at": v["created_at"],
                "created_at": v["created_at"],
                "newest_version": v.get("newest_version"),
            }
            for name, v in saved.items()
        }
        _report, candidates = c.day_candidates(day)
        if saved:
            st.caption(
                "Ostatnio zapisane propozycje dla tego dnia — kliknij „Analizuj”, aby odświeżyć."
            )
    # TASK 013 §5.3: the selection nearest to a WYSOKA TRAFNOŚĆ coupon (read only)
    high = rules.profiles.get(HIGH_HIT)
    closest = (
        ins.closest_text(candidates, high, max_odds=max_odds)
        if high is not None and proposals.get(HIGH_HIT) is None
        else None
    )
    if not any(proposals.values()):
        explanation = ins.explain_no_coupon(candidates, builder, offer, now=now, max_odds=max_odds)
        _no_coupon(explanation, day, run is not None, online)
        if closest and explanation is not None:
            st.caption(f"{high['label']} — {closest}")
        return
    by_odds = {x.odds_id: x for x in candidates}
    exposure = _exposure(proposals)
    _day_risk(proposals, exposure, db)
    for name in rules.profiles:  # one full-width card per profile: never squeezed
        coupon_view.card(
            name,
            proposals.get(name),
            db=db,
            day=day,
            by_odds=by_odds,
            refusal=refusals.get(name),
            coupon_id=ids.get(name),
            status=statuses.get(name),
            exposure=exposure,
            closest=closest if name == HIGH_HIT else None,
            alternatives=[
                proposal_payload(a, policy_version=rules.version, bound=settings.elo_dc_bound)
                for a in alternatives.get(name, ())
            ],
        )


def _exposure(proposals: dict) -> dict[str, list[tuple[str, str, tuple]]]:
    """{event id: [(profile, label, pick), ...]} of the day's coupons."""
    out: dict[str, list[tuple[str, str, tuple]]] = {}
    for name, payload in proposals.items():
        for leg in (payload or {}).get("legs", []):
            pick = (leg.get("market_key"), leg.get("line"), leg.get("selection"))
            out.setdefault(ins.match_of(leg), []).append((name, payload.get("label") or name, pick))
    return out


def risk_numbers(proposals: dict, rates: dict | None = None) -> list[str]:
    """TASK 011 §6: the chance that two or more coupons of the day lose, as a number.
    TASK 012 §5: several selections of one match from the score matrix, or — said plainly —
    the independence assumption."""
    found = ins.day_risk(proposals, rates)
    if found is None:
        return []
    points = [
        f"Szansa, że przegrają co najmniej dwa kupony dnia: "
        f"{ins.pct(found['p_two_or_more_lose'])} (na szansach ostrożnych; wspólna selekcja "
        f"liczona raz). Oczekiwana liczba przegranych kuponów: "
        f"{ins.num(found['expected_losing'], 2)} z {found['coupons']}.",
    ]
    if found["shared_selections"]:
        points.append(
            "Gdyby kupony nie miały wspólnych selekcji (były niezależne), ta szansa wynosiłaby "
            f"{ins.pct(found['independent_two_or_more_lose'])} — wspólne typy zmieniają ryzyko."
        )
    for info in found["joint"].values():
        if info["method"] == "matrix":
            points.append(
                f"{info['event']}: {info['selections']} różne typy tego meczu — łączna szansa "
                f"{ins.pct(info['joint'])} z macierzy wyników Dixon–Coles (przy założeniu "
                f"niezależności byłoby {ins.pct(info['product'])})."
            )
    basis = "Założenie: różne mecze są niezależne"
    if found["matrix_matches"]:
        basis += (
            "; metoda dla typów jednego meczu: macierz wyników Dixon–Coles ("
            + ins.plural(found["matrix_matches"], "mecz", "mecze", "meczów")
            + ")"
        )
    if found["independent_matches"]:
        reasons = sorted(
            {i["reason"] for i in found["joint"].values() if i["method"] == "independent"}
        )
        basis += (
            "; metoda dla typów jednego meczu: założenie niezależności ("
            + ins.plural(found["independent_matches"], "mecz", "mecze", "meczów")
            + ": "
            + ", ".join(reasons)
            + ") — to przybliżenie, bo są skorelowane"
        )
    if found["joint"]:
        sources = sorted({i.get("source", "fallback") for i in found["joint"].values()})
        basis += "; parametry meczu: " + ", ".join(ins.PARAMETER_SOURCES[k] for k in sources)
    if found["model_fallback"]:
        basis += f"; bez szansy ostrożnej (użyto szansy modelu): {found['model_fallback']}"
    points.append(basis + ". To opis ryzyka, nie rekomendacja stawek.")
    return points


def _day_risk(proposals: dict, exposure: dict, db=None) -> None:
    """Ryzyko dnia: matches shared by several coupons — one lost match loses them all."""
    made = [p for p in proposals.values() if p]
    if len(made) < 2:
        return
    names = {ins.match_of(leg): leg.get("event", "—") for p in made for leg in p["legs"]}
    shared = sorted(
        ((event, profiles) for event, profiles in exposure.items() if len(profiles) > 1),
        key=lambda item: -len(item[1]),
    )
    numbers = risk_numbers(proposals, ins.matrix_rates(db, proposals) if db is not None else None)
    if not shared:
        ui.explanation(
            "Ryzyko dnia",
            [
                *numbers,
                "Każdy mecz występuje tylko w jednym kuponie dnia — porażka jednego meczu "
                "przegrywa najwyżej jeden kupon.",
            ],
        )
        return
    points = [*numbers]
    for event, profiles in shared:
        labels = ", ".join(label for _p, label, _pick in profiles)
        losing = ins.plural(len(profiles), "kupon", "kupony", "kuponów")
        if len({pick for _p, _label, pick in profiles}) == 1:
            points.append(
                f"{names[event]}: ten sam typ w kuponach {labels} — jeśli nie wejdzie, "
                f"przegrywa {losing}"
            )
        else:
            points.append(
                f"{names[event]}: różne typy tego meczu w kuponach {labels} — wyniki "
                f"skorelowane ({losing})"
            )
    points.append(
        "Kupony z tym samym meczem są skorelowane. Opcja różnicowania (Ustawienia: „ta sama "
        "selekcja najwyżej w N kuponach”) jest domyślnie wyłączona."
    )
    ui.explanation("Ryzyko dnia", points, tone="warn")
    if same_leg_shared(exposure):  # TASK 014 §4.2: the default stays; the user decides
        c.page_link(
            "zrodla",
            "Ustaw limit „ta sama selekcja najwyżej w N kuponach dnia” w Ustawieniach",
            query_params={"widok": "Ustawienia"},
        )


def same_leg_shared(exposure: dict) -> bool:
    """The SAME selection (not only the same match) is in two or more coupons of the day."""
    return any(
        n >= 2
        for profiles in exposure.values()
        for n in Counter(pick for _p, _label, pick in profiles).values()
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


# ---------------------------------------------------------------- candidate pool


def _pool(days: list[str], rules) -> None:
    now = c.now()
    max_odds = float(rules.builder["max_odds_per_leg"])
    rows = []
    for day in days:
        _report, candidates = c.day_candidates(day)
        rows += [
            {**group, "offer_day": day}
            for group in ins.candidate_groups(candidates, now=now, max_odds=max_odds)
        ]
    rows = [r for r in rows if r["candidate"].event_status == "before_start" or r["group"] == "C"]
    cart.flash()
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
            table = st.dataframe(
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
                on_select="rerun",
                selection_mode="multi-row",
                key=f"pool_table_{key}",
            )
            picked = [subset[i] for i in table.selection.rows if i < len(subset)]
            st.button(
                f"{cart.ADD_LABEL} ({len(picked)})",
                key=f"pool_add_{key}",
                icon=":material/add_shopping_cart:",
                disabled=not picked,
                on_click=cart.add_many,
                args=([(r["candidate"], r["offer_day"]) for r in picked],),
                help="Zaznacz wiersze tabeli, aby dodać typy do Mojego kuponu",
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
