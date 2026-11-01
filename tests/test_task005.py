"""TASK 005: probability-first STS coupons.

Team identities, Dixon–Coles, calibration and predictive validation, the coupon builder
(profiles, payout after tax, dependence between legs), TASK 004 fixes A–D, PitchAPI
statistics and lineups, as-of features, saved proposals, automatic settlement, effectiveness
and the whole flow from the STS PDF to the report.

Every record is a labelled SYNTHETIC fixture (invented teams, scores, statistics and odds).
PitchAPI and football-data.org run through fake openers, never the network. A passing flow is
a functional check, not evidence of predictive skill or profit.
"""

import json
import math
import random
import urllib.error
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_task004 import _Response, fd, match, noop, provider, recent_matches, synthetic_history

from sports_edge import services
from sports_edge.analysis.coupon import build_daily_coupon
from sports_edge.analysis.coupon_builder import VALIDATED, build_coupons, eligibility
from sports_edge.analysis.edge_engine import Candidate, derive_from_1x2
from sports_edge.analysis.policy import Policy
from sports_edge.bookmakers import load_profiles
from sports_edge.datasources.base import FetchOutcome, ProviderStatus, ResultRecord
from sports_edge.datasources.pitchapi import (
    PitchApiProvider,
    parse_lineups,
    parse_stats,
    season_label,
)
from sports_edge.ingestion.types import iso
from sports_edge.matching import TeamIdentities, load_team_identities
from sports_edge.models import calibration, goals
from sports_edge.models.elo import HistoricalMatch
from sports_edge.models.registry import latest_predictions, register_version, save_prediction
from sports_edge.offers.sources import SourceBlocked
from sports_edge.offers.text import load_normalizer
from sports_edge.performance import (
    _streaks,
    coupon_performance,
    probability_bin,
    selection_outcomes,
    selection_summary,
)
from sports_edge.probability_lab import (
    ProbabilityPolicy,
    load_probability_policy,
    predictive_status,
)
from sports_edge.results_engine import apply_identities, source_family, update_results
from sports_edge.sports_intel import (
    collect_match_stats,
    features_as_of,
    fetch_lineups,
    latest_lineups,
)
from sports_edge.sportsdata import (
    Split,
    import_results,
    import_results_csv,
    market_benchmark,
    run_elo_experiment,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 31, 11, 30, tzinfo=UTC)
NAMES = load_normalizer(ROOT / "config" / "aliases.toml")
STS = load_profiles(ROOT / "config")["sts"]
RULES = {
    "min_holdout_n": 300,
    "max_ece": 0.03,
    "require_beats_baseline": True,
    "max_season_ece": 0.06,
    "forbid_overconfident_bins": True,
}


def cand(event: str, odds: str, p: float, low: float, **extra) -> Candidate:
    """SYNTHETIC eligible selection: fresh price, verified rules, before the start."""
    price = Decimal(odds)
    fields = {
        "key": f"{event}|{odds}|{p}",
        "event_id": event,
        "market_id": f"m-{event}-{odds}",
        "quote_set_id": "q",
        "odds_id": f"o-{event}-{odds}-{p}",
        "selection_id": f"s-{event}-{odds}",
        "event": f"{event} - Rywal Testowy",
        "sport": "football",
        "league": "LIGA SYNTETYCZNA",
        "start_at": "2026-10-31T19:00:00+00:00",
        "offer_code": "9001",
        "market_key": "total",
        "period": "FT",
        "line": "1.5",
        "selection": "over",
        "odds": price,
        "implied": 1 / float(price),
        "net_multiplier": Decimal("0.88") * price,
        "margin": 0.05,
        "p_market": None,
        "p_model": p,
        "p_low": low,
        "p_high": min(1.0, p + 0.03),
        "model": "dixon_coles_goals",
        "model_version_id": "v-test",
        "model_status": "experimental",
        "validation": "",
        "ev_gross": None,
        "ev_net": None,
        "ev_net_low": None,
        "quality": 1.0,
        "data_status": "ok",
        "rules_status": "zweryfikowane",
        "tax_status": "ok",
        "freshness": "fresh",
        "presence": "current",
        "tier": "experimental",
        "reasons": (),
        "event_status": "before_start",
        "predictive_status": VALIDATED,
        "break_even": 1 / (float(price) * 0.88),
        "calibration": "holdout 70%–80%: trafione 76% (SYNTETYCZNE)",
        "market_label": "Suma goli — czas regulaminowy (1.5)",
    }
    fields.update(extra)
    return Candidate(**fields)


def policy(per_event: int = 3) -> ProbabilityPolicy:
    return ProbabilityPolicy(
        version="test.p1",
        validation=RULES,
        profiles={
            "safe": {
                "label": "Bezpieczny",
                "max_legs": 2,
                "min_leg_probability": 0.75,
                "min_total_odds": 1.25,
                "min_net_profit_share": 0.08,
                "require_predictive_validation": True,
            },
            "balanced": {
                "label": "Zrównoważony",
                "max_legs": 3,
                "min_leg_probability": 0.65,
                "min_total_odds": 1.60,
                "min_net_profit_share": 0.35,
                "require_predictive_validation": True,
            },
            "aggressive": {
                "label": "Agresywny",
                "max_legs": 4,
                "min_leg_probability": 0.55,
                "min_total_odds": 2.50,
                "min_net_profit_share": 1.00,
                "require_predictive_validation": False,
            },
        },
        builder={
            "stake": "10.00",
            "candidates": 25,
            "per_event": per_event,
            "max_odds_per_leg": 4.0,
        },
    )


