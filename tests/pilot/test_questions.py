"""The pilot's questions follow the protocol's rules, and their stored gold results are gradable."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest
import sqlglot
from pydantic import ValidationError
from sqlglot import exp

from docgap.config import load_config
from docgap.grade import check
from eval.agent.tools import to_duckdb
from eval.agent.warehouse import connect_marts
from eval.questions import (
    DATA_LOCK_KEY,
    GOLD_SQL_KEY,
    PILOT_GOLD,
    PILOT_QUESTIONS,
    SAMPLE_LOCK,
    Category,
    GoldQuestion,
    gold_problems,
    gold_provenance,
    gold_result,
    load_questions,
    query_problems,
    read_gold,
    sha256,
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
    tree = sqlglot.parse_one(question.gold_sql, read="snowflake")
    # A CTE is read by its bare name; every other table must be a mart.
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    tables = [table for table in tree.find_all(exp.Table) if table.name not in ctes or table.db]
    assert {(table.catalog, table.db) for table in tables} == {("ANALYTICS", "MARTS")}
    to_duckdb(question.gold_sql)


def test_every_question_has_stored_gold_and_nothing_else_does() -> None:
    stored = sorted(path.stem for path in PILOT_GOLD.glob("*.parquet"))
    assert stored == sorted(question.id for question in QUESTIONS)


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_stored_gold_is_gradable(question: GoldQuestion) -> None:
    gold = read_gold(PILOT_GOLD / f"{question.id}.parquet")
    assert 0 < len(gold.rows) <= ROW_CAP
    # The grader accepts the width and every value, and the result matches itself.
    assert check(gold, gold, ordered=question.ordered) is None


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_stored_gold_records_its_sql_and_the_sample_it_ran_on(question: GoldQuestion) -> None:
    # Editing the gold SQL, or locking another sample, without running `gold` again fails here.
    assert gold_provenance(PILOT_GOLD / f"{question.id}.parquet") == {
        GOLD_SQL_KEY: sha256(question.gold_sql).encode(),
        DATA_LOCK_KEY: sha256(SAMPLE_LOCK.read_bytes()).encode(),
    }


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


def _connect(agent_db: Path) -> duckdb.DuckDBPyConnection:
    return connect_marts(agent_db, database="ANALYTICS", schema="MARTS")


def _question(sql: str, *, qid: str = "Q1", ordered: bool = False) -> GoldQuestion:
    return GoldQuestion(id=qid, category=Category.REGION, text="t", gold_sql=sql, ordered=ordered)


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("SELECT 1, 2, 3, 4, 5, 6", "6 columns; at most 5"),
        ("SELECT FLX_ANN_MOI FROM FCT_REIMBURSEMENTS", "300 rows; at most 200"),
        ("SELECT ARRAY_CONSTRUCT(1, 2) AS codes", r"a list or a struct: \['codes'\]"),
    ],
)
def test_gold_breaking_a_protocol_rule_fails(agent_db: Path, sql: str, error: str) -> None:
    with _connect(agent_db) as con, pytest.raises(ValueError, match=error):
        gold_result(con, _question(sql), max_rows=200)


# The fixture's fact spreads 300 rows evenly over three months: 100 rows each.
_BY_MONTH = "SELECT FLX_ANN_MOI, COUNT(*) AS n FROM FCT_REIMBURSEMENTS GROUP BY FLX_ANN_MOI"


@pytest.mark.parametrize(
    ("sql", "ordered", "error"),
    [
        (_BY_MONTH + " ORDER BY n", True, "two rows tie on the ORDER BY keys"),
        (_BY_MONTH + " ORDER BY n DESC LIMIT 1", False, "the LIMIT cuts through a tie"),
        (_BY_MONTH, True, "needs an ORDER BY"),
        (_BY_MONTH + " LIMIT 1", False, "needs an ORDER BY"),
    ],
)
def test_an_order_left_to_chance_fails(agent_db: Path, sql: str, ordered: bool, error: str) -> None:
    with _connect(agent_db) as con, pytest.raises(ValueError, match=error):
        gold_result(con, _question(sql, ordered=ordered), max_rows=200)


def test_an_order_on_distinct_keys_passes(agent_db: Path) -> None:
    question = _question(_BY_MONTH + " ORDER BY FLX_ANN_MOI DESC LIMIT 2", ordered=True)
    with _connect(agent_db) as con:
        table = gold_result(con, question, max_rows=200)
    assert to_result(table).rows == ((202503, 100), (202502, 100))


def test_an_unordered_result_is_stored_sorted(agent_db: Path) -> None:
    with _connect(agent_db) as con:
        table = gold_result(con, _question("SELECT BEN_RES_REG_LIB FROM DIM_REGION"), max_rows=200)
    assert to_result(table).rows == (("Centre-Val de Loire",), ("Ile-de-France",))


def test_gold_reads_nothing_outside_the_agent_database(agent_db: Path) -> None:
    with _connect(agent_db) as con, pytest.raises(Exception, match="disabled"):
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
    write_gold(table, tmp_path / "gold.parquet", gold_sql="SELECT 1", data_lock=b"lock")
    gold = read_gold(tmp_path / "gold.parquet")
    assert gold.rows == ((202501, Decimal("1.50"), "Bretagne"), (202502, None, "Inconnu"))
    write_gold(table.slice(0, 0), tmp_path / "empty.parquet", gold_sql="SELECT 1", data_lock=b"")
    assert read_gold(tmp_path / "empty.parquet").width == 3


def test_to_result_reads_columns_by_position() -> None:
    table = pa.table([pa.array([1]), pa.array([2])], names=["x", "x"])
    assert to_result(table).rows == ((1, 2),)


def test_gold_stores_each_result_then_finds_nothing_to_report(
    agent_db: Path, tmp_path: Path
) -> None:
    gold = tmp_path / "gold"
    questions = [_question(_BY_MONTH, qid="Q1"), _question("SELECT nope", qid="Q2")]
    with _connect(agent_db) as con:
        written = gold_problems(con, questions, gold, row_cap=200, data_lock=b"l", write=True)
        checked = gold_problems(con, questions, gold, row_cap=200, data_lock=b"l", write=False)
    # The failing query is reported each time, and the next one still ran.
    assert [problem.split(":")[0] for problem in written] == ["Q2"]
    assert checked == written
    assert sorted(path.name for path in gold.iterdir()) == ["Q1.parquet"]


def test_gold_check_reports_each_kind_of_drift(agent_db: Path, tmp_path: Path) -> None:
    gold = tmp_path / "gold"
    question = _question(_BY_MONTH, qid="Q1")
    with _connect(agent_db) as con:
        gold_problems(con, [question], gold, row_cap=200, data_lock=b"l", write=True)
        (gold / "Q9.parquet").write_bytes((gold / "Q1.parquet").read_bytes())
        edited = _question(_BY_MONTH + " HAVING COUNT(*) > 0", qid="Q1")
        relocked = gold_problems(con, [question], gold, row_cap=200, data_lock=b"m", write=False)
        resql = gold_problems(con, [edited], gold, row_cap=200, data_lock=b"l", write=False)
        missing = gold_problems(
            con,
            [_question(_BY_MONTH, qid="Q3")],
            gold,
            row_cap=200,
            data_lock=b"l",
            write=False,
        )
        table = pa.table({"FLX_ANN_MOI": [202501], "n": [1]})
        write_gold(table, gold / "Q1.parquet", gold_sql=question.gold_sql, data_lock=b"l")
        values = gold_problems(con, [question], gold, row_cap=200, data_lock=b"l", write=False)
    assert relocked == ["Q1: stored from other SQL or other data", "gold with no question: ['Q9']"]
    assert resql == relocked
    assert missing == ["Q3: no stored gold", "gold with no question: ['Q1', 'Q9']"]
    assert values == ["Q1: row_count_mismatch", "gold with no question: ['Q9']"]


def test_check_reports_every_failing_query(agent_db: Path) -> None:
    questions = [_question("SELECT nope", qid="Q1"), _question(_BY_MONTH, qid="Q2")]
    with _connect(agent_db) as con:
        problems = query_problems(con, questions, row_cap=200)
    assert len(problems) == 1
    assert problems[0].startswith("Q1: BinderException")
