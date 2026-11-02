"""Visual QA (TASK 009): every main page at five screen sizes, with layout assertions.

``python -m sports_edge.qa_visual artifacts/qa/visual`` starts the interface in a TEMPORARY
home with the SYNTHETIC DEMO offer (fictional teams, also very long club and league names)
and checks each page at 1920×1080, 1366×768, 1024×768, 768×1024 and 390×844:

* no horizontal overflow of the page and no element outside the screen edges;
* no word, odds, percentage or date split across lines (letters stacked vertically);
* cards not narrower than a readable minimum;
* buttons visible and not squeezed;
* tabs switch without errors; no Streamlit exception on any page.

Screenshots and ``visual_report.json`` are written to the output folder (the QA artifact
never contains a database, .env or keys). ``--home PATH`` reviews an existing home
instead (local review on a COPY of real data; never used in CI).
"""

import json
import re
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sports_edge.qa import FORBIDDEN
from sports_edge.qa_browser import _free_port, _home, _serve, _settle, _wait_ready

VIEWPORTS = {
    "1920x1080": (1920, 1080),
    "1366x768": (1366, 768),
    "1024x768": (1024, 768),
    "768x1024": (768, 1024),
    "390x844": (390, 844),
}
PAGES = (
    ("", "Dashboard"),
    ("kupon", "Kupon dnia"),
    ("moj-kupon", "Mój kupon"),
    ("kupon-na-kurs", "Kupon na kurs"),
    ("tasiemka", "Tasiemka"),
    ("analiza", "Analiza meczów"),
    ("wydarzenia", "Wydarzenia i wyniki"),
    ("symulator", "Symulator budżetu"),
    ("symulator-historyczny", "Symulator historyczny"),
    ("historia", "Historia i skuteczność"),
    ("modele", "Modele i statystyki"),
    ("uczenie", "Uczenie AI"),
    ("automatyzacja", "Automatyzacja AI"),
    ("zrodla", "Źródła i ustawienia"),
)
MIN_CARD = 200  # px: a card narrower than this cannot show a label and a number side by side

CHECK = r"""
() => {
  const vw = document.documentElement.clientWidth;
  const main = document.querySelector('[data-testid="stMain"]') || document.body;
  const out = {overflow: Math.max(0, document.documentElement.scrollWidth - vw),
               outside: [], broken: [], narrow: [], buttons: []};
  const label = (el) => (el.innerText || el.getAttribute('aria-label') || el.tagName)
    .trim().replace(/\s+/g, ' ').slice(0, 60);
  const scrolls = (el) => {
    for (let p = el.parentElement; p && p !== main; p = p.parentElement) {
      if (/(auto|scroll|hidden)/.test(getComputedStyle(p).overflowX)) return true;
    }
    return false;
  };
  for (const el of main.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if ((r.right > vw + 1 || r.left < -1) && !scrolls(el)) {
      out.outside.push(label(el));
    }
  }
  // a word or a number (odds 1,90 · 84,2% · 02.11.2026 · 18:00) must stay on one line
  const word = /[\p{L}\p{N}](?:[\p{L}\p{N}]|[.,:%](?=[\p{L}\p{N}%]))*%?/gu;
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const parent = node.parentElement;
    if (!parent || parent.closest('code, pre, [data-testid="stCode"], canvas')) continue;
    for (const m of node.data.matchAll(word)) {
      const text = m[0];
      if (text.length < 3 || text.length > 24) continue;
      const range = document.createRange();
      range.setStart(node, m.index);
      range.setEnd(node, m.index + text.length);
      const rects = [...range.getClientRects()].filter((r) => r.width > 0.5);
      if (new Set(rects.map((r) => Math.round(r.top))).size > 1) out.broken.push(text);
    }
  }
  for (const el of main.querySelectorAll('.se-card')) {
    const w = el.getBoundingClientRect().width;
    if (w > 0 && w < Math.min(MIN_CARD, vw - 40)) out.narrow.push(Math.round(w));
  }
  const appButtons = 'button[data-testid="stBaseButton-primary"], ' +
    'button[data-testid="stBaseButton-secondary"], button[data-testid="stBaseButton-tertiary"]';
  for (const b of main.querySelectorAll(appButtons)) {
    const r = b.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.right > vw + 1 || r.width < 28 || r.height < 24) out.buttons.push(label(b));
    const text = b.querySelector('p, [data-testid="stMarkdownContainer"]');
    if (text && text.scrollWidth > text.clientWidth + 1) out.buttons.push('ucięty: ' + label(b));
  }
  out.outside = [...new Set(out.outside)].slice(0, 12);
  out.broken = [...new Set(out.broken)].slice(0, 20);
  out.buttons = [...new Set(out.buttons)].slice(0, 12);
  return out;
}
""".replace("MIN_CARD", str(MIN_CARD))