def build(candidates, rules: ProbabilityPolicy | None = None, **kwargs):
    return build_coupons(
        candidates, rules or policy(**kwargs), cost=STS.cost, on_date=date(2026, 10, 31)
    )


# ---------------------------------------------------------------- policy and builder


def test_probability_policy_file_has_three_profiles_and_its_own_version():
    rules = load_probability_policy(ROOT / "config" / "probability_policy.toml")
    assert list(rules.profiles) == ["safe", "balanced", "aggressive"]
    assert rules.version and rules.builder["per_event"] >= 1
    assert rules.validation["min_holdout_n"] >= 300 and rules.validation["max_ece"] <= 0.03
    assert rules.profiles["safe"]["require_predictive_validation"] is True
    assert rules.profiles["balanced"]["require_predictive_validation"] is True
    assert rules.profiles["aggressive"]["require_predictive_validation"] is False


def test_safe_profile_takes_the_most_likely_single_that_still_pays():
    a, b = cand("A", "1.30", 0.83, 0.80), cand("B", "1.35", 0.81, 0.78)
    safe = build([a, b]).proposals["safe"]
    assert [leg.odds_id for leg in safe.legs] == [a.odds_id]  # 0.80 alone beats 0.80 × 0.78
    assert safe.p_conservative == pytest.approx(0.80) and safe.p_calibrated == pytest.approx(0.83)
    assert safe.total_odds == Decimal("1.3000")
    assert safe.payout == Decimal("11.44") and safe.net_profit == Decimal("1.44")  # 0.88 × 13
    assert safe.validated
    assert not any("niezależność" in w for w in safe.warnings)
    assert any("EV netto" in w for w in safe.warnings)  # high chance is not profit: said aloud


def test_balanced_profile_combines_different_matches_to_reach_its_odds():
    a, b = cand("A", "1.30", 0.83, 0.80), cand("B", "1.35", 0.81, 0.78)
    balanced = build([a, b]).proposals["balanced"]
    assert {leg.event_id for leg in balanced.legs} == {"A", "B"}
    assert balanced.total_odds == Decimal("1.7550")
    assert balanced.p_conservative == pytest.approx(0.80 * 0.78)
    assert balanced.p_calibrated == pytest.approx(0.83 * 0.81)
    assert any("niezależność" in w for w in balanced.warnings)
    assert "niezależności" in balanced.joint_basis


def test_a_likelier_single_beats_every_combination():
    a, b = cand("A", "1.30", 0.83, 0.80), cand("B", "1.35", 0.81, 0.78)
    c = cand("C", "1.60", 0.72, 0.70)
    balanced = build([a, b, c]).proposals["balanced"]
    assert [leg.event_id for leg in balanced.legs] == ["C"]  # 0.70 > 0.80 × 0.78


def test_unvalidated_markets_only_enter_the_aggressive_simulation():
    legs = [cand(e, "1.70", 0.72, 0.68, predictive_status="experimental") for e in "ABC"]
    report = build(legs)
    assert report.proposals["safe"] is None and report.proposals["balanced"] is None
    assert "zwalidowanych predykcyjnie" in report.refusals["balanced"]
    aggressive = report.proposals["aggressive"]
    assert aggressive.level == "experimental" and not aggressive.validated
    assert any("symulacja" in w for w in aggressive.warnings)
    assert len(aggressive.legs) == 2  # 1.70² = 2.89 ≥ 2.50 with the fewest legs
    assert aggressive.net_profit >= aggressive.stake


def test_a_win_that_does_not_return_the_stake_never_enters_a_coupon():
    losing = cand("A", "1.12", 0.97, 0.95)  # 1.12 × 0.88 < 1: a win loses money
    assert eligibility(losing, 4.0) == "wygrana nie pokrywa stawki (podatek)"
    thin = cand("B", "1.20", 0.93, 0.90)
    report = build([losing, thin])
    assert report.excluded == {"wygrana nie pokrywa stawki (podatek)": 1}
    assert report.proposals["safe"] is None
    assert "zysku netto" in report.refusals["safe"]


def test_net_profit_after_the_stake_coefficient_is_a_hard_constraint():
    rules = policy()
    rules.profiles["safe"] = {
        **rules.profiles["safe"],
        "min_total_odds": 1.0,
        "min_net_profit_share": 0.5,  # at least 5 zł on 10 zł
    }
    a, b = cand("A", "1.30", 0.83, 0.80), cand("B", "1.35", 0.81, 0.78)
    safe = build([a, b], rules).proposals["safe"]
    assert len(safe.legs) == 2  # a single pays only 1.44 zł
    assert safe.net_profit == Decimal("5.44")


def test_two_markets_of_one_match_are_never_multiplied():
    dc = cand("A", "1.25", 0.92, 0.90, market_key="double_chance", selection="home_draw")
    over = cand("A", "1.40", 0.86, 0.84)
    report = build([dc, over])
    assert report.proposals["balanced"] is None  # 1.25 × 1.40 would use one match twice
    assert report.proposals["aggressive"] is None
    for proposal in report.proposals.values():
        if proposal is not None:
            assert len({leg.event_id for leg in proposal.legs}) == len(proposal.legs)


