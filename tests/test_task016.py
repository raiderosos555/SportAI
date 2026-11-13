"""TASK 016 — data hygiene: thrifty STS imports, WAL checkpoint, cyclic backups with
retention, alerts without noise, manual absences with a source, decisions without "bez zmian".

Every team, price, alert and absence below is SYNTHETIC test data (the fixture STS offer of
offer_fixtures.py), not a real fact.
"""

import json
import sqlite3
from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_task010 as t10

from sports_edge import (
    alerts,
    automation,
    backup,
    coupon_store,
    decisions,
    history,
    maintenance,
    prematch,
    reassessment,
    services,
)
from sports_edge import prospective_eval as pe
from sports_edge.ingestion.types import digest, iso
from sports_edge.repository import (
    counts,
    ensure_reference_data,
    import_offer,
    odds_freshness,
    offer_table,
    quote_history,
)
from sports_edge.settings import AppSettings, load_settings
from sports_edge.storage import SCHEMA_VERSION, Store

DAY = "2026-10-31"
T8 = datetime(2026, 10, 31, 8, tzinfo=UTC)
T11 = datetime(2026, 10, 31, 11, tzinfo=UTC)
T14 = datetime(2026, 10, 31, 14, tzinfo=UTC)
T16 = datetime(2026, 10, 31, 16, tzinfo=UTC)
T1945 = datetime(2026, 10, 31, 19, 45, tzinfo=UTC)  # inside the closing window of 20:00 UTC
SRC = Path(services.__file__).resolve().parent


# ---------------------------------------------------------------- documents and stores


def _document(at: datetime, *, prices: dict | None = None, without: tuple = (), twice: tuple = ()):
    """The fixture offer as a document generated 5 minutes before ``at``; ``prices``
    changes the home price of a 1X2 row, ``without`` removes rows (withdrawn markets),
    ``twice`` prints the 1X2 market of a row a second time with another home price."""
    result = t10._parsed(prices)
    rows = []
    for row in result.rows:
        if row.offer_code in without:
            continue
        rows.append(row)
        if row.offer_code in twice and row.market_key == "1x2":
            again = tuple(
                dc_replace(s, odds=s.odds + Decimal("0.15")) if s.key == "home" else s
                for s in row.selections
            )
            rows.append(dc_replace(row, selections=again, y=row.y + 0.5))
    return dc_replace(result, rows=tuple(rows), document_generated_at=at - timedelta(minutes=5))


def _load(store, sha: str, at: datetime, *, thrifty: bool, **changes) -> str:
    return import_offer(
        store,
        _document(at, **changes),
        sha256=sha * 64,
        file_name=f"oferta_{sha}.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=at,
        thrifty=thrifty,
    ).import_id


SEQUENCE = (
    ("a", T8, {}),
    ("b", T11, {}),  # nothing changed
    ("c", T14, {"prices": {"4101": "2.30"}, "without": ("4202",)}),  # a new price, a withdrawal
    ("d", T16, {"prices": {"4101": "2.30"}, "without": ("4202",)}),  # nothing changed again
    ("e", T1945, {"prices": {"4101": "2.30"}, "without": ("4202",)}),  # the closing document
)


def _pair(upto: int = len(SEQUENCE)) -> tuple[Store, Store, list[str]]:
    """Two stores with the same documents: the full write of before and the thrifty one."""
    full, lean = Store(":memory:"), Store(":memory:")
    ids = []
    for db, thrifty in ((full, False), (lean, True)):
        ensure_reference_data(db)
        ids = [
            _load(db, sha, at, thrifty=thrifty, **changes) for sha, at, changes in SEQUENCE[:upto]
        ]
    return full, lean, ids


def _plain(rows: list[dict]) -> list[dict]:
    """``offer_table`` rows without the storage id of the odds row (a confirmed quote shares
    the odds rows of the quote set it confirmed); everything else must be identical."""
    out = []
    for row in rows:
        row = dict(row)
        row["selections"] = {
            key: {k: v for k, v in quote.items() if k != "odds_id"}
            for key, quote in row["selections"].items()
        }
        out.append(row)
    return out


def _selection(store, code: str, key: str = "home", market: str = "1x2") -> dict:
    row = next(
        r
        for r in offer_table(store, day=DAY)
        if r["offer_code"] == code and r["market_key"] == market and r["period"] == "FT"
    )
    return {**row["selections"][key], "event_id": row["event_id"], "market_id": row["market_id"]}


# 1 ---------------------------------------------------------------- thrifty import (§2.2)


def test_an_unchanged_document_adds_confirmations_not_odds():
    store = Store(":memory:")
    ensure_reference_data(store)
    first = _load(store, "a", T8, thrifty=True)
    before = {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("odds", "quote_sets")}
    assert before["odds"] > 100 and store.scalar("SELECT COUNT(*) FROM quote_confirmations") == 0
    second = _load(store, "b", T11, thrifty=True)
    after = {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("odds", "quote_sets")}
    assert after == before  # not one copied quote set or price
    confirmations = store.rows("SELECT * FROM quote_confirmations")
    assert len(confirmations) == before["quote_sets"]  # every quote of the document confirmed
    assert {c["import_id"] for c in confirmations} == {second}
    stored = {r["id"]: r["import_id"] for r in store.rows("SELECT id, import_id FROM quote_sets")}
    assert {stored[c["quote_set_id"]] for c in confirmations} == {first}
    assert counts(store)["quote_confirmations"] == len(confirmations)
    # the view lists every observation: the stored quote sets and their confirmations
    assert store.scalar("SELECT COUNT(*) FROM quote_observations") == 2 * before["quote_sets"]
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        store.connection.execute("DELETE FROM quote_confirmations")
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        store.connection.execute("UPDATE quote_confirmations SET page=1")


def test_a_new_price_and_a_withdrawn_market_are_written_as_before():
    full, lean, ids = _pair(upto=3)
    changed = lean.rows(
        """SELECT q.id, q.import_id FROM quote_sets q JOIN markets m ON m.id=q.market_id
        WHERE q.offer_code='4101' AND m.market_key='1x2' AND m.period='FT'
        ORDER BY q.ingested_at"""
    )
    assert [r["import_id"] for r in changed] == [ids[0], ids[2]]  # a full quote set again
    prices = lean.rows(
        """SELECT o.price FROM odds o JOIN selections s ON s.id=o.selection_id
        WHERE o.quote_set_id=? AND s.key='home'""",
        (changed[1]["id"],),
    )
    assert [p["price"] for p in prices] == ["2.30"]
    # the withdrawn market: nothing written by the third document, in either store
    for db in (full, lean):
        rows = db.rows(
            """SELECT q.import_id FROM quote_observations q WHERE q.offer_code='4202'
            AND q.import_id=?""",
            (ids[2],),
        )
        assert rows == []
        row = next(r for r in offer_table(db, day=DAY) if r["offer_code"] == "4202")
        assert row["presence"] == "withdrawn" and row["import_id"] == ids[1]
    assert lean.scalar("SELECT COUNT(*) FROM odds") < full.scalar("SELECT COUNT(*) FROM odds")
    assert lean.scalar("SELECT COUNT(*) FROM quote_sets") < full.scalar(
        "SELECT COUNT(*) FROM quote_sets"
    )


