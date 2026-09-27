"""Redact and fingerprint query history into the query snapshot."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal, NamedTuple

import sqlglot
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.expressions.core import Expression
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from docgap.artifacts import write_rows
from docgap.config import ActorsConfig, SnapshotConfig
from docgap.models import (
    CONTRACT_CONFIG,
    Actor,
    Identifier,
    NonEmptyStr,
    Qid,
    QueryRecord,
    RunId,
    StageRecord,
    UtcDatetime,
)

__all__ = [
    "DropReason",
    "HistoryRow",
    "load_history",
    "normalize",
    "run_snapshot",
    "snapshot",
]

SNAPSHOT_FILE = "query_snapshot.parquet"
_DIALECT = "snowflake"
_AGENT_TAG = "agent"
_REPETITION = re.compile(r"[1-9][0-9]*")
_TAG_ID = TypeAdapter[str](Qid)

# Nodes that carry a value from the query text. Each becomes a placeholder, so no
# value reaches disk. An interval is replaced whole: sqlglot prints its quantity
# inside a string (`INTERVAL '? DAY'`), which would not parse back the same way.
_VALUE_NODES = (
    exp.Literal,
    exp.RawString,
    exp.HexString,
    exp.BitString,
    exp.ByteString,
    exp.National,
    exp.UnicodeString,
    exp.Interval,
)


class DropReason(StrEnum):
    """Why a history row is not in the snapshot. Each is counted, never dropped silently."""

    OUTSIDE_WINDOW = "outside_window"
    UNMAPPED_ROLE = "unmapped_role"
    MALFORMED_TAG = "malformed_tag"
    PARSE_ERROR = "parse_error"
    MULTIPLE_STATEMENTS = "multiple_statements"
    NOT_A_QUERY = "not_a_query"
    UNSTABLE_NORMALIZATION = "unstable_normalization"


class HistoryRow(BaseModel):
    """One row of query history as exported, before redaction: it is never written to disk.

    Field names are the `QUERY_HISTORY` columns, uppercased in the export.
    """

    model_config = CONTRACT_CONFIG | ConfigDict(alias_generator=str.upper)

    query_id: NonEmptyStr
    query_text: NonEmptyStr
    start_time: UtcDatetime
    role_name: NonEmptyStr
    query_tag: str | None
    execution_status: Literal["SUCCESS", "FAIL", "INCIDENT"]
    database_name: Identifier | None
    schema_name: Identifier | None


class _Tag(NamedTuple):
    run_id: RunId
    qid: Qid
    repetition: int


class _Dropped(Exception):
    def __init__(self, reason: DropReason) -> None:
        super().__init__(reason)
        self.reason = reason


def _keeps_its_literal(node: Expression) -> bool:
    # Ordinals (`GROUP BY 1`, `ORDER BY 2`) name a column, type parameters
    # (`NUMBER(10, 2)`) and positional parameters (`$1`) are structure: none is a
    # value, and replacing them would change what the query reads.
    parent = node.parent
    if isinstance(parent, exp.DataTypeParam | exp.Parameter):
        return True
    ordinal = isinstance(node, exp.Literal) and not node.is_string
    return ordinal and (
        isinstance(parent, exp.Group)
        or (isinstance(parent, exp.Ordered) and isinstance(parent.parent, exp.Order))
    )


def _redact(node: Expression) -> Expression:
    if isinstance(node, _VALUE_NODES) and not _keeps_its_literal(node):
        return exp.Placeholder()
    # One placeholder for a list of values, so the list's length doesn't split fingerprints.
    values = node.expressions if isinstance(node, exp.In) else []
    if values and all(isinstance(value, (*_VALUE_NODES, exp.Placeholder)) for value in values):
        node.set("expressions", [exp.Placeholder()])
    return node


def _normalize_once(sql: str) -> str:
    try:
        statements = sqlglot.parse(sql, dialect=_DIALECT)
    except (ParseError, TokenError):
        raise _Dropped(DropReason.PARSE_ERROR) from None
    if len(statements) != 1:
        raise _Dropped(DropReason.MULTIPLE_STATEMENTS)
    tree = statements[0]
    # Only queries: a command (`ALTER SESSION`, `SHOW`) reads no column, and sqlglot
    # keeps the text of a statement it can't parse in a `Command` node.
    if not isinstance(tree, exp.Query):
        raise _Dropped(DropReason.NOT_A_QUERY)
    tree = normalize_identifiers(tree.transform(_redact), dialect=_DIALECT)
    # Comments are free text and may hold values, so they are never printed.
    return tree.sql(dialect=_DIALECT, comments=False)


def normalize(sql: str) -> str:
    """Replace every value in a query with a placeholder and print it canonically.

    Unquoted identifiers are uppercased, as Snowflake resolves them. The result is
    normalized again and must come back unchanged, so a redacted snapshot replays
    through this function as is.

    >>> normalize("select a, sum(b) from t where c = 'x' and d in (1, 2) group by 1 -- note")
    'SELECT A, SUM(B) FROM T WHERE C = ? AND D IN (?) GROUP BY 1'

    Raises:
        ValueError: the query can't be normalized; the message is a `DropReason`.
    """
    try:
        normalized = _normalize_once(sql)
        if _normalize_once(normalized) != normalized:
            raise _Dropped(DropReason.UNSTABLE_NORMALIZATION)
    except _Dropped as dropped:
        raise ValueError(dropped.reason.value) from None
    return normalized


def _parse_tag(tag: str | None) -> _Tag | None:
    # Only agent tags carry a run; other tags and no tag are untagged traffic.
    if not tag or not tag.startswith(f"{_AGENT_TAG}:"):
        return None
    parts = tag.split(":")
    if len(parts) != 4 or not _REPETITION.fullmatch(parts[3]):
        raise _Dropped(DropReason.MALFORMED_TAG)
    try:
        return _Tag(
            _TAG_ID.validate_python(parts[1]), _TAG_ID.validate_python(parts[2]), int(parts[3])
        )
    except ValidationError:
        raise _Dropped(DropReason.MALFORMED_TAG) from None


def _record(row: HistoryRow, actor: Actor) -> QueryRecord:
    tag = _parse_tag(row.query_tag)
    try:
        normalized = normalize(row.query_text)
    except ValueError as error:
        raise _Dropped(DropReason(str(error))) from None
    return QueryRecord(
        query_id=row.query_id,
        start_time=row.start_time,
        role=row.role_name,
        actor=actor,
        run_id=tag.run_id if tag else None,
        qid=tag.qid if tag else None,
        repetition=tag.repetition if tag else None,
        database_name=row.database_name,
        schema_name=row.schema_name,
        succeeded=row.execution_status == "SUCCESS",
        normalized_sql=normalized,
        fingerprint=hashlib.sha256(normalized.encode()).hexdigest(),
    )


def snapshot(
    rows: Iterable[HistoryRow],
    *,
    as_of: datetime,
    window_days: int,
    actors: Mapping[str, Actor],
) -> tuple[list[QueryRecord], dict[str, int]]:
    """Keep the window's queries from mapped roles, redacted, and count every row left out.

    The window is `[as_of - window_days, as_of)`. Failed queries are kept: a query
    that failed still shows which columns the agent reached for. Records are
    sorted by start time, then query ID.

    Raises:
        ValueError: `as_of` is not UTC, or two rows share a query ID.
    """
    if as_of.utcoffset() != timedelta(0):
        raise ValueError("as_of must be in UTC")
    start = as_of - timedelta(days=window_days)
    records: list[QueryRecord] = []
    dropped: Counter[DropReason] = Counter()
    seen: set[str] = set()
    read = 0
    for row in rows:
        read += 1
        if row.query_id in seen:
            raise ValueError(f"query_id {row.query_id} appears twice in the history")
        seen.add(row.query_id)
        try:
            if not start <= row.start_time < as_of:
                raise _Dropped(DropReason.OUTSIDE_WINDOW)
            actor = actors.get(row.role_name)
            if actor is None:
                raise _Dropped(DropReason.UNMAPPED_ROLE)
            records.append(_record(row, actor))
        except _Dropped as drop:
            dropped[drop.reason] += 1
    records.sort(key=lambda record: (record.start_time, record.query_id))
    counts = {
        "read": read,
        "kept": len(records),
        "agent_run_ids": len({record.run_id for record in records if record.run_id}),
    } | {f"dropped.{reason.value}": dropped[reason] for reason in DropReason}
    return records, counts


def load_history(path: Path) -> list[HistoryRow]:
    """Read a history export as JSON Lines, one `QUERY_HISTORY` row per line.

    Raises:
        ValueError: a line breaks the row contract; the message names the line and column.
    """
    rows: list[HistoryRow] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            rows.append(HistoryRow.model_validate_json(line))
        except ValidationError as error:
            raise ValueError(f"{path.name} line {number}: {error}") from None
    return rows


def run_snapshot(
    history: Path,
    *,
    as_of: datetime,
    config: SnapshotConfig,
    actors: ActorsConfig,
    setup_sha256: str,
    out_dir: Path,
) -> StageRecord:
    """Snapshot an offline history export into `query_snapshot.parquet` under `out_dir`."""
    records, counts = snapshot(
        load_history(history),
        as_of=as_of,
        window_days=config.history_window_days,
        actors=actors.root,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    return StageRecord(
        setup_sha256=setup_sha256,
        inputs={"history": hashlib.sha256(history.read_bytes()).hexdigest()},
        outputs={"query_snapshot": write_rows(records, QueryRecord, out_dir / SNAPSHOT_FILE)},
        counts=counts,
        gates={},
    )
