"""Optional language layer (TASK 007 §7): a LOCAL, free model explains a coupon.

The model runs on this computer through Ollama (https://ollama.com — free, no account, no
key, works offline). The application talks ONLY to Ollama on this computer: any other host
is refused, so the fact sheet never leaves the computer and nothing is paid for.

OFF by default; every question starts with a click. Sent to the local model: a small fact
sheet of ONE coupon built from the advisor report (matches, picks, odds, model
probabilities, statuses, risks) and the question — never the database, keys, file paths or
settings. The model may only explain these facts: every number of its answer is checked
against the fact sheet, and any problem (Ollama not running, model not downloaded, timeout,
invalid answer, unknown numbers) falls back to the template explainer.

Default model: Bielik 11B v2.3 Instruct (SpeakLeash, Polish, Apache 2.0 + terms of use
bielik.ai/terms), downloaded once with ``ollama pull`` (Q4_K_M ≈ 6.7 GB).
"""

import json
import re
from datetime import datetime
from decimal import Decimal
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sports_edge.ingestion.types import digest, iso
from sports_edge.storage import Store

PROVIDER = "ollama"
DEFAULT_MODEL = "hf.co/speakleash/Bielik-11B-v2.3-Instruct-GGUF:Q4_K_M"
DEFAULT_URL = "http://127.0.0.1:11434"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
TIMEOUT = 180  # seconds; the first answer also loads the model into memory
ALTERNATIVES = (
    ("hf.co/speakleash/Bielik-11B-v2.3-Instruct-GGUF:Q4_K_M", "Bielik 11B (polski), ≈ 6,7 GB"),
    ("hf.co/speakleash/Bielik-11B-v2.3-Instruct-GGUF:Q8_0", "Bielik 11B, wyższa jakość, ≈ 12 GB"),
    ("gemma3:12b", "Gemma 3 12B (wielojęzyczny), ≈ 8 GB"),
    ("qwen2.5:14b", "Qwen 2.5 14B (wielojęzyczny), ≈ 9 GB"),
)
SYSTEM = (
    "Jesteś objaśniaczem w polskiej aplikacji analitycznej do kuponów STS. Odpowiadasz po "
    "polsku, krótko i rzeczowo, WYŁĄCZNIE na podstawie faktów z sekcji FAKTY. Nie liczysz "
    "nowych prawdopodobieństw, nie zgadujesz, nie obiecujesz wygranej i nie zachęcasz do "
    "gry. Gdy faktów brakuje, napisz, czego brakuje. Każdą liczbę przepisuj dokładnie z "
    "faktów. Łączna szansa kuponu AKO jest przybliżeniem (zakłada niezależność meczów) — "
    "zaznacz to, jeśli o niej mówisz. Odpowiedz w formacie JSON zgodnym ze schematem."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "facts_used": {"type": "array", "items": {"type": "string"}},
        "missing": {"type": "string"},
    },
    "required": ["answer", "facts_used", "missing"],
}


def fact_sheet(report: dict) -> dict:
    """The only data given to the model (no identifiers, no database content)."""
    s = report["summary"]
    return {
        "kupon": {
            "selekcje": s["legs"],
            "kurs_laczny": str(s["total_odds"]),
            "stawka_zl": str(s["stake"]),
            "wyplata_zl": str(s.get("payout")),
            "zysk_netto_zl": str(s.get("net_profit")),
            "szansa_model": _pct(s["probability"]),
            "szansa_ostroznie": _pct(s["probability_low"]),
            "uwaga_o_szansie": s["probability_note"],
            "prog_oplacalnosci": _pct(s.get("break_even")),
        },
        "typy": [
            {
                "nr": i + 1,
                "mecz": r["event"],
                "typ": r["pick"],
                "kurs": str(r["odds"]),
                "szansa_model": _pct(r.get("p_model")),
                "szansa_ostroznie": _pct(r.get("p_low")),
                "status": r["status"],
                "kalibracja": r.get("calibration"),
                "ryzyka": r["risks"],
            }
            for i, r in enumerate(report["legs"])
        ],
        "najslabsze_ogniwo": None if report["weakest"] is None else report["weakest"] + 1,
        "powod": report["weakest_reason"],
    }


def _pct(value) -> str | None:
    return None if value is None else f"{value * 100:.1f}%".replace(".", ",")


def _numbers(text: str) -> set[str]:
    """Numbers of a text in one spelling: 75,80 → 75.8; integers keep their zeros."""
    out = set()
    for found in re.findall(r"\d+(?:[.,]\d+)?", text):
        found = found.replace(",", ".")
        out.add(found.rstrip("0").rstrip(".") if "." in found else found)
    return out


