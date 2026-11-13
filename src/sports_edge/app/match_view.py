"""Analiza meczu (TASK 009): the essentials first — who plays, the probabilities of each market,
how the teams compare, what could change the picture and why the AI chose its pick.

Level 1: header and probabilities. Level 2: team comparison, risk factors, short reasoning.
Level 3 (closed panels): model versions, the full list of used and unused data, raw values.
Every value comes from stored data; a missing value is shown as "brak danych", never as 0.
The short reasoning is built from the application's facts only (no language model).
"""

from collections import defaultdict

import streamlit as st

from sports_edge import insights as ins
from sports_edge.app import common as c
from sports_edge.app import prematch_ui, ui

MODEL_NAME = {"elo": "Elo", "dixon": "Dixon–Coles"}
VALIDATED = "validated_predictive"


def model_name(model: str | None) -> str:
    model = model or ""
    if model.startswith("elo"):
        return MODEL_NAME["elo"]
    return MODEL_NAME["dixon"] if "dixon" in model else (model or "brak modelu")


def _tag(statuses: list[str]) -> tuple[str, str] | None:
    if not statuses:
        return None
    if all(s == VALIDATED for s in statuses):
        return ("zwalidowany", "ok")
    if any(s == VALIDATED for s in statuses):
        return ("częściowo zwalidowany", "info")
    return ("eksperymentalny", "warn")


def _date(value: str | None) -> str:
    from datetime import date

    return date.fromisoformat(value).strftime("%d.%m.%Y") if value else "brak danych"


def teams_of(head) -> tuple[str, str]:
    home, away = (head.event.split(" - ", 1) + [""])[:2]
    return home.strip(), away.strip()


# ---------------------------------------------------------------- level 1


def header(
    head,
    context: dict,
    rows,
    started: bool,
    review: dict,
    info: dict,
    view: dict | None = None,
    gaps: str = "",
) -> None:
    home, away = teams_of(head)
    status = c.EVENT_STATUS.get(head.event_status, head.event_status)
    badges = [ui.pill(status.capitalize(), c.EVENT_TONE.get(head.event_status, "neutral"))]
    badges.append(prematch_ui.lineup_pill(info))
    fresh = context.get("freshness") or {}
    national = bool(view) and view.get("category") == "national"
    if fresh.get("last_result"):
        badges.append(
            ui.pill(
                f"{'Wyniki rozgrywek' if national else 'Wyniki ligi'} do {fresh['last_result']}",
                "info" if fresh.get("fresh") else "warn",
            )
        )
    if national:  # TASK 019 §3.1: the team's own newest match next to the pool's
        last = [
            f"{view['teams'][s].get('name') or view['teams'][s]['team']} "
            + (_date(view["schedule"][s].get("last_match")))
            for s in ("home", "away")
        ]
        badges.append(ui.pill("Ostatni mecz drużyny: " + " · ".join(last), "info"))
    for text in [g for g in gaps.split("; ") if g]:  # TASK 019 §1.4
        badges.append(ui.pill(text, "warn", wrap=True))
    badges.append(
        ui.pill("Kurs STS potwierdzony", "ok")
        if head.freshness == "fresh"
        else ui.pill(
            "Kurs niepotwierdzony"
            + (f" ({ins.num(head.odds_age_hours, 1)} h)" if head.odds_age_hours else ""),
            "warn",
        )
    )
    badges.append(ui.pill("Po meczu" if started else "Przed meczem", "info"))
    meta = f"{head.league} · {c.local_time(head.start_at, '%d.%m.%Y · %H:%M')}"
    st.markdown(ui.match_header(home, away, meta, badges), unsafe_allow_html=True)
    prediction = (review.get("forecast") if started else None) or context.get("prediction") or {}
    with ui.details("Model i dane tego meczu"):
        inputs = prediction.get("inputs") or {}
        params = (prediction.get("validation") or {}).get("hyperparameters") or {}
        games = inputs.get("games") or []
        pairs = [
            (
                "Model 1X2",
                f"{model_name(prediction.get('family'))} · wersja {prediction.get('version', '—')}",
            ),
            ("Prognoza z", c.local_time(prediction.get("created_at"))),
            ("Mecze w danych", f"{games[0]} i {games[1]}" if len(games) == 2 else "—"),
            (
                "Parametry Elo",
                f"K = {params['k']}, przewaga boiska {params['home_advantage']}"
                if "k" in params
                else "—",
            ),
            ("Dane", inputs.get("data") or "—"),
            (
                "Wyniki rozgrywek do" if national else "Wyniki ligi do",
                fresh.get("last_result") or "—",
            ),
            ("Liga / sport", f"{head.league} · {head.sport}"),
        ]
        st.markdown(ui.key_values(pairs), unsafe_allow_html=True)
        if not context.get("competition"):
            st.caption("Ta liga nie ma legalnego źródła wyników w aplikacji — brak modelu.")


