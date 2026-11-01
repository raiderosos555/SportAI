"""Local backups instead of a remote repository: backups/YYYY-MM-DD_HH-MM-SS/."""

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from sports_edge import __version__
from sports_edge.settings import AppPaths
from sports_edge.storage import snapshot_file

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


def create_backup(paths: AppPaths, *, now: datetime, include_raw: bool = False) -> Path:
    target = paths.backups / now.strftime("%Y-%m-%d_%H-%M-%S")
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
