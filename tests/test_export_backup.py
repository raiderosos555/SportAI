"""JSON/CSV export, local backups and settings persistence."""

import json
from datetime import UTC, datetime

import pytest

from sports_edge.backup import create_backup, list_backups
from sports_edge.export import export_csv, export_json, offers_flat
from sports_edge.repository import ensure_reference_data
from sports_edge.settings import AppSettings, load_settings, save_settings
from sports_edge.storage import Store

pytest.importorskip("pymupdf")


def test_json_export_contains_offers_and_history(store, import_both, tmp_path):
    target = export_json(
        store, tmp_path / "out" / "export.json", now=datetime(2026, 10, 31, tzinfo=UTC)
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["exported_at"].startswith("2026-10-31")
    data = payload["data"]
    assert {"imports", "predictions", "coupons", "settlements", "offers_latest"} <= set(data)
    assert len(data["imports"]) == 2
    legia = [o for o in data["offers_latest"] if o["event"] == "Legia Warszawa - Lech Poznań"]
    assert {o["bookmaker"] for o in legia} == {"sts", "superbet"}
    assert all(isinstance(o["odds"], str) for o in legia)  # Decimal kept exact as text
    assert len(offers_flat(store)) == len(data["offers_latest"])


def test_csv_export(store, import_both, tmp_path):
    files = export_csv(store, tmp_path / "csv")
    names = {f.name for f in files}
    assert "offers_latest.csv" in names and "coupons.csv" in names
    header = (
        (tmp_path / "csv" / "offers_latest.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    )
    assert header.startswith("bookmaker;offer_date;start_at")


def test_backup_snapshot_and_manifest(app_paths):
    with Store(app_paths.database) as db:
        ensure_reference_data(db)
    save_settings(app_paths, AppSettings(offline_mode=False))
    (app_paths.raw / "sts").mkdir(parents=True)
    (app_paths.raw / "sts" / "big.pdf").write_bytes(b"%PDF-1.7 raw")
    target = create_backup(app_paths, now=datetime(2026, 10, 31, 22, 15, 3))
    assert target.name == "2026-10-31_22-15-03"
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert {"sports_edge.db", "settings.json", "RESTORE.txt", "config/app.toml"} <= set(
        manifest["files"]
    )
    assert not (target / "raw").exists()  # large raw files only on request
    with Store(target / "sports_edge.db") as copy:
        assert copy.scalar("SELECT COUNT(*) FROM bookmakers") == 3
    with_raw = create_backup(app_paths, now=datetime(2026, 10, 31, 22, 16), include_raw=True)
    assert (with_raw / "raw" / "sts" / "big.pdf").is_file()
    assert [b["folder"] for b in list_backups(app_paths)] == [with_raw.name, target.name]
    with pytest.raises(FileExistsError):
        create_backup(app_paths, now=datetime(2026, 10, 31, 22, 15, 3))


def test_settings_defaults_and_persistence(app_paths):
    settings = load_settings(app_paths)
    assert settings.offline_mode is True and settings.retain_raw_pdf is False
    assert settings.sts_download_enabled is False
    settings.offline_mode = False
    save_settings(app_paths, settings)
    assert load_settings(app_paths).offline_mode is False
    app_paths.settings_file.write_text("{broken", encoding="utf-8")
    assert load_settings(app_paths).offline_mode is True  # corrupt file -> safe defaults
