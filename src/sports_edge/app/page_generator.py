"""ZBUDUJ KUPON NA KURS… and TASIEMKA: a coupon close to a target total odds.

The search maximises the conservative probability of the whole coupon within the requested
odds range and filters; the product of probabilities is an approximation (independent
matches). When the data cannot justify such a coupon, none is shown — thresholds are never
lowered to produce one.
"""

from decimal import Decimal

import streamlit as st

from sports_edge import insights as ins
from sports_edge.analysis.generator import Request, generate, preset
from sports_edge.app import cart, ui
from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.repository import offer_days
from sports_edge.services import RANGES, analysis_days, probability_policy

RELIABILITY = {
    "any": "dowolna",
    "moderate": "co najmniej umiarkowana",
    "high": "tylko wyższa",
}
RESULT = "generator_result"


def generator_page() -> None:
    _page(default="standard")


def tasiemka_page() -> None:
    _page(default="tasiemka")


def _page(default: str) -> None:
    c.setup_page()
    rules = probability_policy(c.paths())
    title = "Tasiemka" if default == "tasiemka" else "Kupon na kurs"
    ui.app_header(
        title,
        "Kupon jak najbliżej wybranego kursu z najwyższą ostrożną szansą — bez obniżania progów",
    )
    cart.flash()
    db = c.store()
    days = offer_days(db)
    if not days:
        ui.empty_state("Brak oferty STS", "Zaimportuj ofertę STS w zakładce Źródła i ustawienia.")
        return
    names = [*rules.profiles, "custom"]
    labels = {**{k: v["label"] for k, v in rules.profiles.items()}, "custom": "Własne ustawienia"}
    chosen = st.segmented_control(
        "Profil",
        names,
        default=default if default in names else names[0],
        format_func=lambda k: labels[k],
        key=f"gen_profile_{default}",
    )
    chosen = chosen or default
    base = preset(rules.profiles[chosen]) if chosen in rules.profiles else Request(Decimal("2.00"))
    top = st.columns(4)
    target = top[0].number_input(
        "Kurs docelowy",
        min_value=1.10,
        max_value=100.0,
        value=float(base.target_odds),
        step=0.05,
        format="%.3f",
        key=f"gen_t_{chosen}",
    )
    tolerance = top[1].slider(
        "Tolerancja (±%)", 2, 40, int(round(base.tolerance * 100)), key=f"gen_tol_{chosen}"
    )
    max_legs = top[2].number_input("Maks. selekcji", 1, 10, base.max_legs, key=f"gen_n_{chosen}")
    span = top[3].selectbox("Dni", list(RANGES), index=2, key=f"gen_days_{chosen}")
    pool_days = [d for d in analysis_days(span, c.today_local()) if d in days]
    candidates, day_of = [], {}
    for day in pool_days:
        for x in c.day_candidates(day)[1]:
            candidates.append(x)
            day_of[x.odds_id] = day
    with st.expander("Filtry i wymagania", expanded=chosen == "custom"):
        row = st.columns(3)
        sports = row[0].multiselect(
            "Dyscypliny", sorted({x.sport for x in candidates}), key=f"gen_sp_{chosen}"
        )
        leagues = row[1].multiselect(
            "Wyklucz ligi",
            sorted({x.league for x in candidates if x.league}),
            key=f"gen_l_{chosen}",
        )
        markets = row[2].multiselect(
            "Wyklucz rynki",
            sorted({x.market_key for x in candidates if x.p_model is not None}),
            key=f"gen_m_{chosen}",
        )
        row = st.columns(4)
        minimum = row[0].slider(
            "Min. ostrożna szansa selekcji",
            40,
            95,
            int(base.min_leg_probability * 100),
            key=f"gen_p_{chosen}",
            format="%d%%",
        )
        reliability = row[1].selectbox(
            "Wiarygodność", list(RELIABILITY), format_func=RELIABILITY.get, key=f"gen_r_{chosen}"
        )
        limit = row[2].slider(
            "Maks. niepewność (P − ostrożnie)", 1, 20, 8, key=f"gen_u_{chosen}", format="%d pp"
        )
        validated = row[3].toggle(
            "Tylko rynki zwalidowane", value=base.require_validation, key=f"gen_v_{chosen}"
        )
    request = Request(
        target_odds=Decimal(str(target)),
        tolerance=tolerance / 100,
        max_legs=int(max_legs),
        min_leg_probability=minimum / 100,
        min_reliability=reliability,
        max_uncertainty=limit / 100,
        sports=tuple(sports),
        exclude_leagues=tuple(leagues),
        exclude_markets=tuple(markets),
        require_validation=validated,
        min_coupon_probability=base.min_coupon_probability,
    )
    st.caption(
        f"Przedział kursu: {request.low}–{request.high} · dni: "
        + ", ".join(c.day_label(d) for d in pool_days)
    )
    if st.button(
        f"ZBUDUJ KUPON NA KURS {request.target_odds}", type="primary", icon=":material/tune:"
    ):
        sts = load_profiles(c.paths().config)["sts"]
        st.session_state[RESULT] = (
            request,
            generate(
                candidates,
                request,
                now=c.now(),
                cost=sts.cost,
                on_date=c.today_local(),
                stake=cart.stake(),
                max_odds_per_leg=float(rules.builder["max_odds_per_leg"]),
                pool_size=int(rules.builder.get("long_candidates", 40)),
            ),
            day_of,
        )
    stored = st.session_state.get(RESULT)
    if stored and stored[0] == request:
        _result(*stored[1:])


