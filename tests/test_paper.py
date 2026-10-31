"""Virtual coupons, predictions history, settlement with taxes, ROI and CLV."""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from sports_edge.models.registry import register_version, save_prediction
from sports_edge.paper import (
    closing_line_value,
    create_coupon,
    performance,
    record_result,
    resolve,
    settle_coupon,
)
from sports_edge.repository import offer_table
from sports_edge.settlement import Outcome

pytest.importorskip("pymupdf")

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def odds_id(store, home, market="1x2", selection="home", bookmaker="sts"):
    for row in offer_table(store, bookmaker=bookmaker, day="2026-10-31"):
        if row["home"] == home and row["market_key"] == market:
            return row["selections"][selection]["odds_id"], row["event_id"]
    raise LookupError(home)


def test_prediction_history_is_immutable(store, import_both):
    from sports_edge.analysis.policy import Policy

    version_id, status, reasons = register_version(
        store,
        family="t",
        sport="football",
        description="test",
        version="1",
        created_at=NOW,
        trained_through=None,
        training_data={},
        validation={"n_out_of_sample": 10},
        policy=Policy(),
    )
    assert status == "experimental" and len(reasons) == 4
    first = save_prediction(
        store,
        model_version_id=version_id,
        event_id=None,
        market_key="1x2",
        period="FT",
        line=None,
        probabilities={"home": 0.5, "draw": 0.3, "away": 0.2},
        uncertainty={},
        inputs={},
        kind="prospective",
        created_at=NOW,
    )
    second = save_prediction(
        store,
        model_version_id=version_id,
        event_id=None,
        market_key="1x2",
        period="FT",
        line=None,
        probabilities={"home": 0.4, "draw": 0.3, "away": 0.3},
        uncertainty={},
        inputs={},
        kind="prospective",
        created_at=NOW + timedelta(hours=1),
    )
    assert first != second and store.scalar("SELECT COUNT(*) FROM predictions") == 2
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute("UPDATE predictions SET probabilities_json='{}'")
    with pytest.raises(ValueError):
        save_prediction(
            store,
            model_version_id=version_id,
            event_id=None,
            market_key="1x2",
            period="FT",
            line=None,
            probabilities={"home": 0.9},
            uncertainty={},
            inputs={},
            kind="prospective",
            created_at=NOW,
        )


