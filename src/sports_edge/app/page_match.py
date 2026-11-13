"""Analiza meczów: one event in plain language — pre-match picture or post-match review.

Pre-match mode shows only information available when the forecast was made; post-match mode
adds the result, the outcome of every modelled selection and what the model did not know.
Information that no model uses (form, statistics, lineups) is labelled as such.
"""

from collections import defaultdict
from datetime import datetime

import pandas as pd
import streamlit as st

from sports_edge import insights as ins
from sports_edge import prematch
from sports_edge.app import advanced_ui, cart, match_view, prematch_ui, ui
from sports_edge.app import common as c
from sports_edge.app import international_ui as iui
from sports_edge.repository import offer_days
from sports_edge.services import (
    event_context,
    event_features,
    match_review,
    probability_policy,
)
from sports_edge.sports_intel import latest_lineups

MODEL_TEXT = {
    "elo": "Elo — siła drużyn obliczona z wyników meczów",
    "dixon": "Dixon–Coles — model liczby goli obu drużyn",
}
STATUS_TEXT = {"validated_predictive": "zwalidowany", "experimental": "eksperymentalny"}


@st.cache_data(ttl=600, show_spinner=False, max_entries=64)
def _features(_db, version: tuple, event_id: str, decision: datetime):
    event = _db.get("events", event_id)
    return event_features(_db, c.paths(), event, decision_time=decision, save=False)


def match_page() -> None:
    c.setup_page()
    ui.app_header("Analiza meczów", "Prognoza, ryzyko i uzasadnienie dla pojedynczego wydarzenia")
    db = c.store()
    days = offer_days(db)
    if not days:
        ui.empty_state("Brak oferty STS", "Zaimportuj ofertę STS, aby analizować mecze.")
        return
    top = st.columns([2, 1, 1])
    with top[0]:
        # TASK 019 §3.1: the nearest day with a match still before its start
        day = c.offer_day_choice(days, key="match_day", upcoming=True)
    only_model = top[1].toggle("Tylko z prognozą", value=True, key="match_only_model")
    show_done = top[2].toggle("Pokaż rozpoczęte", value=False, key="match_done")
    kind = iui.category_filter("match_category")
    # cups and national teams have no served model: the filter shows them anyway (TASK 009)
    needs_model = only_model and kind in ("all", "domestic")
    _report, candidates = c.day_candidates(day)
    cart.flash()
    events = defaultdict(list)
    for x in candidates:
        events[x.event_id].append(x)
    # a started match keeps the forecast made before kick-off, even by an older model version
    marks = ",".join("?" for _ in events)
    forecast_any = (
        {
            r["event_id"]
            for r in db.rows(
                f"SELECT DISTINCT event_id FROM predictions WHERE event_id IN ({marks})",
                tuple(events),
            )
        }
        if events
        else set()
    )
    shown = [
        eid
        for eid, rows in events.items()
        if iui.matches(kind, rows[0].league, rows[0].sport)
        and (show_done or rows[0].event_status == "before_start")
        and (
            not needs_model
            or any(r.p_model is not None for r in rows)
            or (rows[0].event_status != "before_start" and eid in forecast_any)
        )
    ]
    if not shown:
        ui.empty_state(
            "Brak meczów spełniających filtry",
            "Wyłącz filtr „Tylko z prognozą” albo włącz „Pokaż rozpoczęte”.",
        )
        return
    shown.sort(key=lambda eid: events[eid][0].start_at or "")
    choice = st.selectbox(
        "Wydarzenie",
        shown,
        format_func=lambda eid: (
            f"{c.local_time(events[eid][0].start_at, '%H:%M')} · "
            f"{events[eid][0].event} ({events[eid][0].league})"
        ),
        key="match_event",
    )
    rows = events[choice]
    head = rows[0]
    now = c.now()
    context = event_context(db, c.paths(), event_id=choice, now=now)
    review = match_review(db, event_id=choice, candidates=rows, now=now)
    started = review["started"]
    start = datetime.fromisoformat(head.start_at) if head.start_at else now
    decision = min(now, start).replace(second=0, microsecond=0)
    features = _features(db, c.data_version(db), choice, decision)
    teams = (features or {}).get("features", {}).get("teams", {})
    max_odds = float(probability_policy(c.paths()).builder["max_odds_per_leg"])
    info = prematch.summary(db, choice)
    international = iui.category(head.league, head.sport) in ("european", "national")
    view = iui.schedule_of(db, choice, now) if international else None
    gaps = next((r.data_gaps for r in rows if getattr(r, "data_gaps", "")), "")
    # level 1: who plays, the probabilities, the pick
    match_view.header(head, context, rows, started, review, info, view, gaps)
    if international:
        iui.event_block(db, choice, now)
    if started:
        _post_match(review, head)
    ui.section(
        "Prawdopodobieństwa",
        "prognoza sprzed rozpoczęcia"
        if started
        else "każdy rynek osobno, z modelem i jego statusem",
    )
    match_view.probabilities(head, rows, context, review)
    ui.section("Najbardziej prawdopodobny obsługiwany typ")
    pick = _pick(rows, now, max_odds, started, day)
    # level 2: comparison, risk factors, short reasoning
    ui.section("Porównanie drużyn", "dane znane przed prognozą")
    match_view.comparison(head, teams, view)
    ui.section("Analiza meczu")
    match_view.analysis(rows, teams, info, pick, started=started)
    # level 3: everything else on demand
    with ui.details(
        "Pełne uzasadnienie: dane użyte i nieuwzględnione przez model, model rozszerzony",
        icon=":material/fact_check:",
    ):
        advanced_ui.why(db, rows, context, teams, review.get("forecast") if started else None)
    tabs = st.tabs(
        [
            "Składy i absencje",
            "Wszystkie rynki i kursy",
            "Ostatnie mecze",
            "Statystyki meczowe",
            "Modele i kalibracja",
            "Dane techniczne",
        ]
    )
    with tabs[0]:
        _lineups(db, context, choice, now, started)
    with tabs[1]:
        _markets(rows, day, started)
    with tabs[2]:
        _form(context, teams, started)
    with tabs[3]:
        _stats(teams, head)
    with tabs[4]:
        _models(rows, context)
    with tabs[5]:
        st.json(
            {
                "kontekst": {k: v for k, v in context.items() if k != "event"},
                "cechy": features,
                "selekcje": [x.explanation for x in rows[:12]],
            },
            expanded=False,
        )