def test_long_odds_stale_prices_started_events_and_missing_bounds_stay_out():
    report = build(
        [
            cand("A", "4.50", 0.62, 0.58),
            cand("B", "1.50", 0.80, 0.77, freshness="unconfirmed"),
            cand("C", "1.50", 0.80, 0.77, event_status="in_play"),
            cand("D", "1.50", 0.80, 0.77, p_low=None),
            cand("E", "1.50", 0.80, 0.77),
        ]
    )
    assert [c.event_id for c in report.pool] == ["E"]
    assert report.excluded == {
        "kurs powyżej 4.0": 1,
        "kurs niepotwierdzony (stara migawka)": 1,
        "wydarzenie nie jest przed rozpoczęciem": 1,
        "brak konserwatywnego oszacowania": 1,
    }


def test_alternatives_of_one_match_can_build_a_better_coupon():
    best = cand("X", "1.15", 0.85, 0.82, market_key="double_chance", selection="home_draw")
    alternative = cand("X", "1.45", 0.79, 0.76)
    other = cand("Y", "1.30", 0.83, 0.80)
    assert build([best, alternative, other], per_event=1).proposals["balanced"] is None
    found = build([best, alternative, other]).proposals["balanced"]
    assert {leg.odds_id for leg in found.legs} == {alternative.odds_id, other.odds_id}
    assert found.total_odds == Decimal("1.8850")


# ---------------------------------------------------------------- calibration and validation


def test_isotonic_calibration_is_monotone_and_wilson_bounds_cover_the_rate():
    rng = random.Random(3)
    p = [rng.uniform(0.3, 0.95) for _ in range(3000)]
    y = [int(rng.random() < max(0.0, v - 0.08)) for v in p]  # SYNTHETIC overconfident model
    blocks = calibration.isotonic_fit(p, y)
    values = [b["value"] for b in blocks]
    assert values == sorted(values) and sum(b["n"] for b in blocks) == 3000
    value, n = calibration.isotonic_apply(blocks, 0.8)
    assert 0.65 < value < 0.78 and n > 0
    low, high = calibration.wilson_interval(76, 100)
    assert low < 0.76 < high
    assert calibration.wilson_interval(0, 0) == (0.0, 1.0)
    rows = calibration.bin_report(p, y, (0.5, 0.6, 0.7, 0.8, 0.9, 1.0))
    assert any(r["overconfident"] for r in rows)
    assert not any(r["underconfident"] for r in rows)


def _info(n=1000, ece=0.01, log_loss=0.60, base=0.65, season_ece=0.02, overconfident=False):
    return {
        "calibrated": {"n": n, "ece": ece, "log_loss": log_loss},
        "baseline_log_loss": base,
        "by_season": {"2025-26": {"ece": season_ece}},
        "ranges": [{"range": "70%–80%", "overconfident": overconfident}],
    }


def test_predictive_validation_needs_every_rule_on_the_holdout():
    assert predictive_status(_info(), RULES)["status"] == VALIDATED
    for bad, words in (
        ({"n": 100}, "Za mało"),
        ({"ece": 0.05}, "ECE"),
        ({"log_loss": 0.70}, "bazowego"),
        ({"season_ece": 0.09}, "Sezon"),
        ({"overconfident": True}, "Nadmierna pewność"),
    ):
        out = predictive_status(_info(**bad), RULES)
        assert out["status"] == "experimental", bad
        assert any(words in reason for reason in out["reasons"]), (bad, out["reasons"])
    assert predictive_status(_info(log_loss=0.648), RULES)["note"]  # minimal skill is labelled


# ---------------------------------------------------------------- Dixon–Coles


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, product = math.exp(-lam), 0, rng.random()
    while product > limit:
        k += 1
        product *= rng.random()
    return k


def _goal_matches(n: int = 600, seed: int = 11) -> list[goals.GoalsMatch]:
    """SYNTHETIC league: "Drużyna 0" attacks and defends much better than the rest."""
    rng = random.Random(seed)
    teams = [f"Drużyna {i}" for i in range(12)]
    attack = {t: 0.5 if i == 0 else rng.gauss(0, 0.1) for i, t in enumerate(teams)}
    defence = {t: 0.4 if i == 0 else rng.gauss(0, 0.1) for i, t in enumerate(teams)}
    start = date(2024, 9, 1)
    out = []
    for k in range(n):
        home, away = rng.sample(teams, 2)
        lam = math.exp(0.2 + 0.25 + attack[home] - defence[away])
        mu = math.exp(0.2 + attack[away] - defence[home])
        out.append(
            goals.GoalsMatch(
                (start + timedelta(days=k)).isoformat(),
                home,
                away,
                _poisson(rng, lam),
                _poisson(rng, mu),
            )
        )
    return out


def test_dixon_coles_uses_only_earlier_matches_and_its_markets_agree():
    matches = _goal_matches()
    fit_date = "2026-06-01"
    leak = goals.GoalsMatch(fit_date, "Drużyna 0", "Drużyna 1", 0, 9)  # the fit day itself
    model = goals.fit(matches + [leak], fit_date=fit_date)
    assert model is not None and model.n_matches == len(matches)
    out = goals.markets(model.score_matrix("Drużyna 0", "Drużyna 5"))
    one = out[("1x2", None)]
    assert sum(one.values()) == pytest.approx(1.0)
    dc = out[("double_chance", None)]
    assert dc["home_draw"] == pytest.approx(one["home"] + one["draw"])
    assert dc["home_away"] == pytest.approx(one["home"] + one["away"])
    for key, probs in out.items():
        if key[0] != "double_chance":
            assert sum(probs.values()) == pytest.approx(1.0), key
    assert out[("total", 1.5)]["over"] > out[("total", 2.5)]["over"] > out[("total", 3.5)]["over"]
    assert one["home"] > 0.6  # the strong team at home
    away = goals.markets(model.score_matrix("Drużyna 5", "Drużyna 0"))[("1x2", None)]
    assert away["away"] > 0.45
    assert model.score_matrix("Drużyna 0", "Nieznana") is None
    assert goals.fit(matches[:30], fit_date=fit_date) is None  # too little history


