"""The pilot's questions follow the protocol's rules, and their stored gold results are gradable."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest
import sqlglot
from pydantic import ValidationError
from sqlglot import exp

from docgap.config import load_config
from docgap.grade import MAX_COLUMNS, check
from eval.agent.tools import to_duckdb
from eval.questions import (
    PILOT_GOLD,
    PILOT_QUESTIONS,
    Category,
    GoldQuestion,
    connect,
    gold_result,
    load_questions,
    read_gold,
    to_result,
    write_gold,
)

ROOT = Path(__file__).parents[2]
QUESTIONS = load_questions(PILOT_QUESTIONS)
ROW_CAP = load_config(ROOT / "docgap.toml").agent.row_cap


def test_two_pilot_questions_per_category() -> None:
    assert Counter(question.category for question in QUESTIONS) == dict.fromkeys(Category, 2)


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_gold_sql_reads_the_marts_only_and_transpiles(question: GoldQuestion) -> None:
    tables = sqlglot.parse_one(question.gold_sql, read="snowflake").find_all(exp.Table)
    assert {(table.catalog, table.db) for table in tables} == {("ANALYTICS", "MARTS")}
    to_duckdb(question.gold_sql)


def test_every_question_has_stored_gold_and_nothing_else_does() -> None:
    stored = sorted(path.stem for path in PILOT_GOLD.glob("*.parquet"))
    assert stored == sorted(question.id for question in QUESTIONS)


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_stored_gold_is_gradable(question: GoldQuestion) -> None:
    gold = read_gold(PILOT_GOLD / f"{question.id}.parquet")
    assert gold.width <= MAX_COLUMNS
    assert 0 < len(gold.rows) <= ROW_CAP
    # The grader accepts every value, and the result matches itself.
    assert check(gold, gold, ordered=question.ordered) is None


def test_a_question_id_used_twice_fails(tmp_path: Path) -> None:
    entry = "- {id: Q1, category: region, text: t, gold_sql: SELECT 1, ordered: false}\n"
    path = tmp_path / "questions.yml"
    path.write_text("questions:\n" + entry + entry)
    with pytest.raises(ValueError, match=r"used twice: \['Q1'\]"):
        load_questions(path)


def test_an_unknown_category_fails(tmp_path: Path) -> None:
    path = tmp_path / "questions.yml"
    path.write_text(
        "questions:\n- {id: Q1, category: optics, text: t, gold_sql: SELECT 1, ordered: false}\n"
    )
    with pytest.raises(ValidationError, match="category"):
        load_questions(path)


def _question(sql: str) -> GoldQuestion:
    return GoldQuestion(id="Q1", category=Category.REGION, text="t", gold_sql=sql, ordered=False)


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("SELECT 1, 2, 3, 4, 5, 6", "6 columns; at most 5"),
        ("SELECT FLX_ANN_MOI FROM FCT_REIMBURSEMENTS", "300 rows; at most 200"),
        ("SELECT ARRAY_CONSTRUCT(1, 2) AS codes", r"a list or a struct: \['codes'\]"),
    ],
)
def test_gold_breaking_a_protocol_rule_fails(agent_db: Path, sql: str, error: str) -> None:
    with connect(agent_db, schema="MARTS") as con, pytest.raises(ValueError, match=error):
        gold_result(con, _question(sql), max_rows=200)


def test_gold_reads_nothing_outside_the_agent_database(agent_db: Path) -> None:
    with connect(agent_db, schema="MARTS") as con, pytest.raises(Exception, match="disabled"):
        gold_result(con, _question("SELECT * FROM read_csv('docgap.toml')"), max_rows=200)


def test_stored_gold_keeps_its_types_and_its_width(tmp_path: Path) -> None:
    table = pa.table(
        [
            pa.array([202501, 202502], pa.int32()),
            pa.array([Decimal("1.50"), None], pa.decimal128(38, 2)),
            pa.array(["Bretagne", "Inconnu"]),
        ],
        names=["m", "m", "label"],
    )
    write_gold(table, tmp_path / "gold.parquet")
    gold = read_gold(tmp_path / "gold.parquet")
    assert gold.rows == ((202501, Decimal("1.50"), "Bretagne"), (202502, None, "Inconnu"))
    write_gold(table.slice(0, 0), tmp_path / "empty.parquet")
    assert read_gold(tmp_path / "empty.parquet").width == 3


def test_to_result_reads_columns_by_position() -> None:
    table = pa.table([pa.array([1]), pa.array([2])], names=["x", "x"])
    assert to_result(table).rows == ((1, 2),)
