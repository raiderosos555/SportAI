"""Virtual coupons, results, settlement and performance. No real-money execution exists.

Modes are kept apart: simulation, historical backtest, prospective paper trading (system
or manual choice) and manually imported real results. A coupon is stored before the first
event starts; results and settlements are appended and never modify the coupon.
"""

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from math import prod

from sports_edge.bookmakers import BookmakerProfile
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.settlement import Outcome, settle
from sports_edge.storage import Store

MODES = {
    "paper_system": "Paper trading — selekcja silnika",
    "paper_manual": "Paper trading — wybór użytkownika (nie rekomendacja)",
    "simulation": "Symulacja",
    "backtest": "Backtest historyczny",
    "real_manual": "Rzeczywiste wyniki użytkownika (import ręczny)",
}
PROSPECTIVE = {"paper_system", "paper_manual", "real_manual"}

LEG_SQL = """
SELECT o.id odds_id, o.price, s.id selection_id, s.key selection, m.id market_id,
       m.market_key, m.period, m.overtime, m.line, e.id event_id, e.start_at, e.bookmaker_id,
       e.home, e.away, q.status quote_status
FROM odds o JOIN selections s ON s.id=o.selection_id JOIN markets m ON m.id=s.market_id
JOIN events e ON e.id=m.event_id JOIN quote_sets q ON q.id=o.quote_set_id WHERE o.id=?
"""


def create_coupon(
    store: Store,
    *,
    odds_ids: list[str],
    stake: Decimal,
    mode: str,
    profile: BookmakerProfile,
    now: datetime,
    analysis_id: str | None = None,
    prediction_ids: dict[str, str] | None = None,
    note: str = "",
) -> str:
    if mode not in MODES:
        raise ValueError("Unknown coupon mode")
    if not odds_ids or len(set(odds_ids)) != len(odds_ids):
        raise ValueError("At least one distinct leg is required")
    if not isinstance(stake, Decimal) or stake <= 0 or stake != stake.quantize(Decimal("0.01")):
        raise ValueError("Stake must be a positive amount with at most 2 decimals")
    legs = []
    for odds_id in odds_ids:
        rows = store.rows(LEG_SQL, (odds_id,))
        if not rows:
            raise ValueError("Unknown odds record")
        leg = rows[0]
        if leg["bookmaker_id"] != profile.id:
            raise ValueError("All legs must come from the coupon's bookmaker")
        if leg["quote_status"] != "ok":
            raise ValueError("Quote awaits manual review")
        if not leg["start_at"]:
            raise ValueError("Event start time unknown")
        if mode in PROSPECTIVE and now >= datetime.fromisoformat(leg["start_at"]):
            raise ValueError("Coupon must be stored before the event starts")
        legs.append(leg)
    if len({leg["event_id"] for leg in legs}) != len(legs):
        raise ValueError("Several legs from one event are correlated; not supported")
    total = prod(Decimal(leg["price"]) for leg in legs)
    first = min(leg["start_at"] for leg in legs)
    key = digest(["coupon", mode, sorted(odds_ids), str(stake), iso(now)])
    with store.transaction():
        store.insert(
            "coupons",
            id=key,
            created_at=iso(now),
            analysis_id=analysis_id,
            kind="solo" if len(legs) == 1 else "ako",
            mode=mode,
            bookmaker_id=profile.id,
            stake=str(stake),
            currency=profile.cost.currency,
            cost_profile_id=profile.cost.id,
            total_odds=str(total),
            first_start_at=first,
            note=note,
        )
        for leg in legs:
            store.insert(
                "coupon_legs",
                id=digest(["leg", key, leg["selection_id"]]),
                coupon_id=key,
                odds_id=leg["odds_id"],
                selection_id=leg["selection_id"],
                price=leg["price"],
                prediction_id=(prediction_ids or {}).get(leg["odds_id"]),
            )
    return key


def record_result(
    store: Store,
    *,
    event_id: str,
    status: str,
    now: datetime,
    home_score: int | None = None,
    away_score: int | None = None,
    ht_home: int | None = None,
    ht_away: int | None = None,
    details: dict | None = None,
    source: str = "manual",
    verified: bool = True,
) -> str:
    """Append a result; a later record for the same event supersedes the previous one."""
    if store.get("events", event_id) is None:
        raise ValueError("Unknown event")
    scores = (home_score, away_score, ht_home, ht_away)
    if any(
        s is not None and (isinstance(s, bool) or not isinstance(s, int) or s < 0) for s in scores
    ):
        raise ValueError("Scores must be nonnegative integers")
    if status == "completed" and (home_score is None or away_score is None):
        raise ValueError("A completed result needs the final score")
    previous = latest_result(store, event_id)
    key = digest(["result", event_id, status, scores, iso(now), source])
    with store.transaction():
        store.insert(
            "results",
            id=key,
            event_id=event_id,
            source=source,
            status=status,
            home_score=home_score,
            away_score=away_score,
            ht_home=ht_home,
            ht_away=ht_away,
            details_json=canonical(details or {}),
            verified=int(verified),
            recorded_at=iso(now),
            supersedes_id=previous["id"] if previous else None,
        )
    return key


