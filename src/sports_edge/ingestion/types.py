import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256

from sports_edge.temporal import require_aware


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value: object) -> str:
    return sha256(canonical(value).encode()).hexdigest()


def instant(value: str | datetime) -> datetime:
    result = (
        datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    )
    require_aware(result)
    return result.astimezone(UTC)


def iso(value: str | datetime) -> str:
    return instant(value).isoformat(timespec="microseconds")


class DataKind(StrEnum):
    SYNTHETIC = "synthetic"
    PROSPECTIVE = "prospective"
    ARCHIVE = "archive"


@dataclass(frozen=True)
class ImportContext:
    source_id: str
    kind: DataKind
    ingested_at: datetime
    observed_at: datetime | None = None
    snapshot_at: datetime | None = None
    evidence: dict | None = None
    fixture_available_at: datetime | None = None

    def __post_init__(self):
        require_aware(self.ingested_at)
        for value in (self.observed_at, self.snapshot_at, self.fixture_available_at):
            if value is not None:
                require_aware(value)
        if self.kind == DataKind.PROSPECTIVE:
            if self.observed_at is None or self.observed_at > self.ingested_at:
                raise ValueError("invalid_local_observation")
        elif self.observed_at is not None:
            raise ValueError("archive_or_synthetic_is_not_a_local_observation")
        if self.kind == DataKind.SYNTHETIC and self.fixture_available_at is None:
            raise ValueError("synthetic_clock_required")

    @property
    def available_at(self) -> str | None:
        if self.kind == DataKind.SYNTHETIC:
            return iso(self.fixture_available_at)
        if self.kind == DataKind.PROSPECTIVE:
            return iso(self.ingested_at)
        return iso(self.snapshot_at) if self.snapshot_at is not None and self.evidence else None

    def semantic(self) -> dict:
        return {
            "source_id": self.source_id,
            "kind": self.kind.value,
            "available_at": self.available_at,
            "evidence": self.evidence,
            "snapshot_at": iso(self.snapshot_at) if self.snapshot_at else None,
        }


@dataclass(frozen=True)
class Candidate:
    category: str
    payload: dict


class QuarantineError(ValueError):
    """Only fixed reason codes; never copy untrusted payloads/URLs into logs."""
