"""AUTOMATYZACJA AI (TASK 008): one supervised background worker controlled from the web page.

* The Streamlit page is the CONTROL PANEL: it stores settings and commands (start, pause,
  resume, stop, run now, check coupons, run one job) in SQLite and reads the status.
* The WORKER is a separate process of this application (``python -m sports_edge.automation
  run``), started from the panel or — when "resume after start" is on — by the launcher.
  It does not depend on any browser session: closing a tab does not stop it. It watches the
  server (parent) process and stops with it; nothing runs while the computer is off.
* One worker only: an operating-system file lock is held for the whole life of the
  process (released by the system even after a crash) — a second tab, a rerun or a second
  start can never create a second worker.
* Heartbeat: ``data/automation/runtime.json`` is rewritten atomically every few seconds by a
  heartbeat thread; the panel reports "active" only when the lock is held AND the heartbeat
  is fresh.
* Jobs leave a ``job_runs`` row each ("auto:<job>"); the next run follows from the last
  finished run, so after a restart an overdue job runs ONCE (bounded catch-up) and past
  pre-match checkpoints are recorded as missed — never replayed with invented data.
* No Windows Task Scheduler, no system service, no paid service.
"""

import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.settings import AppPaths
from sports_edge.storage import Store

HEARTBEAT_SECONDS = 5
STALE_AFTER = timedelta(seconds=30)
TICK_SECONDS = 5
FREQUENCIES = {
    "results": [60, 180, 360, 1440],
    "prediction_settlement": [60, 180, 360, 1440],
    "coupon_settlement": [60, 180, 360, 1440],
    "lineups": [5, 10, 15],
    "coupon_reassessment": [15, 30, 60],
    "sts_offer": [180, 360, 720, 1440],
    "quality": [1440, 4320, 10080],
    "review": [10080, 20160, 0],  # 0 = only on demand
    # TASK 009: international football (own sources, own limits)
    "uefa_results": [180, 360, 720, 1440],
    "national_results": [1440, 4320, 10080],
    "cup_monitor": [60, 180, 360],
    "intl_completeness": [1440, 4320, 10080],
    "cup_training": [1440, 4320, 10080],
}
JOB_LABELS = {
    "results": "Aktualizacja wyników sportowych",
    "prediction_settlement": "Rozliczanie prognoz",
    "coupon_settlement": "Rozliczanie kuponów",
    "lineups": "Monitorowanie składów (względem godziny meczu)",
    "absences": "Monitorowanie kontuzji i zawieszeń",
    "sts_offer": "Sprawdzanie nowych kursów STS",
    "coupon_reassessment": "Ponowna kontrola kuponów",
    "quality": "Analiza jakości modeli",
    "review": "Przegląd potrzeby retreningu",
    "training": "Trening challengera (po spełnieniu kryteriów)",
    "uefa_results": "Wyniki i terminarz pucharów UEFA (LM, LE, LK)",
    "national_results": "Wyniki reprezentacji (Liga Narodów, eliminacje, turnieje)",
    "cup_monitor": "Nowe mecze pucharowe w ofercie STS i prognozy cieniowe",
    "intl_completeness": "Kontrola kompletności danych międzynarodowych",
    "cup_training": "Eksperymenty modeli pucharowych (po spełnieniu warunków)",
}
DEFAULTS = {
    "auto_resume": False,
    "jobs": {
        "results": {"enabled": True, "every_minutes": 180},
        "prediction_settlement": {"enabled": True, "every_minutes": 60},
        "coupon_settlement": {"enabled": True, "every_minutes": 60},
        "lineups": {"enabled": True, "every_minutes": 5},
        "absences": {"enabled": True},
        "sts_offer": {"enabled": False, "every_minutes": 360},
        "coupon_reassessment": {"enabled": True, "every_minutes": 30},
        "quality": {"enabled": True, "every_minutes": 1440},
        "review": {"enabled": True, "every_minutes": 10080},
        "training": {"enabled": False},
        "uefa_results": {"enabled": True, "every_minutes": 360},
        "national_results": {"enabled": True, "every_minutes": 4320},
        "cup_monitor": {"enabled": True, "every_minutes": 180},
        "intl_completeness": {"enabled": True, "every_minutes": 1440},
        "cup_training": {"enabled": True, "every_minutes": 4320},
    },
    "checkpoints_minutes": [2880, 1440, 360, 90, 60, 30, 15],
    "max_candidates": 30,
    "one_off": [],  # jobs planned once: {"id", "job", "at", "created_at"}
}
WARSAW = ZoneInfo("Europe/Warsaw")  # fixed times are wall-clock times in Poland
WEEKDAYS = ("pon", "wt", "śr", "czw", "pt", "sob", "niedz")
MODES = {"interval": "co określony czas", "daily": "o stałych godzinach"}
CHECKPOINT_NAMES = {
    2880: "T-48h",
    1440: "T-24h",
    360: "T-6h",
    90: "T-90min",
    60: "T-60min",
    30: "T-30min",
    15: "T-15min",
}


