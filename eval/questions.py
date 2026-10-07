"""Load the evaluation's questions, and run and store their gold results typed as Parquet.

uv run python -m eval.questions gold [--check]   # the pilot's gold, on the sample
uv run python -m eval.questions check fixture    # every gold query runs on a source

Each question also declares the other readings of its gold SQL the full docs
support, from the catalog in `SWAPS`: accepted, and stored beside the gold, or ruled
out by a phrase in its text (protocol "Questions" item 5, ADR 0033).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import sqlglot
import yaml
from pydantic import BaseModel, model_validator
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

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
    "SWAPS",
    "Category",
    "GoldQuestion",
    "Swap",
    "gold_files",
    "gold_problems",
    "gold_provenance",
    "gold_result",
    "load_questions",
    "query_problems",
    "read_accepted",
    "read_as",
    "read_gold",
    "readings",
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


FACT = "FCT_REIMBURSEMENTS"


@dataclass(frozen=True, slots=True)
class Swap:
    """Another reading of a gold query that the full docs support.

    Each fact column in `replace` is read as its expression, and `where`, when set,
    filters every SELECT holding a replaced column, as an agent's `WHERE` does: an
    `IFF` inside the sum would keep a group with no row of the type, where a filter
    drops it, and 8 of the sample's 113 care months have no type-0 row.
    """

    replace: Mapping[str, str]
    where: str | None = None


# The full docs' recipe for each `FLT_` measure: `PRS_` filtered on the reimbursement
# type. The two differ on type 99, unknown, where `FLT_` is filled too (ADR 0033).
_FILTERED = {
    "rem_by_type": ("FLT_REM_MNT", "PRS_REM_MNT", "PRS_REM_TYP IN (0, 1)"),
    "pai_by_type": ("FLT_PAI_MNT", "PRS_PAI_MNT", "PRS_REM_TYP = 0"),
    "act_by_type": ("FLT_ACT_QTE", "PRS_ACT_QTE", "PRS_REM_TYP = 0"),
    "dep_by_type": ("FLT_DEP_MNT", "PRS_DEP_MNT", "PRS_REM_TYP = 0"),
}
# Fact columns the docs tell apart and their names don't: one region per actor, and
# the executing provider beside the prescribing one.
_REGIONS = (
    "BEN_RES_REG",
    "EXE_INS_REG",
    "PRE_INS_REG",
    "ORG_CLE_REG",
    "ETE_REG_COD",
    "ETP_REG_COD",
)
_EXECUTING_PRESCRIBING = (
    ("PSE_ACT_SNDS", "PSP_ACT_SNDS"),
    ("PSE_SPE_SNDS", "PSP_SPE_SNDS"),
    ("PSE_ACT_CAT", "PSP_ACT_CAT"),
    ("PSE_STJ_SNDS", "PSP_STJ_SNDS"),
    ("ETE_CAT_SNDS", "ETP_CAT_SNDS"),
)
_PROCESSING_TEXT = "CAST(FLX_ANN_MOI AS VARCHAR)"

SWAPS: Mapping[str, Swap] = {
    # Statutory and supplementary shares: the docs say `PRS_REM_MNT` sums unfiltered.
    "rem_total": Swap({"FLT_REM_MNT": "PRS_REM_MNT"}),
    **{name: Swap({flt: prs}, where) for name, (flt, prs, where) in _FILTERED.items()},
    "care_month": Swap({"FLX_ANN_MOI": "CAST(SOI_ANN || SOI_MOI AS INT)"}),
    "processing_month": Swap(
        {
            "SOI_ANN": f"SUBSTR({_PROCESSING_TEXT}, 1, 4)",
            "SOI_MOI": f"SUBSTR({_PROCESSING_TEXT}, 5, 2)",
        }
    ),
    "prescriber": Swap(dict(_EXECUTING_PRESCRIBING)),
    "executor": Swap({p: e for e, p in _EXECUTING_PRESCRIBING}),
    **{
        f"region_{target.lower()}": Swap({r: target for r in _REGIONS if r != target})
        for target in _REGIONS
    },
}


class GoldQuestion(BaseModel):
    """A question with its gold SQL, in Snowflake SQL over the marts, and its other readings."""

    model_config = CONTRACT_CONFIG

    id: Qid
    category: Category
    text: NonEmptyStr
    gold_sql: NonEmptyStr
    # Rows compare in order only when the question asks for one.
    ordered: bool
    # Swaps whose result a run may give instead of the gold's, each stored beside it.
    accept: tuple[str, ...] = ()
    # Swaps the question's text rules out, each with the phrase that does.
    rule_out: dict[str, NonEmptyStr] = {}

    @model_validator(mode="after")
    def _readings_are_declared_once(self) -> GoldQuestion:
        declared = [*self.accept, *self.rule_out]
        if unknown := sorted(set(declared) - SWAPS.keys()):
            raise ValueError(f"{self.id}: no such swap {unknown}")
        if twice := sorted({name for name in declared if declared.count(name) > 1}):
            raise ValueError(f"{self.id}: swaps declared twice {twice}")
        text = self.text.casefold()
        if missing := sorted(n for n, p in self.rule_out.items() if p.casefold() not in text):
            raise ValueError(f"{self.id}: the text lacks the phrase that rules out {missing}")
        return self


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


def _stem(question: GoldQuestion, swap: str) -> str:
    return f"{question.id}.{swap}"


def gold_files(question: GoldQuestion, gold_dir: Path) -> dict[str, Path]:
    """The files holding a question's gold result and each accepted one, by file stem."""
    stems = [question.id, *(_stem(question, name) for name in sorted(question.accept))]
    return {stem: gold_dir / f"{stem}.parquet" for stem in stems}


