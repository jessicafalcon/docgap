"""Shared pytest options and the fixture chain the stage tests share."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from docgap.artifacts import write_rows
from docgap.config import ManifestConfig
from docgap.models import Actor, QueryRecord
from docgap.snapshot import SNAPSHOT_FILE, load_history, snapshot


def pytest_addoption(parser: pytest.Parser) -> None:
    # Golden files and committed schemas change only when asked, never as a side
    # effect of a test run, so a behavior change always shows as a reviewable diff.
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="Rewrite golden files and committed schemas from the current code.",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


# The chain of hand-made fixtures the stage tests share: the query-history export,
# snapshotted at a fixed as-of, and the dbt manifest.
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HISTORY = FIXTURES / "query_history" / "basic.jsonl"
MANIFEST = FIXTURES / "manifest" / "minimal.json"
AS_OF = datetime(2026, 9, 21, tzinfo=UTC)
ACTORS = {"AGENT_READER": Actor.AGENT}
MARTS_CONFIG = ManifestConfig(mart_database="ANALYTICS", mart_schema="MARTS")
SETUP = "0" * 64


def snapshot_records() -> list[QueryRecord]:
    """The snapshot of the fixture history, as the snapshot stage keeps it."""
    records, _ = snapshot(
        load_history(HISTORY.read_bytes()), as_of=AS_OF, window_days=7, actors=ACTORS
    )
    return records


@pytest.fixture
def snapshot_parquet(tmp_path: Path) -> Path:
    path = tmp_path / "snapshot" / SNAPSHOT_FILE
    path.parent.mkdir()
    write_rows(snapshot_records(), QueryRecord, path)
    return path
