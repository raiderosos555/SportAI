"""Application workflows shared by the CLI and the Streamlit interface (STS ONLY)."""

import importlib.util
import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sports_edge.analysis.coupon import CouponReport, build_daily_coupon, diagnose, save_analysis
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
from sports_edge.matching import alias_map, load_team_aliases, match_participant, participant_key
from sports_edge.models.registry import latest_predictions, model_overview
from sports_edge.offers.sources import STS_URLS, SourceBlocked, StsOfficialPdf
from sports_edge.offers.text import NameNormalizer, load_normalizer
from sports_edge.paper import import_source_results, record_status_results
from sports_edge.pdf.engine import ParseError, parse_pdf
from sports_edge.repository import (
    ImportOutcome,
    ensure_reference_data,
    import_offer,
    imports,
    offer_table,
)
from sports_edge.results_engine import effective_results, league_freshness, update_results
from sports_edge.settings import AppPaths, AppSettings, local_secret
from sports_edge.sportsdata import (
    MAX_DATA_GAP_DAYS,
    Split,
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
    if local_secret(API_SPORTS_ENV, paths.root):
        lines.append("OK   klucz API-Sports (Ekstraklasa i inne dyscypliny)")
    else:
        lines.append("UWAGA brak klucza API_SPORTS_KEY (.env) — Ekstraklasa bez wyników")
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
    )
    report["sts_statuses"] = record_status_results(
        store, report.pop("status_records"), now=now, normalizer=names, team_aliases=extra
    )
    report["sts_results"] = import_source_results(
        store, now=now, normalizer=names, team_aliases=extra, only_coupons=False
    )
    return report


@dataclass
class PipelineRun:
    report: CouponReport
    candidates: list[Candidate]
    analysis_id: str
    stages: list[dict]


STAGES = (
    "Oferta STS",
    "Dane sportowe",
    "Modele",
    "Dopasowanie",
    "Matematyka",
    "Walidacja",
    "Wynik",
    "Zapis",
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
) -> PipelineRun:
    """One button: offer → sports data → models → matching → EV → gates → ranking → save.

    Network steps run only when the settings allow them (offline mode, opt-ins, keys); paid
    sources are never called. Missing data stops nothing: each stage reports what it could do.
    """
    stages: list[dict] = []

    def stage(index: int, status: str, detail: str, **numbers) -> None:
        stages.append({"stage": STAGES[index], "status": status, "detail": detail, **numbers})
        if progress:
            progress(index, STAGES[index], detail)

    # 1. STS offer ------------------------------------------------------------------
    refreshed = None
    if refresh_offer:
        refreshed = _refresh_offer(store, paths, settings, day, now=now)
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
    max_age_h = settings.max_quote_age_minutes / 60
    advice = (
        f"Najnowszy dokument STS ma {age} h — zalecane ponowne pobranie oferty"
        if age is not None and age > max_age_h
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

    # 2. Sports data -----------------------------------------------------------------
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
            f"nierozpoznane nazwy: {len(update['unmatched'])}, błędy: {len(update['errors'])}; "
            if update
            else "aktualizacja pominięta; "
        )
        + f"aktualne ligi: {len(fresh_leagues)}/{len(freshness)} (próg {MAX_DATA_GAP_DAYS} dni)",
        update=_short_update(update),
        freshness=freshness,
    )

    # 3. Models -----------------------------------------------------------------------
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
        upcoming = [
            e
            for e in store.rows(
                "SELECT * FROM events WHERE bookmaker_id='sts' AND offer_date=? "
                "AND start_at IS NOT NULL AND start_at > ?",
                (day, now.isoformat()),
            )
        ]
        forecasts = predict_offer(
            store,
            version_id=version["version_id"],
            events=upcoming,
            normalizer=normalizer(paths),
            now=now,
            team_aliases=team_aliases(paths),
        )
    stage(
        2,
        "ok" if version else "brak modelu",
        (
            f"model {version['family']} {version['version']} ({version['status']}); "
            f"nowe prognozy: {forecasts['saved']}, aktualne bez zmian: {forecasts['kept']}, "
            f"pominięte: {len(forecasts['skipped'])}"
            if version and forecasts
            else f"brak modelu: {trained.get('error') if trained else 'nie zarejestrowano'}"
        ),
        model=version["version"] if version else None,
        model_status=version["status"] if version else None,
        trained_now=bool(trained and "error" not in trained),
        forecasts_saved=forecasts["saved"] if forecasts else 0,
        forecasts_kept=forecasts["kept"] if forecasts else 0,
        skipped=(forecasts or {}).get("skipped", [])[:50],
    )

    # 4. Matching ---------------------------------------------------------------------
    supported = sum((r["market_key"], r["period"]) in MODEL_MARKETS for r in rows)
    unmatched = (forecasts or {}).get("unmatched", [])
    stage(
        3,
        "ok" if forecasts and forecasts["considered"] else "brak wydarzeń w ligach z modelem",
        f"wydarzenia w ligach z danymi: {(forecasts or {}).get('considered', 0)}, "
        f"nazwy do potwierdzenia: {len(unmatched)}; rynki obsługiwane przez model "
        f"(1X2 i podwójna szansa — czas regulaminowy): {supported} z {len(rows)}",
        considered=(forecasts or {}).get("considered", 0),
        unmatched=unmatched[:50],
        supported_markets=supported,
    )

    # 5-6. Mathematics and validation gates -------------------------------------------------
    report, candidates = analyse_day(store, paths, settings, day, now=now, data_kinds=data_kinds)
    stage(
        4,
        "ok",
        f"selekcje: {report.counts['analysed']}, z prognozą: {report.counts['with_forecast']}, "
        f"EV netto > 0: {report.counts['ev_positive']}",
        **{k: report.counts[k] for k in ("analysed", "with_forecast", "ev_positive")},
    )
    gates = Counter(r for c in candidates for r in c.reasons)
    stage(
        5,
        "ok",
        f"odrzucone: {report.counts['rejected']}, eksperymentalne: "
        f"{report.counts['experimental']}, zwalidowane: {report.counts['validated']}",
        top_reasons=dict(gates.most_common(12)),
    )

    # 7. Result -----------------------------------------------------------------------
    report = replace(
        report,
        diagnosis=diagnose(report.counts, sports_data_fresh=bool(fresh_leagues)),
    )
    stage(6, "ok", report.headline, diagnosis=list(report.diagnosis))

    # 8. Save -------------------------------------------------------------------------
    pipeline = {"stages": stages, "run_at": now.astimezone(UTC).isoformat()}
    analysis_id = save_analysis(store, report, candidates, policy(paths), pipeline=pipeline)
    stage(7, "ok", f"analiza zapisana ({analysis_id[:12]}…)", analysis_id=analysis_id)
    return PipelineRun(report, candidates, analysis_id, stages)


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
    prediction = latest_predictions(store, as_of=now).get((event_id, "1x2", "FT", None))
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
    rows = effective_results(store, sport=sport, until=now, competitions=(code,))
    names = normalizer(paths)
    aliases = alias_map(store, team_aliases(paths))
    known: dict[str, str] = {}
    for row in reversed(rows):
        for team in (row["home"], row["away"]):
            known.setdefault(participant_key(team, sport, names), team)
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
