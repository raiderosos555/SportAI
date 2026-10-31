from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from sports_edge.config import load_cost_profile
from sports_edge.edge import net_ev
from sports_edge.settlement import Outcome, settle

D = Decimal
DAY = date(2026, 10, 31)


@pytest.mark.parametrize(
    ("outcome", "payout", "profit"),
    [
        (Outcome.WIN, "250", "150"),
        (Outcome.LOSS, "0", "-100"),
        (Outcome.PUSH, "100", "0"),
        (Outcome.VOID, "100", "0"),
        (Outcome.HALF_WIN, "175", "75"),
        (Outcome.HALF_LOSS, "50", "-50"),
    ],
)
def test_cash_flow(outcome, payout, profit, profile):
    flow = settle(D(100), D("2.5"), outcome, profile, on_date=DAY)
    assert flow.payout == D(payout)
    assert flow.net_profit == flow.gross_profit == D(profit)


def test_costs_can_reverse_positive_ev(profile):
    taxed = replace(profile, stake_tax_rate=D("0.12"), execution_fee=D("1"))
    flow = settle(D(100), D(2), Outcome.WIN, taxed, on_date=DAY)
    assert (flow.stake_tax, flow.effective_stake, flow.payout, flow.net_profit) == (
        D(12),
        D(88),
        D(176),
        D(75),
    )
    assert net_ev({Outcome.WIN: 0.55, Outcome.LOSS: 0.45}, D(100), D(2), taxed, on_date=DAY) == D(
        "-0.042"
    )


@pytest.mark.parametrize(
    ("refund", "outcome", "expected"),
    [
        (True, Outcome.VOID, "100"),
        (False, Outcome.PUSH, "88"),
        (True, Outcome.HALF_LOSS, "50"),
        (False, Outcome.HALF_LOSS, "44"),
        (True, Outcome.HALF_WIN, "160"),
        (False, Outcome.HALF_WIN, "154"),
    ],
)
def test_explicit_refund_policy(refund, outcome, expected, profile):
    taxed = replace(profile, stake_tax_rate=D("0.12"), refund_stake_tax=refund)
    assert settle(D(100), D("2.5"), outcome, taxed, on_date=DAY).payout == D(expected)


@pytest.mark.parametrize(("odds", "tax"), [("2.27999", "0"), ("2.28", "0"), ("2.28001", "228.00")])
def test_tax_on_entire_payout_above_threshold(odds, tax, profile):
    taxed = replace(profile, payout_tax_rate=D("0.10"), payout_tax_threshold=D(2280))
    flow = settle(D(1000), D(odds), Outcome.WIN, taxed, on_date=DAY)
    assert flow.payout_tax == D(tax)


def test_nonlinear_tax_applied_before_expectation(profile):
    taxed = replace(profile, payout_tax_rate=D("0.10"), payout_tax_threshold=D(2280))
    assert net_ev({Outcome.WIN: 0.5, Outcome.LOSS: 0.5}, D(1000), D(3), taxed, on_date=DAY) == D(
        "0.35"
    )


def test_commission_on_profit_and_fee_on_loss(profile):
    cost = replace(profile, commission_rate=D("0.05"), execution_fee=D("2"))
    assert settle(D(100), D(3), Outcome.WIN, cost, on_date=DAY).net_profit == D(188)
    assert settle(D(100), D(3), Outcome.LOSS, cost, on_date=DAY).net_profit == D(-102)


def test_decimal_context_independence(profile):
    with localcontext() as ctx:
        ctx.prec = 3
        flow = settle(D(100), D("2.12345"), Outcome.WIN, profile, on_date=DAY)
    assert flow.payout == D("212.35")


@pytest.mark.parametrize(
    ("stake", "odds"),
    [
        (D(0), D(2)),
        (D(-1), D(2)),
        (D("NaN"), D(2)),
        (D(1), D("Infinity")),
        (D("1.001"), D(2)),
        (1.0, D(2)),
    ],
)
def test_invalid_cash_inputs(stake, odds, profile):
    with pytest.raises(ValueError):
        settle(stake, odds, Outcome.WIN, profile, on_date=DAY)


def test_profile_date_rate_and_outcome_guards(profile):
    with pytest.raises(ValueError):
        replace(profile, stake_tax_rate=D(1))
    with pytest.raises(ValueError):
        settle(D(1), D(2), Outcome.WIN, profile, on_date=date(2027, 1, 1))
    with pytest.raises(ValueError):
        settle(D(1), D(2), "win", profile, on_date=DAY)


def test_sts_profile_is_narrow_and_boundary_is_blocked():
    path = Path(__file__).resolve().parents[1] / "config/tax/pl_sts_research.toml"
    sts = load_cost_profile(path)
    assert settle(D(100), D(2), Outcome.WIN, sts, on_date=DAY).payout == D(176)
    with pytest.raises(ValueError, match="not verified"):
        settle(D(100), D(2), Outcome.VOID, sts, on_date=DAY)
    with pytest.raises(ValueError, match="boundary"):
        settle(D(1000), D("2.59090909"), Outcome.WIN, sts, on_date=DAY)