def _one_x_two(rows, context, review) -> tuple[dict, str | None, list[str]]:
    probs, models, statuses = {}, set(), []
    for r in rows:
        if r.market_key == "1x2" and r.period == "FT" and r.p_model is not None:
            probs[r.selection] = r.p_model
            models.add(r.model)
            statuses.append(r.predictive_status)
    original = review.get("forecast") if review.get("started") else None
    if original:  # after kick-off: exactly the forecast made before the match
        return original["probabilities"], original.get("family"), statuses
    if len(probs) < 3 and context.get("prediction"):
        prediction = context["prediction"]
        return prediction["probabilities"], prediction.get("family"), statuses
    return probs, next(iter(models), None), statuses


def probabilities(head, rows, context: dict, review: dict) -> None:
    """One block per market — never one number mixing different models."""
    home, away = teams_of(head)
    blocks = []
    probs, model, statuses = _one_x_two(rows, context, review)
    if len(probs) == 3:
        view = ins.outlook(probs, home, away)
        blocks.append(
            ui.market_block(
                "Wynik meczu (1X2)",
                [
                    (f"{home}", probs["home"], ins.pct(probs["home"])),
                    ("Remis", probs["draw"], ins.pct(probs["draw"])),
                    (f"{away}", probs["away"], ins.pct(probs["away"])),
                ],
                _tag(statuses),
                f"Model: {model_name(model)} · {ins.sentence(view['character']) if view else ''}",
            )
        )
    lines = defaultdict(dict)
    statuses_ou, models_ou = [], set()
    btts, statuses_btts, models_btts = {}, [], set()
    for r in rows:
        if r.p_model is None or r.period != "FT":
            continue
        if r.market_key == "total" and r.line and r.selection == "over":
            lines[float(r.line)] = r
            statuses_ou.append(r.predictive_status)
            models_ou.add(r.model)
        elif r.market_key == "btts":
            btts[r.selection] = r.p_model
            statuses_btts.append(r.predictive_status)
            models_btts.add(r.model)
    if lines:
        shown = sorted(lines)[:4]
        blocks.append(
            ui.market_block(
                "Liczba goli (Over/Under)",
                [
                    (
                        f"Powyżej {ins.num(line, 1)}",
                        lines[line].p_model,
                        ins.pct(lines[line].p_model),
                    )
                    for line in shown
                ],
                _tag(statuses_ou),
                f"Model: {model_name(next(iter(models_ou), None))} · "
                + _validated_lines(lines, shown),
            )
        )
    if btts:
        blocks.append(
            ui.market_block(
                "Obie drużyny strzelą (BTTS)",
                [
                    ("Tak", btts.get("yes"), ins.pct(btts.get("yes"))),
                    ("Nie", btts.get("no"), ins.pct(btts.get("no"))),
                ],
                _tag(statuses_btts),
                f"Model: {model_name(next(iter(models_btts), None))}",
            )
        )
    if not blocks:
        ui.empty_state(
            "Brak prognozy modelu dla tego meczu",
            "Liga bez danych, nieaktualne wyniki, za krótka historia drużyny albo mecz już się "
            "rozpoczął bez zapisanej prognozy.",
        )
        return
    ui.card_grid(blocks, kind="markets")


def _validated_lines(lines: dict, shown: list[float]) -> str:
    good = [ins.num(line, 1) for line in shown if lines[line].predictive_status == VALIDATED]
    if not good:
        return "wszystkie linie eksperymentalne"
    if len(good) == len(shown):
        return "wszystkie linie zwalidowane"
    return "zwalidowane linie: " + ", ".join(good) + "; pozostałe eksperymentalne"


# ---------------------------------------------------------------- level 2


def _value(x, digits=2, signed=False):
    if x is None:
        return None
    text = ins.num(x, digits)
    return ("+" + text) if signed and x > 0 else text


def _better(a, b, *, higher: bool = True) -> str:
    if a is None or b is None or a == b:
        return ""
    return ("home" if a > b else "away") if higher else ("home" if a < b else "away")


def national_form(recent: list[dict]) -> dict:
    """TASK 019 §3.1: the form of a national team from the matches listed on the cards (all
    national competitions in the database); measures of another source are "brak danych"."""
    shown = [r for r in recent if r.get("own_score")][:5]
    if not shown:
        return {"form5": None, "form10": None, "venue_form5": None}
    goals = [tuple(int(x) for x in r["own_score"].split(":")) for r in shown]
    points = [3 if r["outcome"] == "W" else 1 if r["outcome"] == "R" else 0 for r in shown]
    return {
        "form5": {
            "sequence": [r["outcome"] for r in reversed(shown)],
            "points_per_game": sum(points) / len(shown),
            "goals_for": sum(g[0] for g in goals) / len(shown),
            "goals_against": sum(g[1] for g in goals) / len(shown),
            "points_vs_elo_expectation": None,
        },
        "form10": None,
        "venue_form5": None,
    }


