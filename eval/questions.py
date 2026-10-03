"""Load the evaluation's questions, and run and store their gold results typed as Parquet.

uv run python -m eval.questions gold [--check]   # the pilot's gold, on the sample
uv run python -m eval.questions check fixture    # every gold query runs on a source
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterator
from enum import StrEnum
from itertools import pairwise
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import sqlglot
import yaml
from pydantic import BaseModel
from sqlglot import exp

from docgap.artifacts import write_atomic
from docgap.config import load_config
from docgap.grade import MAX_COLUMNS, Result, check
from docgap.models import CONTRACT_CONFIG, NonEmptyStr, Qid
from eval.agent.tools import TranspileError, to_duckdb
from eval.agent.warehouse import connect_marts

__all__ = [
    "DATA_LOCK_KEY",
    "GOLD_SQL_KEY",
    "PILOT_GOLD",
    "PILOT_QUESTIONS",
    "Category",
    "GoldQuestion",
    "gold_problems",
    "gold_provenance",
    "gold_result",
    "load_questions",
    "query_problems",
    "read_gold",
    "sha256",
    "to_result",
    "write_gold",
]

ROOT = Path(__file__).resolve().parents[1]
PILOT_QUESTIONS = ROOT / "eval" / "pilot_questions.yml"
PILOT_GOLD = ROOT / "eval" / "pilot_gold"
SAMPLE_LOCK = ROOT / "loader" / "sample.lock"

# Parquet schema metadata naming what a stored gold result was computed from.
GOLD_SQL_KEY = b"docgap.gold_sql_sha256"
DATA_LOCK_KEY = b"docgap.data_lock_sha256"


class Category(StrEnum):
    """The six question categories, the pilot's and the 40's."""

    PHARMACY_DENTAL = "pharmacy_dental"
    REGION = "region"
    AGE_BRACKET = "age_bracket"
    PROVIDER_TYPE = "provider_type"
    PROCESSING_MONTH = "processing_month"
    CARE_VS_PROCESSING = "care_vs_processing"


class GoldQuestion(BaseModel):
    """A question with its gold SQL, in Snowflake SQL over the marts."""

    model_config = CONTRACT_CONFIG

    id: Qid
    category: Category
    text: NonEmptyStr
    gold_sql: NonEmptyStr
    # Rows compare in order only when the question asks for one.
    ordered: bool


class _QuestionFile(BaseModel):
    model_config = CONTRACT_CONFIG

    questions: list[GoldQuestion]


def load_questions(path: Path) -> list[GoldQuestion]:
    """Read a questions file.

    Raises:
        ValueError: a question breaks the contract, or two share an ID.
    """
    # Through JSON, so strict validation reads a category from its text.
    parsed = _QuestionFile.model_validate_json(json.dumps(yaml.safe_load(path.read_text())))
    ids = [question.id for question in parsed.questions]
    if duplicated := sorted({qid for qid in ids if ids.count(qid) > 1}):
        raise ValueError(f"{path.name}: question IDs used twice: {duplicated}")
    return parsed.questions


def sha256(data: str | bytes) -> str:
    """Hash text or bytes, as a stored gold result records its inputs."""
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _check_ties(con: duckdb.DuckDBPyConnection, question: GoldQuestion) -> None:
    """Refuse an order the gold SQL leaves to chance: tied keys in an ordered result, or a
    `LIMIT` cutting through a tie.

    The query runs again without its `LIMIT`, its `ORDER BY` keys added as columns, so
    the rows the cut leaves out are seen too.
    """
    tree = sqlglot.parse_one(question.gold_sql, read="snowflake")
    order, limit = tree.args.get("order"), tree.args.get("limit")
    if order is None:
        if question.ordered or limit is not None:
            raise ValueError(f"{question.id}: an ordered or limited result needs an ORDER BY")
        return
    if not question.ordered and limit is None:
        return
    if not isinstance(tree, exp.Select):
        raise ValueError(f"{question.id}: ties are checked on a plain SELECT only")
    keys = [key.this.copy() for key in order.expressions]
    probe = tree.copy()
    probe.set("limit", None)
    for i, key in enumerate(keys):
        probe = probe.select(exp.alias_(key, f"_docgap_key_{i}"), copy=False)
    rows = con.execute(to_duckdb(probe.sql(dialect="snowflake"))).fetchall()
    ranked = [row[-len(keys) :] for row in rows]
    count = int(limit.expression.this) if limit is not None else len(ranked)
    if question.ordered and any(a == b for a, b in pairwise(ranked[:count])):
        raise ValueError(f"{question.id}: two rows tie on the ORDER BY keys")
    if count < len(ranked) and ranked[count - 1] == ranked[count]:
        raise ValueError(f"{question.id}: the LIMIT cuts through a tie")


def gold_result(
    con: duckdb.DuckDBPyConnection, question: GoldQuestion, *, max_rows: int
) -> pa.Table:
    """Run a question's gold SQL, transpiled to DuckDB, and return its result with its types.

    An unordered result is sorted, so a second run stores the same rows in the same order.

    Raises:
        ValueError: the result breaks a protocol rule: more than `MAX_COLUMNS` columns,
            more than `max_rows` rows, a list or a struct, or an order left to chance.
        TranspileError: sqlglot can't transpile the gold SQL.
        duckdb.Error: the gold SQL fails.
    """
    table = con.execute(to_duckdb(question.gold_sql)).to_arrow_table()
    if table.num_columns > MAX_COLUMNS:
        raise ValueError(f"{question.id}: {table.num_columns} columns; at most {MAX_COLUMNS}")
    if table.num_rows > max_rows:
        raise ValueError(f"{question.id}: {table.num_rows} rows; at most {max_rows}")
    types = zip(table.column_names, table.schema.types, strict=True)
    if nested := [name for name, type_ in types if pa.types.is_nested(type_)]:
        raise ValueError(f"{question.id}: columns holding a list or a struct: {nested}")
    _check_ties(con, question)
    if question.ordered:
        return table
    # By position, then back to the result's names: two columns may share a name.
    positions = [str(i) for i in range(table.num_columns)]
    by_position = table.rename_columns(positions)
    return by_position.sort_by([(name, "ascending") for name in positions]).rename_columns(  # pyright: ignore[reportUnknownMemberType]
        table.column_names
    )


def to_result(table: pa.Table) -> Result:
    """Turn a result table into the grader's rows, by column position, so names never collide.

    >>> to_result(pa.table({"a": [1, 2], "b": ["x", None]}))
    Result(width=2, rows=((1, 'x'), (2, None)))
    """
    columns = [column.to_pylist() for column in table.columns]
    return Result(table.num_columns, tuple(zip(*columns, strict=True)))


def write_gold(table: pa.Table, path: Path, *, gold_sql: str, data_lock: bytes) -> None:
    """Store a gold result as Parquet, whole or not at all, with the hashes of its SQL and data."""
    stored = table.replace_schema_metadata(
        {GOLD_SQL_KEY: sha256(gold_sql), DATA_LOCK_KEY: sha256(data_lock)}
    )
    write_atomic(path, lambda sink: pq.write_table(stored, sink))  # pyright: ignore[reportUnknownMemberType, reportUnknownLambdaType]


def read_gold(path: Path) -> Result:
    """Read a stored gold result as the grader's rows."""
    # One file, read without the dataset layer, which refuses two columns of one name.
    with pq.ParquetFile(path) as file:
        return to_result(file.read())  # pyright: ignore[reportUnknownMemberType]


