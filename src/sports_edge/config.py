import tomllib
from datetime import date
from decimal import Decimal
from pathlib import Path

from sports_edge.settlement import CostProfile


def load_cost_profile(path: Path) -> CostProfile:
    data = tomllib.loads(path.read_text(encoding="utf-8"))["profile"]
    for key in (
        "stake_tax_rate",
        "payout_tax_rate",
        "payout_tax_threshold",
        "commission_rate",
        "execution_fee",
        "money_quantum",
    ):
        if key in data:
            data[key] = Decimal(data[key])
    for key in ("valid_from", "valid_until"):
        if isinstance(data[key], str):
            data[key] = date.fromisoformat(data[key])
    if "allowed_outcomes" in data:
        data["allowed_outcomes"] = tuple(data["allowed_outcomes"])
    return CostProfile(**data)
