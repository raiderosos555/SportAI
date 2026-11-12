"""Storage quality (TASK 007 §P6): size and growth, integrity, restore test, archive plan,
and the monitoring report shown in the application.

Nothing here deletes data: the archive plan only says what could be moved out of the
working database; doing it is always the user's decision.
"""

import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sports_edge.ingestion.types import iso
from sports_edge.settings import AppPaths
from sports_edge.storage import SCHEMA_VERSION, Store

GROWTH_TABLES = ("quote_sets", "odds", "predictions", "historical_results", "stat_observations")
BUSY_TIMEOUT_MS = 15000  # the Store connection's own timeout (sqlite3.connect(timeout=15))
GROWTH_DAYS = 7
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
            "Aplikacja nie usuwa sama żadnych danych; retencja dotyczy wyłącznie starszych "
            "kopii cyklicznych (backups/auto-…), nigdy kopii sprzed migracji ani rekordów.",
        ],
    }


def checkpoint(store: Store) -> dict:
    """TASK 016 §2.3: move the write-ahead log into the database file and truncate it.

    Never waits: with a reader or writer active the checkpoint is skipped (the next one
    will do it) — the application is not blocked. Returns the status with a plain reason."""
    wal = Path(str(store.path) + "-wal")
    before = wal.stat().st_size if wal.is_file() else 0
    connection = store.connection
    if connection.in_transaction:
        return {"status": "skipped", "reason": "trwa zapis — pominięto", "wal_before": before}
    try:
        connection.execute("PRAGMA busy_timeout=0")
        row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error as error:
        return {
            "status": "skipped",
            "reason": f"baza zajęta — pominięto ({type(error).__name__})",
            "wal_before": before,
        }
    finally:
        try:
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        except sqlite3.Error:
            pass
    after = wal.stat().st_size if wal.is_file() else 0
    busy = bool(row[0]) if row else False
    return {
        "status": "skipped" if busy else "ok",
        "reason": "baza zajęta — pominięto" if busy else "",
        "wal_before": before,
        "wal_after": after,
    }


def _size_points(store: Store, paths: AppPaths) -> list[tuple[datetime, int, str]]:
    """Known sizes of the database in the past: (moment, bytes, where the number is from) —
    the daily backup runs of the worker, cyclic and manual backups, pre-migration copies."""
    points: list[tuple[datetime, int, str]] = []
    if "job_runs" in store.tables:
        for row in store.rows(
            "SELECT finished_at, payload_json FROM job_runs WHERE job='auto:backup' "
            "AND status IN ('ok','skipped','failed')"
        ):
            size = json.loads(row["payload_json"] or "{}").get("logical_bytes")
            if isinstance(size, int) and size > 0:
                points.append(
                    (datetime.fromisoformat(row["finished_at"]), size, "zadanie kopii zapasowej")
                )
    if paths.backups.is_dir():
        from sports_edge.backup import CYCLIC_PREFIX, STAMP, WARSAW

        for item in paths.backups.iterdir():
            name = item.name
            try:
                if item.is_file() and name.startswith("pre-migration-") and name.endswith(".db"):
                    stamp = name[:-3].split("_", 1)[1]
                    moment = datetime.strptime(stamp, STAMP).replace(tzinfo=UTC)
                    points.append((moment, item.stat().st_size, "kopia sprzed migracji"))
                elif item.is_dir() and (item / "sports_edge.db").is_file():
                    stamp = name[len(CYCLIC_PREFIX) :] if name.startswith(CYCLIC_PREFIX) else name
                    moment = datetime.strptime(stamp, STAMP).replace(tzinfo=WARSAW)
                    points.append(
                        (moment, (item / "sports_edge.db").stat().st_size, "kopia zapasowa")
                    )
            except (OSError, ValueError, IndexError):
                continue
    return sorted(points, key=lambda p: p[0])


def size_report(store: Store, paths: AppPaths, *, now: datetime) -> dict:
    """TASK 016 §2.4: the size of the database, its growth over the last 7 days and the free
    disk space. The growth is the difference to a KNOWN earlier size (a backup or a daily
    backup run); without such a point it is "brak danych" — never 0 and never a guess."""
    database = Path(store.path)
    wal = Path(str(database) + "-wal")
    file_bytes = database.stat().st_size if database.is_file() else 0
    pages = store.scalar("PRAGMA page_count") or 0
    logical = pages * (store.scalar("PRAGMA page_size") or 0)
    try:
        free = shutil.disk_usage(database.parent if database.parent.is_dir() else paths.root).free
    except OSError:
        free = None
    target = now - timedelta(days=GROWTH_DAYS)
    # the known size closest to 7 days ago (at least a day old); the text says its real age
    earlier = [p for p in _size_points(store, paths) if p[0] <= now - timedelta(days=1)]
    reference = min(earlier, key=lambda p: abs(p[0] - target)) if earlier else None
    growth = None
    if reference is not None:
        days = (now - reference[0]).total_seconds() / 86400
        if days >= 1:
            growth = {
                "bytes": logical - reference[1],
                "since": reference[0].isoformat(),
                "days": round(days, 1),
                "per_day": (logical - reference[1]) / days,
                "basis": reference[2],
                "exact_week": abs(days - GROWTH_DAYS) < 2,
            }
    return {
        "file_bytes": file_bytes,
        "wal_bytes": wal.stat().st_size if wal.is_file() else 0,
        "logical_bytes": logical,
        "free_bytes": free,
        "growth": growth,
    }


def size_lines(report: dict) -> dict:
    """Polish texts of ``size_report`` for the cards of Źródła i ustawienia."""

    def mb(value) -> str:
        if value is None:
            return "brak danych"
        if abs(value) >= 1e9:
            return f"{value / 1e9:.2f} GB".replace(".", ",")
        return f"{value / 1e6:.1f} MB".replace(".", ",")

    growth = report["growth"]
    if growth is None:
        growth_value, growth_note = "brak danych", "brak wcześniejszego pomiaru rozmiaru"
    else:
        since = datetime.fromisoformat(growth["since"]).astimezone().strftime("%d.%m")
        days = f"{growth['days']:.1f}".replace(".", ",")
        growth_value = ("+" if growth["bytes"] >= 0 else "−") + mb(abs(growth["bytes"]))
        growth_note = (
            f"od {since} ({days} dnia)" if growth["days"] < 2 else f"od {since} ({days} dni)"
        ) + f" · ok. {mb(abs(growth['per_day']))} dziennie · {growth['basis']}"
    return {
        "size": mb(report["logical_bytes"]),
        "size_note": f"plik {mb(report['file_bytes'])} + dziennik WAL {mb(report['wal_bytes'])}",
        "growth": growth_value,
        "growth_note": growth_note,
        "free": mb(report["free_bytes"]),
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
