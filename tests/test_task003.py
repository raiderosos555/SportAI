"""TASK 003: STS ONLY migration, odds history and the full pipeline.

The full-flow test uses labelled SYNTHETIC fixtures (offer PDF and match results). It checks
that the pipeline works end to end; it is NOT evidence of predictive skill or of an edge.
"""

import random
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from sports_edge import history
from sports_edge.repository import archived_summary, counts, import_offer, offer_days, offer_table
from sports_edge.storage import SCHEMA_VERSION, Store

pytest.importorskip("pymupdf")

from sports_edge.pdf.engine import parse_pdf  # noqa: E402

NOW = datetime(2026, 10, 31, 11, 30, tzinfo=UTC)


def _legacy_database(path, sts_fixture_pdf):
    """A schema-2 database that still holds Superbet rows from TASK 002."""
    db = Store(path)
    from sports_edge.repository import ensure_reference_data

    ensure_reference_data(db)
    result, document = parse_pdf(sts_fixture_pdf, file_name="fixture.pdf")
    import_offer(
        db,
        result,
        sha256=document.sha256,
        file_name="fixture.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=NOW,
    )
    sql = db.connection.execute
    sql("INSERT INTO bookmakers VALUES ('superbet','Superbet')")
    sql(
        "INSERT INTO sources VALUES ('superbet_pdf_manual','Superbet PDF','pdf_manual','x','x','x')"
    )
    imp = db.rows("SELECT * FROM imports")[0]
    sql(
        "INSERT INTO imports (id, source_id, bookmaker_id, parser, parser_verified, raw_sha256, "
        "data_kind, offer_kind, ingested_at, rows_total, rows_ok, rows_review, unparsed, quality, "
        "summary_json) VALUES ('sb-import','superbet_pdf_manual','superbet','generic',0,'s',"
        "'prospective','unknown',?,1,1,0,0,0.7,?)",
        (imp["ingested_at"], '{"offer_dates": ["2026-10-31"]}'),
    )
    sql(
        "INSERT INTO events (id, bookmaker_id, sport, sport_raw, league, home, away, "
        "participants_json, name_raw, start_at, offer_date, first_import_id) VALUES "
        "('sb-event','superbet','football','','Hiszpania','Real Madryt','FC Barcelona','[]',"
        "'Real Madryt - FC Barcelona','2026-10-31T20:00:00+00:00','2026-10-31','sb-import')"
    )
    db.close()
    # Schema 2 had neither the archive nor the migration log.
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE schema_migrations")
    raw.execute("DROP TABLE archived_entities")
    raw.execute("PRAGMA user_version=2")
    raw.commit()
    raw.close()


def test_sts_only_migration_keeps_and_archives_history(tmp_path, sts_fixture_pdf):
    path = tmp_path / "data" / "sports_edge.db"
    _legacy_database(path, sts_fixture_pdf)
    raw = sqlite3.connect(path)
    before = {
        t: raw.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("imports", "events", "markets", "odds", "bookmakers")
    }
    raw.close()
    db = Store(path, backup_dir=tmp_path / "backups")
    assert db.version_before == 2 and db.scalar("PRAGMA user_version") == SCHEMA_VERSION
    assert db.migration_backup is not None and db.migration_backup.is_file()
    backup = sqlite3.connect(db.migration_backup)
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 2
    backup.close()
    after = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in before}
    assert after == before  # nothing deleted
    archived = {
        (r["entity_type"], r["entity_id"]) for r in db.rows("SELECT * FROM archived_entities")
    }
    assert ("bookmaker", "superbet") in archived and ("source", "superbet_pdf_manual") in archived
    assert archived_summary(db)[0]["events"] == 1
    assert all(r["bookmaker_id"] == "sts" for r in offer_table(db))
    assert counts(db)["events"] == before["events"] - 1  # active view: STS only
    assert offer_days(db) == ["2026-10-31"]
    assert db.rows("SELECT version FROM schema_migrations") == [{"version": SCHEMA_VERSION}]
    db.close()
    again = Store(path, backup_dir=tmp_path / "backups")
    assert again.migration_backup is None  # migrations run once
    again.close()


def _later(sts_fixture_pdf, *, at: datetime, price: str):
    result, _ = parse_pdf(sts_fixture_pdf, file_name="oferta-dzisiaj-www.pdf")
    rows = []
    for row in result.rows:
        if row.offer_code == "4201" and row.market_key == "1x2":
            row = replace(
                row,
                selections=tuple(
                    replace(s, odds=Decimal(price)) if s.key == "home" else s
                    for s in row.selections
                ),
            )
        rows.append(row)
    return replace(result, rows=tuple(rows), document_generated_at=at)


def test_odds_history_changes_and_horizons(store, sts_fixture_pdf, import_sts):
    later = _later(sts_fixture_pdf, at=datetime(2026, 10, 31, 13, 30, tzinfo=UTC), price="2.25")
    second = import_offer(
        store,
        later,
        sha256="f" * 64,
        file_name="later.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=datetime(2026, 10, 31, 13, 31, tzinfo=UTC),
    )
    row = next(
        r
        for r in offer_table(store, day="2026-10-31")
        if r["offer_code"] == "4201" and r["market_key"] == "1x2"
    )
    observations = history.selection_history(store, row["selections"]["home"]["selection_id"])
    assert [o.price for o in observations] == [Decimal("2.40"), Decimal("2.25")]
    move = history.changes(observations)[0]
    assert move["direction"] == "down" and move["pct"] == pytest.approx(2.25 / 2.40 - 1)
    start = datetime.fromisoformat(row["start_at"])  # 19:30 UTC
    snaps = history.horizon_snapshots(observations, start)
    assert snaps["6h"]["price"] == Decimal("2.25")  # observed 13:30 = start - 6h
    assert snaps["1h"] is None and snaps["closing"] is None  # nothing is interpolated
    report = history.missing_report(store, day="2026-10-31")
    # Only the tennis match (14:00 UTC) has a document inside its 30-minute closing window.
    assert report["observed"]["closing"] == 1
    assert report["missing"]["closing"] + report["observed"]["closing"] == report["markets"]
    first_import = store.rows("SELECT id FROM imports ORDER BY ingested_at LIMIT 1")[0]["id"]
    changes = history.offer_changes(store, first_import, second.import_id)
    assert changes["repriced_selections"] == 1 and changes["down"] == 1


