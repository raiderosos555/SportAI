"""Plain-language layer of the application (TASK 006): what the numbers mean for the user.

No Streamlit here, so everything is testable. Two ideas are never mixed:
* chance of winning — the (calibrated) probability of the outcome;
* reliability of the estimate — how far that number can be trusted. It is a rule-based
  label from validation, uncertainty, price freshness and time to kick-off, never an
  invented 0–100 score.
"""

from collections import Counter
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from sports_edge.analysis.coupon_builder import VALIDATED, eligibility
from sports_edge.analysis.edge_engine import Candidate
from sports_edge.offers.labels import SELECTIONS

NO_DATA = "Brak danych"
STALE = "kurs niepotwierdzony (stara migawka)"
RELIABILITY = {
    "high": "Wyższa wiarygodność prognozy",
    "moderate": "Umiarkowana wiarygodność",
    "elevated": "Podwyższona niepewność",
    "insufficient": "Niewystarczające dane",
}
GROUPS = {
    "A": "Kwalifikują się do kreatora",
    "B": "Eksperymentalne lub z niepotwierdzonym kursem",
    "C": "Odrzucone",
}
MIN_PICK = 0.55  # lowest profile threshold: below it no pick is called "reliable"


# ---------------------------------------------------------------- formatting (Polish)


def pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return NO_DATA
    return f"{value * 100:.{digits}f}%".replace(".", ",")


def num(value, digits: int = 2) -> str:
    if value is None:
        return NO_DATA
    return f"{float(value):.{digits}f}".replace(".", ",")