# ---------------------------------------------------------------- header and modes


def _post_match(review: dict, head) -> None:
    ui.section("Prognoza a wynik")
    result = review.get("result")
    if not review.get("finished"):
        ui.empty_state(
            "Mecz rozpoczęty — brak potwierdzonego wyniku",
            "Wynik pojawi się po aktualizacji danych sportowych. Prognoza sprzed meczu nie zmienia się.",
        )
        return
    forecast = review.get("forecast")
    names = {"home": "gospodarze", "draw": "remis", "away": "goście"}
    points = [
        f"Wynik końcowy: {head.event} {result['home_score']}:{result['away_score']}"
        + (
            f" (do przerwy {result['ht_home']}:{result['ht_away']})"
            if result.get("ht_home") is not None
            else ""
        )
    ]
    if forecast:
        points.append(
            f"Prognoza z {c.local_time(forecast['created_at'], '%d.%m %H:%M')} wskazywała: "
            f"{names[review['favourite']]} ({ins.pct(forecast['probabilities'][review['favourite']])})"
            + (" — trafiona." if review["favourite_hit"] else " — nietrafiona.")
        )
    else:
        points.append("Przed meczem nie było prognozy modelu.")
    ui.explanation(
        "Rozliczenie prognozy", points, tone="accent" if review.get("favourite_hit") else "warn"
    )
    if review["selections"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Typ": ins.pick_label(
                            s["market_key"], s["line"], s["selection"], head.event, s["market"]
                        ),
                        "Szansa przed meczem": round(s["p"] * 100, 1),
                        "Kurs": float(s["odds"]) if s["odds"] is not None else None,
                        "Wynik": {"win": "wygrana", "loss": "przegrana"}.get(
                            s["outcome"], s["outcome"] or "do rozliczenia ręcznego"
                        ),
                    }
                    for s in review["selections"]
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Szansa przed meczem": st.column_config.ProgressColumn(
                    format="%.1f%%", min_value=0, max_value=100
                )
            },
        )
    ui.explanation("Czego model nie wiedział przed meczem", review["unknown_before"])


def _model_text(model: str) -> str:
    key = "elo" if model.startswith("elo") else "dixon" if "dixon" in model else ""
    return MODEL_TEXT.get(key, model or "brak")


