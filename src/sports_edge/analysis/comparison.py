"""Side-by-side prices for identical selections at two bookmakers (linked events only)."""

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from sports_edge.bookmakers import BookmakerProfile
from sports_edge.matching import Match, compatible, market_identity
from sports_edge.repository import freshness


def counterpart_index(
    matches: list[Match], rows: list[dict]
) -> tuple[dict[str, dict], list[tuple[Match, dict, dict, str]]]:
    """quote_set_id -> compatible market row at the other bookmaker, plus pairs for display."""
    by_event: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_event[row["event_id"]].append(row)
    index: dict[str, dict] = {}
    pairs: list[tuple[Match, dict, dict, str]] = []
    for match in matches:
        if match.status not in {"auto", "confirmed"}:
            continue
        markets_b = {market_identity(r): r for r in by_event.get(match.event_b["id"], [])}
        for row_a in by_event.get(match.event_a["id"], []):
            row_b = markets_b.get(market_identity(row_a))
            if row_b is None:
                continue
            ok, note = compatible(row_a, row_b)
            if not ok:
                continue
            index[row_a["quote_set_id"]] = row_b
            index[row_b["quote_set_id"]] = row_a
            pairs.append((match, row_a, row_b, note))
    return index, pairs


def comparison_rows(
    pairs: list[tuple[Match, dict, dict, str]],
    profiles: dict[str, BookmakerProfile],
    *,
    now: datetime,
    max_age: timedelta,
) -> list[dict]:
    out = []
    for match, row_a, row_b, note in pairs:
        for selection in sorted(set(row_a["selections"]) & set(row_b["selections"])):
            odds_a = row_a["selections"][selection]["price"]
            odds_b = row_b["selections"][selection]["price"]
            net_a = _net(odds_a, profiles.get(row_a["bookmaker_id"]), row_a)
            net_b = _net(odds_b, profiles.get(row_b["bookmaker_id"]), row_b)
            better = None
            if net_a is not None and net_b is not None and net_a != net_b:
                better = row_a["bookmaker_id"] if net_a > net_b else row_b["bookmaker_id"]
            out.append(
                {
                    "start_at": row_a["start_at"],
                    "sport": row_a["sport"],
                    "event_a": " - ".join(row_a["participants"]),
                    "event_b": " - ".join(row_b["participants"]),
                    "match_status": match.status,
                    "match_confidence": match.confidence,
                    "market": row_a["market_key"],
                    "period": row_a["period"],
                    "line": row_a["line"],
                    "selection": selection,
                    "odds_a": odds_a,
                    "odds_b": odds_b,
                    "odds_diff_pct": float(odds_a / odds_b - 1) * 100,
                    "net_a": net_a,
                    "net_b": net_b,
                    "better_net": better,
                    "fresh_a": freshness(row_a["valid_at"], row_a["start_at"], now, max_age),
                    "fresh_b": freshness(row_b["valid_at"], row_b["start_at"], now, max_age),
                    "valid_at_a": row_a["valid_at"],
                    "valid_at_b": row_b["valid_at"],
                    "compatibility": note,
                    "bookmaker_a": row_a["bookmaker_id"],
                    "bookmaker_b": row_b["bookmaker_id"],
                }
            )
    return out


def _net(odds: Decimal, profile: BookmakerProfile | None, row: dict) -> Decimal | None:
    """Return per 1 PLN after stake tax, below the high-win threshold; None if no profile."""
    if profile is None or not row["start_at"]:
        return None
    if not profile.cost_valid_on(datetime.fromisoformat(row["start_at"]).date()):
        return None
    return (odds * (1 - profile.cost.stake_tax_rate)).quantize(Decimal("0.0001"))
