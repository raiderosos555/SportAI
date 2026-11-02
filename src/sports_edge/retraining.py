"""Retraining engine and champion–challenger comparison (TASK 007 §P5).

A REVIEW is due every ``every_days`` (default 7). It trains a challenger of a scope only
when the minimum conditions hold — enough new results since the champion was trained, no
challenger already waiting, enough days since the last training — and otherwise records
why it skipped. A challenger is trained under protocol v2 (decisions on the selection
window, the final holdout reported only), runs in shadow mode and is promoted only by the
user (AKTYWUJ NOWY MODEL / POZOSTAW POPRZEDNI MODEL). Automatic promotion does not exist.
"""

import json
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta

from sports_edge import learning
from sports_edge.collection import last_success, record_job, settled_forecasts
from sports_edge.ingestion.types import iso
from sports_edge.storage import Store

EVERY_DAYS = 7
MIN_NEW_RESULTS = 150
MIN_DAYS_BETWEEN_TRAININGS = 7
MIN_PAIRED = 100  # settled forecasts of the same events before a prospective verdict
STATUSES = (
    "Oczekuje na nowe dane",
    "Dane wystarczające",
    "Trening w toku",
    "Walidacja w toku",
    "Challenger gotowy",
    "Odrzucony",
    "Aktywowany",
    "Przywrócono starszą wersję",
)


def due(store: Store, *, now: datetime, every_days: int = EVERY_DAYS) -> bool:
    last = last_success(store, "retraining_review")
    return last is None or now - last >= timedelta(days=every_days)


def conditions(store: Store, scope: str, competitions, *, now: datetime) -> tuple[bool, list[str]]:
    """(ready, reasons) for training a new challenger of one scope."""
    from sports_edge.services import ELO_FAMILY, scope_version

    reasons = []
    champion = scope_version(store, competitions, ELO_FAMILY)
    since = champion["trained_through"] if champion else None
    marks = ",".join("?" for _ in competitions)
    new = store.scalar(
        f"SELECT COUNT(*) FROM historical_results WHERE competition IN ({marks}) "
        "AND (? IS NULL OR available_at>?)",
        (*competitions, since, since),
    )
    if new < MIN_NEW_RESULTS:
        reasons.append(f"nowych wyników od treningu: {new} < {MIN_NEW_RESULTS}")
    if learning.challengers(store, scope, ELO_FAMILY):
        reasons.append("challenger tego zakresu czeka już na decyzję (tryb cienia)")
    last = [e for e in learning.events(store, scope=scope) if e["kind"] == "challenger_ready"]
    if last and now - datetime.fromisoformat(last[-1]["created_at"]) < timedelta(
        days=MIN_DAYS_BETWEEN_TRAININGS
    ):
        reasons.append(f"ostatni trening mniej niż {MIN_DAYS_BETWEEN_TRAININGS} dni temu")
    return not reasons, reasons


def review(
    store: Store,
    paths,
    *,
    now: datetime,
    trigger: str = "manual",
    force: bool = False,
    scopes: tuple[str, ...] | None = None,
    train=None,
) -> dict:
    """One weekly review of every scope. ``train`` (scope → report) defaults to protocol v2."""
    from sports_edge.services import MODEL_SCOPES, run_protocol_v2

    train = train or (lambda scope: run_protocol_v2(store, paths, now=now, scope=scope))
    out = {}
    for scope in scopes or tuple(MODEL_SCOPES):
        ready, reasons = conditions(store, scope, MODEL_SCOPES[scope], now=now)
        if not ready and not force:
            out[scope] = {"status": "skipped", "reasons": reasons}
            learning.record(
                store,
                kind="review",
                actor="system" if trigger != "manual" else "user",
                reason="Przegląd: pominięto trening — " + "; ".join(reasons),
                now=now,
                scope=scope,
                payload={"reasons": reasons, "trigger": trigger},
            )
            continue
        try:
            report = train(scope)
            out[scope] = {"status": "trained", "summary": report.get("summary")}
        except ValueError as error:
            out[scope] = {"status": "failed", "reasons": [str(error)]}
    statuses = {v["status"] for v in out.values()}
    record_job(
        store,
        "retraining_review",
        trigger=trigger,
        started=now,
        finished=now,
        status="ok"
        if "trained" in statuses
        else "skipped"
        if statuses == {"skipped"}
        else "partial",
        reason="; ".join(
            f"{scope}: {', '.join(v.get('reasons') or [])}"
            for scope, v in out.items()
            if v["status"] != "trained"
        )[:500],
        payload=out,
    )
    return out


# ---------------------------------------------------------------- champion vs challenger


def _decision(row: dict | None) -> dict:
    if row is None:
        return {}
    report = json.loads(row["validation_json"])
    return {
        "version_id": row["version_id"],
        "version": row["version"],
        "protocol": report.get("protocol", "v1"),
        "decision": report.get("decision") if report.get("protocol") == "v2" else None,
        "holdout": report.get("holdout"),
        "statuses": {k: v.get("status") for k, v in (report.get("predictive") or {}).items()},
        "created_at": row["created_at"],
    }


