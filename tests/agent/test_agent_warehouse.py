"""The offline agent reaches the marts and nothing else, and a long query is cut at the timeout."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import AGENT_MARTS, SLOW_SQL

from docgap.manifest import Marts
from eval.agent.warehouse import SqlError, SqlTimeout, Warehouse, copy_marts

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def warehouse(agent_db: Path) -> Iterator[Warehouse]:
    warehouse = Warehouse(agent_db, database="ANALYTICS", schema="MARTS", timeout_seconds=60)
    yield warehouse
    warehouse.close()


def test_list_of_tables_holds_the_marts_only(warehouse: Warehouse) -> None:
    # The staging table stays behind, and names are uppercased as Snowflake stores them.
    assert warehouse.tables() == [
        "ANALYTICS.MARTS.DIM_REGION",
        "ANALYTICS.MARTS.FCT_REIMBURSEMENTS",
    ]


@pytest.mark.parametrize(
    "sql",
    [
        # The probes of ADR 0026: list the dictionary's folder, read a file, attach another
        # database, and turn file access back on.
        "SELECT count(*) FROM glob('eval/reference/*')",
        f"SELECT * FROM read_text('{ROOT / 'docgap.toml'}')",
        f"SELECT * FROM read_csv('{ROOT / 'docgap.toml'}')",
        f"ATTACH '{ROOT / 'build.duckdb'}' AS other",
        "SET enable_external_access = true",
        # A setting the next run would inherit.
        "SET threads = 1",
    ],
)
def test_a_file_read_fails(warehouse: Warehouse, sql: str) -> None:
    with pytest.raises(SqlError):
        warehouse.query(sql, 10)


def test_qualified_and_bare_names_resolve(warehouse: Warehouse) -> None:
    for table in (
        "ANALYTICS.MARTS.FCT_REIMBURSEMENTS",
        "marts.fct_reimbursements",
        "FCT_REIMBURSEMENTS",
    ):
        names, result = warehouse.query(f"SELECT count(*) AS n FROM {table}", 10)
        assert (names, result.rows) == (["n"], ((300,),))


def test_fetch_stops_at_the_row_limit(warehouse: Warehouse) -> None:
    _, result = warehouse.query("SELECT * FROM FCT_REIMBURSEMENTS", 201)
    assert (result.width, len(result.rows)) == (3, 201)


def test_long_query_is_interrupted_and_the_next_one_runs(agent_db: Path) -> None:
    warehouse = Warehouse(agent_db, database="ANALYTICS", schema="MARTS", timeout_seconds=0.2)
    try:
        with pytest.raises(SqlTimeout, match=r"0\.2 s"):
            warehouse.query(SLOW_SQL, 10)
        assert warehouse.query("SELECT 1", 10)[1].rows == ((1,),)
    finally:
        warehouse.close()


def test_a_file_not_named_for_the_database_is_refused(agent_db: Path, tmp_path: Path) -> None:
    other = agent_db.rename(tmp_path / "OTHER.duckdb")
    with pytest.raises(ValueError, match=r"ANALYTICS\.duckdb"):
        Warehouse(other, database="ANALYTICS", schema="MARTS", timeout_seconds=60)


def test_a_second_copy_replaces_the_first(agent_db: Path) -> None:
    build = agent_db.parents[1] / "build" / "ANALYTICS.duckdb"
    assert copy_marts(build, agent_db.parent, AGENT_MARTS) == agent_db
    assert sorted(path.name for path in agent_db.parent.iterdir()) == ["ANALYTICS.duckdb"]


def test_a_mart_missing_from_the_build_fails_the_copy(agent_db: Path, tmp_path: Path) -> None:
    build = agent_db.parents[1] / "build" / "ANALYTICS.duckdb"
    marts = Marts("ANALYTICS", "MARTS", {**AGENT_MARTS.tables, "DIM_NEW": {}}, {})
    with pytest.raises(ValueError, match="DIM_NEW"):
        copy_marts(build, tmp_path / "other", marts)
