"""History of STS prices: snapshots, changes, pre-match horizons and closing-line value.

Only prices actually read from imported STS documents are used. A horizon (e.g. 6 hours
before kick-off) is filled only by an observation made inside its tolerance window; nothing
is interpolated. The closing reference must come from the last ``CLOSING_WINDOW`` before the
start, otherwise CLV is reported as unavailable.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sports_edge.storage import Store

HORIZONS = (("24h", 24), ("12h", 12), ("6h", 6), ("3h", 3), ("1h", 1))
CLOSING_WINDOW = timedelta(minutes=30)


@dataclass(frozen=True)
class Observation:
    valid_at: datetime | None  # document generation time (STS PDF metadata/footer)
    fetched_at: datetime | None  # download time, when the app fetched the file itself
    ingested_at: datetime
    import_id: str
    offer_kind: str
    price: Decimal

    @property
    def observed_at(self) -> datetime:
        return self.valid_at or self.ingested_at


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def selection_history(store: Store, selection_id: str) -> list[Observation]:
    """Every observation of a selection's price: the stored quotes and — TASK 016 — the
    documents that confirmed an unchanged quote (``quote_observations``)."""
    rows = store.rows(
        """SELECT q.valid_at, i.fetched_at, q.ingested_at, q.import_id, i.offer_kind, o.price
        FROM selections s JOIN quote_observations q ON q.market_id=s.market_id
        JOIN odds o ON o.quote_set_id=q.source_id AND o.selection_id=s.id
        JOIN imports i ON i.id=q.import_id
        WHERE s.id=? AND q.status='ok' AND i.bookmaker_id='sts'
        AND i.data_kind IN ('prospective','archive')
        ORDER BY q.valid_at, q.ingested_at""",
        (selection_id,),
    )
    return [
        Observation(
            _dt(r["valid_at"]),
            _dt(r["fetched_at"]),
            datetime.fromisoformat(r["ingested_at"]),
            r["import_id"],
            r["offer_kind"],
            Decimal(r["price"]),
        )
        for r in rows
    ]


def changes(observations: list[Observation]) -> list[dict]:
    """Consecutive price movements with direction, size and implied-probability shift."""
    out = []
    for before, after in zip(observations, observations[1:], strict=False):
        if after.price == before.price:
            continue
        out.append(
            {
                "from": before.price,
                "to": after.price,
                "at": after.observed_at,
                "direction": "up" if after.price > before.price else "down",
                "delta": after.price - before.price,
                "pct": float(after.price / before.price - 1),
                "implied_shift": float(1 / after.price - 1 / before.price),
                "minutes_between": (after.observed_at - before.observed_at).total_seconds() / 60,
            }
        )
    return out


def _tolerance(hours: int) -> timedelta:
    return max(timedelta(minutes=30), timedelta(hours=hours) / 4)


def horizon_snapshots(observations: list[Observation], start: datetime) -> dict[str, dict | None]:
    """Latest observation inside [start - h - tolerance, start - h] for each horizon h."""
    out: dict[str, dict | None] = {}
    for label, hours in HORIZONS:
        target = start - timedelta(hours=hours)
        window = [o for o in observations if target - _tolerance(hours) <= o.observed_at <= target]
        best = max(window, key=lambda o: o.observed_at) if window else None
        out[label] = (
            {"price": best.price, "observed_at": best.observed_at, "import_id": best.import_id}
            if best
            else None
        )
    closing = closing_observation(observations, start)
    out["closing"] = (
        {"price": closing.price, "observed_at": closing.observed_at, "import_id": closing.import_id}
        if closing
        else None
    )
    return out


def closing_observation(observations: list[Observation], start: datetime) -> Observation | None:
    """Last legally observed STS price inside the closing window before the start."""
    window = [o for o in observations if start - CLOSING_WINDOW <= o.observed_at < start]
    return max(window, key=lambda o: o.observed_at) if window else None


def clv(entry: Decimal, observations: list[Observation], start: datetime) -> dict:
    closing = closing_observation(observations, start)
    if closing is None:
        last = max(
            (o for o in observations if o.observed_at < start),
            key=lambda o: o.observed_at,
            default=None,
        )
        return {
            "clv": None,
            "status": "niedostępne — brak notowania w ostatnich "
            f"{int(CLOSING_WINDOW.total_seconds() // 60)} min przed startem",
            "last_seen": last.observed_at if last else None,
        }
    return {
        "clv": float(entry / closing.price - 1),
        "status": "dostępne",
        "closing_price": closing.price,
        "closing_observed_at": closing.observed_at,
    }


def missing_report(store: Store, *, day: str) -> dict:
    """How many STS markets of a day have an observation at each horizon (no filling)."""
    markets = store.rows(
        """SELECT m.id market_id, e.start_at, (SELECT s.id FROM selections s
        WHERE s.market_id=m.id ORDER BY s.key LIMIT 1) selection_id
        FROM markets m JOIN events e ON e.id=m.event_id
        WHERE e.bookmaker_id='sts' AND e.offer_date=? AND e.start_at IS NOT NULL""",
        (day,),
    )
    labels = [label for label, _ in HORIZONS] + ["closing"]
    present = dict.fromkeys(labels, 0)
    for market in markets:
        if market["selection_id"] is None:
            continue
        snapshots = horizon_snapshots(
            selection_history(store, market["selection_id"]),
            datetime.fromisoformat(market["start_at"]),
        )
        for label in labels:
            present[label] += snapshots[label] is not None
    total = len(markets)
    return {
        "day": day,
        "markets": total,
        "observed": present,
        "missing": {label: total - count for label, count in present.items()},
    }


def offer_changes(store: Store, older: str, newer: str) -> dict:
    """Markets added, withdrawn and repriced between two STS imports (ok rows only)."""

    def prices(import_id: str) -> dict[tuple[str, str], str]:
        return {
            (r["market_id"], r["key"]): r["price"]
            for r in store.rows(
                """SELECT q.market_id, s.key, o.price FROM quote_observations q
                JOIN odds o ON o.quote_set_id=q.source_id
                JOIN selections s ON s.id=o.selection_id
                WHERE q.import_id=? AND q.status='ok'""",
                (import_id,),
            )
        }

    a, b = prices(older), prices(newer)
    markets_a = {m for m, _ in a}
    markets_b = {m for m, _ in b}
    repriced = [k for k in set(a) & set(b) if a[k] != b[k]]
    return {
        "added_markets": len(markets_b - markets_a),
        "withdrawn_markets": len(markets_a - markets_b),
        "common_selections": len(set(a) & set(b)),
        "repriced_selections": len(repriced),
        "up": sum(Decimal(b[k]) > Decimal(a[k]) for k in repriced),
        "down": sum(Decimal(b[k]) < Decimal(a[k]) for k in repriced),
    }