def test_a_market_printed_twice_in_one_document_is_written_in_full():
    """Found on the copy of the real database (1.7% of the quote sets): two lines of one
    document read as the same market. Which line counts is decided by their order in the
    document, so such a market is never confirmed — it is written as before."""
    full, lean = Store(":memory:"), Store(":memory:")
    for db, thrifty in ((full, False), (lean, True)):
        ensure_reference_data(db)
        for sha, at in (("a", T8), ("b", T11), ("c", T14)):
            _load(db, sha, at, thrifty=thrifty, twice=("4201",))
    market = _selection(lean, "4201")["market_id"]
    assert lean.scalar("SELECT COUNT(*) FROM quote_sets WHERE market_id=?", (market,)) == 6
    assert (
        lean.scalar(
            """SELECT COUNT(*) FROM quote_confirmations c JOIN quote_sets q
            ON q.id=c.quote_set_id WHERE q.market_id=?""",
            (market,),
        )
        == 0
    )
    assert lean.scalar("SELECT COUNT(*) FROM quote_confirmations") > 0  # the other markets
    for moment in (T8, T11, T14, None):
        as_of = moment + timedelta(minutes=30) if moment else None
        a, b = offer_table(full, day=DAY, as_of=as_of), offer_table(lean, day=DAY, as_of=as_of)
        assert _plain(a) == _plain(b), moment
    for selection in [r["id"] for r in full.rows("SELECT id FROM selections ORDER BY id")]:
        assert history.selection_history(full, selection) == history.selection_history(
            lean, selection
        )


def test_every_read_is_identical_to_the_full_write():
    full, lean, ids = _pair()
    # offer_table as of every document and now (prices, documents, presence, ids)
    for moment in (T8, T11, T14, T16, T1945, None):
        as_of = moment + timedelta(minutes=30) if moment else None
        a, b = offer_table(full, day=DAY, as_of=as_of), offer_table(lean, day=DAY, as_of=as_of)
        assert a and _plain(a) == _plain(b), moment
    # freshness: the 180-minute limit runs from the LAST document that confirmed the price
    now = T16 + timedelta(minutes=100)
    limit = timedelta(minutes=180)
    for a, b in zip(
        offer_table(full, day=DAY, as_of=now), offer_table(lean, day=DAY, as_of=now), strict=True
    ):
        assert odds_freshness(a["valid_at"], now, limit) == odds_freshness(
            b["valid_at"], now, limit
        )
    confirmed = next(r for r in offer_table(lean, day=DAY, as_of=now) if r["offer_code"] == "4201")
    assert confirmed["import_id"] == ids[3]  # the document of 16:00 …
    assert odds_freshness(confirmed["valid_at"], now, limit)[0] == "fresh"
    assert (
        lean.scalar(  # … although its odds were written by the first document
            "SELECT COUNT(*) FROM quote_sets WHERE offer_code='4201' AND import_id=?", (ids[3],)
        )
        == 0
    )
    # the price history of every selection, the quote history of every market, CLV
    selections = [r["id"] for r in full.rows("SELECT id FROM selections ORDER BY id")]
    assert selections == [r["id"] for r in lean.rows("SELECT id FROM selections ORDER BY id")]
    for selection in selections:
        assert history.selection_history(full, selection) == history.selection_history(
            lean, selection
        )
    for market in [r["id"] for r in full.rows("SELECT id FROM markets")]:
        a = [{k: v for k, v in q.items() if k != "odds_id"} for q in quote_history(full, market)]
        b = [{k: v for k, v in q.items() if k != "odds_id"} for q in quote_history(lean, market)]
        assert a == b  # (a market whose rows await a manual review has no "ok" history)
    assert quote_history(lean, home_market := _selection(lean, "4101")["market_id"])
    assert len({q["import_id"] for q in quote_history(lean, home_market)}) == len(SEQUENCE)
    home = _selection(lean, "4101")
    start = datetime(2026, 10, 31, 20, tzinfo=UTC)
    clv = [
        history.clv(Decimal("2.10"), history.selection_history(db, home["selection_id"]), start)
        for db in (full, lean)
    ]
    assert clv[0] == clv[1] and clv[1]["status"] == "dostępne"
    assert clv[1]["closing_price"] == Decimal("2.30")  # confirmed by the 19:45 document
    assert history.horizon_snapshots(
        history.selection_history(full, home["selection_id"]), start
    ) == history.horizon_snapshots(history.selection_history(lean, home["selection_id"]), start)
    assert history.offer_changes(full, ids[0], ids[2]) == history.offer_changes(
        lean, ids[0], ids[2]
    )
    assert history.missing_report(full, day=DAY) == history.missing_report(lean, day=DAY)


def test_the_market_benchmark_and_the_odds_age_are_identical():
    full, lean, _ids = _pair()
    events = {r["id"] for r in full.rows("SELECT id FROM events")}
    quotes = [pe.pre_match_quotes(db, events) for db in (full, lean)]
    assert quotes[0] == quotes[1] and quotes[0]  # the same quote set ids and document times
    event = _selection(lean, "4101")["event_id"]
    forecast = {
        "prediction_id": "p-1",
        "version": "v-1",
        "role": "active",
        "family": "elo",
        "match": event,
        "competition": "es.1",
        "scope": "core",
        "league": "HISZPANIA - LALIGA",
        "market_key": "1x2",
        "line": None,
        "market": "1x2",
        "probabilities": {"home": 0.45, "draw": 0.27, "away": 0.28},
        "outcomes": {"home": 1, "draw": 0, "away": 0},
        "predicted_at": iso(T8),
        "start_at": "2026-10-31T20:00:00+00:00",
    }
    compared = [pe.market_comparison(db, rows=[forecast]) for db in (full, lean)]
    assert compared[0] == compared[1]  # also the data snapshot built on the quote set ids
    row = next(
        r
        for r in compared[1]
        if r["scope"] == "core" and r["market"] == "1x2" and r["family"] == "elo"
    )
    assert row["matches"] == 1 and row["forecasts"] == 1
    now = datetime(2026, 10, 31, 21, tzinfo=UTC)
    assert pe.today_odds_age(full, now=now) == pe.today_odds_age(lean, now=now)
    assert pe.odds_age_report(full, now=now) == pe.odds_age_report(lean, now=now)
    assert pe.today_odds_age(lean, now=now)["document_at"] == iso(T1945 - timedelta(minutes=5))


