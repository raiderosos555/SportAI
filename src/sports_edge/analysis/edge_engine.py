"""Per-selection metrics and acceptance gates for the STS offer.

Price information (implied probability, odds after the stake coefficient) is kept separate
from expected value, which needs an independent model probability. STS's own de-margined
price is a market benchmark only; it is never used as a forecast against STS.

Tiers:
* ``validated`` — every gate passed (validated model, verified market rules and tax profile,
  fresh current quote, positive lower bound of net EV);
* ``experimental`` — a model forecast shows positive net EV, but at least one validation gate
  failed (e.g. the model is not validated): an observation, not a recommendation;
* ``rejected`` — data, freshness, rules or EV gates failed.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sports_edge.analysis.policy import Policy
from sports_edge.bookmakers import BookmakerProfile, MarketRules
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
NOT_ANALYSED = {"unknown", "handicap_unsigned"}


@dataclass(frozen=True)
class Candidate:
    key: str
    event_id: str
    market_id: str
    quote_set_id: str
    odds_id: str
    selection_id: str
    event: str
    sport: str
    league: str
    start_at: str | None
    offer_code: str | None
    market_key: str
    period: str
    line: str | None
    selection: str
    odds: Decimal
    implied: float
    net_multiplier: Decimal | None  # payout per 1 PLN after the stake coefficient
    margin: float | None
    p_market: float | None
    p_model: float | None
    p_low: float | None
    p_high: float | None
    model: str | None
    model_version_id: str | None
    model_status: str
    validation: str
    ev_gross: float | None
    ev_net: Decimal | None
    ev_net_low: Decimal | None
    quality: float
    data_status: str
    rules_status: str
    tax_status: str
    freshness: str
    presence: str
    tier: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    explanation: dict = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.tier == "validated"


def devig(row: dict) -> tuple[dict[str, float] | None, float | None]:
    expected = COMPLETE_SETS.get(row["market_key"])
    prices = {k: float(v["price"]) for k, v in row["selections"].items()}
    if not expected or set(prices) != expected:
        return None, None
    margin = sum(1 / p for p in prices.values()) - 1
    return market_probabilities(prices), margin


def _payoff(row: dict, rules: MarketRules) -> tuple[str, str | None]:
    """(rules_status, payoff) — integer total lines can push, never treated as binary."""
    rule = rules.lookup(row["sport"], row["market_key"], row["period"])
    if rule is None:
        return "niezweryfikowane", None
    line = row.get("line")
    if line and row["market_key"].startswith(("total", "team_total")):
        if Decimal(line) == Decimal(line).to_integral_value():
            return "niezweryfikowane", None
    return "zweryfikowane", rule.payoff


def evaluate(
    rows: list[dict],
    *,
    predictions: dict,
    profile: BookmakerProfile,
    market_rules: MarketRules,
    policy: Policy,
    now: datetime,
    max_age: timedelta,
    stake: Decimal,
) -> list[Candidate]:
    out: list[Candidate] = []
    for row in rows:
        if row["market_key"] in NOT_ANALYSED:
            continue
        fair, margin = devig(row)
        start = datetime.fromisoformat(row["start_at"]) if row["start_at"] else None
        event_day = (
            start.date()
            if start
            else (date.fromisoformat(row["offer_date"]) if row["offer_date"] else None)
        )
        status = freshness(row["valid_at"], row["start_at"], now, max_age)
        prediction = predictions.get(
            (row["event_id"], row["market_key"], row["period"], row["line"])
        )
        rules_status, payoff = _payoff(row, market_rules)
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
                    rules_status,
                    payoff,
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
    profile: BookmakerProfile,
    event_day,
    status,
    prediction,
    rules_status,
    payoff,
    policy: Policy,
    stake: Decimal,
) -> Candidate:
    blocking: list[str] = []  # data and EV problems: the selection is rejected
    unproven: list[str] = []  # validation problems: at best an experimental candidate
    warnings: list[str] = []
    odds: Decimal = quote["price"]
    data_status = "ok"
    if row["status"] != "ok":
        blocking.append("Wiersz oferty wymaga ręcznej weryfikacji")
        data_status = "do weryfikacji"
    if row["quality"] < policy.min_data_quality:
        blocking.append(f"Jakość odczytu {row['quality']:.2f} < {policy.min_data_quality}")
        data_status = "niska jakość"
    if policy.require_verified_parser and not row["parser_verified"]:
        blocking.append("Parser niezweryfikowany na autentycznym dokumencie")
        data_status = "parser niezweryfikowany"
    if row.get("presence") == "withdrawn":
        blocking.append("Rynek wycofany — nie występuje w nowszej ofercie tego samego rodzaju")
    if policy.require_fresh_quote and status != "fresh":
        blocking.append(
            {
                "expired": "Kurs wygasł — wydarzenie już się rozpoczęło",
                "stale": "Kurs nieaktualny — przekroczony maksymalny wiek oferty",
                "unknown": "Nieznany czas publikacji kursu",
            }[status]
        )
    if odds > Decimal(str(policy.max_odds)):
        blocking.append(f"Kurs {odds} powyżej limitu polityki {policy.max_odds}")
    profile_ok = event_day is not None and profile.cost_valid_on(event_day)
    tax_status = "zweryfikowany" if profile.tax_verified else "założenie"
    net_multiplier = None
    if not profile_ok:
        blocking.append("Profil podatkowy nieważny dla daty wydarzenia — wymaga weryfikacji")
        tax_status = "nieważny"
    else:
        net_multiplier = (odds * (1 - profile.cost.stake_tax_rate)).quantize(Decimal("0.0001"))
        if not profile.tax_verified:
            unproven.append("Profil podatkowy niezweryfikowany")
    if rules_status != "zweryfikowane":
        unproven.append("Zasady rozliczenia rynku STS niezweryfikowane")
    p_model = p_low = p_high = None
    model_name = model_version_id = None
    model_status = "brak prognozy"
    ev_gross = ev_net_value = ev_net_low = None
    validation_text = "Brak niezależnej walidacji"
    if prediction is None:
        blocking.append("Brak niezależnej prognozy modelu dla tego rynku")
    else:
        model_name = f"{prediction['family']} {prediction['version']}"
        model_version_id = prediction["model_version_id"]
        model_status = prediction["version_status"]
        p_model = prediction["probabilities"].get(selection)
        interval = prediction["uncertainty"].get("interval", {}).get(selection)
        if interval:
            p_low, p_high = interval
        validation = prediction["validation"]
        holdout = validation.get("holdout") or {}
        validation_text = (
            f"holdout n={holdout.get('n')}, log loss={holdout.get('log_loss')}, "
            f"ECE={holdout.get('ece')}, rynek: {validation.get('beats_market')}"
        )
        if policy.require_validated_model and model_status != "validated":
            unproven.append("Model bez zatwierdzonej walidacji — dodatnie EV nie jest przewagą")
        if p_model is None:
            blocking.append("Prognoza nie obejmuje tej selekcji")
        if p_low is None:
            unproven.append("Brak oszacowania niepewności prognozy")
    if p_model is not None:
        ev_gross = binary_ev(p_model, float(odds))
        if payoff not in {None, "binary"}:
            blocking.append("Możliwy zwrot stawki — wzór binarny EV netto nie ma zastosowania")
        elif profile_ok:
            try:
                ev_net_value = _net(p_model, stake, odds, profile, event_day)
                if p_low is not None:
                    ev_net_low = _net(p_low, stake, odds, profile, event_day)
            except ValueError as error:
                blocking.append(f"EV netto nieobliczalne: {error}")
        if ev_net_value is not None and ev_net_value <= 0:
            blocking.append(f"EV netto {ev_net_value:.3f} ≤ 0")
        threshold = Decimal(str(policy.min_net_ev_lower_bound))
        if ev_net_low is not None and ev_net_low <= threshold:
            unproven.append(f"Dolna granica EV netto {ev_net_low:.3f} ≤ próg {threshold}")
    if profile.tax_verified and profile_ok:
        warnings.append("EV netto przy standardowym współczynniku stawki 0,88 (R 17.1)")
    tier = "rejected" if blocking else ("experimental" if unproven else "validated")
    reasons = tuple(blocking + unproven)
    p_market = fair.get(selection) if fair else None
    implied = 1 / float(odds)
    explanation = {
        "Model": model_name or "brak",
        "Co wykrył model": (
            f"P_model={p_model:.3f}, P_rynek STS bez marży={p_market:.3f}"
            if p_model is not None and p_market is not None
            else "Brak prognozy modelu — nie można ocenić przewagi"
        ),
        "Dane modelu": prediction["inputs"] if prediction else "brak",
        "Dlaczego kurs może być nieefektywny": (
            f"Kurs {odds} implikuje {implied:.1%} (marża STS {margin:.1%})"
            if margin is not None
            else f"Kurs {odds} implikuje {implied:.1%}; marża nieznana (rynek niepełny)"
        ),
        "Niepewność": f"[{p_low:.3f}, {p_high:.3f}]" if p_low is not None else "nieoszacowana",
        "Walidacja": validation_text,
        "Decyzja": {
            "validated": "ZWALIDOWANY KANDYDAT (analiza, nie gwarancja)",
            "experimental": "EKSPERYMENTALNY — przewaga niepotwierdzona: " + "; ".join(unproven),
            "rejected": "ODRZUCONY: " + "; ".join(reasons),
        }[tier],
    }
    return Candidate(
        key=f"{row['quote_set_id']}:{selection}",
        event_id=row["event_id"],
        market_id=row["market_id"],
        quote_set_id=row["quote_set_id"],
        odds_id=quote["odds_id"],
        selection_id=quote["selection_id"],
        event=" - ".join(row["participants"]) if row["participants"] else row["name_raw"],
        sport=row["sport"],
        league=row["league"],
        start_at=row["start_at"],
        offer_code=row.get("offer_code"),
        market_key=row["market_key"],
        period=row["period"],
        line=row["line"],
        selection=selection,
        odds=odds,
        implied=implied,
        net_multiplier=net_multiplier,
        margin=margin,
        p_market=p_market,
        p_model=p_model,
        p_low=p_low,
        p_high=p_high,
        model=model_name,
        model_version_id=model_version_id,
        model_status=model_status,
        validation=validation_text,
        ev_gross=ev_gross,
        ev_net=ev_net_value,
        ev_net_low=ev_net_low,
        quality=row["quality"],
        data_status=data_status,
        rules_status=rules_status,
        tax_status=tax_status,
        freshness=status,
        presence=row.get("presence", "current"),
        tier=tier,
        reasons=reasons,
        warnings=tuple(warnings),
        explanation=explanation,
    )


def _net(p: float, stake: Decimal, odds: Decimal, profile: BookmakerProfile, day: date) -> Decimal:
    distribution = {Outcome.WIN: p, Outcome.LOSS: 1 - p}
    return net_ev(distribution, stake, odds, profile.cost, on_date=day).quantize(Decimal("0.0001"))