def comparison(head, teams: dict, view: dict | None = None) -> None:
    """``view`` (TASK 019 §3.1): the cup / national schedule — then the rest days and the
    matches in 14 days come from it, the same numbers as the cards above."""
    home_name, away_name = teams_of(head)
    home, away = dict(teams.get("home") or {}), dict(teams.get("away") or {})
    scope_note = "(tylko ligowe)"
    if view and view.get("schedule"):
        for side, team in (("home", home), ("away", away)):
            plan = view["schedule"][side]
            team["rest_days"] = plan.get("rest_days")
            team["matches_last_14"] = plan.get("matches_14")
            if view.get("category") == "national":  # the form from the same matches as the cards
                team.update(national_form(plan.get("recent") or []))
        scope_note = (
            "(wszystkie mecze reprezentacji w bazie)"
            if view.get("category") == "national"
            else "(liga i puchary)"
        )
    if not (home.get("form5") or away.get("form5")):
        ui.empty_state(
            "Brak danych do porównania drużyn",
            "Nazwy drużyn nie zostały jednoznacznie dopasowane do wyników albo brak historii.",
        )
        return

    def form(team, key="form5", field="points_per_game"):
        return (team.get(key) or {}).get(field)

    def seq(team, key):
        found = (team.get(key) or {}).get("sequence")
        return " ".join(found) if found else None

    rows = []
    rows.append(
        (
            "Forma (5 ostatnich)",
            seq(home, "form5"),
            seq(away, "form5"),
            "",
            "W wygrana · R remis · P porażka, od najstarszego",
        )
    )
    for label, key, field, higher in (
        ("Punkty na mecz (5)", "form5", "points_per_game", True),
        ("Punkty na mecz (10)", "form10", "points_per_game", True),
        ("Gole zdobyte na mecz (5)", "form5", "goals_for", True),
        ("Gole stracone na mecz (5)", "form5", "goals_against", False),
    ):
        a, b = form(home, key, field), form(away, key, field)
        rows.append((label, _value(a), _value(b), _better(a, b, higher=higher), ""))
    rows.append(
        (
            "Forma u siebie / na wyjeździe (5)",
            seq(home, "venue_form5"),
            seq(away, "venue_form5"),
            "",
            "gospodarze w meczach u siebie, goście na wyjeździe",
        )
    )
    a = form(home, "form5", "points_vs_elo_expectation")
    b = form(away, "form5", "points_vs_elo_expectation")
    rows.append(
        (
            "Wynik wobec siły rywali (5)",
            _value(a, 1, signed=True),
            _value(b, 1, signed=True),
            _better(a, b),
            "punkty ponad (+) lub poniżej (−) oczekiwania Elo",
        )
    )
    rest_home, rest_away = home.get("rest_days"), away.get("rest_days")
    rows.append(
        (
            "Dni odpoczynku",
            None if rest_home is None else str(rest_home),
            None if rest_away is None else str(rest_away),
            "",
            f"mecze w 14 dni: {home.get('matches_last_14', '—')} / "
            f"{away.get('matches_last_14', '—')} {scope_note}",
        )
    )
    xg_home = (home.get("stats5") or {}).get("expected_goals")
    xg_away = (away.get("stats5") or {}).get("expected_goals")
    rows.append(
        (
            "xG na mecz (5)",
            None if not xg_home else ins.num(xg_home["for"]),
            None if not xg_away else ins.num(xg_away["for"]),
            _better(xg_home and xg_home["for"], xg_away and xg_away["for"]),
            "gole oczekiwane — statystyki zapisywane od jesieni 2026",
        )
    )
    st.markdown(ui.card(ui.compare_table(home_name, away_name, rows)), unsafe_allow_html=True)
    st.caption(
        "Zielony kolor oznacza korzystniejszą wartość. Porównanie to informacja — prognoza "
        "Elo i Dixon–Coles liczy siłę drużyn z wyników, nie z tej tabeli."
    )


