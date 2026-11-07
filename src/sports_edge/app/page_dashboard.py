"""Dashboard (TASK 009): the centre of the application, in the order of what matters.

1. state of the STS data, sports data, automation and alerts (one status strip);
2. current coupon proposals (identical proposals of several profiles are shown once — the
   database keeps every saved proposal);
3. the most important matches with forecasts;
4. coupons waiting for settlement;
5. alerts that need attention.

Technical numbers (offer counts, pre-match monitoring, data limitations) are in a closed
panel at the bottom.
"""

from collections import defaultdict

import pandas as pd
import streamlit as st

from sports_edge import alerts, automation
from sports_edge import insights as ins
from sports_edge.app import cart, prematch_ui, ui
from sports_edge.app import common as c
from sports_edge.performance import builder_coupons, recent_line
from sports_edge.repository import imports, offer_days
from sports_edge.services import (
    MODEL_SCOPES,
    SCOPE_LABELS,
    active_versions,
    offer_status,
    probability_policy,
    saved_proposals,
)

PROFILE_ORDER = ("high_hit", "standard", "higher_payout", "tasiemka")
OUTCOME = {"win": ("Trafiony", "ok"), "loss": ("Nietrafiony", "bad")}
AUTOMATION_TONE = {"aktywna": "ok", "wstrzymana": "warn", "zatrzymana": "neutral"}


def dashboard_page() -> None:
    c.setup_page()
    ui.app_header(
        "SPORTS EDGE AI",
        "Analiza sportowa i inteligentny generator kuponów STS",
        kicker="Przegląd dnia",
    )
    db = c.store()
    days = offer_days(db)
    now, today = c.now(), str(c.today_local())
    settings = c.settings()
    freshness = c.cached_freshness(db, c.data_version(db), c.minute())
    if not days:
        _status_strip(db, None, freshness, now)
        ui.empty_state(
            "Brak oferty STS",
            "Zaimportuj PDF z ofertą STS (Wydarzenia i wyniki) albo włącz pobieranie oficjalnych "
            "plików w Źródłach i ustawieniach.",
        )
        return
    day = today if today in days else next((d for d in days if d > today), days[-1])
    offer = offer_status(db, settings, day, now=now, data_kinds=c.data_kinds())
    max_odds = float(probability_policy(c.paths()).builder["max_odds_per_leg"])
    cart.flash()
    # 1 -------------------------------------------------------------------------------
    _status_strip(db, offer, freshness, now)
    # 2 -------------------------------------------------------------------------------
    label = "dziś" if day == today else c.day_label(day)
    ui.section("Propozycje kuponów", f"{label} · identyczne propozycje pokazane raz")
    _proposals(db, day, offer)
    # 3 -------------------------------------------------------------------------------
    picks = _best_picks(days, today, now, max_odds)[:6]
    ui.section("Najważniejsze mecze z prognozą", "najwyższa ostrożna szansa typu")
    _picks(picks, now)
    # 4 and 5 ---------------------------------------------------------------------------
    ui.section("Kupony oczekujące na rozliczenie")
    st.caption(recent_line(db, now=now))  # TASK 014 §2.3: expected against actual hits
    _pending(db)
    ui.section("Alerty wymagające uwagi", "składy i kupony przed meczem")
    prematch_ui.attention_block(db, now)
    # level 3 ---------------------------------------------------------------------------
    with ui.details("Szczegóły dnia: liczby oferty, kontrola przed meczem, dane i ograniczenia"):
        _day_numbers(db, day, offer, freshness, max_odds)
        prematch_ui.overview_metrics(db, now)
        _limitations(db, freshness)


# ---------------------------------------------------------------- 1. status


def _status_strip(db, offer: dict | None, freshness: list[dict], now) -> None:
    newest = max((i["generated_at"] or "" for i in imports(db) if i["rows_total"]), default="")
    if offer is None:
        sts = ("Oferta STS", "brak dokumentu", "bad", "zaimportuj ofertę")
    else:
        sts = (
            "Oferta STS",
            "kursy potwierdzone" if offer["fresh"] else "kursy do odświeżenia",
            "ok" if offer["fresh"] else "warn",
            f"dokument {c.local_time(newest, '%d.%m %H:%M')}" if newest else _offer_hint(offer),
        )
    fresh = [f for f in freshness if f["fresh"]]
    last = max((f["last_result"] or "" for f in fresh), default="") or "—"
    sport = (
        "Dane sportowe",
        f"wyniki do {last}",
        "ok" if freshness and len(fresh) == len(freshness) else "warn",
        f"aktualne ligi {len(fresh)}/{len(freshness)}",
    )
    if c.demo_mode():
        robot = ("Automatyzacja", "niedostępna w DEMO", "neutral", "dane syntetyczne")
    else:
        found = automation.status(c.paths(), db, now=now)
        nexts = sorted((found.get("next_runs") or {}).values())
        robot = (
            "Automatyzacja",
            found["label"],
            AUTOMATION_TONE.get(found["label"], "bad"),
            f"następne zadanie {c.local_time(nexts[0], '%d.%m %H:%M')}"
            if nexts and found["alive"]
            else "steruj w Automatyzacji AI",
        )
    unread = alerts.unread_count(db)
    alert = (
        "Alerty",
        f"{unread} nowych" if unread else "brak nowych",
        "warn" if unread else "ok",
        "kontrola składów i kuponów",
    )
    ui.status_strip([sts, sport, robot, alert])


