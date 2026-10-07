"""The pilot's questions follow the protocol's rules, and their stored gold results are gradable."""

from __future__ import annotations

import json
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
    SWAPS,
    Category,
    GoldQuestion,
    gold_files,
    gold_problems,
    gold_provenance,
    gold_result,
    load_questions,
    query_problems,
    read_as,
    read_gold,
    readings,
    sha256,
    to_result,
    write_gold,
)

ROOT = Path(__file__).parents[2]
QUESTIONS = load_questions(PILOT_QUESTIONS)
ROW_CAP = load_config(ROOT / "docgap.toml").agent.row_cap


def test_two_pilot_questions_per_category() -> None:
    assert Counter(question.category for question in QUESTIONS) == dict.fromkeys(Category, 2)


def _family(swap: str) -> str | None:
    if swap.endswith("_month"):
        return "month"
    if swap.startswith("region_"):
        return "region"
    return "provider" if swap in {"prescriber", "executor"} else None


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_each_question_settles_two_of_the_month_region_and_provider(
    question: GoldQuestion,
) -> None:
    # Pass 2's level 1: pass 1's questions settled one at least (ADR 0033).
    families = {_family(swap) for swap in question.rule_out} - {None}
    assert len(families) >= 2


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
    assert stored == sorted(
        stem for question in QUESTIONS for stem in gold_files(question, PILOT_GOLD)
    )


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_stored_gold_is_gradable(question: GoldQuestion) -> None:
    for path in gold_files(question, PILOT_GOLD).values():
        gold = read_gold(path)
        assert 0 < len(gold.rows) <= ROW_CAP
        # The grader accepts the width and every value, and the result matches itself.
        assert check(gold, gold, ordered=question.ordered) is None


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_stored_gold_records_its_sql_and_the_sample_it_ran_on(question: GoldQuestion) -> None:
    # Editing the gold SQL or a swap, or locking another sample, without running `gold`
    # again fails here.
    for stem, path in gold_files(question, PILOT_GOLD).items():
        swap = stem.partition(".")[2]
        sql = read_as(question.gold_sql, SWAPS[swap]) if swap else question.gold_sql
        assert sql is not None
        assert gold_provenance(path) == {
            GOLD_SQL_KEY: sha256(sql).encode(),
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


# Reads no column a swap replaces, so the test database needs none of them.
_TOTALS = "SELECT COUNT(*) AS n, SUM(PRS_PAI_MNT) AS paid FROM FCT_REIMBURSEMENTS"


def test_gold_stores_each_result_then_finds_nothing_to_report(
    agent_db: Path, tmp_path: Path
) -> None:
    gold = tmp_path / "gold"
    questions = [_question(_TOTALS, qid="Q1"), _question("SELECT nope", qid="Q2")]
    with _connect(agent_db) as con:
        written = gold_problems(con, questions, gold, row_cap=200, data_lock=b"l", write=True)
        checked = gold_problems(con, questions, gold, row_cap=200, data_lock=b"l", write=False)
    # The failing query is reported each time, and the next one still ran.
    assert [problem.split(":")[0] for problem in written] == ["Q2"]
    assert checked == written
    assert sorted(path.name for path in gold.iterdir()) == ["Q1.parquet"]


def test_gold_check_reports_each_kind_of_drift(agent_db: Path, tmp_path: Path) -> None:
    gold = tmp_path / "gold"
    question = _question(_TOTALS, qid="Q1")
    with _connect(agent_db) as con:
        gold_problems(con, [question], gold, row_cap=200, data_lock=b"l", write=True)
        (gold / "Q9.parquet").write_bytes((gold / "Q1.parquet").read_bytes())
        edited = _question(_TOTALS + " HAVING COUNT(*) > 0", qid="Q1")
        relocked = gold_problems(con, [question], gold, row_cap=200, data_lock=b"m", write=False)
        resql = gold_problems(con, [edited], gold, row_cap=200, data_lock=b"l", write=False)
        missing = gold_problems(
            con,
            [_question(_TOTALS, qid="Q3")],
            gold,
            row_cap=200,
            data_lock=b"l",
            write=False,
        )
        table = pa.table({"n": [1, 2], "paid": [1, 2]})
        write_gold(table, gold / "Q1.parquet", gold_sql=question.gold_sql, data_lock=b"l")
        values = gold_problems(con, [question], gold, row_cap=200, data_lock=b"l", write=False)
    assert relocked == ["Q1: stored from other SQL or other data", "gold with no question: ['Q9']"]
    assert resql == relocked
    assert missing == ["Q3: no stored gold", "gold with no question: ['Q1', 'Q9']"]
    assert values == ["Q1: row_count_mismatch", "gold with no question: ['Q9']"]


def test_check_reports_every_failing_query(agent_db: Path) -> None:
    questions = [_question("SELECT nope", qid="Q1"), _question(_TOTALS, qid="Q2")]
    with _connect(agent_db) as con:
        problems = query_problems(con, questions, row_cap=200)
    assert len(problems) == 1
    assert problems[0].startswith("Q1: BinderException")


@pytest.fixture
def readings_db(tmp_path: Path) -> Path:
    """A fact with a type-99 line, where `FLT_` and the docs' `PRS_` filter part ways,
    and a line processed a month after its care month."""
    path = tmp_path / "readings" / "ANALYTICS.duckdb"
    path.parent.mkdir()
    with duckdb.connect(path) as con:
        con.execute("CREATE SCHEMA MARTS")
        con.execute(
            "CREATE TABLE MARTS.FCT_REIMBURSEMENTS AS SELECT * FROM (VALUES"
            " (202501, '2025', '01', 0, 10.00, 10.00),"
            " (202501, '2025', '01', 1, 0.00, 4.00),"
            " (202502, '2025', '01', 99, 5.00, 7.00),"
            " (202502, '2025', '02', 0, 20.00, 20.00))"
            " v(FLX_ANN_MOI, SOI_ANN, SOI_MOI, PRS_REM_TYP, FLT_PAI_MNT, PRS_PAI_MNT)"
        )
    return path


_CHARGED = "SELECT SUM(FLT_PAI_MNT) FROM FCT_REIMBURSEMENTS"


def _readings(db: Path, question: GoldQuestion, *, stale_accepts: bool = True) -> list[str]:
    with _connect(db) as con:
        gold = to_result(gold_result(con, question, max_rows=200))
        return readings(con, question, gold, row_cap=200, stale_accepts=stale_accepts)[1]


def test_a_reading_that_changes_the_result_must_be_declared(readings_db: Path) -> None:
    # The docs' filter on type 0 drops the type-99 line `FLT_PAI_MNT` holds: 30 against 35.
    assert _readings(readings_db, _question(_CHARGED)) == [
        "pai_by_type changes the result: accept it, or rule it out in the text"
    ]
    accepted = _question(_CHARGED).model_copy(update={"accept": ("pai_by_type",)})
    assert _readings(readings_db, accepted) == []


def test_a_month_read_the_other_way_must_be_declared(readings_db: Path) -> None:
    by_month = _question(
        "SELECT FLX_ANN_MOI, SUM(PRS_PAI_MNT) FROM FCT_REIMBURSEMENTS GROUP BY 1 ORDER BY FLX_ANN_MOI",
        ordered=True,
    )
    assert _readings(readings_db, by_month) == [
        "care_month changes the result: accept it, or rule it out in the text"
    ]
    ruled_out = GoldQuestion(
        id="Q1",
        category=Category.PROCESSING_MONTH,
        text="What was charged in each month claims were processed in?",
        gold_sql=by_month.gold_sql,
        ordered=True,
        rule_out={"care_month": "processed in"},
    )
    assert _readings(readings_db, ruled_out) == []


def test_an_accepted_reading_giving_the_gold_result_is_stale_on_the_sample_only(
    readings_db: Path,
) -> None:
    # Already on type 0, the docs' filter changes nothing.
    question = _question(_CHARGED + " WHERE PRS_REM_TYP = 0").model_copy(
        update={"accept": ("pai_by_type",)}
    )
    assert _readings(readings_db, question) == [
        "accepts pai_by_type, which gives the gold's result"
    ]
    assert _readings(readings_db, question, stale_accepts=False) == []


def test_a_declared_reading_the_gold_never_reads_is_reported(readings_db: Path) -> None:
    question = _question(_CHARGED).model_copy(update={"accept": ("pai_by_type", "rem_total")})
    assert _readings(readings_db, question) == [
        "declares rem_total, which reads none of its columns"
    ]


def test_gold_stores_each_accepted_reading_beside_the_gold(
    readings_db: Path, tmp_path: Path
) -> None:
    gold = tmp_path / "gold"
    question = _question(_CHARGED).model_copy(update={"accept": ("pai_by_type",)})
    with _connect(readings_db) as con:
        written = gold_problems(con, [question], gold, row_cap=200, data_lock=b"l", write=True)
        checked = gold_problems(con, [question], gold, row_cap=200, data_lock=b"l", write=False)
        fixture = query_problems(con, [question], row_cap=200)
    assert written == checked == fixture == []
    assert read_gold(gold / "Q1.parquet").rows == ((Decimal("35.00"),),)
    assert read_gold(gold / "Q1.pai_by_type.parquet").rows == ((Decimal("30.00"),),)


@pytest.mark.parametrize(
    ("declared", "error"),
    [
        ({"accept": ["no_such"]}, r"no such swap \['no_such'\]"),
        (
            {"accept": ["rem_total"], "rule_out": {"rem_total": "t"}},
            r"declared twice \['rem_total'\]",
        ),
        (
            {"rule_out": {"rem_total": "statutory share"}},
            r"lacks the phrase that rules out \['rem_total'\]",
        ),
    ],
)
def test_a_reading_declared_wrong_fails_on_load(declared: dict[str, object], error: str) -> None:
    fields = {
        "id": "Q1",
        "category": "region",
        "text": "t",
        "gold_sql": "SELECT 1",
        "ordered": False,
    }
    with pytest.raises(ValidationError, match=error):
        # Through JSON, as `load_questions` reads a file.
        GoldQuestion.model_validate_json(json.dumps({**fields, **declared}))


_JOINED = (
    "SELECT r.BEN_RES_REG_LIB, SUM(f.FLT_REM_MNT) FROM FCT_REIMBURSEMENTS AS f"
    " JOIN DIM_REGION AS r ON r.BEN_RES_REG = f.BEN_RES_REG GROUP BY 1"
)


def test_a_swap_reads_the_facts_columns_under_their_qualifier() -> None:
    assert read_as(_JOINED, SWAPS["region_exe_ins_reg"]) == _JOINED.replace(
        "= f.BEN_RES_REG", "= f.EXE_INS_REG"
    )
    assert read_as(_JOINED, SWAPS["rem_by_type"]) == (
        _JOINED.replace("f.FLT_REM_MNT", "f.PRS_REM_MNT").replace(
            " GROUP BY", " WHERE f.PRS_REM_TYP IN (0, 1) GROUP BY"
        )
    )


def test_an_unqualified_column_beside_two_tables_fails() -> None:
    with pytest.raises(ValueError, match="qualify FLT_REM_MNT"):
        read_as(_JOINED.replace("f.FLT_REM_MNT", "FLT_REM_MNT"), SWAPS["rem_total"])


def test_a_swap_leaves_a_ctes_columns_alone() -> None:
    sql = "WITH m AS (SELECT FLX_ANN_MOI FROM FCT_REIMBURSEMENTS) SELECT FLX_ANN_MOI FROM m"
    assert read_as(sql, SWAPS["care_month"]) == (
        "WITH m AS (SELECT CAST(SOI_ANN || SOI_MOI AS INT) AS FLX_ANN_MOI FROM FCT_REIMBURSEMENTS)"
        " SELECT FLX_ANN_MOI FROM m"
    )
