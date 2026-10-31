"""Normalized offer records produced by parsers. Raw text is kept next to every value."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sports_edge.temporal import require_aware


class RowStatus(StrEnum):
    OK = "ok"
    REVIEW = "review"  # incomplete or ambiguous; never used for analysis until reviewed


class OfferKind(StrEnum):
    TODAY = "today"
    TOMORROW = "tomorrow"
    WEEK = "week"
    UNKNOWN = "unknown"


# Issues that block automatic use of a row. Others only lower the quality score.
BLOCKING_ISSUES = frozenset(
    {
        "missing_time",
        "missing_date",
        "missing_sport",
        "unknown_market",
        "unknown_label",
        "unassigned_odds",
        "duplicate_column",
        "invalid_odds",
        "participants_unparsed",
        "unresolved_team_total",
        "line_missing",
        "name_truncated",
        "no_odds",
        "unexpected_token",
    }
)

PENALTIES = {
    "name_truncated": 0.3,
    "name_completed_from_other_row": 0.05,
    "missing_time": 0.5,
    "missing_date": 0.5,
    "missing_sport": 0.4,
    "unknown_market": 0.5,
    "unknown_label": 0.5,
    "unassigned_odds": 0.6,
    "duplicate_column": 0.6,
    "invalid_odds": 0.6,
    "participants_unparsed": 0.4,
    "unresolved_team_total": 0.5,
    "line_missing": 0.5,
    "line_unexpected": 0.3,
    "missing_selection": 0.05,
    "no_odds": 1.0,
    "unexpected_token": 0.4,
    "unknown_sport": 0.1,
    "layout_unverified": 0.3,
    "rules_unverified": 0.0,
}


def score(issues: tuple[str, ...]) -> float:
    value = 1.0
    for issue in issues:
        value -= PENALTIES.get(issue, 0.2)
    return round(max(value, 0.0), 3)


@dataclass(frozen=True, kw_only=True)
class OfferSelection:
    label: str  # exactly as printed, e.g. "1X" or "+"
    key: str  # canonical selection key, e.g. "home_draw" or "over"
    odds: Decimal
    odds_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.odds, Decimal) or not self.odds.is_finite() or self.odds <= 1:
            raise ValueError("Decimal odds above 1 are required")


@dataclass(frozen=True, kw_only=True)
class OfferRow:
    """One printed line: one event, one market (and line) and the selections found on it."""

    bookmaker: str
    sport_raw: str
    sport: str
    league_raw: str
    event_name_raw: str
    participants: tuple[str, ...]
    market_raw: str
    market_key: str
    period: str
    includes_overtime: bool | None
    selections: tuple[OfferSelection, ...]
    page: int
    column: int
    source_text: str
    offer_code: str | None = None
    country: str | None = None
    start_local: datetime | None = None
    line: str | None = None
    offer_date: date | None = None
    issues: tuple[str, ...] = ()
    y: float = 0.0
    rules_ref: str = ""

    def __post_init__(self) -> None:
        if self.start_local is not None:
            require_aware(self.start_local)
        keys = [s.key for s in self.selections]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate selection in one row")

    @property
    def quality(self) -> float:
        return score(self.issues)

    @property
    def status(self) -> RowStatus:
        blocked = BLOCKING_ISSUES.intersection(self.issues)
        return RowStatus.REVIEW if blocked or not self.selections else RowStatus.OK

    @property
    def home(self) -> str | None:
        return self.participants[0] if len(self.participants) == 2 else None

    @property
    def away(self) -> str | None:
        return self.participants[1] if len(self.participants) == 2 else None


@dataclass(frozen=True, kw_only=True)
class ReviewItem:
    """Printed content that could not be assigned to a market; kept for manual verification."""

    page: int
    column: int
    source_text: str
    reason: str
    context: str = ""


@dataclass(frozen=True, kw_only=True)
class ParseResult:
    bookmaker: str
    parser: str
    parser_version: str
    parser_verified: bool  # True only after validation on an authentic document
    offer_kind: OfferKind
    rows: tuple[OfferRow, ...]
    review: tuple[ReviewItem, ...]
    pages_total: int
    pages_with_text: int
    document_generated_at: datetime | None = None
    offer_dates: tuple[date, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.document_generated_at is not None:
            require_aware(self.document_generated_at)

    @property
    def accepted(self) -> tuple[OfferRow, ...]:
        return tuple(r for r in self.rows if r.status == RowStatus.OK)

    @property
    def quality(self) -> float:
        """Share of printed rows usable without manual review, weighted by row quality."""
        total = len(self.rows) + len(self.review)
        if not total:
            return 0.0
        return round(sum(r.quality for r in self.accepted) / total, 3)

    def summary(self) -> dict:
        events = {
            (r.offer_date, r.start_local, r.sport, r.league_raw, r.participants)
            for r in self.accepted
            if len(r.participants) == 2
        }
        return {
            "bookmaker": self.bookmaker,
            "parser": f"{self.parser}@{self.parser_version}",
            "parser_verified": self.parser_verified,
            "offer_kind": self.offer_kind.value,
            "pages_total": self.pages_total,
            "pages_with_text": self.pages_with_text,
            "rows": len(self.rows),
            "rows_ok": len(self.accepted),
            "rows_review": len(self.rows) - len(self.accepted),
            "unparsed_lines": len(self.review),
            "events": len(events),
            "selections": sum(len(r.selections) for r in self.accepted),
            "quality": self.quality,
            "generated_at": self.document_generated_at.isoformat()
            if self.document_generated_at
            else None,
            "offer_dates": [d.isoformat() for d in self.offer_dates],
            "warnings": list(self.warnings),
        }