def read_accepted(question: GoldQuestion, gold_dir: Path) -> list[Result]:
    """Read the stored results of a question's accepted readings."""
    return [
        read_gold(gold_dir / f"{_stem(question, name)}.parquet") for name in sorted(question.accept)
    ]


def read_as(sql: str, swap: Swap) -> str | None:
    """Return the SQL read the swap's way, or None when it reads none of the swap's columns.

    A column is the fact's when its qualifier names the fact, or when it is
    unqualified in a SELECT whose one source is the fact.

    >>> read_as("SELECT SUM(FLT_PAI_MNT) FROM FCT_REIMBURSEMENTS", SWAPS["pai_by_type"])
    'SELECT SUM(PRS_PAI_MNT) FROM FCT_REIMBURSEMENTS WHERE PRS_REM_TYP = 0'
    >>> read_as("SELECT 1 FROM FCT_REIMBURSEMENTS", SWAPS["rem_total"]) is None
    True

    Raises:
        ValueError: a column the swap replaces is unqualified in a SELECT with several sources.
    """
    tree = sqlglot.parse_one(sql, read="snowflake")
    replaced: list[exp.Column] = []
    filtered: dict[int, tuple[exp.Select, str]] = {}
    for scope in traverse_scope(tree):
        # Unquoted identifiers are case-insensitive in Snowflake: `f` and `F` are one alias.
        facts = {
            alias.upper()
            for alias, source in scope.sources.items()
            if isinstance(source, exp.Table) and source.name.upper() == FACT
        }
        for column in scope.columns:
            if column.name.upper() not in swap.replace:
                continue
            if column.table:
                if column.table.upper() not in facts:
                    continue
            elif not facts:
                continue
            elif len(scope.sources) > 1:
                raise ValueError(f"qualify {column.name}: its SELECT reads several tables")
            replaced.append(column)
            if swap.where is not None and isinstance(scope.expression, exp.Select):
                filtered[id(scope.expression)] = (scope.expression, column.table)
    if not replaced:
        return None

    def qualified(text: str, table: str) -> exp.Expr:
        # The replacement reads the replaced column's table, under the same qualifier.
        expression = sqlglot.parse_one(text, read="snowflake")
        if table:
            for column in expression.find_all(exp.Column):
                column.set("table", exp.to_identifier(table))
        return expression

    for column in replaced:
        expression = qualified(swap.replace[column.name.upper()], column.table)
        # A projection keeps its name, which an `ORDER BY` may read.
        if isinstance(column.parent, exp.Select) and column in column.parent.expressions:
            expression = exp.alias_(expression, column.name)
        column.replace(expression)
    if swap.where is not None:
        for select, table in filtered.values():
            select.where(qualified(swap.where, table), copy=False)
    return tree.sql(dialect="snowflake")


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


# What a gold query or a reading of it can fail with, reported per question.
_QUERY_ERRORS = (ValueError, TranspileError, duckdb.Error)