def _offer_hint(offer: dict) -> str:
    if offer["age_hours"] is None:
        return "brak dokumentu dla tego dnia"
    age = ins.num(offer["age_hours"], 1)
    if offer["fresh"]:
        return f"kursy potwierdzone ({age} h)"
    return f"dokument sprzed {age} h (limit {ins.num(offer['limit_hours'], 0)} h)"


# ---------------------------------------------------------------- 2. proposals


def _proposals(db, day: str, offer: dict) -> None:
    proposals = saved_proposals(db, day)
    if not proposals:
        ui.empty_state(
            "Brak zapisanych propozycji na ten dzień",
            "Kreator uruchamia się w Kuponie dnia przyciskiem „Analizuj mecze i generuj kupony”. "
            + ("" if offer["fresh"] else "Najpierw potrzebna jest aktualna oferta STS."),
        )
        c.page_link("kupon", "Przejdź do Kuponu dnia")
        return
    groups: dict[tuple, list[str]] = defaultdict(list)
    # TASK 014: an optional profile switched on by the user comes after the four
    for name in [*PROFILE_ORDER, *(n for n in proposals if n not in PROFILE_ORDER)]:
        payload = proposals.get(name)
        if payload:
            # TASK 010: identity = the selections (stable between STS documents), not odds
            groups[
                tuple(sorted(leg.get("selection_id") or leg["odds_id"] for leg in payload["legs"]))
            ].append(name)
    rows = []
    for names in groups.values():
        payload = proposals[names[0]]
        p = payload["probability"]
        validated = payload["level"] == "validated_predictive"
        outcome = OUTCOME.get(payload.get("outcome") or "", ("Nierozliczony", "neutral"))
        badges = [ui.profile_badge(n) for n in names]
        badges.append(
            ui.pill("Zwalidowany", "ok") if validated else ui.pill("Model eksperymentalny", "warn")
        )
        badges.append(ui.pill(*outcome))
        legs = len(payload["legs"])
        rows.append(
            ui.list_row(
                title=", ".join(leg["event"] for leg in payload["legs"][:2])
                + (f" + {legs - 2}" if legs > 2 else ""),
                meta=f"{ins.plural(legs, 'selekcja', 'selekcje', 'selekcji')} · stawka "
                f"{ins.money(payload.get('stake', '10.00'))} · wypłata {ins.money(payload['payout'])}",
                value=f"kurs {ins.num(payload['total_odds'])}",
                value_hint=f"szansa {ins.pct(p['calibrated'])} · ostrożnie "
                f"{ins.pct(p['conservative'])}",
                badges=badges,
                tone="ok" if validated else "warn",
            )
        )
    ui.rows(rows)
    c.page_link("kupon", "Szczegóły w Kuponie dnia")


# ---------------------------------------------------------------- 3. matches


def _best_picks(days: list[str], today: str, now, max_odds: float) -> list[dict]:
    """Best reliable pick per future event, from today on (up to three offer days)."""
    out = []
    for day in [d for d in days if d >= today][:3]:
        _report, candidates = c.day_candidates(day)
        by_event = defaultdict(list)
        for x in candidates:
            by_event[x.event_id].append(x)
        for rows in by_event.values():
            pick = ins.best_pick(rows, now=now, max_odds=max_odds)
            if pick:
                pick["rows"] = rows
                pick["day"] = day
                out.append(pick)
        if len(out) >= 6:
            break
    return sorted(out, key=lambda p: (p["candidate"].p_low, p["candidate"].p_model), reverse=True)


def _picks(picks: list[dict], now) -> None:
    if not picks:
        ui.empty_state(
            "Brak wystarczająco wiarygodnych typów",
            "Żaden przyszły mecz z prognozą nie ma typu z ostrożną szansą co najmniej 55%.",
        )
        return
    for i, pick in enumerate(picks):
        x = pick["candidate"]
        badges = [ui.risk_pill(pick["reliability"])]
        if pick["eligible"]:
            badges.append(ui.pill("Kwalifikuje się do kuponu", "ok"))
        elif pick["experimental"]:
            badges.append(ui.pill("Model eksperymentalny", "warn"))
        if x.freshness != "fresh":
            badges.append(ui.pill("Kurs niepotwierdzony", "warn"))
        with st.container(key=f"row-pick-{i}"):
            left, right = st.columns([3.5, 1], vertical_alignment="center")
            left.markdown(
                ui.list_row(
                    title=x.event,
                    meta=f"{c.local_time(x.start_at, '%d.%m · %H:%M')} · {x.league} · "
                    f"{ins.candidate_pick(x)}",
                    value=ins.pct(x.p_model),
                    value_hint=f"ostrożnie {ins.pct(x.p_low)} · kurs {ins.num(x.odds)}",
                    badges=badges,
                    tone="ok" if pick["eligible"] else "",
                ),
                unsafe_allow_html=True,
            )
            with right:
                cart.add_button(x, day=pick["day"], key=f"dash_add_{i}")
    c.page_link("analiza", "Wszystkie mecze w Analizie meczów")


