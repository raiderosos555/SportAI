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
from math import prod

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


# ---------------------------------------------------------------- TASK 011: risk of the day


JOINT_METHODS = {
    "matrix": "łączna szansa z macierzy wyników Dixon–Coles (λ, μ, ρ zapisane w prognozie)",
    "independent": "założenie niezależności (przybliżenie — typy jednego meczu są skorelowane)",
}


def match_of(leg: dict) -> str:
    """The match of a coupon leg: its event id; legs saved before the id was stored carry the
    match name and start only (TASK 012 fix: without this they were all ONE match)."""
    return leg.get("event_id") or f"{leg.get('event') or '?'}|{leg.get('start_at') or '?'}"


def day_risk(proposals: dict, rates: dict | None = None) -> dict | None:
    """Chance that at least two coupons of the day lose, on CONSERVATIVE leg chances.

    A selection shared by several coupons is ONE event (it wins or loses for all of them).
    Exact by inclusion–exclusion over the coupons: P(a set of coupons all win) is the
    product over the matches of P(all selections of that match in the UNION of their legs
    win). Different matches are independent (the model's assumption).

    Several different selections of ONE match (TASK 012 §5): when all of them come from the
    same Dixon–Coles forecast whose score-matrix parameters were saved (``rates``: (event id,
    model version id) → {"lambda", "mu", "rho"}) and every market follows from the matrix,
    their joint chance is read from the score matrix: P = Σ matrix · Π masks, shrunk like the
    single chances (× Π q_i / r_i, q: conservative chance, r: the matrix's own chance) — for
    independent selections this is exactly the product of the conservative chances — and
    kept between the Fréchet bounds (max(0, Σ q − (n − 1)) … min q). Otherwise (an old
    forecast without the parameters, Elo 1X2, two models) the selections stay independent
    and ``joint`` says which method each match used. A leg without a conservative chance
    uses the model chance (``model_fallback``). No stake is recommended."""
    from sports_edge.models.goals import outcome_mask, score_matrix

    rates = rates or {}
    coupons = []
    chances: dict[tuple, float] = {}
    legs: dict[tuple, dict] = {}
    fallback = 0
    for name, payload in proposals.items():
        if not payload or not payload.get("legs"):
            continue
        keys = []
        for leg in payload["legs"]:
            key = (match_of(leg), leg.get("market_key"), leg.get("line"), leg["selection"])
            q = leg.get("p_conservative")
            if q is None:
                q = leg.get("p_model")
                fallback += key not in chances
            if q is None:
                return None  # a leg without any estimate: the risk cannot be stated
            chances.setdefault(key, float(q))
            legs.setdefault(key, leg)
            keys.append(key)
        coupons.append((name, payload.get("label") or name, frozenset(keys)))
    m = len(coupons)
    if m < 2:
        return None
    by_event: dict[str, list[tuple]] = {}
    for key in chances:
        by_event.setdefault(key[0], []).append(key)
    matrices: dict[str, tuple] = {}  # event → (score matrix, {key: mask})
    joint: dict[str, dict] = {}
    for event, keys in by_event.items():
        if len(keys) < 2:
            continue
        versions = {legs[k].get("model_version_id") for k in keys}
        params = rates.get((event, next(iter(versions)))) if len(versions) == 1 else None
        masks = {k: outcome_mask(k[1], k[2], k[3]) for k in keys}
        if params and all(mask is not None for mask in masks.values()):
            matrices[event] = (
                score_matrix(float(params["lambda"]), float(params["mu"]), float(params["rho"])),
                masks,
            )
            joint[event] = {"method": "matrix", "selections": len(keys)}
        else:
            if len(versions) > 1:
                why = "typy z różnych modeli"
            elif params:
                why = "rynek spoza macierzy wyników (np. Elo 1X2)"
            else:
                why = "prognoza bez zapisanych parametrów macierzy (starszy zapis)"
            joint[event] = {"method": "independent", "selections": len(keys), "reason": why}
    cache: dict[frozenset, float] = {}

    def together(keys: frozenset) -> float:
        """P(every selection of ONE match in ``keys`` wins)."""
        if keys in cache:
            return cache[keys]
        q = [chances[k] for k in keys]
        event = next(iter(keys))[0]
        if len(keys) < 2 or event not in matrices:
            value = prod(q)
        else:
            matrix, masks = matrices[event]
            raw = float((matrix * prod(masks[k] for k in keys)).sum())
            shrink = prod(chances[k] / max(float((matrix * masks[k]).sum()), 1e-12) for k in keys)
            value = min(min(q), max(max(0.0, sum(q) - (len(q) - 1)), raw * shrink))
        cache[keys] = value
        return value

    def all_win(indices) -> float:
        union = frozenset().union(*(coupons[i][2] for i in indices))
        groups: dict[str, set] = {}
        for key in union:
            groups.setdefault(key[0], set()).add(key)
        return prod(together(frozenset(keys)) for keys in groups.values())

    everyone = all_win(range(m))
    exactly_one = sum(all_win([i for i in range(m) if i != j]) - everyone for j in range(m))
    win = [all_win([i]) for i in range(m)]
    independent_none = prod(win)
    independent_one = sum((1 - win[j]) * prod(win[i] for i in range(m) if i != j) for j in range(m))
    events = Counter(key[0] for key in chances)
    same_match_pairs = sum(n * (n - 1) // 2 for n in events.values() if n > 1)
    shared = [key for key in chances if sum(key in c[2] for c in coupons) > 1]
    for event, info in joint.items():
        keys = frozenset(by_event[event])
        info["joint"] = together(keys)
        info["product"] = prod(chances[k] for k in keys)
        info["event"] = legs[next(iter(keys))].get("event") or event
    methods = Counter(info["method"] for info in joint.values())
    if methods:
        same_match = "typy jednego meczu: " + "; ".join(
            f"{JOINT_METHODS[k]} — {plural(v, 'mecz', 'mecze', 'meczów')}"
            for k, v in sorted(methods.items())
        )
    else:
        same_match = "bez kilku różnych typów jednego meczu"
    return {
        "coupons": m,
        "selections": len(chances),
        "shared_selections": len(shared),
        "same_match_pairs": same_match_pairs,
        "model_fallback": fallback,
        "p_all_win": everyone,
        "p_two_or_more_lose": max(0.0, 1.0 - everyone - exactly_one),
        "expected_losing": sum(1 - p for p in win),
        "independent_two_or_more_lose": max(0.0, 1.0 - independent_none - independent_one),
        "joint": joint,
        "matrix_matches": methods.get("matrix", 0),
        "independent_matches": methods.get("independent", 0),
        "basis": "szanse ostrożne; wspólna selekcja = jedno zdarzenie; różne mecze niezależne; "
        + same_match,
    }


def matrix_rates(store, proposals: dict) -> dict[tuple, dict]:
    """(event id, model version id) → the score-matrix parameters saved with the newest
    prospective forecast of that version for the match (TASK 012 §5.1); only for matches
    with several different selections in the day's coupons. Old forecasts have none."""
    import json

    seen: dict[str, set] = {}
    versions: dict[str, set] = {}
    for payload in proposals.values():
        for leg in (payload or {}).get("legs", []):
            pick = (leg.get("market_key"), leg.get("line"), leg.get("selection"))
            if not leg.get("event_id"):
                continue  # an old leg without the event id: no stored forecast to read
            seen.setdefault(leg["event_id"], set()).add(pick)
            versions.setdefault(leg["event_id"], set()).add(leg.get("model_version_id"))
    out: dict[tuple, dict] = {}
    for event, picks in seen.items():
        if len(picks) < 2:
            continue
        for version in versions[event]:
            for row in store.rows(
                """SELECT inputs_json FROM predictions WHERE event_id=? AND model_version_id=?
                AND kind='prospective' ORDER BY created_at DESC, id""",
                (event, version),
            ):
                found = json.loads(row["inputs_json"] or "{}").get("score_matrix")
                if found:
                    out[(event, version)] = found
                    break
    return out
