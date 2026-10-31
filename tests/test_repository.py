"""Local SQLite store: imports, idempotency, history without overwriting, append-only."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from sports_edge.repository import counts, freshness, import_offer, offer_table, quote_history
from sports_edge.storage import SCHEMA_VERSION, Store

pytest.importorskip("pymupdf")

from sports_edge.pdf.engine import parse_pdf  # noqa: E402


def test_local_database_schema(tmp_path):
    with Store(tmp_path / "data" / "app.db") as db:
        assert db.scalar("PRAGMA user_version") == SCHEMA_VERSION
        for table in (
            "imports",
            "events",
            "markets",
            "odds",
            "predictions",
            "coupons",
            "settlements",
        ):
            assert table in db.tables
    assert (tmp_path / "data" / "app.db").is_file()


def test_repeated_import_is_detected(store, sts_fixture_pdf, import_both):
    before = counts(store)
    result, document = parse_pdf(sts_fixture_pdf, file_name="again.pdf")
    outcome = import_offer(
        store,
        result,
        sha256=document.sha256,
        file_name="again.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=datetime(2026, 10, 31, 13, tzinfo=UTC),
    )
    assert outcome.duplicate is True
    assert counts(store) == before


def test_new_document_adds_history_without_overwriting(store, sts_fixture_pdf, import_both):
    result, _ = parse_pdf(sts_fixture_pdf)
    changed = []
    for row in result.rows:
        if row.offer_code == "4101" and row.market_key == "1x2":
            selections = tuple(replace(s, odds=s.odds + Decimal("0.05")) for s in row.selections)
            row = replace(row, selections=selections)
        changed.append(row)
    later = replace(
        result,
        rows=tuple(changed),
        document_generated_at=datetime(2026, 10, 31, 14, tzinfo=UTC),
    )
    import_offer(
        store,
        later,
        sha256="b" * 64,
        file_name="later.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=datetime(2026, 10, 31, 14, 5, tzinfo=UTC),
    )
    latest = next(
        r
        for r in offer_table(store, bookmaker="sts", day="2026-10-31")
        if r["offer_code"] == "4101" and r["market_key"] == "1x2"
    )
    assert latest["selections"]["home"]["price"] == Decimal("2.15")
    history = [h for h in quote_history(store, latest["market_id"]) if h["key"] == "home"]
    assert [h["price"] for h in history] == ["2.10", "2.15"]


def test_records_are_append_only(store, import_both):
    for sql in (
        "UPDATE odds SET price='9.99'",
        "DELETE FROM quote_sets",
        "UPDATE imports SET quality=1",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            store.connection.execute(sql)
    with pytest.raises(ValueError, match="identity_conflict"):
        row = store.rows("SELECT * FROM bookmakers WHERE id='sts'")[0]
        store.insert("bookmakers", id=row["id"], name="changed")


def test_review_rows_are_stored_but_separated(store, import_both):
    everything = offer_table(store, bookmaker="sts", day="2026-10-31")
    usable = offer_table(store, bookmaker="sts", day="2026-10-31", include_review=False)
    assert len(everything) > len(usable)
    assert all(r["status"] == "ok" for r in usable)
    assert store.scalar("SELECT COUNT(*) FROM review_items") == 1
    assert offer_table(store, bookmaker="sts", data_kinds=("demo",)) == []


def test_expired_and_stale_odds_are_recognised():
    start = "2026-10-31T20:00:00+00:00"
    valid = "2026-10-31T12:00:00+00:00"
    age = timedelta(hours=3)
    assert freshness(valid, start, datetime(2026, 10, 31, 13, tzinfo=UTC), age) == "fresh"
    assert freshness(valid, start, datetime(2026, 10, 31, 16, tzinfo=UTC), age) == "stale"
    assert freshness(valid, start, datetime(2026, 10, 31, 20, tzinfo=UTC), age) == "expired"
    assert freshness(None, start, datetime(2026, 10, 31, 13, tzinfo=UTC), age) == "unknown"
    future_document = "2026-10-31T14:00:00+00:00"
    assert (
        freshness(future_document, start, datetime(2026, 10, 31, 13, tzinfo=UTC), age) == "unknown"
    )
