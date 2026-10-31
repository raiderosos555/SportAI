"""Ports for later tasks. These are interfaces, not working ingestion/ML engines."""

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from sports_edge.domain import FeatureSnapshot, Market, Prediction


class DataAdapter(Protocol):
    source_id: str

    def fetch(self, *, since: datetime, until: datetime) -> Iterable[bytes]: ...


class Predictor(Protocol):
    def predict(self, market: Market, features: FeatureSnapshot, *, at: datetime) -> Prediction: ...


class AppendOnlyStore(Protocol):
    def append(self, *, stream: str, record_id: str, payload: bytes) -> None:
        """Insert once; duplicate IDs fail. Corrections use new IDs and references."""
        ...
