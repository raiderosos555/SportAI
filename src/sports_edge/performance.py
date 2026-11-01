"""Effectiveness of the coupon builder (TASK 005): coupons and their selections.

Only coupons saved by the builder count here (they carry a profile and the proposal with the
predicted probability). Per profile: hit rate with a Wilson interval, the average predicted
probability against the observed hit rate, ROI after the STS stake coefficient and payout
tax, maximum drawdown and streaks. Per selection (each one counted once, even when several
profiles used it): hit rate by sport, league, market and probability range against the
mean forecast. A handful of settled coupons proves nothing — intervals show how little.
"""

import json
from collections import defaultdict
from decimal import Decimal

from sports_edge.models.calibration import wilson_interval
from sports_edge.offers.labels import market_label
from sports_edge.paper import latest_result, latest_settlement, resolve
from sports_edge.storage import Store

PROBABILITY_BINS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001))
SETTLED = {"win", "loss", "push", "void", "half_win", "half_loss"}


def builder_coupons(store: Store) -> list[dict]:
    """Saved proposals in start order, with their latest settlement (if any)."""
    out = []
    for coupon in store.rows(
        """SELECT * FROM coupons WHERE profile IS NOT NULL AND proposal_json IS NOT NULL
        ORDER BY first_start_at, created_at"""
    ):
        settlement = latest_settlement(store, coupon["id"])
        out.append(
            {
                **coupon,
                "proposal": json.loads(coupon["proposal_json"]),
                "outcome": settlement["outcome"] if settlement else None,
                "net_profit": Decimal(settlement["net_profit"]) if settlement else None,
                "payout": Decimal(settlement["payout"]) if settlement else None,
            }
        )
    return out


def _streaks(outcomes: list[str]) -> dict:
    longest = {"win": 0, "loss": 0}
    current, run = None, 0
    for outcome in outcomes:
        if outcome not in longest:
            current, run = None, 0  # a void/push interrupts the series
            continue
        run = run + 1 if outcome == current else 1
        current = outcome
        longest[outcome] = max(longest[outcome], run)
    return {
        "longest_win_streak": longest["win"],
        "longest_loss_streak": longest["loss"],
        "current_streak": (
            f"{run} × {'wygrana' if current == 'win' else 'przegrana'}" if current else "—"
        ),
    }


def coupon_performance(store: Store) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for coupon in builder_coupons(store):
        groups[coupon["profile"]].append(coupon)
    out = {}
    for profile, coupons in groups.items():
        settled = [c for c in coupons if c["outcome"] in SETTLED]
        decided = [c for c in settled if c["outcome"] in {"win", "loss"}]
        wins = sum(c["outcome"] == "win" for c in decided)
        stake = sum((Decimal(c["stake"]) for c in settled), Decimal(0))
        profit = sum((c["net_profit"] for c in settled), Decimal(0))
        peak = running = drawdown = Decimal(0)
        for c in settled:
            running += c["net_profit"]
            peak = max(peak, running)
            drawdown = max(drawdown, peak - running)
        predicted = [c["proposal"]["probability"] for c in decided]
        out[profile] = {
            "label": coupons[0]["proposal"].get("label", profile),
            "coupons": len(coupons),
            "pending": len(coupons) - len(settled),
            "settled": len(settled),
            "wins": wins,
            "hit_rate": wins / len(decided) if decided else None,
            "hit_rate_ci95": wilson_interval(wins, len(decided)) if decided else None,
            "avg_predicted": (
                sum(p["calibrated"] for p in predicted) / len(predicted) if predicted else None
            ),
            "avg_predicted_conservative": (
                sum(p["conservative"] for p in predicted) / len(predicted) if predicted else None
            ),
            "avg_legs": sum(len(c["proposal"]["legs"]) for c in coupons) / len(coupons),
            "avg_odds": sum(float(c["total_odds"]) for c in coupons) / len(coupons),
            "stake": stake,
            "profit": profit,
            "roi": profit / stake if stake else None,
            "max_drawdown": drawdown,
            **_streaks([c["outcome"] for c in settled]),
        }
    return out


def selection_outcomes(store: Store) -> list[dict]:
    """Every distinct selection of a saved proposal with its outcome (None = not yet known)."""
    seen: dict[str, dict] = {}
    for coupon in builder_coupons(store):
        for leg in coupon["proposal"]["legs"]:
            if leg["odds_id"] in seen:
                seen[leg["odds_id"]]["profiles"].add(coupon["profile"])
                continue
            rows = store.rows(
                """SELECT s.key selection, m.market_key, m.period, m.overtime, m.line,
                e.id event_id, e.sport, e.league, e.start_at FROM odds o
                JOIN selections s ON s.id=o.selection_id JOIN markets m ON m.id=s.market_id
                JOIN events e ON e.id=m.event_id WHERE o.id=?""",
                (leg["odds_id"],),
            )
            if not rows:
                continue
            market = rows[0]
            result = latest_result(store, market["event_id"])
            outcome = resolve(market, market["selection"], result) if result else None
            seen[leg["odds_id"]] = {
                **leg,
                "sport": market["sport"],
                "league": market["league"],
                "market_family": market_label(
                    market["market_key"], market["period"], market["line"]
                ),
                "outcome": outcome.value if outcome else None,
                "profiles": {coupon["profile"]},
            }
    return list(seen.values())


def probability_bin(p: float) -> str:
    for low, high in PROBABILITY_BINS:
        if low <= p < high:
            return f"{low:.0%}–{min(high, 1.0):.0%}"
    return "< 50%"


def selection_summary(rows: list[dict], by: str) -> list[dict]:
    """Hit rate against the mean forecast, grouped by sport, league, market or range."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["outcome"] not in {"win", "loss"}:
            continue
        key = probability_bin(row["p_model"]) if by == "range" else row[by]
        groups[key].append(row)
    out = []
    for key, items in sorted(groups.items()):
        hits = sum(r["outcome"] == "win" for r in items)
        low, high = wilson_interval(hits, len(items))
        mean_p = sum(r["p_model"] for r in items) / len(items)
        out.append(
            {
                "group": key,
                "n": len(items),
                "hits": hits,
                "hit_rate": hits / len(items),
                "ci95": (low, high),
                "mean_p_model": mean_p,
                "mean_p_conservative": sum(r["p_conservative"] for r in items) / len(items),
                # the forecast is inconsistent with the observations when outside the interval
                "flag": "zbyt optymistyczna"
                if high < mean_p
                else "zbyt ostrożna"
                if low > mean_p
                else "zgodna z wynikami",
            }
        )
    return out
