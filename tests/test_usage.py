"""The usage stage: counts per column, and the ranking scope that keeps them to one run's discovery traffic."""

from __future__ import annotations

import hashlib
import random
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import (
    BASELINE,
    MANIFEST,
    MARTS,
    MARTS_CONFIG,
    SCOPE,
    SETUP,
    assert_golden,
    snapshot_records,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from docgap.artifacts import read_rows, rows_sha256
from docgap.models import (
    Actor,
    Clause,
    ColumnRef,
    ColumnUsage,
    QueryRecord,
    RankingScope,
    canonical_sha256,
)
from docgap.resolve import COLUMN_REFS_FILE, resolve, run_resolve
from docgap.usage import COLUMN_USAGE_FILE, run_usage, usage

FQN = "ANALYTICS.MARTS.FCT_REIMBURSEMENTS.PRS_PAI_MNT"


def _inputs() -> tuple[list[QueryRecord], list[ColumnRef]]:
    records = snapshot_records()
    refs, _ = resolve(records, MARTS)
    return records, refs


def _query(query_id: str, sql: str, **fields: object) -> QueryRecord:
    """A snapshot record for `sql`, tagged into the baseline run unless overridden."""
    template = snapshot_records()[0]
    return template.model_copy(
        update={
            "query_id": query_id,
            "start_time": template.start_time + timedelta(minutes=1),
            "normalized_sql": sql,
            "fingerprint": hashlib.sha256(sql.encode()).hexdigest(),
            **fields,
        }
    )


def test_usage_matches_golden(snapshot_parquet: Path, tmp_path: Path, update_golden: bool) -> None:
    run_resolve(
        snapshot_parquet, MANIFEST, config=MARTS_CONFIG, setup_sha256=SETUP, out_dir=tmp_path
    )
    stage = run_usage(
        snapshot_parquet,
        tmp_path / COLUMN_REFS_FILE,
        scope=SCOPE,
        setup_sha256=SETUP,
        out_dir=tmp_path / "usage",
    )
    rows = read_rows(tmp_path / "usage" / COLUMN_USAGE_FILE, ColumnUsage)
    assert_golden("usage", "column_usage", rows, stage, update_golden)
    assert stage.outputs == {"column_usage": rows_sha256(rows)}
    assert stage.inputs["ranking_scope"] == canonical_sha256(SCOPE)


# Evaluation integrity


def _refs_for(records: list[QueryRecord], refs: list[ColumnRef]) -> list[ColumnRef]:
    ids = {record.query_id for record in records}
    return [ref for ref in refs if ref.query_id in ids]


def test_holdout_and_another_runs_queries_change_no_ranking_input() -> None:
    records, refs = _inputs()
    before, _ = usage(records, refs, scope=SCOPE)
    # A holdout question in the scoped run and a discovery question in another run,
    # both touching a column already counted and one nothing else touches.
    sql = "SELECT PRS_PAI_MNT, SOI_ANN FROM FCT_REIMBURSEMENTS"
    extra = [
        _query("holdout", sql, qid="q02"),
        _query("other-run", sql, run_id="20260921T090000Z-5e6f7a8b"),
    ]
    extra_refs, _ = resolve(extra, MARTS)
    assert {ref.fqn for ref in extra_refs} >= {FQN}
    after, counts = usage(records + extra, refs + extra_refs, scope=SCOPE)
    assert after == before
    assert counts["queries.out_of_scope.other_question"] == 3
    assert counts["queries.out_of_scope.other_run"] == 3


@pytest.mark.parametrize("runs", [{BASELINE}, None], ids=["one_run", "two_runs"])
def test_tagged_traffic_needs_a_scope(runs: set[str] | None) -> None:
    # One run's traffic holds its holdout questions too (q02 here), so it needs a scope.
    records, refs = _inputs()
    if runs is not None:
        records = [record for record in records if record.run_id in runs]
    with pytest.raises(ValueError, match="tagged agent traffic needs a ranking scope"):
        usage(records, _refs_for(records, refs), scope=None)


def test_without_a_scope_untagged_traffic_counts_whole() -> None:
    records, refs = _inputs()
    untagged = [record for record in records if record.run_id is None]
    rows, counts = usage(untagged, _refs_for(untagged, refs), scope=None)
    assert counts["queries.in_scope"] == len(untagged)
    # The untagged `SELECT *` on DIM_REGION counts as execution, but in no run.
    region = next(row for row in rows if row.fqn.endswith("DIM_REGION.REG_LIB"))
    assert (region.executions_agent, region.questions, region.runs) == (1, 0, 0)


def test_counted_traffic_touching_no_mart_column_fails() -> None:
    records, _ = _inputs()
    with pytest.raises(ValueError, match="no counted query touches a mart column"):
        usage(records, [], scope=SCOPE)


def test_a_scope_question_with_no_query_is_counted() -> None:
    records, refs = _inputs()
    scope = RankingScope(run_id=BASELINE, qids=("q01", "q03", "q99"))
    _, counts = usage(records, refs, scope=scope)
    assert counts["scope.qids_without_queries"] == 1


def test_a_scope_that_matches_no_query_fails() -> None:
    records, refs = _inputs()
    scope = RankingScope(run_id="20260101T000000Z-00000000", qids=("q01",))
    with pytest.raises(ValueError, match="no query in the snapshot belongs"):
        usage(records, refs, scope=scope)


def test_a_reference_to_an_unknown_query_fails() -> None:
    records, refs = _inputs()
    stray = ColumnRef(query_id="nowhere", fqn=FQN, clause=Clause.SELECT)
    with pytest.raises(ValueError, match="query nowhere"):
        usage(records, [*refs, stray], scope=SCOPE)


def test_equal_scopes_hash_the_same() -> None:
    same = RankingScope(run_id=BASELINE, qids=("q01", "q03", "q01"))
    assert canonical_sha256(same) == canonical_sha256(SCOPE)


# Counting


def test_a_column_counts_once_per_query_across_clauses() -> None:
    record = _query("q", "SELECT A FROM T WHERE A = ? GROUP BY A")
    refs = [
        ColumnRef(query_id="q", fqn=FQN, clause=clause) for clause in (Clause.SELECT, Clause.WHERE)
    ]
    rows, _ = usage([record], refs, scope=SCOPE)
    assert [(row.executions_agent, row.fingerprints_agent, row.runs) for row in rows] == [(1, 1, 1)]


def test_runs_count_repetitions_and_fingerprints_count_distinct_sql() -> None:
    sql = "SELECT PRS_PAI_MNT FROM FCT_REIMBURSEMENTS"
    records = [_query("a1", sql, repetition=1), _query("a2", sql, repetition=2)]
    refs = [ColumnRef(query_id=r.query_id, fqn=FQN, clause=Clause.SELECT) for r in records]
    rows, _ = usage(records, refs, scope=SCOPE)
    assert rows == [
        ColumnUsage(
            fqn=FQN,
            executions_agent=2,
            executions_human=0,
            fingerprints_agent=1,
            fingerprints_human=0,
            questions=1,
            runs=2,
        )
    ]


def test_untagged_counts_split_by_actor() -> None:
    sql = "SELECT PRS_PAI_MNT FROM FCT_REIMBURSEMENTS"
    untagged = {"run_id": None, "qid": None, "repetition": None}
    records = [
        _query("a1", sql, **untagged),
        _query("a2", sql + " LIMIT ?", **untagged),
        _query("h1", sql, actor=Actor.HUMAN, **untagged),
    ]
    refs = [ColumnRef(query_id=r.query_id, fqn=FQN, clause=Clause.SELECT) for r in records]
    rows, _ = usage(records, refs, scope=None)
    assert [
        (row.executions_agent, row.executions_human, row.fingerprints_agent, row.runs)
        for row in rows
    ] == [(2, 1, 2, 0)]


@settings(max_examples=25, deadline=None)
@given(st.randoms(use_true_random=False))
def test_input_order_does_not_change_usage(rng: random.Random) -> None:
    records, refs = _inputs()
    expected = usage(records, refs, scope=SCOPE)
    rng.shuffle(records)
    rng.shuffle(refs)
    assert usage(records, refs, scope=SCOPE) == expected