# ---------------------------------------------------------------- identities and predictions


def test_identities_merge_spellings_and_reviewed_renames_only():
    identities = TeamIdentities(NAMES, {("es.1", "Real Sociedad"): "Real Sociedad de Fútbol"})
    assert identities.key("es.1", "football", "Real Sociedad") == identities.key(
        "es.1", "football", "Real Sociedad de Fútbol"
    )
    rows = [
        {
            "competition": "es.1",
            "sport": "football",
            "match_date": "2019-05-01",
            "home": "Real Sociedad",
            "away": "Getafe CF",
        },
        {
            "competition": "es.1",
            "sport": "football",
            "match_date": "2026-10-01",
            "home": "Getafe",
            "away": "Real Sociedad de Fútbol",
        },
    ]
    merged = apply_identities(rows, identities)
    assert merged[0]["home"] == merged[1]["away"] == "Real Sociedad de Fútbol"
    assert merged[0]["home_source"] == "Real Sociedad"  # the original spelling stays
    assert merged[0]["away"] == merged[1]["home"]
    real = load_team_identities(ROOT / "config" / "team_identities.toml", NAMES)
    assert real.renames[("es.1", "Real Sociedad")] == "Real Sociedad de Fútbol"
    assert ("fr.1", "Paris FC") not in real.renames  # different clubs are never merged


def _version(store, family: str, version: str, when: datetime, validation: dict | None = None):
    version_id, _status, _reasons = register_version(
        store,
        family=family,
        sport="football",
        description="SYNTETYCZNY model testowy",
        version=version,
        created_at=when,
        trained_through=when,
        training_data={},
        validation=validation or {},
        policy=Policy(),
    )
    return version_id


def test_the_family_chosen_on_validation_serves_1x2_and_carries_its_status(store, import_sts):
    event = store.rows("SELECT * FROM events WHERE home LIKE 'Real Madrid%'")[0]
    elo = _version(store, "elo_ordered_draw", "elo-test", NOW)
    dc = _version(store, "dixon_coles_goals", "dc-test", NOW)
    for version, home, when in ((elo, 0.5, NOW), (dc, 0.4, NOW + timedelta(minutes=5))):
        save_prediction(
            store,
            model_version_id=version,
            event_id=event["id"],
            market_key="1x2",
            period="FT",
            line=None,
            probabilities={"home": home, "draw": 0.3, "away": round(0.7 - home, 6)},
            uncertainty={"interval": {}},
            inputs={"info_cutoff": iso(when), "data_snapshot": "syntetyczne", "code_state": "test"},
            kind="prospective",
            created_at=when,
        )
    as_of = NOW + timedelta(hours=1)
    key = (event["id"], "1x2", "FT", None)
    assert latest_predictions(store, as_of=as_of)[key]["model_version_id"] == dc  # newest
    preferred = latest_predictions(store, as_of=as_of, prefer={"1x2": "elo_ordered_draw"})
    assert preferred[key]["model_version_id"] == elo
    # The goals experiment judged THIS Elo version; its forecasts receive the statuses.
    _version(
        store,
        "dixon_coles_goals",
        "dc-test-2",
        NOW + timedelta(minutes=1),
        {
            "elo_version_id": elo,
            "elo_predictive": {
                "1x2": {"status": VALIDATED},
                "double_chance": {"status": "experimental"},
            },
            "elo_families": {},
        },
    )
    served = services.with_predictive_status(store, preferred)[key]
    assert served["uncertainty"]["predictive_status"] == VALIDATED
    derived = derive_from_1x2(served, "double_chance")
    assert derived["uncertainty"]["predictive_status"] == "experimental"  # its own status


def test_double_chance_from_1x2_has_its_own_status_and_conservative_bounds():
    elo_row = {
        "probabilities": {"home": 0.5, "draw": 0.3, "away": 0.2},
        "uncertainty": {
            "interval": {"home": [0.45, 0.55], "draw": [0.27, 0.33], "away": [0.17, 0.23]},
            "predictive_status": VALIDATED,
        },
    }
    dc = derive_from_1x2(elo_row, "double_chance")
    assert dc["probabilities"]["home_draw"] == pytest.approx(0.8)
    assert dc["uncertainty"]["interval"]["home_draw"] == pytest.approx([0.72, 0.88])
    assert dc["uncertainty"]["predictive_status"] == "experimental"  # never the 1X2 status
    shipped = {
        **elo_row,
        "uncertainty": {
            **elo_row["uncertainty"],
            "derived": {
                "double_chance": {
                    "calibrated": {"home_draw": 0.77, "draw_away": 0.48, "home_away": 0.75},
                    "interval": {"home_draw": [0.74, 0.80]},
                    "predictive_status": VALIDATED,
                }
            },
        },
    }
    calibrated = derive_from_1x2(shipped, "double_chance")
    assert calibrated["probabilities"]["home_draw"] == 0.77
    assert calibrated["uncertainty"]["predictive_status"] == VALIDATED


# ---------------------------------------------------------------- TASK 004 fixes A–D


