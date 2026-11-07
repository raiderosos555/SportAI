"""Per-selection metrics and acceptance gates for the STS offer.

Price information (implied probability, odds after the stake coefficient) is kept separate
from expected value, which needs an independent model probability. STS's own de-margined
price is a market benchmark only; it is never used as a forecast against STS.

Tiers:
* ``validated`` — every gate passed (validated model, verified market rules and tax profile,
  fresh current quote, positive lower bound of net EV);
* ``experimental`` — a model forecast shows positive net EV, but at least one validation gate
  failed (e.g. the model is not validated, the price snapshot is old): an observation, not a
  recommendation;
* ``rejected`` — data, event status, rules or EV gates failed.

Three statuses are kept apart (TASK 004): the event (before start, in play, finished...), the
age of the price snapshot ("Kurs niepotwierdzony od X h") and the market's presence in the
newest relevant offer. A price is never treated as current just because the event has not
started yet.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sports_edge.analysis.policy import Policy
from sports_edge.bookmakers import BookmakerProfile, MarketRules
from sports_edge.edge import binary_ev, net_ev
from sports_edge.offers.labels import market_label
from sports_edge.probability import market_probabilities
from sports_edge.repository import event_status, odds_freshness
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
# Markets whose probabilities follow from the 1X2 forecast of the same period.
DERIVED_FROM_1X2 = {
    "double_chance": {
        "home_draw": ("home", "draw"),
        "draw_away": ("draw", "away"),
        "home_away": ("home", "away"),
    }
}


def derive_from_1x2(prediction: dict, market_key: str) -> dict:
    """Double chance from the same 1X2 forecast; interval bounds are added (conservative).

    Dixon–Coles ships its double chance inside the 1X2 row: since TASK 007 it is derived
    from the final 1X2 (P(1X) = 1 − P(2)); older rows calibrated every side separately, so
    their drift from 1 − P(excluded outcome) is measured and too large a drift blocks them.
    """
    shipped = (prediction["uncertainty"].get("derived") or {}).get(market_key)
    if shipped:
        base = prediction["probabilities"]
        return {
            **prediction,
            # forecasts before TASK 007 calibrated every side separately: measure how far
            # they drift from 1 − P(excluded outcome) of the same 1X2 forecast
            "coherence_gap": max(
                (
                    abs(p - (1 - base[EXCLUDED[sel]]))
                    for sel, p in shipped["calibrated"].items()
                    if EXCLUDED.get(sel) in base
                ),
                default=0.0,
            ),
            "probabilities": shipped["calibrated"],
            "uncertainty": {
                **prediction["uncertainty"],
                "interval": shipped.get("interval", {}),
                "predictive_status": shipped.get("predictive_status", "experimental"),
                "family": market_key,
            },
            "derived_from": "1x2",
        }
    parts = DERIVED_FROM_1X2[market_key]
    probabilities = prediction["probabilities"]
    interval = prediction["uncertainty"].get("interval", {})
    derived_p = {
        selection: sum(probabilities[k] for k in keys)
        for selection, keys in parts.items()
        if all(k in probabilities for k in keys)
    }
    derived_i = {
        selection: [
            max(0.0, sum(interval[k][0] for k in keys)),
            min(1.0, sum(interval[k][1] for k in keys)),
        ]
        for selection, keys in parts.items()
        if all(k in interval for k in keys)
    }
    uncertainty = prediction["uncertainty"]
    return {
        **prediction,
        "probabilities": derived_p,
        "uncertainty": {
            **uncertainty,
            "interval": derived_i,
            "family": market_key,
            # a derived market has its own validation status (never the one of 1X2)
            "predictive_status": (uncertainty.get("derived_status") or {}).get(
                market_key, "experimental"
            ),
        },
        "derived_from": "1x2",
    }


EXCLUDED = {"home_draw": "away", "draw_away": "home", "home_away": "draw"}
SUM_TOLERANCE = 1e-3  # rounding only: exclusive outcomes sum to one, double chance to two
DERIVED_SUM = {"double_chance": 2.0}


def market_problem(prediction: dict, market_key: str) -> str | None:
    """Why the probabilities of one market cannot be trusted (None = consistent)."""
    probs = prediction["probabilities"]
    if any(not isinstance(v, int | float) or not 0.0 <= v <= 1.0 for v in probs.values()):
        return "prawdopodobieństwo poza zakresem 0–1"
    gap = prediction.get("coherence_gap") or 0.0
    if gap > SUM_TOLERANCE:
        return f"podwójna szansa niezgodna z 1X2 tego samego modelu (różnica {gap:.1%})"
    expected = DERIVED_SUM.get(market_key, 1.0)
    complete = COMPLETE_SETS.get(market_key) or set(DERIVED_FROM_1X2.get(market_key, {}))
    if complete and set(probs) == complete:
        total = sum(probs.values())
        if abs(total - expected) > SUM_TOLERANCE:
            return f"suma prawdopodobieństw {total:.3f} zamiast {expected:.0f}"
    return None


def consistent_interval(p: float | None, low: float | None, high: float | None) -> bool:
    """0 ≤ P_low ≤ P ≤ P_high ≤ 1 (a small tolerance only for floating-point noise)."""
    if p is None or low is None or high is None:
        return False
    eps = 1e-9
    return -eps <= low <= p + eps and p - eps <= high <= 1 + eps


def break_even_probability(odds: Decimal, stake_tax_rate: Decimal) -> float:
    """Win probability at which net EV is zero: 1 / (odds × (1 − stake tax)).

    Above 1.0 a win cannot even return the stake (STS: odds below about 1.14).
    """
    return 1 / (float(odds) * float(1 - stake_tax_rate))


def calibration_note(prediction: dict | None, p: float | None) -> str:
    """How often out-of-sample forecasts in the same probability bin came true: the
    selection window of protocol v2, the holdout of older versions."""
    if prediction is None or p is None:
        return "—"
    validation = prediction["validation"]
    v2 = validation.get("protocol") in ("v2", "v3")  # TASK 012: v3 is decision-free too
    where = "okno selekcji" if v2 else "holdout"
    family = prediction["uncertainty"].get("family")
    ranges = ((validation.get("families") or {}).get(family) or {}).get("ranges")
    if ranges:
        for row in ranges:
            low, high = (float(x.strip("%")) / 100 for x in row["range"].split("–"))
            if row["n"] and low <= p < high + (1e-9 if high == 1.0 else 0):
                lo, hi = row["ci95"]
                return (
                    f"{where} {row['range']}: trafione {row['freq']:.0%} "
                    f"(95%: {lo:.0%}–{hi:.0%}, n={row['n']})"
                )
        return f"brak prognoz ({where}) w tym przedziale"
    if prediction.get("derived_from"):
        return "prognoza pochodna z 1X2 — bez osobnej kalibracji"
    sample = (validation.get("decision") if v2 else validation.get("holdout")) or {}
    for row in sample.get("reliability", []):
        if row["n"] and row["low"] <= p < row["high"]:
            return (
                f"{where} {row['low']:.0%}–{row['high']:.0%}: trafione {row['freq']:.0%} "
                f"(n={row['n']})"
            )
    return "brak danych kalibracji w tym przedziale"


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
    event_status: str = "unknown"
    predictive_status: str = "brak prognozy"  # accuracy/calibration, never the economic edge
    break_even: float | None = None  # win probability needed for zero net EV (STS tax)
    calibration: str = "—"
    odds_age_hours: float | None = None
    probability_ok: bool = True  # False: inconsistent bounds — never ranked, never in a coupon
    status_note: str = ""  # why a stored status is suspended (TASK 007 protocol v2)
    document_at: str | None = None  # time printed in the STS document of this price
    fetched_at: str | None = None  # download time (None for a manually supplied file)
    market_label: str = ""
    label_raw: str = ""  # market header exactly as printed in the PDF
    source_text: str = ""  # original PDF row
    # TASK 013 §4: the stored forecast the probability comes from (None: no forecast); a
    # derived market (double chance from 1X2) names the forecast it was derived from
    prediction_id: str | None = None

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
    fair_1x2 = {(r["event_id"], r["period"]): devig(r)[0] for r in rows if r["market_key"] == "1x2"}
    for row in rows:
        if row["market_key"] in NOT_ANALYSED:
            continue
        fair, margin = devig(row)
        if fair is None and row["market_key"] in DERIVED_FROM_1X2:
            # STS de-margined 1X2 of the same match and period, combined like the forecast
            base = fair_1x2.get((row["event_id"], row["period"]))
            parts = DERIVED_FROM_1X2[row["market_key"]]
            if base and set(row["selections"]) == set(parts):
                fair = {sel: sum(base[k] for k in keys) for sel, keys in parts.items()}
                margin = sum(1 / float(q["price"]) for q in row["selections"].values()) - 2
        start = datetime.fromisoformat(row["start_at"]) if row["start_at"] else None
        event_day = (
            start.date()
            if start
            else (date.fromisoformat(row["offer_date"]) if row["offer_date"] else None)
        )
        status = (
            event_status(
                row["start_at"], now, sport=row["sport"], result_status=row.get("result_status")
            ),
            *odds_freshness(row["valid_at"], now, max_age),
        )
        prediction = predictions.get(
            (row["event_id"], row["market_key"], row["period"], row["line"])
        )
        if prediction is None and row["market_key"] in DERIVED_FROM_1X2:
            base = predictions.get((row["event_id"], "1x2", row["period"], None))
            if base is not None:
                prediction = derive_from_1x2(base, row["market_key"])
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
    event_state, odds_state, age_hours = status
    if row.get("presence") == "withdrawn":
        blocking.append("Rynek wycofany — nie występuje w nowszej ofercie tego samego rodzaju")
    elif row.get("presence") == "unconfirmed":
        unproven.append("Rynek niepotwierdzony w nowszej ofercie dnia — pobierz ofertę ponownie")
    if event_state != "before_start":
        blocking.append(
            {
                "finished": "Wydarzenie zakończone — kurs nieaktualny",
                "postponed": "Wydarzenie przełożone — kurs nieaktualny",
                "cancelled": "Wydarzenie anulowane — kurs nieaktualny",
            }.get(
                event_state,
                "Kurs wygasł — wydarzenie już się rozpoczęło"
                if row["start_at"]
                else "Nieznana godzina rozpoczęcia wydarzenia",
            )
        )
    if policy.require_fresh_quote and odds_state == "unconfirmed":
        unproven.append(f"Kurs niepotwierdzony od {age_hours:.0f} h — pobierz aktualną ofertę STS")
    elif policy.require_fresh_quote and odds_state == "unknown":
        unproven.append("Nieznany czas obserwacji kursu")
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
    probability_ok = True
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
        problem = market_problem(prediction, row["market_key"])
        if problem:
            blocking.append(f"Niespójne prawdopodobieństwa rynku: {problem} — poza rankingiem")
            probability_ok = False
        interval = prediction["uncertainty"].get("interval", {}).get(selection)
        if interval and probability_ok:
            p_low, p_high = interval
            if not consistent_interval(p_model, p_low, p_high):
                # e.g. bounds computed before a normalisation (forecasts before TASK 007)
                blocking.append(
                    "Niespójny przedział prawdopodobieństwa (P_low ≤ P ≤ P_high nie zachodzi)"
                    " — selekcja wyłączona z rankingu"
                )
                probability_ok = False
                p_low = p_high = None
        validation = prediction["validation"]
        v2 = validation.get("protocol") in ("v2", "v3")
        sample = (validation.get("decision") if v2 else validation.get("holdout")) or {}
        # TASK 011: without a benchmark on the same matches the comparison is unknown, never a
        # default "False" (the prospective comparison lives in prospective_eval)
        market_text = (
            "nieznane (brak porównania na tych samych meczach)"
            if validation.get("market_benchmark") is None
            else ("model lepszy" if validation.get("beats_market") else "model nie lepszy")
        )
        validation_text = (
            f"{'okno selekcji' if v2 else 'holdout'} n={sample.get('n')}, "
            f"log loss={sample.get('log_loss')}, ECE={sample.get('ece')}, "
            f"rynek: {market_text}"
        )
        if policy.require_validated_model and model_status != "validated":
            unproven.append("Model bez zatwierdzonej walidacji — dodatnie EV nie jest przewagą")
        if p_model is None:
            blocking.append("Prognoza nie obejmuje tej selekcji")
        if p_low is None and probability_ok:
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
        freshness=odds_state,
        presence=row.get("presence", "current"),
        tier=tier,
        reasons=reasons,
        warnings=tuple(warnings),
        explanation=explanation,
        event_status=event_state,
        break_even=(
            break_even_probability(odds, profile.cost.stake_tax_rate)
            if profile_ok and payoff in {None, "binary"}
            else None
        ),
        calibration=calibration_note(prediction, p_model),
        predictive_status=(
            prediction["uncertainty"].get("predictive_status", "experimental")
            if prediction
            else "brak prognozy"
        ),
        odds_age_hours=age_hours,
        probability_ok=probability_ok,
        status_note=(prediction["uncertainty"].get("status_note", "") if prediction else ""),
        document_at=row.get("valid_at"),
        fetched_at=row.get("fetched_at"),
        market_label=market_label(row["market_key"], row["period"], row["line"]),
        label_raw=row.get("label_raw", ""),
        source_text=row.get("source_text", ""),
        prediction_id=(prediction.get("id") if prediction else None),
    )


def _net(p: float, stake: Decimal, odds: Decimal, profile: BookmakerProfile, day: date) -> Decimal:
    distribution = {Outcome.WIN: p, Outcome.LOSS: 1 - p}
    return net_ev(distribution, stake, odds, profile.cost, on_date=day).quantize(Decimal("0.0001"))
