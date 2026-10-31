from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from sports_edge.domain import OddsSnapshot
from sports_edge.quality import basic_issues
from sports_edge.temporal import Timestamps, require_available, require_fresh_quote


def test_timezone_required():
    with pytest.raises(ValueError):
        Timestamps(ingested_at=datetime(2026, 10, 31))


def test_missing_source_times_are_not_fabricated(now):
    ts = Timestamps(ingested_at=now)
    assert ts.valid_at is ts.published_at is ts.observed_at is None
    require_available(ts, now)
    with pytest.raises(ValueError):
        require_available(ts, now - timedelta(seconds=1))


@pytest.mark.parametrize("field", ["published_at", "observed_at"])
def test_impossible_timestamp_order(now, field):
    with pytest.raises(ValueError):
        Timestamps(ingested_at=now, **{field: now + timedelta(seconds=1)})


def test_publication_after_observation(now):
    with pytest.raises(ValueError):
        Timestamps(ingested_at=now, published_at=now, observed_at=now - timedelta(seconds=1))


@pytest.mark.parametrize("seconds", [-1, 301])
def test_future_and_stale_quote(now, seconds):
    ts = Timestamps(ingested_at=now, valid_at=now - timedelta(seconds=seconds))
    with pytest.raises(ValueError):
        require_fresh_quote(ts, now, now + timedelta(hours=1), timedelta(minutes=5))


def test_quote_exact_max_age_and_event_boundary(now):
    ts = Timestamps(ingested_at=now, valid_at=now - timedelta(minutes=5))
    require_fresh_quote(ts, now, now + timedelta(seconds=1), timedelta(minutes=5))
    with pytest.raises(ValueError):
        require_fresh_quote(ts, now, now, timedelta(minutes=5))


def test_quality_reports_duplicates_orphans_and_missing_times(now, event, market, selection):
    quote = OddsSnapshot(
        id="q",
        selection_id="s",
        bookmaker_id="b",
        source_id="ds",
        decimal_odds=Decimal("2"),
        raw_sha256="synthetic",
        timestamps=Timestamps(ingested_at=now),
    )
    issues = basic_issues([event, event], [market], [selection], [quote, replace(quote, id="q2")])
    assert "duplicate_event:e" in issues
    assert "unknown_quote_time:q" in issues
    assert "duplicate_quote_key:q2" in issues
    assert basic_issues(
        [], [market], [replace(selection, key="x")], [replace(quote, selection_id="missing")]
    ) == ["invalid_selection:s", "orphan_market:m", "orphan_odds:q", "unknown_quote_time:q"]


def test_invalid_odds_record(now):
    with pytest.raises(ValueError):
        OddsSnapshot(
            id="q",
            selection_id="s",
            bookmaker_id="b",
            source_id="d",
            decimal_odds=Decimal("NaN"),
            raw_sha256="x",
            timestamps=Timestamps(ingested_at=now),
        )
