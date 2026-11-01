import argparse
import json
import sys
from datetime import UTC, date, datetime
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


def research(step: str, scope: str = "core") -> None:
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
            "backfill",
            "results",
        ],
    )
    res.add_argument(
        "--scope", choices=["core", "extended"], default="core", help="model scope (leagues)"
    )
    ana = commands.add_parser(
        "analyse", help="GENERUJ KUPONY DNIA without the interface (14 stages, saved proposals)"
    )
    ana.add_argument("day", nargs="?", help="YYYY-MM-DD (default: today, Warsaw)")
    ana.add_argument("--refresh-offer", action="store_true", help="download the STS PDF (opt-in)")
    ana.add_argument("--no-results", action="store_true", help="skip the results update")
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
        research(args.step, args.scope)
    elif args.command == "analyse":
        analyse(args.day, refresh_offer=args.refresh_offer, refresh_results=not args.no_results)


if __name__ == "__main__":
    main()
