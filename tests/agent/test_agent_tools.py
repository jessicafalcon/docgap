"""The agent's tools read names from the warehouse and descriptions from the arm's manifest,
cap results, and turn every failure into text the agent reads."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from docgap.manifest import Marts
from eval.agent.tools import AgentTools, TranspileError, to_duckdb, tool_definitions
from eval.agent.warehouse import Warehouse

FCT = "ANALYTICS.MARTS.FCT_REIMBURSEMENTS"
DOCS = Marts(
    "ANALYTICS",
    "MARTS",
    {"FCT_REIMBURSEMENTS": {"FLX_ANN_MOI": "INTEGER"}, "DIM_REGION": {}},
    frozenset({f"{FCT}.FLX_ANN_MOI"}),
    {"FCT_REIMBURSEMENTS": "One row per source line."},
    {f"{FCT}.FLX_ANN_MOI": "Mois de traitement"},
)


@pytest.fixture
def tools(agent_db: Path) -> Iterator[AgentTools]:
    warehouse = Warehouse(agent_db, database="ANALYTICS", schema="MARTS", timeout_seconds=60)
    yield AgentTools(warehouse, DOCS, row_cap=200)
    warehouse.close()


def _json(tools: AgentTools, name: str, **arguments: object) -> object:
    result = tools.call(name, arguments)
    assert not result.is_error, result.text
    return json.loads(result.text)


def test_list_tables_names_the_marts(tools: AgentTools) -> None:
    assert _json(tools, "list_tables") == ["ANALYTICS.MARTS.DIM_REGION", FCT]


@pytest.mark.parametrize(
    "table",
    [
        "FCT_REIMBURSEMENTS",
        "fct_reimbursements",
        "marts.FCT_REIMBURSEMENTS",
        FCT,
        '"FCT_REIMBURSEMENTS"',
    ],
)
def test_describe_takes_the_name_as_the_agent_writes_it(tools: AgentTools, table: str) -> None:
    assert _json(tools, "describe", table=table) == {
        "table": FCT,
        "description": "One row per source line.",
        "columns": [
            # Types come from the warehouse; a column with no description says so.
            {"name": "FLX_ANN_MOI", "type": "INTEGER", "description": "Mois de traitement"},
            {"name": "BEN_RES_REG", "type": "INTEGER", "description": None},
            {"name": "PRS_PAI_MNT", "type": "DECIMAL(12,2)", "description": None},
        ],
    }


@pytest.mark.parametrize(
    "table", ["NO_SUCH_TABLE", "STAGING.FCT_REIMBURSEMENTS", "X.ANALYTICS.MARTS.FCT_REIMBURSEMENTS"]
)
def test_describe_of_another_table_is_an_error(tools: AgentTools, table: str) -> None:
    result = tools.call("describe", {"table": table})
    assert result.is_error
    assert "list_tables" in result.text


def test_run_sql_shows_names_and_marks_a_truncated_result(tools: AgentTools) -> None:
    shown = _json(
        tools, "run_sql", sql="SELECT FLX_ANN_MOI AS m, PRS_PAI_MNT FROM FCT_REIMBURSEMENTS"
    )
    assert isinstance(shown, dict)
    assert shown["columns"] == ["m", "PRS_PAI_MNT"]
    assert len(shown["rows"]) == 200
    assert shown["truncated"] is True
    # A decimal is shown as its text.
    assert shown["rows"][1] == [202502, "0.25"]


def test_run_sql_transpiles_snowflake_sql(tools: AgentTools) -> None:
    sql = "SELECT IFF(COUNT(*) > 100, 'many', 'few') AS size FROM FCT_REIMBURSEMENTS"
    assert _json(tools, "run_sql", sql=sql) == {
        "columns": ["size"],
        "rows": [["many"]],
        "truncated": False,
    }


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("SELECT FROM WHERE", "TranspileError"),
        ("SELECT 1; SELECT 2", "TranspileError"),
        ("SELECT NO_SUCH_COLUMN FROM FCT_REIMBURSEMENTS", "SqlError"),
    ],
)
def test_failed_sql_is_an_error_the_agent_reads(tools: AgentTools, sql: str, error: str) -> None:
    result = tools.call("run_sql", {"sql": sql})
    assert result.is_error
    assert result.text.startswith(f"{error}: ")


@pytest.mark.parametrize(
    ("name", "arguments"),
    [("drop_table", {}), ("run_sql", {"query": "SELECT 1"}), ("describe", {"table": 1})],
)
def test_unknown_tool_or_input_is_an_error(
    tools: AgentTools, name: str, arguments: dict[str, object]
) -> None:
    assert tools.call(name, arguments).is_error


def test_final_sql_fetches_one_row_past_the_cap(tools: AgentTools) -> None:
    result = tools.run_final("SELECT * FROM FCT_REIMBURSEMENTS")
    assert len(result.rows) == 201


def test_final_sql_that_cannot_transpile_raises() -> None:
    with pytest.raises(TranspileError):
        to_duckdb("SELECT 1 +")


def test_tool_text_states_the_configured_limits() -> None:
    text = json.dumps(tool_definitions(row_cap=200, timeout_seconds=60))
    assert "first 200 rows" in text
    assert "past 60 seconds" in text