def test_coupon_must_be_saved_before_start(store, import_both, profiles):
    leg, _ = odds_id(store, "Legia Warszawa")
    coupon = create_coupon(
        store,
        odds_ids=[leg],
        stake=Decimal("10.00"),
        mode="paper_manual",
        profile=profiles["sts"],
        now=NOW,
    )
    assert store.get("coupons", coupon)["kind"] == "solo"
    with pytest.raises(ValueError, match="before the event starts"):
        create_coupon(
            store,
            odds_ids=[leg],
            stake=Decimal("10.00"),
            mode="paper_manual",
            profile=profiles["sts"],
            now=datetime(2026, 10, 31, 20, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="bookmaker"):
        create_coupon(
            store,
            odds_ids=[leg],
            stake=Decimal("10.00"),
            mode="paper_manual",
            profile=profiles["superbet"],
            now=NOW,
        )
    same_event, _ = odds_id(store, "Legia Warszawa", "double_chance", "home_draw")
    with pytest.raises(ValueError, match="correlated"):
        create_coupon(
            store,
            odds_ids=[leg, same_event],
            stake=Decimal("10.00"),
            mode="paper_manual",
            profile=profiles["sts"],
            now=NOW,
        )


def test_solo_settlement_with_stake_tax_and_result_correction(store, import_both, profiles):
    leg, event = odds_id(store, "Legia Warszawa")
    coupon = create_coupon(
        store,
        odds_ids=[leg],
        stake=Decimal("10.00"),
        mode="paper_system",
        profile=profiles["sts"],
        now=NOW,
    )
    assert settle_coupon(store, coupon, profiles, now=NOW)["status"] == "pending"
    later = datetime(2026, 10, 31, 22, tzinfo=UTC)
    record_result(
        store,
        event_id=event,
        status="completed",
        home_score=2,
        away_score=1,
        ht_home=1,
        ht_away=0,
        now=later,
    )
    settled = settle_coupon(store, coupon, profiles, now=later)
    # 10 PLN stake, 12% stake tax, odds 2.40: payout 8.80 x 2.40 = 21.12, profit 11.12.
    assert settled == {
        "status": "settled",
        "settlement_id": settled["settlement_id"],
        "net_profit": "11.12",
    }
    record_result(
        store,
        event_id=event,
        status="completed",
        home_score=1,
        away_score=1,
        ht_home=1,
        ht_away=0,
        now=later + timedelta(hours=1),
    )
    corrected = settle_coupon(store, coupon, profiles, now=later + timedelta(hours=1))
    assert corrected["net_profit"] == "-10.00"
    rows = store.rows("SELECT * FROM settlements ORDER BY settled_at")
    assert len(rows) == 2 and rows[1]["supersedes_id"] == rows[0]["id"]
    stats = performance(store)["paper_system"]
    assert stats["settled"] == 1 and stats["profit"] == Decimal("-10.00") and stats["roi"] == -1


def test_ako_settlement_and_separate_modes(store, import_both, profiles):
    legia, legia_event = odds_id(store, "Legia Warszawa")
    gornik, gornik_event = odds_id(store, "Górnik Zabrze")
    ako = create_coupon(
        store,
        odds_ids=[legia, gornik],
        stake=Decimal("10.00"),
        mode="simulation",
        profile=profiles["sts"],
        now=NOW,
    )
    assert store.get("coupons", ako)["total_odds"] == "4.8000"
    later = datetime(2026, 10, 31, 22, tzinfo=UTC)
    record_result(
        store, event_id=legia_event, status="completed", home_score=3, away_score=0, now=later
    )
    assert settle_coupon(store, ako, profiles, now=later)["status"] == "pending"
    record_result(
        store, event_id=gornik_event, status="completed", home_score=2, away_score=0, now=later
    )
    result = settle_coupon(store, ako, profiles, now=later)
    assert result["net_profit"] == "32.24"  # 8.80 x 4.80 - 10
    stats = performance(store)
    assert set(stats) == {"simulation"} and stats["simulation"]["roi"] == Decimal("3.224")


def test_generic_resolution_rules():
    result = {
        "status": "completed",
        "verified": 1,
        "home_score": 2,
        "away_score": 2,
        "ht_home": 0,
        "ht_away": 1,
        "details_json": "{}",
    }
    ft = {"market_key": "1x2", "period": "FT", "overtime": "false", "line": None}
    assert resolve(ft, "draw", result) == Outcome.WIN
    assert resolve({**ft, "market_key": "total", "line": "3.5"}, "over", result) == Outcome.WIN
    assert resolve({**ft, "market_key": "total", "line": "4"}, "over", result) is None  # push
    assert resolve({**ft, "market_key": "btts"}, "yes", result) == Outcome.WIN
    assert resolve({**ft, "market_key": "draw_no_bet"}, "home", result) == Outcome.VOID
    assert resolve({**ft, "period": "H1"}, "away", result) == Outcome.WIN
    assert resolve({**ft, "period": "H2"}, "home", result) == Outcome.WIN
    assert resolve({**ft, "market_key": "ht_ft"}, "A/D", result) == Outcome.WIN
    assert (
        resolve({**ft, "market_key": "handicap_1x2", "line": "0:1"}, "away", result) == Outcome.WIN
    )
    assert resolve({**ft, "market_key": "handicap_unsigned", "line": "1.5"}, "home", result) is None
    assert resolve(ft, "home", {**result, "verified": 0}) is None
    winner = {"market_key": "match_winner", "period": "MATCH", "overtime": "true", "line": None}
    assert resolve(winner, "home", result) is None  # draw after regulation, OT score unknown
    assert (
        resolve(winner, "home", {**result, "details_json": '{"after_ot": [3, 2]}'}) == Outcome.WIN
    )


def test_clv_uses_last_quote_before_start(store, import_both, profiles):
    leg, _ = odds_id(store, "Legia Warszawa")
    coupon = create_coupon(
        store,
        odds_ids=[leg],
        stake=Decimal("10.00"),
        mode="paper_manual",
        profile=profiles["sts"],
        now=NOW,
    )
    clv = closing_line_value(store, coupon)
    assert clv == [
        {"entry": "2.40", "close": "2.40", "clv": 0.0, "close_valid_at": clv[0]["close_valid_at"]}
    ]