def money(value) -> str:
    if value is None:
        return NO_DATA
    return f"{Decimal(str(value)):.2f} zł".replace(".", ",")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Polish count: 1 mecz, 2 mecze, 5 meczów (12-14 → many)."""
    if n == 1:
        return f"{n} {one}"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} {few}"
    return f"{n} {many}"


def sentence(text: str) -> str:
    """Capital first letter only (names inside stay as they are)."""
    return text[:1].upper() + text[1:]


def selection_label(selection: str) -> str:
    return SELECTIONS.get(selection, selection)


def teams(event: str) -> tuple[str, str]:
    home, _, away = event.partition(" - ")
    return home.strip(), away.strip()


def pick_label(
    market_key: str, line: str | None, selection: str, event: str, market: str = ""
) -> str:
    """A selection in plain words: "Powyżej 1,5 gola", "Atalanta lub remis (1X)"."""
    home, away = teams(event)
    try:  # goal lines are numbers; a handicap line such as "0:1" is not
        goals = num(line, 1) if line not in (None, "") else ""
    except ValueError:
        goals = ""
    if market_key == "1x2":
        return {"home": f"Wygrana: {home}", "draw": "Remis", "away": f"Wygrana: {away}"}.get(
            selection, selection
        )
    if market_key in {"match_winner", "draw_no_bet"}:
        suffix = " (bez remisu)" if market_key == "draw_no_bet" else ""
        return {"home": f"Wygrana: {home}{suffix}", "away": f"Wygrana: {away}{suffix}"}.get(
            selection, selection
        )
    if market_key == "double_chance":
        return {
            "home_draw": f"{home} lub remis (1X)",
            "draw_away": f"Remis lub {away} (X2)",
            "home_away": "Bez remisu (12)",
        }.get(selection, selection)
    if market_key == "total" and goals:
        return f"{'Powyżej' if selection == 'over' else 'Poniżej'} {goals} gola"
    if market_key in {"team_total_home", "team_total_away"} and goals:
        team = home if market_key == "team_total_home" else away
        return f"{team}: {'powyżej' if selection == 'over' else 'poniżej'} {goals} gola"
    if market_key == "btts":
        return "Obie drużyny strzelą" if selection == "yes" else "Nie obie drużyny strzelą"
    if market_key in {"home_to_score", "away_to_score"}:
        team = home if market_key == "home_to_score" else away
        return f"{team} strzeli gola" if selection == "yes" else f"{team} nie strzeli gola"
    if market_key == "handicap_1x2" and line:
        side = {"home": home, "draw": "remis", "away": away}.get(selection, selection)
        return f"Handicap {line}: {side}"
    return f"{market} → {selection_label(selection)}" if market else selection_label(selection)


def candidate_pick(c: Candidate) -> str:
    return pick_label(c.market_key, c.line, c.selection, c.event, c.market_label)


# ---------------------------------------------------------------- reliability


def reliability(candidate: Candidate, *, now: datetime) -> tuple[str, list[str]]:
    """(level key, reasons). Major concerns → elevated; minor ones → moderate."""
    if candidate.p_model is None or candidate.p_low is None:
        return "insufficient", ["brak skalibrowanego oszacowania dla tego rynku"]
    major, minor, good = [], [], []
    gap = candidate.p_model - candidate.p_low
    if candidate.predictive_status != VALIDATED:
        major.append("model tego rynku nie przeszedł walidacji predykcyjnej")
    else:
        good.append("model tego rynku przeszedł walidację na nowych danych")
    if gap > 0.08:
        major.append(f"szeroki przedział niepewności (do {pct(gap)} w dół)")
    elif gap > 0.04:
        minor.append(f"umiarkowana niepewność (do {pct(gap)} w dół)")
    else:
        good.append("wąski przedział niepewności")
    if candidate.freshness != "fresh":
        major.append("kurs niepotwierdzony w aktualnej ofercie STS")
    days = _days_ahead(candidate, now)
    if days is not None and days > 3:
        major.append("mecz za kilka dni — składy i terminarz mogą się zmienić")
    elif days is not None and days > 1:
        minor.append("mecz nie jutro — składy jeszcze nieznane")
    if major:
        return "elevated", major + minor
    if minor:
        return "moderate", minor + good
    return "high", good


def _days_ahead(candidate: Candidate, now: datetime) -> float | None:
    if not candidate.start_at:
        return None
    return (datetime.fromisoformat(candidate.start_at) - now).total_seconds() / 86400


# ---------------------------------------------------------------- candidates A / B / C


def candidate_groups(candidates: list[Candidate], *, now: datetime, max_odds: float) -> list[dict]:
    """Every selection with a model probability, with its group and plain reason.

    A — meets every builder condition and its market family is validated;
    B — would qualify, but the model is experimental or the price is only historical;
    C — rejected (started, odds too low after tax, odds too high, no conservative bound…).
    """
    out = []
    for c in candidates:
        if c.p_model is None or not c.probability_ok:
            continue  # no or inconsistent probability: never ranked
        why = eligibility(c, max_odds)
        fresh_why = eligibility(replace(c, freshness="fresh"), max_odds) if why == STALE else why
        notes = []
        if why is None and c.predictive_status == VALIDATED:
            group = "A"
            notes.append("spełnia warunki kreatora")
        elif why is None or (why == STALE and fresh_why is None):
            group = "B"
            if c.predictive_status != VALIDATED:
                notes.append("model eksperymentalny — tylko symulacja")
            if why == STALE:
                age = f" ({num(c.odds_age_hours, 1)} h)" if c.odds_age_hours is not None else ""
                notes.append(f"kurs historyczny, niepotwierdzony{age}")
        else:
            group = "C"
            notes.append(why)
        level, reasons = reliability(c, now=now)
        out.append(
            {
                "group": group,
                "candidate": c,
                "event": c.event,
                "day": (c.start_at or "")[:10],
                "sport": c.sport,
                "league": c.league,
                "market": c.market_label,
                "market_key": c.market_key,
                "selection": selection_label(c.selection),
                "pick": candidate_pick(c),
                "p": c.p_model,
                "p_low": c.p_low,
                "odds": c.odds,
                "model_status": "zwalidowany"
                if c.predictive_status == VALIDATED
                else "eksperymentalny",
                "price_status": "potwierdzony" if c.freshness == "fresh" else "historyczny",
                "reason": "; ".join(notes),
                "reliability": level,
                "reliability_reasons": reasons,
            }
        )
    out.sort(key=lambda r: ((r["p_low"] if r["p_low"] is not None else -1), r["p"]), reverse=True)
    return out


# ---------------------------------------------------------------- why there is no coupon

CASES = {
    "A": "stare kursy",
    "B": "brak modeli",
    "C": "niska szansa trafienia",
    "D": "nieodpowiednia wypłata",
    "E": "niezwalidowany model",
}


def explain_no_coupon(
    candidates: list[Candidate],
    builder,
    offer: dict,
    *,
    now: datetime,
    max_odds: float,
) -> dict | None:
    """One main explanation of a day without any coupon (None when a coupon exists)."""
    if builder is not None and any(p is not None for p in builder.proposals.values()):
        return None
    events = {c.event_id for c in candidates}
    modelled = [c for c in candidates if c.p_model is not None]
    with_forecast = {c.event_id for c in modelled}
    groups = candidate_groups(candidates, now=now, max_odds=max_odds)
    stale = [g for g in groups if g["group"] == "B" and g["price_status"] == "historyczny"]
    rejected = [g for g in groups if g["group"] == "C"]
    reasons = Counter(g["reason"] for g in rejected)
    codes = Counter((builder.refusal_codes if builder is not None else {}).values())
    age, limit = offer.get("age_hours"), offer.get("limit_hours")
    if offer and not offer.get("fresh") and stale:
        case = "A"
        headline = "Analiza meczów zakończona, ale kursy STS wymagają odświeżenia. " + (
            f"Najnowszy dokument ma {num(age, 1)} h, a limit wynosi {num(limit, 0)} h."
            if age is not None
            else "Brak aktualnego dokumentu STS dla tego dnia."
        )
        action = (
            "Sprawdź oficjalną ofertę STS ponownie (przycisk poniżej) albo zaimportuj aktualny "
            "PDF ręcznie w zakładce Źródła i ustawienia."
        )
        count = len(stale)
    elif not modelled or len(with_forecast) < max(1, len(events) // 10):
        case = "B"
        headline = (
            f"Znaleziono {len(events)} wydarzeń, ale modele potrafią obecnie analizować tylko "
            f"{len(with_forecast)}. Pozostałe rozgrywki nie mają wystarczających danych."
        )
        action = "Szczegóły w raporcie pokrycia (Modele i statystyki → Pokrycie)."
        count = len(events) - len(with_forecast)
    elif codes.get("not_validated"):
        case = "E"
        candidates_e = [g for g in groups if g["group"] == "B"]
        headline = (
            f"Znaleziono {len(candidates_e)} potencjalnych selekcji, ale modele ich rynków nie "
            "przeszły jeszcze walidacji — mogą trafić tylko do symulacji."
        )
        action = "Zobacz ranking eksperymentalny poniżej (bez gwarancji jakości oszacowania)."
        count = len(candidates_e)
    elif codes.get("payout"):
        case = "D"
        headline = (
            "Dostępne typy mają wysokie prawdopodobieństwo, ale nie składają się na kurs łączny "
            "z przedziału profilu albo nie dają wymaganej wypłaty netto po współczynniku 0,88."
        )
        action = "Kursy tych typów są zbyt niskie; poczekaj na inne mecze lub inny profil."
        count = len([g for g in groups if g["group"] == "A"])
    else:
        case = "C"
        headline = (
            f"Przeanalizowano {len(modelled)} kandydatów. Żaden nie osiągnął minimalnej ostrożnej "
            "szansy wymaganej przez profile."
        )
        action = "Zobacz najlepsze typy do rozważenia — żaden nie jest wystarczająco pewny."
        count = len(modelled)
    return {
        "case": case,
        "label": CASES[case],
        "headline": headline,
        "action": action,
        "count": count,
        "rejected": len(rejected),
        "reasons": reasons.most_common(),
        "events": len(events),
        "events_with_forecast": len(with_forecast),
    }


# ---------------------------------------------------------------- match summary


def outlook(probabilities: dict[str, float] | None, home: str, away: str) -> dict | None:
    """Favourite and character of a match from 1X2 probabilities."""
    if not probabilities or not all(k in probabilities for k in ("home", "draw", "away")):
        return None
    h, d, a = probabilities["home"], probabilities["draw"], probabilities["away"]
    lead, name = (h, home) if h >= a else (a, away)
    if lead >= 0.55:
        character = f"wyraźny faworyt: {name}"
    elif abs(h - a) < 0.10:
        character = "mecz wyrównany"
    else:
        character = f"lekka przewaga: {name}"
    return {
        "favourite": name if abs(h - a) >= 0.10 else None,
        "home": h,
        "draw": d,
        "away": a,
        "character": character,
    }


def best_pick(candidates: list[Candidate], *, now: datetime, max_odds: float) -> dict | None:
    """The most likely supported selection that is reliable enough (or None)."""
    usable = [
        c
        for c in candidates
        if c.probability_ok
        and c.p_low is not None
        and c.p_low >= MIN_PICK
        and c.break_even is not None
        and c.break_even < 1
        and c.event_status == "before_start"
    ]
    if not usable:
        return None
    best = max(usable, key=lambda c: (c.p_low, c.p_model))
    why = eligibility(best, max_odds)
    level, reasons = reliability(best, now=now)
    return {
        "candidate": best,
        "eligible": why is None and best.predictive_status == VALIDATED,
        "experimental": best.predictive_status != VALIDATED,
        "why_not": why,
        "reliability": level,
        "reliability_reasons": reasons,
    }


def form_points(team: dict | None) -> list[str]:
    """Plain sentences about a team's form features (information outside the model)."""
    if not team or not team.get("form5"):
        return []
    form = team["form5"]
    out = [
        f"forma z {form['matches']} meczów: {form['sequence']} ({num(form['points_per_game'], 1)} "
        f"pkt/mecz, bramki {num(form['goals_for'], 1)}:{num(form['goals_against'], 1)} na mecz)"
    ]
    vs = form.get("points_vs_elo_expectation")
    if vs is not None:
        out.append(f"{'+' if vs >= 0 else ''}{num(vs, 1)} pkt względem oczekiwań (siła rywali)")
    venue = team.get("venue_form5") or {}
    if venue.get("sequence"):
        out.append(f"ostatnie mecze w tej roli (dom/wyjazd): {venue['sequence']}")
    if team.get("rest_days") is not None:
        out.append(
            f"{plural(team['rest_days'], 'dzień', 'dni', 'dni')} odpoczynku, "
            f"{plural(team.get('matches_last_14', 0), 'mecz', 'mecze', 'meczów')} w 14 dni"
        )
    return out