class FakeResults:
    """SYNTHETIC results provider (no network)."""

    paid = False
    requires_network = False
    license = "dane syntetyczne (test)"

    def __init__(self, provider_id, *, family=None, records=(), error=None):
        self.id = provider_id
        self.name = f"Źródło testowe {provider_id}"
        if family:
            self.family = family
        self.competitions = ("es.1",)
        self.records, self.error, self.calls = list(records), error, 0

    def status(self, *, offline):
        return ProviderStatus(not offline, "aktywny (test)")

    def fetch(self, competition, date_from, date_to, *, now):
        self.calls += 1
        if self.error:
            raise SourceBlocked(self.error)
        return FetchOutcome(self.records, [(f"synthetic://{self.id}", b"{}")], 1)


def _update(store, providers, tmp_path):
    return update_results(
        store,
        providers,
        competitions=("es.1",),
        now=NOW,
        normalizer=NAMES,
        aliases={},
        offline=False,
        raw_dir=tmp_path,
    )


def test_fix_a_providers_need_unique_ids_but_may_share_a_family(store, tmp_path):
    twins = [FakeResults("api_sports_football", family="api_sports") for _ in range(2)]
    with pytest.raises(ValueError, match="unikalny"):
        _update(store, twins, tmp_path)
    assert source_family(FakeResults("api_sports_basketball", family="api_sports")) == "api_sports"
    assert source_family(FakeResults("pitchapi")) == "pitchapi"


def test_fix_b_one_event_counts_once_however_many_operators_quote_it(store):
    available = datetime(2025, 10, 6, 6, tzinfo=UTC)
    out = import_results(
        store,
        [
            {
                "match_date": "2025-10-04",
                "start_at": datetime(2025, 10, 4, 18, tzinfo=UTC),
                "home": "Orły Testowe",
                "away": "Sokoły Testowe",
                "home_score": 1,
                "away_score": 0,
                "available_at": available,
            }
        ],
        file_bytes=b"benchmark-fixture",
        file_name="synthetic.json",
        source_name="SYNTHETIC FIXTURE",
        license_note="dane syntetyczne (test)",
        sport="football",
        competition="es.1",
        season="2025-26",
        now=NOW,
        availability_basis="assumed_lag_36h",
    )
    result_id = store.rows("SELECT id FROM historical_results")[0]["id"]
    with store.transaction():
        for operator, prices in (
            ("op1", ("1.80", "3.60", "4.50")),
            ("op2", ("1.90", "3.40", "4.20")),
        ):
            for selection, price in zip(("home", "draw", "away"), prices, strict=True):
                store.insert(
                    "benchmark_odds",
                    id=f"{operator}-{selection}",
                    import_id=out["import_id"],
                    result_id=result_id,
                    operator=operator,
                    moment="closing",
                    market_key="1x2",
                    selection=selection,
                    price=price,
                    observed_at=None,
                )
    item = {
        "match": HistoricalMatch(
            available_at=available,
            match_date="2025-10-04",
            home="Orły Testowe",
            away="Sokoły Testowe",
            home_score=1,
            away_score=0,
            result_id=result_id,
        ),
        "forecast": {"home": 0.5, "draw": 0.28, "away": 0.22},
    }
    bench = market_benchmark(store, [item])
    assert bench["n_events"] == 1 and bench["operator_quotes"] == 2
    assert bench["operators"] == ["op1", "op2"]


def test_fix_c_a_failing_provider_falls_back_to_the_next_legal_source(store, tmp_path):
    record = ResultRecord(
        provider="api_sports",
        sport="football",
        competition="es.1",
        season="2026-27",
        source_event_id="77",
        home="Orły Testowe",
        away="Sokoły Testowe",
        start_at=datetime(2026, 10, 25, 19, tzinfo=UTC),
        match_date="2026-10-25",
        status="finished",
        home_score=2,
        away_score=0,
        published_at=datetime(2026, 10, 25, 21, tzinfo=UTC),
        observed_at=NOW,
        license="dane syntetyczne (test)",
    )
    failing = FakeResults("pitchapi", error="HTTP 429 — limit")
    backup = FakeResults("api_sports_football", family="api_sports", records=[record])
    report = _update(store, [backup, failing], tmp_path)  # hierarchy, not list order, decides
    entry = report["competitions"]["es.1"]
    assert failing.calls == 1 and backup.calls == 1
    assert entry["provider"] == "api_sports_football"
    assert "HTTP 429" in entry["fallback"][0]
    assert report["inserted"] == 1


def test_fix_d_unconfirmed_prices_are_listed_apart_from_the_ranking():
    fresh = cand("A", "1.40", 0.80, 0.76)
    stale = cand("B", "1.40", 0.82, 0.78, freshness="unconfirmed", odds_age_hours=26.0)
    report = build_daily_coupon([fresh, stale], Policy(), day="2026-10-31", now=NOW)
    assert [c.event_id for c in report.probable] == ["A"]
    assert [c.event_id for c in report.probable_unconfirmed] == ["B"]
    assert report.counts["probable"] == 1


# ---------------------------------------------------------------- PitchAPI, statistics, features

