"""Per-selection metrics and acceptance gates.

Price comparison (odds after stake tax) is separated from expected value, which needs an
independent, validated model probability. A bookmaker's own de-margined price is shown as a
market benchmark only; it is never treated as an independent forecast against that bookmaker.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sports_edge.analysis.policy import Policy
from sports_edge.bookmakers import BookmakerProfile
from sports_edge.edge import binary_ev, net_ev
from sports_edge.probability import market_probabilities
from sports_edge.repository import freshness
from sports_edge.settlement import Outcome

COMPLETE_SETS = {
    "1x2": {"home", "draw", "away"},
    "handicap_1x2": {"home", "draw", "away"},
    "match_winner": {"home", "away"},
    "set_winner": {"home", "away"},
    "draw_no_bet": {"home", "away"},
    "total": {"under", "over"},
    "team_total_home": {"under", "over"},
    "team_total_away": {"under", "over"},
    "btts": {"yes", "no"},
    "home_to_score": {"yes", "no"},
    "away_to_score": {"yes", "no"},
    "red_card": {"yes", "no"},
}
VOID_MARKETS = {"draw_no_bet"}
NOT_ANALYSED = {"unknown", "handicap_unsigned"}


@dataclass(frozen=True)
class Candidate:
    key: str
    bookmaker: str
    event_id: str
    market_id: str
    quote_set_id: str
    odds_id: str
    selection_id: str
    event: str
    sport: str
    league: str
    start_at: str | None
    market_key: str
    period: str
    line: str | None
    selection: str
    odds: Decimal
    net_multiplier: Decimal | None  # payout per 1 PLN staked after stake tax (no model needed)
    margin: float | None
    p_market: float | None
    p_model: float | None
    p_low: float | None
    p_high: float | None
    model: str | None
    model_status: str | None
    ev_gross: float | None
    ev_net: Decimal | None
    ev_net_low: Decimal | None
    quality: float
    parser_verified: bool
    freshness: str
    accepted: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    other: dict = field(default_factory=dict)  # same selection at the other bookmaker
    explanation: dict = field(default_factory=dict)


def devig(row: dict) -> tuple[dict[str, float] | None, float | None]:
    expected = COMPLETE_SETS.get(row["market_key"])
    prices = {k: float(v["price"]) for k, v in row["selections"].items()}
    if not expected or set(prices) != expected:
        return None, None
    margin = sum(1 / p for p in prices.values()) - 1
    return market_probabilities(prices), margin


def _void_possible(row: dict) -> bool:
    if row["market_key"] in VOID_MARKETS:
        return True
    line = row.get("line")
    if line and row["market_key"].startswith(("total", "team_total")):
        return Decimal(line) == Decimal(line).to_integral_value()
    return False


def _event_name(row: dict) -> str:
    return " - ".join(row["participants"]) if row["participants"] else row["name_raw"]


def evaluate(
    rows: list[dict],
    *,
    predictions: dict,
    profiles: dict[str, BookmakerProfile],
    policy: Policy,
    now: datetime,
    max_age: timedelta,
    stake: Decimal,
    counterparts: dict[str, dict] | None = None,
) -> list[Candidate]:
    counterparts = counterparts or {}
    out: list[Candidate] = []
    for row in rows:
        if row["market_key"] in NOT_ANALYSED:
            continue
        fair, margin = devig(row)
        profile = profiles.get(row["bookmaker_id"])
        event_day = (
            datetime.fromisoformat(row["start_at"]).date()
            if row["start_at"]
            else date.fromisoformat(row["offer_date"])
            if row["offer_date"]
            else None
        )
        status = freshness(row["valid_at"], row["start_at"], now, max_age)
        prediction = predictions.get(
            (row["event_id"], row["market_key"], row["period"], row["line"])
        )
        other_row = counterparts.get(row["quote_set_id"])
        for selection, quote in row["selections"].items():
            out.append(
                _candidate(
                    row,
                    selection,
                    quote,
                    fair,
                    margin,
                    profile,
                    event_day,
                    status,
                    prediction,
                    other_row,
                    policy,
                    stake,
                )
            )
    return out


def _candidate(
    row,
    selection,
    quote,
    fair,
    margin,
    profile,
    event_day,
    status,
    prediction,
    other_row,
    policy: Policy,
    stake: Decimal,
) -> Candidate:
    reasons: list[str] = []
    warnings: list[str] = []
    odds: Decimal = quote["price"]
    if row["status"] != "ok":
        reasons.append("Wiersz oferty wymaga ręcznej weryfikacji")
    if row["quality"] < policy.min_data_quality:
        reasons.append(f"Jakość odczytu {row['quality']:.2f} < {policy.min_data_quality}")
    if policy.require_verified_parser and not row["parser_verified"]:
        reasons.append("Parser niezweryfikowany na autentycznym dokumencie operatora")
    if policy.require_fresh_quote and status != "fresh":
        reasons.append(
            {
                "expired": "Kurs wygasł — wydarzenie już się rozpoczęło",
                "stale": "Kurs nieaktualny — przekroczony maksymalny wiek oferty",
                "unknown": "Nieznany czas publikacji kursu",
            }[status]
        )
    if "unverified" in row["rules_ref"]:
        warnings.append("Reguły rozliczenia wywnioskowane z etykiety oferty, nie z regulaminu")
    net_multiplier = None
    profile_ok = profile is not None and event_day is not None and profile.cost_valid_on(event_day)
    if profile is None:
        reasons.append("Brak profilu kosztów bukmachera")
    elif not profile_ok:
        reasons.append("Profil podatkowy nieważny dla daty wydarzenia — wymaga weryfikacji")
    else:
        net_multiplier = (odds * (1 - profile.cost.stake_tax_rate)).quantize(Decimal("0.0001"))
        if not profile.tax_verified:
            warnings.append("Profil podatkowy oparty na założeniu (niezweryfikowany)")
    if odds > Decimal(str(policy.max_odds)):
        reasons.append(f"Kurs {odds} powyżej limitu polityki {policy.max_odds}")
    p_model = p_low = p_high = None
    model_name = model_status = None
    ev_gross = ev_net_value = ev_net_low = None
    validation_text = "Brak niezależnej walidacji"
    if prediction is None:
        reasons.append("Brak niezależnej prognozy modelu dla tego rynku")
    else:
        model_name = f"{prediction['family']} {prediction['version']}"
        model_status = prediction["version_status"]
        p_model = prediction["probabilities"].get(selection)
        interval = prediction["uncertainty"].get("interval", {}).get(selection)
        if interval:
            p_low, p_high = interval
        validation = prediction["validation"]
        validation_text = (
            f"n={validation.get('n_out_of_sample')}, ECE={validation.get('ece')}, "
            f"log-loss={validation.get('log_loss')}, rynek: {validation.get('beats_market')}"
        )
        if policy.require_validated_model and model_status != "validated":
            reasons.append("Model bez zatwierdzonej walidacji — dodatnie EV nie jest przewagą")
        if p_model is None:
            reasons.append("Prognoza nie obejmuje tej selekcji")
        if p_low is None:
            reasons.append("Brak oszacowania niepewności prognozy")
    if p_model is not None:
        ev_gross = binary_ev(p_model, float(odds))
        if _void_possible(row):
            reasons.append("Możliwy zwrot stawki — uproszczony wzór EV netto nie ma zastosowania")
        elif profile_ok:
            try:
                ev_net_value = _net(p_model, stake, odds, profile, event_day)
                if p_low is not None:
                    ev_net_low = _net(p_low, stake, odds, profile, event_day)
            except ValueError as error:
                reasons.append(f"EV netto nieobliczalne: {error}")
        if ev_net_low is not None and ev_net_low <= Decimal(str(policy.min_net_ev_lower_bound)):
            reasons.append(
                f"EV netto (dolna granica) {ev_net_low:.3f} ≤ próg {policy.min_net_ev_lower_bound}"
            )
    p_market = fair.get(selection) if fair else None
    other = {}
    if other_row is not None and selection in other_row["selections"]:
        other = {
            "bookmaker": other_row["bookmaker_id"],
            "odds": other_row["selections"][selection]["price"],
            "valid_at": other_row["valid_at"],
        }
    explanation = {
        "Model": model_name or "brak",
        "Co wykrył model": (
            f"P_model={p_model:.3f}, P_rynek(bez marży)={p_market:.3f}"
            if p_model is not None and p_market is not None
            else "Brak prognozy modelu — nie można ocenić przewagi"
        ),
        "Dane modelu": prediction["inputs"] if prediction else "brak",
        "Dlaczego kurs może być nieefektywny": (
            f"Kurs {odds} implikuje {1 / float(odds):.1%} (z marżą {margin:.1%})"
            if margin is not None
            else f"Kurs {odds} implikuje {1 / float(odds):.1%}; marża nieznana (rynek niepełny)"
        ),
        "Niepewność": f"[{p_low:.3f}, {p_high:.3f}]" if p_low is not None else "nieoszacowana",
        "Walidacja": validation_text,
        "Decyzja": "PRZYJĘTA" if not reasons else "ODRZUCONA: " + "; ".join(reasons),
    }
    return Candidate(
        key=f"{row['quote_set_id']}:{selection}",
        bookmaker=row["bookmaker_id"],
        event_id=row["event_id"],
        market_id=row["market_id"],
        quote_set_id=row["quote_set_id"],
        odds_id=quote["odds_id"],
        selection_id=quote["selection_id"],
        event=_event_name(row),
        sport=row["sport"],
        league=row["league"],
        start_at=row["start_at"],
        market_key=row["market_key"],
        period=row["period"],
        line=row["line"],
        selection=selection,
        odds=odds,
        net_multiplier=net_multiplier,
        margin=margin,
        p_market=p_market,
        p_model=p_model,
        p_low=p_low,
        p_high=p_high,
        model=model_name,
        model_status=model_status,
        ev_gross=ev_gross,
        ev_net=ev_net_value,
        ev_net_low=ev_net_low,
        quality=row["quality"],
        parser_verified=bool(row["parser_verified"]),
        freshness=status,
        accepted=not reasons,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        other=other,
        explanation=explanation,
    )


def _net(p: float, stake: Decimal, odds: Decimal, profile: BookmakerProfile, day: date) -> Decimal:
    distribution = {Outcome.WIN: p, Outcome.LOSS: 1 - p}
    return net_ev(distribution, stake, odds, profile.cost, on_date=day).quantize(Decimal("0.0001"))
