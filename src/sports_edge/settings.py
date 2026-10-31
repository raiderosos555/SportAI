"""Local paths and user settings. Everything stays inside the project directory."""

import json
import os
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def project_root() -> Path:
    if env := os.environ.get("SPORTS_EDGE_HOME"):
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in (Path.cwd(), *here.parents):
        if (parent / "config" / "app.toml").is_file():
            return parent
    return here.parents[2]


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def database(self) -> Path:
        return self.data / "sports_edge.db"

    @property
    def demo_database(self) -> Path:
        return self.data / "demo" / "demo.db"

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def exports(self) -> Path:
        return self.data / "exports"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def settings_file(self) -> Path:
        return self.data / "settings.json"


@dataclass
class AppSettings:
    offline_mode: bool = True  # no network calls at all until the user switches it off
    retain_raw_pdf: bool = False  # keep a private local copy only if source terms allow it
    nominal_stake: str = "10.00"  # PLN, used for net EV with payout thresholds
    max_quote_age_minutes: int = 180
    match_tolerance_minutes: int = 10
    sts_download_enabled: bool = False  # explicit opt-in for the official STS PDF links
    arbiscan_enabled: bool = False
    propline_enabled: bool = False


def load_settings(paths: AppPaths) -> AppSettings:
    defaults: dict = {}
    app_toml = paths.config / "app.toml"
    if app_toml.is_file():
        defaults = tomllib.loads(app_toml.read_text(encoding="utf-8")).get("settings", {})
    stored: dict = {}
    if paths.settings_file.is_file():
        try:
            stored = json.loads(paths.settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
    allowed = {f.name for f in fields(AppSettings)}
    merged = {k: v for k, v in {**defaults, **stored}.items() if k in allowed}
    return AppSettings(**merged)


def save_settings(paths: AppPaths, settings: AppSettings) -> None:
    paths.data.mkdir(parents=True, exist_ok=True)
    paths.settings_file.write_text(
        json.dumps(asdict(settings), indent=2, ensure_ascii=False), encoding="utf-8"
    )
