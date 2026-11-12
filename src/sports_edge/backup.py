"""Local backups instead of a remote repository: backups/YYYY-MM-DD_HH-MM-SS/.

TASK 016: the automation worker makes a CYCLIC backup once a day (``backups/auto-…/``, the
same content and manifest as a manual one) — checked after it is written, kept by a
retention rule (the newest of each of the last N days and of the last M weeks). Retention
removes cyclic backup FOLDERS only: never a ``pre-migration-*`` copy, never a manual backup,
never a record of the database.
"""

import hashlib
import json
import shutil
import sqlite3
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_edge import __version__
from sports_edge.settings import AppPaths
from sports_edge.storage import snapshot_file

WARSAW = ZoneInfo("Europe/Warsaw")
CYCLIC_PREFIX = "auto-"
STAMP = "%Y-%m-%d_%H-%M-%S"
BACKUP_DEFAULTS = {
    "keep_daily": 7,  # the newest cyclic backup of each of the last 7 days with a backup
    "keep_weekly": 4,  # … and of each of the last 4 weeks with a backup
    "min_free_factor": 2.0,  # free disk space required: this many times the database size
    "check": "quick",  # "quick" (PRAGMA quick_check) or "full" (PRAGMA integrity_check)
}

RESTORE = """Przywracanie kopii Sports Edge AI
1. Zamknij aplikację (okno start.bat).
2. Skopiuj katalog config/ z kopii do katalogu projektu (nadpisze konfigurację).
3. Skopiuj sports_edge.db do data/sports_edge.db, a settings.json do data/settings.json.
4. Jeśli kopia zawiera raw/, skopiuj ją do data/raw/.
5. Uruchom start.bat. Sumy SHA-256 plików są w manifest.json.
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(
    paths: AppPaths,
    *,
    now: datetime,
    include_raw: bool = False,
    prefix: str = "",
    extra: dict | None = None,
) -> Path:
    target = paths.backups / f"{prefix}{now.strftime(STAMP)}"
    if target.exists():
        raise FileExistsError(f"Backup already exists: {target}")
    target.mkdir(parents=True)
    shutil.copytree(paths.config, target / "config")
    if paths.database.is_file():
        # Read-only copy: opening through Store could migrate the schema before the backup.
        snapshot_file(paths.database, target / "sports_edge.db")
    if paths.settings_file.is_file():
        shutil.copy2(paths.settings_file, target / "settings.json")
    if include_raw and paths.raw.is_dir():
        shutil.copytree(paths.raw, target / "raw")
    (target / "RESTORE.txt").write_text(RESTORE, encoding="utf-8")
    files = sorted(p for p in target.rglob("*") if p.is_file())
    manifest = {
        "created_at": now.isoformat(),
        "app_version": __version__,
        "include_raw": include_raw,
        **(extra or {}),
        "files": {
            str(p.relative_to(target)).replace("\\", "/"): {
                "bytes": p.stat().st_size,
                "sha256": _sha256(p),
            }
            for p in files
        },
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return target


def list_backups(paths: AppPaths) -> list[dict]:
    if not paths.backups.is_dir():
        return []
    out = []
    for folder in sorted(paths.backups.iterdir(), reverse=True):
        manifest = folder / "manifest.json"
        if folder.is_dir() and manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            size = sum(f["bytes"] for f in data["files"].values())
            out.append({"folder": folder.name, "created_at": data["created_at"], "bytes": size})
    return out


# ---------------------------------------------------------------- cyclic backups (TASK 016)


def backup_config(paths: AppPaths) -> dict:
    """``[backup]`` of config/app.toml over the defaults (retention, free space, check)."""
    found: dict = {}
    app_toml = paths.config / "app.toml"
    if app_toml.is_file():
        try:
            found = tomllib.loads(app_toml.read_text(encoding="utf-8")).get("backup", {})
        except (OSError, tomllib.TOMLDecodeError):
            found = {}
    merged = {**BACKUP_DEFAULTS, **{k: v for k, v in found.items() if k in BACKUP_DEFAULTS}}
    merged["keep_daily"] = max(1, int(merged["keep_daily"]))
    merged["keep_weekly"] = max(0, int(merged["keep_weekly"]))
    merged["min_free_factor"] = max(1.0, float(merged["min_free_factor"]))
    return merged


def verify(database: Path, *, check: str = "quick") -> str:
    """ "ok" or the first problem the integrity check of a backup copy reports (read-only)."""
    pragma = "integrity_check" if check == "full" else "quick_check"
    try:
        copy = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        try:
            rows = copy.execute(f"PRAGMA {pragma}").fetchall()
        finally:
            copy.close()
    except sqlite3.Error as error:
        return f"{type(error).__name__}: {str(error)[:120]}"
    found = "; ".join(str(r[0]) for r in rows[:3])
    return found or "brak odpowiedzi kontroli"


def cyclic_backups(paths: AppPaths) -> list[dict]:
    """Cyclic backups, newest first: folder, creation time (Warsaw), size, check result."""
    if not paths.backups.is_dir():
        return []
    out = []
    for folder in paths.backups.iterdir():
        manifest = folder / "manifest.json"
        if not (folder.is_dir() and folder.name.startswith(CYCLIC_PREFIX) and manifest.is_file()):
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            created = datetime.strptime(folder.name[len(CYCLIC_PREFIX) :], STAMP)
        except (OSError, ValueError):
            continue
        if not data.get("cyclic"):
            continue
        database = (data.get("files") or {}).get("sports_edge.db") or {}
        out.append(
            {
                "folder": folder.name,
                "path": folder,
                "created": created.replace(tzinfo=WARSAW),
                "bytes": sum(f["bytes"] for f in data["files"].values()),
                "db_bytes": database.get("bytes"),
                "integrity": data.get("integrity"),
                "ok": data.get("integrity") == "ok",
            }
        )
    return sorted(out, key=lambda b: b["created"], reverse=True)


def retention_plan(backups: list[dict], *, keep_daily: int, keep_weekly: int) -> dict:
    """Which cyclic backups stay: the newest verified one of each of the last ``keep_daily``
    days and of each of the last ``keep_weekly`` ISO weeks that have a backup. A backup
    that failed its check stays only while it is the newest one (for diagnosis)."""
    ordered = sorted(backups, key=lambda b: b["created"], reverse=True)
    keep: dict[str, str] = {}
    days: set = set()
    weeks: set = set()
    for item in (b for b in ordered if b["ok"]):
        day = item["created"].date()
        if day not in days and len(days) < keep_daily:
            days.add(day)
            keep.setdefault(item["folder"], "dzienna")
        week = tuple(item["created"].isocalendar()[:2])
        if week not in weeks and len(weeks) < keep_weekly:
            weeks.add(week)
            keep.setdefault(item["folder"], "tygodniowa")
    if ordered and not ordered[0]["ok"]:
        keep.setdefault(ordered[0]["folder"], "najnowsza (nieudana kontrola)")
    return {
        "keep": keep,
        "remove": [b["folder"] for b in ordered if b["folder"] not in keep],
    }


def apply_retention(paths: AppPaths, *, keep_daily: int, keep_weekly: int) -> dict:
    """Remove the cyclic backup folders the plan does not keep. Only a folder directly in
    ``backups/`` whose name starts with ``auto-`` and whose manifest says ``cyclic`` is ever
    removed — ``pre-migration-*`` files and manual backups are not even looked at."""
    found = cyclic_backups(paths)
    plan = retention_plan(found, keep_daily=keep_daily, keep_weekly=keep_weekly)
    removed = []
    by_name = {b["folder"]: b for b in found}
    for name in plan["remove"]:
        folder = by_name[name]["path"]
        if folder.parent != paths.backups or not folder.name.startswith(CYCLIC_PREFIX):
            continue  # defensive: never outside backups/, never another kind of copy
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            removed.append(name)
    return {"kept": plan["keep"], "removed": removed}


def free_space(paths: AppPaths) -> int:
    target = paths.backups if paths.backups.is_dir() else paths.root
    return shutil.disk_usage(target).free


def database_bytes(paths: AppPaths) -> int:
    """The database file with its write-ahead log (what a copy may have to hold)."""
    total = 0
    for name in (paths.database, Path(str(paths.database) + "-wal")):
        if name.is_file():
            total += name.stat().st_size
    return total


def run_cyclic(paths: AppPaths, *, now: datetime, config: dict | None = None) -> dict:
    """One cyclic backup: free-space check → copy through SQLite's backup API → integrity
    check of the copy → retention. Returns ``status`` ("ok" | "skipped" | "failed") with a
    plain reason; nothing is removed when the new copy is not verified."""
    cfg = config or backup_config(paths)
    if not paths.database.is_file():
        return {"status": "skipped", "reason": "brak pliku bazy — nie ma czego kopiować"}
    size = database_bytes(paths)
    free = free_space(paths)
    needed = int(size * cfg["min_free_factor"])
    base = {"db_bytes": size, "free_bytes": free, "needed_bytes": needed}
    if free < needed:
        return {
            **base,
            "status": "skipped",
            "problem": "space",
            "reason": f"za mało wolnego miejsca na kopię: wolne {free / 1e6:.0f} MB, potrzeba "
            f"{needed / 1e6:.0f} MB ({cfg['min_free_factor']:g}× rozmiar bazy)",
        }
    local = now.astimezone(WARSAW).replace(tzinfo=None)
    folder = create_backup(paths, now=local, prefix=CYCLIC_PREFIX, extra={"cyclic": True})
    result = verify(folder / "sports_edge.db", check=cfg["check"])
    manifest = folder / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["integrity"] = result
    data["integrity_check"] = cfg["check"]
    manifest.write_text(json.dumps(data, indent=1), encoding="utf-8")
    copy_bytes = (folder / "sports_edge.db").stat().st_size
    base.update(folder=folder.name, copy_bytes=copy_bytes, integrity=result)
    if result != "ok":
        return {
            **base,
            "status": "failed",
            "problem": "integrity",
            "reason": f"kopia {folder.name} nie przeszła kontroli integralności: {result}",
        }
    kept = apply_retention(paths, keep_daily=cfg["keep_daily"], keep_weekly=cfg["keep_weekly"])
    return {
        **base,
        "status": "ok",
        "reason": "",
        "kept": sorted(kept["kept"]),
        "removed": kept["removed"],
    }
