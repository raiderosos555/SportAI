"""Cash-flow arithmetic after a dedicated market adapter determines the outcome."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum

D = Decimal


class Outcome(StrEnum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    VOID = "void"
    HALF_WIN = "half_win"
    HALF_LOSS = "half_loss"


@dataclass(frozen=True, kw_only=True)
class CostProfile:
    id: str
    currency: str
    valid_from: date
    valid_until: date
    stake_tax_rate: Decimal = D("0")
    payout_tax_rate: Decimal = D("0")
    payout_tax_threshold: Decimal = D("0")
    threshold_rule: str = "exclusive"  # exclusive, inclusive, reject_equal
    commission_rate: Decimal = D("0")  # on positive ticket profit after stake tax
    execution_fee: Decimal = D("0")
    refund_stake_tax: bool = True  # returned fraction only, including split stakes
    money_quantum: Decimal = D("0.01")
    allowed_outcomes: tuple[str, ...] = tuple(o.value for o in Outcome)

    def __post_init__(self) -> None:
        for rate in (self.stake_tax_rate, self.payout_tax_rate, self.commission_rate):
            if not isinstance(rate, Decimal) or not rate.is_finite() or not 0 <= rate < 1:
                raise ValueError("Rates must be finite Decimals in [0, 1)")
        for amount in (self.payout_tax_threshold, self.execution_fee, self.money_quantum):
            if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
                raise ValueError("Invalid monetary configuration")
        if (
            self.money_quantum <= 0
            or self.money_quantum.as_tuple().digits != (1,)
            or self.valid_until < self.valid_from
        ):
            raise ValueError("Invalid rounding quantum or validity interval")
        if self.threshold_rule not in {"exclusive", "inclusive", "reject_equal"}:
            raise ValueError("Unknown threshold policy")
        if not self.allowed_outcomes or not set(self.allowed_outcomes) <= set(Outcome):
            raise ValueError("Unknown allowed outcomes")


@dataclass(frozen=True)
class CashFlow:
    deposited_stake: Decimal
    stake_tax: Decimal
    effective_stake: Decimal
    stake_tax_refund: Decimal
    gross_payout: Decimal  # counterfactual no-cost payout, same quoted odds
    payout_before_withholding: Decimal
    payout_tax: Decimal
    commission: Decimal
    execution_fee: Decimal
    payout: Decimal
    gross_profit: Decimal
    net_profit: Decimal


def settle(
    stake: Decimal, odds: Decimal, outcome: Outcome, profile: CostProfile, *, on_date: date
) -> CashFlow:
    """Single ticket, no accumulator/exchange netting; taxes assessed per outcome."""
    if not isinstance(outcome, Outcome):
        raise ValueError("An explicit settlement outcome is required")
    if outcome.value not in profile.allowed_outcomes:
        raise ValueError("Settlement outcome not verified for this cost profile")
    for value in (stake, odds):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("Finite Decimal inputs required")
    if stake <= 0 or odds <= 1:
        raise ValueError("Positive stake and decimal odds > 1 required")
    if not profile.valid_from <= on_date <= profile.valid_until:
        raise ValueError("Cost profile outside verified date range")
    with localcontext() as context:
        context.prec = 38
        context.rounding = ROUND_HALF_UP
        q = lambda value: value.quantize(profile.money_quantum)  # noqa: E731
        if stake != q(stake):
            raise ValueError("Stake exceeds currency precision")
        win, refund = {
            Outcome.WIN: (D(1), D(0)),
            Outcome.LOSS: (D(0), D(0)),
            Outcome.PUSH: (D(0), D(1)),
            Outcome.VOID: (D(0), D(1)),
            Outcome.HALF_WIN: (D("0.5"), D("0.5")),
            Outcome.HALF_LOSS: (D(0), D("0.5")),
        }[outcome]
        tax = q(stake * profile.stake_tax_rate)
        effective = stake - tax
        tax_refund = q(tax * refund) if profile.refund_stake_tax else D(0)
        gross = q(stake * (win * odds + refund))
        before = q(effective * (win * odds + refund) + tax_refund)
        withholding = D(0)
        if profile.payout_tax_rate and win > 0:
            threshold = profile.payout_tax_threshold
            if before == threshold and profile.threshold_rule == "reject_equal":
                raise ValueError("Threshold boundary requires operator clarification")
            taxable = before > threshold or (
                before == threshold and profile.threshold_rule == "inclusive"
            )
            if taxable:
                withholding = q(before * profile.payout_tax_rate)
        commission = q(max(before - stake, D(0)) * profile.commission_rate)
        payout = before - withholding - commission
        fee = q(profile.execution_fee)
        return CashFlow(
            stake,
            tax,
            effective,
            tax_refund,
            gross,
            before,
            withholding,
            commission,
            fee,
            payout,
            gross - stake,
            payout - stake - fee,
        )
