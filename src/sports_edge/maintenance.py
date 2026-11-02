"""Storage quality (TASK 007 §P6): size and growth, integrity, restore test, archive plan,
and the monitoring report shown in the application.

Nothing here deletes data: the archive plan only says what could be moved out of the
working database; doing it is always the user's decision.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from sports_edge.ingestion.types import iso
from sports_edge.settings import AppPaths
from sports_edge.storage import SCHEMA_VERSION, Store

GROWTH_TABLES = ("quote_sets", "odds", "predictions", "historical_results", "stat_observations")
DATE_COLUMNS = {
    "quote_sets": "ingested_at",
    "predictions": "created_at",
    "historical_results": "available_at",
    "stat_observations": "fetched_at",
}


def _folder_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def integrity(store: Store) -> dict:
    ok = store.connection.execute("PRAGMA integrity_check").fetchone()[0]
    keys = store.connection.execute("PRAGMA foreign_key_check").fetchall()
    return {"integrity": ok, "foreign_key_errors": len(keys), "ok": ok == "ok" and not keys}


def storage_report(store: Store, paths: AppPaths, *, now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    database = Path(store.path)
    size = database.stat().st_size if database.is_file() else 0
    wal = Path(str(database) + "-wal")
    counts = {
        table: store.scalar(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 — fixed table names
        for table in sorted(store.tables)
        if not table.startswith("sqlite_")
    }
    week = iso(now - timedelta(days=7))
    growth = {}
    for table, column in DATE_COLUMNS.items():
        growth[table] = store.scalar(
            f"SELECT COUNT(*) FROM {table} WHERE {column}>=?",
            (week,),  # noqa: S608
        )
    rows = sum(counts.values()) or 1
    per_row = size / rows
    weekly_rows = sum(growth.values()) + growth.get("quote_sets", 0) * 2.5  # odds per quote set
    return {
        "database": str(database.name),
        "schema": store.scalar("PRAGMA user_version"),
        "schema_expected": SCHEMA_VERSION,
        "bytes": size,
        "wal_bytes": wal.stat().st_size if wal.is_file() else 0,
        "rows": counts,
        "growth_last_7_days": growth,
        "projected_bytes_per_month": int(weekly_rows * per_row * 30 / 7),
        "raw_bytes": _folder_bytes(paths.raw) + _folder_bytes(paths.data / "sources"),
        "backups_bytes": _folder_bytes(paths.backups),
        "integrity": integrity(store),
        "archive_plan": [
            "Kursy (quote_sets, odds) starsze niż 12 miesięcy można wyeksportować do pliku "
            "archiwum i przenieść do osobnej bazy — prognozy i rozliczenia zostają.",
            "Surowe pliki źródłowe (data/sources, data/raw) można spakować po weryfikacji importu.",
            "Kopie zapasowe starsze niż 3 miesiące można przenieść na dysk zewnętrzny.",
            "Aplikacja niczego nie usuwa sama — każda zmiana to decyzja użytkownika.",
        ],
    }


def restore_test(backup_db: Path, store: Store) -> dict:
    """Open a backup copy read-only, check it and compare its row counts with the live
    database (the live one may only have more rows: storage is append-only)."""
    copy = sqlite3.connect(f"file:{backup_db.as_posix()}?mode=ro", uri=True)
    try:
        ok = copy.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [r[0] for r in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        problems = []
        for table in tables:
            if table.startswith("sqlite_") or table not in store.tables:
                continue
            old = copy.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            new = store.scalar(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
            if new < old:
                problems.append(f"{table}: w bazie {new} < w kopii {old}")
        version = copy.execute("PRAGMA user_version").fetchone()[0]
    finally:
        copy.close()
    return {
        "ok": ok == "ok" and not problems,
        "integrity": ok,
        "schema": version,
        "tables": len(tables),
        "problems": problems,
    }


def monitoring(store: Store, paths: AppPaths, settings, *, now: datetime) -> list[dict]:
    """Plain diagnostic report: sources, last fetch, pending work, why no new model.
    Errors are summarised in one sentence — never as raw tracebacks."""
    from sports_edge import collection, retraining
    from sports_edge.results_engine import league_freshness
    from sports_edge.services import MODEL_SCOPES, RESEARCH_COMPETITIONS, SCOPE_LABELS

    out = []
    newest = store.scalar("SELECT MAX(ingested_at) FROM imports WHERE bookmaker_id='sts'")
    out.append(
        {
            "obszar": "Oferta STS",
            "stan": f"ostatni import {newest or 'brak'}",
            "uwagi": "pobieranie tylko na żądanie"
            if not settings.sts_download_enabled
            else "pobieranie włączone",
        }
    )
    fresh = league_freshness(store, RESEARCH_COMPETITIONS, now=now)
    stale = [f["name"] for f in fresh if not f["fresh"]]
    out.append(
        {
            "obszar": "Wyniki lig",
            "stan": f"aktualne {len(fresh) - len(stale)}/{len(fresh)}",
            "uwagi": ", ".join(stale) or "—",
        }
    )
    runs = collection.last_runs(store)
    for job, label in (
        ("results_update", "Aktualizacja wyników"),
        ("prediction_settlement", "Rozliczanie prognoz"),
        ("retraining_review", "Przegląd modeli"),
    ):
        run = runs.get(job)
        out.append(
            {
                "obszar": label,
                "stan": f"{run['status']} ({run['finished_at'][:16]})"
                if run
                else "nie uruchomiono",
                "uwagi": (run or {}).get("reason") or "—",
            }
        )
    report = collection.completeness(store, now=now)
    out.append(
        {
            "obszar": "Prognozy oczekujące",
            "stan": str(report["pending"]),
            "uwagi": f"bez wyniku > 48 h: {report['missing_results_count']}",
        }
    )
    for scope, competitions in MODEL_SCOPES.items():
        ready, reasons = retraining.conditions(store, scope, competitions, now=now)
        out.append(
            {
                "obszar": f"Nowy model — {SCOPE_LABELS[scope]}",
                "stan": retraining.scope_status(store, scope, now=now),
                "uwagi": "warunki spełnione" if ready else "; ".join(reasons),
            }
        )
    disk = storage_report(store, paths, now=now)
    out.append(
        {
            "obszar": "Baza danych",
            "stan": f"{disk['bytes'] / 1e6:.1f} MB",
            "uwagi": "integralność OK" if disk["integrity"]["ok"] else "BŁĄD integralności",
        }
    )
    return out
