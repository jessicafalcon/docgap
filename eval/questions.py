"""Load the evaluation's questions, and run and store their gold results typed as Parquet.

uv run python -m eval.questions gold [--check]   # the pilot's gold, on the sample
uv run python -m eval.questions check fixture    # every gold query runs on a source
"""

from __future__ import annotations

import argparse
import json
import sys
from enum import StrEnum
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from pydantic import BaseModel

from docgap.artifacts import write_atomic
from docgap.config import load_config
from docgap.grade import MAX_COLUMNS, Result, check
from docgap.models import CONTRACT_CONFIG, NonEmptyStr, Qid
from eval.agent.tools import to_duckdb

__all__ = [
    "PILOT_GOLD",
    "PILOT_QUESTIONS",
    "Category",
    "GoldQuestion",
    "connect",
    "gold_result",
    "load_questions",
    "read_gold",
    "to_result",
    "write_gold",
]

ROOT = Path(__file__).resolve().parents[1]
PILOT_QUESTIONS = ROOT / "eval" / "pilot_questions.yml"
PILOT_GOLD = ROOT / "eval" / "pilot_gold"


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


def connect(database: Path, *, schema: str) -> duckdb.DuckDBPyConnection:
    """Open an agent database read-only with file access off, as the agent's warehouse does.

    Gold SQL then reads the marts alone, the protocol's rule, since nothing else is there.
    """
    con = duckdb.connect(database, read_only=True, config={"enable_external_access": False})
    con.execute(f'USE "{database.stem}"."{schema}"')
    return con


def gold_result(
    con: duckdb.DuckDBPyConnection, question: GoldQuestion, *, max_rows: int
) -> pa.Table:
    """Run a question's gold SQL, transpiled to DuckDB, and return its result with its types.

    Raises:
        ValueError: the result breaks a protocol rule: more than `MAX_COLUMNS` columns,
            more than `max_rows` rows, or a list or a struct.
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
    return table


def to_result(table: pa.Table) -> Result:
    """Turn a result table into the grader's rows, by column position, so names never collide.

    >>> to_result(pa.table({"a": [1, 2], "b": ["x", None]}))
    Result(width=2, rows=((1, 'x'), (2, None)))
    """
    columns = [column.to_pylist() for column in table.columns]
    return Result(table.num_columns, tuple(zip(*columns, strict=True)))


def write_gold(table: pa.Table, path: Path) -> None:
    """Store a gold result as Parquet, whole or not at all."""
    write_atomic(path, lambda sink: pq.write_table(table, sink))  # pyright: ignore[reportUnknownMemberType, reportUnknownLambdaType]


def read_gold(path: Path) -> Result:
    """Read a stored gold result as the grader's rows."""
    # One file, read without the dataset layer, which refuses two columns of one name.
    with pq.ParquetFile(path) as file:
        return to_result(file.read())  # pyright: ignore[reportUnknownMemberType]


def _gold(check_only: bool) -> int:
    config = load_config(ROOT / "docgap.toml")
    database = ROOT / "data" / "agent" / "sample" / f"{config.manifest.mart_database}.duckdb"
    questions = load_questions(PILOT_QUESTIONS)
    PILOT_GOLD.mkdir(exist_ok=True)
    failed = 0
    with connect(database, schema=config.manifest.mart_schema) as con:
        for question in questions:
            table = gold_result(con, question, max_rows=config.agent.row_cap)
            path = PILOT_GOLD / f"{question.id}.parquet"
            if not check_only:
                write_gold(table, path)
            elif not path.exists():
                print(f"{question.id}: no stored gold")
                failed += 1
            elif (
                reason := check(to_result(table), read_gold(path), ordered=question.ordered)
            ) is not None:
                print(f"{question.id}: {reason.value}")
                failed += 1
    stale = sorted({path.stem for path in PILOT_GOLD.glob("*.parquet")} - {q.id for q in questions})
    if stale:
        print(f"gold with no question: {stale}")
        failed += 1
    print(
        f"{len(questions)} gold results {'checked' if check_only else 'written'}, {failed} failed"
    )
    return failed


def _check(source: str) -> int:
    config = load_config(ROOT / "docgap.toml")
    database = ROOT / "data" / "agent" / source / f"{config.manifest.mart_database}.duckdb"
    questions = load_questions(PILOT_QUESTIONS)
    failed = 0
    with connect(database, schema=config.manifest.mart_schema) as con:
        for question in questions:
            try:
                table = gold_result(con, question, max_rows=config.agent.row_cap)
            except (ValueError, duckdb.Error) as error:
                print(f"{question.id}: {type(error).__name__}: {error}")
                failed += 1
            else:
                print(f"{question.id}: {table.num_rows} rows x {table.num_columns} columns")
    return failed


def main(argv: list[str] | None = None) -> None:
    """Write or check the pilot's gold results, or run every gold query on a source."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gold = commands.add_parser("gold", help="run the gold SQL on the sample and store its results")
    gold.add_argument("--check", action="store_true", help="compare with the stored results")
    run = commands.add_parser("check", help="run every gold query on a source's agent database")
    run.add_argument("source", choices=["sample", "fixture"])
    args = parser.parse_args(argv)
    failed = _gold(args.check) if args.command == "gold" else _check(args.source)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