def _pick(rows, now, max_odds, started, day) -> dict | None:
    pick = ins.best_pick(rows, now=now, max_odds=max_odds) if not started else None
    if pick is None:
        ui.empty_state(
            "Brak wystarczająco wiarygodnego typu dla tego meczu",
            "Żaden obsługiwany rynek nie ma ostrożnie oszacowanej szansy co najmniej 55% przy "
            "kursie, który po współczynniku 0,88 zwraca stawkę."
            if not started
            else "Mecz już się rozpoczął — nie jest kandydatem do kuponu.",
        )
        return None
    x = pick["candidate"]
    qualifies = (
        ("Kwalifikuje się do kuponu", "ok")
        if pick["eligible"]
        else ("Tylko symulacja (model eksperymentalny)", "warn")
        if pick["experimental"]
        else (f"Poza kuponem: {pick['why_not']}", "warn")
    )
    st.markdown(
        ui.match_card(
            teams=ins.candidate_pick(x),
            meta=f"{x.market_label} · kurs STS {ins.num(x.odds)} · nr zakładu {x.offer_code or '—'}",
            pick="Szansa trafienia",
            market="skalibrowana; obok wersja ostrożna",
            probability=ins.pct(x.p_model),
            conservative=ins.pct(x.p_low),
            odds=ins.num(x.odds),
            badges=[ui.RISK[pick["reliability"]], qualifies],
            note="; ".join(pick["reliability_reasons"]),
        ),
        unsafe_allow_html=True,
    )
    cart.add_button(x, day=day, key=f"match_add_{x.selection_id}")
    return pick


# ---------------------------------------------------------------- tabs


def _form(context, teams, started) -> None:
    if not context.get("teams"):
        st.caption("Brak niezależnych wyników dla tej ligi.")
        return
    cols = st.columns(2, gap="medium")
    for col, side in zip(cols, ("home", "away"), strict=True):
        team = context["teams"][side]
        with col:
            if not team["canonical"]:
                st.warning(
                    f"{team['sts']}: brak zweryfikowanego dopasowania nazwy — wyników nie zgadujemy."
                )
                continue
            st.markdown(
                f"**{team['sts']}** · {ins.plural(team['games'], 'mecz', 'mecze', 'meczów')} w danych"
            )
            for point in ins.form_points(teams.get(side)):
                st.caption(point)
            matches = [m for m in team["matches"] if started or m["known_before_forecast"]]
            if not matches:
                st.caption("Brak meczów znanych przed prognozą.")
                continue
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Data": m["date"],
                            "Mecz": m.get("match") or m["score"],
                            "Miejsce": m["venue"],
                            "Wynik drużyny": m.get("team_score", ""),
                            "W/R/P": m["result"],
                            "Przed prognozą": "tak" if m["known_before_forecast"] else "nie",
                        }
                        for m in matches
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
    st.caption(
        "W/R/P oraz „Wynik drużyny” są liczone z perspektywy danej drużyny (jej gole : gole "
        "rywala); kolumna „Mecz” pokazuje wynik w kolejności gospodarze – goście."
    )


