"""Application workflows shared by the CLI and the Streamlit interface (STS ONLY)."""

import importlib.util
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sports_edge import learning
from sports_edge.analysis.coupon import CouponReport, build_daily_coupon, diagnose, save_analysis
from sports_edge.analysis.coupon_builder import BuilderReport, build_coupons
from sports_edge.analysis.edge_engine import Candidate, evaluate
from sports_edge.analysis.policy import Policy, load_policy
from sports_edge.bookmakers import load_profiles, load_rules
from sports_edge.collection import record_job, settle_predictions
from sports_edge.coupon_store import (  # noqa: F401 — re-exported (TASK 010 move)
    backfill_lineage,
    my_coupon_payload,
    proposal_payload,
    save_coupon_proposals,
    save_my_coupon,
    save_my_coupon_result,
    save_proposals,
    saved_proposals,
)
from sports_edge.datasources import openfootball
from sports_edge.datasources.api_sports import ENV_KEY as API_SPORTS_ENV
from sports_edge.datasources.api_sports import (
    ApiSportsFootballProvider,
    ApiSportsGamesProvider,
)
from sports_edge.datasources.base import SportsResultsProvider
from sports_edge.datasources.bookmaker_results import StsResultsProvider, SuperbetResultsProvider
from sports_edge.datasources.competitions import COMPETITIONS, by_category, for_sts_league
from sports_edge.datasources.football_data import FootballDataOrgProvider, api_key
from sports_edge.datasources.pitchapi import ENV_KEY as PITCH_ENV
from sports_edge.datasources.pitchapi import PitchApiProvider
from sports_edge.ingestion.types import canonical as canonical_json
from sports_edge.ingestion.types import digest
from sports_edge.matching import (
    TeamIdentities,
    alias_map,
    load_team_aliases,
    load_team_identities,
    match_participant,
)
from sports_edge.models.elo import Elo, walk_forward
from sports_edge.models.registry import latest_predictions, model_overview
from sports_edge.offers.labels import market_label
from sports_edge.offers.sources import STS_URLS, SourceBlocked, StsOfficialPdf
from sports_edge.offers.text import NameNormalizer, load_normalizer
from sports_edge.paper import (
    import_source_results,
    latest_result,
    record_status_results,
    resolve,
    settle_coupon,
)
from sports_edge.pdf.engine import ParseError, parse_pdf
from sports_edge.probability_lab import (
    FAMILY as GOALS_FAMILY,
)
from sports_edge.probability_lab import (
    ProbabilityPolicy,
    league_status,
    load_probability_policy,
    predict_offer_goals,
    run_probability_experiment,
)
from sports_edge.repository import (
    ImportOutcome,
    ensure_reference_data,
    import_offer,
    imports,
    offer_table,
)
from sports_edge.results_engine import (
    backfill_results,
    effective_results,
    known_names,
    league_freshness,
    update_results,
)
from sports_edge.settings import AppPaths, AppSettings, local_secret
from sports_edge.sports_intel import collect_match_stats, features_as_of, fetch_lineups
from sports_edge.sportsdata import (
    MAX_DATA_GAP_DAYS,
    Split,
    _forecasts,
    _scoped,
    history,
    import_openfootball,
    predict_offer,
    run_elo_experiment,
)
from sports_edge.storage import SCHEMA_VERSION, Store

WARSAW = ZoneInfo("Europe/Warsaw")

# First research area (TASK 003): 1X2 regular time, five top European leagues.
# TASK 006: six leagues with PitchAPI history since 2021/22 and Ekstraklasa (API-Sports
# 2022-2024 + PitchAPI) join the five top leagues; each league must also pass its own gate.
# Each scope has its own Elo and Dixon–Coles versions and its own pooled validation: one
# model for all 12 leagues was worse calibrated (TASK 006 experiment, 01.11.2026).
CORE_COMPETITIONS = ("en.1", "de.1", "es.1", "it.1", "fr.1")
EXTENDED_COMPETITIONS = ("en.2", "nl.1", "pt.1", "be.1", "de.2", "tr.1", "pl.1")
MODEL_SCOPES = {"core": CORE_COMPETITIONS, "extended": EXTENDED_COMPETITIONS}
SCOPE_LABELS = {"core": "5 czołowych lig", "extended": "7 lig dodanych w TASK 006"}
RESEARCH_COMPETITIONS = CORE_COMPETITIONS + EXTENDED_COMPETITIONS
RESEARCH_SPLIT = Split(validation_from="2022-07-01", holdout_from="2024-07-01")
# TASK 007 protocol v2: the final holdout (from 2024-07-01) never decides anything. The
# added leagues have results only from 2021/22 (Poland and Turkey from 2022/23), so their
# hyperparameter and calibration windows are half seasons.
PROTOCOLS = {
    "core": Split(
        validation_from="2021-07-01",
        calibration_from="2022-07-01",
        selection_from="2023-07-01",
        holdout_from="2024-07-01",
    ),
    "extended": Split(
        validation_from="2022-07-01",
        calibration_from="2023-01-01",
        selection_from="2023-07-01",
        holdout_from="2024-07-01",
    ),
}
# What the registered Elo model forecasts (double chance follows from the same 1X2).
MODEL_MARKETS = {("1x2", "FT"), ("double_chance", "FT")}
# TASK 009: the regular results update covers domestic leagues and the other sports; cups and
# national teams have their own import (international_data) with fixture-evidence aliases.
RESULTS_COMPETITIONS = tuple(
    code for code, c in COMPETITIONS.items() if c.sport != "football" or c.category == "domestic"
)
EUROPEAN_COMPETITIONS = by_category("european")
NATIONAL_COMPETITIONS = by_category("national")


def open_store(paths: AppPaths) -> Store:
    """Production database; a schema migration first copies the file to backups/."""
    store = Store(paths.database, backup_dir=paths.backups)
    ensure_reference_data(store)
    ensure_model_roles(store)
    backfill_lineage(store, now=datetime.now(UTC))  # TASK 010: idempotent, append-only
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


