"""The rank stage: every undocumented column, by score, then by FQN."""

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path

import pytest
from conftest import MANIFEST, MARTS, MARTS_CONFIG, SETUP, assert_golden, usage_rows
from hypothesis import given, settings
from hypothesis import strategies as st

from docgap.artifacts import read_rows
from docgap.config import RankConfig
from docgap.models import ColumnUsage, RankedGap
from docgap.rank import RANKED_GAPS_FILE, rank, run_rank, score

FCT = "ANALYTICS.MARTS.FCT_REIMBURSEMENTS"


def test_rank_matches_golden(usage_parquet: Path, tmp_path: Path, update_golden: bool) -> None:
    stage = run_rank(
        usage_parquet,
        MANIFEST,
        manifest_config=MARTS_CONFIG,
        config=RankConfig(w=1),
        setup_sha256=SETUP,
        out_dir=tmp_path / "rank",
    )
    rows = read_rows(tmp_path / "rank" / RANKED_GAPS_FILE, RankedGap)
    assert_golden("rank", "ranked_gaps", rows, stage, update_golden)


def test_only_undocumented_columns_rank_and_untouched_ones_come_last() -> None:
    rows = rank(MARTS, usage_rows(), w=1)
    assert {row.fqn for row in rows} == set(MARTS.fqns()) - MARTS.documented
    # BEN_SEX_COD and PRS_NAT are the two touched gaps, one execution each.
    assert [row.fqn for row in rows[:2]] == [f"{FCT}.BEN_SEX_COD", f"{FCT}.PRS_NAT"]
    assert all(row.executions == 0 and row.score == 0 for row in rows[2:])
    assert [row.rank for row in rows] == list(range(1, len(rows) + 1))


def test_tie_on_score_orders_by_fqn() -> None:
    rows = rank(MARTS, usage_rows(), w=1)
    for before, after in pairwise(rows):
        assert (-before.score, before.fqn) < (-after.score, after.fqn)


def test_more_executions_rank_higher() -> None:
    heavy = next(row for row in usage_rows() if row.fqn == f"{FCT}.PRS_NAT")
    heavy = heavy.model_copy(update={"executions_human": 9, "fingerprints_human": 1})
    rows = rank(MARTS, [heavy], w=1)
    assert (rows[0].fqn, rows[0].executions) == (f"{FCT}.PRS_NAT", 10)


@settings(max_examples=25, deadline=None)
@given(st.permutations(usage_rows()))
def test_usage_row_order_does_not_change_the_ranking(rows: list[ColumnUsage]) -> None:
    assert rank(MARTS, rows, w=1) == rank(MARTS, usage_rows(), w=1)


@pytest.mark.parametrize("executions", [0, 1, 2, 7, 100, 12_345])
def test_score_is_the_natural_log_of_one_plus_usage(executions: int) -> None:
    assert score(executions, 0.0, 1.0) == pytest.approx(math.log1p(executions), rel=1e-15)


def test_failure_rate_lifts_the_score_by_w() -> None:
    assert score(3, 0.5, 2.0) == pytest.approx(2 * math.log(4), rel=1e-15)