def _stats(teams, head) -> None:
    home, away = teams.get("home") or {}, teams.get("away") or {}
    if not home and not away:
        st.caption("Brak danych o drużynach tego meczu.")
        return
    names = (home.get("team") or "Gospodarze", away.get("team") or "Goście")
    rows = []
    for label, key, field, digits in (
        ("Punkty na mecz (5)", "form5", "points_per_game", 2),
        ("Gole zdobyte (5)", "form5", "goals_for", 2),
        ("Gole stracone (5)", "form5", "goals_against", 2),
        ("Punkty na mecz (10)", "form10", "points_per_game", 2),
    ):
        a = (home.get(key) or {}).get(field)
        b = (away.get(key) or {}).get(field)
        n = ((home.get(key) or {}).get("matches"), (away.get(key) or {}).get("matches"))
        rows.append(
            (
                label,
                ins.num(a, digits),
                ins.num(b, digits),
                a,
                b,
                f"mecze: {n[0] or 0} / {n[1] or 0}",
            )
        )
    keys = sorted(set(home.get("stats5") or {}) | set(away.get("stats5") or {}))
    for key in keys:
        a = (home.get("stats5") or {}).get(key)
        b = (away.get("stats5") or {}).get(key)
        label = (a or b)["label"]
        rows.append(
            (
                f"{label} (średnio)",
                ins.num(a["for"]) if a else ins.NO_DATA,
                ins.num(b["for"]) if b else ins.NO_DATA,
                a["for"] if a else None,
                b["for"] if b else None,
                f"dane z {a['matches'] if a else 0} / {b['matches'] if b else 0} meczów",
            )
        )
    st.markdown(
        ui.card(
            f"<div style='display:flex;justify-content:space-between'><b>{ui.esc(names[0])}</b>"
            f"<b>{ui.esc(names[1])}</b></div>" + ui.comparison_rows(rows)
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        "Średnie z ostatnich meczów znanych przed chwilą prognozy. Przy 1–2 meczach średnia nie "
        "jest reprezentatywna. Statystyki meczowe (xG, strzały) są tylko informacją — modele "
        "ich nie używają."
    )


def _lineups(db, context, event_id, now, started) -> None:
    start_at = (context.get("event") or {}).get("start_at")
    if prematch_ui.lineups_panel(db, event_id, start_at=start_at, now=now, started=started):
        return
    # observations stored before the pre-match engine (TASK 007) are still shown
    teams = context.get("teams") or {}
    names = [teams[s]["canonical"] for s in ("home", "away")] if teams else []
    stored = None
    if names and all(names):
        stored = latest_lineups(
            db, home=names[0], away=names[1], day=context["event"]["start_at"][:10]
        )
    if stored:
        observed = datetime.fromisoformat(stored["observed_at"])
        start = datetime.fromisoformat(context["event"]["start_at"])
        cols = st.columns(2)
        for col, side in zip(cols, ("home", "away"), strict=True):
            part = stored["lineups"][side]
            with col:
                st.markdown(
                    f"**{part['team']}** · {part['formation'] or '—'} · "
                    + ("potwierdzony" if part["confirmed"] else "przewidywany")
                )
                st.write(", ".join(p["name"] or "?" for p in part["starters"]) or ins.NO_DATA)
        st.caption(
            f"Obserwacja z {c.local_time(stored['observed_at'])}"
            + (" — po rozpoczęciu meczu (informacja pomeczowa)" if observed >= start else "")
        )
    else:
        st.caption("Składy są publikowane zwykle około godziny przed meczem.")


def _markets(rows, day, started) -> None:
    ordered = sorted(rows, key=lambda x: (x.market_label, x.selection))
    table = st.dataframe(
        pd.DataFrame(
            [
                {
                    "Rynek": x.market_label,
                    "Typ": ins.selection_label(x.selection),
                    "Kurs": float(x.odds),
                    "Nr zakładu": x.offer_code or "",
                    "Szansa (model)": round(x.p_model * 100, 1) if x.p_model is not None else None,
                    "Ostrożnie": round(x.p_low * 100, 1) if x.p_low is not None else None,
                    "Rynek bez marży": round(x.p_market * 100, 1)
                    if x.p_market is not None
                    else None,
                    "Kurs STS": "potwierdzony" if x.freshness == "fresh" else "niepotwierdzony",
                }
                for x in ordered
            ]
        ),
        hide_index=True,
        width="stretch",
        height=420,
        column_config={
            "Szansa (model)": st.column_config.NumberColumn(format="%.1f%%"),
            "Ostrożnie": st.column_config.NumberColumn(format="%.1f%%"),
            "Rynek bez marży": st.column_config.NumberColumn(format="%.1f%%"),
            "Kurs": st.column_config.NumberColumn(format="%.2f"),
        },
        on_select="rerun",
        selection_mode="multi-row",
        key=f"match_markets_{rows[0].event_id}",
    )
    picked = [ordered[i] for i in table.selection.rows if i < len(ordered)]
    if not started:
        st.button(
            f"{cart.ADD_LABEL} ({len(picked)})",
            key=f"match_add_rows_{rows[0].event_id}",
            icon=":material/add_shopping_cart:",
            disabled=not picked,
            on_click=cart.add_many,
            args=([(x, day) for x in picked],),
            help="Zaznacz wiersze tabeli, aby dodać typy do Mojego kuponu",
        )
    st.caption("Brak wartości oznacza brak danych (model nie liczy tego rynku), a nie zero.")


def _models(rows, context) -> None:
    modelled = [x for x in rows if x.p_model is not None]
    if not modelled:
        st.caption("Brak prognoz modelu dla tego meczu.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Rynek": x.market_label,
                    "Typ": ins.selection_label(x.selection),
                    "Model": _model_text(x.model or ""),
                    "Walidacja predykcyjna": STATUS_TEXT.get(
                        x.predictive_status, x.predictive_status
                    ),
                    "Kalibracja (okno oceny)": x.calibration,
                }
                for x in modelled
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    prediction = context.get("prediction")
    if prediction:
        st.caption(
            f"Wersja modelu 1X2: {prediction['family']} {prediction['version']} · prognoza z "
            f"{c.local_time(prediction['created_at'])} · dane: "
            f"{(prediction.get('inputs') or {}).get('data', '')}"
        )
    st.caption(
        "Status „zwalidowany” dotyczy trafności i kalibracji na nowych meczach, nie przewagi "
        "nad kursami STS."
    )
