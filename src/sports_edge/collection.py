"""Data Collection Engine (TASK 007 §P4): settling every forecast, job runs, completeness.

* Every PROSPECTIVE forecast is settled automatically once its event has a result — not
  only the legs of coupons. Separate statuses: settled, unresolved (no result long after
  the start), cancelled, postponed, void, uncertain (unverified result), source_conflict
  (two sources disagree) and missing_stat (the market needs a statistic we do not have).
* Settlements are append-only revisions: a corrected result creates a new revision that
  points to the previous one; an unchanged outcome records nothing.
* Every automatic job leaves one row in ``job_runs`` (status, reason, counts), so the
  application can tell what ran, what was skipped and why, and resume after a break.
"""

import json
from collections import Counter
from datetime import datetime, timedelta

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.paper import resolve
from sports_edge.settlement import Outcome
from sports_edge.storage import Store

UNRESOLVED_AFTER = timedelta(hours=48)  # no result two days after the start: unresolved
RESULT_STATUS = {
    "postponed": "postponed",
    "cancelled": "cancelled",
    "abandoned": "cancelled",
    "void": "void",
}
STATUS_LABELS = {
    "settled": "rozliczona",
    "unresolved": "nierozstrzygnięta (brak wyniku)",
    "cancelled": "odwołana",
    "postponed": "przełożona",
    "void": "unieważniona",
    "uncertain": "niepewna (wynik niezweryfikowany)",
    "source_conflict": "konflikt źródeł",
    "missing_stat": "brak statystyki",
}


def _results(store: Store, event_id: str) -> list[dict]:
    return store.rows(
        "SELECT * FROM results WHERE event_id=? ORDER BY recorded_at DESC, id DESC", (event_id,)
    )


def _settle_one(
    prediction: dict, results: list[dict], *, now: datetime
) -> tuple[str, dict, dict | None]:
    """(status, outcome, result row) of one forecast from the recorded results."""
    start = datetime.fromisoformat(prediction["event_start"])
    if not results:
        return ("unresolved" if now - start >= UNRESOLVED_AFTER else "pending"), {}, None
    latest = results[0]
    if latest["status"] in RESULT_STATUS:
        return RESULT_STATUS[latest["status"]], {}, latest
    completed = [r for r in results if r["status"] == "completed" and r["home_score"] is not None]
    if len(completed) >= 2:
        newest, older = completed[0], completed[1]
        differs = (newest["home_score"], newest["away_score"]) != (
            older["home_score"],
            older["away_score"],
        )
        # a later record of the SAME source is a correction; two sources of equal standing
        # that disagree are a conflict (a verified record outranks an unverified one)
        if (
            differs
            and newest["source"] != older["source"]
            and bool(newest["verified"]) == bool(older["verified"])
        ):
            return "source_conflict", {}, newest
    if not latest["verified"]:
        return "uncertain", {}, latest
    market = {
        "market_key": prediction["market_key"],
        "period": prediction["period"],
        "line": prediction["line"],
        "overtime": "false",
    }
    probabilities = json.loads(prediction["probabilities_json"])
    outcome: dict = {"score": f"{latest['home_score']}:{latest['away_score']}", "selections": {}}
    for selection in probabilities:
        found = resolve(market, selection, latest)
        outcome["selections"][selection] = (
            None if found is None else 1 if found == Outcome.WIN else 0
        )
    derived = json.loads(prediction["uncertainty_json"] or "{}").get("derived") or {}
    for key, value in derived.items():
        sides = value.get("calibrated") or {}
        dc = {"market_key": key, "period": prediction["period"], "line": None, "overtime": "false"}
        outcome.setdefault("derived", {})[key] = {
            sel: (None if (o := resolve(dc, sel, latest)) is None else int(o == Outcome.WIN))
            for sel in sides
        }
    if all(v is None for v in outcome["selections"].values()):
        return "missing_stat", outcome, latest
    return "settled", outcome, latest


