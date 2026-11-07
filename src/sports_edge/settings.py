"""Local paths and user settings. Everything stays inside the project directory."""

import json
import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
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
    # Results providers (TASK 004): free sources only; inactive while offline or without key.
    football_data_enabled: bool = True  # needs the user's own free FOOTBALL_DATA_API_KEY
    openfootball_update_enabled: bool = True  # re-reads current-season CC0 files
    api_sports_enabled: bool = True  # needs the user's own free API_SPORTS_KEY
    pitchapi_enabled: bool = True  # results + match statistics, user's PITCHAPI_KEY
    stats_requests_per_run: int = 60  # polite cap for match statistics per analysis
    # TASK 010: None = the [builder] value of probability_policy.toml (0 = off)
    max_profiles_per_selection: int | None = None
    # TASK 014: optional coupon profiles (``enabled = false`` in probability_policy.toml) the
    # user switched on in Ustawienia — empty by default (e.g. ["safe_solo"] = PEWNY SOLO)
    enabled_profiles: list[str] = field(default_factory=list)
    # Optional language layer (TASK 007 §7): a free LOCAL model (Ollama), off by default.
    llm_enabled: bool = False
    llm_model: str = "hf.co/speakleash/Bielik-11B-v2.3-Instruct-GGUF:Q4_K_M"
    llm_url: str = "http://127.0.0.1:11434"  # only this computer is ever accepted


def local_secret(name: str, root: Path | None = None) -> str | None:
    """Environment variable first, then the git-ignored ``.env`` of the project.

    Values are never logged or shown; only their presence is reported to the user.
    """
    if value := os.environ.get(name):
        return value.strip() or None
    if root is None:
        return None
    try:
        lines = (root / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            return value.strip().strip('"').strip("'") or None
    return None


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
