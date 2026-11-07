import os
import shutil
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# TASK 012 §6: the editable install of the shared virtual environment points at the MAIN
# checkout; a worktree (or any copy of the repository) must test its OWN code. ``src`` of
# this checkout goes first on sys.path — before sports_edge is imported anywhere — and on
# PYTHONPATH for the tests that start a subprocess (the automation worker, Streamlit QA).
if not sys.path or Path(sys.path[0]).resolve() != SRC:
    sys.path.insert(0, str(SRC))
os.environ["PYTHONPATH"] = os.pathsep.join(
    [str(SRC), *[p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]]
)
for _name in [m for m in sys.modules if m == "sports_edge" or m.startswith("sports_edge.")]:
    if not str(getattr(sys.modules[_name], "__file__", "") or "").startswith(str(SRC)):
        del sys.modules[_name]  # imported from another checkout before this file ran

import pytest  # noqa: E402

from sports_edge.domain import Event, Market, Selection  # noqa: E402
from sports_edge.settlement import CostProfile  # noqa: E402
from sports_edge.temporal import Timestamps  # noqa: E402


@pytest.fixture
def now():
    return datetime(2026, 10, 31, 12, tzinfo=UTC)


@pytest.fixture
def profile():
    return CostProfile(
        id="synthetic", currency="UNIT", valid_from=date(2026, 1, 1), valid_until=date(2026, 12, 31)
    )


@pytest.fixture
def event(now):
    return Event(
        id="e",
        sport_id="race",
        competition_id="c",
        season_id="s",
        participant_ids=("a", "b", "c", "d"),
        starts_at=now,
        timestamps=Timestamps(ingested_at=now),
    )


@pytest.fixture
def market():
    return Market(
        id="m",
        event_id="e",
        definition_id="total",
        definition_version="1",
        period="match",
        selection_keys=("over", "under"),
        state_keys=("0", "1", "2", "3+"),
        line=Decimal("2.25"),
        settlement_rule_id="total.v1",
        includes_overtime=False,
    )


@pytest.fixture
def selection():
    return Selection(id="s", market_id="m", key="over")


# ---- TASK 002: local application fixtures (synthetic documents, isolated directories)


@pytest.fixture
def app_paths(tmp_path, monkeypatch):
    """Isolated project home with a copy of config/; never touches the real database."""
    shutil.copytree(ROOT / "config", tmp_path / "config")
    monkeypatch.setenv("SPORTS_EDGE_HOME", str(tmp_path))
    from sports_edge.settings import AppPaths

    return AppPaths(tmp_path)


@pytest.fixture
def store():
    from sports_edge.repository import ensure_reference_data
    from sports_edge.storage import Store

    db = Store(":memory:")
    ensure_reference_data(db)
    yield db
    db.close()


@pytest.fixture(scope="session")
def sts_fixture_pdf():
    pytest.importorskip("pymupdf")
    from offer_fixtures import sts_pdf

    return sts_pdf()


@pytest.fixture(scope="session")
def foreign_fixture_pdf():
    """Superbet-like table (synthetic): must be refused by the STS-only importer."""
    pytest.importorskip("pymupdf")
    from offer_fixtures import superbet_pdf

    return superbet_pdf()


@pytest.fixture
def profiles():
    from sports_edge.bookmakers import load_profiles

    return load_profiles(ROOT / "config")


@pytest.fixture
def import_sts(store, sts_fixture_pdf):
    """Import the STS fixture as prospective data observed on 2026-10-31 12:30 UTC."""
    from sports_edge.pdf.engine import parse_pdf
    from sports_edge.repository import import_offer

    result, document = parse_pdf(sts_fixture_pdf, file_name="fixture_sts.pdf")
    return import_offer(
        store,
        result,
        sha256=document.sha256,
        file_name="fixture_sts.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=datetime(2026, 10, 31, 12, 30, tzinfo=UTC),
    )


@pytest.fixture
def rules():
    from sports_edge.bookmakers import load_rules

    return load_rules(ROOT / "config")