PL_TEAMS = ("Legia Warszawa", "Lech Poznań", "Górnik Zabrze", "Śląsk Wrocław")
PL_MATCHES = [  # SYNTHETIC: (id, kick-off UTC, home, away, score, Legia xG)
    ("m_1", "2026-10-04T16:00:00Z", "Legia Warszawa", "Górnik Zabrze", (2, 0), 1.0),
    ("m_2", "2026-10-11T16:00:00Z", "Lech Poznań", "Legia Warszawa", (1, 1), 2.0),
    ("m_3", "2026-10-18T16:00:00Z", "Legia Warszawa", "Śląsk Wrocław", (3, 1), 3.0),
    ("m_4", "2026-10-25T16:00:00Z", "Górnik Zabrze", "Legia Warszawa", (0, 1), 2.0),
    ("m_5", "2026-10-31T08:00:00Z", "Legia Warszawa", "Lech Poznań", (5, 0), 9.9),  # decision day
]


def _stats_payload(home_xg: float, away_xg: float) -> dict:
    return {
        "data": {
            "periods": [
                {
                    "period": "All",
                    "groups": [
                        {
                            "items": [
                                {
                                    "key": "expected_goals",
                                    "home": f"{home_xg}",
                                    "away": f"{away_xg}",
                                },
                                {"key": "total_shots", "home": "14", "away": "9"},
                                {"key": "ShotsOnTarget", "home": "6 (43%)", "away": "3 (33%)"},
                                {"key": "BallPossesion", "home": "58%", "away": "42%"},
                            ]
                        }
                    ],
                },
                {
                    "period": "FirstHalf",
                    "groups": [
                        {"items": [{"key": "expected_goals", "home": "0.7", "away": "0.2"}]}
                    ],
                },
            ]
        }
    }


def _lineup_payload(starters: int = 11) -> dict:
    def side(team, formation):
        return {
            "formation": formation,
            "confirmed": True,
            "lineup_type": "standard",
            "coach": {"name": f"Trener {team}"},
            "starters": [
                {"player_id": f"p{i}", "name": f"Zawodnik {team} {i}", "shirt_number": i}
                for i in range(1, starters + 1)
            ],
            "subs": [{"player_id": "s1", "name": f"Rezerwowy {team}", "shirt_number": 30}],
        }

    return {
        "data": {
            "match_id": "m_9",
            "home_team": {"id": "t1", "name": "Legia Warszawa"},
            "away_team": {"id": "t2", "name": "Lech Poznań"},
            "home": side("Legia", "4-3-3"),
            "away": side("Lech", "3-5-2"),
        }
    }


def _season_payload() -> dict:
    matches = [
        {
            "id": mid,
            "time_utc": when,
            "status": "finished",
            "home_team": {"id": f"t-{home}", "name": home},
            "away_team": {"id": f"t-{away}", "name": away},
            "score_home": score[0],
            "score_away": score[1],
        }
        for mid, when, home, away, score, _xg in PL_MATCHES
    ]
    matches.append(
        {
            "id": "m_9",
            "time_utc": "2026-10-31T19:30:00Z",
            "status": "not_started",
            "home_team": {"id": "t1", "name": "Legia Warszawa"},
            "away_team": {"id": "t2", "name": "Lech Poznań"},
            "score_home": None,
            "score_away": None,
        }
    )
    return {"data": {"matches": matches}}


def _pitch(routes: dict[str, dict], seen: list | None = None) -> PitchApiProvider:
    def open_url(request, timeout=30):
        if seen is not None:
            seen.append(request.full_url)
        assert request.get_header("X-api-key") == "synthetic-test-key"
        path = request.full_url.split("api.pitchapi.dev", 1)[1]
        for prefix, payload in routes.items():
            if path.startswith(prefix):
                return _Response(json.dumps(payload).encode())
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    return PitchApiProvider("synthetic-test-key", opener=open_url, sleep=noop)


def _pl_routes(lineups: dict | None = None) -> dict[str, dict]:
    routes = {"/v1/leagues/l_214sqK/matches": _season_payload()}
    for mid, _when, home, _away, _score, xg in PL_MATCHES:
        legia_home = home == "Legia Warszawa"
        routes[f"/v1/matches/{mid}/stats"] = _stats_payload(
            xg if legia_home else 0.8, 0.8 if legia_home else xg
        )
    routes["/v1/matches/m_9/lineups"] = lineups or _lineup_payload()
    return routes


def _seed_pl(store) -> None:
    rows = []
    for _mid, when, home, away, (h, a), _xg in PL_MATCHES:
        start = datetime.fromisoformat(when.replace("Z", "+00:00"))
        rows.append(
            {
                "match_date": start.date().isoformat(),
                "start_at": start,
                "home": home,
                "away": away,
                "home_score": h,
                "away_score": a,
                "available_at": start + timedelta(hours=2, minutes=30),
            }
        )
    import_results(
        store,
        rows,
        file_bytes=b"pl-fixture",
        file_name="synthetic.json",
        source_name="SYNTHETIC FIXTURE",
        license_note="dane syntetyczne (test)",
        sport="football",
        competition="pl.1",
        season="2026-27",
        now=NOW,
        availability_basis="source_last_updated",
    )