def team_identities(paths: AppPaths) -> TeamIdentities:
    return load_team_identities(paths.config / "team_identities.toml", normalizer(paths))


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
    as_of: datetime | None = None,
) -> tuple[CouponReport, list[Candidate]]:
    """Every selection of an offer day with its forecast and status at ``now``; ``as_of``
    restricts the offer to what was known then (historical simulator)."""
    profiles = load_profiles(paths.config)
    rules = policy(paths)
    candidates, _predictions = day_candidates_at(
        store, paths, settings, day, now=now, data_kinds=data_kinds, as_of=as_of
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


def run_research_experiment(
    store: Store,
    paths: AppPaths,
    *,
    now: datetime,
    competitions: tuple[str, ...] = CORE_COMPETITIONS,
) -> dict:
    return run_elo_experiment(
        store,
        competitions=competitions,
        split=RESEARCH_SPLIT,
        now=now,
        policy=policy(paths),
        identities=team_identities(paths),
    )


ELO_FAMILY = "elo_ordered_draw"


def latest_model_version(
    store: Store, sport: str = "football", family: str = ELO_FAMILY
) -> dict | None:
    """Newest non-retired version of one model family (Elo unless asked otherwise)."""
    for row in model_overview(store):
        if row["sport"] == sport and row["family"] == family and row["status"] != "retired":
            return row
    return None


def version_competitions(row: dict) -> tuple[str, ...]:
    data = json.loads(row.get("training_data_json") or "{}")
    found = data.get("competitions") or json.loads(row["validation_json"]).get("competitions")
    return tuple(found or ())


def scope_name(scope: tuple[str, ...]) -> str | None:
    return next((n for n, found in MODEL_SCOPES.items() if tuple(found) == tuple(scope)), None)


def scope_version(
    store: Store, scope: tuple[str, ...], family: str = ELO_FAMILY, sport: str = "football"
) -> dict | None:
    """Served version (champion) of a family in a scope.

    TASK 007: the champion is recorded in the model history (activation / rollback by the
    user); a new version waits as a challenger. Without a recorded champion the newest
    version of the scope is served, as before.
    """
    name = scope_name(scope)
    champion = learning.champion_id(store, name, family) if name else None
    if champion is not None:
        for row in model_overview(store):
            if row["version_id"] == champion and row["status"] != "retired":
                return row
    return newest_scope_version(store, scope, family, sport)


def newest_scope_version(
    store: Store, scope: tuple[str, ...], family: str = ELO_FAMILY, sport: str = "football"
) -> dict | None:
    """Newest version of a family trained on (a subset of) the scope's competitions.

    Versions spanning several scopes (e.g. the 12-league experiment of TASK 006) are kept in
    the history but never served.
    """
    for row in model_overview(store):
        if row["sport"] != sport or row["family"] != family or row["status"] == "retired":
            continue
        competitions = version_competitions(row)
        if competitions and set(competitions) <= set(scope):
            return row
    return None


def active_versions(store: Store) -> dict[str, dict[str, dict | None]]:
    return {
        name: {
            "elo": scope_version(store, scope),
            "goals": scope_version(store, scope, GOALS_FAMILY),
        }
        for name, scope in MODEL_SCOPES.items()
    }


def ensure_model_roles(store: Store, *, now: datetime | None = None) -> int:
    """Seed the model history once (TASK 007): which versions were served before and which
    versions had their holdout used for decisions. Idempotent; never changes a result."""
    now = now or datetime.now(UTC)
    served = {}
    for name, scope in MODEL_SCOPES.items():
        for family in (ELO_FAMILY, GOALS_FAMILY):
            row = newest_scope_version(store, scope, family)
            if row is not None:
                served[(name, family)] = row
    v1 = [
        row["version_id"]
        for row in model_overview(store)
        if json.loads(row["validation_json"]).get("protocol") not in ("v2", "v3")
    ]
    return learning.ensure_roles(store, now=now, served=served, holdout_used=v1)


def challenger_versions(store: Store) -> dict[str, dict[str, dict]]:
    """Challengers waiting for a decision (shadow mode), per scope and model."""
    rows = {row["version_id"]: row for row in model_overview(store)}
    out: dict[str, dict[str, dict]] = {}
    for name in MODEL_SCOPES:
        for key, family in (("elo", ELO_FAMILY), ("goals", GOALS_FAMILY)):
            waiting = learning.challengers(store, name, family)
            if waiting and waiting[-1] in rows:
                out.setdefault(name, {})[key] = rows[waiting[-1]]
    return out


def active_version_ids(store: Store) -> set[str]:
    return {
        row["version_id"]
        for pair in active_versions(store).values()
        for row in pair.values()
        if row is not None
    }


def scope_has_data(store: Store, scope: tuple[str, ...], minimum: int = 500) -> bool:
    marks = ",".join("?" for _ in scope)
    return (
        store.scalar(
            f"SELECT COUNT(*) FROM historical_results WHERE competition IN ({marks})", scope
        )
        >= minimum
    )


def _upcoming(store: Store, now: datetime) -> list[dict]:
    return store.rows(
        "SELECT * FROM events WHERE bookmaker_id='sts' AND start_at IS NOT NULL AND start_at>?",
        (now.isoformat(),),
    )


def generate_predictions(
    store: Store, paths: AppPaths, *, now: datetime, events: list[dict] | None = None
) -> dict:
    """Pre-match Elo forecasts for upcoming STS events with the served version of each scope."""
    events = _upcoming(store, now) if events is None else events
    out: dict = {"saved": 0, "kept": 0, "skipped": [], "scopes": {}, "shadow": {}}
    for name, pair in active_versions(store).items():
        if pair["elo"] is None:
            continue
        found = predict_offer(
            store,
            version_id=pair["elo"]["version_id"],
            events=events,
            normalizer=normalizer(paths),
            now=now,
            team_aliases=team_aliases(paths),
            identities=team_identities(paths),
        )
        out["scopes"][name] = {k: found[k] for k in ("saved", "kept")}
        out["saved"] += found["saved"]
        out["kept"] += found["kept"]
        out["skipped"] += found["skipped"]
    # shadow mode: challengers forecast the same events; archived, never served
    for name, pair in challenger_versions(store).items():
        if "elo" in pair:
            found = predict_offer(
                store,
                version_id=pair["elo"]["version_id"],
                events=events,
                normalizer=normalizer(paths),
                now=now,
                team_aliases=team_aliases(paths),
                identities=team_identities(paths),
            )
            out["shadow"][name] = {k: found[k] for k in ("saved", "kept")}
    if not out["scopes"]:
        raise ValueError("Brak zarejestrowanego modelu — uruchom eksperyment")
    return out


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
        "probability_policy.toml",
        "team_identities.toml",
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
    try:
        rules = probability_policy(paths)
        lines.append(
            f"OK   polityka prawdopodobieństwa {rules.version} (profile: "
            f"{', '.join(p['label'] for p in rules.profiles.values())})"
        )
    except (OSError, KeyError, ValueError) as error:
        lines.append(f"BŁĄD polityka prawdopodobieństwa (kreator kuponów): {error}")
    for module, purpose in (
        ("pymupdf", "import PDF"),
        ("streamlit", "interfejs"),
        ("pandas", "tabele"),
    ):
        found = importlib.util.find_spec(module) is not None
        lines.append(f"{'OK  ' if found else 'BŁĄD'} pakiet {module} ({purpose})")
    if local_secret(API_SPORTS_ENV, paths.root):
        lines.append("OK   klucz API-Sports (Ekstraklasa i inne dyscypliny)")
    else:
        lines.append("UWAGA brak klucza API_SPORTS_KEY (.env) — Ekstraklasa bez wyników")
    if local_secret(PITCH_ENV, paths.root):
        lines.append("OK   klucz PitchAPI (wyniki, statystyki meczowe, składy)")
    else:
        lines.append("UWAGA brak klucza PITCHAPI_KEY (.env) — bez statystyk meczowych i składów")
    if api_key(paths.root):
        lines.append("OK   klucz football-data.org (bieżące wyniki 5 lig)")
    else:
        lines.append(
            "UWAGA brak klucza FOOTBALL_DATA_API_KEY (.env) — bez bieżących wyników prognozy "
            "mogą być wstrzymane"
        )
    try:
        with open_store(paths) as store:
            version = store.scalar("PRAGMA user_version")
            migrated = store.migration_backup
        note = f", kopia przed migracją: {migrated}" if migrated else ""
        lines.append(f"OK   baza {paths.database.name} (schemat {version}/{SCHEMA_VERSION}){note}")
    except Exception as error:  # report any database failure to the launcher
        lines.append(f"BŁĄD baza danych: {error}")
    return lines


# ---------------------------------------------------------------- TASK 004: results engine


def results_providers(
    paths: AppPaths, settings: AppSettings, *, opener=None
) -> list[SportsResultsProvider]:
    """Free providers in hierarchy order; bookmaker websites are listed but inactive."""
    providers: list[SportsResultsProvider] = []
    if settings.football_data_enabled:
        providers.append(FootballDataOrgProvider(api_key(paths.root), opener=opener))
    if settings.pitchapi_enabled:
        providers.append(PitchApiProvider(local_secret(PITCH_ENV, paths.root), opener=opener))
    if settings.api_sports_enabled:
        key = local_secret(API_SPORTS_ENV, paths.root)
        quota = paths.data / "api_sports_quota.json"
        providers.append(ApiSportsFootballProvider(key, quota_path=quota, opener=opener))
        providers += [
            ApiSportsGamesProvider(key, sport=sport, quota_path=quota, opener=opener)
            for sport in ("basketball", "ice_hockey")
        ]
    if settings.openfootball_update_enabled:
        providers.append(openfootball.OpenfootballProvider(opener=opener))
    providers += [StsResultsProvider(), SuperbetResultsProvider()]
    return providers


# TASK 006: past seasons that make a league modellable — (competition, source id, first and
# last season start year, new clubs accepted from this source). API-Sports Free covers only
# seasons 2022-2024 (checked 01.11.2026). Its Ekstraklasa club names were reviewed against
# PitchAPI on 01.11.2026: two need aliases (config/team_aliases.toml), the remaining unknown
# names are clubs absent from the PitchAPI seasons (relegated earlier).
HISTORY_PLAN: tuple[tuple[str, str, int, int, bool], ...] = (
    ("en.2", "pitchapi", 2021, 2026, False),
    ("nl.1", "pitchapi", 2021, 2026, False),
    ("pt.1", "pitchapi", 2021, 2026, False),
    ("be.1", "pitchapi", 2021, 2026, False),
    ("de.2", "pitchapi", 2021, 2026, False),
    ("tr.1", "pitchapi", 2022, 2026, False),
    ("pl.1", "pitchapi", 2025, 2026, False),
    ("pl.1", "api_sports_football", 2022, 2024, True),
)


def backfill_history(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    *,
    now: datetime,
    plan: Sequence[tuple[str, str, int, int, bool]] = HISTORY_PLAN,
    providers: Sequence[SportsResultsProvider] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Complete past seasons from free legal sources (one request batch per season).

    A finished season already present from the same source is not requested again.
    """
    if settings.offline_mode:
        raise ValueError("Tryb offline jest włączony — uzupełnianie historii wymaga sieci")
    available = {p.id: p for p in (providers or results_providers(paths, settings))}
    today = now.astimezone(WARSAW).date()
    names, identities = normalizer(paths), team_identities(paths)
    out = []
    for competition, source, first, last, new_clubs in plan:
        provider = available.get(source)
        if provider is None or not getattr(provider, "key", True):
            out.append({"competition": competition, "provider": source, "errors": ["brak klucza"]})
            continue
        family = getattr(provider, "family", provider.id)
        windows = []
        for year in range(first, last + 1):
            start, end = date(year, 7, 1), min(date(year + 1, 7, 1), today + timedelta(days=1))
            finished = end <= today
            stored = store.scalar(
                """SELECT COUNT(*) FROM historical_results WHERE competition=? AND provider=?
                AND match_date>=? AND match_date<?""",
                (competition, family, start.isoformat(), end.isoformat()),
            )
            if finished and stored:
                continue  # complete past season already imported from this source
            windows.append((start, end))
        report = backfill_results(
            store,
            provider,
            competition,
            windows,
            now=now,
            normalizer=names,
            aliases=alias_map(store, team_aliases(paths)),
            raw_dir=paths.data / "sources" / "results",
            identities=identities,
            progress=progress,
            accept_new_clubs=new_clubs,
        )
        out.append(report)
    return out


# ---------------------------------------------------------------- TASK 009: international


def international_providers(
    paths: AppPaths, settings: AppSettings, *, opener=None, members=None
) -> dict[str, SportsResultsProvider]:
    """Sources of UEFA cups and national teams (keys stay in the environment or .env)."""
    from sports_edge.datasources.openfootball_cups import OpenfootballCupsProvider
    from sports_edge.datasources.openfootball_int import OpenfootballIntProvider

    raw = paths.data / "sources" / "openfootball"
    found: dict[str, SportsResultsProvider] = {
        "openfootball_cups": OpenfootballCupsProvider(raw / "champions-league", opener=opener),
        "openfootball_int": OpenfootballIntProvider(
            raw / "internationals", opener=opener, members=members
        ),
    }
    if settings.football_data_enabled:
        found["football_data_org"] = FootballDataOrgProvider(api_key(paths.root), opener=opener)
    if settings.pitchapi_enabled:
        found["pitchapi"] = PitchApiProvider(local_secret(PITCH_ENV, paths.root), opener=opener)
    return found


def club_identities(store: Store, paths: AppPaths, *, now: datetime) -> TeamIdentities:
    """Reviewed renames plus the evidence-based links of cup clubs to domestic clubs."""
    from sports_edge.clubs import links, with_links

    base = team_identities(paths)
    found = links(
        store,
        normalizer=normalizer(paths),
        aliases=alias_map(store, team_aliases(paths)),
        identities=base,
        until=now,
    )
    return with_links(base, found)


def import_international(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    *,
    now: datetime,
    cups: bool = True,
    national: bool = True,
    current_only: bool = False,
    providers: dict[str, SportsResultsProvider] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """UEFA cups (history or the current season) and, optionally, national teams."""
    from sports_edge import international_data as data

    if settings.offline_mode:
        raise ValueError("Tryb offline jest włączony — import rozgrywek wymaga sieci")
    available = providers or international_providers(paths, settings)
    common = {
        "now": now,
        "normalizer": normalizer(paths),
        "extra_aliases": team_aliases(paths),
        "raw_dir": paths.data / "sources" / "results",
        "identities": team_identities(paths),
        "progress": progress,
    }
    plan = data.current_plan(now) if current_only else data.CUP_PLAN
    report = {
        "cups": data.run_plan(store, available, plan, **common) if cups else [],
        "national": [],
    }
    if national:
        plan = data.national_current_plan(now) if current_only else data.NATIONAL_PLAN
        first = [p for p in plan if p[0] in ("uefa.nl", "uefa.euro_q")]
        report["national"] = data.run_plan(store, available, first, **common)
        if "openfootball_int" in available and hasattr(available["openfootball_int"], "members"):
            available["openfootball_int"].members = data.members(store, until=now)
        rest = [p for p in plan if p[0] not in ("uefa.nl", "uefa.euro_q")]
        report["national"] += data.run_plan(store, available, rest, **common)
    entries = report["cups"] + report["national"]
    errors = [f"{e['competition']}: {e['error']}" for e in entries if e.get("error")]
    record_job(
        store,
        "international_results",
        trigger="analysis",
        started=now,
        finished=now,
        status="partial" if errors else "ok",
        reason="; ".join(errors[:3])[:500],
        payload={
            "inserted": sum(e.get("inserted", 0) for e in entries),
            "unmatched": sum(len(e.get("unmatched", [])) for e in entries),
            "learned_aliases": sum(len(e.get("learned_aliases", [])) for e in entries),
        },
    )
    return report


def update_results_data(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    *,
    now: datetime,
    providers: Sequence[SportsResultsProvider] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Missing results → history; then statuses and results of STS events (certain only)."""
    names = normalizer(paths)
    extra = team_aliases(paths)
    report = update_results(
        store,
        providers if providers is not None else results_providers(paths, settings),
        competitions=RESULTS_COMPETITIONS,
        now=now,
        normalizer=names,
        aliases=alias_map(store, extra),
        offline=settings.offline_mode,
        raw_dir=paths.data / "sources" / "results",
        progress=progress,
        identities=team_identities(paths),
    )
    report["sts_statuses"] = record_status_results(
        store, report.pop("status_records"), now=now, normalizer=names, team_aliases=extra
    )
    report["sts_results"] = import_source_results(
        store,
        now=now,
        normalizer=names,
        team_aliases=extra,
        only_coupons=False,
        identities=club_identities(store, paths, now=now),  # cup clubs = league clubs
    )
    report["settlements"] = settle_coupons(store, paths, now=now)
    # TASK 007: every prospective forecast is settled, not only the legs of coupons
    report["prediction_settlements"] = settle_predictions(store, now=now)
    errors = [str(e) for e in report.get("errors") or []]
    record_job(
        store,
        "results_update",
        trigger="analysis",
        started=now,
        finished=now,
        status="partial" if errors else "ok",
        reason="; ".join(errors[:3])[:500],
        payload={"settlements": report["settlements"]},
    )
    record_job(
        store,
        "prediction_settlement",
        trigger="results_update",
        started=now,
        finished=now,
        status="ok",
        payload=report["prediction_settlements"],
    )
    return report


def settle_coupons(store: Store, paths: AppPaths, *, now: datetime) -> dict[str, int]:
    """Settle every coupon whose legs are decided (a correction of a result re-settles it).

    Settling is idempotent: an unchanged outcome reuses the stored settlement.
    """
    profiles = load_profiles(paths.config)
    counts: dict[str, int] = {}
    for coupon in store.rows("SELECT id FROM coupons"):
        status = settle_coupon(store, coupon["id"], profiles, now=now)["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


@dataclass
class PipelineRun:
    report: CouponReport
    candidates: list[Candidate]
    analysis_id: str
    stages: list[dict]
    coupons: BuilderReport | None = None
    coupon_ids: dict[str, str] = field(default_factory=dict)
    day: str = ""
    # TASK 010: per profile what saving did (official, duplicate, price_update, revision)
    coupon_saves: dict[str, dict] = field(default_factory=dict)
    offer: dict = field(default_factory=dict)  # newest STS document for the day and its age
    refresh: list[dict] = field(default_factory=list)  # what the STS download attempt did


@dataclass
class RangeRun:
    """One analysis of several days: shared data steps, then separate coupons for each day."""

    days: list[str]
    runs: dict[str, PipelineRun]
    stages: list[dict]  # shared steps (offer, results, statistics, matching, models)
    refresh: list[dict]
    run_at: str


STAGES = (
    "Oferta STS",
    "Dane sportowe",
    "Statystyki",
    "Dopasowanie",
    "Cechy przedmeczowe",
    "Modele",
    "Kalibracja",
    "Ranking selekcji",
    "Optymalizacja kuponów",
    "Prawdopodobieństwa kuponów",
    "Wypłaty",
    "Kontrola ryzyka",
    "Zapis",
    "Prezentacja",
)
SHARED_STAGES = 7  # stages 0-6 run once for all analysed days, 7-13 for every day
STS_KINDS = {
    "today": "oferta na dziś",
    "tomorrow": "oferta na jutro",
    "week": "oferta na kolejne dni",
}
RANGES = {"Dziś": 1, "Jutro": 1, "3 dni": 3, "7 dni": 7}


def analysis_days(choice: str, today: date) -> list[str]:
    """ISO days of a range choice (Warsaw calendar): today, tomorrow, 3 or 7 days from today."""
    first = today + timedelta(days=1) if choice == "Jutro" else today
    return [(first + timedelta(days=i)).isoformat() for i in range(RANGES[choice])]


def offer_status(
    store: Store,
    settings: AppSettings,
    day: str,
    *,
    now: datetime,
    data_kinds: tuple[str, ...] = ("prospective", "archive"),
) -> dict:
    """Newest non-empty STS document covering the day and its age against the price limit."""
    rows = offer_table(store, day=day, data_kinds=data_kinds)
    docs = [
        i
        for i in imports(store)
        if i["data_kind"] in data_kinds
        and day in json.loads(i["summary_json"]).get("offer_dates", [])
    ]
    non_empty = [i for i in docs if i["rows_total"] > 0]
    newest = max((i["generated_at"] or "" for i in non_empty), default=None) or None
    age = (
        round((now - datetime.fromisoformat(newest)).total_seconds() / 3600, 1) if newest else None
    )
    limit = round(settings.max_quote_age_minutes / 60, 1)
    return {
        "day": day,
        "events": len({r["event_id"] for r in rows}),
        "markets": len(rows),
        "review": sum(r["status"] != "ok" for r in rows),
        "documents": len(docs),
        "empty_documents": len(docs) - len(non_empty),
        "newest_document": newest,
        "age_hours": age,
        "limit_hours": limit,
        "fresh": age is not None and age <= limit,
    }


def sts_kinds_for(days: Sequence[str], today: date) -> list[str]:
    kinds: list[str] = []
    for day in days:
        offset = (date.fromisoformat(day) - today).days
        kind = "today" if offset <= 0 else "tomorrow" if offset == 1 else "week"
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def refresh_offers(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    days: Sequence[str],
    *,
    now: datetime,
    fetch: Callable[..., ImportOutcome] | None = None,
) -> list[dict]:
    """Download the official STS PDFs needed for the days and say exactly what happened.

    Statuses: skipped (not executed), blocked (offline, limit, refusal), empty (0 rows),
    duplicate (the same document as before: its prices are NOT confirmed again), no_day_events
    (a new document without the analysed days) and new. An empty or day-less document makes
    the other official variants be checked too. Only the document's own time makes a price
    fresh — a download time is never assigned to an old price.
    """
    today = now.astimezone(WARSAW).date()
    kinds = sts_kinds_for(days, today)
    if settings.offline_mode or not settings.sts_download_enabled:
        reason = (
            "tryb offline jest włączony"
            if settings.offline_mode
            else "pobieranie oficjalnych PDF STS jest wyłączone w Ustawieniach"
        )
        return [
            {
                "kind": kind,
                "label": STS_KINDS[kind],
                "status": "skipped",
                "message": f"Nie pobrano — {reason}",
                "days": {},
            }
            for kind in kinds
        ]
    attempts, queue, tried = [], list(kinds), set()
    while queue:
        kind = queue.pop(0)
        if kind in tried:
            continue
        tried.add(kind)
        attempt = _fetch_attempt(store, paths, settings, kind, days, now=now, fetch=fetch)
        attempts.append(attempt)
        if attempt["status"] in {"empty", "no_day_events"}:
            queue += [k for k in STS_KINDS if k not in tried and k not in queue]
    return attempts


def sts_download(
    store: Store, paths: AppPaths, settings: AppSettings, kind: str, *, now: datetime
) -> dict:
    """One official STS file on the user's click, with the same explicit status."""
    if settings.offline_mode or not settings.sts_download_enabled:
        return {
            "kind": kind,
            "label": STS_KINDS[kind],
            "status": "skipped",
            "message": "Nie pobrano — tryb offline lub pobieranie wyłączone w Ustawieniach",
            "days": {},
        }
    return _fetch_attempt(store, paths, settings, kind, [], now=now, fetch=None)


def _fetch_attempt(store, paths, settings, kind, days, *, now, fetch) -> dict:
    label = STS_KINDS[kind]
    try:
        outcome = (fetch or fetch_sts_offer)(store, paths, settings, kind, now=now)
    except (SourceBlocked, ParseError) as error:
        return {
            "kind": kind,
            "label": label,
            "status": "blocked",
            "message": f"Nie pobrano: {error}",
            "days": {},
        }
    summary = outcome.summary
    generated = summary.get("generated_at")
    when = (
        datetime.fromisoformat(generated).astimezone(WARSAW).strftime("%d.%m %H:%M")
        if generated
        else "bez daty"
    )
    covered = {day: day in summary.get("offer_dates", []) for day in days}
    if not summary.get("rows"):
        status = "empty"
        message = f"STS udostępnia pusty plik (dokument z {when}, 0 wydarzeń)"
    elif outcome.duplicate:
        status = "duplicate"
        message = (
            f"Pobrano ten sam dokument co wcześniej (z {when}) — kursy nie zostały potwierdzone "
            "na nowo"
        )
    elif days and not any(covered.values()):
        status = "no_day_events"
        message = f"Nowy dokument z {when} nie zawiera wydarzeń analizowanych dni"
    else:
        status = "new"
        message = f"Pobrano nowy dokument z {when}: {summary.get('events', 0)} wydarzeń"
    return {
        "kind": kind,
        "label": label,
        "status": status,
        "message": message,
        "generated_at": generated,
        "events": summary.get("events"),
        "days": covered,
    }


def run_daily_analysis(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    day: str,
    *,
    now: datetime,
    refresh_offer: bool = False,
    refresh_results: bool = True,
    providers: Sequence[SportsResultsProvider] | None = None,
    data_kinds: tuple[str, ...] = ("prospective", "archive"),
    progress: Callable[[int, str, str], None] | None = None,
    save_coupons: bool = True,
    stats_provider: PitchApiProvider | None = None,
    fetch: Callable[..., ImportOutcome] | None = None,
) -> PipelineRun:
    """One button for one day (see ``run_analysis``)."""
    return run_analysis(
        store,
        paths,
        settings,
        [day],
        now=now,
        refresh_offer=refresh_offer,
        refresh_results=refresh_results,
        providers=providers,
        data_kinds=data_kinds,
        progress=progress,
        save_coupons=save_coupons,
        stats_provider=stats_provider,
        fetch=fetch,
    ).runs[day]


def run_analysis(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    days: Sequence[str],
    *,
    now: datetime,
    refresh_offer: bool = False,
    refresh_results: bool = True,
    providers: Sequence[SportsResultsProvider] | None = None,
    data_kinds: tuple[str, ...] = ("prospective", "archive"),
    progress: Callable[[int, str, str], None] | None = None,
    save_coupons: bool = True,
    stats_provider: PitchApiProvider | None = None,
    fetch: Callable[..., ImportOutcome] | None = None,
    forecasts_only: bool = False,
    train_missing: bool = True,
) -> RangeRun:
    """Offer → results → statistics → matching → features → forecasts → calibration (once),
    then for EVERY day separately: ranking → coupon builder → probabilities → payouts → risk →
    save → presentation. Coupons never mix days.

    Network steps run only when the settings allow them (offline mode, opt-ins, keys); paid
    sources are never called; models are trained only when no version exists yet. Forecasts
    for later days use only data available now. Missing data stops nothing: each stage
    reports what it could do.

    TASK 012: ``forecasts_only`` stops after the forecasts (no pre-match feature snapshots,
    no ranking, no coupon, no saved analysis — the automatic forecasts of the worker), and
    ``train_missing=False`` never trains a missing model.
    """
    days = list(days)
    shared: list[dict] = []

    def stage(target: list[dict], index: int, status: str, detail: str, **numbers) -> None:
        target.append({"stage": STAGES[index], "status": status, "detail": detail, **numbers})
        if progress:
            progress(index, STAGES[index], detail)

    # 1. STS offer ------------------------------------------------------------------
    refresh = (
        refresh_offers(store, paths, settings, days, now=now, fetch=fetch) if refresh_offer else []
    )
    offers = {
        day: offer_status(store, settings, day, now=now, data_kinds=data_kinds) for day in days
    }
    first = offers[days[0]]

    def advice(info: dict) -> str:
        if info["age_hours"] is None:
            return "brak dokumentu STS"
        if not info["fresh"]:
            return (
                f"najnowszy dokument STS ma {info['age_hours']} h (limit {info['limit_hours']} h) "
                "— kursy wymagają odświeżenia"
            )
        return "oferta aktualna"

    stage(
        shared,
        0,
        "ok" if any(o["markets"] for o in offers.values()) else "brak danych",
        "; ".join(
            f"{d}: {o['events']} wydarzeń, {o['markets']} rynków, {advice(o)}"
            for d, o in offers.items()
        )
        + ("; pobieranie: " + " | ".join(a["message"] for a in refresh) if refresh else ""),
        events=sum(o["events"] for o in offers.values()),
        markets=sum(o["markets"] for o in offers.values()),
        review=sum(o["review"] for o in offers.values()),
        documents=first["documents"],
        empty_documents=first["empty_documents"],
        newest_document=first["newest_document"],
        document_age_hours=first["age_hours"],
        offers=offers,
        refresh=refresh,
    )

    # 2. Results ----------------------------------------------------------------------
    update = None
    if refresh_results:
        update = update_results_data(store, paths, settings, now=now, providers=providers)
    freshness = league_freshness(store, RESEARCH_COMPETITIONS, now=now)
    fresh_leagues = [f for f in freshness if f["fresh"]]
    stage(
        shared,
        1,
        "ok" if fresh_leagues else "dane nieaktualne",
        (
            f"nowe wyniki: {update['inserted']}, konflikty: {len(update['conflicts'])}, "
            f"nierozpoznane nazwy: {len(update['unmatched'])}, błędy: {len(update['errors'])}, "
            f"kupony rozliczone: {update.get('settlements', {}).get('settled', 0)}; "
            if update
            else "aktualizacja pominięta; "
        )
        + f"aktualne ligi: {len(fresh_leagues)}/{len(freshness)} (próg {MAX_DATA_GAP_DAYS} dni)",
        update=_short_update(update),
        freshness=freshness,
    )
    marks = ",".join("?" for _ in days)
    upcoming = store.rows(
        f"SELECT * FROM events WHERE bookmaker_id='sts' AND offer_date IN ({marks}) "
        "AND start_at IS NOT NULL AND start_at > ?",
        (*days, now.isoformat()),
    )
    covered = [e for e in upcoming if league_code(e["league"], e["sport"])]

    # 3. Detailed statistics -------------------------------------------------------------
    stats = (
        collect_offer_stats(
            store, paths, settings, now=now, events=covered, provider=stats_provider
        )
        if refresh_results
        else {"status": "pominięte"}
    )
    fetched = sum(v.get("fetched", 0) for v in stats.values() if isinstance(v, dict))
    stage(
        shared,
        2,
        "ok" if fetched or stats.get("status") is None else "pominięte",
        f"pobrane statystyki meczów: {fetched}"
        + (f" ({stats['status']})" if isinstance(stats.get("status"), str) else ""),
        statistics=stats,
        fetched=fetched,
    )

    # 4. Matching ---------------------------------------------------------------------
    names, aliases = normalizer(paths), alias_map(store, team_aliases(paths))
    identities = team_identities(paths)
    matched, unmatched, known_by_code = 0, [], {}
    for event in covered:
        code = league_code(event["league"], event["sport"])
        if code not in known_by_code:
            known_by_code[code] = known_names(
                effective_results(
                    store,
                    sport=event["sport"],
                    until=now,
                    competitions=(code,),
                    identities=identities,
                ),
                event["sport"],
                names,
            )
        sides = [
            match_participant(event[s] or "", event["sport"], known_by_code[code], names, aliases)
            for s in ("home", "away")
        ]
        if all(f.status in {"exact", "alias"} for f in sides):
            matched += 1
        else:
            unmatched += [
                {"sts": event[s], "status": f.status, "suggestion": f.target, "league": code}
                for s, f in zip(("home", "away"), sides, strict=True)
                if f.status not in {"exact", "alias"}
            ]
    stage(
        shared,
        3,
        "ok" if covered else "brak wydarzeń w ligach z danymi",
        f"wydarzenia w ligach z danymi: {len(covered)}, dopasowane: {matched}, "
        f"nazwy do potwierdzenia: {len(unmatched)}",
        considered=len(covered),
        matched=matched,
        unmatched=unmatched[:50],
    )

    # 5. Pre-match features (as of now) ------------------------------------------------
    snapshots = 0
    if forecasts_only:
        stage(shared, 4, "pominięte", "tryb prognoz automatycznych — bez migawek cech")
    else:
        for event in covered:
            if event_features(store, paths, event, decision_time=now) is not None:
                snapshots += 1
        stage(shared, 4, "ok", f"migawki cech przedmeczowych: {snapshots}", snapshots=snapshots)

    # 6. Models (one Elo and one Dixon–Coles version per scope; trained only when missing,
    # under protocol v2: the first version of a scope is served, later ones are challengers)
    trained, errors = False, []
    for name, scope in MODEL_SCOPES.items():
        if not scope_has_data(store, scope):
            continue  # no history for this scope yet: nothing to train
        had_elo = scope_version(store, scope) is not None
        missing = not had_elo or scope_version(store, scope, GOALS_FAMILY) is None
        if missing and not train_missing:
            errors.append(f"{SCOPE_LABELS[name]}: brak modelu (trening pominięty)")
        elif missing:
            try:
                run_protocol_v2(store, paths, now=now, scope=name)
                trained = trained or not had_elo
            except ValueError as error:
                # Too little history for the four windows of protocol v2: a v1 model keeps the
                # application working and its statuses stay suspended (experimental).
                errors.append(f"{SCOPE_LABELS[name]}: protokół v2 niewykonalny ({error})")
                try:
                    if scope_version(store, scope) is None:
                        run_research_experiment(store, paths, now=now, competitions=scope)
                    if scope_version(store, scope, GOALS_FAMILY) is None:
                        run_goals_experiment(store, paths, now=now, competitions=scope)
                    trained = trained or not had_elo
                except ValueError as second:
                    errors.append(f"{SCOPE_LABELS[name]}: {second}")
    served = active_versions(store)
    forecasts = goals = None
    if any(pair["elo"] for pair in served.values()):
        forecasts = generate_predictions(store, paths, now=now, events=upcoming)
    if any(pair["goals"] for pair in served.values()):
        goals = generate_goals_predictions(store, paths, now=now, events=upcoming)
    shadow_advanced = None
    if forecasts:
        from sports_edge import advanced_lab

        try:  # admitted extended challengers: frozen, archived, never served
            shadow_advanced = advanced_lab.generate(store, paths, now=now, events=upcoming)
        except (ValueError, KeyError) as error:
            errors.append(f"model rozszerzony (tryb cienia): {error}")
    core = served["core"]["elo"] or next((p["elo"] for p in served.values() if p["elo"]), None)
    core_goals = served["core"]["goals"] or next(
        (p["goals"] for p in served.values() if p["goals"]), None
    )
    stage(
        shared,
        5,
        "ok" if forecasts else "brak modelu",
        (
            f"Elo: nowe prognozy {forecasts['saved']}, aktualne {forecasts['kept']}, pominięte "
            f"{len(forecasts['skipped'])}"
            + (f"; Dixon–Coles: nowe {goals['saved']}, aktualne {goals['kept']}" if goals else "")
            if forecasts
            else "brak modelu: " + ("; ".join(errors) or "nie zarejestrowano")
        ),
        model=core["version"] if core else None,
        model_status=core["status"] if core else None,
        goals_model=core_goals["version"] if core_goals else None,
        scopes={
            name: {k: (row["version"] if row else None) for k, row in pair.items()}
            for name, pair in served.items()
        },
        trained_now=trained,
        forecasts_saved=forecasts["saved"] if forecasts else 0,
        forecasts_kept=forecasts["kept"] if forecasts else 0,
        goals_saved=goals["saved"] if goals else 0,
        advanced_shadow_saved=(shadow_advanced or {}).get("saved", 0),
        skipped=(forecasts or {}).get("skipped", [])[:50],
        errors=errors,
    )

    if forecasts_only:  # TASK 012: the automatic forecasts end here (no coupon, no analysis)
        return RangeRun(days, {}, shared, refresh, now.astimezone(UTC).isoformat())

    # 7. Calibration and predictive validation -----------------------------------------
    quality = model_quality(store)
    by_scope = model_quality_by_scope(store)
    stage(
        shared,
        6,
        "ok" if quality else "brak oceny",
        "; ".join(
            f"{SCOPE_LABELS[name]}: zwalidowane predykcyjnie "
            + (
                ", ".join(k for k, v in fams.items() if v["status"] == "validated_predictive")
                or "—"
            )
            for name, fams in by_scope.items()
            if fams
        )
        or "brak oceny",
        families=quality,
        scopes=by_scope,
    )

    rules = coupon_rules(paths, settings)  # TASK 014: + the optional profiles switched on
    cost = load_profiles(paths.config)["sts"].cost
    runs = {}
    for day in days:
        runs[day] = _analyse_one_day(
            store,
            paths,
            settings,
            day,
            now=now,
            data_kinds=data_kinds,
            shared=shared,
            stage=stage,
            fresh_leagues=bool(fresh_leagues),
            rules=rules,
            cost=cost,
            save_coupons=save_coupons,
        )
        runs[day].offer = offers[day]
        runs[day].refresh = refresh
    return RangeRun(days, runs, shared, refresh, now.astimezone(UTC).isoformat())


def run_forecasts(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    *,
    now: datetime,
    days: Sequence[str] | None = None,
) -> dict:
    """TASK 012 §3.1: the analysis stages from the STS offer to the forecasts for today and
    tomorrow — no download, no results update, no statistics request, no model training, no
    coupon and no saved analysis. Forecasts that are still valid (same model, same data) are
    kept, not duplicated (the existing idempotency of the forecast functions)."""
    today = now.astimezone(WARSAW).date()
    days = list(days or [today.isoformat(), (today + timedelta(days=1)).isoformat()])
    run = run_analysis(
        store,
        paths,
        settings,
        days,
        now=now,
        refresh_offer=False,
        refresh_results=False,
        save_coupons=False,
        forecasts_only=True,
        train_missing=False,
    )
    models = next(s for s in run.stages if s["stage"] == STAGES[5])
    coverage = forecast_coverage(store, now=now, days=days)
    return {
        "days": days,
        "events": coverage["events"],
        "with_forecast": coverage["with_forecast"],
        "without_forecast": coverage["without_forecast"][:30],
        "saved": int(models.get("forecasts_saved", 0)) + int(models.get("goals_saved", 0)),
        "kept": int(models.get("forecasts_kept", 0)),
        "errors": list(models.get("errors") or []),
        "skipped": len(models.get("skipped") or []),
        "detail": models["detail"],
    }


def forecast_coverage(store: Store, *, now: datetime, days: Sequence[str]) -> dict:
    """Upcoming STS matches of the supported leagues on ``days`` and whether a served model
    has a prospective forecast for them (TASK 012: every match should have one)."""
    marks = ",".join("?" for _ in days)
    events = [
        e
        for e in store.rows(
            f"SELECT * FROM events WHERE bookmaker_id='sts' AND offer_date IN ({marks}) "
            "AND start_at IS NOT NULL AND start_at > ?",
            (*days, now.isoformat()),
        )
        if league_code(e["league"], e["sport"]) in RESEARCH_COMPETITIONS
    ]
    served = active_version_ids(store)
    covered = set()
    for chunk in range(0, len(events), 400):
        part = [e["id"] for e in events[chunk : chunk + 400]]
        if not part or not served:
            continue
        found = store.rows(
            f"""SELECT DISTINCT event_id FROM predictions WHERE kind='prospective'
            AND event_id IN ({",".join("?" for _ in part)})
            AND model_version_id IN ({",".join("?" for _ in served)})""",
            (*part, *sorted(served)),
        )
        covered |= {r["event_id"] for r in found}
    missing = [e for e in events if e["id"] not in covered]
    return {
        "events": len(events),
        "with_forecast": len(events) - len(missing),
        "without_forecast": [f"{e['home']} - {e['away']} ({e['league']})" for e in missing],
    }


def _analyse_one_day(
    store,
    paths,
    settings,
    day,
    *,
    now,
    data_kinds,
    shared,
    stage,
    fresh_leagues,
    rules,
    cost,
    save_coupons,
) -> PipelineRun:
    stages = list(shared)
    # 8. Ranking of selections ---------------------------------------------------------------
    report, candidates = analyse_day(store, paths, settings, day, now=now, data_kinds=data_kinds)
    report = replace(report, diagnosis=diagnose(report.counts, sports_data_fresh=fresh_leagues))
    stage(
        stages,
        7,
        "ok",
        f"{day}: selekcje {report.counts['analysed']}, z prognozą "
        f"{report.counts['with_forecast']}, z P ≥ progu i świeżym kursem {len(report.probable)}",
        **{k: report.counts[k] for k in ("analysed", "with_forecast", "ev_positive")},
        probable=len(report.probable),
    )

    # 9-12. Coupon builder --------------------------------------------------------------
    built = build_coupons(
        candidates,
        rules,
        cost=cost,
        on_date=date.fromisoformat(day),
        max_profiles_per_selection=settings.max_profiles_per_selection,
    )
    made = {k: v for k, v in built.proposals.items() if v is not None}
    stage(
        stages,
        8,
        "ok" if made else "brak kuponu",
        f"{day}: pula selekcji {len(built.pool)}; kupony: {', '.join(made) or 'żaden profil'}",
        pool=len(built.pool),
    )
    stage(
        stages,
        9,
        "ok",
        "; ".join(f"{k}: {v.p_conservative:.0%} (ostrożnie)" for k, v in made.items()) or "—",
        probabilities={k: [v.p_calibrated, v.p_conservative] for k, v in made.items()},
    )
    stage(
        stages,
        10,
        "ok",
        "; ".join(f"{k}: wypłata {v.payout} zł, zysk {v.net_profit} zł" for k, v in made.items())
        or "—",
    )
    stage(
        stages,
        11,
        "ok",
        "; ".join(f"{k}: {why}" for k, why in built.refusals.items()) or "wszystkie profile",
        excluded=built.excluded,
        refusals=built.refusals,
    )

    # 13. Save -----------------------------------------------------------------------
    pipeline = {"stages": stages, "run_at": now.astimezone(UTC).isoformat()}
    analysis_id = save_analysis(store, report, candidates, policy(paths), pipeline=pipeline)
    saves = (
        save_proposals(store, paths, built, analysis_id=analysis_id, now=now, day=day)
        if save_coupons
        else {}
    )
    coupon_ids = {
        name: result["coupon_id"] if "coupon_id" in result else f"nie zapisano: {result['error']}"
        for name, result in saves.items()
    }
    relations = Counter(result.get("relation", "błąd") for result in saves.values())
    stage(
        stages,
        12,
        "ok",
        f"analiza {analysis_id[:12]}…, propozycje kuponów: {len(coupon_ids)}"
        + (f" ({', '.join(f'{k}: {v}' for k, v in sorted(relations.items()))})" if saves else ""),
        analysis_id=analysis_id,
        coupons=coupon_ids,
        relations=dict(relations),
    )

    # 14. Presentation ---------------------------------------------------------------------
    stage(
        stages,
        13,
        "ok" if made else "brak kuponu",
        "kupony gotowe"
        if made
        else "BRAK KUPONU SPEŁNIAJĄCEGO WARUNKI PROFILI — " + report.headline,
    )
    return PipelineRun(
        report, candidates, analysis_id, stages, built, coupon_ids, day=day, coupon_saves=saves
    )


def model_quality(store: Store, scope: tuple[str, ...] = CORE_COMPETITIONS) -> dict[str, dict]:
    """Predictive status per market family of one scope: Elo serves 1X2 / double chance,
    Dixon–Coles the goals markets."""
    goals = scope_version(store, scope, GOALS_FAMILY)
    if goals is None:
        return {}
    validation = json.loads(goals["validation_json"])
    out = {}
    for family, info in (validation.get("elo_predictive") or {}).items():
        out[family] = {**info, "model": ELO_FAMILY}
    for family, info in validation.get("predictive", {}).items():
        if family not in out:
            out[family] = {**info, "model": GOALS_FAMILY}
    return out


def model_quality_by_scope(store: Store) -> dict[str, dict[str, dict]]:
    return {name: model_quality(store, scope) for name, scope in MODEL_SCOPES.items()}


def event_lineups(
    store: Store, paths: AppPaths, settings: AppSettings, *, event_id: str, now: datetime
) -> dict:
    """On-demand PitchAPI lineups of one upcoming STS event (two requests; free plan)."""
    context = event_context(store, paths, event_id=event_id, now=now)
    teams = context["teams"]
    if not teams or not all(teams[s]["canonical"] for s in ("home", "away")):
        return {"status": "brak zweryfikowanego dopasowania drużyn"}
    if settings.offline_mode or not settings.pitchapi_enabled:
        return {"status": "tryb offline lub PitchAPI wyłączone w Ustawieniach"}
    provider = PitchApiProvider(local_secret(PITCH_ENV, paths.root))
    if not provider.key:
        return {"status": f"brak klucza {PITCH_ENV}"}
    return fetch_lineups(
        store,
        provider,
        competition=context["competition"],
        home=teams["home"]["canonical"],
        away=teams["away"]["canonical"],
        start_at=datetime.fromisoformat(context["event"]["start_at"]),
        now=now,
        normalizer=normalizer(paths),
        aliases=alias_map(store, team_aliases(paths)),
        identities=team_identities(paths),
    )


def _refresh_offer(
    store: Store, paths: AppPaths, settings: AppSettings, day: str, *, now: datetime
) -> str:
    """Official STS PDF for the analysed day, only when the user enabled downloads."""
    if settings.offline_mode or not settings.sts_download_enabled:
        return "pominięte (tryb offline lub pobieranie STS wyłączone w Ustawieniach)"
    today = now.astimezone(WARSAW).date()
    kind = (
        "today"
        if day == today.isoformat()
        else "tomorrow"
        if day == (today + timedelta(days=1)).isoformat()
        else "week"
    )
    try:
        outcome = fetch_sts_offer(store, paths, settings, kind, now=now)
    except (SourceBlocked, ParseError) as error:
        return f"nie pobrano: {error}"
    return "bez zmian (duplikat)" if outcome.duplicate else f"pobrano plik „{kind}”"


def _short_update(update: dict | None) -> dict | None:
    if update is None:
        return None
    return {
        "inserted": update["inserted"],
        "duplicates": update["duplicates"],
        "confirmations": update["confirmations"],
        "corrections": update["corrections"][:20],
        "conflicts": update["conflicts"][:20],
        "unmatched": update["unmatched"][:20],
        "incomplete": update["incomplete"][:20],
        "errors": update["errors"],
        "requests": update["requests"],
        "providers": update["providers"],
        "sts_results": update["sts_results"]["recorded"],
        "sts_statuses": update["sts_statuses"]["recorded"],
    }


def league_code(sts_league: str, sport: str | None = None) -> str | None:
    """Competition code of an STS league header covered by independent results."""
    found = for_sts_league(sts_league, sport)
    return found.code if found else None


def day_candidates_at(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    day: str,
    *,
    now: datetime,
    data_kinds: tuple[str, ...] = ("prospective", "archive"),
    as_of: datetime | None = None,
    versions: set[str] | None = None,
    goals: dict[str, dict] | None = None,
) -> tuple[list[Candidate], dict]:
    """(candidates, forecasts) of an offer day — the engine of ``analyse_day``.

    TASK 013 §5 (measurements only): ``versions`` — the model versions whose forecasts are
    used (default: the served ones); ``goals`` — scope → the Dixon–Coles version whose
    report decides the 1X2 model and the Elo statuses (default: the served one). A shadow
    replay passes the challengers here; nothing is served or saved by it."""
    profiles = load_profiles(paths.config)
    rules = policy(paths)
    predictions = with_predictive_status(
        store,
        latest_predictions(
            store,
            as_of=now,
            prefer=prediction_preference(store, goals=goals),
            versions=active_version_ids(store) if versions is None else versions,
            offer_dates=[day],
        ),
        benchmarks=elo_benchmarks(store, goals=goals) if goals is not None else None,
    )
    candidates = evaluate(
        offer_table(store, day=day, data_kinds=data_kinds, as_of=as_of),
        predictions=predictions,
        profile=profiles["sts"],
        market_rules=load_rules(paths.config),
        policy=rules,
        now=now,
        max_age=timedelta(minutes=settings.max_quote_age_minutes),
        stake=Decimal(settings.nominal_stake),
    )
    from sports_edge.international import gate_candidates

    return gate_candidates(store, candidates), predictions  # TASK 009: cups after validation


def match_review(
    store: Store, *, event_id: str, candidates: list[Candidate], now: datetime
) -> dict:
    """Pre-match picture versus what happened (TASK 006). Never changes a forecast.

    The original forecast is the newest one made before kick-off by any version (the version
    served then may no longer be active); the result, the outcome of every forecast selection
    and the information the model did not have are listed separately.
    """
    event = store.get("events", event_id)
    start = datetime.fromisoformat(event["start_at"]) if event and event["start_at"] else None
    started = bool(start and now >= start)
    out: dict = {"started": started, "start_at": event["start_at"] if event else None}
    if not started:
        return out
    forecasts = with_predictive_status(
        store,
        latest_predictions(
            store,
            as_of=start,
            prefer=prediction_preference(store),
            offer_dates=[event["offer_date"]] if event.get("offer_date") else None,
        ),
    )
    forecast = forecasts.get((event_id, "1x2", "FT", None))
    result = latest_result(store, event_id)
    out["forecast"] = forecast
    out["result"] = result
    out["finished"] = bool(result and result["status"] == "completed")
    if forecast and out["finished"]:
        probs = forecast["probabilities"]
        favourite = max(probs, key=probs.get)
        h, a = result["home_score"], result["away_score"]
        actual = "home" if h > a else "away" if h < a else "draw"
        out["favourite"] = favourite
        out["actual"] = actual
        out["favourite_hit"] = favourite == actual
    selections = []
    if out["finished"]:
        odds = {(c.market_key, c.period, c.line, c.selection): c.odds for c in candidates}
        for (eid, market_key, period, line), row in forecasts.items():
            if eid != event_id:
                continue
            market = {"market_key": market_key, "period": period, "overtime": "false", "line": line}
            for selection, p in row["probabilities"].items():
                outcome = resolve(market, selection, result)
                selections.append(
                    {
                        "market": market_label(market_key, period, line),
                        "market_key": market_key,
                        "line": line,
                        "selection": selection,
                        "p": p,
                        "odds": odds.get((market_key, period, line, selection)),
                        "outcome": outcome.value if outcome else None,
                    }
                )
    out["selections"] = selections
    out["unknown_before"] = [
        "składy (publikowane około godziny przed meczem; model ich nie używa)",
        "kontuzje i zawieszenia (brak bezpłatnego, zweryfikowanego źródła)",
        "przebieg i statystyki tego meczu (znane dopiero po jego zakończeniu)",
    ]
    return out


def event_context(
    store: Store, paths: AppPaths, *, event_id: str, now: datetime, last: int = 10
) -> dict:
    """Everything the event view shows: verified team mapping, recent results, forecast.

    Teams are linked only through exact normalised keys or reviewed aliases (never by a
    fragment of the name); recent results come from the hierarchy-resolved history.
    """
    event = store.get("events", event_id)
    if event is None:
        raise ValueError("Nieznane wydarzenie")
    prediction = with_predictive_status(
        store,
        latest_predictions(
            store,
            as_of=now,
            prefer=prediction_preference(store),
            versions=active_version_ids(store),
        ),
    ).get((event_id, "1x2", "FT", None))
    code = league_code(event["league"], event["sport"])
    sport = event["sport"]
    out: dict = {
        "event": event,
        "competition": code,
        "prediction": prediction,
        "teams": {},
        "freshness": None,
    }
    if code is None:
        return out
    rows = effective_results(
        store, sport=sport, until=now, competitions=(code,), identities=team_identities(paths)
    )
    names = normalizer(paths)
    aliases = alias_map(store, team_aliases(paths))
    known = known_names(rows, sport, names)
    cutoff = datetime.fromisoformat(prediction["created_at"]) if prediction else now
    for side in ("home", "away"):
        found = match_participant(event[side] or "", sport, known, names, aliases)
        verified = found.status in {"exact", "alias"}
        entry = {
            "sts": event[side],
            "status": found.status,
            "canonical": found.target if verified else None,
            "suggestion": None if verified else found.target,
            "games": 0,
            "matches": [],
        }
        if verified:
            team = found.target
            mine = [r for r in rows if team in (r["home"], r["away"])]
            entry["games"] = len(mine)
            for r in reversed(mine[-last:]):
                at_home = r["home"] == team
                scored, conceded = (
                    (r["home_score"], r["away_score"])
                    if at_home
                    else (r["away_score"], r["home_score"])
                )
                entry["matches"].append(
                    {
                        "date": r["match_date"],
                        "venue": "dom" if at_home else "wyjazd",
                        "opponent": r["away"] if at_home else r["home"],
                        "score": f"{r['home_score']}:{r['away_score']}",
                        # unambiguous: the match as played (home first) and the team's view
                        "match": f"{r['home']} {r['home_score']}:{r['away_score']} {r['away']}",
                        "team_score": f"{scored}:{conceded}",
                        "result": "W" if scored > conceded else "P" if scored < conceded else "R",
                        "source": r["provider_resolved"],
                        "conflict": r["conflict"],
                        "known_before_forecast": datetime.fromisoformat(r["available_at"])
                        <= cutoff,
                    }
                )
        out["teams"][side] = entry
    out["freshness"] = league_freshness(store, (code,), now=now)[0]
    return out


# ---------------------------------------------------------------- TASK 005: goals model


def probability_policy(paths: AppPaths) -> ProbabilityPolicy:
    return load_probability_policy(paths.config / "probability_policy.toml")


def coupon_rules(paths: AppPaths, settings: AppSettings) -> ProbabilityPolicy:
    """TASK 014: the profiles the builder makes coupons for — the profiles of the policy
    plus the optional ones the user switched on in Ustawienia (built after them)."""
    return probability_policy(paths).with_profiles(getattr(settings, "enabled_profiles", ()))


def elo_walk_forward_forecasts(
    store: Store,
    *,
    now: datetime,
    identities: TeamIdentities | None = None,
    competitions: tuple[str, ...] = CORE_COMPETITIONS,
    version: dict | None = None,
) -> dict[str, dict]:
    """As-of Elo forecasts of every historical match (benchmark for the goals model)."""
    version = version or scope_version(store, competitions)
    if version is None:
        return {}
    validation = json.loads(version["validation_json"])
    params = validation["hyperparameters"]
    matches = _scoped(
        history(
            store,
            sport="football",
            until=now,
            competitions=competitions,
            identities=identities,
        )
    )
    if validation.get("protocol") == "v3":
        # TASK 012: the draw margin of a v3 version was fitted on its calibration window
        model = Elo(
            k=params["k"],
            home_advantage=params["home_advantage"],
            draw_margin=params["draw_margin"],
        )
        items = walk_forward(matches, model, three_way=True)
    else:
        items, _margin = _forecasts(
            matches,
            k=params["k"],
            home_advantage=params["home_advantage"],
            # v2: the draw margin of the version (fitted before its selection window)
            margin_until=params.get("draw_margin_until", RESEARCH_SPLIT.holdout_from),
        )
    return {item["match"].result_id: item["forecast"] for item in items}


def run_goals_experiment(
    store: Store,
    paths: AppPaths,
    *,
    now: datetime,
    competitions: tuple[str, ...] = CORE_COMPETITIONS,
) -> dict:
    elo = scope_version(store, competitions)
    return run_probability_experiment(
        store,
        competitions=competitions,
        split=RESEARCH_SPLIT,
        now=now,
        policy=policy(paths),
        probability_policy=probability_policy(paths),
        elo_forecasts=elo_walk_forward_forecasts(
            store,
            now=now,
            identities=team_identities(paths),
            competitions=competitions,
            version=elo,
        ),
        identities=team_identities(paths),
        elo_version_id=elo["version_id"] if elo else None,
    )


def elo_benchmarks(store: Store, *, goals: dict[str, dict] | None = None) -> dict[str, dict]:
    """Elo version id → report of the served goals experiment that judged it (per scope).

    Goals versions registered before the Elo id was recorded used the newest Elo version of
    their scope that existed when the goals experiment ran. ``goals`` (TASK 013, shadow
    measurements only): scope name → the goals version to read instead of the served one.
    """
    out: dict[str, dict] = {}
    for name, scope in MODEL_SCOPES.items():
        row = scope_version(store, scope, GOALS_FAMILY) if goals is None else goals.get(name)
        if row is None:
            continue
        report = json.loads(row["validation_json"])
        version_id = report.get("elo_version_id")
        if version_id is None:
            version_id = next(
                (
                    found["version_id"]
                    for found in model_overview(store)
                    if found["family"] == ELO_FAMILY
                    and found["created_at"] <= row["created_at"]
                    and set(version_competitions(found)) <= set(scope)
                ),
                None,
            )
        if version_id:
            out[version_id] = report
    return out


def elo_validation(store: Store) -> tuple[str | None, dict, dict]:
    """(Elo version id, status per family, metrics per family) of the core scope."""
    elo = scope_version(store, CORE_COMPETITIONS)
    report = elo_benchmarks(store).get(elo["version_id"]) if elo else None
    if report is None:
        return None, {}, {}
    return elo["version_id"], report.get("elo_predictive") or {}, report.get("elo_families") or {}


def with_predictive_status(
    store: Store, predictions: dict[tuple, dict], *, benchmarks: dict[str, dict] | None = None
) -> dict[tuple, dict]:
    """Elo forecasts carry no status of their own: the goals experiment of their scope
    measured it for one Elo version, so only that version's forecasts receive it; a league
    that fails its own gate stays experimental. ``benchmarks`` (TASK 013, measurements):
    the Elo version → goals report map to use instead of the served one."""
    benchmarks = elo_benchmarks(store) if benchmarks is None else benchmarks

    def status(report: dict, family: str, competition: str | None) -> str:
        value = ((report.get("elo_predictive") or {}).get(family) or {}).get(
            "status", "experimental"
        )
        gate = league_status(report, competition, "elo", family)
        return "experimental" if gate and not gate["ok"] else value

    for row in predictions.values():
        if (row.get("validation") or {}).get("protocol") not in ("v2", "v3"):
            _suspend_status(row)  # TASK 007 §4.2: statuses chosen on a used holdout
            continue
        report = benchmarks.get(row["model_version_id"])
        if report is None or row["market_key"] != "1x2":
            continue
        if "predictive_status" in row["uncertainty"]:
            continue
        competition = (row.get("inputs") or {}).get("competition")
        row["uncertainty"] = {
            **row["uncertainty"],
            "family": "1x2",
            "predictive_status": status(report, "1x2", competition),
            "derived_status": {"double_chance": status(report, "double_chance", competition)},
            "league_gate": league_status(report, competition, "elo", "1x2"),
        }
        row["validation"] = {**row["validation"], "families": report.get("elo_families") or {}}
    return predictions


def _suspend_status(row: dict) -> None:
    """Read-time status of a protocol v1 forecast: experimental, with the reason shown.

    The stored forecast and the stored version status stay untouched (append-only); only
    the status used for decisions is suspended, explicitly and visibly."""
    uncertainty = row["uncertainty"]
    derived = {
        key: {**value, "predictive_status": "experimental"}
        for key, value in (uncertainty.get("derived") or {}).items()
    }
    row["uncertainty"] = {
        **uncertainty,
        "family": uncertainty.get("family")
        or (row["market_key"] if row["market_key"] in ("1x2", "double_chance") else None),
        "predictive_status": "experimental",
        "derived": derived,
        "derived_status": {"double_chance": "experimental"},
        "status_note": learning.HOLDOUT_NOTE,
        "stored_status": uncertainty.get("predictive_status"),
    }
    row["version_status"] = "experimental"


def prediction_preference(store: Store, *, goals: dict[str, dict] | None = None) -> dict:
    """Model serving 1X2 (and the double chance derived from it), per scope and league.

    The model that passed the predictive validation wins; when both or neither passed, the
    lower log loss of the deciding window decides (validation in v1, selection in v2). The
    holdout never decides: statuses of v1 versions (chosen on a used holdout) do not count.
    ``goals`` (TASK 013, measurements only): scope name → the goals version to read.
    """
    out: dict = {}
    for name, scope in MODEL_SCOPES.items():
        row = scope_version(store, scope, GOALS_FAMILY) if goals is None else goals.get(name)
        if row is None:
            continue
        report = json.loads(row["validation_json"])
        v2 = report.get("protocol") in ("v2", "v3")
        elo_ok = (
            v2
            and ((report.get("elo_predictive") or {}).get("1x2") or {}).get("status")
            == "validated_predictive"
        )
        dc_ok = (
            v2
            and ((report.get("predictive") or {}).get("1x2") or {}).get("status")
            == "validated_predictive"
        )
        by_loss = GOALS_FAMILY if "1x2" in report.get("markets_served", []) else ELO_FAMILY
        family = by_loss if elo_ok == dc_ok else ELO_FAMILY if elo_ok else GOALS_FAMILY
        out.update({("1x2", code): family for code in scope})
        if name == "core" or "1x2" not in out:
            out["1x2"] = family
    return out


def run_protocol_v2(
    store: Store,
    paths: AppPaths,
    *,
    now: datetime,
    scope: str,
    progress=None,
    force: bool = False,
) -> dict:
    """TASK 007 §4.2: Elo and Dixon–Coles of one scope under protocol v2, registered as
    challengers (shadow mode). The champion is changed only by the user.

    TASK 011 §5.3: no challenger is trained on the same data as a protocol v2 champion (no
    new match since its training) unless the user forces it (force) — such a challenger
    is then marked as forced, with 0 new matches, in its event."""
    from sports_edge.retraining import new_matches

    competitions = MODEL_SCOPES[scope]
    split = PROTOCOLS[scope]
    ensure_model_roles(store, now=now)
    champion = scope_version(store, competitions, ELO_FAMILY)
    v2_champion = (
        champion is not None and json.loads(champion["validation_json"]).get("protocol") == "v2"
    )
    fresh = new_matches(
        store,
        competitions,
        since=champion["trained_through"] if champion else None,
        now=now,
        identities=team_identities(paths),
    )
    if v2_champion and fresh == 0 and not force:
        raise ValueError(
            "Brak nowych meczów od treningu aktywnej wersji — challenger miałby te same dane co "
            "champion. Trening możliwy tylko jako wymuszony (zostanie wyraźnie oznaczony)."
        )
    forced = bool(force and v2_champion and fresh == 0)
    if progress:
        progress(f"Elo ({SCOPE_LABELS[scope]}): okna v2")
    elo = run_elo_experiment(
        store,
        competitions=competitions,
        split=split,
        now=now,
        policy=policy(paths),
        identities=team_identities(paths),
    )
    elo_row = next(r for r in model_overview(store) if r["version_id"] == elo["version_id"])
    if progress:
        progress(f"Dixon–Coles ({SCOPE_LABELS[scope]}): okna v2")
    goals = run_probability_experiment(
        store,
        competitions=competitions,
        split=split,
        now=now,
        policy=policy(paths),
        probability_policy=probability_policy(paths),
        elo_forecasts=elo_walk_forward_forecasts(
            store,
            now=now,
            identities=team_identities(paths),
            competitions=competitions,
            version=elo_row,
        ),
        identities=team_identities(paths),
        elo_version_id=elo["version_id"],
    )
    summary = {
        "protocol": "v2",
        "split": split.as_dict(),
        "elo_decision": elo.get("decision"),
        "goals_decision": goals.get("decision"),
        "statuses": {k: v["status"] for k, v in goals["predictive"].items()},
        "elo_statuses": {k: v["status"] for k, v in goals["elo_predictive"].items()},
        "markets_served": goals["markets_served"],
        "new_matches": fresh,
        "forced": forced,
    }
    reason = "Protokół v2: wybór i statusy na oknie selekcji, holdout tylko raportowany"
    if forced:
        reason = (
            "WYMUSZONY TRENING (użytkownik): 0 nowych meczów — te same dane co aktywna wersja. "
            + reason
        )
    for family, report in ((ELO_FAMILY, elo), (GOALS_FAMILY, goals)):
        first = learning.champion_id(store, scope, family) is None
        learning.record(
            store,
            kind="challenger_ready",
            actor="system",
            reason=reason,
            now=now,
            version_id=report["version_id"],
            scope=scope,
            family=family,
            payload=summary,
        )
        if first:  # nothing to compare with: the first model of a scope is served
            learning.record(
                store,
                kind="activated",
                actor="system",
                reason="Pierwsza wersja modelu w tym zakresie — brak modelu do porównania",
                now=now,
                version_id=report["version_id"],
                scope=scope,
                family=family,
            )
    return {"elo": elo, "goals": goals, "summary": summary}


def newest_protocol_version(store: Store, scope: str, family: str, protocol: str) -> dict | None:
    """Newest version of a family of one scope trained under ``protocol``."""
    for row in model_overview(store):
        if row["family"] != family or row["status"] == "retired":
            continue
        if json.loads(row["validation_json"]).get("protocol") == protocol and set(
            version_competitions(row)
        ) <= set(MODEL_SCOPES[scope]):
            return row
    return None


def run_protocol_v3(
    store: Store,
    paths: AppPaths,
    *,
    now: datetime,
    scope: str,
    progress=None,
    force: bool = False,
) -> dict:
    """TASK 012 §2: Elo and Dixon–Coles of one scope under protocol v3 (rolling windows from
    the training date), registered as CHALLENGERS (shadow mode).

    v3 trains challengers only: the champion (v2) stays served, and nothing is ever activated
    here — the activation gate of v3 is the prospective comparison in shadow mode and the
    decision is the user's. A v3 challenger on the same data as the newest v3 version of the
    scope (no new match since its training) is refused unless forced."""
    from sports_edge import rolling
    from sports_edge.retraining import new_matches

    competitions = MODEL_SCOPES[scope]
    ensure_model_roles(store, now=now)
    if learning.champion_id(store, scope, ELO_FAMILY) is None:
        raise ValueError(
            "Brak aktywnego modelu tego zakresu — protokół v3 trenuje wyłącznie challengery "
            "(pierwszy model zakresu powstaje w protokole v2)."
        )
    # TASK 011 §5.3 holds for v3 too: no challenger on the same data as a v2/v3 champion or
    # as the newest v3 version of the scope (no new match since) unless the user forces it
    champion = scope_version(store, competitions, ELO_FAMILY)
    if champion is not None and not rolling.decision_free(json.loads(champion["validation_json"])):
        champion = None  # a v1 champion (used holdout) never blocks a new challenger
    since = max(
        (
            row["trained_through"]
            for row in (
                champion,
                newest_protocol_version(store, scope, ELO_FAMILY, rolling.PROTOCOL),
            )
            if row is not None and row.get("trained_through")
        ),
        default=None,
    )
    fresh = new_matches(
        store, competitions, since=since, now=now, identities=team_identities(paths)
    )
    if since is not None and fresh == 0 and not force:
        raise ValueError(
            "Brak nowych meczów od treningu aktywnej wersji ani ostatniego challengera v3 — "
            "challenger miałby te same dane. Trening możliwy tylko jako wymuszony (zostanie "
            "wyraźnie oznaczony)."
        )
    forced = bool(force and since is not None and fresh == 0)
    windows_policy = rolling.load_policy(paths.config).for_scope(scope)  # TASK 013 §3
    if progress:
        progress(f"Elo ({SCOPE_LABELS[scope]}): okna v3")
    elo = rolling.run_elo_v3(
        store,
        competitions=competitions,
        now=now,
        policy=policy(paths),
        identities=team_identities(paths),
        rolling=windows_policy,
    )
    elo_row = next(r for r in model_overview(store) if r["version_id"] == elo["version_id"])
    if progress:
        progress(f"Dixon–Coles ({SCOPE_LABELS[scope]}): okna v3")
    goals = rolling.run_dc_v3(
        store,
        competitions=competitions,
        now=now,
        policy=policy(paths),
        probability_policy=probability_policy(paths),
        elo_forecasts=elo_walk_forward_forecasts(
            store,
            now=now,
            identities=team_identities(paths),
            competitions=competitions,
            version=elo_row,
        ),
        identities=team_identities(paths),
        elo_version_id=elo["version_id"],
        rolling=windows_policy,
        previous_half_life=previous_half_life(store, scope),
    )
    goals_row = next(r for r in model_overview(store) if r["version_id"] == goals["version_id"])
    changed = {
        "elo": rolling.differences(scope_version(store, competitions, ELO_FAMILY), elo_row),
        "goals": rolling.differences(scope_version(store, competitions, GOALS_FAMILY), goals_row),
    }
    summary = {
        "protocol": rolling.PROTOCOL,
        "split": elo["split"],
        "elo_decision": elo.get("decision"),
        "goals_decision": goals.get("decision"),
        "statuses": {k: v["status"] for k, v in goals["predictive"].items()},
        "elo_statuses": {k: v["status"] for k, v in goals["elo_predictive"].items()},
        "markets_served": goals["markets_served"],
        "new_matches": fresh,
        "forced": forced,
        "decision_gate": elo["decision_gate"],
        "differs_from_champion": {k: not v.get("identical", False) for k, v in changed.items()},
        "hyperparameters": {k: v.get("hyperparameters") for k, v in changed.items()},
    }
    reason = (
        f"Protokół v3 (okna ruchome, {elo['split']['rule']}): wybór na ostatnich miesiącach, "
        "bramka = porównanie w trybie cienia; aktywacja wyłącznie przez użytkownika"
    )
    if forced:
        reason = "WYMUSZONY TRENING (użytkownik): 0 nowych meczów od poprzedniego v3. " + reason
    for family, report in ((ELO_FAMILY, elo), (GOALS_FAMILY, goals)):
        learning.record(
            store,
            kind="challenger_ready",
            actor="system",
            reason=reason,
            now=now,
            version_id=report["version_id"],
            scope=scope,
            family=family,
            payload=summary,
        )
    return {"elo": elo, "goals": goals, "summary": summary, "differences": changed}


def run_walk_forward_v3(
    store: Store,
    paths: AppPaths,
    *,
    now: datetime,
    scope: str,
    first_month: str | None = None,
    progress=None,
    rules: tuple[str, ...] | None = None,
) -> dict:
    """TASK 012 §2.3: monthly v3 retraining against the frozen champion (served v2 version)
    of a scope, on the same matches (read-only: nothing is registered or activated).
    ``rules`` (TASK 013 §3): Dixon–Coles half-life rules measured on the same matches."""
    from sports_edge import rolling

    champions = (
        scope_version(store, MODEL_SCOPES[scope], ELO_FAMILY),
        scope_version(store, MODEL_SCOPES[scope], GOALS_FAMILY),
    )
    if None in champions:
        raise ValueError("Brak aktywnych wersji Elo i Dixon–Coles tego zakresu")
    policy_v3 = rolling.load_policy(paths.config).for_scope(scope)  # TASK 013 §3
    starts = rolling.month_starts(first_month or policy_v3.first_month, now)
    return rolling.walk_forward(
        store,
        scope,
        now=now,
        competitions=MODEL_SCOPES[scope],
        champion_elo=champions[0],
        champion_goals=champions[1],
        identities=team_identities(paths),
        rolling=policy_v3,
        months=starts,
        progress=progress,
        rules=rules,
    )


def previous_half_life(store: Store, scope: str) -> float | None:
    """TASK 013 §3: the previous half-life choice of a scope — the newest v3 Dixon–Coles
    version (its own rule kept it or changed it), else the served version."""
    from sports_edge import rolling

    for row in (
        newest_protocol_version(store, scope, GOALS_FAMILY, rolling.PROTOCOL),
        scope_version(store, MODEL_SCOPES[scope], GOALS_FAMILY),
    ):
        if row is not None:
            value = json.loads(row["validation_json"]).get("half_life_days")
            if value is not None:
                return float(value)
    return None


def train_challengers(
    store: Store,
    paths: AppPaths,
    *,
    now: datetime,
    scope: str,
    progress=None,
    force: bool = False,
) -> dict:
    """Challengers of one scope: protocol v3 (TASK 012); protocol v2 only when the v3
    windows are not feasible (too short a history) — said in the summary."""
    try:
        return run_protocol_v3(store, paths, now=now, scope=scope, progress=progress, force=force)
    except ValueError as error:
        if "oknach protokołu v3" not in str(error) and "trenuje wyłącznie" not in str(error):
            raise  # e.g. no new matches: the same refusal as before
        found = run_protocol_v2(store, paths, now=now, scope=scope, progress=progress, force=force)
        found["summary"]["v3_not_feasible"] = str(error)
        return found


ROLE_LABELS = {
    "active": "aktywna",
    "challenger": "challenger (tryb cienia)",
    "rejected": "odrzucona",
    "expired": "wygasła (brak dowodów)",
    "previous": "poprzednio aktywna",
    "unused": "nieużywana",
}


def model_versions_of(store: Store, scope: str) -> list[dict]:
    """Every version of both models of a scope with its role (for choosing what to serve)."""
    from sports_edge.retraining import elo_reports

    measured = elo_reports(store)
    out = []
    for family in (ELO_FAMILY, GOALS_FAMILY):
        champion = scope_version(store, MODEL_SCOPES[scope], family)
        events = learning.events(store, scope=scope, family=family)
        waiting = set(learning.challengers(store, scope, family))
        rejected = {
            e["model_version_id"] for e in events if e["kind"] in ("rejected", "kept_previous")
        }
        expired = {e["model_version_id"] for e in events if e["kind"] == "expired"}
        served = {e["model_version_id"] for e in events if e["kind"] in learning.ROLE_KINDS}
        for row in model_overview(store):
            competitions = version_competitions(row)
            if row["family"] != family or not competitions:
                continue
            if not set(competitions) <= set(MODEL_SCOPES[scope]):
                continue
            report = json.loads(row["validation_json"])
            vid = row["version_id"]
            role = (
                "active"
                if champion and vid == champion["version_id"]
                else "challenger"
                if vid in waiting
                else "rejected"
                if vid in rejected
                else "expired"
                if vid in expired
                else "previous"
                if vid in served
                else "unused"
            )
            decision = report.get("decision") if report.get("protocol") in ("v2", "v3") else None
            statuses = (
                report.get("predictive")
                or (measured.get(vid) or {}).get("elo_predictive")  # TASK 011: Elo statuses
                or {}
            )
            validated = [
                k for k, v in statuses.items() if v.get("status") == "validated_predictive"
            ]
            out.append(
                {
                    "family": family,
                    "version_id": vid,
                    "version": row["version"],
                    "created_at": row["created_at"],
                    "protocol": report.get("protocol", "v1"),
                    "role": role,
                    "log_loss": (decision or {}).get("log_loss"),
                    "validated": validated if report.get("protocol") in ("v2", "v3") else [],
                }
            )
    return out


def activation_block(store: Store, scope: str, versions: dict[str, str]) -> str | None:
    """TASK 013 §6.2: why serving these versions is refused (None: allowed — the decision
    is still the user's). A v3 version that was never served needs the user's acceptance of
    the v3 gate and then the shadow evidence: at least ``decision_matches`` common settled
    matches with the champion (n/300). v1/v2 versions and older champions are not gated."""
    from sports_edge import prospective_eval as pe

    blocked = learning.gate_block(store, versions.values())
    if blocked:
        return blocked
    policy = pe.load_policy()
    rows = None
    for family, version_id in sorted(versions.items()):
        if learning.version_protocol(store, version_id) != "v3":
            continue
        if learning.ever_served(store, version_id):
            continue
        champion = scope_version(store, MODEL_SCOPES[scope], family)
        if champion is None or champion["version_id"] == version_id:
            continue
        rows = pe.market_forecasts(store) if rows is None else rows
        found = pe.paired_matches(
            store, champion["version_id"], version_id, policy=policy, rows=rows
        )
        if not found.get("enough"):
            needed = found.get("needed") or policy.decision_matches
            return (
                "za mało dowodów w trybie cienia "
                f"({found.get('matches', 0)}/{needed} wspólnych rozliczonych meczów)"
            )
    return None


def switch_models(
    store: Store,
    paths: AppPaths,
    *,
    scope: str,
    versions: dict[str, str],
    now: datetime,
    reason: str,
) -> dict:
    """Serve the chosen versions (user decision) and forecast upcoming events with them at
    once, so the new champions do not start without forecasts. TASK 013: refused with the
    reason of ``activation_block`` (v3 gate not accepted, too little shadow evidence)."""
    blocked = activation_block(store, scope, versions)
    if blocked:
        raise ValueError(f"Aktywacja zablokowana: {blocked}")
    learning.activate(store, scope=scope, versions=versions, now=now, reason=reason)
    out = {"activated": versions}
    try:
        out["elo"] = generate_predictions(store, paths, now=now)["scopes"].get(scope)
        out["goals"] = generate_goals_predictions(store, paths, now=now)["scopes"].get(scope)
    except ValueError as error:
        out["forecasts"] = str(error)
    return out


def train_now(
    store: Store,
    paths: AppPaths,
    *,
    now: datetime,
    scope: str,
    progress=None,
    force: bool = False,
) -> dict:
    """Training on demand (button in Uczenie AI): new versions under protocol v3 (TASK 012;
    v2 when v3 is not feasible) become challengers in shadow mode at once; activating them
    stays the user's decision. Without new matches since the champion's training it refuses
    unless force (TASK 011)."""
    found = train_challengers(store, paths, now=now, scope=scope, progress=progress, force=force)
    if progress:
        progress("Prognozy challengerów (tryb cienia)")
    try:
        generate_predictions(store, paths, now=now)
        generate_goals_predictions(store, paths, now=now)
    except ValueError:
        pass  # no served model at all: the challengers still exist
    return found


# ---------------------------------------------------------------- TASK 008: pre-match


def lineup_fetcher(
    store: Store, paths: AppPaths, settings: AppSettings, *, now: datetime, provider=None
):
    """``fetch(event) → (provider match id, lineup payload or None, requests)`` for the
    pre-match engine. The PitchAPI match of an STS event is found once (verified team names,
    kick-off within 36 h) and kept in ``provider_fixtures``; season lists are cached per run."""
    from sports_edge.datasources.pitchapi import ENV_KEY as PITCH_KEY
    from sports_edge.datasources.pitchapi import season_label
    from sports_edge.results_engine import known_names

    provider = provider or PitchApiProvider(local_secret(PITCH_KEY, paths.root))
    status = provider.status(offline=settings.offline_mode)
    seasons: dict[tuple, list] = {}
    names: dict[str, dict] = {}
    aliases = alias_map(store, team_aliases(paths))
    norm = normalizer(paths)

    def fetch(event: dict) -> tuple[str | None, dict | None, int]:
        if not status.active:
            raise SourceBlocked(status.reason)
        code = league_code(event.get("league") or "", event.get("sport"))
        if code is None or code not in provider.competitions:
            return None, None, 0
        requests = 0
        cached = store.rows(
            "SELECT provider_match_id FROM provider_fixtures WHERE event_id=? AND provider=?",
            (event["id"], "pitchapi"),
        )
        match_id = cached[0]["provider_match_id"] if cached else None
        if match_id is None:
            start = datetime.fromisoformat(event["start_at"])
            key = (code, season_label(start.date()))
            if key not in seasons:
                seasons[key], _raw, _url = provider.season_matches(*key)
                requests += 1
            if code not in names:
                rows = effective_results(
                    store,
                    sport="football",
                    until=now,
                    competitions=(code,),
                    identities=team_identities(paths),
                )
                names[code] = known_names(rows, "football", norm)

            def canonical_name(name: str) -> str | None:
                found = match_participant(name, "football", names[code], norm, aliases)
                return found.target if found.status in {"exact", "alias"} else None

            wanted = [canonical_name(event.get(s) or "") for s in ("home", "away")]
            best, gap = None, timedelta(hours=36)
            for match in seasons[key]:
                when = match.get("time_utc")
                if not when or None in wanted:
                    continue
                distance = abs(datetime.fromisoformat(when.replace("Z", "+00:00")) - start)
                sides = [
                    canonical_name((match.get(f"{s}_team") or {}).get("name") or "")
                    for s in ("home", "away")
                ]
                if distance <= gap and sides == wanted:
                    best, gap = match, distance
            if best is None:
                return None, None, requests
            match_id = best["id"]
            store.insert(
                "provider_fixtures",
                id=digest(["fixture", event["id"], "pitchapi"]),
                event_id=event["id"],
                provider="pitchapi",
                provider_match_id=match_id,
                method="zweryfikowane nazwy drużyn + godzina ±36 h",
                matched_at=now.isoformat(),
            )
        payload, _raw, _url = provider.get(f"/v1/matches/{match_id}/lineups")
        requests += 1
        data = payload.get("data") or {}
        if not any((data.get(s) or {}).get("starters") for s in ("home", "away")):
            return match_id, None, requests
        return match_id, payload, requests

    fetch.blocked = None if status.active else status.reason  # type: ignore[attr-defined]
    return fetch


def prematch_overview(store: Store, *, now: datetime, horizon_hours: int = 48) -> dict:
    """KONTROLA PRZED MECZEM: what the pre-match engine has stored (no network)."""
    from sports_edge import prematch, reassessment

    rows = [
        {"event": event, "info": prematch.summary(store, event["id"])}
        for event in prematch_targets(store, now=now, horizon_hours=horizon_hours)
    ]
    attention = []
    for coupon in reassessment.active_coupons(store, now=now):
        last = reassessment.latest(store, coupon["id"])
        if last and last["result"] != "bez zmian":
            attention.append({"coupon": coupon, "reassessment": last})
    checks = [r["info"]["last_check"]["executed_at"] for r in rows if r["info"]["last_check"]]
    confirmed = ("confirmed", "official")
    return {
        "monitored": len(rows),
        "on_coupons": sum(r["event"]["priority"] == 1 for r in rows),
        "confirmed": sum(r["info"]["lineup_status"] in confirmed for r in rows),
        "predicted": sum(r["info"]["lineup_status"] == "predicted" for r in rows),
        "without_data": sum(
            r["info"]["lineup_status"] not in (*confirmed, "predicted") for r in rows
        ),
        "changes": sum(len(r["info"]["changes"]) for r in rows),
        "attention": attention,
        "last_update": max(checks, default=None),
        "rows": rows,
    }


def prematch_targets(
    store: Store, *, now: datetime, horizon_hours: int = 48, max_candidates: int = 30
) -> list[dict]:
    """Events to monitor before kick-off: every match on an unsettled coupon first (any
    kind: automatic, my coupon, tasiemka, virtual, real), then forecast candidates."""
    until = (now + timedelta(hours=horizon_hours)).isoformat()
    on_coupons = store.rows(
        """SELECT DISTINCT e.*, 1 priority FROM coupon_legs l JOIN odds o ON o.id=l.odds_id
        JOIN selections s ON s.id=o.selection_id JOIN markets m ON m.id=s.market_id
        JOIN events e ON e.id=m.event_id
        WHERE e.start_at>? AND e.start_at<=?
        AND NOT EXISTS (SELECT 1 FROM settlements x WHERE x.coupon_id=l.coupon_id)""",
        (now.isoformat(), until),
    )
    chosen = {e["id"]: e for e in on_coupons}
    candidates = store.rows(
        """SELECT DISTINCT e.*, 2 priority FROM predictions p JOIN events e ON e.id=p.event_id
        WHERE p.kind='prospective' AND e.start_at>? AND e.start_at<=? AND e.sport='football'
        ORDER BY e.start_at LIMIT ?""",
        (now.isoformat(), until, max_candidates * 3),
    )
    for event in candidates:
        if len(chosen) >= len(on_coupons) + max_candidates:
            break
        chosen.setdefault(event["id"], event)
    return sorted(chosen.values(), key=lambda e: (e["priority"], e["start_at"]))


def run_prematch(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    *,
    now: datetime,
    checkpoints=None,
    trigger: str = "automation",
    only_coupons: bool = False,
    force: bool = False,
    fetch=None,
    absence_provider=None,
    max_candidates: int = 30,
    event_ids: list[str] | None = None,
) -> dict:
    """Pre-match checks that are due (or every coupon event now, ``force``; or the chosen
    ``event_ids`` now — e.g. the matches of a coupon that is not saved yet)."""
    from sports_edge import alerts, prematch

    checkpoints = checkpoints or prematch.CHECKPOINTS
    fetch = fetch or lineup_fetcher(store, paths, settings, now=now)
    absence_provider = absence_provider or prematch.NoVerifiedAbsenceSource()
    if event_ids:
        marks = ",".join("?" for _ in event_ids)
        targets = [
            {**e, "priority": 1}
            for e in store.rows(f"SELECT * FROM events WHERE id IN ({marks})", tuple(event_ids))
        ]
        force = True
    else:
        targets = prematch_targets(store, now=now, max_candidates=max_candidates)
    if only_coupons:
        targets = [e for e in targets if e["priority"] == 1]
    out = {
        "events": len(targets),
        "checked": 0,
        "missed": 0,
        "changes": 0,
        "requests": 0,
        "errors": 0,
        "alerts": 0,
    }
    blocked = getattr(fetch, "blocked", None)
    if blocked:  # no key / offline / disabled: one clear reason, not an error per match
        return {**out, "blocked": blocked}
    for event in targets:
        if force:
            due, missed = ("ręcznie", None), []
        else:
            due, missed = prematch.due_checkpoints(store, event, now=now, checkpoints=checkpoints)
        for name, planned in missed:
            prematch.record_missed(store, event, name, planned, now)
            out["missed"] += 1
        if due is None:
            continue
        result = prematch.check_event(
            store,
            event,
            fetch_lineup=fetch,
            absence_provider=absence_provider,
            checkpoint=due[0],
            now=now,
            trigger=trigger,
            planned_at=due[1],
        )
        out["checked"] += 1
        out["requests"] += result.requests
        out["changes"] += len(result.changes)
        out["errors"] += result.status == "error"
        out["alerts"] += alerts.from_check(store, event, result, now=now)
    return out


def newest_v2_version(store: Store, scope: str, family: str) -> dict | None:
    for row in model_overview(store):
        if row["family"] != family or row["status"] == "retired":
            continue
        report = json.loads(row["validation_json"])
        if report.get("protocol") == "v2" and set(version_competitions(row)) <= set(
            MODEL_SCOPES[scope]
        ):
            return row
    return None


def run_ablation(store: Store, paths: AppPaths, *, now: datetime, scope: str) -> dict:
    """Feature ablation A–D of one scope on the protocol v2 windows (stored as an
    experiment; every result is kept, also a worse one)."""
    from sports_edge import ablation
    from sports_edge.probability_lab import dc_walk_forward, served_view

    elo_row = newest_v2_version(store, scope, ELO_FAMILY)
    goals_row = newest_v2_version(store, scope, GOALS_FAMILY)
    if elo_row is None or goals_row is None:
        raise ValueError("Brak wersji protokołu v2 w tym zakresie — najpierw protokół v2")
    split = PROTOCOLS[scope]
    competitions = MODEL_SCOPES[scope]
    rows = effective_results(
        store,
        sport="football",
        until=now,
        competitions=competitions,
        identities=team_identities(paths),
    )
    goals_report = json.loads(goals_row["validation_json"])
    raw = dc_walk_forward(
        rows, half_life=goals_report["half_life_days"], from_date=split.validation_from
    )
    calibrators = goals_report["calibrators"]
    goals = {k: served_view(v, calibrators)[("1x2", None)] for k, v in raw.items()}
    elo = elo_walk_forward_forecasts(
        store,
        now=now,
        identities=team_identities(paths),
        competitions=competitions,
        version=elo_row,
    )
    report = ablation.run(rows, split=split, elo=elo, goals=goals)
    report.update(
        scope=scope, elo_version_id=elo_row["version_id"], goals_version_id=goals_row["version_id"]
    )
    store.insert(
        "experiments",
        id=digest(["ablation", scope, elo_row["version_id"], goals_row["version_id"]]),
        hypothesis="Ablacja 1X2: Elo (A), Dixon–Coles (B), DC + cechy (C), prosty model ML (D)",
        registered_at=now.isoformat(),
        kind="backtest",
        config_json=canonical_json(report),
        status="concluded",
    )
    return report


def ablation_reports(store: Store) -> list[dict]:
    out = []
    for row in store.rows(
        "SELECT * FROM experiments WHERE hypothesis LIKE 'Ablacja 1X2%' ORDER BY registered_at"
    ):
        out.append({**json.loads(row["config_json"]), "registered_at": row["registered_at"]})
    return out


def generate_goals_predictions(
    store: Store, paths: AppPaths, *, now: datetime, events: list[dict] | None = None
) -> dict:
    events = _upcoming(store, now) if events is None else events
    out: dict = {"saved": 0, "kept": 0, "skipped": [], "scopes": {}, "shadow": {}}
    for name, pair in active_versions(store).items():
        if pair["goals"] is None:
            continue
        found = predict_offer_goals(
            store,
            version_id=pair["goals"]["version_id"],
            events=events,
            normalizer=normalizer(paths),
            now=now,
            team_aliases=team_aliases(paths),
            identities=team_identities(paths),
        )
        out["scopes"][name] = {k: found[k] for k in ("saved", "kept")}
        out["saved"] += found["saved"]
        out["kept"] += found["kept"]
        out["skipped"] += found["skipped"]
    for name, pair in challenger_versions(store).items():
        if "goals" in pair:  # shadow mode: archived, never served
            found = predict_offer_goals(
                store,
                version_id=pair["goals"]["version_id"],
                events=events,
                normalizer=normalizer(paths),
                now=now,
                team_aliases=team_aliases(paths),
                identities=team_identities(paths),
            )
            out["shadow"][name] = {k: found[k] for k in ("saved", "kept")}
    if not out["scopes"]:
        raise ValueError("Brak zarejestrowanego modelu bramkowego — uruchom eksperyment")
    return out


# ---------------------------------------------------------------- TASK 005: sports intelligence


def collect_offer_stats(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    *,
    now: datetime,
    events: list[dict],
    provider: PitchApiProvider | None = None,
) -> dict:
    """Match statistics of the recent matches of teams playing in the given STS events."""
    provider = provider or PitchApiProvider(local_secret(PITCH_ENV, paths.root))
    if settings.offline_mode or not settings.pitchapi_enabled:
        return {"status": "pominięte (tryb offline lub PitchAPI wyłączone)"}
    if not provider.key:
        return {"status": f"brak klucza {PITCH_ENV}"}
    names = normalizer(paths)
    aliases = alias_map(store, team_aliases(paths))
    identities = team_identities(paths)
    teams: dict[str, set[str]] = defaultdict(set)
    for event in events:
        code = league_code(event.get("league", ""), event.get("sport"))
        if code not in provider.competitions:
            continue
        rows = effective_results(
            store, sport="football", until=now, competitions=(code,), identities=identities
        )
        known = known_names(rows, "football", names)
        for side in ("home", "away"):
            found = match_participant(event[side] or "", "football", known, names, aliases)
            if found.status in {"exact", "alias"}:
                teams[code].add(found.target)
    report, budget = {}, settings.stats_requests_per_run
    for code, wanted in sorted(teams.items()):
        if budget <= 0:
            report[code] = {"status": "limit zapytań na uruchomienie wyczerpany"}
            continue
        out = collect_match_stats(
            store,
            provider,
            competition=code,
            teams=wanted,
            now=now,
            normalizer=names,
            aliases=aliases,
            identities=identities,
            cap=budget,
        )
        budget -= out.get("fetched", 0)
        report[code] = out
    return report


def event_features(
    store: Store, paths: AppPaths, event: dict, *, decision_time: datetime, save: bool = True
) -> dict | None:
    return features_as_of(
        store,
        event,
        decision_time,
        normalizer=normalizer(paths),
        aliases=alias_map(store, team_aliases(paths)),
        identities=team_identities(paths),
        save=save,
    )