def gold_provenance(path: Path) -> dict[bytes, bytes]:
    """The hashes a stored gold result records: its SQL's and its data lock's."""
    metadata = pq.read_schema(path).metadata or {}  # pyright: ignore[reportUnknownMemberType]
    return {key: metadata[key] for key in (GOLD_SQL_KEY, DATA_LOCK_KEY) if key in metadata}


def _run(
    con: duckdb.DuckDBPyConnection, questions: list[GoldQuestion], row_cap: int
) -> Iterator[tuple[GoldQuestion, pa.Table | str]]:
    for question in questions:
        try:
            yield question, gold_result(con, question, max_rows=row_cap)
        except (ValueError, TranspileError, duckdb.Error) as error:
            yield question, f"{type(error).__name__}: {error}"


def gold_problems(
    con: duckdb.DuckDBPyConnection,
    questions: list[GoldQuestion],
    gold_dir: Path,
    *,
    row_cap: int,
    data_lock: bytes,
    write: bool,
) -> list[str]:
    """Run every gold query, then store its result or compare it with the stored one.

    A failing query is reported and the next one runs. Compared, a result must match
    its stored rows, and the stored file must record this SQL and this data lock.
    """
    problems: list[str] = []
    if write:
        gold_dir.mkdir(parents=True, exist_ok=True)
    for question, table in _run(con, questions, row_cap):
        path = gold_dir / f"{question.id}.parquet"
        expected = {
            GOLD_SQL_KEY: sha256(question.gold_sql).encode(),
            DATA_LOCK_KEY: sha256(data_lock).encode(),
        }
        if isinstance(table, str):
            problems.append(f"{question.id}: {table}")
        elif write:
            write_gold(table, path, gold_sql=question.gold_sql, data_lock=data_lock)
        elif not path.exists():
            problems.append(f"{question.id}: no stored gold")
        elif gold_provenance(path) != expected:
            problems.append(f"{question.id}: stored from other SQL or other data")
        elif reason := check(to_result(table), read_gold(path), ordered=question.ordered):
            problems.append(f"{question.id}: {reason.value}")
    stored = {path.stem for path in gold_dir.glob("*.parquet")}
    if stale := sorted(stored - {question.id for question in questions}):
        problems.append(f"gold with no question: {stale}")
    return problems


def query_problems(
    con: duckdb.DuckDBPyConnection, questions: list[GoldQuestion], *, row_cap: int
) -> list[str]:
    """Run every gold query and report each one that fails or breaks a protocol rule."""
    return [
        f"{question.id}: {table}"
        for question, table in _run(con, questions, row_cap)
        if isinstance(table, str)
    ]


def main(argv: list[str] | None = None) -> None:
    """Write or check the pilot's gold results, or run every gold query on a source."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gold = commands.add_parser("gold", help="run the gold SQL on the sample and store its results")
    gold.add_argument("--check", action="store_true", help="compare with the stored results")
    run = commands.add_parser("check", help="run every gold query on a source's agent database")
    run.add_argument("source", choices=["sample", "fixture"])
    args = parser.parse_args(argv)
    config = load_config(ROOT / "docgap.toml")
    database, schema = config.manifest.mart_database, config.manifest.mart_schema
    source = "sample" if args.command == "gold" else args.source
    path = ROOT / "data" / "agent" / source / f"{database}.duckdb"
    questions = load_questions(PILOT_QUESTIONS)
    with connect_marts(path, database=database, schema=schema) as con:
        if args.command == "gold":
            problems = gold_problems(
                con,
                questions,
                PILOT_GOLD,
                row_cap=config.agent.row_cap,
                data_lock=SAMPLE_LOCK.read_bytes(),
                write=not args.check,
            )
        else:
            problems = query_problems(con, questions, row_cap=config.agent.row_cap)
    for problem in problems:
        print(problem)
    print(f"{len(questions)} gold queries on {source}, {len(problems)} problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
