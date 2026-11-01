"""Reproducibility identifiers: code state and data snapshots recorded with every forecast."""

import hashlib
from collections.abc import Iterable
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def code_state() -> str:
    """SHA-256 of the package sources plus git HEAD when present (works without commits)."""
    digest = hashlib.sha256()
    for path in sorted(PACKAGE.rglob("*.py")):
        digest.update(path.relative_to(PACKAGE).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    head = _git_head()
    return f"src-sha256:{digest.hexdigest()[:16]}" + (f"+git:{head[:10]}" if head else "")


def _git_head() -> str | None:
    git = PACKAGE.parents[1] / ".git"
    try:
        ref = (git / "HEAD").read_text(encoding="utf-8").strip()
        if ref.startswith("ref: "):
            return (git / ref[5:]).read_text(encoding="utf-8").strip()
        return ref
    except OSError:
        return None


def data_snapshot(record_ids: Iterable[str]) -> str:
    """Order-independent fingerprint of the exact rows a model saw."""
    ids = sorted(record_ids)
    digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()
    return f"rows:{len(ids)}:sha256:{digest[:16]}"