def unknown_numbers(answer: str, facts: dict) -> list[str]:
    """Numbers of the answer that do not occur in the fact sheet (1–10 are allowed: legs)."""
    known = _numbers(json.dumps(facts, ensure_ascii=False))
    return sorted(
        n for n in _numbers(answer) if n not in known and not (n.isdigit() and int(n) <= 10)
    )


def local_url(url: str) -> str:
    """Base URL of Ollama, accepted only on this computer (no data may leave it)."""
    parsed = urlparse(url or DEFAULT_URL)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("Dozwolony jest wyłącznie lokalny model na tym komputerze (127.0.0.1)")
    return f"http://{parsed.hostname}:{parsed.port or 11434}"


def _call(url: str, path: str, payload: dict | None = None, *, opener=None, timeout=10) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        local_url(url) + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with (opener or urlopen)(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def installed_models(settings, *, opener=None) -> list[str] | None:
    """Models downloaded into the local Ollama; None when Ollama does not answer."""
    try:
        found = _call(settings.llm_url, "/api/tags", opener=opener, timeout=5)
    except (URLError, OSError, ValueError):
        return None
    return [m.get("name", "") for m in found.get("models", [])]


def _matches(model: str, names: list[str]) -> bool:
    return model in names or f"{model}:latest" in names


def available(settings, *, opener=None) -> tuple[bool, str]:
    """(usable, reason) — told to the user in plain words."""
    if not settings.llm_enabled:
        return False, "Lokalny model AI jest wyłączony (Źródła i ustawienia → Ustawienia)."
    try:
        local_url(settings.llm_url)
    except ValueError as error:
        return False, str(error)
    names = installed_models(settings, opener=opener)
    if names is None:
        return False, (
            "Ollama nie działa na tym komputerze — zainstaluj ją z ollama.com i uruchom "
            "(aplikacja Ollama w zasobniku systemowym)."
        )
    if not _matches(settings.llm_model, names):
        return False, f"Model nie jest pobrany — w terminalu: ollama pull {settings.llm_model}"
    return True, "gotowe"


def ask(
    question: str,
    report: dict,
    *,
    store: Store,
    settings,
    now: datetime,
    opener=None,
) -> dict:
    """Explanation by the local model, checked against the facts. Never raises: any
    problem returns {"ok": False, "reason": ...} and the template answer is used instead."""
    usable, reason = available(settings, opener=opener)
    if not usable:
        return {"ok": False, "reason": reason}
    facts = fact_sheet(report)
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": "FAKTY:\n"
                + json.dumps(facts, ensure_ascii=False, indent=1)
                + f"\n\nPYTANIE: {question}",
            },
        ],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0.1, "num_ctx": 8192},
        "keep_alive": "10m",
    }
    try:
        response = _call(settings.llm_url, "/api/chat", payload, opener=opener, timeout=TIMEOUT)
    except (URLError, OSError, ValueError) as error:
        return {"ok": False, "reason": f"Lokalny model nie odpowiedział ({type(error).__name__})."}
    store.insert(
        "llm_usage",
        id=digest(["llm", iso(now), question, settings.llm_model]),
        created_at=iso(now),
        provider=PROVIDER,
        model=settings.llm_model,
        purpose="coupon_question",
        input_tokens=int(response.get("prompt_eval_count") or 0),
        output_tokens=int(response.get("eval_count") or 0),
        cache_read_tokens=0,
        cost_usd="0",  # a local model costs nothing per question
        status=str(response.get("done_reason") or ("stop" if response.get("done") else "?")),
    )
    text = (response.get("message") or {}).get("content", "")
    try:
        data = json.loads(text)
    except ValueError:
        return {"ok": False, "reason": "Lokalny model zwrócił odpowiedź w niepoprawnym formacie."}
    answer = str(data.get("answer", "")).strip()
    if not answer:
        return {"ok": False, "reason": "Lokalny model nie udzielił odpowiedzi."}
    unknown = unknown_numbers(answer, facts)
    seconds = (response.get("total_duration") or 0) / 1e9
    return {
        "ok": not unknown,
        "answer": answer,
        "missing": str(data.get("missing", "")),
        "unknown_numbers": unknown,
        "reason": "Odpowiedź zawiera liczby spoza danych: " + ", ".join(unknown) if unknown else "",
        "seconds": round(seconds, 1),
        "cost_usd": Decimal("0"),
        "source": f"lokalny model {settings.llm_model} (Ollama, bezpłatnie)",
    }
