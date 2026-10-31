import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Policy:
    version: str = "default"
    min_data_quality: float = 0.9
    require_verified_parser: bool = True
    require_fresh_quote: bool = True
    require_validated_model: bool = True
    min_validation_events: int = 300
    max_calibration_ece: float = 0.03
    require_beats_market_benchmark: bool = True
    min_net_ev_lower_bound: float = 0.02
    max_odds: float = 10.0
    max_selections: int = 5
    ako_enabled: bool = False
    ako_requires_joint_model: bool = True


def load_policy(path: Path) -> Policy:
    data = tomllib.loads(path.read_text(encoding="utf-8"))["policy"]
    allowed = {f.name for f in fields(Policy)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown policy keys: {sorted(unknown)}")
    return Policy(**data)
