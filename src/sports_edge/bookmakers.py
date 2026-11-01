"""Per-bookmaker profiles: taxes, promotions, limits, settlement rules and fees."""

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sports_edge.config import load_cost_profile
from sports_edge.settlement import CostProfile


@dataclass(frozen=True)
class BookmakerProfile:
    id: str
    name: str
    jurisdiction: str
    checked_on: str
    cost: CostProfile
    tax_verified: bool
    sections: dict  # raw TOML sections for display: tax, promotions, limits, settlement, fees

    def cost_valid_on(self, day: date) -> bool:
        return self.cost.valid_from <= day <= self.cost.valid_until


def load_profile(path: Path) -> BookmakerProfile:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    meta = data["bookmaker"]
    cost = load_cost_profile(path.parent.parent / data["tax"]["profile"])
    return BookmakerProfile(
        id=meta["id"],
        name=meta["name"],
        jurisdiction=meta["jurisdiction"],
        checked_on=meta["checked_on"],
        cost=cost,
        tax_verified=bool(data["tax"].get("verified", False)),
        sections=data,
    )


def load_profiles(config_dir: Path) -> dict[str, BookmakerProfile]:
    """Active operators only; archived profiles live in config/archive and are not read."""
    profiles = {}
    for path in sorted((config_dir / "bookmakers").glob("*.toml")):
        profile = load_profile(path)
        profiles[profile.id] = profile
    return profiles


@dataclass(frozen=True)
class MarketRule:
    sport: str
    market_key: str
    period: str
    payoff: str  # binary | void_on_draw
    source: str


@dataclass(frozen=True)
class MarketRules:
    rules: dict[tuple[str, str, str], MarketRule]
    general: dict
    documents: dict

    def lookup(self, sport: str, market_key: str, period: str) -> MarketRule | None:
        return self.rules.get((sport, market_key, period))


def load_rules(config_dir: Path) -> MarketRules:
    """STS settlement rules checked against the operator's documents (config/sts_rules.toml)."""
    data = tomllib.loads((config_dir / "sts_rules.toml").read_text(encoding="utf-8"))
    rules = {}
    for entry in data.get("rule", []):
        if entry["payoff"] not in {"binary", "void_on_draw"}:
            raise ValueError(f"Unknown payoff {entry['payoff']!r}")
        for market in entry["markets"]:
            key = (entry["sport"], market, entry["period"])
            if key in rules:
                raise ValueError(f"Duplicate market rule {key}")
            rules[key] = MarketRule(
                entry["sport"], market, entry["period"], entry["payoff"], entry["source"]
            )
    return MarketRules(rules, data.get("general", {}), data.get("documents", {}))
