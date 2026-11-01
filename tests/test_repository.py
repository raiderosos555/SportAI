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


def test_repeated_import_is_detected(store, sts_fixture_pdf, import_sts):
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


def later_document(sts_fixture_pdf, *, bump: Decimal, drop: str | None = None, hour: int = 14):
    """Same fixture re-published later: 4101 1X2 repriced, optionally one market removed."""
    result, _ = parse_pdf(sts_fixture_pdf, file_name="oferta-dzisiaj-www.pdf")
    changed = []
    for row in result.rows:
        if drop and row.offer_code == drop:
            continue
        if row.offer_code == "4101" and row.market_key == "1x2":
            selections = tuple(replace(s, odds=s.odds + bump) for s in row.selections)
            row = replace(row, selections=selections)
        changed.append(row)
    return replace(
        result, rows=tuple(changed), document_generated_at=datetime(2026, 10, 31, hour, tzinfo=UTC)
    )


def test_new_document_adds_history_without_overwriting(store, sts_fixture_pdf, import_sts):
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
        for r in offer_table(store, day="2026-10-31")
        if r["offer_code"] == "4101" and r["market_key"] == "1x2"
    )
    assert latest["selections"]["home"]["price"] == Decimal("2.15")
    history = [h for h in quote_history(store, latest["market_id"]) if h["key"] == "home"]
    assert [h["price"] for h in history] == ["2.10", "2.15"]


def test_records_are_append_only(store, import_sts):
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


def test_review_rows_are_stored_but_separated(store, import_sts):
    everything = offer_table(store, day="2026-10-31")
    usable = offer_table(store, day="2026-10-31", include_review=False)
    assert len(everything) > len(usable)
    assert all(r["status"] == "ok" for r in usable)
    assert store.scalar("SELECT COUNT(*) FROM review_items") == 1
    assert offer_table(store, data_kinds=("demo",)) == []


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


def test_withdrawn_market_is_detected_only_from_same_kind_documents(
    store, sts_fixture_pdf, import_sts
):
    later = later_document(sts_fixture_pdf, bump=Decimal("0"), drop="4105")
    import_offer(
        store,
        later,
        sha256="c" * 64,
        file_name="later.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=datetime(2026, 10, 31, 14, 5, tzinfo=UTC),
    )
    rows = {(r["offer_code"], r["market_key"]): r for r in offer_table(store, day="2026-10-31")}
    assert rows[("4105", "draw_no_bet")]["presence"] == "withdrawn"
    assert rows[("4101", "1x2")]["presence"] == "current"


def test_empty_document_does_not_withdraw_markets(store, sts_fixture_pdf, import_sts):
    empty = replace(
        later_document(sts_fixture_pdf, bump=Decimal("0")),
        rows=(),
        review=(),
    )
    import_offer(
        store,
        empty,
        sha256="d" * 64,
        file_name="empty.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=datetime(2026, 10, 31, 15, tzinfo=UTC),
    )
    assert {r["presence"] for r in offer_table(store, day="2026-10-31")} == {"current"}


def test_only_sts_reaches_the_active_store(store, sts_fixture_pdf):
    result, document = parse_pdf(sts_fixture_pdf)
    foreign = replace(result, bookmaker="superbet")
    with pytest.raises(ValueError, match="wyłącznie ofertę STS"):
        import_offer(
            store,
            foreign,
            sha256=document.sha256,
            file_name="x.pdf",
            source_id="sts_pdf_manual",
            data_kind="prospective",
            ingested_at=datetime(2026, 10, 31, 12, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="Nieaktywne"):
        import_offer(
            store,
            result,
            sha256=document.sha256,
            file_name="x.pdf",
            source_id="superbet_pdf_manual",
            data_kind="prospective",
            ingested_at=datetime(2026, 10, 31, 12, tzinfo=UTC),
        )