def settle_predictions(store: Store, *, now: datetime) -> dict[str, int]:
    """Settle (or re-settle after a correction) every prospective forecast whose event
    started; idempotent."""
    rows = store.rows(
        """SELECT p.*, e.start_at event_start FROM predictions p JOIN events e ON e.id=p.event_id
        WHERE p.kind='prospective' AND e.start_at IS NOT NULL AND e.start_at<=?""",
        (iso(now),),
    )
    latest = {}
    for row in store.rows("SELECT * FROM prediction_settlements ORDER BY prediction_id, revision"):
        latest[row["prediction_id"]] = row
    by_event: dict[str, list[dict]] = {}
    counts: Counter = Counter()
    for prediction in rows:
        event = prediction["event_id"]
        if event not in by_event:
            by_event[event] = _results(store, event)
        status, outcome, result = _settle_one(prediction, by_event[event], now=now)
        if status == "pending":
            counts["pending"] += 1
            continue
        previous = latest.get(prediction["id"])
        if (
            previous is not None
            and previous["status"] == status
            and json.loads(previous["outcome_json"]) == outcome
        ):
            counts["unchanged"] += 1
            continue
        revision = 1 if previous is None else previous["revision"] + 1
        store.insert(
            "prediction_settlements",
            id=digest(["prediction_settlement", prediction["id"], revision]),
            prediction_id=prediction["id"],
            revision=revision,
            status=status,
            outcome_json=canonical(outcome),
            result_ref=result["id"] if result else None,
            source=result["source"] if result else None,
            settled_at=iso(now),
            supersedes_id=previous["id"] if previous else None,
            reason="korekta wyniku" if previous else "",
        )
        counts[status] += 1
        if previous is not None:
            counts["corrections"] += 1
    return dict(counts)


def settled_forecasts(store: Store) -> list[dict]:
    """Latest settlement of every settled forecast with what quality reports need."""
    out = []
    for row in store.rows(
        """SELECT s.*, p.model_version_id, p.event_id, p.market_key, p.line, p.probabilities_json,
        p.uncertainty_json, p.inputs_json, p.created_at predicted_at, e.start_at, e.league
        FROM prediction_settlements s JOIN predictions p ON p.id=s.prediction_id
        JOIN events e ON e.id=p.event_id
        WHERE s.revision=(SELECT MAX(revision) FROM prediction_settlements x
                          WHERE x.prediction_id=s.prediction_id)"""
    ):
        out.append(
            {
                **row,
                "outcome": json.loads(row["outcome_json"]),
                "probabilities": json.loads(row["probabilities_json"]),
                "uncertainty": json.loads(row["uncertainty_json"] or "{}"),
                "inputs": json.loads(row["inputs_json"] or "{}"),
            }
        )
    return out


# ---------------------------------------------------------------- job runs


def record_job(
    store: Store,
    job: str,
    *,
    trigger: str,
    started: datetime,
    finished: datetime,
    status: str,
    reason: str = "",
    payload: dict | None = None,
) -> str:
    run_id = digest(["job", job, iso(started)])
    if store.get("job_runs", run_id) is not None:
        return run_id  # the same run recorded once
    store.insert(
        "job_runs",
        id=run_id,
        job=job,
        trigger=trigger,
        started_at=iso(started),
        finished_at=iso(finished),
        status=status,
        reason=reason,
        payload_json=canonical(
            payload or {},
        ),
    )
    return run_id


def last_runs(store: Store) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in store.rows("SELECT * FROM job_runs ORDER BY started_at"):
        out[row["job"]] = {**row, "payload": json.loads(row["payload_json"] or "{}")}
    return out


def last_run(
    store: Store, job: str, statuses: tuple[str, ...] = ("ok", "partial", "skipped")
) -> datetime | None:
    """When a job last FINISHED its work — a skip with a reason is a finished run too."""
    marks = ",".join("?" for _ in statuses)
    value = store.scalar(
        f"SELECT MAX(finished_at) FROM job_runs WHERE job=? AND status IN ({marks})",
        (job, *statuses),
    )
    return datetime.fromisoformat(value) if value else None


