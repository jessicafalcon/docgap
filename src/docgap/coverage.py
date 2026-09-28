"""Measure documentation coverage by columns and by how often the columns are queried."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from docgap.artifacts import read_rows, rows_sha256
from docgap.config import ManifestConfig
from docgap.manifest import Marts, read_marts
from docgap.models import ColumnUsage, StageRecord
from docgap.usage import executions

__all__ = ["coverage", "run_coverage"]


def coverage(marts: Marts, usage: Iterable[ColumnUsage]) -> dict[str, int]:
    """Count the documented mart columns, and the executions that touch them.

    Plain coverage is `columns.documented / columns.total`, and usage-weighted
    coverage is `executions.documented / executions.total`. An execution is one
    counted query touching one column, so a query that reads five columns counts
    five times. The counts stay whole numbers; the report divides them.

    Raises:
        ValueError: a usage row names a column that isn't in the marts.
    """
    counts = executions(usage, marts)
    return {
        "columns.documented": len(marts.documented),
        "columns.total": len(counts),
        "executions.documented": sum(counts[fqn] for fqn in marts.documented),
        "executions.total": sum(counts.values()),
    }


def run_coverage(
    column_usage: Path, manifest: Path, *, config: ManifestConfig, setup_sha256: str
) -> StageRecord:
    """Record both coverage numbers' counts from `column_usage.parquet` and the dbt manifest.

    The stage writes no artifact: its counts in the run manifest are its output.
    """
    rows = read_rows(column_usage, ColumnUsage)
    marts, manifest_sha256 = read_marts(manifest, config)
    return StageRecord(
        setup_sha256=setup_sha256,
        inputs={"column_usage": rows_sha256(rows), "manifest": manifest_sha256},
        outputs={},
        counts=coverage(marts, rows),
        gates={},
    )