def readings(
    con: duckdb.DuckDBPyConnection,
    question: GoldQuestion,
    gold: Result,
    *,
    row_cap: int,
    stale_accepts: bool,
) -> tuple[dict[str, tuple[str, pa.Table]], list[str]]:
    """Run every swap of a question's gold SQL; return each accepted result to store with
    the SQL it ran, by file stem, and the problems found.

    A problem is a swap that changes the gold's result and that the question doesn't
    declare, or a declared swap that reads none of the gold SQL's columns. With
    `stale_accepts`, an accepted swap that gives the gold's result is one too. Only on
    the sample: the fixture's few rows can tie readings the sample tells apart.
    """
    accepted: dict[str, tuple[str, pa.Table]] = {}
    problems: list[str] = []
    for name, swap in sorted(SWAPS.items()):
        declared = name in question.accept or name in question.rule_out
        try:
            sql = read_as(question.gold_sql, swap)
            if sql is None:
                if declared:
                    problems.append(f"declares {name}, which reads none of its columns")
                continue
            if name in question.accept:
                # Stored as gold, so held to the gold's rules.
                reading = question.model_copy(update={"gold_sql": sql})
                table = gold_result(con, reading, max_rows=row_cap)
                accepted[_stem(question, name)] = (sql, table)
            else:
                table = con.execute(to_duckdb(sql)).to_arrow_table()
        except _QUERY_ERRORS as error:
            problems.append(f"{name}: {type(error).__name__}: {error}")
            continue
        same = check(to_result(table), gold, ordered=question.ordered) is None
        if not declared and not same:
            problems.append(f"{name} changes the result: accept it, or rule it out in the text")
        elif stale_accepts and same and name in question.accept:
            problems.append(f"accepts {name}, which gives the gold's result")
    return accepted, problems


def _results(
    con: duckdb.DuckDBPyConnection,
    question: GoldQuestion,
    *,
    row_cap: int,
    stale_accepts: bool,
) -> tuple[dict[str, tuple[str, pa.Table]], list[str]]:
    """Run a question's gold SQL and every reading of it.

    Returns each result to store with the SQL it ran, by file stem, and the problems found.
    """
    try:
        gold = gold_result(con, question, max_rows=row_cap)
    except _QUERY_ERRORS as error:
        return {}, [f"{question.id}: {type(error).__name__}: {error}"]
    accepted, found = readings(
        con, question, to_result(gold), row_cap=row_cap, stale_accepts=stale_accepts
    )
    results = {question.id: (question.gold_sql, gold), **accepted}
    return results, [f"{question.id}: {problem}" for problem in found]


def gold_problems(
    con: duckdb.DuckDBPyConnection,
    questions: list[GoldQuestion],
    gold_dir: Path,
    *,
    row_cap: int,
    data_lock: bytes,
    write: bool,
) -> list[str]:
    """Run every gold query and its accepted readings, then store each result or compare
    it with the stored one.

    A failing query is reported and the next one runs. Compared, a result must match
    its stored rows, and the stored file must record this SQL and this data lock.
    Each question's readings are checked as on the sample (`reading_problems`).
    """
    problems: list[str] = []
    if write:
        gold_dir.mkdir(parents=True, exist_ok=True)
    for question in questions:
        results, found = _results(con, question, row_cap=row_cap, stale_accepts=True)
        problems += found
        for stem, (sql, table) in results.items():
            path = gold_dir / f"{stem}.parquet"
            expected = {
                GOLD_SQL_KEY: sha256(sql).encode(),
                DATA_LOCK_KEY: sha256(data_lock).encode(),
            }
            if write:
                write_gold(table, path, gold_sql=sql, data_lock=data_lock)
            elif not path.exists():
                problems.append(f"{stem}: no stored gold")
            elif gold_provenance(path) != expected:
                problems.append(f"{stem}: stored from other SQL or other data")
            elif reason := check(to_result(table), read_gold(path), ordered=question.ordered):
                problems.append(f"{stem}: {reason.value}")
    stored = {path.stem for path in gold_dir.glob("*.parquet")}
    owned = {stem for question in questions for stem in gold_files(question, gold_dir)}
    if stale := sorted(stored - owned):
        problems.append(f"gold with no question: {stale}")
    return problems


def query_problems(
    con: duckdb.DuckDBPyConnection, questions: list[GoldQuestion], *, row_cap: int
) -> list[str]:
    """Run every gold query and its accepted readings, and report each one that fails or
    breaks a protocol rule, and each reading a question leaves undeclared."""
    return [
        problem
        for question in questions
        for problem in _results(con, question, row_cap=row_cap, stale_accepts=False)[1]
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
