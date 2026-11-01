"""Application workflows shared by the CLI and the Streamlit interface (STS ONLY)."""

import importlib.util
import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sports_edge.analysis.coupon import CouponReport, build_daily_coupon, diagnose, save_analysis
from sports_edge.analysis.coupon_builder import BuilderReport, Proposal, build_coupons
from sports_edge.analysis.edge_engine import Candidate, evaluate
from sports_edge.analysis.policy import Policy, load_policy
from sports_edge.bookmakers import load_profiles, load_rules
from sports_edge.datasources import openfootball
from sports_edge.datasources.api_sports import ENV_KEY as API_SPORTS_ENV
from sports_edge.datasources.api_sports import (
    ApiSportsFootballProvider,
    ApiSportsGamesProvider,
)
from sports_edge.datasources.base import SportsResultsProvider
from sports_edge.datasources.bookmaker_results import StsResultsProvider, SuperbetResultsProvider
from sports_edge.datasources.competitions import COMPETITIONS, for_sts_league
from sports_edge.datasources.football_data import FootballDataOrgProvider, api_key
from sports_edge.datasources.pitchapi import ENV_KEY as PITCH_ENV
from sports_edge.datasources.pitchapi import PitchApiProvider
from sports_edge.ingestion.types import digest
from sports_edge.matching import (
    TeamIdentities,
    alias_map,
    load_team_aliases,
    load_team_identities,
    match_participant,
)
from sports_edge.models.registry import latest_predictions, model_overview
from sports_edge.offers.sources import STS_URLS, SourceBlocked, StsOfficialPdf
from sports_edge.offers.text import NameNormalizer, load_normalizer
from sports_edge.paper import (
    create_coupon,
    import_source_results,
    latest_settlement,
    record_status_results,
    settle_coupon,
)
from sports_edge.pdf.engine import ParseError, parse_pdf
from sports_edge.probability_lab import (
    FAMILY as GOALS_FAMILY,
)
from sports_edge.probability_lab import (
    ProbabilityPolicy,
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
RESEARCH_COMPETITIONS = ("en.1", "de.1", "es.1", "it.1", "fr.1")
RESEARCH_SPLIT = Split(validation_from="2022-07-01", holdout_from="2024-07-01")
# What the registered Elo model forecasts (double chance follows from the same 1X2).
MODEL_MARKETS = {("1x2", "FT"), ("double_chance", "FT")}
RESULTS_COMPETITIONS = tuple(COMPETITIONS)  # every league with a results source


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
) -> tuple[CouponReport, list[Candidate]]:
    profiles = load_profiles(paths.config)
    rules = policy(paths)
    candidates = evaluate(
        offer_table(store, day=day, data_kinds=data_kinds),
        predictions=with_predictive_status(
            store, latest_predictions(store, as_of=now, prefer=prediction_preference(store))
        ),
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
        identities=team_identities(paths),
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
        identities=team_identities(paths),
    )
    report["settlements"] = settle_coupons(store, paths, now=now)
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
) -> PipelineRun:
    """One button: offer → results → statistics → features → forecasts → calibration →
    ranking → coupon builder → probabilities → payouts → risk → save → presentation.

    Network steps run only when the settings allow them (offline mode, opt-ins, keys); paid
    sources are never called; models are trained only when no version exists yet. Missing
    data stops nothing: each stage reports what it could do.
    """
    stages: list[dict] = []

    def stage(index: int, status: str, detail: str, **numbers) -> None:
        stages.append({"stage": STAGES[index], "status": status, "detail": detail, **numbers})
        if progress:
            progress(index, STAGES[index], detail)

    # 1. STS offer ------------------------------------------------------------------
    refreshed = _refresh_offer(store, paths, settings, day, now=now) if refresh_offer else None
    rows = offer_table(store, day=day, data_kinds=data_kinds)
    docs = [
        i
        for i in imports(store)
        if i["data_kind"] in data_kinds
        and day in json.loads(i["summary_json"]).get("offer_dates", [])
    ]
    non_empty = [i for i in docs if i["rows_total"] > 0]
    newest = max((i["generated_at"] or "" for i in non_empty), default=None)
    age = (
        round((now - datetime.fromisoformat(newest)).total_seconds() / 3600, 1) if newest else None
    )
    advice = (
        f"Najnowszy dokument STS ma {age} h — zalecane ponowne pobranie oferty"
        if age is not None and age > settings.max_quote_age_minutes / 60
        else "Oferta aktualna"
        if age is not None
        else "Brak dokumentu STS dla tego dnia"
    )
    stage(
        0,
        "ok" if rows else "brak danych",
        f"{len({r['event_id'] for r in rows})} wydarzeń, {len(rows)} rynków; {advice}"
        + (f"; odświeżenie: {refreshed}" if refreshed else ""),
        events=len({r["event_id"] for r in rows}),
        markets=len(rows),
        review=sum(r["status"] != "ok" for r in rows),
        documents=len(docs),
        empty_documents=len(docs) - len(non_empty),
        newest_document=newest,
        document_age_hours=age,
    )

    # 2. Results ----------------------------------------------------------------------
    update = None
    if refresh_results:
        update = update_results_data(store, paths, settings, now=now, providers=providers)
    freshness = league_freshness(store, RESEARCH_COMPETITIONS, now=now)
    fresh_leagues = [f for f in freshness if f["fresh"]]
    stage(
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
    upcoming = store.rows(
        "SELECT * FROM events WHERE bookmaker_id='sts' AND offer_date=? "
        "AND start_at IS NOT NULL AND start_at > ?",
        (day, now.isoformat()),
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
    matched, unmatched = 0, []
    for event in covered:
        code = league_code(event["league"], event["sport"])
        known = known_names(
            effective_results(
                store, sport=event["sport"], until=now, competitions=(code,), identities=identities
            ),
            event["sport"],
            names,
        )
        sides = [
            match_participant(event[s] or "", event["sport"], known, names, aliases)
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
    for event in covered:
        if event_features(store, paths, event, decision_time=now) is not None:
            snapshots += 1
    stage(4, "ok", f"migawki cech przedmeczowych: {snapshots}", snapshots=snapshots)

    # 6. Models -----------------------------------------------------------------------
    version = latest_model_version(store)
    trained = None
    if version is None:
        try:
            trained = run_research_experiment(store, paths, now=now)
            version = latest_model_version(store)
        except ValueError as error:
            trained = {"error": str(error)}
    forecasts = None
    if version is not None:
        forecasts = predict_offer(
            store,
            version_id=version["version_id"],
            events=upcoming,
            normalizer=names,
            now=now,
            team_aliases=team_aliases(paths),
            identities=identities,
        )
    goals_version = latest_model_version(store, family=GOALS_FAMILY)
    if goals_version is None and version is not None:
        try:
            run_goals_experiment(store, paths, now=now)
            goals_version = latest_model_version(store, family=GOALS_FAMILY)
        except ValueError:
            goals_version = None
    goals = (
        generate_goals_predictions(store, paths, now=now, events=upcoming)
        if goals_version
        else None
    )
    stage(
        5,
        "ok" if version else "brak modelu",
        (
            f"Elo {version['version']}: nowe prognozy {forecasts['saved']}, aktualne "
            f"{forecasts['kept']}, pominięte {len(forecasts['skipped'])}"
            + (f"; Dixon–Coles: nowe {goals['saved']}, aktualne {goals['kept']}" if goals else "")
            if version and forecasts
            else f"brak modelu: {trained.get('error') if trained else 'nie zarejestrowano'}"
        ),
        model=version["version"] if version else None,
        model_status=version["status"] if version else None,
        goals_model=goals_version["version"] if goals_version else None,
        trained_now=bool(trained and "error" not in trained),
        forecasts_saved=forecasts["saved"] if forecasts else 0,
        forecasts_kept=forecasts["kept"] if forecasts else 0,
        goals_saved=goals["saved"] if goals else 0,
        skipped=(forecasts or {}).get("skipped", [])[:50],
    )

    # 7. Calibration and predictive validation -----------------------------------------
    quality = model_quality(store)
    stage(
        6,
        "ok" if quality else "brak oceny",
        "zwalidowane predykcyjnie: "
        + (
            ", ".join(k for k, v in quality.items() if v["status"] == "validated_predictive") or "—"
        ),
        families=quality,
    )

    # 8. Ranking of selections ---------------------------------------------------------------
    report, candidates = analyse_day(store, paths, settings, day, now=now, data_kinds=data_kinds)
    report = replace(
        report,
        diagnosis=diagnose(report.counts, sports_data_fresh=bool(fresh_leagues)),
    )
    stage(
        7,
        "ok",
        f"selekcje: {report.counts['analysed']}, z prognozą: {report.counts['with_forecast']}, "
        f"z P ≥ progu i świeżym kursem: {len(report.probable)}",
        **{k: report.counts[k] for k in ("analysed", "with_forecast", "ev_positive")},
        probable=len(report.probable),
    )

    # 9-12. Coupon builder --------------------------------------------------------------
    built = build_coupons(
        candidates,
        probability_policy(paths),
        cost=load_profiles(paths.config)["sts"].cost,
        on_date=date.fromisoformat(day),
    )
    made = {k: v for k, v in built.proposals.items() if v is not None}
    stage(
        8,
        "ok" if made else "brak kuponu",
        f"pula selekcji: {len(built.pool)}; kupony: {', '.join(made) or 'żaden profil'}",
        pool=len(built.pool),
    )
    stage(
        9,
        "ok",
        "; ".join(f"{k}: {v.p_conservative:.0%} (ostrożnie)" for k, v in made.items()) or "—",
        probabilities={k: [v.p_calibrated, v.p_conservative] for k, v in made.items()},
    )
    stage(
        10,
        "ok",
        "; ".join(f"{k}: wypłata {v.payout} zł, zysk {v.net_profit} zł" for k, v in made.items())
        or "—",
    )
    stage(
        11,
        "ok",
        "; ".join(f"{k}: {why}" for k, why in built.refusals.items()) or "wszystkie profile",
        excluded=built.excluded,
        refusals=built.refusals,
    )

    # 13. Save -----------------------------------------------------------------------
    pipeline = {"stages": stages, "run_at": now.astimezone(UTC).isoformat()}
    analysis_id = save_analysis(store, report, candidates, policy(paths), pipeline=pipeline)
    coupon_ids = (
        save_coupon_proposals(store, paths, built, analysis_id=analysis_id, now=now, day=day)
        if save_coupons
        else {}
    )
    stage(
        12,
        "ok",
        f"analiza {analysis_id[:12]}…, propozycje kuponów: {len(coupon_ids)}",
        analysis_id=analysis_id,
        coupons=coupon_ids,
    )

    # 14. Presentation ---------------------------------------------------------------------
    stage(
        13,
        "ok",
        "kupony gotowe"
        if made
        else "BRAK KUPONU SPEŁNIAJĄCEGO WARUNKI PROFILI — " + report.headline,
    )
    return PipelineRun(report, candidates, analysis_id, stages, built, coupon_ids)


def model_quality(store: Store) -> dict[str, dict]:
    """Predictive status per market family: Elo serves 1X2 / double chance, Dixon–Coles goals."""
    goals = latest_model_version(store, family=GOALS_FAMILY)
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


def proposal_payload(proposal: Proposal, *, policy_version: str) -> dict:
    """What the coupon view shows and what is stored with a saved proposal (JSON-safe)."""
    return {
        "profile": proposal.profile,
        "label": proposal.label,
        "policy_version": policy_version,
        "level": proposal.level,
        "probability": {
            "calibrated": proposal.p_calibrated,
            "conservative": proposal.p_conservative,
            "basis": proposal.joint_basis,
        },
        "total_odds": str(proposal.total_odds),
        "stake": str(proposal.stake),
        "payout": str(proposal.payout),
        "net_profit": str(proposal.net_profit),
        "ev_net": str(proposal.ev_net),
        "legs": [
            {
                "event": leg.event,
                "league": leg.league,
                "start_at": leg.start_at,
                "market": leg.market_label,
                "market_key": leg.market_key,
                "line": leg.line,
                "selection": leg.selection,
                "offer_code": leg.offer_code,
                "odds": str(leg.odds),
                "odds_id": leg.odds_id,
                "p_model": leg.p_model,
                "p_conservative": leg.p_low,
                "p_market": leg.p_market,
                "predictive_status": leg.predictive_status,
                "calibration": leg.calibration,
                "model": leg.model,
                "model_version_id": leg.model_version_id,
                "document_at": leg.document_at,
                "odds_age_hours": leg.odds_age_hours,
                "freshness": leg.freshness,
            }
            for leg in proposal.legs
        ],
        "reasons": list(proposal.reasons),
        "warnings": list(proposal.warnings),
    }


def saved_proposals(store: Store, day: str) -> dict[str, dict]:
    """Newest saved builder coupon of each profile for an analysis day (with settlement)."""
    out: dict[str, dict] = {}
    for row in store.rows(
        """SELECT c.* FROM coupons c JOIN analyses a ON a.id=c.analysis_id
        WHERE a.analysis_date=? AND c.proposal_json IS NOT NULL ORDER BY c.created_at DESC""",
        (day,),
    ):
        if row["profile"] in out:
            continue
        settlement = latest_settlement(store, row["id"])
        out[row["profile"]] = {
            **json.loads(row["proposal_json"]),
            "coupon_id": row["id"],
            "created_at": row["created_at"],
            "outcome": settlement["outcome"] if settlement else None,
            "settled_net_profit": settlement["net_profit"] if settlement else None,
        }
    return out


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


def save_coupon_proposals(
    store: Store,
    paths: AppPaths,
    built: BuilderReport,
    *,
    analysis_id: str,
    now: datetime,
    day: str,
) -> dict[str, str]:
    """Every proposal becomes a paper coupon (idempotent per day and policy)."""
    rules = probability_policy(paths)
    sts = load_profiles(paths.config)["sts"]
    ids = {}
    for name, proposal in built.proposals.items():
        if proposal is None:
            continue
        odds_ids = [leg.odds_id for leg in proposal.legs]
        payload = proposal_payload(proposal, policy_version=rules.version)
        try:
            ids[name] = create_coupon(
                store,
                odds_ids=odds_ids,
                stake=proposal.stake,
                mode="paper_system",
                profile=sts,
                now=now,
                analysis_id=analysis_id,
                note=f"Kreator kuponów — {proposal.label}",
                proposal=payload,
                key=digest(["proposal", name, sorted(odds_ids), rules.version, day]),
            )
        except ValueError as error:
            ids[name] = f"nie zapisano: {error}"
    return ids


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
        store, latest_predictions(store, as_of=now, prefer=prediction_preference(store))
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


def elo_walk_forward_forecasts(
    store: Store, *, now: datetime, identities: TeamIdentities | None = None
) -> dict[str, dict]:
    """As-of Elo forecasts of every historical match (benchmark for the goals model)."""
    version = latest_model_version(store)
    if version is None:
        return {}
    params = json.loads(version["validation_json"])["hyperparameters"]
    matches = _scoped(
        history(
            store,
            sport="football",
            until=now,
            competitions=RESEARCH_COMPETITIONS,
            identities=identities,
        )
    )
    items, _margin = _forecasts(
        matches,
        k=params["k"],
        home_advantage=params["home_advantage"],
        margin_until=RESEARCH_SPLIT.holdout_from,
    )
    return {item["match"].result_id: item["forecast"] for item in items}


def run_goals_experiment(store: Store, paths: AppPaths, *, now: datetime) -> dict:
    elo = latest_model_version(store)
    return run_probability_experiment(
        store,
        competitions=RESEARCH_COMPETITIONS,
        split=RESEARCH_SPLIT,
        now=now,
        policy=policy(paths),
        probability_policy=probability_policy(paths),
        elo_forecasts=elo_walk_forward_forecasts(store, now=now, identities=team_identities(paths)),
        identities=team_identities(paths),
        elo_version_id=elo["version_id"] if elo else None,
    )


def elo_validation(store: Store) -> tuple[str | None, dict, dict]:
    """Predictive status and holdout ranges of the Elo version benchmarked by the newest
    goals experiment: (Elo version id, status per family, metrics per family).

    Versions registered before the id was recorded used the newest Elo version that existed
    when the goals experiment ran.
    """
    goals = latest_model_version(store, family=GOALS_FAMILY)
    if goals is None:
        return None, {}, {}
    report = json.loads(goals["validation_json"])
    version_id = report.get("elo_version_id")
    if version_id is None:
        version_id = next(
            (
                row["version_id"]
                for row in model_overview(store)
                if row["family"] == ELO_FAMILY and row["created_at"] <= goals["created_at"]
            ),
            None,
        )
    return version_id, report.get("elo_predictive") or {}, report.get("elo_families") or {}


def with_predictive_status(store: Store, predictions: dict[tuple, dict]) -> dict[tuple, dict]:
    """Elo forecasts carry no status of their own: the goals experiment measured it for one
    Elo version, so only that version's forecasts receive it (others stay experimental)."""
    version_id, statuses, families = elo_validation(store)
    if version_id is None:
        return predictions
    for row in predictions.values():
        if row["model_version_id"] != version_id or row["market_key"] != "1x2":
            continue
        if "predictive_status" in row["uncertainty"]:
            continue
        row["uncertainty"] = {
            **row["uncertainty"],
            "family": "1x2",
            "predictive_status": (statuses.get("1x2") or {}).get("status", "experimental"),
            "derived_status": {
                "double_chance": (statuses.get("double_chance") or {}).get("status", "experimental")
            },
        }
        row["validation"] = {**row["validation"], "families": families}
    return predictions


def prediction_preference(store: Store) -> dict[str, str]:
    """1X2 is served by the model that won the VALIDATION comparison (stored in the version)."""
    goals = latest_model_version(store, family=GOALS_FAMILY)
    if goals is None:
        return {}
    served = json.loads(goals["validation_json"]).get("markets_served", [])
    return {"1x2": GOALS_FAMILY if "1x2" in served else ELO_FAMILY}


def generate_goals_predictions(
    store: Store, paths: AppPaths, *, now: datetime, events: list[dict] | None = None
) -> dict:
    version = latest_model_version(store, family=GOALS_FAMILY)
    if version is None:
        raise ValueError("Brak zarejestrowanego modelu bramkowego — uruchom eksperyment")
    if events is None:
        events = store.rows(
            "SELECT * FROM events WHERE bookmaker_id='sts' AND start_at IS NOT NULL AND start_at>?",
            (now.isoformat(),),
        )
    return predict_offer_goals(
        store,
        version_id=version["version_id"],
        events=events,
        normalizer=normalizer(paths),
        now=now,
        team_aliases=team_aliases(paths),
        identities=team_identities(paths),
    )


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