def latest_result(store: Store, event_id: str) -> dict | None:
    rows = store.rows(
        "SELECT * FROM results WHERE event_id=? ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (event_id,),
    )
    return rows[0] if rows else None


def _scores(market: dict, result: dict) -> tuple[int, int] | None:
    h, a = result["home_score"], result["away_score"]
    details = json.loads(result.get("details_json") or "{}")
    period, overtime = market["period"], market["overtime"]
    if period == "FT" and overtime == "false":
        return (h, a) if h is not None else None
    if period == "H1":
        return (result["ht_home"], result["ht_away"]) if result["ht_home"] is not None else None
    if period == "H2":
        if result["ht_home"] is None or h is None:
            return None
        return h - result["ht_home"], a - result["ht_away"]
    if period in {"FT_OT", "MATCH"} and overtime == "true":
        after = details.get("after_ot")
        if after:
            return int(after[0]), int(after[1])
        return (h, a) if h is not None and h != a else None
    return None


def resolve(market: dict, selection: str, result: dict) -> Outcome | None:
    """Generic settlement of common markets; None means 'needs manual settlement'."""
    if result["status"] != "completed" or not result["verified"]:
        return None
    key = market["market_key"]
    details = json.loads(result.get("details_json") or "{}")
    if key == "match_winner" and market["overtime"] == "na":
        winner = details.get("winner")
        return None if winner not in {"home", "away"} else _bool(selection == winner)
    if key == "ht_ft":
        if result["ht_home"] is None or market["period"] != "FT":
            return None
        ht = _side(result["ht_home"], result["ht_away"])
        ft = _side(result["home_score"], result["away_score"])
        code = {"home": "H", "draw": "D", "away": "A"}
        return _bool(selection == f"{code[ht]}/{code[ft]}")
    scores = _scores(market, result)
    if scores is None:
        return None
    h, a = scores
    side = _side(h, a)
    if key in {"1x2", "handicap_1x2"}:
        if key == "handicap_1x2":
            if not market["line"] or ":" not in market["line"]:
                return None
            plus_h, plus_a = (int(x) for x in market["line"].split(":"))
            side = _side(h + plus_h, a + plus_a)
        return _bool(selection == side)
    if key == "double_chance":
        return _bool(
            side
            in {
                "home_draw": {"home", "draw"},
                "draw_away": {"draw", "away"},
                "home_away": {"home", "away"},
            }.get(selection, set())
        )
    if key == "match_winner":
        return None if side == "draw" else _bool(selection == side)
    if key == "draw_no_bet":
        return Outcome.VOID if side == "draw" else _bool(selection == side)
    if key in {"total", "team_total_home", "team_total_away"}:
        if market["line"] is None:
            return None
        count = {"total": h + a, "team_total_home": h, "team_total_away": a}[key]
        line = Decimal(market["line"])
        if count == line:
            return None  # push: refund rules are operator-specific
        return _bool((count > line) == (selection == "over"))
    if key == "btts":
        return _bool((h > 0 and a > 0) == (selection == "yes"))
    if key in {"home_to_score", "away_to_score"}:
        goals = h if key == "home_to_score" else a
        return _bool((goals > 0) == (selection == "yes"))
    if key == "ht_or_ft":
        if result["ht_home"] is None:
            return None
        sides = {_side(result["ht_home"], result["ht_away"]), side}
        return _bool(selection in sides)
    return None


def _side(h: int, a: int) -> str:
    return "home" if h > a else "away" if h < a else "draw"


def _bool(won: bool) -> Outcome:
    return Outcome.WIN if won else Outcome.LOSS


