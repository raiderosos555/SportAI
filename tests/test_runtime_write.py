"""The worker status file ``data/automation/runtime.json`` under concurrent writers.

The worker writes it from two threads (heartbeat thread and main loop); a fixed temporary
name ``runtime.tmp`` made one ``os.replace`` find the file already moved and the worker died
(FileNotFoundError, 2026-11-06). Every write now has its own temporary file.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sports_edge import automation

WRITERS = 8
WRITES = 60


def _leftovers(app_paths) -> list[str]:
    return sorted(p.name for p in automation.folder(app_paths).iterdir() if p.suffix == ".tmp")


def test_concurrent_threads_never_lose_a_write_or_crash(app_paths):
    barrier = threading.Barrier(WRITERS)
    errors: list[BaseException] = []

    def writer(n: int) -> None:
        barrier.wait()
        for i in range(WRITES):
            try:
                automation.write_runtime(app_paths, {"writer": n, "i": i, "text": "zażółć"})
            except BaseException as error:  # noqa: BLE001 — collected and asserted below
                errors.append(error)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(WRITERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    found = automation.read_runtime(app_paths)
    assert found["text"] == "zażółć" and 0 <= found["writer"] < WRITERS  # whole, valid JSON
    assert _leftovers(app_paths) == []


CHILD = """
import sys, time
from pathlib import Path
from sports_edge import automation
from sports_edge.settings import AppPaths

paths, name, go = AppPaths(Path(sys.argv[1])), sys.argv[2], Path(sys.argv[3])
deadline = time.time() + 30
while not go.exists() and time.time() < deadline:
    time.sleep(0.005)
for i in range(int(sys.argv[4])):
    automation.write_runtime(paths, {"writer": name, "i": i})
"""


def test_two_processes_writing_at_once_both_finish(app_paths, tmp_path):
    src = Path(automation.__file__).resolve().parents[1]  # this checkout, not an installed copy
    paths = [str(src), *filter(None, [os.environ.get("PYTHONPATH")])]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    go = tmp_path / "go"
    children = [
        subprocess.Popen(  # noqa: S603 — fixed interpreter and inline script
            [sys.executable, "-c", CHILD, str(app_paths.root), name, str(go), "300"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for name in ("a", "b")
    ]
    time.sleep(1.0)  # both interpreters are up and waiting
    go.touch()
    outputs = [child.communicate(timeout=120) for child in children]
    for child, (_, err) in zip(children, outputs, strict=True):
        assert child.returncode == 0, err.decode("utf-8", "replace")
    found = json.loads((automation.folder(app_paths) / "runtime.json").read_text("utf-8"))
    assert found["writer"] in ("a", "b") and found["i"] == 299  # the last write of one of them
    assert _leftovers(app_paths) == []


def test_heartbeat_thread_and_main_loop_beat_together(app_paths, monkeypatch):
    """The crash scenario: the heartbeat thread beats non-stop while the main loop beats."""
    monkeypatch.setattr(automation, "HEARTBEAT_SECONDS", 0)
    worker = automation.Worker(app_paths)
    heartbeat = threading.Thread(target=worker._heartbeat_loop, daemon=True)
    heartbeat.start()
    try:
        for _ in range(300):
            worker.beat()  # raised FileNotFoundError with the shared runtime.tmp
    finally:
        worker._stop.set()
        heartbeat.join(timeout=10)
    assert automation.read_runtime(app_paths)["worker_id"] == worker.worker_id
    assert _leftovers(app_paths) == []


def test_transient_permission_error_on_replace_is_retried(app_paths, monkeypatch):
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) < 3:
            raise PermissionError(13, "plik używany przez inny proces")
        return real(src, dst)

    monkeypatch.setattr(automation.os, "replace", flaky)
    automation.write_runtime(app_paths, {"state": "running"})
    assert len(calls) == 3 and automation.read_runtime(app_paths) == {"state": "running"}
    assert _leftovers(app_paths) == []


def test_a_lasting_permission_error_raises_and_leaves_no_temporary_file(app_paths, monkeypatch):
    def locked(src, dst):
        raise PermissionError(13, "plik zablokowany")

    monkeypatch.setattr(automation.os, "replace", locked)
    with pytest.raises(PermissionError):
        automation.write_runtime(app_paths, {"state": "running"})
    assert _leftovers(app_paths) == []


def test_a_failed_status_write_never_stops_the_worker(app_paths, monkeypatch, caplog):
    from test_task008 import _fake_runners

    from sports_edge import services

    store = services.open_store(app_paths)
    automation.command(store, "start", now=automation.datetime.now(automation.UTC))

    def broken(paths, data):
        raise FileNotFoundError(2, "runtime.tmp zniknął")

    monkeypatch.setattr(automation, "write_runtime", broken)
    calls: list[str] = []
    worker = automation.Worker(app_paths, runners=_fake_runners(calls))
    with caplog.at_level("WARNING", logger="sports_edge.automation"):
        assert worker.run(max_ticks=3, sleep=lambda s: None) == "zatrzymany"
    assert calls  # the jobs ran although no status could be written
    assert "runtime.json" in caplog.text
    kinds = [r["kind"] for r in store.rows("SELECT kind FROM automation_events")]
    assert "worker_crashed" not in kinds and "worker_stopped" in kinds
