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
    profiles = {}
    for path in sorted((config_dir / "bookmakers").glob("*.toml")):
        profile = load_profile(path)
        profiles[profile.id] = profile
    return profiles