# ---------------------------------------------------------------- 4. pending coupons


def _pending(db) -> None:
    waiting = [x for x in builder_coupons(db) if x["outcome"] is None]
    if not waiting:
        ui.empty_state("Brak kuponów do rozliczenia", "Wszystkie zapisane kupony są rozliczone.")
        return
    rows = []
    for coupon in waiting[:6]:
        payload = coupon["proposal"]
        legs = len(payload.get("legs", []))
        events = [leg["event"] for leg in payload.get("legs", [])]
        rows.append(
            ui.list_row(
                title=", ".join(events[:2]) + (f" + {legs - 2}" if legs > 2 else "")
                or (payload.get("label") or coupon["profile"]),
                meta=f"pierwszy mecz {c.local_time(coupon['first_start_at'], '%d.%m %H:%M')} · "
                f"{ins.plural(legs, 'selekcja', 'selekcje', 'selekcji')} · "
                + ("kupon postawiony w STS" if coupon["mode"] == "real_manual" else "wirtualny"),
                value=f"kurs {ins.num(coupon['total_odds'])}",
                value_hint=f"stawka {ins.money(coupon['stake'])}",
                badges=[ui.profile_badge(coupon["profile"], payload.get("label"))],
            )
        )
    ui.rows(rows)
    if len(waiting) > 6:
        st.caption(f"…i {len(waiting) - 6} kolejnych.")
    c.page_link("historia", "Pełna historia kuponów")


# ---------------------------------------------------------------- level 3


def _day_numbers(db, day, offer, freshness, max_odds) -> None:
    report, candidates = c.day_candidates(day)
    groups = ins.candidate_groups(candidates, now=c.now(), max_odds=max_odds)
    events = {x.event_id for x in candidates}
    forecast = {x.event_id for x in candidates if x.p_model is not None}
    proposals = saved_proposals(db, day)
    ui.metric_cards(
        [
            ("Wydarzenia STS", str(len(events)), f"{offer['markets']} rynków w ofercie"),
            ("Z prognozą modelu", str(len(forecast)), _share(len(forecast), len(events))),
            (
                "Selekcje spełniające wymagania",
                str(sum(g["group"] == "A" for g in groups)),
                "zwalidowany model i potwierdzony kurs",
            ),
            ("Propozycje kuponów", f"{len(proposals)} / {len(PROFILE_ORDER)}", "zapisane"),
        ]
    )
    _upcoming(candidates)


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%} wydarzeń".replace(".", ",") if whole else "—"


def _upcoming(candidates) -> None:
    rows = defaultdict(dict)
    for x in candidates:
        if x.event_status != "before_start" or x.market_key != "1x2" or x.p_model is None:
            continue
        rows[x.event_id][x.selection] = x
    table = []
    for picks in rows.values():
        head = next(iter(picks.values()))
        favourite = max(picks.values(), key=lambda x: x.p_model)
        table.append(
            {
                "Godzina": c.local_time(head.start_at, "%d.%m %H:%M"),
                "Mecz": head.event,
                "Liga": head.league,
                "Faworyt modelu": ins.selection_label(favourite.selection),
                "Szansa": round(favourite.p_model * 100, 1),
                "_start": head.start_at,
            }
        )
    if not table:
        return
    frame = pd.DataFrame(sorted(table, key=lambda r: r["_start"])[:10]).drop(columns="_start")
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Szansa": st.column_config.ProgressColumn(
                "Szansa faworyta", format="%.1f%%", min_value=0, max_value=100
            )
        },
    )


def _limitations(db, freshness: list[dict]) -> None:
    served = active_versions(db)
    stale = [f["name"] for f in freshness if not f["fresh"]]
    points = [
        f"Wyniki lig: aktualne {len(freshness) - len(stale)} z {len(freshness)}"
        + (f" (nieaktualne: {', '.join(stale)})" if stale else ""),
        *[
            f"{SCOPE_LABELS[name]}: "
            + ("modele aktywne" if pair["elo"] or pair["goals"] else "brak modelu")
            for name, pair in served.items()
        ],
        f"Modele obejmują {sum(len(s) for s in MODEL_SCOPES.values())} lig piłkarskich; inne "
        "sporty nie mają jeszcze modelu.",
        "Prognozy nie uwzględniają składów, kontuzji ani motywacji.",
        "Szansa trafienia to oszacowanie modelu, nie gwarancja wygranej.",
    ]
    ui.explanation("Dane i ograniczenia", points, tone="warn")
