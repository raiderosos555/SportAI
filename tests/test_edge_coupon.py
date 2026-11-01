"""EV gross/net, tax profile, rules and model gates, SOLO ranking and AKO (STS ONLY).

Model probabilities below are synthetic test inputs, not forecasts of real matches.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from sports_edge.analysis.coupon import NO_EDGE, IndependentEventsModel, build_daily_coupon
from sports_edge.analysis.edge_engine import evaluate
from sports_edge.analysis.policy import Policy, load_policy
from sports_edge.edge import binary_ev
from sports_edge.models.registry import latest_predictions, register_version, save_prediction
from sports_edge.repository import offer_table
from sports_edge.settlement import Outcome, settle

pytest.importorskip("pymupdf")

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
GOOD_VALIDATION = {
    "holdout": {"n": 500, "ece": 0.01, "log_loss": 0.95},
    "market_benchmark": {
        "same_events": True,
        "n_events": 500,
        "model_log_loss": 0.95,
        "market_log_loss": 0.97,
    },
    "leakage_checked": True,
}
INPUTS = {
    "info_cutoff": "2026-10-30T00:00:00+00:00",
    "data_snapshot": "synthetic-test",
    "code_state": "test",
}


@pytest.fixture
def policy() -> Policy:
    return load_policy(ROOT / "config/coupon_policy.toml")


def event_id(store, home):
    return store.rows("SELECT id FROM events WHERE home=?", (home,))[0]["id"]


def version(store, policy, validation=GOOD_VALIDATION, name="good"):
    version_id, status, _ = register_version(
        store,
        family="synthetic_test_model",
        sport="football",
        description="test only",
        version=name,
        created_at=NOW - timedelta(days=1),
        trained_through=NOW - timedelta(days=2),
        training_data={"synthetic": True},
        validation=validation,
        policy=policy,
    )
    return version_id, status


def predict(store, version_id, home, probabilities, interval, market=("1x2", "FT")):
    save_prediction(
        store,
        model_version_id=version_id,
        event_id=event_id(store, home),
        market_key=market[0],
        period=market[1],
        line=None,
        probabilities=probabilities,
        uncertainty={"interval": interval},
        inputs=INPUTS,
        kind="prospective",
        created_at=NOW - timedelta(minutes=30),
    )


def candidates(store, policy, profiles, rules, *, now=NOW, profile=None):
    return evaluate(
        offer_table(store, day="2026-10-31"),
        predictions=latest_predictions(store, as_of=now),
        profile=profile or profiles["sts"],
        market_rules=rules,
        policy=policy,
        now=now,
        max_age=timedelta(minutes=180),
        stake=Decimal("10.00"),
    )


def pick(found, home, selection, market="1x2"):
    return next(
        c
        for c in found
        if c.event.startswith(home) and c.selection == selection and c.market_key == market
    )


def test_gross_and_net_ev_formulas(profiles):
    assert binary_ev(0.55, 2.4) == pytest.approx(0.55 * 2.4 - 1)
    sts = profiles["sts"].cost
    win = settle(Decimal("10.00"), Decimal("2.40"), Outcome.WIN, sts, on_date=date(2026, 10, 31))
    assert win.stake_tax == Decimal("1.20") and win.payout == Decimal("21.12")
    ev = (Decimal("0.55") * win.net_profit + Decimal("0.45") * Decimal("-10")) / Decimal("10")
    assert ev == pytest.approx(Decimal(str(0.55 * 2.4 * 0.88 - 1)))


def test_only_the_sts_profile_is_active(profiles):
    assert set(profiles) == {"sts"}
    sts = profiles["sts"]
    assert sts.tax_verified and sts.cost.stake_tax_rate == Decimal("0.12")
    assert sts.cost_valid_on(date(2026, 11, 30)) and not sts.cost_valid_on(date(2026, 12, 1))
    big = settle(
        Decimal("1000.00"), Decimal("3.00"), Outcome.WIN, sts.cost, on_date=date(2026, 11, 1)
    )
    assert big.payout_tax == Decimal("264.00") and big.net_profit == Decimal("1376.00")
    assert sts.sections["limits"]["max_payout_pln"] == "1000000"
    assert (ROOT / "config/archive/bookmakers/superbet.toml").is_file()  # archived, not deleted


def test_no_model_means_no_coupon(store, import_sts, policy, profiles, rules):
    found = candidates(store, policy, profiles, rules)
    assert found and not any(c.accepted for c in found)
    legia = pick(found, "Legia", "home")
    assert legia.tier == "rejected" and legia.model_status == "brak prognozy"
    assert "Brak niezależnej prognozy modelu dla tego rynku" in legia.reasons
    assert legia.net_multiplier == Decimal("2.1120") and legia.rules_status == "zweryfikowane"
    assert legia.p_market == pytest.approx((1 / 2.40) / (1 / 2.40 + 1 / 3.30 + 1 / 2.90))
    report = build_daily_coupon(found, policy, day="2026-10-31", now=NOW)
    assert report.headline == NO_EDGE and not report.solo and not report.experimental
    assert report.counts["with_forecast"] == 0


def test_validated_positive_ev_is_ranked_solo(store, import_sts, policy, profiles, rules):
    version_id, status = version(store, policy)
    assert status == "validated"
    predict(
        store,
        version_id,
        "Legia Warszawa",
        {"home": 0.55, "draw": 0.25, "away": 0.20},
        {"home": [0.50, 0.60]},
    )
    predict(
        store,
        version_id,
        "Górnik Zabrze",
        {"home": 0.62, "draw": 0.22, "away": 0.16},
        {"home": [0.60, 0.64]},
    )
    found = candidates(store, policy, profiles, rules)
    legia = pick(found, "Legia", "home")
    assert legia.tier == "validated", legia.reasons
    assert legia.ev_gross == pytest.approx(0.32)
    assert (legia.ev_net, legia.ev_net_low) == (Decimal("0.1616"), Decimal("0.0560"))
    report = build_daily_coupon(found, policy, day="2026-10-31", now=NOW)
    assert [c.event for c in report.solo] == [
        "Górnik Zabrze - Śląsk Wrocław",
        "Legia Warszawa - Lech Poznań",
    ]
    assert report.headline != NO_EDGE and report.counts["validated"] == 2
    away = pick(found, "Legia", "away")  # negative EV and no interval: never a candidate
    assert away.tier == "rejected"


def test_untrusted_model_gives_experimental_candidates_only(
    store, import_sts, policy, profiles, rules
):
    weak = {**GOOD_VALIDATION, "market_benchmark": None}
    version_id, status = version(store, policy, weak, name="weak")
    assert status == "experimental"
    predict(
        store,
        version_id,
        "Legia Warszawa",
        {"home": 0.55, "draw": 0.25, "away": 0.20},
        {"home": [0.50, 0.60]},
    )
    found = candidates(store, policy, profiles, rules)
    legia = pick(found, "Legia", "home")
    assert legia.tier == "experimental"
    assert "Model bez zatwierdzonej walidacji — dodatnie EV nie jest przewagą" in legia.reasons
    report = build_daily_coupon(found, policy, day="2026-10-31", now=NOW)
    assert report.headline == NO_EDGE and report.experimental == (legia,)
    assert "EKSPERYMENTALNY" in legia.explanation["Decyzja"]


def test_unverified_rules_and_tax_block_validation(store, import_sts, policy, profiles, rules):
    version_id, _ = version(store, policy)
    predict(
        store,
        version_id,
        "Kowalski Jan",
        {"home": 0.70, "away": 0.30},
        {"home": [0.65, 0.75]},
        market=("match_winner", "MATCH"),
    )
    predict(
        store,
        version_id,
        "Legia Warszawa",
        {"home": 0.55, "draw": 0.25, "away": 0.20},
        {"home": [0.50, 0.60]},
    )
    tennis = pick(candidates(store, policy, profiles, rules), "Kowalski", "home", "match_winner")
    assert tennis.rules_status == "niezweryfikowane" and tennis.tier == "experimental"
    assert "Zasady rozliczenia rynku STS niezweryfikowane" in tennis.reasons
    assumed = replace(profiles["sts"], tax_verified=False)
    legia = pick(candidates(store, policy, profiles, rules, profile=assumed), "Legia", "home")
    assert legia.tier == "experimental" and "Profil podatkowy niezweryfikowany" in legia.reasons


def test_stale_expired_and_void_markets_are_rejected(store, import_sts, policy, profiles, rules):
    version_id, _ = version(store, policy)
    predict(
        store,
        version_id,
        "Legia Warszawa",
        {"home": 0.55, "draw": 0.25, "away": 0.20},
        {"home": [0.50, 0.60]},
    )
    stale = pick(
        candidates(store, policy, profiles, rules, now=NOW + timedelta(hours=4)), "Legia", "home"
    )
    assert "Kurs nieaktualny — przekroczony maksymalny wiek oferty" in stale.reasons
    expired = pick(
        candidates(store, policy, profiles, rules, now=datetime(2026, 10, 31, 20, tzinfo=UTC)),
        "Legia",
        "home",
    )
    assert expired.tier == "rejected"
    assert "Kurs wygasł — wydarzenie już się rozpoczęło" in expired.reasons
    dnb = pick(candidates(store, policy, profiles, rules), "Real", "home", "draw_no_bet")
    assert dnb.tier == "rejected"


def test_ako_requires_explicit_joint_model(store, import_sts, policy, profiles, rules):
    version_id, _ = version(store, policy)
    predict(
        store,
        version_id,
        "Legia Warszawa",
        {"home": 0.55, "draw": 0.25, "away": 0.20},
        {"home": [0.50, 0.60]},
    )
    predict(
        store,
        version_id,
        "Górnik Zabrze",
        {"home": 0.62, "draw": 0.22, "away": 0.16},
        {"home": [0.60, 0.64]},
    )
    found = candidates(store, policy, profiles, rules)
    report = build_daily_coupon(found, policy, day="2026-10-31", now=NOW)
    assert not report.ako.created and "wyłączona" in report.ako.reason
    enabled = replace(policy, ako_enabled=True)
    report = build_daily_coupon(found, enabled, day="2026-10-31", now=NOW)
    assert not report.ako.created and "prawdopodobieństwa łącznego" in report.ako.reason
    report = build_daily_coupon(
        found,
        enabled,
        day="2026-10-31",
        now=NOW,
        joint_model=IndependentEventsModel(),
        cost=profiles["sts"].cost,
        stake=Decimal("10.00"),
    )
    assert report.ako.created and report.ako.joint_probability == pytest.approx(0.55 * 0.62)
    assert float(report.ako.ev_net) == pytest.approx(0.55 * 0.62 * 4.8 * 0.88 - 1, abs=1e-4)
    same_event = [c for c in found if c.event.startswith("Legia") and c.accepted] * 2
    assert IndependentEventsModel().joint_probability(same_event) is None
