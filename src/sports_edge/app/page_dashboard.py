"""Dashboard: the day at a glance — offer, forecasts, best picks, coupons, data status."""

from collections import defaultdict

import pandas as pd
import streamlit as st

from sports_edge import insights as ins
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.performance import builder_coupons
from sports_edge.repository import imports, offer_days
from sports_edge.services import (
    MODEL_SCOPES,
    SCOPE_LABELS,
    active_versions,
    offer_status,
    probability_policy,
    saved_proposals,
)

PROFILE_ORDER = ("safe", "balanced", "aggressive")
OUTCOME = {"win": ("Trafiony", "ok"), "loss": ("Nietrafiony", "bad")}


def dashboard_page() -> None:
    c.setup_page()
    ui.app_header(
        "SPORTS EDGE AI",
        "Analiza sportowa i inteligentny generator kuponów STS",
        kicker="Przegląd dnia",
    )
    db = c.store()
    days = offer_days(db)
    if not days:
        ui.empty_state(
            "Brak oferty STS",
            "Zaimportuj PDF z ofertą STS (Wydarzenia i wyniki) albo włącz pobieranie oficjalnych "
            "plików w Źródłach i ustawieniach.",
        )
        return
    now, today = c.now(), str(c.today_local())
    day = today if today in days else next((d for d in days if d > today), days[-1])
    report, candidates = c.day_candidates(day)
    settings = c.settings()
    offer = offer_status(db, settings, day, now=now, data_kinds=c.data_kinds())
    max_odds = float(probability_policy(c.paths()).builder["max_odds_per_leg"])
    groups = ins.candidate_groups(candidates, now=now, max_odds=max_odds)
    proposals = saved_proposals(db, day)
    freshness = c.cached_freshness(db, c.data_version(db), c.minute())
    fresh = [f for f in freshness if f["fresh"]]
    newest = max((i["generated_at"] or "" for i in imports(db) if i["rows_total"]), default="")
    events = {x.event_id for x in candidates}
    forecast = {x.event_id for x in candidates if x.p_model is not None}
    label = "Dziś" if day == today else c.day_label(day)

    ui.metric_cards(
        [
            (f"Wydarzenia STS — {label}", str(len(events)), f"{offer['markets']} rynków w ofercie"),
            ("Z prognozą modelu", str(len(forecast)), _share(len(forecast), len(events))),
            (
                "Selekcje spełniające wymagania",
                str(sum(g["group"] == "A" for g in groups)),
                "zwalidowany model i potwierdzony kurs",
            ),
            (
                "Propozycje kuponów",
                f"{len(proposals)} / {len(PROFILE_ORDER)}",
                "zapisane dla tego dnia" if proposals else "uruchom analizę w Kuponie dnia",
            ),
            (
                "Dane sportowe",
                max((f["last_result"] or "" for f in fresh), default="—") or "—",
                f"aktualne ligi: {len(fresh)}/{len(freshness)}",
            ),
            (
                "Ostatnia oferta STS",
                c.local_time(newest, "%d.%m %H:%M") if newest else "—",
                _offer_hint(offer),
            ),
        ]
    )
    if not offer["fresh"]:
        ui.pills([(_offer_hint(offer), "warn")])

    ui.section("Najlepsze prognozy", "przyszłe mecze z najwyższą ostrożną szansą typu")
    picks = _best_picks(days, today, now, max_odds)
    if picks:
        ui.card_grid([_pick_card(p, now) for p in picks[:5]])
    else:
        ui.empty_state(
            "Brak wystarczająco wiarygodnych typów",
            "Żaden przyszły mecz z prognozą nie ma typu z ostrożną szansą co najmniej 55%.",
        )

    ui.section("Kupon dnia", c.day_label(day))
    if proposals:
        ui.card_grid(
            [_proposal_card(name, proposals[name]) for name in PROFILE_ORDER if name in proposals]
        )
    else:
        ui.empty_state(
            "Brak zapisanych propozycji na ten dzień",
            "Kreator uruchamia się przyciskiem „Analizuj mecze i generuj kupony”. "
            + ("" if offer["fresh"] else "Najpierw potrzebna jest aktualna oferta STS."),
        )
    c.page_link("kupon", "Przejdź do Kuponu dnia")

    left, right = st.columns([3, 2], gap="large")
    with left:
        ui.section("Najbliższe wydarzenia z prognozą")
        _upcoming(candidates, now)
    with right:
        ui.section("Ostatnie kupony")
        _recent_coupons()
    ui.section("Status danych i ograniczenia")
    _status(offer, freshness, db)


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%} wydarzeń".replace(".", ",") if whole else "—"


def _offer_hint(offer: dict) -> str:
    if offer["age_hours"] is None:
        return "brak dokumentu dla tego dnia"
    age = ins.num(offer["age_hours"], 1)
    if offer["fresh"]:
        return f"kursy potwierdzone ({age} h)"
    return f"kursy niepotwierdzone — dokument sprzed {age} h (limit {ins.num(offer['limit_hours'], 0)} h)"


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
                out.append(pick)
        if len(out) >= 5:
            break
    return sorted(out, key=lambda p: (p["candidate"].p_low, p["candidate"].p_model), reverse=True)