def paired(store: Store, champion: str, challenger: str, *, seed: int = 7) -> dict:
    """Log loss of both versions on the SAME settled selections (prospective, shadow mode);
    the interval of the difference resamples events."""
    by_key: dict[tuple, dict[str, list]] = defaultdict(dict)
    for row in settled_forecasts(store):
        if row["status"] != "settled" or row["model_version_id"] not in (champion, challenger):
            continue
        key = (row["event_id"], row["market_key"], row["line"])
        by_key[key][row["model_version_id"]] = [
            (row["probabilities"][sel], y)
            for sel, y in (row["outcome"].get("selections") or {}).items()
            if y is not None and sel in row["probabilities"]
        ]
    pairs = [
        (v[champion], v[challenger]) for v in by_key.values() if champion in v and challenger in v
    ]
    if not pairs:
        return {"n": 0}

    def loss(items):
        return sum(-math.log(max(1e-6, p if y else 1 - p)) for p, y in items) / max(1, len(items))

    diffs = [loss(b) - loss(a) for a, b in pairs]
    rng = random.Random(seed)
    draws = sorted(sum(rng.choices(diffs, k=len(diffs))) / len(diffs) for _ in range(500))
    mean = sum(diffs) / len(diffs)
    return {
        "n": len(pairs),
        "difference": round(mean, 4),  # challenger − champion (negative: challenger better)
        "ci95": [round(draws[12], 4), round(draws[487], 4)],
        "enough": len(pairs) >= MIN_PAIRED,
    }


def comparison(store: Store, scope: str) -> dict:
    """Champion and the waiting challenger of a scope with a plain conclusion."""
    from sports_edge.services import (
        ELO_FAMILY,
        GOALS_FAMILY,
        MODEL_SCOPES,
        challenger_versions,
        scope_version,
    )

    out = {}
    waiting = challenger_versions(store).get(scope, {})
    for key, family in (("elo", ELO_FAMILY), ("goals", GOALS_FAMILY)):
        champion = scope_version(store, MODEL_SCOPES[scope], family)
        challenger = waiting.get(key)
        entry = {"champion": _decision(champion), "challenger": _decision(challenger)}
        if champion and challenger:
            entry["prospective"] = paired(store, champion["version_id"], challenger["version_id"])
        entry["conclusion"] = conclusion(entry)
        out[key] = entry
    return out


def conclusion(entry: dict) -> str:
    champion, challenger = entry["champion"], entry["challenger"]
    if not challenger:
        return "Brak challengera — model aktywny bez zmian."
    parts = []
    if champion.get("protocol") != "v2":
        parts.append(
            "Aktywna wersja ma statusy z protokołu v1 (holdout użyty do decyzji) — są zawieszone."
        )
    new = challenger.get("decision") or {}
    old = champion.get("decision") or {}
    if new and old:
        delta = new["log_loss"] - old["log_loss"]
        parts.append(
            f"W oknie selekcji challenger ma log loss {new['log_loss']} wobec {old['log_loss']} "
            + ("(lepiej)." if delta < 0 else "(gorzej)." if delta > 0 else "(tak samo).")
        )
    elif new:
        parts.append(
            f"Challenger: okno selekcji n={new.get('n')}, log loss {new.get('log_loss')}, "
            f"ECE {new.get('ece')} — bez czystego porównania z wersją v1."
        )
    validated = [
        k for k, v in (challenger.get("statuses") or {}).items() if v == "validated_predictive"
    ]
    if validated:
        parts.append("Rynki zwalidowane w oknie selekcji: " + ", ".join(validated) + ".")
    pro = entry.get("prospective") or {}
    if pro.get("n"):
        verdict = (
            "challenger lepszy"
            if pro["ci95"][1] < 0
            else "challenger gorszy"
            if pro["ci95"][0] > 0
            else "brak istotnej różnicy"
        )
        parts.append(
            f"Prospektywnie (te same mecze, n={pro['n']}): {verdict}"
            + ("" if pro["enough"] else " — za mało rozliczonych prognoz na decyzję")
            + "."
        )
    else:
        parts.append("Prospektywnie: brak jeszcze wspólnych rozliczonych prognoz (tryb cienia).")
    return " ".join(parts)


def scope_status(store: Store, scope: str, *, now: datetime, running: str | None = None) -> str:
    """One of the eight statuses of the panel."""
    from sports_edge.services import ELO_FAMILY, MODEL_SCOPES

    if running in ("Trening w toku", "Walidacja w toku"):
        return running
    found = learning.events(store, scope=scope, family=ELO_FAMILY)
    last = next(
        (
            e
            for e in reversed(found)
            if e["kind"]
            in {"challenger_ready", "activated", "rejected", "kept_previous", "rolled_back"}
        ),
        None,
    )
    if last is not None and last["kind"] == "challenger_ready":
        return "Challenger gotowy"
    if last is not None and last["kind"] == "rolled_back":
        return "Przywrócono starszą wersję"
    if last is not None and last["kind"] in {"rejected", "kept_previous"}:
        return "Odrzucony"
    ready, _reasons = conditions(store, scope, MODEL_SCOPES[scope], now=now)
    if ready:
        return "Dane wystarczające"
    if last is not None and last["kind"] == "activated" and last["actor"] == "user":
        return "Aktywowany"
    return "Oczekuje na nowe dane"


def history(store: Store) -> list[dict]:
    """Learning history table (newest first)."""
    rows = learning.events(store)
    return [
        {
            "Kiedy": r["created_at"],
            "Zakres": r["scope"] or "—",
            "Model": r["family"] or "—",
            "Wersja": (r["model_version_id"] or "")[:12],
            "Zdarzenie": r["label"],
            "Kto": {"user": "użytkownik", "system": "system", "migration": "migracja"}[r["actor"]],
            "Powód": r["reason"],
        }
        for r in reversed(rows)
    ]


def stamp(now: datetime) -> str:
    return iso(now)
