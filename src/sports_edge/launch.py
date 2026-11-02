"""Launcher of the application (start.bat / start.ps1): interface + optional worker.

1. starts the Streamlit server (127.0.0.1 only);
2. if the user switched on "AUTOMATYCZNIE WZNÓW PO STARCIE SERWERA" in AUTOMATYZACJA AI and
   did not stop the automation, starts the worker bound to that server process — without
   any browser session;
3. waits for the server; when it ends (window closed, Ctrl+C), the worker notices that its
   server is gone and stops after the current operation.

No Windows Task Scheduler and no system service are involved.
"""

import argparse
import subprocess
import sys
import time

from sports_edge.settings import AppPaths, project_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sports_edge.launch")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args(argv)
    paths = AppPaths(project_root())
    server = subprocess.Popen(  # noqa: S603 — fixed interpreter and module
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(paths.root / "src" / "sports_edge" / "app" / "main.py"),
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(args.port),
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=str(paths.root),
    )
    time.sleep(2)
    try:
        from sports_edge import automation
        from sports_edge.services import open_store

        print(
            "[Sports Edge AI] Automatyzacja:",
            automation.autostart(paths, open_store(paths), parent=server.pid),
        )
    except Exception as error:  # noqa: BLE001 — the interface must start anyway
        print(f"[Sports Edge AI] Automatyzacja nie wystartowała: {type(error).__name__}")
    try:
        return server.wait()
    except KeyboardInterrupt:
        server.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