# ---------------------------------------------------------------- settings and commands


def load_settings(store: Store) -> dict:
    rows = store.rows(
        "SELECT settings_json FROM automation_settings ORDER BY saved_at DESC, rowid DESC LIMIT 1"
    )
    saved = json.loads(rows[0]["settings_json"]) if rows else {}
    merged = json.loads(json.dumps(DEFAULTS))
    merged.update({k: v for k, v in saved.items() if k != "jobs"})
    for job, cfg in (saved.get("jobs") or {}).items():
        merged["jobs"].setdefault(job, {}).update(cfg)
    return merged


def save_settings(store: Store, settings: dict, *, now: datetime, by: str = "user") -> str:
    body = canonical(settings)
    settings_id = digest(["automation_settings", body, iso(now)])
    store.insert(
        "automation_settings", id=settings_id, saved_at=iso(now), settings_json=body, saved_by=by
    )
    return settings_id


def command(
    store: Store, name: str, *, now: datetime, payload: dict | None = None, by: str = "user"
) -> str:
    command_id = digest(["automation_command", name, iso(now), uuid.uuid4().hex])
    store.insert(
        "automation_commands",
        id=command_id,
        created_at=iso(now),
        command=name,
        payload_json=canonical(payload or {}),
        requested_by=by,
    )
    return command_id


def request(
    paths: AppPaths, store: Store, name: str, *, now: datetime, payload: dict | None = None
) -> str:
    """A button of the interface: the command goes to the worker; when no worker runs, a
    one-off process executes just the pending commands and ends (no scheduled jobs)."""
    command(store, name, now=now, payload=payload)
    if lock_held(paths):
        return "Zlecono workerowi — wynik pojawi się za chwilę (odśwież stronę)."
    start_worker(paths, commands_only=True)
    return (
        "Worker nie był uruchomiony — kontrola wykona się jednorazowo w tle (bez pozostałych "
        "zadań). Odśwież stronę za kilkanaście sekund."
    )


def desired_state(store: Store) -> str:
    """running | paused | stopped — from the latest control command (default: stopped)."""
    rows = store.rows(
        """SELECT command FROM automation_commands WHERE command IN
        ('start','pause','resume','stop') ORDER BY created_at DESC, rowid DESC LIMIT 1"""
    )
    if not rows:
        return "stopped"
    return {"start": "running", "resume": "running", "pause": "paused", "stop": "stopped"}[
        rows[0]["command"]
    ]


def pending_commands(store: Store) -> list[dict]:
    done = {
        json.loads(r["payload_json"]).get("command_id")
        for r in store.rows(
            "SELECT payload_json FROM automation_events WHERE kind='command_processed'"
        )
    }
    return [
        {**r, "payload": json.loads(r["payload_json"])}
        for r in store.rows("SELECT * FROM automation_commands ORDER BY created_at, rowid")
        if r["id"] not in done
    ]


def event(
    store: Store,
    kind: str,
    *,
    now: datetime,
    worker_id: str | None = None,
    detail: str = "",
    payload: dict | None = None,
) -> None:
    store.insert(
        "automation_events",
        id=digest(["automation_event", kind, iso(now), uuid.uuid4().hex]),
        created_at=iso(now),
        kind=kind,
        worker_id=worker_id,
        detail=detail,
        payload_json=canonical(payload or {}),
    )


# ---------------------------------------------------------------- lock, heartbeat, process


def folder(paths: AppPaths) -> Path:
    path = paths.data / "automation"
    path.mkdir(parents=True, exist_ok=True)
    return path