def test_reassessment_gives_the_same_result(app_paths):
    full, lean, _ids = _pair(upto=1)
    saved = []
    for db in (full, lean):
        legs = [_selection(db, "4101")["odds_id"], _selection(db, "4202")["odds_id"]]
        saved.append(t10._save(db, app_paths, legs, T8 + timedelta(minutes=5)))
        for (sha, at, changes), thrifty in zip(SEQUENCE[1:4], [db is lean] * 3, strict=True):
            _load(db, sha, at, thrifty=thrifty, **changes)
    now = T16 + timedelta(minutes=10)
    found = [reassessment.reassess_all(db, now=now) for db in (full, lean)]
    assert found[0] == found[1] and found[1]["nieaktualny"] == 1
    latest = [
        reassessment.latest(db, s["coupon_id"]) for db, s in zip((full, lean), saved, strict=True)
    ]
    assert latest[0]["reasons"] == latest[1]["reasons"]
    assert any("kurs zmienił się z 2.10 na 2.30" in r for r in latest[1]["reasons"])
    assert any("selekcji nie ma już w aktualnej ofercie STS" in r for r in latest[1]["reasons"])


def test_a_coupon_saved_from_a_confirmed_quote_keeps_price_and_identity(app_paths):
    full, lean, _ids = _pair(upto=1)
    first = {}
    for name, db in (("full", full), ("lean", lean)):
        legs = [_selection(db, "4101")["odds_id"], _selection(db, "4201")["odds_id"]]
        first[name] = t10._save(db, app_paths, legs, T8 + timedelta(minutes=5))
        _load(db, "b", T11, thrifty=db is lean)  # the same offer in a new document
    assert first["full"]["relation"] == first["lean"]["relation"] == "official"
    again = {}
    for name, db in (("full", full), ("lean", lean)):
        legs = [_selection(db, "4101")["odds_id"], _selection(db, "4201")["odds_id"]]
        again[name] = t10._save(db, app_paths, legs, T11 + timedelta(minutes=5))
    # the new PDF never makes a second official coupon — in either store
    assert again["full"]["relation"] == again["lean"]["relation"] == "duplicate"
    assert again["lean"]["coupon_id"] == first["lean"]["coupon_id"]
    assert lean.scalar("SELECT COUNT(*) FROM coupons") == full.scalar(
        "SELECT COUNT(*) FROM coupons"
    )
    identity = [
        db.rows("SELECT identity_key, slot_key FROM coupon_lineage ORDER BY recorded_at")
        for db in (full, lean)
    ]
    assert identity[0] == identity[1]  # the identity is built on selections, not on odds ids
    # a coupon created after the confirmation: the price of the confirmed quote, a real leg
    home = _selection(lean, "4101")
    assert home["price"] == Decimal("2.10")
    legs = lean.rows(
        """SELECT l.price, l.odds_id, l.selection_id FROM coupon_legs l
        WHERE l.coupon_id=? ORDER BY l.price""",
        (first["lean"]["coupon_id"],),
    )
    assert [leg["price"] for leg in legs] == ["2.10", "2.40"]
    assert home["odds_id"] in {leg["odds_id"] for leg in legs}
    assert coupon_store.coupon_identity(
        source="kreator",
        profile="standard",
        day=DAY,
        policy_version="p",
        selection_ids=[leg["selection_id"] for leg in legs],
    ) == coupon_store.coupon_identity(
        source="kreator",
        profile="standard",
        day=DAY,
        policy_version="p",
        selection_ids=[
            _selection(full, "4101")["selection_id"],
            _selection(full, "4201")["selection_id"],
        ],
    )


def test_a_new_analysis_stores_only_the_selections_it_could_judge(store, app_paths):
    from sports_edge.analysis.coupon import AkoAnalysis, CouponReport, save_analysis

    def candidate(key: str, p_model: float | None):
        return SimpleNamespace(
            key=key,
            event="A - B",
            start_at="2026-10-31T20:00:00+00:00",
            offer_code="1",
            market_key="1x2",
            period="FT",
            line=None,
            selection="home",
            odds=Decimal("2.00"),
            odds_id=f"odds-{key}",
            p_model=p_model,
            p_market=None,
            ev_net=None,
            ev_net_low=None,
            freshness="fresh",
            odds_age_hours=1.0,
            document_at=None,
            event_status="before_start",
            break_even=0.5,
            calibration="",
            market_label="Mecz",
            label_raw="Mecz",
            tier="rejected",
            model_version_id="v-1" if p_model is not None else None,
            data_status="ok",
            model_status="ok",
            rules_status="ok",
            tax_status="ok",
            presence="current",
            reasons=(),
        )

    named = candidate("named", None)
    every = [candidate("model", 0.6), named, *[candidate(f"offer-{n}", None) for n in range(50)]]
    report = CouponReport(
        day=DAY,
        created_at=T8,
        policy_version="test",
        headline="test",
        solo=(),
        analysed=len(every),
        rejection_reasons={},
        ako=AkoAnalysis(False, "test", ()),
        probable=(named,),
    )
    analysis_id = save_analysis(store, report, every, services.policy(app_paths))
    row = store.get("analyses", analysis_id)
    assert {i["key"] for i in json.loads(row["items_json"])} == {"model", "named"}
    summary = json.loads(row["summary_json"])
    assert summary["analysed"] == 52 and summary["items_total"] == 52
    assert summary["items_stored"] == 2 and summary["items_scope"] == "with_forecast_or_named"


# 2 ---------------------------------------------------------------- WAL checkpoint (§2.3)


def test_the_checkpoint_truncates_the_log_and_never_fails_on_a_busy_database(tmp_path):
    path = tmp_path / "wal.db"
    store = Store(path)
    ensure_reference_data(store)
    _load(store, "a", T8, thrifty=True)
    wal = Path(str(path) + "-wal")
    assert wal.stat().st_size > 0
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM odds").fetchone()  # a reader holds its snapshot
    _load(store, "b", T11, thrifty=True)
    busy = maintenance.checkpoint(store)  # must return at once — never raise, never wait 15 s
    assert busy["status"] in ("ok", "skipped") and "wal_before" in busy
    assert store.scalar("PRAGMA busy_timeout") == maintenance.BUSY_TIMEOUT_MS
    reader.rollback()
    reader.close()
    done = maintenance.checkpoint(store)
    assert done["status"] == "ok" and done["wal_after"] == 0
    assert store.scalar("SELECT COUNT(*) FROM imports") == 2  # nothing lost
    with store.transaction():
        assert maintenance.checkpoint(store)["status"] == "skipped"  # a write in progress
    store.close()