def _tabs(page, shot: Path | None = None) -> dict:
    """Click every visible tab; each must become selected without an error. With ``shot``
    (pages with a few tabs) every tab is also photographed for the manual review."""
    tabs = page.locator('[role="tab"]')
    count = tabs.count()
    visible = [i for i in range(count) if tabs.nth(i).is_visible()]
    failed = []
    for n, i in enumerate(visible):
        tab = tabs.nth(i)
        name = tab.inner_text().strip()[:40]
        tab.click()
        time.sleep(0.8)
        if tab.get_attribute("aria-selected") != "true":
            failed.append(name)
        if shot is not None and len(visible) <= 7:
            _full_screenshot(page, shot.with_name(f"{shot.stem}_tab{n + 1}.png"))
    errors = page.locator('[data-testid="stException"]').count()
    return {"tabs": count, "visible": len(visible), "failed": failed, "exceptions": errors}


def _full_screenshot(page, shot: Path) -> None:
    """Streamlit scrolls inside its main container: the viewport grows to the content height
    for the screenshot only (the width — and so the layout — stays the same)."""
    size = page.viewport_size
    height = page.evaluate(
        "() => { const m = document.querySelector('[data-testid=\"stMain\"]');"
        " return Math.ceil(m ? m.scrollHeight : document.body.scrollHeight); }"
    )
    page.set_viewport_size(
        {"width": size["width"], "height": max(size["height"], min(height, 14000))}
    )
    time.sleep(0.8)
    page.screenshot(path=str(shot))
    page.set_viewport_size(size)


def check_page(page, url: str, shot: Path, *, tabs: bool) -> dict:
    page.goto(url)
    _settle(page)
    found = page.evaluate(CHECK)
    found["exceptions"] = page.locator('[data-testid="stException"]').count()
    _full_screenshot(page, shot)
    if tabs:
        found["tab_check"] = _tabs(page, shot)
    found["ok"] = (
        found["overflow"] <= 1
        and not found["outside"]
        and not found["broken"]
        and not found["narrow"]
        and not found["buttons"]
        and not found["exceptions"]
        and not (found.get("tab_check") or {}).get("failed")
        and not (found.get("tab_check") or {}).get("exceptions")
    )
    return found


def _assess(page) -> dict:
    found = page.evaluate(CHECK)
    found["exceptions"] = page.locator('[data-testid="stException"]').count()
    found["ok"] = (
        found["overflow"] <= 1
        and not found["outside"]
        and not found["broken"]
        and not found["narrow"]
        and not found["buttons"]
        and not found["exceptions"]
    )
    return found


def _scenario_my_coupon(page, base: str, shot: Path) -> dict:
    """Add a selection from the offer, evaluate the coupon with AI, open every tab."""
    page.goto(f"{base}/moj-kupon?demo=1")
    _settle(page)
    page.locator('[data-testid="stExpander"] summary').first.click()  # open the first market
    time.sleep(0.8)
    page.get_by_role("button", name=re.compile(r"Dodaj$")).first.click()
    _settle(page)
    page.locator("button", has_text="OCEŃ MÓJ KUPON AI").first.click()
    _settle(page)
    found = _assess(page)
    _full_screenshot(page, shot)
    found["tab_check"] = _tabs(page, shot)
    found["ok"] = found["ok"] and not found["tab_check"]["failed"]
    return found


