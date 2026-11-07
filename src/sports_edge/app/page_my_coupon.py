"""MÓJ KUPON: build your own STS coupon from the offer and have it evaluated.

Left: the STS offer of a day (filters, search, one event with its markets). Right: the
coupon — current prices, stake, payout after tax, the model's probability when it can be
computed, problems that block saving. Below: OCEŃ MÓJ KUPON AI (per selection, the whole
coupon, the weakest link), ZAPROPONUJ ALTERNATYWĘ (applied only by the user) and questions
answered from the data. No guarantees of winning: every number is an estimate.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

import streamlit as st

from sports_edge import advisor, automation, llm
from sports_edge import explainer as ex
from sports_edge import insights as ins
from sports_edge import my_coupon as mine
from sports_edge.app import cart, coupon_view, ui
from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.repository import offer_days
from sports_edge.services import probability_policy, save_my_coupon_result, saved_proposals

MAIN_MARKETS = (
    "1x2",
    "double_chance",
    "total",
    "btts",
    "home_to_score",
    "away_to_score",
    "team_total_home",
    "team_total_away",
    "draw_no_bet",
)
EVALUATION = "my_coupon_evaluation"
ALTERNATIVES = "my_coupon_alternatives"
ANSWERS = "my_coupon_answers"
SAVED = "my_coupon_saved"
LATE = "mc_late"  # a coupon already placed in STS, recorded after its matches started


def my_coupon_page() -> None:
    c.setup_page()
    ui.app_header(
        "Mój kupon",
        "Zbuduj własny kupon z oferty STS i oceń go modelami aplikacji — to analiza, nie gwarancja",
    )
    cart.flash()
    db = c.store()
    days = offer_days(db)
    # the offer and the coupon side by side on wide screens, one below the other otherwise
    with st.container(key="split-my-coupon"):
        left, right = st.columns([3, 2], gap="large")
    with left:
        _browser(days)
    with right:
        _coupon()
    if cart.legs():
        _evaluation()


# ---------------------------------------------------------------- offer browser


def _browser(days: list[str]) -> None:
    ui.section("Oferta STS", "dzień, filtry, wydarzenie i typ")
    if not days:
        ui.empty_state("Brak oferty STS", "Zaimportuj ofertę STS w zakładce Źródła i ustawienia.")
        return
    with st.container(key="row-mc-filters"):
        top = st.columns([2, 2, 2])
    with top[0]:
        day = c.offer_day_choice(days, key="mc_day", upcoming=True)
    _report, candidates = c.day_candidates(day)
    sports = sorted({x.sport for x in candidates})
    chosen_sports = top[1].multiselect("Sport", sports, key="mc_sport", placeholder="wszystkie")
    pool = [x for x in candidates if not chosen_sports or x.sport in chosen_sports]
    leagues = sorted({x.league for x in pool if x.league})
    chosen_leagues = top[2].multiselect("Liga", leagues, key="mc_league", placeholder="wszystkie")
    pool = [x for x in pool if not chosen_leagues or x.league in chosen_leagues]
    st.toggle(
        "Wpis po fakcie: kupon już postawiony w STS (pozwala dodać rozpoczęte mecze)",
        key=LATE,
        help="Do historii trafia jako kupon rzeczywisty z kursami sprzed rozpoczęcia meczów, "
        "oddzielnie od kuponów zapisanych przed meczem (wybór po wyniku to wiedza z przyszłości).",
    )
    late = bool(st.session_state.get(LATE))
    with st.container(key="row-mc-search"):
        row = st.columns([3, 1.4, 1.4])
    query = row[0].text_input("Szukaj drużyny lub zawodnika", key="mc_query").strip().lower()
    only_model = row[1].toggle("Tylko z prognozą", value=False, key="mc_only_model")
    show_started = row[2].toggle("Pokaż rozpoczęte", value=False, key="mc_started") or late
    events: dict[str, list] = defaultdict(list)
    for x in pool:
        events[x.event_id].append(x)
    shown = [
        eid
        for eid, rows in events.items()
        if (not query or query in rows[0].event.lower())
        and (show_started or rows[0].event_status == "before_start")
        and (not only_model or any(r.p_model is not None for r in rows))
    ]
    shown.sort(key=lambda eid: (events[eid][0].start_at or "", events[eid][0].event))
    if not shown:
        ui.empty_state(
            "Brak wydarzeń dla filtrów",
            "Zmień dzień, sport, ligę lub wyszukiwaną nazwę.",
        )
        return

    def label(eid: str) -> str:
        head = events[eid][0]
        mark = " · prognoza" if any(r.p_model is not None for r in events[eid]) else ""
        return f"{c.local_time(head.start_at, '%d.%m %H:%M')} · {head.event} ({head.league}){mark}"

    choice = st.selectbox(
        f"Wydarzenie ({len(shown)})", shown, format_func=label, key=f"mc_event_{day}"
    )
    event_panel(events[choice], day)


def event_panel(rows: list, day: str) -> None:
    """One event of the offer: status, freshness, forecast, every market with add buttons."""
    head = rows[0]
    has_model = any(r.p_model is not None for r in rows)
    ui.pills(
        [
            (c.EVENT_STATUS.get(head.event_status, head.event_status), "info"),
            (
                ("Kurs potwierdzony", "ok")
                if head.freshness == "fresh"
                else ("Kurs niepotwierdzony — odśwież ofertę", "warn")
            ),
            ("Prognoza modelu dostępna", "ok")
            if has_model
            else ("Brak prognozy modelu", "neutral"),
        ]
    )
    st.markdown(
        ui.key_values(
            [
                ("Uczestnicy", head.event),
                ("Liga", head.league or "—"),
                ("Start", c.local_time(head.start_at, "%d.%m.%Y %H:%M")),
                ("Dokument STS", c.local_time(head.document_at) if head.document_at else "—"),
                (
                    "Wiek kursu",
                    f"{ins.num(head.odds_age_hours, 1)} h"
                    if head.odds_age_hours is not None
                    else "—",
                ),
            ]
        ),
        unsafe_allow_html=True,
    )
    markets: dict[str, list] = defaultdict(list)
    for r in rows:
        markets[r.market_id].append(r)

    def order(market_id: str) -> tuple:
        first = markets[market_id][0]
        rank = MAIN_MARKETS.index(first.market_key) if first.market_key in MAIN_MARKETS else 99
        return rank, first.market_label

    for market_id in sorted(markets, key=order):
        group = markets[market_id]
        first = group[0]
        modelled = any(r.p_model is not None for r in group)
        title = first.market_label + (" · prognoza modelu" if modelled else "")
        with st.expander(title, expanded=first.market_key in MAIN_MARKETS[:3] and modelled):
            for r in group:
                if mine.modelled(r):
                    model = f"model {ins.pct(r.p_model)} · ostrożnie {ins.pct(r.p_low)}"
                elif r.p_model is not None and r.probability_ok:
                    model = f"model bez kalibracji: {ins.pct(r.p_model)} — poza kuponem"
                else:
                    model = "Brak zwalidowanego modelu"
                with st.container(key=f"row-sel-{r.selection_id}"):
                    text, action = st.columns([4, 1.4], vertical_alignment="center")
                    text.markdown(
                        f"<div><b>{ui.esc(ins.candidate_pick(r))}</b> "
                        f'<span class="se-num">@ <b>{ui.esc(ins.num(r.odds))}</b></span>'
                        f'<div class="se-muted" style="font-size:.82rem">{ui.esc(model)}</div>'
                        "</div>",
                        unsafe_allow_html=True,
                    )
                    with action:
                        if r.event_status == "before_start" or st.session_state.get(LATE):
                            cart.add_button(r, day=day, key=f"add_{r.selection_id}", compact=True)
        if head.event_status != "before_start" and not st.session_state.get(LATE):
            st.caption(
                "Mecz już się rozpoczął — dodasz go tylko jako wpis po fakcie (przełącznik nad "
                "listą wydarzeń), jeśli kupon był naprawdę postawiony w STS."
            )


# ---------------------------------------------------------------- the coupon


def _coupon() -> None:
    legs = cart.legs()
    ui.section("Twój kupon", cart.summary_line())
    if not legs:
        ui.empty_state(
            "Kupon jest pusty",
            "Dodaj typy z oferty obok albo przyciskiem DODAJ DO MOJEGO KUPONU na stronach "
            "Dashboard, Analiza meczów, Kupon dnia i Wydarzenia.",
        )
        _saved_note()
        return
    rows, _pool = cart.rows_for(legs)
    for i, (leg, row) in enumerate(zip(legs, rows, strict=True)):
        _leg_card(i, leg, row)
    value = st.number_input(
        "Stawka (zł)",
        min_value=1.0,
        max_value=100000.0,
        value=float(cart.stake()),
        step=1.0,
        key="mc_stake",
    )
    stake = Decimal(str(value)).quantize(Decimal("0.01"))
    if stake != cart.stake():
        cart.set_stake(stake)
    sts = load_profiles(c.paths().config)["sts"]
    late = bool(st.session_state.get(LATE))
    first = min((r.start_at for r in rows if r is not None and r.start_at), default=None)
    on_date = c.today_local()
    if late and first:
        on_date = datetime.fromisoformat(first).date()
    summary = mine.summary(legs, rows, stake=stake, cost=sts.cost, on_date=on_date, late=late)
    st.markdown(
        ui.profile_badge("my_coupon_after" if late else "my_coupon")
        + ui.stat_strip(
            [
                ("Kurs łączny", ins.num(summary["total_odds"], 2)),
                ("Szansa", ins.pct(summary["probability"])),
                ("Ostrożnie", ins.pct(summary["probability_low"])),
                ("Stawka", ins.money(stake)),
                ("Wypłata", ins.money(summary.get("payout"))),
                ("Zysk netto", ins.money(summary.get("net_profit"))),
            ],
            emphasis=(0, 1),
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        f"{ins.plural(len(legs), 'selekcja', 'selekcje', 'selekcji')} · wypłata po podatku STS · "
        f"próg opłacalności {ins.pct(summary.get('break_even'))} trafionych kuponów"
        + (f" · {summary['probability_note']}" if summary["probability_note"] else "")
    )
    for issue in summary["issues"]:
        if issue.code == "same_event" or issue.severity == "block":
            st.error(issue.message, icon=":material/block:")
        else:
            st.warning(issue.message, icon=":material/warning:")
    _shared_and_weakest(legs, rows, summary)
    with st.container(key="row-mc-actions"):
        buttons = st.columns(2)
    if buttons[0].button(
        "OCEŃ MÓJ KUPON AI", type="primary", icon=":material/psychology:", width="stretch"
    ):
        st.session_state[EVALUATION] = True
        st.session_state.pop(ALTERNATIVES, None)
    if buttons[1].button("ZAPROPONUJ ALTERNATYWĘ", icon=":material/alt_route:", width="stretch"):
        st.session_state[EVALUATION] = True
        st.session_state[ALTERNATIVES] = True
    real = late or st.checkbox(
        "Postawiłem ten kupon naprawdę w STS (wpis ręczny — oddzielony od wirtualnych)",
        key="mc_real",
    )
    if late:
        st.info(
            "Wpis po fakcie: kupon trafi do Historii jako rzeczywisty, z kursami sprzed "
            "rozpoczęcia meczów, i rozliczy się automatycznie. W statystykach jest oddzielony "
            "od kuponów zapisanych przed meczem."
        )
    again = (real or late) and st.checkbox(
        "Postawiłem identyczny kupon w STS jeszcze raz (zapisz drugi egzemplarz)",
        key="mc_copy",
        help="Bez tego zaznaczenia ponowne kliknięcie zwraca już zapisany kupon.",
    )
    with st.container(key="row-mc-save"):
        save = st.columns(2)
    if save[0].button(
        "Zapisz kupon postawiony w STS" if late else "Zapisz kupon",
        icon=":material/save:",
        disabled=summary["blocked"],
        width="stretch",
        help="Zapis wymaga świeżych kursów i wydarzeń przed startem"
        if summary["blocked"] and not late
        else None,
    ):
        try:
            result = save_my_coupon_result(
                c.store(),
                c.paths(),
                legs,
                rows,
                stake=stake,
                now=c.now(),
                real=real,
                late=late,
                confirm_copy=bool(again),
            )
            st.session_state[SAVED] = (result.coupon_id, result.relation)
            st.rerun()
        except ValueError as error:
            st.error(str(error))
    if save[1].button("Wyczyść kupon", icon=":material/delete_sweep:", width="stretch"):
        cart.clear()
        for key in (EVALUATION, ALTERNATIVES, ANSWERS):
            st.session_state.pop(key, None)
        st.rerun()
    _saved_note()


def _shared_and_weakest(legs: list[dict], rows: list, summary: dict) -> None:
    """TASK 014 §4.3: a leg that is also in an official coupon of the day, the chance of a
    long coupon said plainly and the weakest leg (lowest cautious chance)."""
    db = c.store()
    official = {day: saved_proposals(db, day) for day in {leg.get("day") for leg in legs} if day}
    for text in mine.shared_with_official(legs, official):
        st.warning(text, icon=":material/link:")
    note = mine.chance_note(summary)
    if note:
        st.info(note + ".", icon=":material/casino:")
    weak = mine.weakest_leg(rows)
    if weak is not None and len(legs) > 1:
        st.caption(
            f"Najsłabsza noga: {legs[weak]['event']} — {legs[weak]['label']} "
            f"(ostrożnie {ins.pct(rows[weak].p_low)})."
        )


def _leg_card(i: int, leg: dict, row) -> None:
    with st.container(border=True):
        top = st.columns([6, 1], vertical_alignment="top")
        top[0].markdown(f"**{i + 1}. {leg['event']}**")
        top[0].caption(
            f"{leg.get('market', '')} · {c.local_time(leg.get('start_at'), '%d.%m %H:%M')}"
        )
        if top[1].button(
            "", key=f"mc_rm_{leg['selection_id']}", icon=":material/close:", help="Usuń z kuponu"
        ):
            cart.remove(leg["selection_id"])
            st.rerun()
        if row is None:
            st.markdown(f"{leg['label']} — **brak w bieżącej ofercie**")
            return
        changed = Decimal(leg["added_odds"]) != row.odds
        price = f"**{row.odds}**" + (f" (było {leg['added_odds']})" if changed else "")
        model = (
            f"model {ins.pct(row.p_model)}, ostrożnie {ins.pct(row.p_low)}"
            if mine.modelled(row)
            else "brak zwalidowanego modelu"
        )
        st.markdown(f"{ins.candidate_pick(row)} @ {price} · {model}")
        badges = [
            ("Kurs potwierdzony", "ok")
            if row.freshness == "fresh"
            else ("Kurs niepotwierdzony", "warn")
        ]
        if mine.modelled(row):
            badges.append(
                ("zwalidowany", "ok")
                if row.predictive_status == "validated_predictive"
                else ("eksperymentalny", "warn")
            )
        ui.pills(badges)
        note = c.calibration_note(c.candidate_leg(row)) if mine.modelled(row) else None
        if note:  # TASK 011 §2.3: the market and range are flagged prospectively
            st.caption("⚠ " + note)


def _saved_note() -> None:
    saved = st.session_state.get(SAVED)
    if saved:
        coupon_id, relation = saved if isinstance(saved, tuple) else (saved, "user")
        if relation == "duplicate":
            st.info(f"Ten kupon jest już zapisany ({coupon_id[:10]}…) — nie zapisano kopii.")
        else:
            st.success(
                f"Kupon zapisany ({coupon_id[:10]}…). Znajdziesz go w Historii i skuteczności."
            )
        c.page_link("historia", "Otwórz historię kuponów")


# ---------------------------------------------------------------- AI evaluation


def _evaluation() -> None:
    if not st.session_state.get(EVALUATION):
        return
    legs = cart.legs()
    rows, pool = cart.rows_for(legs)
    stake = cart.stake()
    sts = load_profiles(c.paths().config)["sts"]
    report = advisor.evaluate(
        legs,
        rows,
        stake=stake,
        cost=sts.cost,
        on_date=c.today_local(),
        now=c.now(),
        store=c.store(),
    )
    ui.section("Ocena kuponu", "modele aplikacji (Elo, Dixon–Coles) i dane — nie gwarancja")
    tone = "accent" if report["summary"]["probability"] is not None else "warn"
    weak = report["weakest"]
    st.markdown(
        ui.card(
            f"<b>{ui.esc(report['verdict'])}</b>"
            + (
                '<div class="se-muted" style="margin-top:.3rem">Najsłabsze ogniwo: '
                f"{ui.esc(report['legs'][weak]['event'])} — "
                f"{ui.esc(report['legs'][weak]['pick'])}</div>"
                if weak is not None
                else ""
            ),
            tone=tone,
        ),
        unsafe_allow_html=True,
    )
    tabs = st.tabs(
        [
            "Ocena AI",
            "Ryzyko",
            "Składy",
            "Alternatywy",
            "Symulacja budżetu",
            "Historia zmian",
            "Zapytaj",
        ]
    )
    with tabs[0]:
        _legs_table(report)
        _whole(report)
    with tabs[1]:
        _risks(report)
    with tabs[2]:
        _lineups(report)
        _lineup_check(legs)
    with tabs[3]:
        if st.session_state.get(ALTERNATIVES):
            _alternatives(legs, rows, pool, stake, sts)
        elif st.button("Zaproponuj alternatywę", icon=":material/alt_route:", key="mc_alt_tab"):
            st.session_state[ALTERNATIVES] = True
            st.rerun()
    with tabs[4]:
        _budget(report, stake)
    with tabs[5]:
        _changes(legs, rows)
    with tabs[6]:
        _questions(report, legs, rows, stake, sts)


def _legs_table(report: dict) -> None:
    body = []
    for i, r in enumerate(report["legs"]):
        mark = " · najsłabsze ogniwo" if i == report["weakest"] else ""
        body.append(
            [
                f"{i + 1}. {r['event']} — {r['pick']}{mark}",
                ins.num(r["odds"]),
                ins.pct(r.get("p_model")),
                ins.pct(r.get("p_low")),
                r["status"],
                ui.RISK.get(r["reliability"], ui.RISK["insufficient"])[0],
            ]
        )
    st.markdown(
        ui.table(
            ["Selekcja", "Kurs", "Szansa", "Ostrożnie", "Status", "Wiarygodność"],
            body,
            numeric=(1, 2, 3),
        ),
        unsafe_allow_html=True,
    )
    for r in report["legs"]:
        if r.get("reason"):
            st.caption(f"{r['event']}: {r['reason']}")


def _whole(report: dict) -> None:
    s = report["summary"]
    ui.explanation(
        "Cały kupon",
        [
            f"Selekcje: {s['legs']}, kurs łączny {ins.num(s['total_odds'], 2)}",
            f"Wypłata {ins.money(s.get('payout'))}, zysk netto {ins.money(s.get('net_profit'))} "
            "(po podatku STS)",
            f"Szansa: {ins.pct(s['probability'])}, ostrożnie {ins.pct(s['probability_low'])}"
            if s["probability"] is not None
            else f"Szansa: nie można wiarygodnie oszacować — {s['probability_note']}",
            f"Próg opłacalności: {ins.pct(s.get('break_even'))} trafionych kuponów",
            f"EV netto: {ins.num(s['ev'], 3)} na 1 zł stawki (diagnostyka, nie cel)"
            if s.get("ev") is not None
            else "EV netto: nieobliczalne bez wiarygodnej szansy",
        ],
    )


def _risks(report: dict) -> None:
    if report["uncertainties"]:
        ui.explanation("Największe niepewności", report["uncertainties"], tone="warn")
    items = [
        (f"{i + 1}. {r['event']}: {risk}", "warning")
        for i, r in enumerate(report["legs"])
        for risk in r["risks"]
    ]
    if items:
        st.markdown(ui.card(ui.factors(items)), unsafe_allow_html=True)
    else:
        st.caption("Brak dodatkowych ryzyk w danych aplikacji.")


def _lineups(report: dict) -> None:
    body = []
    for i, r in enumerate(report["legs"]):
        lineup = r.get("lineups") or {}
        body.append(
            [
                f"{i + 1}. {r['event']}",
                lineup.get("lineup_status", "brak danych"),
                "; ".join(lineup.get("changes", [])) or "bez zmian",
                ", ".join(lineup.get("absences", [])) or lineup.get("absence_source", "—"),
            ]
        )
    st.markdown(ui.table(["Mecz", "Skład", "Zmiany", "Absencje"], body), unsafe_allow_html=True)
    st.caption("Model uwzględnia skład: nie — składy i absencje są ostrzeżeniem.")


def _budget(report: dict, stake) -> None:
    s = report["summary"]
    if s["probability"] is None or s.get("net_profit") is None:
        st.caption("Symulacja wymaga wiarygodnej szansy całego kuponu.")
        return
    payload = {
        "stake": str(stake),
        "net_profit": str(s["net_profit"]),
        "probability": {
            "calibrated": s["probability"],
            "conservative": s["probability_low"]
            if s["probability_low"] is not None
            else s["probability"],
        },
    }
    coupon_view.simulation(payload)


def _changes(legs, rows) -> None:
    body = []
    for leg, row in zip(legs, rows, strict=True):
        body.append(
            [
                leg["event"],
                c.local_time(leg.get("added_at"), "%d.%m %H:%M"),
                ins.num(Decimal(leg["added_odds"])),
                ins.num(row.odds) if row is not None else "brak w ofercie",
            ]
        )
    st.markdown(
        ui.table(["Mecz", "Dodano", "Kurs przy dodaniu", "Kurs teraz"], body, numeric=(1, 2, 3)),
        unsafe_allow_html=True,
    )
    st.caption("Zapisane kupony i ich rozliczenia znajdziesz w Historii i skuteczności.")


def _lineup_check(legs) -> None:
    """The matches of this (not yet saved) coupon go to the worker for a lineup check."""
    ids = sorted({leg["event_id"] for leg in legs if leg.get("event_id")})
    if st.button(
        "Sprawdź składy meczów tego kuponu teraz",
        icon=":material/groups:",
        disabled=c.demo_mode() or not ids,
        help="PitchAPI: do 2 zapytań na mecz. Prognozy nie zmieniają się od składów.",
    ):
        st.success(
            automation.request(
                c.paths(), c.store(), "check_events", now=c.now(), payload={"event_ids": ids}
            )
        )


def _alternatives(legs, rows, pool, stake, sts) -> None:
    ui.section("Alternatywy", "propozycje — nic nie zmienia się bez Twojego kliknięcia")
    found = advisor.alternatives(
        legs, rows, pool, stake=stake, cost=sts.cost, on_date=c.today_local(), now=c.now()
    )
    if not found:
        ui.empty_state(
            "Brak sensownych alternatyw",
            "Na kuponie brakuje selekcji z wiarygodnym oszacowaniem, z których dałoby się "
            "zbudować inny wariant.",
        )
        return
    for i, option in enumerate(found):
        s = option["summary"]
        with st.container(border=True):
            st.markdown(f"**{option['title']}** — {option['why']}")
            st.caption(
                f"Kurs {ins.num(s['total_odds'], 2)} · wypłata {ins.money(s.get('payout'))} · "
                + (
                    f"szansa {ins.pct(s['probability'])} (ostrożnie {ins.pct(s['probability_low'])})"
                    if s["probability"] is not None
                    else f"szansa nieobliczalna ({s['probability_note']})"
                )
            )
            if st.button("Zastosuj ten wariant", key=f"mc_alt_{i}", icon=":material/done_all:"):
                cart.set_legs(option["legs"])
                st.session_state.pop(ALTERNATIVES, None)
                st.rerun()


def _questions(report, legs, rows, stake, sts) -> None:
    ui.section("Zapytaj o kupon", "odpowiedzi z danych aplikacji (objaśniacz szablonowy)")
    asked = None
    for row, group in enumerate((ex.EXAMPLES, ex.LINEUP_EXAMPLES)):
        examples = st.columns(len(group))
        for i, (col, example) in enumerate(zip(examples, group, strict=True)):
            if col.button(example, key=f"mc_q_{row}_{i}", width="stretch"):
                asked = example
    typed = st.text_input("Twoje pytanie", key="mc_question")
    if st.button("Zapytaj", icon=":material/help:") and typed.strip():
        asked = typed.strip()
    if asked:
        automatic = None
        days = sorted({leg["day"] for leg in legs})
        if days:
            saved = saved_proposals(c.store(), days[0])
            automatic = saved.get("standard") or next(iter(saved.values()), None)
            if automatic is not None:
                automatic = {
                    "label": automatic.get("label"),
                    "p_conservative": (automatic.get("probability") or {}).get("conservative"),
                    "total_odds": automatic.get("total_odds"),
                }
        found = ex.answer(
            asked,
            report,
            cart=legs,
            rows=rows,
            stake=stake,
            cost=sts.cost,
            on_date=c.today_local(),
            profiles=probability_policy(c.paths()).profiles,
            automatic=automatic,
        )
        st.session_state.setdefault(ANSWERS, []).insert(0, (asked, found))
    usable, why_not = llm.available(c.settings())
    answers = st.session_state.get(ANSWERS, [])
    for n, (question, found) in enumerate(answers[:5]):
        with st.chat_message("user"):
            st.write(question)
        with st.chat_message("assistant"):
            st.write(found["answer"])
            st.caption(f"Źródło: {found['source']} (dane aplikacji)")
            if (
                n == 0
                and usable
                and st.button(
                    "Rozwiń odpowiedź — lokalny model AI (bezpłatnie)",
                    key="mc_llm",
                    icon=":material/smart_toy:",
                )
            ):
                with st.spinner("Lokalny model pisze odpowiedź…"):
                    local = llm.ask(
                        question, report, store=c.store(), settings=c.settings(), now=c.now()
                    )
                if local["ok"]:  # only an answer that passed the checks is ever shown
                    st.write(local["answer"])
                    st.caption(f"Źródło: {local['source']} · {ins.num(local['seconds'], 1)} s")
                else:
                    st.warning(local["reason"] + " Obowiązuje odpowiedź szablonowa powyżej.")
    if not usable:
        st.caption(f"Lokalny model AI (opcjonalny): {why_not}")
