"""JSON and CSV exports of local results. Files are written only to the local disk."""

import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sports_edge.repository import offer_table
from sports_edge.storage import Store

TABLES = ("imports", "analyses", "predictions", "coupons", "coupon_legs", "results", "settlements")


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def offers_flat(store: Store, **filters) -> list[dict]:
    out = []
    for row in offer_table(store, **filters):
        for key, quote in row["selections"].items():
            out.append(
                {
                    "bookmaker": row["bookmaker_id"],
                    "offer_date": row["offer_date"],
                    "start_at": row["start_at"],
                    "sport": row["sport"],
                    "league": row["league"],
                    "event": " - ".join(row["participants"]) or row["name_raw"],
                    "market": row["market_key"],
                    "period": row["period"],
                    "overtime": row["overtime"],
                    "line": row["line"],
                    "selection": key,
                    "odds": str(quote["price"]),
                    "offer_code": row["offer_code"],
                    "status": row["status"],
                    "quality": row["quality"],
                    "valid_at": row["valid_at"],
                    "ingested_at": row["ingested_at"],
                    "import_id": row["import_id"],
                }
            )
    return out


def snapshot(store: Store) -> dict:
    data = {table: store.rows(f"SELECT * FROM {table}") for table in TABLES}
    data["offers_latest"] = offers_flat(store)
    return data


def export_json(store: Store, target: Path, *, now: datetime) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"exported_at": now.isoformat(), "data": _plain(snapshot(store))}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def export_csv(store: Store, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, rows in snapshot(store).items():
        path = directory / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            if rows:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter=";")
                writer.writeheader()
                writer.writerows(_plain(rows))
        written.append(path)
    return written
