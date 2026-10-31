"""Application workflows shared by the CLI and the Streamlit interface."""

import importlib.util
from datetime import date, datetime, timedelta
from decimal import Decimal

from sports_edge.analysis.comparison import comparison_rows, counterpart_index
from sports_edge.analysis.coupon import CouponReport, build_daily_coupon
from sports_edge.analysis.edge_engine import Candidate, evaluate
from sports_edge.analysis.policy import Policy, load_policy
from sports_edge.bookmakers import load_profiles
from sports_edge.matching import Match, find_matches
from sports_edge.models.registry import latest_predictions
from sports_edge.offers.sources import StsOfficialPdf
from sports_edge.offers.text import NameNormalizer, load_normalizer
from sports_edge.pdf.engine import parse_pdf
from sports_edge.repository import ImportOutcome, import_offer, offer_table
from sports_edge.settings import AppPaths, AppSettings
from sports_edge.storage import SCHEMA_VERSION, Store

SOURCE_FOR = {"sts": "sts_pdf_manual", "superbet": "superbet_pdf_manual"}


def import_pdf_bytes(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    data: bytes,
    *,
    file_name: str,
    bookmaker: str | None,
    now: datetime,
    source_id: str | None = None,
    data_kind: str = "prospective",
) -> ImportOutcome:
    result, document = parse_pdf(data, bookmaker=bookmaker, file_name=file_name)
    raw_path = None
    if settings.retain_raw_pdf and data_kind == "prospective":
        folder = paths.raw / result.bookmaker / now.strftime("%Y-%m-%d")
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
        source_id=source_id or SOURCE_FOR.get(result.bookmaker, "generic_pdf_manual"),
        data_kind=data_kind,
        ingested_at=now,
        raw_path=raw_path,
    )


def fetch_sts_offer(
    store: Store, paths: AppPaths, settings: AppSettings, day: str, *, now: datetime
) -> ImportOutcome:
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
        bookmaker="sts",
        now=now,
        source_id="sts_pdf_official",
    )


def normalizer(paths: AppPaths) -> NameNormalizer:
    return load_normalizer(paths.config / "aliases.toml")


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
) -> tuple[CouponReport, list[Candidate], list[dict], list[Match]]:
    profiles = load_profiles(paths.config)
    rules = policy(paths)
    rows = offer_table(store, day=day, data_kinds=data_kinds)
    matches = find_matches(
        store,
        day=day,
        normalizer=normalizer(paths),
        tolerance_minutes=settings.match_tolerance_minutes,
    )
    index, pairs = counterpart_index(matches, rows)
    max_age = timedelta(minutes=settings.max_quote_age_minutes)
    candidates = evaluate(
        rows,
        predictions=latest_predictions(store, as_of=now),
        profiles=profiles,
        policy=rules,
        now=now,
        max_age=max_age,
        stake=Decimal(settings.nominal_stake),
        counterparts=index,
    )
    report = build_daily_coupon(
        candidates,
        rules,
        day=day,
        now=now,
        cost=profiles["sts"].cost if "sts" in profiles else None,
        stake=Decimal(settings.nominal_stake),
    )
    comparison = comparison_rows(pairs, profiles, now=now, max_age=max_age)
    return report, candidates, comparison, matches


def startup_check(paths: AppPaths, *, today: date | None = None) -> list[str]:
    """Human-readable checks; lines starting with 'BŁĄD' stop the launcher."""
    today = today or date.today()
    lines = []
    for name in ("app.toml", "aliases.toml", "coupon_policy.toml", "markets.toml"):
        path = paths.config / name
        lines.append(f"{'OK  ' if path.is_file() else 'BŁĄD'} config/{name}")
    try:
        profiles = load_profiles(paths.config)
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
        lines.append("OK   polityka kuponu")
    except (OSError, KeyError, ValueError) as error:
        lines.append(f"BŁĄD polityka kuponu: {error}")
    for module, purpose in (
        ("pymupdf", "import PDF"),
        ("streamlit", "interfejs"),
        ("pandas", "tabele"),
    ):
        found = importlib.util.find_spec(module) is not None
        lines.append(f"{'OK  ' if found else 'BŁĄD'} pakiet {module} ({purpose})")
    try:
        with Store(paths.database) as store:
            version = store.scalar("PRAGMA user_version")
        lines.append(f"OK   baza {paths.database.name} (schemat {version}/{SCHEMA_VERSION})")
    except Exception as error:  # report any database failure to the launcher
        lines.append(f"BŁĄD baza danych: {error}")
    return lines