class WorkerLock:
    """Exclusive OS lock on ``worker.lock`` held for the life of the worker process."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 — kept open while the lock is held
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def lock_held(paths: AppPaths) -> bool:
    """True while some process holds the worker lock."""
    probe = WorkerLock(folder(paths) / "worker.lock")
    if probe.acquire():
        probe.release()
        return False
    return True


def write_runtime(paths: AppPaths, data: dict) -> None:
    """Atomic replace; on Windows a reader may hold the file for a moment — retry briefly."""
    target = folder(paths) / "runtime.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))


def read_runtime(paths: AppPaths) -> dict:
    try:
        return json.loads((folder(paths) / "runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def process_alive(pid: int | None) -> bool:
    """Is a process alive? (Windows: never os.kill — it would terminate the process.)"""
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def status(paths: AppPaths, store: Store, *, now: datetime) -> dict:
    """What the panel shows — "active" only for a live, locked, recently beating worker."""
    runtime = read_runtime(paths)
    beat = runtime.get("heartbeat_at")
    age = (now - datetime.fromisoformat(beat)).total_seconds() if beat else None
    alive = lock_held(paths) and age is not None and age <= STALE_AFTER.total_seconds()
    desired = desired_state(store)
    if alive and runtime.get("state") == "paused":
        label = "wstrzymana"
    elif alive:
        label = "aktywna"
    elif desired == "stopped":
        label = "zatrzymana"
    else:
        label = "błąd — worker nie działa"
    return {
        **runtime,
        "alive": alive,
        "heartbeat_age": age,
        "desired": desired,
        "label": label,
        "pending": len(pending_commands(store)),
    }


def start_worker(
    paths: AppPaths, *, once: bool = False, parent: int | None = None, commands_only: bool = False
) -> int | None:
    """Start the worker process (no-op while one is alive). Returns the new PID or None.
    ``once`` runs the pending commands and the due jobs, then ends; ``commands_only`` runs
    just the pending commands (e.g. "check my coupons") without the scheduled jobs."""
    if lock_held(paths):
        return None
    log = open(folder(paths) / "worker.log", "ab")  # noqa: SIM115 — handed to the child
    args = [
        sys.executable,
        "-m",
        "sports_edge.automation",
        "commands" if commands_only else "once" if once else "run",
        "--parent",
        str(parent or os.getpid()),
    ]
    env = {**os.environ, "SPORTS_EDGE_HOME": str(paths.root)}
    kwargs: dict = {
        "cwd": str(paths.root),
        "stdout": log,
        "stderr": log,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000200 | 0x08000000  # NEW_PROCESS_GROUP | NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(args, **kwargs)  # noqa: S603 — fixed interpreter and module
    log.close()
    return process.pid


def autostart(paths: AppPaths, store: Store, *, parent: int | None = None) -> str:
    """Server start: resume the worker if the user asked for it (persisted setting)."""
    settings = load_settings(store)
    if not settings.get("auto_resume"):
        return "automatyczne wznowienie wyłączone"
    if desired_state(store) == "stopped":
        return "automatyzacja była zatrzymana przez użytkownika"
    pid = start_worker(paths, parent=parent)
    return f"worker uruchomiony (PID {pid})" if pid else "worker już działa"


# ---------------------------------------------------------------- jobs


@dataclass
class JobOutcome:
    status: str  # ok | skipped | failed | partial
    reason: str = ""
    payload: dict | None = None


def _job_name(job: str) -> str:
    return f"auto:{job}"


def last_finished(store: Store, job: str) -> datetime | None:
    value = store.scalar(
        "SELECT MAX(finished_at) FROM job_runs WHERE job=? "
        "AND status IN ('ok','partial','skipped')",
        (_job_name(job),),
    )
    return datetime.fromisoformat(value) if value else None


def failures(store: Store, job: str) -> int:
    """Consecutive failed runs of a job (for exponential backoff)."""
    count = 0
    for row in store.rows(
        "SELECT status FROM job_runs WHERE job=? ORDER BY started_at DESC LIMIT 6",
        (_job_name(job),),
    ):
        if row["status"] != "failed":
            break
        count += 1
    return count


def _valid_time(value: str) -> bool:
    try:
        hour, minute = (int(x) for x in str(value).split(":"))
    except ValueError:
        return False
    return 0 <= hour < 24 and 0 <= minute < 60


def slots(cfg: dict, start: datetime, end: datetime) -> list[datetime]:
    """Fixed-time runs (Europe/Warsaw, chosen weekdays) between ``start`` and ``end``."""
    times = sorted({t for t in cfg.get("times") or [] if _valid_time(t)})
    days = set(cfg["weekdays"]) if cfg.get("weekdays") is not None else set(range(7))
    out = []
    day = start.astimezone(WARSAW).date() - timedelta(days=1)
    last = end.astimezone(WARSAW).date() + timedelta(days=1)
    while day <= last:
        if day.weekday() in days:
            for value in times:
                hour, minute = (int(x) for x in value.split(":"))
                moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=WARSAW)
                moment = moment.astimezone(UTC)
                if start <= moment <= end:
                    out.append(moment)
        day += timedelta(days=1)
    return sorted(out)


def _daily_next(store: Store, job: str, cfg: dict, *, now: datetime) -> datetime | None:
    previous = slots(cfg, now - timedelta(days=8), now)
    upcoming = slots(cfg, now + timedelta(seconds=1), now + timedelta(days=8))
    following = upcoming[0] if upcoming else None
    failed = failures(store, job)
    if failed:
        last = store.scalar("SELECT MAX(finished_at) FROM job_runs WHERE job=?", (_job_name(job),))
        retry = datetime.fromisoformat(last) + timedelta(minutes=5 * 2 ** (failed - 1))
        return min(retry, following) if following else retry
    done = last_finished(store, job)
    if previous and (done is None or done < previous[-1]):
        return previous[-1]  # a planned time passed (e.g. the app was off): run it once
    return following


def next_run(store: Store, job: str, settings: dict, *, now: datetime) -> datetime | None:
    cfg = settings["jobs"].get(job) or {}
    if cfg.get("enabled") and cfg.get("mode") == "daily":
        return _daily_next(store, job, cfg, now=now)
    every = int(cfg.get("every_minutes") or 0)
    if not cfg.get("enabled") or every <= 0:
        return None
    failed = failures(store, job)
    if failed:
        last = store.scalar("SELECT MAX(finished_at) FROM job_runs WHERE job=?", (_job_name(job),))
        backoff = timedelta(minutes=min(every, 5 * 2 ** (failed - 1)))
        return datetime.fromisoformat(last) + backoff
    last = last_finished(store, job)
    return now if last is None else last + timedelta(minutes=every)


def run_job(
    store: Store,
    paths: AppPaths,
    job: str,
    *,
    now: datetime,
    settings: dict,
    trigger: str,
    runner: Callable | None = None,
) -> JobOutcome:
    """Run one job and record it; a failure is recorded (no raw traceback, no secrets)."""
    started = now
    clock = time.monotonic()
    try:
        outcome = (runner or _runner(job))(store, paths, now=now, settings=settings)
    except Exception as error:  # noqa: BLE001 — every failure is a recorded status
        outcome = JobOutcome("failed", f"{type(error).__name__}: {str(error)[:160]}")
    seconds = round(time.monotonic() - clock, 2)
    payload = {**(outcome.payload or {}), "duration_s": seconds}
    record = {
        "job": _job_name(job),
        "trigger": trigger,
        "started": started,
        "finished": started + timedelta(seconds=seconds),
        "status": outcome.status,
        "reason": outcome.reason,
        "payload": payload,
    }
    from sports_edge.collection import record_job

    for attempt in range(3):  # SQLite busy: short retry with backoff
        try:
            record_job(
                store,
                record["job"],
                trigger=record["trigger"],
                started=record["started"],
                finished=record["finished"],
                status=record["status"],
                reason=record["reason"],
                payload=record["payload"],
            )
            break
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(0.5 * 2**attempt)
    return outcome


def _app(paths: AppPaths):
    from sports_edge.settings import load_settings as app_settings

    return app_settings(paths)


def job_results(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge.services import update_results_data

    app = _app(paths)
    if app.offline_mode:
        return JobOutcome("skipped", "tryb offline — pobieranie wyników wyłączone")
    report = update_results_data(store, paths, app, now=now)
    errors = report.get("errors") or []
    return JobOutcome(
        "partial" if errors else "ok",
        "; ".join(map(str, errors[:2]))[:300],
        {
            "processed": report.get("inserted", 0),
            "new": report.get("inserted", 0),
            "errors": len(errors),
        },
    )


def job_prediction_settlement(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge.collection import settle_predictions

    found = settle_predictions(store, now=now)
    return JobOutcome(
        "ok",
        "",
        {"processed": sum(found.values()), "new": found.get("settled", 0), "detail": found},
    )


def job_coupon_settlement(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge.services import settle_coupons

    found = settle_coupons(store, paths, now=now)
    return JobOutcome("ok", "", {"processed": sum(found.values()), "detail": found})


def job_lineups(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge.services import run_prematch

    app = _app(paths)
    if app.offline_mode:
        return JobOutcome("skipped", "tryb offline — składy nie są pobierane")
    marks = [m for m in settings.get("checkpoints_minutes", []) if m in CHECKPOINT_NAMES]
    checkpoints = tuple(
        (CHECKPOINT_NAMES[m], timedelta(minutes=m)) for m in sorted(marks, reverse=True)
    )
    found = run_prematch(
        store,
        paths,
        app,
        now=now,
        checkpoints=checkpoints or None,
        max_candidates=int(settings.get("max_candidates", 30)),
    )
    if found.get("blocked"):
        return JobOutcome("skipped", f"źródło składów niedostępne: {found['blocked']}", found)
    return JobOutcome(
        "partial" if found["errors"] else "ok",
        f"błędy źródła: {found['errors']}" if found["errors"] else "",
        {"processed": found["checked"], "new": found["changes"], **found},
    )


def job_coupon_reassessment(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge.reassessment import reassess_all

    found = reassess_all(store, now=now)
    return JobOutcome("ok", "", {"processed": found["coupons"], "detail": found})


def job_quality(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge import forecast_quality as fq
    from sports_edge import prospective_eval as pe

    rows = fq.items(store)
    markets = fq.by_market(rows)
    # TASK 011: the prospective STS market benchmark, appended once per data state
    benchmark = pe.record_market_benchmarks(store, now=now, policy=pe.load_policy(paths.config))
    return JobOutcome(
        "ok",
        "",
        {
            "processed": len(rows),
            "detail": {
                k: {"matches": v["matches"], "log_loss": v["log_loss"], "ece": v["ece"]}
                for k, v in markets.items()
            },
            "market_benchmark": benchmark,
        },
    )


def job_review(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge import retraining

    allow = bool((settings["jobs"].get("training") or {}).get("enabled"))
    found = retraining.review(store, paths, now=now, trigger="automation", allow_training=allow)
    statuses = {v["status"] for v in found.values()}
    return JobOutcome(
        "ok" if "trained" in statuses else "skipped",
        "; ".join(
            f"{k}: {', '.join(v.get('reasons') or [])}"
            for k, v in found.items()
            if v["status"] != "trained"
        )[:400],
        {"detail": found},
    )


def job_sts_offer(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge.services import refresh_offers

    app = _app(paths)
    if app.offline_mode or not app.sts_download_enabled:
        return JobOutcome("skipped", "pobieranie oferty STS jest wyłączone w ustawieniach")
    today = now.date()
    days = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
    attempts = refresh_offers(store, paths, app, days, now=now)
    return JobOutcome("ok", "", {"processed": len(attempts)})


def _international(store, paths, *, now, cups: bool, national: bool) -> JobOutcome:
    from sports_edge.international_data import EUROPEAN, NATIONAL
    from sports_edge.services import import_international

    app = _app(paths)
    if app.offline_mode:
        return JobOutcome("skipped", "tryb offline — pobieranie wyników wyłączone")
    codes = EUROPEAN if cups else NATIONAL
    marks = ",".join("?" for _ in codes)
    history = store.scalar(
        f"SELECT COUNT(*) FROM historical_results WHERE competition IN ({marks})", codes
    )
    report = import_international(
        store, paths, app, now=now, cups=cups, national=national, current_only=bool(history)
    )
    entries = report["cups"] + report["national"]
    errors = [f"{e['competition']}: {e['error']}" for e in entries if e.get("error")]
    return JobOutcome(
        "partial" if errors else "ok",
        "; ".join(errors[:2])[:300],
        {
            "processed": sum(e.get("records", 0) for e in entries),
            "new": sum(e.get("inserted", 0) for e in entries),
            "fixtures": sum(e.get("fixtures", 0) for e in entries),
            "unmatched": sorted({n for e in entries for n in e.get("unmatched", [])})[:20],
            "history_import": not history,
            "errors": len(errors),
        },
    )


def job_uefa_results(store, paths, *, now, settings) -> JobOutcome:
    return _international(store, paths, now=now, cups=True, national=False)


def job_national_results(store, paths, *, now, settings) -> JobOutcome:
    return _international(store, paths, now=now, cups=False, national=True)


def job_cup_monitor(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge.international import shadow_forecasts
    from sports_edge.international_data import cup_monitor

    found = cup_monitor(store, paths, now=now)
    shadow = shadow_forecasts(store, paths, now=now)
    return JobOutcome(
        "ok",
        "",
        {
            "processed": found["events"],
            "new": shadow.get("stored", 0),
            "detail": found,
            "shadow": shadow,
        },
    )


def job_intl_completeness(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge.international_data import completeness

    rows = completeness(store, now=now)
    gaps = [f"{r['competition']} {r['season']}: {r['status']}" for r in rows if r["status"] != "ok"]
    return JobOutcome(
        "partial" if gaps else "ok",
        "; ".join(gaps[:3])[:300],
        {"processed": len(rows), "gaps": gaps[:20]},
    )


CUP_TRAINING_MIN_NEW = 30  # new international results needed before experiments are re-run


def job_cup_training(store, paths, *, now, settings) -> JobOutcome:
    from sports_edge import international

    codes = (*international.CUPS, *international.NATIONAL_OFFICIAL)
    marks = ",".join("?" for _ in codes)
    count = store.scalar(
        f"SELECT COUNT(*) FROM historical_results WHERE competition IN ({marks})", codes
    )
    last = international.latest(store)
    if count < 1000:
        return JobOutcome("skipped", f"za mało wyników międzynarodowych ({count})")
    if last and count - int(last.get("results_count") or 0) < CUP_TRAINING_MIN_NEW:
        return JobOutcome(
            "skipped",
            f"warunki niespełnione: mniej niż {CUP_TRAINING_MIN_NEW} nowych wyników od ostatniego "
            "eksperymentu",
        )
    report = international.run(store, paths, now=now)
    validated = [
        f"{scope}:{market}"
        for scope in ("european", "national")
        for market, v in international.market_status(report, scope).items()
        if v["validated"]
    ]
    return JobOutcome(
        "ok",
        "eksperyment zapisany; aktywacja wymaga decyzji użytkownika",
        {"processed": count, "validated": validated},
    )


JOBS: dict[str, Callable] = {
    "results": job_results,
    "prediction_settlement": job_prediction_settlement,
    "coupon_settlement": job_coupon_settlement,
    "lineups": job_lineups,
    "coupon_reassessment": job_coupon_reassessment,
    "quality": job_quality,
    "review": job_review,
    "sts_offer": job_sts_offer,
    "uefa_results": job_uefa_results,
    "national_results": job_national_results,
    "cup_monitor": job_cup_monitor,
    "intl_completeness": job_intl_completeness,
    "cup_training": job_cup_training,
}


def due_jobs(store: Store, settings: dict, *, now: datetime) -> list[str]:
    out = []
    for job in JOBS:
        when = next_run(store, job, settings, now=now)
        if when is not None and when <= now:
            out.append(job)
    return out


def done_one_offs(store: Store) -> set[str]:
    return {
        json.loads(r["payload_json"]).get("id")
        for r in store.rows("SELECT payload_json FROM automation_events WHERE kind='one_off_done'")
    }


def due_one_offs(store: Store, settings: dict, *, now: datetime) -> list[dict]:
    """One-off tasks whose time has come and which have not run yet."""
    done = done_one_offs(store)
    return [
        task
        for task in settings.get("one_off") or []
        if task.get("job") in JOBS
        and task["id"] not in done
        and datetime.fromisoformat(task["at"]) <= now
    ]


def add_one_off(store: Store, *, job: str, at: datetime, now: datetime) -> str:
    if job not in JOBS:
        raise ValueError("Nieznane zadanie")
    settings = load_settings(store)
    task_id = digest(["one_off", job, iso(at), iso(now)])[:16]
    settings["one_off"] = [
        *(settings.get("one_off") or []),
        {"id": task_id, "job": job, "at": iso(at), "created_at": iso(now)},
    ]
    save_settings(store, settings, now=now)
    return task_id


def cancel_one_off(store: Store, task_id: str, *, now: datetime) -> None:
    """Removes a planned one-off task from the current settings (older versions keep it)."""
    settings = load_settings(store)
    settings["one_off"] = [t for t in settings.get("one_off") or [] if t["id"] != task_id]
    save_settings(store, settings, now=now)


def planned(store: Store, settings: dict, *, now: datetime, days: int = 7) -> list[dict]:
    """The calendar: fixed times, long intervals (6 h and more) and one-off tasks; short
    intervals are summarised by the panel instead of being listed one by one."""
    end = now + timedelta(days=days)
    out = []
    for job, cfg in settings["jobs"].items():
        if job not in JOBS or not cfg.get("enabled"):
            continue
        if cfg.get("mode") == "daily":
            out += [{"at": at, "job": job, "kind": "o godzinie"} for at in slots(cfg, now, end)]
            continue
        every = int(cfg.get("every_minutes") or 0)
        if every >= 360:
            first = next_run(store, job, settings, now=now) or now
            moment = max(first, now)
            while moment <= end:
                out.append({"at": moment, "job": job, "kind": "interwał"})
                moment += timedelta(minutes=every)
    done = done_one_offs(store)
    for task in settings.get("one_off") or []:
        at = datetime.fromisoformat(task["at"])
        if task["id"] not in done and at <= end:
            out.append(
                {"at": max(at, now), "job": task["job"], "kind": "jednorazowo", "id": task["id"]}
            )
    return sorted(out, key=lambda x: x["at"])


# ---------------------------------------------------------------- the worker


class Worker:
    def __init__(
        self,
        paths: AppPaths,
        *,
        parent: int | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        runners: dict | None = None,
    ):
        self.paths = paths
        self.parent = parent
        self.clock = clock
        self.runners = runners or {}
        self.worker_id = uuid.uuid4().hex[:12]
        self.state = "running"
        self.current = None
        self.stats = {
            "processed_events": 0,
            "errors": 0,
            "last_success_at": None,
            "last_error_at": None,
            "last_error": None,
            "catch_up": None,
        }
        self.started_at = self.clock()
        self.next_runs: dict[str, str] = {}
        self._stop = threading.Event()
        self.lock = WorkerLock(folder(paths) / "worker.lock")

    # -- heartbeat ----------------------------------------------------------------
    def beat(self, store: Store | None = None, settings: dict | None = None) -> None:
        now = self.clock()
        if store is not None and settings is not None:
            nexts = {}
            for job in JOBS:
                when = next_run(store, job, settings, now=now)
                if when is not None:
                    nexts[job] = iso(when)
            self.next_runs = nexts
        write_runtime(
            self.paths,
            {
                "worker_id": self.worker_id,
                "pid": os.getpid(),
                "parent": self.parent,
                "state": self.state,
                "started_at": iso(self.started_at),
                "heartbeat_at": iso(now),
                "current_task": self.current,
                **self.stats,
                "next_runs": self.next_runs,
            },
        )

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self.beat()
            except OSError:
                pass

    # -- commands and jobs -------------------------------------------------------
    def _run(
        self, store: Store, job: str, settings: dict, trigger: str, runner: Callable | None = None
    ) -> None:
        self.current = JOB_LABELS.get(job, job)
        self.beat()
        outcome = run_job(
            store,
            self.paths,
            job,
            now=self.clock(),
            settings=settings,
            trigger=trigger,
            runner=self.runners.get(job) or runner,
        )
        self.current = None
        processed = (outcome.payload or {}).get("processed") or 0
        self.stats["processed_events"] += int(processed) if isinstance(processed, int) else 0
        if outcome.status == "failed":
            self.stats["errors"] += 1
            self.stats["last_error_at"] = iso(self.clock())
            self.stats["last_error"] = f"{JOB_LABELS.get(job, job)}: {outcome.reason}"
        else:
            self.stats["last_success_at"] = iso(self.clock())

    def handle(self, store: Store, settings: dict) -> bool:
        """Process new commands; returns False when the worker must stop."""
        for cmd in pending_commands(store):
            name = cmd["command"]
            if name == "stop":
                self.state = "stopping"
            elif name == "pause":
                self.state = "paused"
            elif name in ("start", "resume"):
                self.state = "running"
            elif name == "run_now":
                for job in JOBS:
                    if (settings["jobs"].get(job) or {}).get("enabled"):
                        self._run(store, job, settings, "run_now")
            elif name == "check_coupons":
                self._run(store, "lineups_coupons", settings, "user")
                self._run(store, "coupon_reassessment", settings, "user")
            elif name == "check_events":
                ids = list((cmd.get("payload") or {}).get("event_ids") or [])
                self._run(
                    store,
                    "lineups_events",
                    settings,
                    "user",
                    runner=lambda s, p, *, now, settings, ids=ids: job_lineups_events(
                        s, p, now=now, settings=settings, event_ids=ids
                    ),
                )
            elif name.startswith("run_job:") and name[8:] in JOBS:
                self._run(store, name[8:], settings, "rerun")
            event(
                store,
                "command_processed",
                now=self.clock(),
                worker_id=self.worker_id,
                detail=name,
                payload={"command_id": cmd["id"]},
            )
            if self.state == "stopping":
                return False
        return True

    def tick(self, store: Store) -> bool:
        settings = load_settings(store)
        if not self.handle(store, settings):
            return False
        if self.state == "running":
            for job in due_jobs(store, settings, now=self.clock()):
                self._run(store, job, settings, "schedule")
                if self._stop.is_set():
                    return False
            self._one_offs(store, settings)
        self.beat(store, settings)
        return True

    def _one_offs(self, store: Store, settings: dict) -> None:
        for task in due_one_offs(store, settings, now=self.clock()):
            self._run(store, task["job"], settings, "one_off")
            event(
                store,
                "one_off_done",
                now=self.clock(),
                worker_id=self.worker_id,
                detail=task["job"],
                payload={"id": task["id"]},
            )

    def run(
        self,
        *,
        once: bool = False,
        max_ticks: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        commands_only: bool = False,
    ) -> str:
        if not self.lock.acquire():
            return "inny worker już działa"
        from sports_edge.services import open_store

        store = open_store(self.paths)
        heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat.start()
        try:
            event(
                store,
                "worker_started",
                now=self.clock(),
                worker_id=self.worker_id,
                detail="commands" if commands_only else "once" if once else "run",
                payload={"pid": os.getpid()},
            )
            settings = load_settings(store)
            overdue = due_jobs(store, settings, now=self.clock())
            self.stats["catch_up"] = overdue
            if overdue:
                event(
                    store,
                    "catch_up",
                    now=self.clock(),
                    worker_id=self.worker_id,
                    detail=", ".join(overdue),
                )
            if commands_only:
                self.handle(store, settings)
                return "polecenia wykonane"
            if once:
                self.handle(store, settings)
                for job in due_jobs(store, settings, now=self.clock()):
                    self._run(store, job, settings, "once")
                self._one_offs(store, settings)
                return "jednorazowe uruchomienie zakończone"
            ticks = 0
            while not self._stop.is_set():
                if self.parent and not process_alive(self.parent):
                    event(
                        store,
                        "server_gone",
                        now=self.clock(),
                        worker_id=self.worker_id,
                        detail="serwer aplikacji zakończył pracę",
                    )
                    break
                if not self.tick(store):
                    break
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
                sleep(TICK_SECONDS)
            return "zatrzymany"
        except Exception as error:  # noqa: BLE001 — a crash is recorded, then re-raised
            event(
                store,
                "worker_crashed",
                now=self.clock(),
                worker_id=self.worker_id,
                detail=type(error).__name__,
                payload={"trace": traceback.format_exc()[-800:]},
            )
            raise
        finally:
            self._stop.set()
            self.state = "stopped"
            self.current = None
            try:
                self.beat()
                event(store, "worker_stopped", now=self.clock(), worker_id=self.worker_id)
            finally:
                self.lock.release()


def job_lineups_coupons(store, paths, *, now, settings) -> JobOutcome:
    """Every match of an unsettled coupon now (button "check my coupons")."""
    from sports_edge.services import run_prematch

    app = _app(paths)
    if app.offline_mode:
        return JobOutcome("skipped", "tryb offline — składy nie są pobierane")
    found = run_prematch(store, paths, app, now=now, trigger="user", only_coupons=True, force=True)
    if found.get("blocked"):
        return JobOutcome("skipped", f"źródło składów niedostępne: {found['blocked']}", found)
    return JobOutcome(
        "ok" if not found["errors"] else "partial",
        "",
        {"processed": found["checked"], "new": found["changes"], **found},
    )


def job_lineups_events(store, paths, *, now, settings, event_ids=()) -> JobOutcome:
    """The chosen matches now (e.g. the legs of a coupon that is still being built)."""
    from sports_edge.services import run_prematch

    app = _app(paths)
    if app.offline_mode:
        return JobOutcome("skipped", "tryb offline — składy nie są pobierane")
    if not event_ids:
        return JobOutcome("skipped", "nie wskazano meczów do sprawdzenia")
    found = run_prematch(store, paths, app, now=now, trigger="user", event_ids=list(event_ids))
    if found.get("blocked"):
        return JobOutcome("skipped", f"źródło składów niedostępne: {found['blocked']}", found)
    return JobOutcome(
        "ok" if not found["errors"] else "partial",
        "",
        {"processed": found["checked"], "new": found["changes"], **found},
    )


JOBS_EXTRA = {"lineups_coupons": job_lineups_coupons, "lineups_events": job_lineups_events}
JOB_LABELS["lineups_coupons"] = "Kontrola składów meczów z kuponów (na żądanie)"
JOB_LABELS["lineups_events"] = "Kontrola składów wybranych meczów (na żądanie)"


def _runner(job: str) -> Callable:
    return JOBS.get(job) or JOBS_EXTRA[job]


def history(store: Store, *, limit: int = 100) -> list[dict]:
    rows = store.rows(
        "SELECT * FROM job_runs WHERE job LIKE 'auto:%' ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )
    return [
        {
            **r,
            "payload": json.loads(r["payload_json"] or "{}"),
            "label": JOB_LABELS.get(r["job"][5:], r["job"][5:]),
        }
        for r in rows
    ]


def main(argv: list[str] | None = None) -> int:
    import argparse

    from sports_edge.settings import project_root

    parser = argparse.ArgumentParser(prog="sports_edge.automation")
    parser.add_argument("mode", choices=["run", "once", "commands", "autostart"])
    parser.add_argument("--parent", type=int, default=None)
    args = parser.parse_args(argv)
    paths = AppPaths(project_root())
    if args.mode == "autostart":
        from sports_edge.services import open_store

        print(autostart(paths, open_store(paths), parent=args.parent))
        return 0
    worker = Worker(paths, parent=args.parent)
    print(worker.run(once=args.mode == "once", commands_only=args.mode == "commands"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