def risk_factors(rows, teams: dict, info: dict) -> list[tuple[str, str]]:
    """What could change the assessment: model inputs vs warnings vs missing data."""
    out: list[tuple[str, str]] = []
    if any(r.p_model is not None and r.model and r.model.startswith("elo") for r in rows):
        out.append(("Siła obu drużyn z wyników meczów i przewaga własnego boiska (Elo)", "model"))
    if any(r.p_model is not None and r.model and "dixon" in r.model for r in rows):
        out.append(("Średnie gole zdobyte i stracone z ostatnich 2 lat (Dixon–Coles)", "model"))
    status = info["lineup_status"]
    if status in ("confirmed", "official"):
        out.append(("Skład potwierdzony — sprawdź zmiany przed postawieniem kuponu", "warning"))
    elif status == "predicted":
        out.append(("Skład tylko przewidywany przez dostawcę — może się zmienić", "warning"))
    else:
        out.append(("Brak informacji o składach (zwykle ok. godziny przed meczem)", "missing"))
    for change in info["changes"][:4]:
        out.append((f"Zmiana w składzie: {change['detail']}", "warning"))
    verified = [a for a in info["absences"] if a["match_status"] == "verified"]
    if verified:
        names = ", ".join(a["player_name"] for a in verified[:4])
        out.append((f"Absencje (zweryfikowane): {names}", "warning"))
    else:
        out.append(("Kontuzje i zawieszenia: brak zweryfikowanego źródła", "missing"))
    for side, label in (("home", "Gospodarze"), ("away", "Goście")):
        team = teams.get(side) or {}
        rest, load = team.get("rest_days"), team.get("matches_last_14")
        if (rest is not None and rest <= 3) or (load is not None and load >= 4):
            out.append(
                (
                    f"{label}: zagęszczony terminarz — {rest} dni odpoczynku, {load} mecze w 14 dni",
                    "warning",
                )
            )
    if not any((teams.get(s) or {}).get("stats5") for s in ("home", "away")):
        out.append(("Brak kompletnych statystyk meczowych (xG, strzały)", "missing"))
    weak = sorted(
        {
            r.market_label.split(" — ")[0]
            for r in rows
            if r.p_model is not None and r.predictive_status != VALIDATED
        }
    )
    if weak:
        more = f" i {len(weak) - 2} innych" if len(weak) > 2 else ""
        out.append(
            (
                f"Rynki bez walidacji predykcyjnej (model eksperymentalny): {len(weak)} — np. "
                + ", ".join(weak[:2])
                + more,
                "warning",
            )
        )
    return out


def ai_summary(pick: dict | None, rows, teams: dict, info: dict, *, started: bool) -> list:
    """A few sentences from the application's facts (template, no language model)."""
    if started:
        return [("Status", "Mecz już się rozpoczął — prognoza sprzed meczu jest zamrożona.")]
    if pick is None:
        return [
            ("Wniosek", "Żaden obsługiwany rynek nie ma wystarczająco wiarygodnego typu."),
            (
                "Dlaczego",
                "Ostrożna szansa poniżej 55% albo kurs, który po podatku nie zwraca stawki.",
            ),
        ]
    x = pick["candidate"]
    points = [
        (
            "Za prognozą",
            f"Model szacuje „{ins.candidate_pick(x)}” na {ins.pct(x.p_model)} "
            f"(ostrożnie {ins.pct(x.p_low)}) przy kursie STS {ins.num(x.odds)}.",
        )
    ]
    doubts = []
    if x.p_model is not None and x.p_low is not None and x.p_model - x.p_low > 0.04:
        doubts.append(f"przedział niepewności do {ins.pct(x.p_model - x.p_low)} w dół")
    if x.predictive_status != VALIDATED:
        doubts.append("model tego rynku jest eksperymentalny")
    if info["lineup_status"] not in ("confirmed", "official"):
        doubts.append("składy nie są jeszcze potwierdzone")
    if x.freshness != "fresh":
        doubts.append("kurs niepotwierdzony w aktualnej ofercie")
    points.append(
        (
            "Niepewność",
            "; ".join(doubts).capitalize() + "."
            if doubts
            else "Brak istotnych zastrzeżeń w danych aplikacji.",
        )
    )
    points.append(
        (
            "Wykorzystane dane",
            f"Wyniki meczów obu drużyn ({model_name(x.model)}), kalibracja na "
            "meczach sprzed okna selekcji.",
        )
    )
    points.append(
        (
            "Nie wykorzystano",
            "Składów, kontuzji, odpoczynku i statystyk xG — są pokazane tylko jako informacja.",
        )
    )
    return points


def analysis(rows, teams: dict, info: dict, pick: dict | None, *, started: bool) -> None:
    """Level 2: risk factors and the short reasoning side by side (stacked on phones)."""
    left = ui.card(
        "<b>Co może wpłynąć na ocenę meczu?</b>" + ui.factors(risk_factors(rows, teams, info))
    )
    right = ui.card(
        "<b>Dlaczego AI wybrało taki typ?</b>"
        + ui.summary(ai_summary(pick, rows, teams, info, started=started))
        + '<div class="se-muted" style="font-size:.78rem;margin-top:.6rem">Podsumowanie z danych '
        "aplikacji — bez modelu językowego.</div>",
        tone="accent",
    )
    st.markdown(f'<div class="se-grid two">{left}{right}</div>', unsafe_allow_html=True)
