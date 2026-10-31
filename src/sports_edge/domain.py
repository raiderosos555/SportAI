"""Immutable domain records; foreign-key checks live at the dataset boundary."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite

from sports_edge.probability import validate_probabilities
from sports_edge.temporal import Timestamps, require_aware


@dataclass(frozen=True, kw_only=True)
class Entity:
    id: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("A nonempty identifier is required")


@dataclass(frozen=True, kw_only=True)
class Sport(Entity):
    name: str


@dataclass(frozen=True, kw_only=True)
class Competition(Entity):
    sport_id: str
    name: str
    country: str | None = None


@dataclass(frozen=True, kw_only=True)
class Season(Entity):
    competition_id: str
    label: str
    starts_on: date
    ends_on: date

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.ends_on < self.starts_on:
            raise ValueError("Season ends before it starts")


@dataclass(frozen=True, kw_only=True)
class Participant(Entity):
    name: str
    kind: str  # team, person, pair, field entry; not restricted to teams


@dataclass(frozen=True, kw_only=True)
class Event(Entity):
    sport_id: str
    competition_id: str
    season_id: str
    participant_ids: tuple[str, ...]
    starts_at: datetime
    timestamps: Timestamps
    status: str = "scheduled"

    def __post_init__(self) -> None:
        super().__post_init__()
        require_aware(self.starts_at)
        if (
            not isinstance(self.participant_ids, tuple)
            or not self.participant_ids
            or any(not isinstance(k, str) or not k for k in self.participant_ids)
            or len(set(self.participant_ids)) != len(self.participant_ids)
        ):
            raise ValueError("Participants must be a nonempty tuple without duplicates")


@dataclass(frozen=True, kw_only=True)
class Market(Entity):
    event_id: str
    definition_id: str
    definition_version: str
    period: str
    selection_keys: tuple[str, ...]
    state_keys: tuple[str, ...]  # disjoint forecast states; may differ from selections
    settlement_rule_id: str
    includes_overtime: bool
    line: Decimal | None = None
    subject_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        for keys in (self.selection_keys, self.state_keys):
            if (
                not isinstance(keys, tuple)
                or not keys
                or any(not isinstance(k, str) or not k for k in keys)
                or len(set(keys)) != len(keys)
            ):
                raise ValueError("Market keys must be nonempty and unique")
        if self.line is not None and (
            not isinstance(self.line, Decimal) or not self.line.is_finite()
        ):
            raise ValueError("Invalid line")


@dataclass(frozen=True, kw_only=True)
class Selection(Entity):
    market_id: str
    key: str


@dataclass(frozen=True, kw_only=True)
class Bookmaker(Entity):
    name: str
    jurisdiction: str
    rules_version: str


@dataclass(frozen=True, kw_only=True)
class DataSource(Entity):
    name: str
    terms_url: str
    terms_checked_on: date
    retention_policy: str
    redistribution_allowed: bool


@dataclass(frozen=True, kw_only=True)
class OddsSnapshot(Entity):
    selection_id: str
    bookmaker_id: str
    source_id: str
    decimal_odds: Decimal
    timestamps: Timestamps
    raw_sha256: str
    available: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            not isinstance(self.decimal_odds, Decimal)
            or not self.decimal_odds.is_finite()
            or self.decimal_odds <= 1
        ):
            raise ValueError("Invalid decimal odds")


@dataclass(frozen=True, kw_only=True)
class FeatureSnapshot(Entity):
    event_id: str
    feature_version: str
    values: tuple[tuple[str, float | None], ...]
    source_record_ids: tuple[str, ...]
    timestamps: Timestamps

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            not isinstance(self.values, tuple)
            or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in self.values)
            or len(dict(self.values)) != len(self.values)
            or not isinstance(self.source_record_ids, tuple)
        ):
            raise ValueError("Feature inputs must be immutable with unique keys")
        for key, value in self.values:
            if not isinstance(key, str) or not key:
                raise ValueError("Invalid feature key")
            if value is not None and (isinstance(value, bool) or not isfinite(value)):
                raise ValueError("Use None for missing values, not NaN/infinity")


@dataclass(frozen=True, kw_only=True)
class ModelVersion(Entity):
    family: str
    artifact_sha256: str
    training_data_version: str
    trained_through: datetime
    provider: str | None = None
    provider_version: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        require_aware(self.trained_through)


@dataclass(frozen=True, kw_only=True)
class Prediction(Entity):
    market_id: str
    model_version_id: str
    feature_snapshot_id: str
    created_at: datetime
    probabilities: tuple[tuple[str, float], ...]
    uncertainty_method: str
    uncertainty_artifact: str | None = None
    prompt: str | None = None
    input_sha256: str | None = None
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        require_aware(self.created_at)
        if (
            not isinstance(self.probabilities, tuple)
            or any(not isinstance(pair, tuple) for pair in self.probabilities)
            or len(dict(self.probabilities)) != len(self.probabilities)
        ):
            raise ValueError("Duplicate states or mutable probability input")
        validate_probabilities(dict(self.probabilities))


@dataclass(frozen=True, kw_only=True)
class Experiment(Entity):
    hypothesis: str
    data_version: str
    code_version: str
    model_version_id: str
    config_json: str
    registered_at: datetime
    train_start: datetime
    train_end: datetime
    validation_end: datetime
    test_end: datetime
    independence: str
    status: str = "registered"
    results_uri: str | None = None
    conclusion: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        for t in (
            self.registered_at,
            self.train_start,
            self.train_end,
            self.validation_end,
            self.test_end,
        ):
            require_aware(t)
        if not self.train_start < self.train_end < self.validation_end < self.test_end:
            raise ValueError("Experiment intervals must be chronological and nonempty")


@dataclass(frozen=True, kw_only=True)
class PaperBet(Entity):
    experiment_id: str
    prediction_id: str
    odds_snapshot_id: str
    selection_id: str
    decided_at: datetime
    stake: Decimal
    currency: str
    cost_profile_id: str
    settlement_rule_id: str
    reason: str

    def __post_init__(self) -> None:
        super().__post_init__()
        require_aware(self.decided_at)
        if not isinstance(self.stake, Decimal) or not self.stake.is_finite() or self.stake <= 0:
            raise ValueError("Stake must be a positive Decimal")


@dataclass(frozen=True, kw_only=True)
class Settlement(Entity):
    paper_bet_id: str
    result_version: str
    settled_at: datetime
    outcome: str
    payout: Decimal
    net_profit: Decimal
    supersedes_id: str | None = None  # corrections append; never overwrite

    def __post_init__(self) -> None:
        super().__post_init__()
        require_aware(self.settled_at)
        if (
            not isinstance(self.payout, Decimal)
            or not self.payout.is_finite()
            or self.payout < 0
            or not isinstance(self.net_profit, Decimal)
            or not self.net_profit.is_finite()
        ):
            raise ValueError("Invalid settlement amounts")
