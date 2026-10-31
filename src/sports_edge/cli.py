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
    from sports_edge.repository import ensure_reference_data
    from sports_edge.settings import AppPaths, load_settings, project_root
    from sports_edge.storage import Store

    paths = AppPaths(project_root())
    store = Store(paths.database)
    ensure_reference_data(store)
    return paths, load_settings(paths), store


def import_pdf(path: Path, bookmaker: str | None) -> None:
    from sports_edge.services import import_pdf_bytes

    paths, settings, store = _context()
    outcome = import_pdf_bytes(
        store,
        paths,
        settings,
        path.read_bytes(),
        file_name=path.name,
        bookmaker=bookmaker,
        now=datetime.now(UTC),
    )
    print(
        json.dumps(
            {"duplicate": outcome.duplicate, **outcome.summary}, indent=2, ensure_ascii=False
        )
    )


def fetch_sts(day: str) -> None:
    from sports_edge.services import fetch_sts_offer

    paths, settings, store = _context()
    outcome = fetch_sts_offer(store, paths, settings, day, now=datetime.now(UTC))
    print(
        json.dumps(
            {"duplicate": outcome.duplicate, **outcome.summary}, indent=2, ensure_ascii=False
        )
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
    pdf = commands.add_parser("import-pdf", help="import an STS/Superbet PDF offer")
    pdf.add_argument("path", type=Path)
    pdf.add_argument("--bookmaker", choices=["sts", "superbet"], default=None)
    sts = commands.add_parser("fetch-sts", help="download an official STS PDF (opt-in)")
    sts.add_argument("day", choices=["today", "tomorrow", "week"])
    exp = commands.add_parser("export", help="export local results")
    exp.add_argument("format", choices=["json", "csv"])
    bak = commands.add_parser("backup", help="create backups/YYYY-MM-DD_HH-MM-SS")
    bak.add_argument("--include-raw", action="store_true")
    commands.add_parser("check", help="verify configuration before starting the app")
    args = parser.parse_args()
    if args.command == "demo":
        demo()
    elif args.command == "import-pdf":
        import_pdf(args.path, args.bookmaker)
    elif args.command == "fetch-sts":
        fetch_sts(args.day)
    elif args.command == "export":
        export(args.format)
    elif args.command == "backup":
        backup(args.include_raw)
    elif args.command == "check":
        sys.exit(check())


if __name__ == "__main__":
    main()
