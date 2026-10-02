"""The test agent's three tools offline: `list_tables`, `describe` and `run_sql` over the marts copy."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

import sqlglot
from pydantic import JsonValue
from sqlglot.errors import ErrorLevel, SqlglotError

from docgap.grade import Result
from docgap.manifest import Marts
from eval.agent.warehouse import SqlError, SqlTimeout, Warehouse

__all__ = [
    "FINAL_ANSWER",
    "AgentTools",
    "ToolResult",
    "TranspileError",
    "to_duckdb",
    "tool_definitions",
]

FINAL_ANSWER = "final_answer"


def tool_definitions(*, row_cap: int, timeout_seconds: int) -> list[JsonValue]:
    """The tools as the Messages API takes them: the three tools, then the final answer.

    Keep in sync with `AgentTools.call`. The text is part of the prompt the protocol
    freezes, so a change here is a new `PROMPT_VERSION` in `loop.py`.
    """
    return [
        {
            "name": "list_tables",
            "description": "List the tables you can query, as DATABASE.SCHEMA.TABLE.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "describe",
            "description": (
                "Describe one table: its description, and each column's name, type and"
                " description. A description is null when the table or column has none."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"table": {"type": "string", "description": "The table's name."}},
                "required": ["table"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_sql",
            "description": (
                f"Run one Snowflake SQL query and see its column names and first {row_cap}"
                f" rows. A query running past {timeout_seconds} seconds is cancelled."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "One Snowflake SQL query."}
                },
                "required": ["sql"],
                "additionalProperties": False,
            },
        },
        {
            "name": FINAL_ANSWER,
            "description": (
                "Give your answer: one Snowflake SQL query whose result answers the"
                " question. This ends your work; it is not a tool call."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "final_sql": {"type": "string", "description": "One Snowflake SQL query."}
                },
                "required": ["final_sql"],
                "additionalProperties": False,
            },
        },
    ]


class TranspileError(Exception):
    """sqlglot can't turn the agent's Snowflake SQL into one DuckDB statement."""


def to_duckdb(sql: str) -> str:
    """Transpile one Snowflake statement to DuckDB, as the offline harness runs it (ADR 0014).

    >>> to_duckdb("SELECT IFF(a > 0, 'pos', 'neg') FROM t")
    "SELECT CASE WHEN a > 0 THEN 'pos' ELSE 'neg' END FROM t"

    Raises:
        TranspileError: the text doesn't parse, uses a construct sqlglot can't write
            for DuckDB, or isn't exactly one statement.
    """
    try:
        statements = sqlglot.transpile(
            sql, read="snowflake", write="duckdb", unsupported_level=ErrorLevel.RAISE
        )
    except SqlglotError as error:
        raise TranspileError(str(error)) from None
    if len(statements) != 1:
        raise TranspileError(f"expected one statement, got {len(statements)}")
    return statements[0]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool call returns to the agent: text, and whether it failed."""

    text: str
    is_error: bool = False


def _json(value: object) -> str:
    # `default=str` writes a Decimal, a date or a timestamp as its text.
    return json.dumps(value, ensure_ascii=False, default=str)


class AgentTools:
    """The three tools over one warehouse and one arm's docs.

    `describe` reads names and types from the warehouse, as Snowflake's
    `information_schema` gives them, and descriptions from the arm's dbt manifest,
    which offline stands in for the column comments.
    """

    def __init__(self, warehouse: Warehouse, docs: Marts, *, row_cap: int) -> None:
        self._warehouse = warehouse
        self._docs = docs
        self._row_cap = row_cap

    def call(self, name: str, arguments: Mapping[str, object]) -> ToolResult:
        """Run one tool call. A failure is a result the agent reads, never an exception."""
        match name, dict(arguments):
            case "list_tables", {}:
                return ToolResult(_json(list(self._warehouse.tables())))
            case "describe", {"table": str(table)}:
                return self._describe(table)
            case "run_sql", {"sql": str(sql)}:
                return self._run_sql(sql)
            case _:
                return ToolResult(f"no tool {name} taking {sorted(arguments)}", is_error=True)

    def run_final(self, sql: str) -> Result:
        """Run the agent's final SQL, fetching one row past the cap so a longer result fails.

        Raises:
            TranspileError: sqlglot can't transpile the SQL.
            SqlError: the statement failed.
            SqlTimeout: the statement ran past the timeout.
        """
        return self._warehouse.query(to_duckdb(sql), self._row_cap + 1)[1]

    def _describe(self, table: str) -> ToolResult:
        # The table as the agent may write it: bare or qualified, quoted or not, in any case.
        parts = table.replace('"', "").upper().split(".")
        qualifier = [self._docs.database, self._docs.schema][3 - len(parts) :]
        name = parts[-1]
        columns = self._warehouse.columns(name) if parts[:-1] == qualifier else []
        if not columns:
            return ToolResult(f"no table {table}; list_tables names them", is_error=True)
        prefix = f"{self._docs.database}.{self._docs.schema}.{name}"
        return ToolResult(
            _json(
                {
                    "table": prefix,
                    "description": self._docs.table_descriptions.get(name),
                    "columns": [
                        {
                            "name": column,
                            "type": data_type,
                            "description": self._docs.column_descriptions.get(f"{prefix}.{column}"),
                        }
                        for column, data_type in columns
                    ],
                }
            )
        )

    def _run_sql(self, sql: str) -> ToolResult:
        try:
            names, result = self._warehouse.query(to_duckdb(sql), self._row_cap + 1)
        except (TranspileError, SqlError, SqlTimeout) as error:
            return ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        rows = result.rows[: self._row_cap]
        return ToolResult(
            _json(
                {
                    "columns": list(names),
                    "rows": [list(row) for row in rows],
                    "truncated": len(result.rows) > self._row_cap,
                }
            )
        )