def test_pitchapi_results_statistics_and_lineups_parse_without_network():
    seen: list[str] = []
    pitch = _pitch(_pl_routes(), seen)
    assert season_label(date(2026, 10, 31)) == season_label(date(2027, 3, 1)) == "2026/2027"
    assert not PitchApiProvider(None).status(offline=False).active
    assert not pitch.status(offline=True).active
    out = pitch.fetch("pl.1", date(2026, 10, 20), date(2026, 11, 1), now=NOW)
    finished = sorted(
        (r.match_date, r.home, r.home_score, r.away_score) for r in out.records if r.complete
    )
    assert finished == [
        ("2026-10-25", "Górnik Zabrze", 0, 1),
        ("2026-10-31", "Legia Warszawa", 5, 0),
    ]
    scheduled = [r for r in out.records if r.status == "scheduled"]
    assert len(scheduled) == 1 and scheduled[0].home_score is None
    stats = parse_stats(pitch.get("/v1/matches/m_1/stats")[0])
    assert stats["All"]["expected_goals"] == [1.0, 0.8]
    assert stats["All"]["ShotsOnTarget"] == [6.0, 3.0] and stats["FirstHalf"]["expected_goals"]
    lineups = parse_lineups(pitch.get("/v1/matches/m_9/lineups")[0])
    assert lineups["home"]["formation"] == "4-3-3" and len(lineups["home"]["starters"]) == 11
    assert lineups["away"]["confirmed"] is True
    assert all("synthetic-test-key" not in url for url in seen)  # the key travels in a header


def test_statistics_are_stored_once_and_features_never_see_the_future(store, import_sts):
    _seed_pl(store)
    pitch = _pitch(_pl_routes())
    teams = {"Legia Warszawa"}
    out = collect_match_stats(
        store, pitch, competition="pl.1", teams=teams, now=NOW, normalizer=NAMES, aliases={}
    )
    assert out["fetched"] == 5 and out["errors"] == []
    again = collect_match_stats(
        store, pitch, competition="pl.1", teams=teams, now=NOW, normalizer=NAMES, aliases={}
    )
    assert again["fetched"] == 0 and again["already"] == 5
    rows = store.rows("SELECT * FROM stat_observations WHERE kind='event_statistics'")
    assert len(rows) == 5
    assert all(r["available_basis"] == "observed_at_fetch" for r in rows)
    assert all(r["available_at"] == iso(NOW) for r in rows)
    event = store.rows("SELECT * FROM events WHERE home='Legia Warszawa'")[0]
    snapshot = features_as_of(store, event, NOW, normalizer=NAMES, aliases={})
    legia = snapshot["features"]["teams"]["home"]
    assert legia["team"] == "Legia Warszawa"
    assert legia["form5"]["sequence"] == "WRWW"  # the 5:0 on the decision day is excluded
    assert legia["form5"]["goals_for"] == pytest.approx(1.75)
    assert legia["stats5"]["expected_goals"] == {
        "label": "xG",
        "for": 2.0,
        "against": 0.8,
        "matches": 4,
    }
    assert legia["rest_days"] == 6 and legia["matches_last_7"] == 1
    assert len(snapshot["inputs"]["stat_observations"]) == 4
    saved = store.rows("SELECT * FROM feature_snapshots WHERE event_id=?", (event["id"],))
    assert len(saved) == 1 and saved[0]["decision_time"] == iso(NOW)
    earlier = features_as_of(
        store, event, NOW - timedelta(minutes=1), normalizer=NAMES, aliases={}, save=False
    )
    assert earlier["features"]["teams"]["home"]["stats5"] == {}  # observed only at NOW
    assert earlier["features"]["teams"]["home"]["form5"]["sequence"] == "WRWW"


def test_lineups_are_fetched_on_demand_and_kept_as_observations(store, import_sts):
    _seed_pl(store)
    args = {
        "competition": "pl.1",
        "home": "Legia Warszawa",
        "away": "Lech Poznań",
        "start_at": datetime(2026, 10, 31, 19, 30, tzinfo=UTC),
        "now": NOW,
        "normalizer": NAMES,
        "aliases": {},
    }
    waiting = fetch_lineups(store, _pitch(_pl_routes(_lineup_payload(starters=0))), **args)
    assert waiting["status"] == "składy jeszcze nieopublikowane"
    assert (
        latest_lineups(store, home="Legia Warszawa", away="Lech Poznań", day="2026-10-31") is None
    )
    out = fetch_lineups(store, _pitch(_pl_routes()), **args)
    assert out["status"] == "ok" and out["confirmed"]
    stored = latest_lineups(store, home="Legia Warszawa", away="Lech Poznań", day="2026-10-31")
    assert stored["lineups"]["away"]["formation"] == "3-5-2" and stored["quality"] == "complete"
    missing = fetch_lineups(store, _pitch(_pl_routes()), **{**args, "away": "Śląsk Wrocław"})
    assert missing["status"] == "nie znaleziono meczu w terminarzu PitchAPI"


# ---------------------------------------------------------------- effectiveness


def test_streaks_drawdown_inputs_and_selection_calibration_flags():
    assert _streaks(["win", "win", "loss", "void", "loss", "loss"]) == {
        "longest_win_streak": 2,
        "longest_loss_streak": 2,
        "current_streak": "2 × przegrana",
    }
    rows = [
        {
            "outcome": "win" if i < 40 else "loss",
            "p_model": 0.9,
            "p_conservative": 0.85,
            "league": "LIGA SYNTETYCZNA",
            "sport": "football",
            "market_family": "Suma goli",
        }
        for i in range(100)
    ]
    rows.append({**rows[0], "outcome": None})  # not yet decided: ignored
    [summary] = selection_summary(rows, "range")
    assert summary["group"] == "90%–100%" and summary["n"] == 100
    assert summary["hit_rate"] == 0.4 and summary["flag"] == "zbyt optymistyczna"
    assert probability_bin(0.55) == "50%–60%" and probability_bin(0.3) == "< 50%"


# ---------------------------------------------------------------- the whole flow