def last_success(store: Store, job: str) -> datetime | None:
    value = store.scalar(
        "SELECT MAX(finished_at) FROM job_runs WHERE job=? AND status IN ('ok','partial')", (job,)
    )
    return datetime.fromisoformat(value) if value else None


# ---------------------------------------------------------------- completeness


def completeness(store: Store, *, now: datetime) -> dict:
    """What has been collected, what is pending and what is missing — with reasons."""
    statuses = Counter(
        r["status"]
        for r in store.rows(
            """SELECT s.status FROM prediction_settlements s WHERE s.revision=(SELECT MAX(revision)
            FROM prediction_settlements x WHERE x.prediction_id=s.prediction_id)"""
        )
    )
    started = store.scalar(
        """SELECT COUNT(*) FROM predictions p JOIN events e ON e.id=p.event_id
        WHERE p.kind='prospective' AND e.start_at<=?""",
        (iso(now),),
    )
    total = store.scalar("SELECT COUNT(*) FROM predictions WHERE kind='prospective'")
    settled_total = sum(statuses.values())
    missing_results = store.rows(
        """SELECT e.id, e.home, e.away, e.league, e.start_at FROM events e
        WHERE e.bookmaker_id='sts' AND e.start_at<=? AND e.start_at>=?
        AND EXISTS (SELECT 1 FROM predictions p WHERE p.event_id=e.id)
        AND NOT EXISTS (SELECT 1 FROM results r WHERE r.event_id=e.id)
        ORDER BY e.start_at""",
        (iso(now - UNRESOLVED_AFTER), iso(now - timedelta(days=30))),
    )
    versions = Counter(
        r["model_version_id"]
        for r in store.rows("SELECT model_version_id FROM predictions WHERE kind='prospective'")
    )
    return {
        "predictions": total,
        "started": started,
        "upcoming": total - started,
        "settlements": dict(statuses),
        "pending": max(0, started - settled_total),
        "missing_results": [
            {
                "event": f"{r['home']} - {r['away']}",
                "league": r["league"],
                "start_at": r["start_at"],
            }
            for r in missing_results[:50]
        ],
        "missing_results_count": len(missing_results),
        "by_version": dict(versions),
        "jobs": {
            job: {k: run[k] for k in ("status", "finished_at", "reason")}
            for job, run in last_runs(store).items()
        },
    }


def catch_up(store: Store, *, now: datetime, update=None, max_gap_days: int = 14) -> dict:
    """Resume after a break: settle every pending forecast (idempotent) and, if a results
    updater is given, run it once when the last successful update is older than a day.
    The gap is bounded — no unlimited history downloads."""
    started = now
    out: dict = {}
    last = last_success(store, "results_update")
    gap = (now - last).days if last else None
    if update is not None and (last is None or now - last > timedelta(days=1)):
        if gap is not None and gap > max_gap_days:
            out["results_update"] = (
                f"przerwa {gap} dni > {max_gap_days} — uzupełnij historię ręcznie (Źródła)"
            )
        else:
            try:
                out["results_update"] = update()
                record_job(
                    store,
                    "results_update",
                    trigger="catch_up",
                    started=started,
                    finished=now,
                    status="ok",
                    payload={"gap_days": gap},
                )
            except Exception as error:  # noqa: BLE001 — a failed source must not stop catch-up
                out["results_update"] = f"błąd: {type(error).__name__}"
                record_job(
                    store,
                    "results_update",
                    trigger="catch_up",
                    started=started,
                    finished=now,
                    status="failed",
                    reason=type(error).__name__,
                )
    out["settlements"] = settle_predictions(store, now=now)
    record_job(
        store,
        "prediction_settlement",
        trigger="catch_up",
        started=started,
        finished=now,
        status="ok",
        payload=out["settlements"],
    )
    return out
