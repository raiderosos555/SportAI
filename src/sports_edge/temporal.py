"""Conservative availability checks. Missing source timestamps remain missing."""

from dataclasses import dataclass
from datetime import datetime, timedelta


def require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An offset-aware datetime is required")


@dataclass(frozen=True, kw_only=True)
class Timestamps:
    ingested_at: datetime
    event_time: datetime | None = None
    valid_at: datetime | None = None
    published_at: datetime | None = None
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        for value in (
            self.ingested_at,
            self.event_time,
            self.valid_at,
            self.published_at,
            self.observed_at,
        ):
            if value is not None:
                require_aware(value)
        if self.observed_at is not None and self.observed_at > self.ingested_at:
            raise ValueError("Observation cannot follow ingestion")
        if self.published_at is not None and self.published_at > self.ingested_at:
            raise ValueError("Publication cannot follow ingestion")
        if (
            self.published_at is not None
            and self.observed_at is not None
            and self.published_at > self.observed_at
        ):
            raise ValueError("Publication cannot follow observation")


def require_available(timestamps: Timestamps, decision_at: datetime) -> None:
    """Strict local replay/paper policy; downloaded historical data is not backdated."""
    require_aware(decision_at)
    if timestamps.ingested_at > decision_at:
        raise ValueError("Information was not in the system at decision time")


def require_fresh_quote(
    timestamps: Timestamps, decision_at: datetime, event_start: datetime, max_age: timedelta
) -> None:
    require_available(timestamps, decision_at)
    require_aware(event_start)
    if max_age < timedelta(0):
        raise ValueError("Negative max_age")
    if decision_at >= event_start:
        raise ValueError("Pre-event decisions must precede the event")
    if timestamps.valid_at is None:
        raise ValueError("Quote validity timestamp unknown")
    age = decision_at - timestamps.valid_at
    if age < timedelta(0) or age > max_age:
        raise ValueError("Future or stale quote")