def _pick_card(pick: dict, now) -> str:
    x = pick["candidate"]
    badges = [(ui.RISK[pick["reliability"]][0], ui.RISK[pick["reliability"]][1])]
    if pick["eligible"]:
        badges.append(("Kwalifikuje się do kuponu", "ok"))
    elif pick["experimental"]:
        badges.append(("Model eksperymentalny", "warn"))
    if x.freshness != "fresh":
        badges.append(("Kurs niepotwierdzony", "warn"))
    return ui.match_card(
        teams=x.event,
        meta=f"{c.local_time(x.start_at, '%d.%m · %H:%M')} · {x.league}",
        pick=ins.candidate_pick(x),
        market=f"nr zakładu STS {x.offer_code or '—'}",
        probability=ins.pct(x.p_model),
        conservative=ins.pct(x.p_low),
        odds=ins.num(x.odds),
        badges=badges,
        note="; ".join(pick["reliability_reasons"][:2]),
    )


def _proposal_card(name: str, payload: dict) -> str:
    p = payload["probability"]
    status = (
        ("Zwalidowany predykcyjnie", "ok")
        if payload["level"] == "validated_predictive"
        else ("Symulacja — model eksperymentalny", "warn")
    )
    outcome = OUTCOME.get(payload.get("outcome") or "", ("Nierozliczony", "neutral"))
    body = (
        f"<b>{ui.esc(payload.get('label', name)).upper()}</b><br>"
        + ui.pill(status[0], status[1])
        + ui.pill(outcome[0], outcome[1])
        + ui.key_values(
            [
                ("Selekcje", str(len(payload["legs"]))),
                (
                    "Szansa (ostrożnie)",
                    f"{ins.pct(p['calibrated'])} ({ins.pct(p['conservative'])})",
                ),
                ("Kurs łączny", ins.num(payload["total_odds"])),
                (
                    "Wypłata / zysk",
                    f"{ins.money(payload['payout'])} / {ins.money(payload['net_profit'])}",
                ),
            ]
        )
    )
    return ui.card(body, tone="accent" if status[1] == "ok" else "warn")


def _upcoming(candidates, now) -> None:
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
        ui.empty_state(
            "Brak nadchodzących meczów z prognozą 1X2", "Sprawdź kolejne dni w Analizie meczów."
        )
        return
    frame = pd.DataFrame(sorted(table, key=lambda r: r["_start"])[:8]).drop(columns="_start")
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


def _recent_coupons() -> None:
    coupons = list(reversed(builder_coupons(c.store())))[:5]
    if not coupons:
        ui.empty_state("Brak zapisanych kuponów", "Historia pojawi się po pierwszej analizie.")
        return
    cards = []
    for coupon in coupons:
        outcome = OUTCOME.get(coupon["outcome"] or "", ("Nierozliczony", "neutral"))
        payload = coupon["proposal"]
        cards.append(
            ui.card(
                f"<b>{ui.esc(payload.get('label', coupon['profile']))}</b> · "
                f"{ui.esc(c.local_time(coupon['first_start_at'], '%d.%m'))}<br>"
                + ui.pill(outcome[0], outcome[1])
                + f"<span style='color:#94A3B8'>{len(payload['legs'])} sel. · kurs "
                f"{ui.esc(ins.num(coupon['total_odds']))} · szansa "
                f"{ui.esc(ins.pct(payload['probability']['calibrated']))}</span>"
            )
        )
    ui.card_grid(cards, wide=False)


def _status(offer: dict, freshness: list[dict], db) -> None:
    served = active_versions(db)
    scopes = [
        f"{SCOPE_LABELS[name]}: "
        + ("modele aktywne" if pair["elo"] or pair["goals"] else "brak modelu")
        for name, pair in served.items()
    ]
    stale = [f["name"] for f in freshness if not f["fresh"]]
    ui.card_grid(
        [
            ui.card(
                "<b>Dane</b>"
                + ui.bullets(
                    [
                        _offer_hint(offer).capitalize(),
                        f"Wyniki lig: aktualne {len(freshness) - len(stale)} z {len(freshness)}"
                        + (f" (nieaktualne: {', '.join(stale)})" if stale else ""),
                        *scopes,
                    ]
                )
            ),
            ui.card(
                "<b>Ograniczenia</b>"
                + ui.bullets(
                    [
                        f"Modele obejmują {sum(len(s) for s in MODEL_SCOPES.values())} lig "
                        "piłkarskich; inne sporty nie mają jeszcze modelu.",
                        "Prognozy nie uwzględniają składów, kontuzji ani motywacji.",
                        "Szansa trafienia to oszacowanie modelu, nie gwarancja wygranej.",
                        "Opłacalność (ROI) nie jest potwierdzona — mało rozliczonych kuponów.",
                    ]
                ),
                tone="warn",
            ),
        ]
    )