def test_the_size_report_never_guesses_the_growth(app_paths):
    store = services.open_store(app_paths)
    _load(store, "a", T8, thrifty=True)
    now = datetime(2026, 11, 11, 12, tzinfo=UTC)
    report = maintenance.size_report(store, app_paths, now=now)
    assert report["logical_bytes"] > 0 and report["free_bytes"] > 0
    assert report["growth"] is None  # no earlier size is known
    lines = maintenance.size_lines(report)
    assert lines["growth"] == "brak danych" and "MB" in lines["size"]
    app_paths.backups.mkdir(parents=True, exist_ok=True)
    old = app_paths.backups / "pre-migration-v11_2026-11-03_10-00-00.db"
    old.write_bytes(b"x" * 4096)
    report = maintenance.size_report(store, app_paths, now=now)
    assert report["growth"]["bytes"] == report["logical_bytes"] - 4096
    assert 8.0 < report["growth"]["days"] < 8.2 and report["growth"]["exact_week"]
    lines = maintenance.size_lines(report)
    assert lines["growth"].startswith("+") and "od 03.11" in lines["growth_note"]
    store.close()


# 3 ---------------------------------------------------------------- cyclic backup (§3)


def _run_backup(store, app_paths, now):
    settings = automation.load_settings(store)
    return automation.run_job(
        store, app_paths, "backup", now=now, settings=settings, trigger="test"
    )


def test_the_backup_job_makes_a_checked_copy(app_paths):
    store = services.open_store(app_paths)
    _load(store, "a", T8, thrifty=True)
    settings = automation.load_settings(store)
    job = settings["jobs"]["backup"]
    assert job["enabled"] and job["mode"] == "daily" and job["times"] == ["03:30"]  # at night
    assert "backup" in automation.JOBS and "backup" in automation.JOB_LABELS
    night = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)
    assert automation.next_run(store, "backup", settings, now=night) <= night  # never run yet
    outcome = _run_backup(store, app_paths, night)
    assert outcome.status == "ok", outcome.reason
    copies = backup.cyclic_backups(app_paths)
    assert len(copies) == 1 and copies[0]["ok"] and copies[0]["folder"].startswith("auto-")
    assert outcome.payload["integrity"] == "ok" and outcome.payload["copy_bytes"] > 0
    assert outcome.payload["logical_bytes"] > 0  # the size history of Źródła i ustawienia
    copy = sqlite3.connect(copies[0]["path"] / "sports_edge.db")
    assert copy.execute("SELECT COUNT(*) FROM odds").fetchone()[0] == store.scalar(
        "SELECT COUNT(*) FROM odds"
    )
    copy.close()
    run = store.rows("SELECT status, trigger FROM job_runs WHERE job='auto:backup'")
    assert run == [{"status": "ok", "trigger": "test"}]  # in the job history like any job
    following = automation.next_run(store, "backup", settings, now=night + timedelta(minutes=5))
    assert following.astimezone(automation.WARSAW).strftime("%H:%M") == "03:30"
    assert following > night
    assert store.scalar("SELECT COUNT(*) FROM prematch_alerts") == 0
    store.close()


