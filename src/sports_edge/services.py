"""Application workflows shared by the CLI and the Streamlit interface (STS ONLY)."""

import importlib.util
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from sports_edge.analysis.coupon import CouponReport, build_daily_coupon
from sports_edge.analysis.edge_engine import Candidate, evaluate
from sports_edge.analysis.policy import Policy, load_policy
from sports_edge.bookmakers import load_profiles, load_rules
from sports_edge.datasources import openfootball
from sports_edge.matching import load_team_aliases
from sports_edge.models.registry import latest_predictions, model_overview
from sports_edge.offers.sources import STS_URLS, StsOfficialPdf
from sports_edge.offers.text import NameNormalizer, load_normalizer
from sports_edge.pdf.engine import parse_pdf
from sports_edge.repository import ImportOutcome, ensure_reference_data, import_offer, offer_table
from sports_edge.settings import AppPaths, AppSettings
from sports_edge.sportsdata import Split, import_openfootball, predict_offer, run_elo_experiment
from sports_edge.storage import SCHEMA_VERSION, Store

# First research area (TASK 003): 1X2 regular time, five top European leagues.
RESEARCH_COMPETITIONS = ("en.1", "de.1", "es.1", "it.1", "fr.1")
RESEARCH_SPLIT = Split(validation_from="2022-07-01", holdout_from="2024-07-01")


def open_store(paths: AppPaths) -> Store:
    """Production database; a schema migration first copies the file to backups/."""
    store = Store(paths.database, backup_dir=paths.backups)
    ensure_reference_data(store)
    return store


def import_pdf_bytes(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    data: bytes,
    *,
    file_name: str,
    now: datetime,
    source_id: str = "sts_pdf_manual",
    data_kind: str = "prospective",
    fetched_at: datetime | None = None,
    source_url: str | None = None,
) -> ImportOutcome:
    result, document = parse_pdf(data, file_name=file_name)
    raw_path = None
    if settings.retain_raw_pdf and data_kind == "prospective":
        folder = paths.raw / "sts" / now.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{document.sha256}.pdf"
        if not target.exists():
            target.write_bytes(data)
        raw_path = str(target.relative_to(paths.root)).replace("\\", "/")
    return import_offer(
        store,
        result,
        sha256=document.sha256,
        file_name=file_name,
        source_id=source_id,
        data_kind=data_kind,
        ingested_at=now,
        raw_path=raw_path,
        fetched_at=fetched_at,
        source_url=source_url,
    )


def fetch_sts_offer(
    store: Store, paths: AppPaths, settings: AppSettings, day: str, *, now: datetime
) -> ImportOutcome:
    """Official STS link, only on explicit request (no schedule: terms not confirmed)."""
    source = StsOfficialPdf(paths.data / "fetch_log.json")
    document = source.fetch(
        day, offline=settings.offline_mode, enabled=settings.sts_download_enabled
    )
    return import_pdf_bytes(
        store,
        paths,
        settings,
        document.data,
        file_name=document.file_name,
        now=now,
        source_id="sts_pdf_official",
        fetched_at=document.fetched_at,
        source_url=STS_URLS[day],
    )


def normalizer(paths: AppPaths) -> NameNormalizer:
    return load_normalizer(paths.config / "aliases.toml")


def team_aliases(paths: AppPaths) -> dict[tuple[str, str], str]:
    return load_team_aliases(paths.config / "team_aliases.toml", normalizer(paths))


def policy(paths: AppPaths) -> Policy:
    return load_policy(paths.config / "coupon_policy.toml")


def analyse_day(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    day: str,
    *,
    now: datetime,
    data_kinds: tuple[str, ...] = ("prospective", "archive"),
) -> tuple[CouponReport, list[Candidate]]:
    profiles = load_profiles(paths.config)
    rules = policy(paths)
    candidates = evaluate(
        offer_table(store, day=day, data_kinds=data_kinds),
        predictions=latest_predictions(store, as_of=now),
        profile=profiles["sts"],
        market_rules=load_rules(paths.config),
        policy=rules,
        now=now,
        max_age=timedelta(minutes=settings.max_quote_age_minutes),
        stake=Decimal(settings.nominal_stake),
    )
    report = build_daily_coupon(
        candidates,
        rules,
        day=day,
        now=now,
        cost=profiles["sts"].cost,
        stake=Decimal(settings.nominal_stake),
    )
    return report, candidates


