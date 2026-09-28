"""The coverage stage: documented columns, and the executions that touch them."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from conftest import MANIFEST, MARTS, MARTS_CONFIG, SETUP, usage_rows

from docgap.artifacts import read_rows, rows_sha256
from docgap.coverage import coverage, run_coverage
from docgap.models import ColumnUsage


def test_coverage_counts_columns_and_executions(usage_parquet: Path) -> None:
    stage = run_coverage(usage_parquet, MANIFEST, config=MARTS_CONFIG, setup_sha256=SETUP)
    # 2 of the 12 mart columns have a description, and they carry 4 of the 6
    # executions in scope: PRS_PAI_MNT 3 and FLX_ANN_MOI 1.
    assert stage.counts == {
        "columns.documented": 2,
        "columns.total": 12,
        "executions.documented": 4,
        "executions.total": 6,
    }
    assert stage.outputs == {}
    assert stage.inputs == {
        "column_usage": rows_sha256(read_rows(usage_parquet, ColumnUsage)),
        "manifest": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
    }


def test_human_executions_count_too() -> None:
    row = usage_rows()[0].model_copy(update={"executions_human": 5, "fingerprints_human": 1})
    counts = coverage(MARTS, [row])
    assert counts["executions.total"] == row.executions_agent + 5


def test_a_column_missing_from_the_manifest_fails() -> None:
    stray = usage_rows()[0].model_copy(update={"fqn": "ANALYTICS.MARTS.FCT_REIMBURSEMENTS.GONE"})
    with pytest.raises(ValueError, match=r"GONE, which is not a mart column"):
        coverage(MARTS, [stray])
