"""Shared pytest options and the fixture chain the stage tests share."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from docgap.artifacts import canonical_lines, write_rows
from docgap.config import ManifestConfig
from docgap.manifest import load_marts
from docgap.models import Actor, ColumnUsage, QueryRecord, RankingScope, StageRecord
from docgap.resolve import resolve
from docgap.snapshot import SNAPSHOT_FILE, load_history, snapshot
from docgap.usage import COLUMN_USAGE_FILE, usage


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
# snapshotted at a fixed as-of, the dbt manifest, and the ranking scope.
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HISTORY = FIXTURES / "query_history" / "basic.jsonl"
MANIFEST = FIXTURES / "manifest" / "minimal.json"
SCOPE_FILE = FIXTURES / "ranking_scope" / "basic.json"
# In the history, q01 and q03 of run BASELINE play the discovery questions and
# q02 a holdout one.
SCOPE = RankingScope.model_validate_json(SCOPE_FILE.read_bytes())
BASELINE = SCOPE.run_id
AS_OF = datetime(2026, 9, 21, tzinfo=UTC)
ACTORS = {"AGENT_READER": Actor.AGENT}
MARTS_CONFIG = ManifestConfig(mart_database="ANALYTICS", mart_schema="MARTS")
MARTS = load_marts(MANIFEST.read_bytes(), MARTS_CONFIG)
GOLDEN = Path(__file__).resolve().parent / "golden"
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


def usage_rows() -> list[ColumnUsage]:
    """The column usage of the fixture snapshot, in the fixture scope."""
    records = snapshot_records()
    refs, _ = resolve(records, MARTS)
    rows, _ = usage(records, refs, scope=SCOPE)
    return rows


@pytest.fixture
def usage_parquet(tmp_path: Path) -> Path:
    path = tmp_path / "usage" / COLUMN_USAGE_FILE
    path.parent.mkdir()
    write_rows(usage_rows(), ColumnUsage, path)
    return path


def assert_golden(
    stage: str, artifact: str, rows: Sequence[BaseModel], record: StageRecord, update: bool
) -> None:
    """Compare a stage's rows and counts with `tests/golden/<stage>/`, or rewrite them on `--update-golden`."""
    directory = GOLDEN / stage
    lines = canonical_lines(rows)
    counts = json.dumps(record.counts, indent=2, sort_keys=True) + "\n"
    if update:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{artifact}.jsonl").write_bytes(lines)
        (directory / "counts.json").write_text(counts)
    assert lines == (directory / f"{artifact}.jsonl").read_bytes()
    assert counts == (directory / "counts.json").read_text()