def _synthetic_results(end: date, n: int, seed: int = 3):
    rng = random.Random(seed)
    teams = ["Real Madrid CF", "FC Barcelona"] + [f"Club Sintetico {i} CF" for i in range(18)]
    strength = {t: rng.gauss(0, 0.6) for t in teams}
    lines = ["date;home;away;home_score;away_score"]
    for k in range(n):
        home, away = rng.sample(teams, 2)
        lam_h = 1.45 * 2.718 ** (strength[home] - strength[away] + 0.1)
        lam_a = 1.10 * 2.718 ** (strength[away] - strength[home])
        hs = sum(rng.random() < lam_h / 6 for _ in range(6))
        as_ = sum(rng.random() < lam_a / 6 for _ in range(6))
        lines.append(f"{(end - timedelta(days=n - 1 - k)).isoformat()};{home};{away};{hs};{as_}")
    return "\n".join(lines).encode()


def test_full_pipeline_on_synthetic_fixtures(app_paths, sts_fixture_pdf):
    """STS PDF → SQLite → sports data → model → forecast → EV → decision → paper → result →
    settlement → report. Synthetic data: a functional test, not a skill test."""
    from sports_edge import services
    from sports_edge.analysis.coupon import NO_EDGE, save_analysis
    from sports_edge.bookmakers import load_profiles
    from sports_edge.export import export_json
    from sports_edge.paper import (
        closing_line_value,
        create_coupon,
        import_source_results,
        performance,
        settle_coupon,
    )
    from sports_edge.settings import AppSettings
    from sports_edge.sportsdata import Split, import_results_csv, predict_offer, run_elo_experiment

    store = services.open_store(app_paths)
    settings = AppSettings()
    imported = services.import_pdf_bytes(
        store, app_paths, settings, sts_fixture_pdf, file_name="fixture_flow.pdf", now=NOW
    )
    assert imported.summary["rows_ok"] > 100
    csv = _synthetic_results(date(2026, 10, 29), 1800)
    loaded = import_results_csv(
        store,
        csv,
        file_name="synthetic.csv",
        source_name="FIXTURE",
        license_note="dane syntetyczne (test)",
        sport="football",
        competition="es.1",
        now=NOW,
    )
    assert loaded["inserted"] == 1800
    report = run_elo_experiment(
        store,
        competitions=("es.1",),
        split=Split("2024-09-01", "2025-09-01"),
        now=NOW,
        policy=services.policy(app_paths),
    )
    assert report["status"] == "experimental" and report["market_benchmark"] is None
    events = store.rows("SELECT * FROM events WHERE start_at > ?", (NOW.isoformat(),))
    forecasts = predict_offer(
        store,
        version_id=report["version_id"],
        events=events,
        normalizer=services.normalizer(app_paths),
        now=NOW,
        team_aliases=services.team_aliases(app_paths),
    )
    assert forecasts["saved"] == 1  # Real Madrid - Barcelona (LaLiga); others not covered
    coupon_report, candidates = services.analyse_day(
        store, app_paths, settings, "2026-10-31", now=NOW
    )
    real = [c for c in candidates if c.event == "Real Madrid - Barcelona" and c.market_key == "1x2"]
    assert len(real) == 3 and all(c.p_model is not None for c in real)
    assert all(c.tier in {"experimental", "rejected"} for c in real)  # model not validated
    assert coupon_report.headline == NO_EDGE
    analysis_id = save_analysis(store, coupon_report, candidates, services.policy(app_paths))
    home = next(c for c in real if c.selection == "home")
    coupon = create_coupon(
        store,
        odds_ids=[home.odds_id],
        stake=Decimal("10.00"),
        mode="paper_manual",
        profile=load_profiles(app_paths.config)["sts"],
        now=NOW,
        analysis_id=analysis_id,
    )
    result_csv = b"date;home;away;home_score;away_score\n2026-10-31;Real Madrid CF;FC Barcelona;2;1"
    import_results_csv(
        store,
        result_csv,
        file_name="result.csv",
        source_name="FIXTURE",
        license_note="dane syntetyczne (test)",
        sport="football",
        competition="es.1",
        now=datetime(2026, 11, 2, tzinfo=UTC),
    )
    later = datetime(2026, 11, 2, 1, tzinfo=UTC)
    found = import_source_results(
        store,
        now=later,
        normalizer=services.normalizer(app_paths),
        team_aliases=services.team_aliases(app_paths),
    )
    assert found["recorded"] == 1
    settled = settle_coupon(store, coupon, load_profiles(app_paths.config), now=later)
    assert settled["net_profit"] == "8.48"  # 10 × 0.88 × 2.10 − 10
    stats = performance(store)["paper_manual"]
    assert stats["roi"] == Decimal("0.848") and stats["max_drawdown"] == 0
    assert closing_line_value(store, coupon)[0]["clv"] is None
    exported = export_json(store, app_paths.exports / "flow.json", now=later)
    text = exported.read_text(encoding="utf-8")
    assert '"predictions"' in text and '"settlements"' in text and "openfootball" in text