def _cyclic(app_paths, day: datetime, *, ok: bool = True) -> Path:
    folder = app_paths.backups / f"{backup.CYCLIC_PREFIX}{day.strftime(backup.STAMP)}"
    folder.mkdir(parents=True)
    (folder / "sports_edge.db").write_bytes(b"copy")
    manifest = {
        "created_at": day.isoformat(),
        "cyclic": True,
        "integrity": "ok" if ok else "*** in database main ***",
        "files": {"sports_edge.db": {"bytes": 4, "sha256": "0"}},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def test_retention_removes_only_old_cyclic_backups(app_paths):
    newest = datetime(2026, 11, 30, 3, 30)
    for age in range(40):  # one cyclic backup a day for 40 days
        _cyclic(app_paths, newest - timedelta(days=age))
    _cyclic(app_paths, newest - timedelta(days=2, hours=5))  # a second copy of one day
    migration = app_paths.backups / "pre-migration-v11_2026-10-01_10-00-00.db"
    migration.write_bytes(b"migration")
    manual = app_paths.backups / "2026-10-02_09-00-00"
    manual.mkdir()
    (manual / "manifest.json").write_text('{"created_at": "x", "files": {}}', encoding="utf-8")
    (manual / "sports_edge.db").write_bytes(b"manual")
    foreign = app_paths.backups / "auto-notes"  # not a cyclic backup: no manifest
    foreign.mkdir()
    config = backup.backup_config(app_paths)
    assert (config["keep_daily"], config["keep_weekly"]) == (7, 4)  # config/app.toml [backup]
    found = backup.apply_retention(
        app_paths, keep_daily=config["keep_daily"], keep_weekly=config["keep_weekly"]
    )
    left = backup.cyclic_backups(app_paths)
    days = sorted({b["created"].date() for b in left}, reverse=True)
    assert days[:7] == [(newest - timedelta(days=n)).date() for n in range(7)]  # 7 daily
    weeks = {tuple(b["created"].isocalendar()[:2]) for b in left}
    # 30.11 is a Monday: the 7 days cover its week and the week before; the 4 weeks add the
    # newest copy of the two weeks before those (22.11 and 15.11)
    assert len(weeks) == 4 and [d.isoformat() for d in days[7:]] == ["2026-11-22", "2026-11-15"]
    assert len(left) == 9 and len(found["removed"]) == 41 - 9
    assert set(found["kept"]) == {b["folder"] for b in left}
    assert sorted(found["kept"].values()).count("tygodniowa") == 2
    assert all(name.startswith("auto-") for name in found["removed"])
    assert migration.read_bytes() == b"migration"  # never a pre-migration copy
    assert (manual / "sports_edge.db").read_bytes() == b"manual" and foreign.is_dir()
    again = backup.apply_retention(app_paths, keep_daily=7, keep_weekly=4)
    assert again["removed"] == []  # stable


def test_retention_keeps_a_failed_copy_only_while_it_is_the_newest(app_paths):
    day = datetime(2026, 11, 10, 3, 30)
    good = _cyclic(app_paths, day)
    broken = _cyclic(app_paths, day + timedelta(days=1), ok=False)
    plan = backup.apply_retention(app_paths, keep_daily=7, keep_weekly=4)
    assert plan["removed"] == [] and good.is_dir() and broken.is_dir()
    _cyclic(app_paths, day + timedelta(days=2))
    plan = backup.apply_retention(app_paths, keep_daily=7, keep_weekly=4)
    assert plan["removed"] == [broken.name] and good.is_dir()


def test_no_free_space_skips_the_backup_with_a_reason_and_an_alert(app_paths, monkeypatch):
    store = services.open_store(app_paths)
    _load(store, "a", T8, thrifty=True)
    monkeypatch.setattr(backup, "free_space", lambda paths: 1024)
    now = datetime(2026, 11, 11, 3, 30, tzinfo=UTC)
    outcome = _run_backup(store, app_paths, now)
    assert outcome.status == "skipped" and "za mało wolnego miejsca" in outcome.reason
    assert "2× rozmiar bazy" in outcome.reason
    assert backup.cyclic_backups(app_paths) == []  # nothing was written
    found = store.rows("SELECT kind, priority, message FROM prematch_alerts")
    assert [a["kind"] for a in found] == ["backup_skipped"] and found[0]["priority"] == "high"
    assert "za mało wolnego miejsca" in found[0]["message"]
    _run_backup(store, app_paths, now + timedelta(hours=1))
    assert store.scalar("SELECT COUNT(*) FROM prematch_alerts") == 1  # once a day
    assert alerts.overview(store, now=now)["important"] == 1
    store.close()


def test_a_copy_that_fails_its_check_gives_an_alert_and_removes_nothing(app_paths, monkeypatch):
    store = services.open_store(app_paths)
    _load(store, "a", T8, thrifty=True)
    earlier = _cyclic(app_paths, datetime(2026, 11, 1, 3, 30))
    garbage = app_paths.root / "garbage.db"
    garbage.write_bytes(b"this is not a database" * 400)
    assert backup.verify(garbage) != "ok"  # a real check of a broken file
    monkeypatch.setattr(
        backup, "verify", lambda database, check="quick": "row 7 missing from index"
    )
    outcome = _run_backup(store, app_paths, datetime(2026, 11, 11, 3, 30, tzinfo=UTC))
    assert outcome.status == "failed" and "nie przeszła kontroli integralności" in outcome.reason
    found = store.rows("SELECT kind, message FROM prematch_alerts")
    assert [a["kind"] for a in found] == ["backup_failed"]
    assert "row 7 missing from index" in found[0]["message"]
    copies = backup.cyclic_backups(app_paths)
    assert [c["ok"] for c in copies] == [False, True] and earlier.is_dir()  # retention not run
    store.close()


# 4 ---------------------------------------------------------------- alerts (§4)


def _change(kind: str, detail: str, team: str = "Real Madrid") -> dict:
    return {"kind": kind, "team": team, "detail": detail}


CHANGES = [
    _change("goalkeeper_change", "bramkarz: A → B"),
    _change("new_starter", "nowy w wyjściowym składzie: C"),
    _change("moved_to_bench", "D: przesunięty na ławkę"),
    _change("removed_starter", "E: nie ma go w składzie"),
    _change("formation_change", "formacja 4-4-2 → 4-3-3"),
    _change("confirmed_published", "opublikowano potwierdzony skład"),
]


def _result(changes: list[dict], *, status: str = "ok", absence: str = "no_source"):
    return SimpleNamespace(changes=changes, status=status, absence_status=absence, detail="")


def _event(store, code: str = "4101") -> dict:
    return store.get("events", _selection(store, code)["event_id"])


def test_lineup_changes_of_one_check_are_one_alert_with_the_list(store):
    _load(store, "a", T8, thrifty=True)
    event = _event(store)
    added = alerts.from_check(store, {**event, "priority": 1}, _result(CHANGES), now=T8)
    assert added == 2  # the five lineup changes together + the published confirmed lineup
    rows = {r["kind"]: r for r in store.rows("SELECT * FROM prematch_alerts")}
    assert set(rows) == {"lineup_changes", "confirmed_published"}
    grouped = rows["lineup_changes"]
    assert grouped["priority"] == "high"  # the goalkeeper change decides
    assert "zmiany w składzie (5)" in grouped["message"] and "bramkarz: A → B" in grouped["message"]
    assert [c["kind"] for c in json.loads(grouped["payload_json"])["changes"]] == [
        c["kind"] for c in CHANGES[:5]
    ]
    assert alerts.from_check(store, event, _result(CHANGES), now=T11) == 0  # the same facts
    assert "no_absence_source" not in rows  # never one alert per match any more


def _legacy(store, event: dict, at: datetime, mark: str = "") -> None:
    """Alerts the way they were stored before TASK 016: one per lineup change (``mark``
    makes the changes of another check of the same match different facts)."""
    for change in CHANGES[:5]:
        change = {**change, "detail": change["detail"] + mark}
        alerts.create(
            store,
            kind=change["kind"],
            message=f"{alerts.event_label(event)}: {change['detail']} ({change['team']})",
            source="PitchAPI",
            now=at,
            event_id=event["id"],
            fingerprint=f"{change['team']}|{change['detail']}",
            payload=change,
        )


def test_old_alerts_are_grouped_when_shown_and_ranked(store, app_paths):
    _load(store, "a", T8, thrifty=True)
    on_coupon, other = _event(store, "4101"), _event(store, "4201")
    t10._save(store, app_paths, [_selection(store, "4101")["odds_id"]], T8)
    _legacy(store, other, T8 + timedelta(minutes=1))  # a match that is on no coupon
    _legacy(store, on_coupon, T8 + timedelta(minutes=2))
    _legacy(store, on_coupon, T8 + timedelta(minutes=40), " (2)")  # another check
    alerts.create(
        store, kind="calibration_flag", message="flaga", source="x", now=T8 + timedelta(minutes=3)
    )
    alerts.create(
        store,
        kind="coupon_attention",
        message="kurs zmienił się",
        source="Sports Edge AI",
        now=T8 + timedelta(minutes=4),
        event_id=on_coupon["id"],
    )
    before = store.rows("SELECT * FROM prematch_alerts ORDER BY id")
    assert len(before) == 17
    found = alerts.overview(store, now=T11)
    assert found["raw_unread"] == 17 and found["unread"] == 5  # 15 lineup alerts → 3 items
    assert [(i["rank"], i["kind"]) for i in found["items"]] == [
        (0, "coupon_attention"),  # 1. coupons
        (1, "lineup_changes"),  # 2. lineups of a match that is on a coupon
        (1, "lineup_changes"),
        (2, "lineup_changes"),  # 3. the rest
        (2, "calibration_flag"),
    ]
    assert found["important"] == 3 and alerts.counts_text(found) == "ważne 3 · wszystkie 5"
    group = found["items"][1]
    assert len(group["ids"]) == 5 and group["priority"] == "high" and len(group["changes"]) == 5
    assert group["message"].startswith("Real Madrid - Barcelona: zmiany w składzie (5): ")
    assert store.rows("SELECT * FROM prematch_alerts ORDER BY id") == before  # nothing changed


def test_an_alert_of_a_started_match_is_collapsed_not_deleted(store, app_paths):
    _load(store, "a", T8, thrifty=True)
    event = _event(store, "4101")  # starts 20:00 UTC
    t10._save(store, app_paths, [_selection(store, "4101")["odds_id"]], T8)
    alerts.from_check(store, event, _result(CHANGES[:2]), now=T8)
    before = alerts.overview(store, now=datetime(2026, 10, 31, 19, 59, tzinfo=UTC))
    assert [i["stale"] for i in before["items"]] == [False] and before["important"] == 1
    after = alerts.overview(store, now=datetime(2026, 10, 31, 20, 0, tzinfo=UTC))
    assert [i["stale"] for i in after["items"]] == [True]
    assert after["important"] == 0 and after["unread"] == 1 and after["stale_unread"] == 1
    assert store.scalar("SELECT COUNT(*) FROM prematch_alerts") == 1
    # an older coupon alert stored without its match: the coupon's first match decides
    coupon = store.rows("SELECT id FROM coupons")[0]["id"]
    alerts.create(store, kind="coupon_attention", message="x", source="x", now=T8, coupon_id=coupon)
    late = alerts.overview(store, now=datetime(2026, 10, 31, 20, 0, tzinfo=UTC))
    assert all(i["stale"] for i in late["items"]) and late["important"] == 0
    early = alerts.overview(store, now=T11)
    assert early["important"] == 2 and not any(i["stale"] for i in early["items"])


def test_mark_all_read_appends_reads_for_every_alert(store):
    _load(store, "a", T8, thrifty=True)
    event = _event(store)
    for n in range(130):  # more than any page of the interface shows
        alerts.create(store, kind="odds_changed", message=f"alert {n}", source="x", now=T8)
    _legacy(store, event, T8)
    rows = store.rows("SELECT * FROM prematch_alerts ORDER BY id")
    assert alerts.unread_count(store) == 135
    assert alerts.mark_all_read(store, now=T11) == 135
    assert store.scalar("SELECT COUNT(*) FROM alert_reads") == 135
    assert alerts.unread_count(store) == 0 and alerts.overview(store, now=T11)["unread"] == 0
    assert alerts.counts_text(alerts.overview(store, now=T11)) == "brak nowych"
    assert alerts.mark_all_read(store, now=T14) == 0  # nothing twice
    assert store.rows("SELECT * FROM prematch_alerts ORDER BY id") == rows  # alerts untouched
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        store.connection.execute("DELETE FROM alert_reads")


def test_the_missing_absence_source_is_one_notice_a_day(store, app_paths):
    _load(store, "a", T8, thrifty=True)
    legs = [_selection(store, "4101")["odds_id"], _selection(store, "4201")["odds_id"]]
    t10._save(store, app_paths, legs, T8)

    def run(now):
        return services.run_prematch(
            store,
            app_paths,
            AppSettings(),
            now=now,
            fetch=lambda event: (None, None, 0),
            force=True,
            only_coupons=True,
        )

    first = run(T8 + timedelta(minutes=10))
    assert first["events"] == 2 and first["alerts"] == 1  # two matches, one notice
    assert run(T11)["alerts"] == 0 and run(T14)["alerts"] == 0  # the same day: nothing more
    notices = store.rows("SELECT * FROM prematch_alerts WHERE kind='no_absence_source'")
    assert len(notices) == 1 and notices[0]["event_id"] is None
    assert len(json.loads(notices[0]["payload_json"])["event_ids"]) == 2
    assert "Analizie meczu" in notices[0]["message"]
    events = [_event(store, "4101"), _event(store, "4201")]
    next_day = datetime(2026, 11, 1, 9, tzinfo=UTC)
    assert alerts.absence_notice(store, events, now=next_day) == 1  # the next day: one more
    assert alerts.absence_notice(store, events, now=next_day + timedelta(hours=5)) == 0
    assert alerts.absence_notice(store, [], now=next_day + timedelta(days=1)) == 0


# 5 ---------------------------------------------------------------- manual absences (§5.3)


def _absence(store, event, **changes) -> str:
    values = {
        "team": event["home"],
        "player": "Jan Testowy",
        "reason": "kontuzja",
        "source": "komunikat klubu (test)",
        "source_url": "https://example.test/komunikat",
        "information_date": "2026-10-30",
        "now": T8,
    }
    return prematch.add_user_absence(store, event, **{**values, **changes})


def test_a_manual_absence_needs_a_source(store):
    _load(store, "a", T8, thrifty=True)
    event = _event(store)
    for source in ("", "   ", None):
        with pytest.raises(ValueError, match="źródło"):
            _absence(store, event, source=source)
    with pytest.raises(ValueError, match="zawodnika"):
        _absence(store, event, player=" ")
    with pytest.raises(ValueError, match="drużynę"):
        _absence(store, event, team="Inna Drużyna")
    with pytest.raises(ValueError, match="powód"):
        _absence(store, event, reason="return")
    assert store.scalar("SELECT COUNT(*) FROM player_absence_observations") == 0
    absence_id = _absence(store, event)
    row = store.get("player_absence_observations", absence_id)
    assert row["entered_by"] == "user" and row["match_status"] == "verified"
    assert row["source"] == "komunikat klubu (test)" and row["reason"] == "injury_reported"
    assert row["source_url"] == "https://example.test/komunikat"
    assert row["published_at"] == "2026-10-30" and row["observed_at"] == iso(T8)
    assert row["known_before_start"] == 1 and row["quality"] == prematch.USER_QUALITY
    assert prematch.verification_label(row) == "zweryfikowana przez użytkownika"
    assert _absence(store, event, reason="zawieszenie", player="Inny Gracz", now=T11)
    reasons = {r["reason"] for r in store.rows("SELECT reason FROM player_absence_observations")}
    assert reasons == {"injury_reported", "suspension"}


def test_withdrawing_an_absence_is_a_new_row(store):
    _load(store, "a", T8, thrifty=True)
    event = _event(store)
    absence_id = _absence(store, event)
    assert [a["id"] for a in prematch.active_absences(store, event["id"])] == [absence_id]
    withdrawal = prematch.withdraw_absence(store, absence_id, now=T11)
    rows = prematch.absences(store, event["id"])  # the full history stays
    assert {r["id"] for r in rows} == {absence_id, withdrawal} and len(rows) == 2
    newest = store.get("player_absence_observations", withdrawal)
    assert newest["quality"] == prematch.WITHDRAWN and newest["previous_id"] == absence_id
    assert store.get("player_absence_observations", absence_id)["quality"] == "user"  # untouched
    assert prematch.active_absences(store, event["id"]) == []
    assert prematch.summary(store, event["id"])["absences"] == []
    with pytest.raises(ValueError, match="wycofaniem"):
        prematch.withdraw_absence(store, withdrawal, now=T14)
    again = _absence(store, event, now=T14)  # the same player can be entered again
    assert [a["id"] for a in prematch.active_absences(store, event["id"])] == [again]
    for sql in (
        "UPDATE player_absence_observations SET source='x'",
        "DELETE FROM player_absence_observations",
    ):
        with pytest.raises(sqlite3.DatabaseError, match="append_only"):
            store.connection.execute(sql)


def test_a_manual_absence_is_a_warning_in_the_check_and_the_coupon_not_a_model_input(
    store, app_paths
):
    _load(store, "a", T8, thrifty=True)
    event = _event(store)
    saved = t10._save(store, app_paths, [_selection(store, "4101")["odds_id"]], T8)
    tables = ("predictions", "feature_snapshots", "model_versions", "model_events", "odds")
    before = {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in tables}
    offer = _plain(offer_table(store, day=DAY))
    quiet = reassessment.reassess_all(store, now=T8 + timedelta(minutes=30))
    assert quiet["bez zmian"] == 1
    _absence(store, event, now=T8 + timedelta(minutes=40))
    info = prematch.summary(store, event["id"])
    assert [a["player_name"] for a in info["absences"]] == ["Jan Testowy"]
    assert info["model_uses_lineups"] is False
    found = reassessment.reassess_all(store, now=T11)
    assert found["wymaga uwagi"] == 1
    latest = reassessment.latest(store, saved["coupon_id"])
    assert any(
        "absencja Jan Testowy" in r and "wpis użytkownika, źródło: komunikat klubu (test)" in r
        for r in latest["reasons"]
    )
    assert "komunikat klubu (test)" in latest["sources"]
    assert not any("brak zweryfikowanych informacji o absencjach" in r for r in latest["risks"])
    # no forecast, model or price changed
    assert {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in tables} == before
    assert _plain(offer_table(store, day=DAY)) == offer
    # the notice "no absence source" skips a match that has a manual entry
    run = services.run_prematch(
        store,
        app_paths,
        AppSettings(),
        now=T11,
        fetch=lambda e: (None, None, 0),
        force=True,
        only_coupons=True,
    )
    assert run["events"] == 1 and run["alerts"] == 0
    # absences are never read by the code that makes forecasts
    for name in ("probability_lab.py", "sportsdata.py", "rolling.py", "analysis/edge_engine.py"):
        text = (SRC / name).read_text(encoding="utf-8")
        assert "absence" not in text and "absencj" not in text, name


# 6 ---------------------------------------------------------------- decisions (§7)


def test_saving_a_decision_that_changes_nothing_adds_no_row(store, app_paths):
    settings = load_settings(app_paths)
    now = datetime(2026, 11, 11, 10, tzinfo=UTC)
    # the first confirmation of a default closes the open decision: one row
    first = decisions.decide_safe_solo(store, app_paths, settings, enabled=False, now=now)
    assert first and len(decisions.history(store)) == 1
    for minute in (1, 2):
        again = decisions.decide_safe_solo(
            store,
            app_paths,
            load_settings(app_paths),
            enabled=False,
            now=now + timedelta(minutes=minute),
        )
        assert again is None
    assert len(decisions.history(store)) == 1  # no "bez zmian" rows
    assert decisions.decide_limit(store, app_paths, load_settings(app_paths), value=1, now=now)
    assert (
        decisions.decide_limit(
            store, app_paths, load_settings(app_paths), value=1, now=now + timedelta(minutes=3)
        )
        is None
    )
    assert decisions.decide_option_d(
        store, app_paths, load_settings(app_paths), value="sum", now=now
    )
    assert (
        decisions.decide_option_d(
            store, app_paths, load_settings(app_paths), value="sum", now=now + timedelta(minutes=4)
        )
        is None
    )
    assert decisions.decide_gate(store, app_paths, accept=True, now=now + timedelta(minutes=5))
    events = store.scalar("SELECT COUNT(*) FROM model_events")
    assert (
        decisions.decide_gate(store, app_paths, accept=True, now=now + timedelta(minutes=6)) is None
    )
    assert store.scalar("SELECT COUNT(*) FROM model_events") == events  # no second acceptance
    assert decisions.decide_gate(store, app_paths, accept=False, now=now + timedelta(minutes=7))
    assert (
        decisions.decide_gate(store, app_paths, accept=False, now=now + timedelta(minutes=8))
        is None
    )
    rows = decisions.history(store)
    assert len(rows) == 5 and not any("bez zmian" in r["summary"] for r in rows)
    assert rows[0]["summary"].endswith("(potwierdzenie dotychczasowego stanu)")
    assert decisions.UNCHANGED == "bez zmian — nic nie zapisano"
    # a real change is still a new row
    assert decisions.decide_safe_solo(
        store, app_paths, load_settings(app_paths), enabled=True, now=now + timedelta(minutes=9)
    )
    assert len(decisions.history(store)) == 6


# 7 ---------------------------------------------------------------- migration v11 → v12


def test_migration_to_v12_backs_up_and_keeps_every_row(tmp_path):
    path = tmp_path / "old.db"
    store = Store(path)
    ensure_reference_data(store)
    _load(store, "a", T8, thrifty=False)
    _load(store, "b", T11, thrifty=False)  # a v11 database: every document copied its odds
    store.close()
    raw = sqlite3.connect(path)
    raw.execute("DROP VIEW quote_observations")
    raw.execute("DROP TABLE quote_confirmations")
    raw.execute("PRAGMA user_version=11")
    tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    rows = {t: raw.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    odds = raw.execute("SELECT id, price FROM odds ORDER BY id").fetchall()
    raw.commit()
    raw.close()
    migrated = Store(path, backup_dir=tmp_path / "backups")
    # TASK 019: v13 (alert states) follows; the v12 step is part of the same migration run
    assert SCHEMA_VERSION >= 12 and migrated.version_before == 11
    assert migrated.migration_backup.name.startswith(f"pre-migration-v{SCHEMA_VERSION}_")
    assert migrated.migration_backup.exists()
    for table, n in rows.items():
        if table != "schema_migrations":
            assert migrated.scalar(f"SELECT COUNT(*) FROM {table}") == n, table
    assert "quote_confirmations" in migrated.tables
    assert migrated.scalar("SELECT COUNT(*) FROM quote_confirmations") == 0
    assert migrated.scalar("SELECT COUNT(*) FROM quote_observations") == rows["quote_sets"]
    kept = migrated.connection.execute("SELECT id, price FROM odds ORDER BY id").fetchall()
    assert [tuple(r) for r in kept] == odds  # old repeated odds stay as they are
    note = migrated.rows(
        "SELECT description FROM schema_migrations WHERE version=?", (SCHEMA_VERSION,)
    )
    assert note and "v12 TASK 016" in note[-1]["description"]
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        migrated.connection.execute("DELETE FROM odds")
    # a document imported after the migration confirms the stored quotes
    _load(migrated, "c", T14, thrifty=True)
    assert migrated.scalar("SELECT COUNT(*) FROM odds") == rows["odds"]
    assert migrated.scalar("SELECT COUNT(*) FROM quote_confirmations") > 0
    migrated.close()


# 8 ---------------------------------------------------------------- interface (AppTest)


def _text(app) -> str:
    return (
        " ".join(m.value for m in app.markdown)
        + " ".join(str(x.value) for x in [*app.caption, *app.info, *app.success, *app.error])
        + " ".join(d.value.to_string() for d in app.dataframe)
    )


def test_dashboard_sources_and_match_analysis_show_the_new_parts(app_paths):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    store = services.open_store(app_paths)
    _load(store, "a", T8, thrifty=True)
    event = _event(store)
    now = datetime.now(UTC)
    alerts.create(store, kind="coupon_attention", message="kupon: test", source="x", now=now)
    alerts.create(store, kind="calibration_flag", message="flaga: test", source="x", now=now)
    _run_backup(store, app_paths, now)
    store.close()

    dashboard = AppTest.from_string(
        "from sports_edge.app.page_dashboard import dashboard_page; dashboard_page()",
        default_timeout=90,
    )
    dashboard.session_state["demo_mode"] = False
    dashboard.run()
    assert not dashboard.exception, dashboard.exception
    assert "ważne 1 · wszystkie 2" in _text(dashboard)  # important and all, separately

    sources = AppTest.from_string(
        "from sports_edge.app.page_sources import sources_page; sources_page()",
        default_timeout=90,
    )
    sources.session_state["demo_mode"] = False
    sources.session_state["sources_view"] = "Zbieranie danych i monitoring"
    sources.run()
    assert not sources.exception, sources.exception
    shown = _text(sources)
    for expected in (
        "Rozmiar bazy",
        "Przyrost z 7 dni",
        "Wolne miejsce na dysku",
        "Kopie cykliczne",
    ):
        assert expected in shown, expected
    assert "auto-" in shown and "7 dziennych + 4 tygodniowe" in shown

    panel = AppTest.from_string(
        "from sports_edge.app import common as c\n"
        "from sports_edge.app import prematch_ui\n"
        f"prematch_ui.lineups_panel(c.store(), '{event['id']}', start_at=None, now=c.now(), "
        "started=False)\n",
        default_timeout=90,
    )
    panel.session_state["demo_mode"] = False
    panel.run()
    assert not panel.exception, panel.exception
    labels = {w.label for w in [*panel.text_input, *panel.selectbox, *panel.date_input]}
    assert {"Drużyna", "Zawodnik", "Powód", "Źródło (obowiązkowe)", "Data informacji"} <= labels
    next(w for w in panel.text_input if w.label == "Zawodnik").set_value("Jan Testowy")
    next(b for b in panel.button if b.label == "Zapisz absencję").click().run()
    assert any("źródło" in str(e.value) for e in panel.error)  # no source: nothing stored
    store = services.open_store(app_paths)
    assert store.scalar("SELECT COUNT(*) FROM player_absence_observations") == 0
    store.close()
    next(w for w in panel.text_input if w.label == "Zawodnik").set_value("Jan Testowy")
    next(w for w in panel.text_input if w.label == "Źródło (obowiązkowe)").set_value(
        "komunikat klubu (test)"
    )
    next(b for b in panel.button if b.label == "Zapisz absencję").click().run()
    assert not panel.exception, panel.exception
    store = services.open_store(app_paths)
    rows = store.rows("SELECT player_name, source, entered_by FROM player_absence_observations")
    assert rows == [
        {"player_name": "Jan Testowy", "source": "komunikat klubu (test)", "entered_by": "user"}
    ]
    store.close()
    assert "zweryfikowana przez użytkownika" in _text(panel)


def test_the_alert_panel_marks_everything_read_and_the_decision_panel_says_unchanged(app_paths):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    store = services.open_store(app_paths)
    _load(store, "a", T8, thrifty=True)
    event = _event(store)
    _legacy(store, event, datetime.now(UTC))
    alerts.create(
        store, kind="coupon_attention", message="kupon: test", source="x", now=datetime.now(UTC)
    )
    store.close()
    panel = AppTest.from_string(
        "from sports_edge.app import common as c\n"
        "from sports_edge.app.page_automation import _alerts\n"
        "_alerts(c.store())\n",
        default_timeout=90,
    )
    panel.session_state["demo_mode"] = False
    panel.run()
    assert not panel.exception, panel.exception
    shown = _text(panel)
    # TASK 019 §4.3: the count of what can still be acted on (a started match is archived)
    assert "ważne: 1 · do reakcji: 1" in shown
    assert "6 zapisanych alertów to 2 pozycji" in shown  # grouped when shown
    next(b for b in panel.button if b.label == "Oznacz wszystkie jako przeczytane").click().run()
    assert not panel.exception, panel.exception
    store = services.open_store(app_paths)
    assert store.scalar("SELECT COUNT(*) FROM alert_reads") == 6
    assert store.scalar("SELECT COUNT(*) FROM prematch_alerts") == 6
    store.close()
    assert "Brak ważnych nowych alertów" in _text(panel)

    board = AppTest.from_string(
        "from sports_edge.app.page_models import models_page; models_page()", default_timeout=90
    )
    board.session_state["demo_mode"] = False
    board.query_params["widok"] = "Decyzje do podjęcia"
    board.run()
    assert not board.exception, board.exception
    assert "Nazwy drużyn STS do potwierdzenia" in _text(board)  # §6.2: the way to the names
    for expected in (1, 1):  # the second save changes nothing: no second row
        next(b for b in board.checkbox if b.label == "Potwierdzam limit różnicowania").check()
        board.run()
        next(b for b in board.button if b.label == "Zapisz decyzję o limicie").click().run()
        assert not board.exception, board.exception
        store = services.open_store(app_paths)
        assert len(decisions.history(store, decisions.LIMIT)) == expected
        store.close()
    assert "bez zmian — nic nie zapisano" in _text(board)


def test_the_new_job_and_tables_are_wired():
    assert digest(["x"])  # the helpers of the suite are importable
    assert automation.FREQUENCIES["backup"] and automation.DEFAULTS["jobs"]["backup"]["enabled"]
    assert set(alerts.LINEUP_KINDS) == {
        "new_starter",
        "moved_to_bench",
        "removed_starter",
        "formation_change",
        "goalkeeper_change",
    }
    assert alerts.PRIORITY["backup_failed"] == alerts.PRIORITY["backup_skipped"] == "high"
    assert backup.BACKUP_DEFAULTS["min_free_factor"] == 2.0