LENIENT_POLICY = """
[policy]
version = "test-flow.p1"

[validation]
min_holdout_n = 300
max_ece = 0.03
require_beats_baseline = true
max_season_ece = 0.06
forbid_overconfident_bins = true

[profiles.safe]
label = "Bezpieczny (test)"
max_legs = 1
min_leg_probability = 0.30
min_total_odds = 1.20
min_net_profit_share = 0.05
require_predictive_validation = false

[builder]
stake = "10.00"
candidates = 25
per_event = 3
max_odds_per_leg = 4.0
"""


def test_flow_from_the_sts_pdf_to_saved_coupons_settlement_and_report(app_paths, sts_fixture_pdf):
    """STS PDF → results → statistics → features → models → probabilities → ranking →
    builder → saved proposal → result → automatic settlement → effectiveness report.

    A lenient SYNTHETIC test profile makes the builder produce a coupon from synthetic
    forecasts; the real profiles are tested above.
    """
    from sports_edge.settings import AppSettings

    (app_paths.config / "probability_policy.toml").write_text(LENIENT_POLICY, encoding="utf-8")
    store = services.open_store(app_paths)
    settings = AppSettings(offline_mode=False)
    services.import_pdf_bytes(
        store, app_paths, settings, sts_fixture_pdf, file_name="fixture_flow.pdf", now=NOW
    )
    import_results_csv(
        store,
        synthetic_history(date(2026, 9, 20), 1800),
        file_name="synthetic.csv",
        source_name="SYNTHETIC FIXTURE",
        license_note="dane syntetyczne (test)",
        sport="football",
        competition="es.1",
        now=NOW,
    )
    run_elo_experiment(
        store,
        competitions=("es.1",),
        split=Split("2024-09-01", "2025-09-01"),
        now=NOW,
        policy=services.policy(app_paths),
    )
    es_season = {
        "data": {
            "matches": [
                {
                    "id": f"e_{i}",
                    "time_utc": f"2026-10-{day}T19:00:00Z",
                    "status": "finished",
                    "home_team": {"id": "rm", "name": home},
                    "away_team": {"id": "xx", "name": away},
                    "score_home": 1,
                    "score_away": 0,
                }
                for i, (day, home, away) in enumerate(
                    (
                        ("11", "Real Madrid CF", "Klub Syntetyczny C CF"),
                        ("18", "FC Barcelona", "Klub Syntetyczny D CF"),
                    )
                )
            ]
        }
    }
    pitch = _pitch(
        {
            "/v1/leagues/l_0ErfuF/matches": es_season,
            "/v1/matches/e_0/stats": _stats_payload(2.1, 0.4),
            "/v1/matches/e_1/stats": _stats_payload(1.7, 0.9),
        }
    )
    payloads = {"PD": fd(recent_matches())}
    flow = services.run_daily_analysis(
        store,
        app_paths,
        settings,
        "2026-10-31",
        now=NOW,
        providers=[provider(payloads)],
        stats_provider=pitch,
    )
    stages = {s["stage"]: s for s in flow.stages}
    assert [s["stage"] for s in flow.stages] == list(services.STAGES)
    assert stages["Statystyki"]["fetched"] == 2
    assert stages["Cechy przedmeczowe"]["snapshots"] == 1  # Real Madrid - Barcelona
    assert stages["Modele"]["goals_model"]  # Dixon–Coles trained once, because it was missing
    assert stages["Kalibracja"]["families"]  # predictive status of every market family
    safe = flow.coupons.proposals["safe"]
    assert safe is not None and len(safe.legs) == 1
    leg = safe.legs[0]
    assert leg.event == "Real Madrid - Barcelona" and leg.p_low >= 0.30
    assert safe.net_profit >= Decimal("0.50")
    coupon_id = flow.coupon_ids["safe"]
    stored = store.get("coupons", coupon_id)
    assert stored["profile"] == "safe" and stored["policy_version"] == "test-flow.p1"
    assert json.loads(stored["predicted_probability"])["conservative"] == pytest.approx(
        safe.p_conservative
    )
    # The same proposal saved again (another click) is one coupon, not two.
    again = services.save_coupon_proposals(
        store, app_paths, flow.coupons, analysis_id=flow.analysis_id, now=NOW, day="2026-10-31"
    )
    assert again == flow.coupon_ids
    assert store.scalar("SELECT COUNT(*) FROM coupons") == 1
    assert services.saved_proposals(store, "2026-10-31")["safe"]["coupon_id"] == coupon_id

    payloads["PD"] = fd(
        recent_matches()
        + [
            match(
                99,
                "2026-10-31T20:00:00Z",
                "Real Madrid CF",
                "FC Barcelona",
                ft=(2, 1),
                ht=(1, 0),
            )
        ]
    )
    later = NOW + timedelta(days=1)
    update = services.update_results_data(
        store, app_paths, settings, now=later, providers=[provider(payloads)]
    )
    assert update["settlements"].get("settled") == 1
    report = coupon_performance(store)["safe"]
    assert report["coupons"] == report["settled"] == 1 and report["pending"] == 0
    assert report["avg_predicted"] == pytest.approx(safe.p_calibrated)
    outcomes = selection_outcomes(store)
    assert len(outcomes) == 1 and outcomes[0]["outcome"] in {"win", "loss"}
    assert report["wins"] == (outcomes[0]["outcome"] == "win")
    expected = safe.net_profit if report["wins"] else -safe.stake
    assert report["profit"] == expected
    assert services.saved_proposals(store, "2026-10-31")["safe"]["outcome"] == (
        "win" if report["wins"] else "loss"
    )
