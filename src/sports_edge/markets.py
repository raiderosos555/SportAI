"""Explicit market adapters. Unknown rules/statuses fail closed."""

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from sports_edge.domain import Market, Prediction, Selection
from sports_edge.settlement import Outcome


def validate_selection(selection: Selection, market: Market) -> None:
    if selection.market_id != market.id or selection.key not in market.selection_keys:
        raise ValueError("Selection does not belong to this market")


def validate_prediction(prediction: Prediction, market: Market) -> None:
    if prediction.market_id != market.id or set(dict(prediction.probabilities)) != set(
        market.state_keys
    ):
        raise ValueError("Forecast must cover exactly the market's disjoint states")


@dataclass(frozen=True)
class Result:
    status: str
    period: str
    includes_overtime: bool
    winner_key: str | None = None
    total: Decimal | None = None


class SettlementRule(Protocol):
    def resolve(self, market: Market, selection: Selection, result: Result) -> Outcome: ...


def _check(market: Market, selection: Selection, result: Result) -> None:
    validate_selection(selection, market)
    if (
        result.status != "completed"
        or result.period != market.period
        or result.includes_overtime != market.includes_overtime
    ):
        raise ValueError("Unresolved result status or incompatible result scope")


class WinnerRule:
    """Exactly one winner (including an explicit draw state), any field size."""

    def resolve(self, market: Market, selection: Selection, result: Result) -> Outcome:
        _check(market, selection, result)
        if (
            set(market.selection_keys) != set(market.state_keys)
            or result.winner_key not in market.selection_keys
        ):
            raise ValueError("Winner is missing or market needs a different rule")
        return Outcome.WIN if selection.key == result.winner_key else Outcome.LOSS


class AsianTotalRule:
    """Quarter-line totals on nonnegative integer counts in the specified period."""

    def resolve(self, market: Market, selection: Selection, result: Result) -> Outcome:
        _check(market, selection, result)
        line, total = market.line, result.total
        if (
            set(market.selection_keys) != {"over", "under"}
            or line is None
            or line < 0
            or line % Decimal("0.25") != 0
            or total is None
            or not total.is_finite()
            or total < 0
            or total != total.to_integral_value()
        ):
            raise ValueError("Invalid total market or result")
        lines = (
            (line - Decimal("0.25"), line + Decimal("0.25")) if line % Decimal("0.5") else (line,)
        )
        scores = []
        for component in lines:
            difference = total - component
            if selection.key == "under":
                difference = -difference
            scores.append(1 if difference > 0 else -1 if difference < 0 else 0)
        average = sum(scores) / len(scores)
        return {
            1: Outcome.WIN,
            -1: Outcome.LOSS,
            0: Outcome.PUSH,
            0.5: Outcome.HALF_WIN,
            -0.5: Outcome.HALF_LOSS,
        }[average]


class RuleRegistry:
    def __init__(self) -> None:
        self._rules: dict[str, SettlementRule] = {}

    def register(self, key: str, rule: SettlementRule) -> None:
        if not key or key in self._rules:
            raise ValueError("Empty or duplicate versioned rule id")
        self._rules[key] = rule

    def resolve(self, market: Market, selection: Selection, result: Result) -> Outcome:
        if market.settlement_rule_id not in self._rules:
            raise ValueError("No verified adapter for this market")
        return self._rules[market.settlement_rule_id].resolve(market, selection, result)


def load_catalog(path: Path) -> dict[str, dict]:
    """TOML defines templates; dynamic participant/state keys supplied per event."""
    definitions = tomllib.loads(path.read_text(encoding="utf-8"))["market"]
    result = {}
    for definition in definitions:
        key = definition["id"]
        if key in result or not all(definition.get(k) for k in ("id", "version", "rule_id")):
            raise ValueError("Invalid or duplicate market definition")
        result[key] = definition
    return result


def market_from_catalog(
    catalog: dict[str, dict],
    definition_id: str,
    *,
    id: str,
    event_id: str,
    selection_keys: tuple[str, ...] | None = None,
    state_keys: tuple[str, ...] | None = None,
    line: Decimal | None = None,
) -> Market:
    spec = catalog[definition_id]
    keys = tuple(spec.get("selection_keys", ())) or selection_keys
    states = tuple(spec.get("state_keys", ())) or state_keys
    if not keys or not states or (spec.get("requires_line", False) != (line is not None)):
        raise ValueError("Missing dynamic keys or incompatible line")
    return Market(
        id=id,
        event_id=event_id,
        definition_id=definition_id,
        definition_version=spec["version"],
        period=spec["period"],
        selection_keys=keys,
        state_keys=states,
        line=line,
        includes_overtime=spec["includes_overtime"],
        settlement_rule_id=spec["rule_id"],
    )