def _scenario_long_names(page, base: str, shot: Path) -> dict:
    """The match with the longest names: the header must wrap between words."""
    page.goto(f"{base}/analiza?demo=1")
    _settle(page)
    page.get_by_text("Tylko z prognozą", exact=True).click()
    _settle(page)
    page.locator('[data-testid="stSelectbox"]').filter(has_text="Wydarzenie").click()
    page.get_by_role("option", name=re.compile("Wysokogórskie")).first.click()
    _settle(page)
    found = _assess(page)
    found["long_name_visible"] = page.get_by_text("Wysokogórskie Towarzystwo").count() > 0
    found["ok"] = found["ok"] and found["long_name_visible"]
    _full_screenshot(page, shot)
    return found


def _scenario_analysis(page, base: str, shot: Path) -> dict:
    """Kupon dnia without internet: the analysis explains the result, nothing is downloaded."""
    page.goto(f"{base}/kupon?demo=1")
    _settle(page)
    page.locator("button", has_text="ANALIZUJ MECZE I GENERUJ KUPONY").first.click()
    _settle(page)
    found = _assess(page)
    _full_screenshot(page, shot)
    return found


SCENARIOS = (
    ("moj_kupon_ocena", "Mój kupon: dodanie typu i ocena AI", _scenario_my_coupon),
    ("dlugie_nazwy", "Analiza meczu: długie nazwy klubu i ligi", _scenario_long_names),
    ("kupon_analiza", "Kupon dnia: analiza bez internetu", _scenario_analysis),
)


def run(out: Path, *, home: Path | None = None, viewports=None, pages=None) -> dict:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    temporary = home is None
    home = home or _home()
    port = _free_port()
    server = _serve(home, port)
    base = f"http://127.0.0.1:{port}"
    results = []
    demo = "?demo=1" if temporary else ""
    try:
        _wait_ready(base)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for view in viewports or VIEWPORTS:
                width, height = VIEWPORTS[view]
                page = browser.new_page(viewport={"width": width, "height": height}, locale="pl-PL")
                for path, title in pages or PAGES:
                    name = f"{view}_{path or 'dashboard'}.png"
                    try:
                        found = check_page(
                            page, f"{base}/{path}{demo}", out / name, tabs=view == "1366x768"
                        )
                    except Exception as error:  # noqa: BLE001 — a failed page is a result
                        found = {"ok": False, "error": f"{type(error).__name__}: {error}"[:300]}
                    results.append(
                        {"page": title, "path": path, "view": view, "file": name, **found}
                    )
                if temporary and view in ("1366x768", "390x844"):
                    for key, title, scenario in SCENARIOS:
                        name = f"{view}_scenariusz_{key}.png"
                        try:
                            found = scenario(page, base, out / name)
                        except Exception as error:  # noqa: BLE001 — a failed step is a result
                            found = {"ok": False, "error": f"{type(error).__name__}: {error}"[:300]}
                        results.append(
                            {
                                "page": title,
                                "path": key,
                                "view": view,
                                "file": name,
                                "scenario": True,
                                **found,
                            }
                        )
                page.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except Exception:  # noqa: BLE001
            server.kill()
        if temporary:
            shutil.rmtree(home, ignore_errors=True)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "viewports": list(viewports or VIEWPORTS),
        "results": results,
        "failed": [f"{r['view']} {r['page']}" for r in results if not r["ok"]],
    }
    (out / "visual_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    leaks = [str(p) for p in out.rglob("*") if p.suffix.lower() in FORBIDDEN or p.name == ".env"]
    if leaks:
        raise RuntimeError("Artefakt QA zawiera niedozwolone pliki: " + ", ".join(leaks))
    return {"checks": len(results), "failed": len(report["failed"])}


if __name__ == "__main__":
    args = sys.argv[1:]
    home = None
    if "--home" in args:
        i = args.index("--home")
        home = Path(args[i + 1])
        del args[i : i + 2]
    views = None
    if "--views" in args:
        i = args.index("--views")
        views = args[i + 1].split(",")
        del args[i : i + 2]
    only = None
    if "--pages" in args:
        i = args.index("--pages")
        wanted = args[i + 1].split(",")
        only = [p for p in PAGES if (p[0] or "dashboard") in wanted]
        del args[i : i + 2]
    target = Path(args[0] if args else "artifacts/qa/visual")
    summary = run(target, home=home, viewports=views, pages=only)
    print(json.dumps(summary))
    sys.exit(1 if summary["failed"] else 0)
