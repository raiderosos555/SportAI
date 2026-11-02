"""SYMULATOR BUDŻETU and SYMULATOR HISTORYCZNY.

Simulations show RISK — the spread of possible budgets — never a promise. The budget
simulator keeps a fixed stake or a share of the current budget (no stake raised after a
loss) and pays STS taxes; a probability without predictive validation turns the result
into a conditional, experimental simulation. The historical simulator replays a past day
with only the prices and forecasts known at the decision time. Both are stored apart from
virtual and real coupons.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

import altair as alt
import pandas as pd
import streamlit as st

from sports_edge import insights as ins
from sports_edge import my_coupon as mine
from sports_edge import simulation as sim
from sports_edge.app import cart, ui
from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.repository import offer_days
from sports_edge.services import probability_policy, saved_proposals

INFO = ui.TOKENS["info"]
PRIMARY = ui.TOKENS["primary"]
MUTED = ui.TOKENS["muted"]
RESPONSIBLE = (
    "Symulacja pokazuje rozrzut możliwych wyników przy założonej szansie — nie jest prognozą "
    "zysku ani zachętą do gry. Nie zwiększaj stawki po przegranej, ustal limit straty, "
    "pamiętaj o podatkach STS. Gra u nielegalnych operatorów i gra na kredyt są ryzykowne."
)


def budget_page() -> None:
    c.setup_page()
    ui.app_header(
        "Symulator budżetu", "Monte Carlo: co może się stać z budżetem przy serii kuponów"
    )
    st.caption(RESPONSIBLE)
    source = _source()
    if source is None:
        return
    top = st.columns(4)
    budget = top[0].number_input("Budżet (zł)", 10.0, 1_000_000.0, 500.0, 10.0)
    mode = top[1].segmented_control(
        "Stawka",
        ["fixed", "percent"],
        default="fixed",
        format_func={"fixed": "stała kwota", "percent": "% bieżącego budżetu"}.get,
    )
    mode = mode or "fixed"
    stake = top[2].number_input(
        "Stawka (zł)" if mode == "fixed" else "Stawka (% budżetu)",
        0.5,
        100_000.0,
        10.0 if mode == "fixed" else 2.0,
        0.5,
    )
    coupons = top[3].number_input("Liczba kuponów", 1, 1000, 50)
    more = st.columns(5)
    exposure = more[0].number_input(
        "Limit stawki jednego kuponu (zł, 0 = brak)", 0.0, 100_000.0, 0.0
    )
    loss = more[1].number_input("Limit straty (zł, 0 = brak)", 0.0, 1_000_000.0, 0.0)
    shared = more[2].slider("Wspólne selekcje kuponów", 0, 60, 0, format="%d%%")
    runs = more[3].select_slider("Przebiegi", [1000, 2000, 5000, 10000, 20000], value=5000)
    seed = more[4].number_input("Ziarno losowania", 0, 1_000_000, 7)
    if st.button("URUCHOM SYMULACJĘ", type="primary", icon=":material/play_arrow:"):
        cfg = sim.BudgetConfig(
            budget=float(budget),
            stake_mode=mode,
            stake=float(stake),
            coupons=int(coupons),
            odds=float(source["odds"]),
            probability=float(source["p"]),
            probability_low=source["p_low"],
            validated=bool(source["validated"]),
            source=source["label"],
            exposure_limit=float(exposure) or None,
            loss_limit=float(loss) or None,
            shared_exposure=shared / 100,
            runs=int(runs),
            seed=int(seed),
        )
        try:
            st.session_state["budget_result"] = sim.simulate_budget(cfg)
        except ValueError as error:
            st.error(str(error))
    result = st.session_state.get("budget_result")
    if result is not None:
        _budget_result(result)


def _source() -> dict | None:
    """Where the coupon probability comes from — never from the odds alone."""
    options = {"cart": "Mój kupon", "profile": "Ostatnia propozycja profilu", "manual": "Ręcznie"}
    chosen = st.radio(
        "Źródło szansy kuponu", list(options), format_func=options.get, horizontal=True
    )
    if chosen == "cart":
        legs = cart.legs()
        if not legs:
            ui.empty_state("Mój kupon jest pusty", "Dodaj typy albo wybierz inne źródło szansy.")
            return None
        rows, _pool = cart.rows_for(legs)
        sts = load_profiles(c.paths().config)["sts"]
        s = mine.summary(legs, rows, stake=cart.stake(), cost=sts.cost, on_date=c.today_local())
        if s["probability"] is None:
            ui.empty_state("Nie można użyć Mojego kuponu", s["probability_note"] or "brak szansy")
            return None
        found = {
            "p": s["probability"],
            "p_low": s["probability_low"],
            "odds": s["total_odds"],
            "validated": s["validated"],
            "label": "Mój kupon",
        }
    elif chosen == "profile":
        rules = probability_policy(c.paths())
        name = st.selectbox(
            "Profil", list(rules.profiles), format_func=lambda k: rules.profiles[k]["label"]
        )
        days = [d for d in offer_days(c.store()) if d <= str(c.today_local() + timedelta(days=7))]
        payload = next(
            (
                saved_proposals(c.store(), d).get(name)
                for d in reversed(days)
                if saved_proposals(c.store(), d).get(name)
            ),
            None,
        )
        if payload is None:
            ui.empty_state(
                "Brak zapisanej propozycji tego profilu",
                "Uruchom analizę w Kuponie dnia albo wybierz inne źródło szansy.",
            )
            return None
        p = payload["probability"]
        found = {
            "p": p["calibrated"],
            "p_low": p["conservative"],
            "odds": payload["total_odds"],
            "validated": payload["level"] == "validated_predictive",
            "label": f"profil {payload.get('label', name)}",
        }
    else:
        cols = st.columns(3)
        p = cols[0].slider("Szansa trafienia kuponu", 1, 99, 50, format="%d%%") / 100
        spread = cols[1].slider("Niepewność (punkty w dół)", 0, 20, 5, format="%d pp") / 100
        odds = cols[2].number_input("Kurs łączny", 1.01, 1000.0, 2.0, 0.05)
        found = {
            "p": p,
            "p_low": max(0.001, p - spread) if spread else None,
            "odds": Decimal(str(odds)),
            "validated": False,
            "label": "ręczne założenie użytkownika",
        }
    ui.pills(
        [
            (f"Szansa {ins.pct(found['p'])}", "info"),
            (f"Kurs {ins.num(found['odds'])}", "neutral"),
            ("Zwalidowany model", "ok")
            if found["validated"]
            else ("Szansa niezwalidowana", "warn"),
        ]
    )
    return found


def _budget_result(result: sim.BudgetResult) -> None:
    cfg = result.config
    tone = "ok" if cfg.validated else "warn"
    ui.pills([(f"Symulacja {result.status}", tone), (f"Źródło: {cfg.source}", "neutral")])
    ui.metric_cards(
        [
            ("Mediana budżetu", ins.money(result.final["p50"]), f"start {ins.money(cfg.budget)}"),
            (
                "5–95 percentyl",
                f"{ins.money(result.final['p5'])} – {ins.money(result.final['p95'])}",
                "90% przebiegów",
            ),
            ("Szansa straty", ins.pct(result.p_loss), "budżet końcowy < start"),
            (
                "Limit straty",
                ins.pct(result.p_loss_limit) if cfg.loss_limit else "—",
                "przebiegi zatrzymane",
            ),
            (
                "Seria porażek",
                f"{result.streaks['median']} (p95 {result.streaks['p95']})",
                "najdłuższa w przebiegu",
            ),
            (
                "Obsunięcie",
                f"{ins.money(result.drawdown['median'])} (p95 {ins.money(result.drawdown['p95'])})",
                "od szczytu",
            ),
        ]
    )
    left, right = st.columns(2, gap="large")
    with left:
        ui.section("Budżet po kolejnych kuponach", "mediana i zakresy przebiegów")
        st.altair_chart(_fan(result), width="stretch")
    with right:
        ui.section("Rozkład budżetu końcowego", f"{cfg.runs} przebiegów")
        st.altair_chart(_histogram(result), width="stretch")
    with st.expander("Tabela percentyli (widok tabelaryczny)"):
        frame = pd.DataFrame({"Kupon": range(cfg.coupons + 1), **result.bands})
        st.dataframe(frame, hide_index=True, width="stretch", height=260)
    ui.explanation(
        "Założenia i ograniczenia", result.notes, tone="warn" if not cfg.validated else ""
    )
    st.caption(
        f"Oczekiwany wynik jednego kuponu przy tej szansie: {ins.money(result.expected_profit_per_coupon)} "
        "(diagnostyka; ujemny = strata w długim okresie)."
    )
    if st.button("Zapisz przebieg symulacji", icon=":material/save:"):
        run_id = sim.save_run(
            c.store(),
            "budget",
            result.as_dict()["config"],
            {k: v for k, v in result.as_dict().items() if k not in {"config", "sample_paths"}},
            now=c.now(),
            status=result.status,
            policy_version=probability_policy(c.paths()).version,
        )
        st.success(f"Zapisano jako symulację {run_id[:10]}… (oddzielnie od kuponów).")


def _fan(result: sim.BudgetResult) -> alt.Chart:
    bands = result.bands
    frame = pd.DataFrame({"kupon": range(len(bands["p50"])), **bands})
    base = alt.Chart(frame).encode(x=alt.X("kupon:Q", title="Kupon", axis=alt.Axis(grid=False)))
    wide = base.mark_area(color=INFO, opacity=0.18).encode(
        y=alt.Y("p5:Q", title="Budżet (zł)"),
        y2="p95:Q",
        tooltip=[
            alt.Tooltip("kupon:Q", title="Kupon"),
            alt.Tooltip("p5:Q", title="5%", format=".2f"),
            alt.Tooltip("p95:Q", title="95%", format=".2f"),
        ],
    )
    core = base.mark_area(color=INFO, opacity=0.38).encode(y="p25:Q", y2="p75:Q")
    median = base.mark_line(color=PRIMARY, strokeWidth=2).encode(
        y="p50:Q",
        tooltip=[
            alt.Tooltip("kupon:Q", title="Kupon"),
            alt.Tooltip("p50:Q", title="Mediana", format=".2f"),
        ],
    )
    start = (
        alt.Chart(pd.DataFrame({"y": [result.config.budget]}))
        .mark_rule(color=MUTED, strokeDash=[4, 4])
        .encode(y="y:Q")
    )
    legend = pd.DataFrame(
        {"seria": ["mediana", "50% przebiegów", "90% przebiegów"], "x": [0, 0, 0], "y": [0, 0, 0]}
    )
    key = (
        alt.Chart(legend)
        .mark_point(opacity=0)
        .encode(
            color=alt.Color(
                "seria:N",
                scale=alt.Scale(domain=list(legend["seria"]), range=[PRIMARY, INFO, INFO]),
                legend=alt.Legend(title=None, orient="top"),
            )
        )
    )
    return (wide + core + median + start + key).properties(height=300)


def _histogram(result: sim.BudgetResult) -> alt.Chart:
    edges, counts = result.histogram["edges"], result.histogram["counts"]
    frame = pd.DataFrame({"od": edges[:-1], "do": edges[1:], "przebiegi": counts})
    bars = (
        alt.Chart(frame)
        .mark_bar(color=INFO, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("od:Q", bin="binned", title="Budżet końcowy (zł)"),
            x2="do:Q",
            y=alt.Y("przebiegi:Q", title="Przebiegi"),
            tooltip=[
                alt.Tooltip("od:Q", title="Od", format=".2f"),
                alt.Tooltip("do:Q", title="Do", format=".2f"),
                alt.Tooltip("przebiegi:Q", title="Przebiegi"),
            ],
        )
    )
    start = (
        alt.Chart(pd.DataFrame({"x": [result.config.budget]}))
        .mark_rule(color=MUTED, strokeDash=[4, 4])
        .encode(x="x:Q")
    )
    return (bars + start).properties(height=300)


# ---------------------------------------------------------------- historical simulator


def historical_page() -> None:
    c.setup_page()
    ui.app_header(
        "Symulator historyczny",
        "Jak polityka kuponów zachowałaby się w przeszłości — tylko z danymi znanymi w chwili decyzji",
    )
    st.caption(
        "Odtworzenie dnia: oferta, kursy i prognozy znane przed wybraną godziną decyzji, kupony "
        "zbudowane według bieżącej polityki, rozliczenie zapisanymi wynikami. Bez danych z "
        "przyszłości i bez fikcyjnych kursów. Wynik to symulacja historyczna — oddzielona od "
        "kuponów wirtualnych i rzeczywistych."
    )
    days = [d for d in offer_days(c.store()) if d < str(c.today_local())]
    if not days:
        ui.empty_state("Brak przeszłych dni oferty", "Symulacja potrzebuje oferty z minionych dni.")
        return
    top = st.columns(3)
    day = top[0].selectbox("Dzień oferty", list(reversed(days)), format_func=c.day_label)
    moment = top[1].time_input("Godzina decyzji (czas polski)", time(10, 0), step=1800)
    stake = top[2].number_input("Stawka (zł)", 1.0, 10_000.0, 10.0, 1.0)
    decision = datetime.combine(datetime.fromisoformat(day).date(), moment, tzinfo=c.WARSAW)
    if st.button("ODTWÓRZ DZIEŃ", type="primary", icon=":material/history:"):
        with st.spinner("Odtwarzanie dnia z danych as-of…"):
            st.session_state["replay"] = sim.replay_day(
                c.store(),
                c.paths(),
                c.settings(),
                day,
                decision_at=decision,
                stake=Decimal(str(stake)).quantize(Decimal("0.01")),
            )
    found = st.session_state.get("replay")
    if not found or found["day"] != day:
        return
    st.caption(
        f"Decyzja: {c.local_time(found['decision_at'])} · wybory z {found['candidates']} selekcji "
        f"(z prognozą: {found['with_forecast']}) · polityka {found['policy_version']}"
    )
    cards = []
    for entry in found["profiles"].values():
        coupon = entry["coupon"]
        if coupon is None:
            cards.append(
                ui.card(
                    f"<b>{ui.esc(entry['label'])}</b><br>{ui.esc(entry['refusal'] or 'brak kuponu')}"
                )
            )
            continue
        tone = {"trafiony": "accent", "nietrafiony": "bad"}.get(coupon["status"], "warn")
        lines = [
            f"{leg['event']}: {leg['pick']} @ {leg['odds']} — {leg['outcome']}"
            for leg in coupon["legs"]
        ]
        cards.append(
            ui.card(
                f"<b>{ui.esc(entry['label'])}</b> · {ui.esc(coupon['status'])}"
                + ui.key_values(
                    [
                        ("Kurs", ins.num(coupon["total_odds"])),
                        (
                            "Szansa (ostrożnie)",
                            f"{ins.pct(coupon['p_calibrated'])} ({ins.pct(coupon['p_conservative'])})",
                        ),
                        (
                            "Wynik netto",
                            ins.money(coupon["net_profit"]) if coupon["net_profit"] else "—",
                        ),
                    ]
                )
                + ui.bullets(lines),
                tone=tone,
            )
        )
    ui.card_grid(cards)
    if st.button("Zapisz symulację historyczną", icon=":material/save:"):
        run_id = sim.save_run(
            c.store(),
            "historical",
            {"day": day, "decision_at": found["decision_at"], "stake": str(stake)},
            found,
            now=c.now(),
            status="historyczna (as-of)",
            policy_version=found["policy_version"],
        )
        st.success(f"Zapisano jako symulację {run_id[:10]}…")
