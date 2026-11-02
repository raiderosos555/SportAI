"""Browser QA (TASK 008 §29): screenshots of the main pages on SYNTHETIC data.

``python -m sports_edge.qa_browser artifacts/qa/screenshots`` starts the interface in a
TEMPORARY home (a copy of config/ only: no database, no .env, no keys), loads the DEMO offer
(fictional teams) and saves desktop and phone screenshots with Playwright (Chromium). Every
page is checked for Streamlit exceptions; the output folder is checked for forbidden files.

Playwright is a CI tool here: ``pip install playwright`` and
``python -m playwright install chromium`` (GitHub Actions does it; nothing is downloaded by
the application itself).
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

from sports_edge.qa import FORBIDDEN, ROOT

PAGES = (
    ("", "Dashboard: kontrola przed meczem i alerty"),  # the default page lives at the root
    ("kupon", "Kupon dnia"),
    ("moj-kupon", "Mój kupon"),
    ("tasiemka", "Tasiemka"),
    ("symulator", "Symulator budżetu"),
    ("analiza", "Analiza meczu: składy i model rozszerzony"),
    ("uczenie", "Uczenie AI"),
    ("automatyzacja", "Automatyzacja AI (DEMO)"),
)
WORKER_STEPS = (
    ("URUCHOM AUTOMATYZACJĘ", "Automatyzacja: aktywna", "worker_uruchomiony"),
    ("WSTRZYMAJ AUTOMATYZACJĘ", "Automatyzacja: wstrzymana", "worker_wstrzymany"),
    ("WZNÓW AUTOMATYZACJĘ", "Automatyzacja: aktywna", "worker_wznowiony"),
    ("ZATRZYMAJ AUTOMATYZACJĘ", "Automatyzacja: zatrzymana", "worker_zatrzymany"),
)
VIEWPORTS = {"desktop": {"width": 1366, "height": 900}, "telefon": {"width": 390, "height": 844}}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _home() -> Path:
    """Temporary home with the DEMO offer in data/demo/demo.db (synthetic only)."""
    home = Path(tempfile.mkdtemp(prefix="sports_edge_browser_"))
    shutil.copytree(ROOT / "config", home / "config")
    from sports_edge.demo import demo_document
    from sports_edge.repository import ensure_reference_data
    from sports_edge.services import WARSAW, import_pdf_bytes
    from sports_edge.settings import AppPaths, load_settings, save_settings
    from sports_edge.storage import Store

    paths = AppPaths(home)
    paths.demo_database.parent.mkdir(parents=True, exist_ok=True)
    store = Store(paths.demo_database)
    ensure_reference_data(store)
    now = datetime.now(UTC)
    day = now.astimezone(WARSAW).date() + timedelta(days=1)
    import_pdf_bytes(
        store,
        paths,
        load_settings(paths),
        demo_document(day, now.astimezone(WARSAW).replace(tzinfo=None)),
        file_name="DEMO_sts.pdf",
        now=now,
        source_id="fixture",
        data_kind="demo",
    )
    store.close()
    # no internet for the whole run: the worker and the buttons never download anything
    save_settings(paths, replace(load_settings(paths), offline_mode=True))
    return home


def load_offline(home: Path) -> bool:
    from sports_edge.settings import AppPaths, load_settings

    return load_settings(AppPaths(home)).offline_mode


def _serve(home: Path, port: int) -> subprocess.Popen:
    env = {**os.environ, "SPORTS_EDGE_HOME": str(home)}
    return subprocess.Popen(  # noqa: S603 — fixed interpreter and module
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "src/sports_edge/app/main.py"),
            "--server.headless",
            "true",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(port),
            "--browser.gatherUsageStats",
            "false",
        ],
        env=env,
        cwd=str(ROOT),  # the project's .streamlit theme; the data come from the temporary home
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_ready(base: str, timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(base + "/_stcore/health", timeout=5) as response:  # noqa: S310
                if response.read().strip() == b"ok":
                    return
        except OSError:
            time.sleep(1)
    raise RuntimeError("Interfejs nie wystartował")


def _settle(page) -> None:
    """Wait until Streamlit finished running the page script."""
    page.wait_for_selector(".se-header", timeout=90_000)
    page.wait_for_function(
        "() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=90_000
    )
    time.sleep(1.5)


def _button(page, label: str):
    return page.locator("button", has_text=label).first


def _until(page, text: str, timeout: float = 120) -> bool:
    """Reload until the status shows ``text`` (the worker is a separate process)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        page.reload()
        _settle(page)
        if page.get_by_text(text).count():
            return True
        time.sleep(2)
    return False


def capture(out: Path) -> dict:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    home = _home()
    port = _free_port()
    server = _serve(home, port)
    base = f"http://127.0.0.1:{port}"
    shots: list[dict] = []

    def shot(page, name: str, title: str, view: str, ok: bool = True) -> None:
        errors = page.locator('[data-testid="stException"]').count()
        page.screenshot(path=str(out / f"{name}.png"), full_page=True)
        shots.append(
            {
                "page": title,
                "view": view,
                "file": f"{name}.png",
                "ok": ok and errors == 0,
                "exceptions": errors,
            }
        )

    try:
        _wait_ready(base)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for view, size in VIEWPORTS.items():
                page = browser.new_page(viewport=size, locale="pl-PL")
                for path, title in PAGES:
                    page.goto(f"{base}/{path}?demo=1")
                    _settle(page)
                    shot(page, f"{view}_{path or 'dashboard'}", title, view)
                page.close()
            page = browser.new_page(viewport=VIEWPORTS["desktop"], locale="pl-PL")
            # no internet: the analysis runs without downloads (DEMO offer)
            page.goto(f"{base}/kupon?demo=1")
            _settle(page)
            _button(page, "ANALIZUJ MECZE I GENERUJ KUPONY").click()
            _settle(page)
            shot(page, "desktop_kupon_bez_internetu", "Kupon dnia bez internetu", "desktop")
            # the real (temporary, empty) home: the worker driven from the panel
            page.goto(f"{base}/automatyzacja")
            _settle(page)
            _button(page, "ZAPISZ USTAWIENIA").click()
            page.get_by_text("Ustawienia zapisane").wait_for(timeout=60_000)
            shot(page, "desktop_automatyzacja_zapis", "Automatyzacja AI: zapis ustawień", "desktop")
            for label, expected, name in WORKER_STEPS:
                _button(page, label).click()
                reached = _until(page, expected)
                shot(
                    page,
                    f"desktop_{name}",
                    f"Automatyzacja AI: {label.lower()}",
                    "desktop",
                    ok=reached,
                )
            page.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
        time.sleep(3)  # the worker notices the server is gone and ends
        shutil.rmtree(home, ignore_errors=True)
    (out / "screenshots.json").write_text(
        json.dumps(
            {"created_at": datetime.now(UTC).isoformat(), "screenshots": shots},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    leaks = [str(p) for p in out.rglob("*") if p.suffix.lower() in FORBIDDEN or p.name == ".env"]
    if leaks:
        raise RuntimeError("Artefakt QA zawiera niedozwolone pliki: " + ", ".join(leaks))
    return {"screenshots": len(shots), "failed": sum(not s["ok"] for s in shots)}


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/qa/screenshots")
    summary = capture(target)
    print(json.dumps(summary))
    sys.exit(1 if summary["failed"] else 0)