def import_openfootball_files(store: Store, paths: AppPaths, *, now: datetime) -> dict:
    """Import every downloaded openfootball file listed in the local manifest."""
    folder = paths.data / "sources" / "openfootball"
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Brak pobranych danych openfootball (data/sources/openfootball)")
    summary = {"files": 0, "inserted": 0, "duplicates": 0, "errors": []}
    for relative in sorted(json.loads(manifest_path.read_text(encoding="utf-8"))):
        season, file_name = relative.split("/")
        league = file_name.removesuffix(".json")
        if league not in openfootball.LEAGUES:
            continue
        out = import_openfootball(
            store,
            (folder / relative).read_bytes(),
            league_code=league,
            season=season,
            file_name=relative,
            now=now,
        )
        summary["files"] += 1
        summary["inserted"] += out["inserted"]
        summary["duplicates"] += out["duplicate"]
        summary["errors"].extend(f"{relative}: {e}" for e in out["errors"])
    return summary


def run_research_experiment(store: Store, paths: AppPaths, *, now: datetime) -> dict:
    return run_elo_experiment(
        store,
        competitions=RESEARCH_COMPETITIONS,
        split=RESEARCH_SPLIT,
        now=now,
        policy=policy(paths),
    )


def latest_model_version(store: Store, sport: str = "football") -> dict | None:
    for row in model_overview(store):
        if row["sport"] == sport and row["status"] != "retired":
            return row
    return None


def generate_predictions(store: Store, paths: AppPaths, *, now: datetime) -> dict:
    """Pre-match forecasts for upcoming STS events with the newest registered model."""
    version = latest_model_version(store)
    if version is None:
        raise ValueError("Brak zarejestrowanego modelu — uruchom eksperyment")
    events = store.rows(
        "SELECT * FROM events WHERE bookmaker_id='sts' AND start_at IS NOT NULL AND start_at>?",
        (now.isoformat(),),
    )
    return predict_offer(
        store,
        version_id=version["version_id"],
        events=events,
        normalizer=normalizer(paths),
        now=now,
        team_aliases=team_aliases(paths),
    )


def startup_check(paths: AppPaths, *, today: date | None = None) -> list[str]:
    """Human-readable checks; lines starting with 'BŁĄD' stop the launcher."""
    today = today or date.today()
    lines = []
    for name in (
        "app.toml",
        "aliases.toml",
        "team_aliases.toml",
        "coupon_policy.toml",
        "markets.toml",
        "sts_rules.toml",
    ):
        path = paths.config / name
        lines.append(f"{'OK  ' if path.is_file() else 'BŁĄD'} config/{name}")
    try:
        profiles = load_profiles(paths.config)
        if set(profiles) != {"sts"}:
            lines.append(f"BŁĄD aktywne profile bukmacherów: {sorted(profiles)} (oczekiwano: sts)")
        for profile in profiles.values():
            valid = profile.cost_valid_on(today)
            status = "OK  " if valid else "UWAGA"
            note = "" if valid else " — profil podatkowy wygasł, EV netto będzie blokowane"
            lines.append(
                f"{status} profil {profile.name}: {profile.cost.id} "
                f"({profile.cost.valid_from} - {profile.cost.valid_until}){note}"
            )
    except (OSError, KeyError, ValueError) as error:
        lines.append(f"BŁĄD profile bukmacherów: {error}")
    try:
        policy(paths)
        load_rules(paths.config)
        lines.append("OK   polityka kuponu i reguły rynków STS")
    except (OSError, KeyError, ValueError) as error:
        lines.append(f"BŁĄD polityka kuponu / reguły rynków: {error}")
    for module, purpose in (
        ("pymupdf", "import PDF"),
        ("streamlit", "interfejs"),
        ("pandas", "tabele"),
    ):
        found = importlib.util.find_spec(module) is not None
        lines.append(f"{'OK  ' if found else 'BŁĄD'} pakiet {module} ({purpose})")
    try:
        with open_store(paths) as store:
            version = store.scalar("PRAGMA user_version")
            migrated = store.migration_backup
        note = f", kopia przed migracją: {migrated}" if migrated else ""
        lines.append(f"OK   baza {paths.database.name} (schemat {version}/{SCHEMA_VERSION}){note}")
    except Exception as error:  # report any database failure to the launcher
        lines.append(f"BŁĄD baza danych: {error}")
    return lines
