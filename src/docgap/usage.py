"""Count how each mart column is used: executions, fingerprints, questions and agent runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from docgap.artifacts import read_rows, rows_sha256, write_rows
from docgap.models import (
    CONTRACT_CONFIG,
    Actor,
    ColumnRef,
    ColumnUsage,
    Qid,
    QueryRecord,
    RunId,
    StageRecord,
    canonical_sha256,
)

__all__ = ["COLUMN_USAGE_FILE", "RankingScope", "run_usage", "usage"]

COLUMN_USAGE_FILE = "column_usage.parquet"


def _sorted_unique(qids: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(qids)))


class RankingScope(BaseModel):
    """The traffic the ranking reads: one agent run and its discovery questions (ADR 0015)."""

    model_config = CONTRACT_CONFIG

    run_id: RunId
    # Sorted and deduplicated, so equal scopes hash the same.
    qids: Annotated[tuple[Qid, ...], Field(min_length=1), AfterValidator(_sorted_unique)]


def _in_scope(
    records: Iterable[QueryRecord], scope: RankingScope | None
) -> tuple[list[QueryRecord], dict[str, int]]:
    records = list(records)
    run_ids = sorted({record.run_id for record in records if record.run_id})
    if scope is None:
        # Two runs of the same questions would add up: u doubles and r halves.
        if len(run_ids) > 1:
            raise ValueError(f"tagged traffic from {len(run_ids)} agent runs needs a ranking scope")
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
    return kept, {
        "queries.out_of_scope.other_question": other_question,
        "queries.out_of_scope.other_run": other_run,
        "queries.out_of_scope.untagged": untagged,
    }


def usage(
    records: Iterable[QueryRecord], refs: Iterable[ColumnRef], *, scope: RankingScope | None
) -> tuple[list[ColumnUsage], dict[str, int]]:
    """Count, per mart column, the in-scope queries that touch it, split by actor.

    With a scope, only its run's queries for its questions count; every other query
    is counted by why it was left out. Without one, all traffic counts, and tagged
    traffic from more than one agent run fails. A column appears once per query,
    whatever clauses it is in. Rows cover the columns touched, sorted by FQN.

    Raises:
        ValueError: several agent runs and no scope, a scope that matches no query,
            or a reference to a query that isn't in the snapshot.
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
