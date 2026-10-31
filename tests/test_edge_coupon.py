"""EV gross/net, tax profiles, acceptance gates, SOLO ranking and AKO analysis.

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
    "n_out_of_sample": 500,
    "ece": 0.01,
    "log_loss": 0.97,
    "beats_market": True,
    "leakage_checked": True,
}


@pytest.fixture
def policy() -> Policy:
    return load_policy(ROOT / "config/coupon_policy.toml")


def event_id(store, home):
    return store.rows("SELECT id FROM events WHERE home=? AND bookmaker_id='sts'", (home,))[0]["id"]


def predict(store, policy, home, probabilities, interval, *, validation=GOOD_VALIDATION):
    version_id, status, _ = register_version(
        store,
        family="synthetic_test_model",
        sport="football",
        description="test only",
        version=f"v-{status_key(validation)}",
        created_at=NOW - timedelta(days=1),
        trained_through=NOW - timedelta(days=2),
        training_data={"synthetic": True},
        validation=validation,
        policy=policy,
    )
    save_prediction(
        store,
        model_version_id=version_id,
        event_id=event_id(store, home),
        market_key="1x2",
        period="FT",
        line=None,
        probabilities=probabilities,
        uncertainty={"interval": interval},
        inputs={"synthetic": True},
        kind="prospective",
        created_at=NOW - timedelta(minutes=30),
    )
    return status


def status_key(validation):
    return "good" if validation is GOOD_VALIDATION else "weak"


def candidates(store, policy, profiles, *, now=NOW, rules=None):
    return evaluate(
        offer_table(store, day="2026-10-31"),
        predictions=latest_predictions(store, as_of=now),
        profiles=profiles,
        policy=rules or policy,
        now=now,
        max_age=timedelta(minutes=180),
        stake=Decimal("10.00"),
    )


def pick(found, home, selection, bookmaker="sts", market="1x2"):
    return next(
        c
        for c in found
        if c.event.startswith(home)
        and c.selection == selection
        and c.bookmaker == bookmaker
        and c.market_key == market
    )


def test_gross_and_net_ev_formulas(profiles):
    assert binary_ev(0.55, 2.4) == pytest.approx(0.55 * 2.4 - 1)
    sts = profiles["sts"].cost
    win = settle(Decimal("10.00"), Decimal("2.40"), Outcome.WIN, sts, on_date=date(2026, 10, 31))
    assert win.stake_tax == Decimal("1.20") and win.payout == Decimal("21.12")
    ev = (Decimal("0.55") * win.net_profit + Decimal("0.45") * Decimal("-10")) / Decimal("10")
    assert ev == pytest.approx(Decimal(str(0.55 * 2.4 * 0.88 - 1)))


def test_tax_profiles_are_separate_and_dated(profiles):
    sts, superbet = profiles["sts"], profiles["superbet"]
    assert sts.tax_verified and not superbet.tax_verified
    assert sts.cost.stake_tax_rate == superbet.cost.stake_tax_rate == Decimal("0.12")
    assert sts.cost_valid_on(date(2026, 11, 30)) and not sts.cost_valid_on(date(2026, 12, 1))
    big = settle(
        Decimal("1000.00"), Decimal("3.00"), Outcome.WIN, sts.cost, on_date=date(2026, 11, 1)
    )
    assert big.payout_tax == Decimal("264.00") and big.net_profit == Decimal("1376.00")
    assert sts.sections["promotions"]["modelled"] is False
    assert "regulamin" in superbet.sections["settlement"]["rules_url"]


def test_no_model_means_no_coupon(store, import_both, policy, profiles):
    found = candidates(store, policy, profiles)
    assert found and not any(c.accepted for c in found)
    legia = pick(found, "Legia", "home")
    assert "Brak niezależnej prognozy modelu dla tego rynku" in legia.reasons
    assert legia.ev_net is None and legia.net_multiplier == Decimal("2.1120")
    assert legia.p_market == pytest.approx(
        (1 / 2.40) / (1 / 2.40 + 1 / 3.30 + 1 / 2.90)
    )  # benchmark only, never used as a forecast against STS
    report = build_daily_coupon(found, policy, day="2026-10-31", now=NOW)
    assert report.headline == NO_EDGE and not report.solo
    assert report.rejection_reasons["Brak niezależnej prognozy modelu dla tego rynku"] > 0


def test_validated_positive_ev_is_ranked_solo(store, import_both, policy, profiles):
    assert (
        predict(
            store,
            policy,
            "Legia Warszawa",
            {"home": 0.55, "draw": 0.25, "away": 0.20},
            {"home": [0.50, 0.60]},
        )
        == "validated"
    )
    predict(
        store,
        policy,
        "Górnik Zabrze",
        {"home": 0.62, "draw": 0.22, "away": 0.16},
        {"home": [0.60, 0.64]},
    )
    found = candidates(store, policy, profiles)
    legia = pick(found, "Legia", "home")
    assert legia.accepted, legia.reasons
    assert legia.ev_gross == pytest.approx(0.32)
    assert legia.ev_net == Decimal("0.1616")
    assert legia.ev_net_low == Decimal("0.0560")
    report = build_daily_coupon(found, policy, day="2026-10-31", now=NOW)
    assert [c.event for c in report.solo] == [
        "Górnik Zabrze - Śląsk Wrocław",
        "Legia Warszawa - Lech Poznań",
    ]
    assert report.headline != NO_EDGE
    # Draw/away of the same match lack an uncertainty interval: rejected, not ranked.
    assert not pick(found, "Legia", "away").accepted


def test_uncertain_or_unverified_data_is_rejected(store, import_both, policy, profiles):
    weak = {**GOOD_VALIDATION, "n_out_of_sample": 50}
    assert (
        predict(
            store,
            policy,
            "Legia Warszawa",
            {"home": 0.55, "draw": 0.25, "away": 0.20},
            {"home": [0.50, 0.60]},
            validation=weak,
        )
        == "experimental"
    )
    found = candidates(store, policy, profiles)
    legia = pick(found, "Legia", "home")
    assert not legia.accepted
    assert "Model bez zatwierdzonej walidacji — dodatnie EV nie jest przewagą" in legia.reasons
    superbet = pick(found, "Legia", "home", bookmaker="superbet")
    assert "Parser niezweryfikowany na autentycznym dokumencie operatora" in superbet.reasons
    assert any(r.startswith("Jakość odczytu") for r in superbet.reasons)
    stale = pick(candidates(store, policy, profiles, now=NOW + timedelta(hours=4)), "Legia", "home")
    assert "Kurs nieaktualny — przekroczony maksymalny wiek oferty" in stale.reasons
    expired = pick(
        candidates(store, policy, profiles, now=datetime(2026, 10, 31, 20, tzinfo=UTC)),
        "Legia",
        "home",
    )
    assert "Kurs wygasł — wydarzenie już się rozpoczęło" in expired.reasons
    dnb = pick(found, "Real", "home", market="draw_no_bet")
    assert not dnb.accepted


def test_ako_requires_explicit_joint_model(store, import_both, policy, profiles):
    predict(
        store,
        policy,
        "Legia Warszawa",
        {"home": 0.55, "draw": 0.25, "away": 0.20},
        {"home": [0.50, 0.60]},
    )
    predict(
        store,
        policy,
        "Górnik Zabrze",
        {"home": 0.62, "draw": 0.22, "away": 0.16},
        {"home": [0.60, 0.64]},
    )
    found = candidates(store, policy, profiles)
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
    ako = report.ako
    assert ako.created and ako.joint_probability == pytest.approx(0.55 * 0.62)
    assert ako.total_odds == Decimal("2.40") * Decimal("2.00")
    expected = 0.55 * 0.62 * 4.8 * 0.88 - 1
    assert float(ako.ev_net) == pytest.approx(expected, abs=1e-4)
    assert "nie jest domyślnie lepsze" in ako.reason
    same_event = [c for c in found if c.event.startswith("Legia") and c.accepted] * 2
    assert IndependentEventsModel().joint_probability(same_event) is None
