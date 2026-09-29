"""Count how each mart column is used: executions, fingerprints, questions and agent runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from docgap.artifacts import read_rows, rows_sha256, write_rows
from docgap.manifest import Marts
from docgap.models import (
    Actor,
    ColumnRef,
    ColumnUsage,
    QueryRecord,
    RankingScope,
    StageRecord,
    canonical_sha256,
)

__all__ = ["COLUMN_USAGE_FILE", "executions", "run_usage", "usage"]

COLUMN_USAGE_FILE = "column_usage.parquet"


def _in_scope(
    records: Sequence[QueryRecord], scope: RankingScope | None
) -> tuple[Sequence[QueryRecord], dict[str, int]]:
    if scope is None:
        # Agent traffic holds holdout questions, and a window can hold several runs of
        # the same questions, which would add up: u doubles and r halves (ADR 0019).
        if any(record.run_id for record in records):
            raise ValueError("tagged agent traffic needs a ranking scope")
        return records, {}
    kept: list[QueryRecord] = []
    other_run = other_question = untagged = 0
    for record in records:
        if record.run_id is None:
            untagged += 1
        elif record.run_id != scope.run_id:
            other_run += 1
        elif record.qid not in scope.qids:
            other_question += 1
        else:
            kept.append(record)
    if not kept:
        raise ValueError(
            f"no query in the snapshot belongs to the ranking scope's run {scope.run_id}"
        )
    # A scope question with no query is a sign of a wrong scope file: every agent
    # run issues at least its final answer.
    unmatched = set(scope.qids) - {record.qid for record in kept}
    return kept, {
        "queries.out_of_scope.other_question": other_question,
        "queries.out_of_scope.other_run": other_run,
        "queries.out_of_scope.untagged": untagged,
        "scope.qids_without_queries": len(unmatched),
    }


def usage(
    records: Iterable[QueryRecord], refs: Iterable[ColumnRef], *, scope: RankingScope | None
) -> tuple[list[ColumnUsage], dict[str, int]]:
    """Count, per mart column, the in-scope queries that touch it, split by actor.

    With a scope, only its run's queries for its questions count; every other query
    is counted by why it was left out. Without one, all traffic counts, and any
    agent-tagged query fails. A column appears once per query, whatever clauses it
    is in. Rows cover the columns touched, sorted by FQN.

    Raises:
        ValueError: tagged traffic and no scope, a scope that matches no query, counted
            traffic that touches no mart column, or a reference to a query that isn't
            in the snapshot.
    """
    records = list(records)
    known = {record.query_id for record in records}
    counted, counts = _in_scope(records, scope)
    by_id = {record.query_id: record for record in counted}
    touched: defaultdict[str, set[str]] = defaultdict(set)
    for ref in refs:
        if ref.query_id not in known:
            raise ValueError(
                f"column reference to query {ref.query_id}, which is not in the snapshot"
            )
        if ref.query_id in by_id:
            touched[ref.fqn].add(ref.query_id)
    # Nothing to rank: an all-zero ranking would look like a result.
    if not touched:
        raise ValueError("no counted query touches a mart column")
    rows: list[ColumnUsage] = []
    for fqn in sorted(touched):
        queries = [by_id[query_id] for query_id in touched[fqn]]
        agent = [query for query in queries if query.actor is Actor.AGENT]
        human = [query for query in queries if query.actor is Actor.HUMAN]
        tagged = [query for query in agent if query.qid is not None]
        rows.append(
            ColumnUsage(
                fqn=fqn,
                executions_agent=len(agent),
                executions_human=len(human),
                fingerprints_agent=len({query.fingerprint for query in agent}),
                fingerprints_human=len({query.fingerprint for query in human}),
                questions=len({query.qid for query in tagged}),
                runs=len({(query.run_id, query.qid, query.repetition) for query in tagged}),
            )
        )
    counts = {
        "queries.read": len(records),
        "queries.in_scope": len(counted),
        "columns": len(rows),
    } | counts
    return rows, counts


def executions(rows: Iterable[ColumnUsage], marts: Marts) -> dict[str, int]:
    """Executions per mart column, agent and human together, and 0 for an untouched column.

    Raises:
        ValueError: a row names a column that isn't in the marts, so the usage was
            resolved against another manifest.
    """
    counts = dict.fromkeys(marts.fqns(), 0)
    for row in rows:
        if row.fqn not in counts:
            raise ValueError(f"column usage for {row.fqn}, which is not a mart column")
        counts[row.fqn] = row.executions_agent + row.executions_human
    return counts


def run_usage(
    snapshot: Path,
    column_refs: Path,
    *,
    scope: RankingScope | None,
    setup_sha256: str,
    out_dir: Path,
) -> StageRecord:
    """Count column usage from the snapshot and `column_refs.parquet` into `column_usage.parquet`."""
    records = read_rows(snapshot, QueryRecord)
    refs = read_rows(column_refs, ColumnRef)
    rows, counts = usage(records, refs, scope=scope)
    inputs = {"column_refs": rows_sha256(refs), "query_snapshot": rows_sha256(records)}
    if scope is not None:
        inputs["ranking_scope"] = canonical_sha256(scope)
    out_dir.mkdir(parents=True, exist_ok=True)
    return StageRecord(
        setup_sha256=setup_sha256,
        inputs=inputs,
        outputs={"column_usage": write_rows(rows, ColumnUsage, out_dir / COLUMN_USAGE_FILE)},
        counts=counts,
        gates={},
    )