def _result(found: dict, day_of: dict) -> None:
    proposal = found["proposal"]
    if proposal is None:
        points = [found["refusal"]]
        points += [
            f"{why}: {n}" for why, n in sorted(found["excluded"].items(), key=lambda x: -x[1])[:6]
        ]
        ui.explanation("Brak kuponu dla tych warunków", points, tone="warn")
        return
    for i, option in enumerate([proposal, *found["alternatives"]]):
        title = "Najlepszy kupon" if i == 0 else f"Alternatywa {i}"
        validated = option.level == "validated_predictive"
        with st.container(border=True):
            st.markdown(f"**{title}** — {len(option.legs)} sel. · kurs {option.total_odds}")
            ui.pills(
                [
                    ("Zwalidowany predykcyjnie", "ok")
                    if validated
                    else ("SYMULACJA — model eksperymentalny", "warn")
                ]
            )
            ui.metric_cards(
                [
                    ("Szansa (model)", ins.pct(option.p_calibrated), "przybliżenie"),
                    ("Ostrożnie", ins.pct(option.p_conservative), "dolna granica"),
                    ("Wypłata", ins.money(option.payout), f"stawka {ins.money(option.stake)}"),
                    ("Zysk netto", ins.money(option.net_profit), "po podatku STS"),
                ]
            )
            st.markdown(
                ui.legs_html(
                    [
                        {
                            "event": leg.event,
                            "when": f"{c.local_time(leg.start_at, '%d.%m %H:%M')} · {leg.league}",
                            "pick": ins.candidate_pick(leg),
                            "odds": ins.num(leg.odds),
                            "detail": f"szansa {ins.pct(leg.p_model)} (ostrożnie "
                            f"{ins.pct(leg.p_low)}) · "
                            + (
                                "zwalidowany"
                                if leg.predictive_status == "validated_predictive"
                                else "eksperymentalny"
                            ),
                        }
                        for leg in option.legs
                    ]
                ),
                unsafe_allow_html=True,
            )
            st.caption(option.joint_basis)
            for warning in option.warnings:
                st.caption("⚠ " + warning)
            st.button(
                cart.ADD_LABEL,
                key=f"gen_cart_{i}",
                icon=":material/add_shopping_cart:",
                on_click=cart.add_many,
                args=([(leg, day_of.get(leg.odds_id, "")) for leg in option.legs],),
            )
