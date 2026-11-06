"""QA paths of the interface and the CI artifact (TASK 007 §28).

``python -m sports_edge.qa artifacts/qa`` replays the main user paths in a TEMPORARY home
with SYNTHETIC data (the DEMO offer with fictional teams) and writes:

* ``ui_report.json`` — every path, ok / error (one sentence, no traceback);
* ``versions.txt`` — library versions;
* ``fixtures/demo_sts.pdf`` — the synthetic STS-like document the paths used;
* ``README.md`` — a short summary.

The artifact never contains a real database, keys, .env, session data or tokens: the
output folder is checked before the run ends. Screenshots need a browser and are not taken.
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = (".db", ".sqlite", ".sqlite3", ".env", ".key", ".pem")
PACKAGES = ("streamlit", "numpy", "pandas", "pymupdf", "altair", "pytest", "ruff")


def _home(config: Path) -> Path:
    home = Path(tempfile.mkdtemp(prefix="sports_edge_qa_"))
    shutil.copytree(config, home / "config")
    return home


def _app(script: str, demo: bool = True):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(script, default_timeout=90)
    app.session_state["demo_mode"] = demo
    return app


def _page(name: str) -> str:
    modules = {
        "dashboard": ("page_dashboard", "dashboard_page"),
        "kupon": ("page_coupon", "coupon_page"),
        "moj_kupon": ("page_my_coupon", "my_coupon_page"),
        "generator": ("page_generator", "generator_page"),
        "tasiemka": ("page_generator", "tasiemka_page"),
        "budzet": ("page_simulators", "budget_page"),
        "historyczny": ("page_simulators", "historical_page"),
        "historia": ("pages_history", "history_page"),
        "uczenie": ("page_learning", "learning_page"),
        "zrodla": ("page_sources", "sources_page"),
        "analiza": ("page_match", "match_page"),
        "automatyzacja": ("page_automation", "automation_page"),
        "modele": ("page_models", "models_page"),
    }
    module, function = modules[name]
    return f"from sports_edge.app.{module} import {function}; {function}()"


def _errors(app) -> list[str]:
    return [str(e.value).splitlines()[0][:200] for e in app.exception]


def _button(app, label: str):
    return next(b for b in app.button if b.label.startswith(label))


def ui_paths(config: Path | None = None) -> list[dict]:
    """Run the 20 paths; returns [{"path", "ok", "detail"}]."""
    config = config or ROOT / "config"
    results: list[dict] = []
    previous = os.environ.get("SPORTS_EDGE_HOME")

    def record(path: str, ok: bool, detail: str = "") -> None:
        results.append({"path": path, "ok": bool(ok), "detail": detail})

    def check(path: str, app, expect=None) -> None:
        errors = _errors(app)
        text = (
            " ".join(m.value for m in app.markdown)
            + " ".join(
                str(x.value)
                for x in [*app.caption, *app.warning, *app.error, *app.info, *app.success]
            )
            + " ".join(d.value.to_string() for d in app.dataframe)
        )
        if errors:
            record(path, False, errors[0])
        elif expect and not any(e in text for e in expect):
            record(path, False, f"brak oczekiwanego tekstu: {expect}")
        else:
            record(path, True)

    try:
        # ------------------------------------------------ 13. no data (empty home)
        os.environ["SPORTS_EDGE_HOME"] = str(_home(config))
        for name in ("dashboard", "moj_kupon", "uczenie"):
            app = _app(_page(name), demo=False)
            app.run()
            check(f"brak danych: {name}", app, ["Brak", "brak"])
        # ------------------------------------------------ AUTOMATYZACJA AI (temporary home)
        panel = _app(_page("automatyzacja"), demo=False)
        panel.run()
        check("automatyzacja: status", panel, ["zatrzymana"])
        _button(panel, "ZAPISZ USTAWIENIA").click().run()
        check("automatyzacja: zapis ustawień", panel, ["Ustawienia zapisane"])
        # ------------------------------------------------ DEMO home with a fresh offer
        os.environ["SPORTS_EDGE_HOME"] = str(_home(config))
        loader = _app("from sports_edge.app import common as c; c.load_demo_data()")
        loader.run()
        check("start: dane DEMO", loader)
        start = _app(Path(ROOT / "src/sports_edge/app/main.py").read_text(encoding="utf-8"))
        start.run()
        check("start aplikacji (nawigacja)", start)
        for name, label in (
            ("dashboard", "dashboard"),
            ("kupon", "kupon dnia"),
            ("historia", "historia"),
            ("uczenie", "Uczenie AI"),
        ):
            app = _app(_page(name))
            app.run()
            check(label, app)
        app = _app(_page("kupon"))
        app.run()
        if app.button:
            app.button[0].click().run()  # offline: analysis without downloads
        check("brak internetu (tryb offline)", app, ["offline", "Brak", "Kupon", "kupon"])
        mine = _app(_page("moj_kupon"))
        mine.run()
        check("mój kupon", mine)
        adds = [b for b in mine.button if b.label == "Dodaj"]
        if adds:
            adds[0].click().run()
        legs = (
            mine.session_state["my_coupon_cart"]["legs"]
            if "my_coupon_cart" in mine.session_state
            else []
        )
        record(
            "dodanie selekcji",
            bool(legs) and not _errors(mine),
            "" if legs else "kupon pusty po kliknięciu",
        )
        mine.number_input[0].set_value(25.0).run()
        record(
            "zmiana stawki",
            mine.session_state["my_coupon_cart"]["stake"] == "25.00" and not _errors(mine),
        )
        _button(mine, "OCEŃ MÓJ KUPON AI").click().run()
        check("ocena AI", mine, ["Szacowana szansa", "Nie można wiarygodnie"])
        check("brak prognozy", mine, ["Brak zwalidowanego modelu"])
        gen = _app(_page("generator"))
        gen.run()
        _button(gen, "ZBUDUJ KUPON NA KURS").click().run()
        check("kurs docelowy", gen, ["Brak kuponu", "Najlepszy kupon"])
        tape = _app(_page("tasiemka"))
        tape.run()
        _button(tape, "ZBUDUJ KUPON NA KURS").click().run()
        check("tasiemka", tape, ["Brak kuponu", "Najlepszy kupon"])
        budget = _app(_page("budzet"))
        budget.run()
        budget.radio[0].set_value("manual").run()
        _button(budget, "URUCHOM SYMULACJĘ").click().run()
        check("symulator budżetu", budget, ["Symulacja warunkowa", "warunkowa"])
        # ------------------------------------------------ stale odds: offer received 6 h ago
        os.environ["SPORTS_EDGE_HOME"] = str(_home(config))
        stale = _app(
            "from datetime import timedelta\n"
            "from sports_edge.app import common as c\n"
            "from sports_edge.demo import demo_document\n"
            "from sports_edge.services import import_pdf_bytes\n"
            "earlier = c.now() - timedelta(hours=6)\n"
            "day = c.today_local() + timedelta(days=1)\n"
            "import_pdf_bytes(c.store(), c.paths(), c.settings(), demo_document(day, "
            "earlier.astimezone(c.WARSAW).replace(tzinfo=None)), file_name='DEMO_stale.pdf', "
            "now=earlier, source_id='fixture', data_kind='demo')\n"
        )
        stale.run()
        page = _app(_page("moj_kupon"))
        page.run()
        check("nieaktualne kursy", page, ["Kurs niepotwierdzony", "niepotwierdzony"])
        # ------------------------------------------------ results source failure
        failing = _app(
            "from sports_edge import collection\n"
            "from sports_edge.app import common as c\n"
            "def broken():\n"
            "    raise ConnectionError('źródło wyników niedostępne')\n"
            "collection.catch_up(c.store(), now=c.now(), update=broken)\n"
            "from sports_edge.app.page_sources import _monitoring\n"
            "_monitoring()\n"
        )
        failing.run()
        check("awaria źródła wyników", failing, ["failed", "ConnectionError"])
        # ------------------------------------------------ TASK 009: cups and national teams
        cups = _app(_page("analiza"))
        cups.session_state["match_category"] = "european"
        cups.run()
        check("analiza: europejskie puchary", cups, ["model predykcyjny jeszcze niezwalidowany"])
        sources = _app(_page("zrodla"))
        sources.session_state["sources_view"] = "Puchary i reprezentacje"
        sources.run()
        check("źródła: puchary i reprezentacje", sources, ["Europejskie puchary i reprezentacje"])
        models = _app(_page("modele"))
        models.session_state["models_view"] = "Puchary i reprezentacje"
        models.run()
        check("modele: puchary i reprezentacje", models, ["Brak eksperymentu międzynarodowego"])
    finally:
        if previous is None:
            os.environ.pop("SPORTS_EDGE_HOME", None)
        else:
            os.environ["SPORTS_EDGE_HOME"] = previous
    return results


def write_artifact(out: Path) -> dict:
    from sports_edge.demo import demo_document

    out.mkdir(parents=True, exist_ok=True)
    paths = ui_paths()
    (out / "ui_report.json").write_text(
        json.dumps(
            {"created_at": datetime.now(UTC).isoformat(), "paths": paths},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    versions = []
    for name in PACKAGES:
        try:
            versions.append(f"{name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            versions.append(f"{name}: brak")
    versions.append(f"python=={sys.version.split()[0]}")
    (out / "versions.txt").write_text("\n".join(versions) + "\n", encoding="utf-8")
    fixtures = out / "fixtures"
    fixtures.mkdir(exist_ok=True)
    day = (datetime.now(UTC) + timedelta(days=1)).date()
    (fixtures / "demo_sts.pdf").write_bytes(
        demo_document(day, datetime.now(UTC).replace(tzinfo=None))
    )
    failed = [p for p in paths if not p["ok"]]
    (out / "README.md").write_text(
        "# Raport QA interfejsu (dane syntetyczne)\n\n"
        f"Ścieżki: {len(paths)}, zaliczone: {len(paths) - len(failed)}, błędy: {len(failed)}.\n\n"
        + "".join(f"- {'OK' if p['ok'] else 'BŁĄD'} — {p['path']} {p['detail']}\n" for p in paths)
        + "\nZrzuty ekranu (Playwright, desktop i telefon): katalog screenshots/ — w CI.\n",
        encoding="utf-8",
    )
    leaks = [str(p) for p in out.rglob("*") if p.suffix.lower() in FORBIDDEN or p.name == ".env"]
    if leaks:
        raise RuntimeError("Artefakt QA zawiera niedozwolone pliki: " + ", ".join(leaks))
    return {"paths": len(paths), "failed": len(failed)}


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/qa")
    summary = write_artifact(target)
    print(json.dumps(summary))
    sys.exit(1 if summary["failed"] else 0)
