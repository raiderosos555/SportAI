"""Dataset boundary checks; no automatic deduplication or identifier guessing."""

from collections import Counter
from collections.abc import Sequence

from sports_edge.domain import Event, Market, OddsSnapshot, Selection
from sports_edge.markets import validate_selection


def basic_issues(
    events: Sequence[Event],
    markets: Sequence[Market],
    selections: Sequence[Selection],
    odds: Sequence[OddsSnapshot],
) -> list[str]:
    issues = []
    for name, rows in (
        ("event", events),
        ("market", markets),
        ("selection", selections),
        ("odds", odds),
    ):
        issues.extend(
            f"duplicate_{name}:{key}" for key, n in Counter(r.id for r in rows).items() if n > 1
        )
    event_ids = {e.id for e in events}
    by_market = {m.id: m for m in markets}
    selection_ids = {s.id for s in selections}
    for market in markets:
        if market.event_id not in event_ids:
            issues.append(f"orphan_market:{market.id}")
    for selection in selections:
        try:
            validate_selection(selection, by_market[selection.market_id])
        except (KeyError, ValueError):
            issues.append(f"invalid_selection:{selection.id}")
    natural_keys = set()
    for quote in odds:
        if quote.selection_id not in selection_ids:
            issues.append(f"orphan_odds:{quote.id}")
        if quote.timestamps.valid_at is None:
            issues.append(f"unknown_quote_time:{quote.id}")
        key = (quote.selection_id, quote.bookmaker_id, quote.source_id, quote.timestamps.valid_at)
        if key in natural_keys:
            issues.append(f"duplicate_quote_key:{quote.id}")
        natural_keys.add(key)
        if quote.timestamps.valid_at is not None and (
            quote.timestamps.valid_at > quote.timestamps.ingested_at
        ):
            issues.append(f"future_quote:{quote.id}")
    return sorted(issues)