def settle_coupon(
    store: Store, coupon_id: str, profiles: dict[str, BookmakerProfile], *, now: datetime
) -> dict:
    coupon = store.get("coupons", coupon_id)
    if coupon is None:
        raise ValueError("Unknown coupon")
    legs = store.rows(
        """SELECT l.*, s.key selection, m.market_key, m.period, m.overtime, m.line,
        e.id event_id FROM coupon_legs l JOIN selections s ON s.id=l.selection_id
        JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id
        WHERE l.coupon_id=?""",
        (coupon_id,),
    )
    outcomes, details = [], []
    for leg in legs:
        result = latest_result(store, leg["event_id"])
        outcome = resolve(leg, leg["selection"], result) if result else None
        outcomes.append(outcome)
        details.append(
            {
                "selection": leg["selection"],
                "market": leg["market_key"],
                "result_id": result["id"] if result else None,
                "outcome": outcome.value if outcome else None,
            }
        )
    if any(o is None for o in outcomes):
        missing = sum(o is None for o in outcomes)
        return {"status": "pending", "unresolved_legs": missing, "legs": details}
    if all(o == Outcome.WIN for o in outcomes):
        outcome = Outcome.WIN
    elif any(o == Outcome.LOSS for o in outcomes):
        outcome = Outcome.LOSS
    elif len(outcomes) == 1:
        outcome = outcomes[0]
    else:
        return {"status": "manual", "reason": "Zwrot w AKO wymaga reguł operatora", "legs": details}
    profile = profiles[coupon["bookmaker_id"]]
    if profile.cost.id != coupon["cost_profile_id"]:
        return {"status": "manual", "reason": "Profil kosztów kuponu niedostępny", "legs": details}
    on_date = datetime.fromisoformat(coupon["first_start_at"]).date()
    try:
        flow = settle(
            Decimal(coupon["stake"]),
            Decimal(coupon["total_odds"]),
            outcome,
            profile.cost,
            on_date=on_date,
        )
    except ValueError as error:
        return {"status": "manual", "reason": str(error), "legs": details}
    previous = latest_settlement(store, coupon_id)
    if (
        previous
        and previous["outcome"] == outcome.value
        and json.loads(previous["legs_json"]) == details
    ):
        return {
            "status": "settled",
            "settlement_id": previous["id"],
            "net_profit": previous["net_profit"],
        }
    key = digest(["settlement", coupon_id, outcome.value, details, iso(now)])
    flow_json = {k: str(v) for k, v in asdict(flow).items()}
    with store.transaction():
        store.insert(
            "settlements",
            id=key,
            coupon_id=coupon_id,
            outcome=outcome.value,
            payout=str(flow.payout),
            net_profit=str(flow.net_profit),
            cash_flow_json=canonical(flow_json),
            legs_json=canonical(details),
            settled_at=iso(now),
            supersedes_id=previous["id"] if previous else None,
        )
    return {"status": "settled", "settlement_id": key, "net_profit": str(flow.net_profit)}


def latest_settlement(store: Store, coupon_id: str) -> dict | None:
    rows = store.rows(
        "SELECT * FROM settlements WHERE coupon_id=? ORDER BY settled_at DESC, id DESC LIMIT 1",
        (coupon_id,),
    )
    return rows[0] if rows else None


def performance(store: Store) -> dict[str, dict]:
    """ROI = sum(net profit) / sum(stake) over settled coupons, separately for each mode."""
    out: dict[str, dict] = {}
    for coupon in store.rows("SELECT * FROM coupons ORDER BY created_at"):
        stats = out.setdefault(
            coupon["mode"],
            {"coupons": 0, "settled": 0, "stake": Decimal(0), "profit": Decimal(0), "wins": 0},
        )
        stats["coupons"] += 1
        settlement = latest_settlement(store, coupon["id"])
        if settlement is None:
            continue
        stats["settled"] += 1
        stats["stake"] += Decimal(coupon["stake"])
        stats["profit"] += Decimal(settlement["net_profit"])
        stats["wins"] += settlement["outcome"] == "win"
    for stats in out.values():
        stats["roi"] = (stats["profit"] / stats["stake"]) if stats["stake"] else None
        stats["hit_rate"] = stats["wins"] / stats["settled"] if stats["settled"] else None
    return out


def closing_line_value(store: Store, coupon_id: str) -> list[dict]:
    """Entry price vs the last observed price of the same selection before the start."""
    rows = store.rows(
        """SELECT l.price entry, l.selection_id, e.start_at FROM coupon_legs l
        JOIN selections s ON s.id=l.selection_id JOIN markets m ON m.id=s.market_id
        JOIN events e ON e.id=m.event_id WHERE l.coupon_id=?""",
        (coupon_id,),
    )
    out = []
    for row in rows:
        closing = store.rows(
            """SELECT o.price, q.valid_at FROM odds o JOIN quote_sets q ON q.id=o.quote_set_id
            WHERE o.selection_id=? AND q.status='ok' AND q.valid_at IS NOT NULL
            AND q.valid_at < ? ORDER BY q.valid_at DESC LIMIT 1""",
            (row["selection_id"], row["start_at"]),
        )
        if not closing:
            out.append({"entry": row["entry"], "close": None, "clv": None})
            continue
        entry, close = Decimal(row["entry"]), Decimal(closing[0]["price"])
        out.append(
            {
                "entry": str(entry),
                "close": str(close),
                "clv": float(entry / close - 1),
                "close_valid_at": closing[0]["valid_at"],
            }
        )
    return out


def coupons_table(store: Store) -> list[dict]:
    out = []
    for coupon in store.rows("SELECT * FROM coupons ORDER BY created_at DESC"):
        settlement = latest_settlement(store, coupon["id"])
        legs = store.rows(
            """SELECT e.home, e.away, e.start_at, m.market_key, m.period, m.line, s.key selection,
            l.price FROM coupon_legs l JOIN selections s ON s.id=l.selection_id
            JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id
            WHERE l.coupon_id=?""",
            (coupon["id"],),
        )
        out.append(
            {
                **coupon,
                "legs": legs,
                "outcome": settlement["outcome"] if settlement else None,
                "net_profit": settlement["net_profit"] if settlement else None,
            }
        )
    return out
