import argparse
import json
import sys
from datetime import UTC, date, datetime
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path

from sports_edge.edge import binary_ev, net_ev
from sports_edge.settlement import CostProfile, Outcome


def demo() -> None:
    # Synthetic arithmetic fixture, explicitly not a market forecast or Polish tax profile.
    profile = CostProfile(
        id="synthetic-12pct",
        currency="UNIT",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
        stake_tax_rate=Decimal("0.12"),
    )
    result = net_ev(
        {Outcome.WIN: 0.55, Outcome.LOSS: 0.45},
        Decimal("100"),
        Decimal("2"),
        profile,
        on_date=date(2026, 10, 31),
    )
    print(
        json.dumps(
            {
                "mode": "synthetic_demo",
                "gross_ev": round(binary_ev(0.55, 2), 6),
                "net_ev": str(result),
                "recommendation": "none",
                "evidence": "no empirical experiment performed",
            },
            indent=2,
        )
    )


def _context():
    from sports_edge.services import open_store
    from sports_edge.settings import AppPaths, load_settings, project_root

    paths = AppPaths(project_root())
    return paths, load_settings(paths), open_store(paths)


def _print(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def import_pdf(path: Path) -> None:
    from sports_edge.services import import_pdf_bytes

    paths, settings, store = _context()
    outcome = import_pdf_bytes(
        store, paths, settings, path.read_bytes(), file_name=path.name, now=datetime.now(UTC)
    )
    _print({"duplicate": outcome.duplicate, **outcome.summary})


def fetch_sts(day: str) -> None:
    from sports_edge.services import fetch_sts_offer

    paths, settings, store = _context()
    outcome = fetch_sts_offer(store, paths, settings, day, now=datetime.now(UTC))
    _print({"duplicate": outcome.duplicate, **outcome.summary})


def research(
    step: str,
    scope: str = "core",
    *,
    force: bool = False,
    first_month: str | None = None,
    out: Path | None = None,
    rules: str | None = None,
) -> None:
    """Independent data and the first experiment; downloads respect the offline setting."""
    from sports_edge import services
    from sports_edge.datasources import openfootball

    paths, settings, store = _context()
    now = datetime.now(UTC)
    if step == "fetch-data":
        _print(
            openfootball.fetch(
                paths.data / "sources" / "openfootball",
                leagues=list(openfootball.LEAGUES),
                seasons=openfootball.season_range("2012-13", "2026-27"),
                offline=settings.offline_mode,
            )
        )
    elif step == "import-data":
        _print(services.import_openfootball_files(store, paths, now=now))
    elif step == "experiment":
        report = services.run_research_experiment(
            store, paths, now=now, competitions=services.MODEL_SCOPES[scope]
        )
        _print({k: v for k, v in report.items() if k not in {"selection", "holdout"}})
    elif step == "predict":
        _print(services.generate_predictions(store, paths, now=now))
    elif step == "goals-experiment":
        report = services.run_goals_experiment(
            store, paths, now=now, competitions=services.MODEL_SCOPES[scope]
        )
        _print(
            {k: report[k] for k in ("version_id", "predictive", "markets_served") if k in report}
        )
    elif step == "ablation":
        _print(services.run_ablation(store, paths, now=now, scope=scope))
    elif step == "protocol-v2":
        found = services.run_protocol_v2(
            store, paths, now=now, scope=scope, progress=print, force=force
        )
        _print(
            {
                "elo_version_id": found["elo"]["version_id"],
                "goals_version_id": found["goals"]["version_id"],
                **found["summary"],
            }
        )
    elif step == "protocol-v3":  # TASK 012: rolling windows, challengers only
        found = services.run_protocol_v3(
            store, paths, now=now, scope=scope, progress=print, force=force
        )
        _print(
            {
                "elo_version_id": found["elo"]["version_id"],
                "goals_version_id": found["goals"]["version_id"],
                **found["summary"],
                "differences": found["differences"],
            }
        )
    elif step == "walk-forward-v3":  # TASK 012 §2.3: read-only proof on history
        found = services.run_walk_forward_v3(
            store,
            paths,
            now=now,
            scope=scope,
            first_month=first_month,
            progress=print,
            rules=tuple(r.strip() for r in rules.split(",")) if rules else None,
        )
        rows = found.pop("rows")
        if out is not None:
            out.write_text(
                json.dumps({**found, "rows": rows}, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        _print(found)
    elif step == "goals-predict":
        _print(services.generate_goals_predictions(store, paths, now=now))
    elif step == "backfill":
        _print(services.backfill_history(store, paths, settings, now=now, progress=print))
    elif step == "update-results":
        _print(services.update_results_data(store, paths, settings, now=now, progress=print))
    elif step == "results":
        from sports_edge.paper import import_source_results

        _print(
            import_source_results(
                store,
                now=now,
                normalizer=services.normalizer(paths),
                team_aliases=services.team_aliases(paths),
                identities=services.team_identities(paths),
            )
        )


def analyse(day: str | None, *, refresh_offer: bool, refresh_results: bool) -> None:
    """The one-button daily analysis without the interface."""
    from zoneinfo import ZoneInfo

    from sports_edge import services

    paths, settings, store = _context()
    now = datetime.now(UTC)
    day = day or now.astimezone(ZoneInfo("Europe/Warsaw")).date().isoformat()
    run = services.run_daily_analysis(
        store,
        paths,
        settings,
        day,
        now=now,
        refresh_offer=refresh_offer,
        refresh_results=refresh_results,
        progress=lambda i, name, detail: print(
            f"[{i + 1}/{len(services.STAGES)}] {name}: {detail}"
        ),
    )
    rules = services.probability_policy(paths)
    _print(
        {
            "coupons": {
                name: services.proposal_payload(p, policy_version=rules.version)
                if p
                else {"refusal": run.coupons.refusals.get(name)}
                for name, p in run.coupons.proposals.items()
            },
            "saved_coupons": run.coupon_ids,
            "headline_ev": run.report.headline,
            "counts": run.report.counts,
            "analysis_id": run.analysis_id,
        }
    )


def export(kind: str) -> None:
    from sports_edge.export import export_csv, export_json

    paths, _settings, store = _context()
    now = datetime.now(UTC)
    if kind == "json":
        print(export_json(store, paths.exports / f"export_{now:%Y%m%d_%H%M%S}.json", now=now))
    else:
        for path in export_csv(store, paths.exports / f"csv_{now:%Y%m%d_%H%M%S}"):
            print(path)


def backup(include_raw: bool) -> None:
    from sports_edge.backup import create_backup
    from sports_edge.settings import AppPaths, project_root

    print(create_backup(AppPaths(project_root()), now=datetime.now(), include_raw=include_raw))


def lineage() -> None:
    """TASK 010: record the lineage of every coupon that has none yet (idempotent)."""
    from sports_edge.coupon_store import backfill_lineage

    _paths, _settings, store = _context()
    found = backfill_lineage(store, now=datetime.now(UTC))
    print(json.dumps({"recorded": found, "total": sum(found.values())}, ensure_ascii=False))


def diversity(days: str | None, at: str) -> None:
    """TASK 010 §4: as-of replay with the diversity limit off / 1 / 2 (measurement only)."""
    from sports_edge.repository import offer_days
    from sports_edge.simulation import diversity_replay

    paths, settings, store = _context()
    today = datetime.now(UTC).date().isoformat()
    chosen = days.split(",") if days else [d for d in offer_days(store) if d < today]
    hour, minute = (int(x) for x in at.split(":"))
    found = diversity_replay(store, paths, settings, chosen, decision_time=dtime(hour, minute))
    print(json.dumps(found, ensure_ascii=False, indent=1, default=str))


def calibration(scope: str | None, min_prob: float, *, market: bool = False) -> None:
    """TASK 011 §2.4: reliability of the coupon zone counted on MATCHES (JSON, read-only)."""
    from sports_edge import prospective_eval as pe

    paths, _settings, store = _context()
    policy = pe.load_policy(paths.config)
    rows = pe.market_forecasts(store)
    found = pe.calibration_report(store, policy=policy, scope=scope, min_prob=min_prob, rows=rows)
    if market:
        found["market"] = [
            r
            for r in pe.market_comparison(store, policy=policy, rows=rows)
            if scope is None or r["scope"] == scope
        ]
    _print(found)


def forecasts() -> None:
    """TASK 012 §3.1: automatic forecasts of today's and tomorrow's offer (no coupons)."""
    from sports_edge.services import run_forecasts

    paths, settings, store = _context()
    _print(run_forecasts(store, paths, settings, now=datetime.now(UTC)))


def calibration_report() -> None:
    """TASK 012 §3.2: the weekly calibration report (history entry + alerts once per fact)."""
    from sports_edge import prospective_eval as pe

    paths, _settings, store = _context()
    _print(pe.weekly_report(store, now=datetime.now(UTC), policy=pe.load_policy(paths.config)))


def market_benchmark() -> None:
    """TASK 011 §3: record the prospective STS market benchmark (append-only, idempotent)."""
    from sports_edge import prospective_eval as pe

    paths, _settings, store = _context()
    policy = pe.load_policy(paths.config)
    recorded = pe.record_market_benchmarks(store, now=datetime.now(UTC), policy=policy)
    rows = pe.market_comparison(store, policy=policy)
    _print({**recorded, "groups": [{k: r[k] for k in BENCHMARK_FIELDS} for r in rows]})


BENCHMARK_FIELDS = (
    "scope",
    "family",
    "market",
    "matches",
    "state",
    "state_label",
    "model_log_loss",
    "market_log_loss",
    "difference",
    "ci95",
)


def learning(action: str, *, force: bool = False) -> None:
    """Model review, catch-up and status reports (automation itself runs from the web UI)."""
    from sports_edge import collection, retraining, services

    paths, settings, store = _context()
    now = datetime.now(UTC)
    if action == "review":
        if not force and not retraining.due(store, now=now):
            _print({"review": "nie jest jeszcze należny (co 7 dni)"})
            return
        _print(retraining.review(store, paths, now=now, trigger="cli", force=force))
    elif action == "catch-up":

        def update():
            return services.update_results_data(store, paths, settings, now=now)

        updater = None if settings.offline_mode else update
        _print(collection.catch_up(store, now=now, update=updater))
    elif action == "status":
        _print(
            {
                "completeness": collection.completeness(store, now=now),
                "scopes": {
                    scope: retraining.scope_status(store, scope, now=now)
                    for scope in services.MODEL_SCOPES
                },
            }
        )
    elif action == "storage":
        from sports_edge import maintenance

        _print(maintenance.storage_report(store, paths))


def check() -> int:
    from sports_edge.services import startup_check
    from sports_edge.settings import AppPaths, project_root

    problems = startup_check(AppPaths(project_root()))
    for line in problems:
        print(line)
    return 1 if any(line.startswith("BŁĄD") for line in problems) else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Sports Edge AI: local analysis, paper only")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="synthetic arithmetic example (no data)")
    pdf = commands.add_parser("import-pdf", help="import an STS printed-offer PDF")
    pdf.add_argument("path", type=Path)
    sts = commands.add_parser("fetch-sts", help="download an official STS PDF (opt-in)")
    sts.add_argument("day", choices=["today", "tomorrow", "week"])
    exp = commands.add_parser("export", help="export local results")
    exp.add_argument("format", choices=["json", "csv"])
    bak = commands.add_parser("backup", help="create backups/YYYY-MM-DD_HH-MM-SS")
    bak.add_argument("--include-raw", action="store_true")
    commands.add_parser("check", help="verify configuration before starting the app")
    res = commands.add_parser("research", help="independent data, experiment, forecasts")
    res.add_argument(
        "step",
        choices=[
            "fetch-data",
            "import-data",
            "update-results",
            "experiment",
            "predict",
            "goals-experiment",
            "goals-predict",
            "protocol-v2",
            "protocol-v3",
            "walk-forward-v3",
            "ablation",
            "backfill",
            "results",
        ],
    )
    res.add_argument(
        "--scope", choices=["core", "extended"], default="core", help="model scope (leagues)"
    )
    res.add_argument(
        "--force",
        action="store_true",
        help="protocol-v2/v3: train even without new matches since the champion (marked)",
    )
    res.add_argument("--first-month", help="walk-forward-v3: first month YYYY-MM (default 2024-07)")
    res.add_argument("--out", type=Path, help="walk-forward-v3: write every forecast row to JSON")
    res.add_argument(
        "--rules",
        help="walk-forward-v3 (TASK 013): Dixon–Coles half-life rules on the same matches, "
        "comma separated (select12, select24, sticky12, sticky24, fixed); the first is 'v3'",
    )
    ana = commands.add_parser(
        "analyse", help="GENERUJ KUPONY DNIA without the interface (14 stages, saved proposals)"
    )
    ana.add_argument("day", nargs="?", help="YYYY-MM-DD (default: today, Warsaw)")
    ana.add_argument("--refresh-offer", action="store_true", help="download the STS PDF (opt-in)")
    ana.add_argument("--no-results", action="store_true", help="skip the results update")
    lrn = commands.add_parser(
        "learning", help="weekly model review, catch-up after a break, storage report"
    )
    lrn.add_argument("action", choices=["review", "catch-up", "status", "storage"])
    lrn.add_argument("--force", action="store_true", help="train even if conditions are unmet")
    commands.add_parser(
        "lineage", help="TASK 010: official coupon of the day / duplicates / versions (backfill)"
    )
    div = commands.add_parser(
        "diversity", help="TASK 010: as-of replay with the diversity limit off / 1 / 2"
    )
    div.add_argument("--days", help="YYYY-MM-DD,YYYY-MM-DD (default: every past offer day)")
    div.add_argument("--time", default="10:00", help="decision time, Warsaw (default 10:00)")
    cal = commands.add_parser(
        "calibration", help="TASK 011: coupon-zone reliability counted on matches (JSON)"
    )
    cal.add_argument("--scope", choices=["core", "extended"], help="model scope (default: all)")
    cal.add_argument("--min-prob", type=float, default=0.6, help="lowest range (default 0.6)")
    cal.add_argument("--market", action="store_true", help="add the STS market benchmark")
    commands.add_parser(
        "market-benchmark", help="TASK 011: record the prospective STS market benchmark"
    )
    commands.add_parser(
        "forecasts", help="TASK 012: forecasts of today's and tomorrow's offer, no coupons"
    )
    commands.add_parser(
        "calibration-report", help="TASK 012: weekly calibration report and its alerts"
    )
    args = parser.parse_args()
    if args.command == "demo":
        demo()
    elif args.command == "import-pdf":
        import_pdf(args.path)
    elif args.command == "fetch-sts":
        fetch_sts(args.day)
    elif args.command == "export":
        export(args.format)
    elif args.command == "backup":
        backup(args.include_raw)
    elif args.command == "check":
        sys.exit(check())
    elif args.command == "research":
        research(
            args.step,
            args.scope,
            force=args.force,
            first_month=args.first_month,
            out=args.out,
            rules=args.rules,
        )
    elif args.command == "analyse":
        analyse(args.day, refresh_offer=args.refresh_offer, refresh_results=not args.no_results)
    elif args.command == "learning":
        learning(args.action, force=args.force)
    elif args.command == "lineage":
        lineage()
    elif args.command == "diversity":
        diversity(args.days, args.time)
    elif args.command == "calibration":
        calibration(args.scope, args.min_prob, market=args.market)
    elif args.command == "market-benchmark":
        market_benchmark()
    elif args.command == "forecasts":
        forecasts()
    elif args.command == "calibration-report":
        calibration_report()


if __name__ == "__main__":
    main()
