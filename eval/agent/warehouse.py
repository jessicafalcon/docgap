"""The offline agent's warehouse: a DuckDB copy of the marts alone, opened read-only with file access off."""

from __future__ import annotations

import argparse
import os
import shutil
import threading
from pathlib import Path

import duckdb

from docgap.config import load_config
from docgap.grade import Result
from docgap.manifest import Marts, read_marts

__all__ = ["SqlError", "SqlTimeout", "Warehouse", "connect_marts", "copy_marts"]

ROOT = Path(__file__).resolve().parents[2]


class SqlError(Exception):
    """The statement failed: its message goes back to the agent."""


class SqlTimeout(Exception):
    """The statement ran past the timeout and was interrupted."""


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def copy_marts(build: Path, out_dir: Path, marts: Marts) -> Path:
    """Copy the manifest's mart tables from a dbt build into `<out_dir>/<database>.duckdb`.

    The tables are the manifest's, not every table in the build's schema: dbt never
    drops the table of a renamed or deleted model, and a stale one would reach the
    agent undocumented. DuckDB names a database after its file, so the copy resolves
    the agent's `ANALYTICS.MARTS` names only as `ANALYTICS.duckdb` (ADR 0026). Table
    names are uppercased, as Snowflake stores dbt's unquoted model names. The copy is
    built beside the target and renamed over it, so a crash never leaves half a
    warehouse.

    Raises:
        ValueError: a mart table is missing from the build.
    """
    target = out_dir / f"{marts.database}.duckdb"
    staging = out_dir / f".tmp-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    schema = _quoted(marts.schema)
    try:
        with duckdb.connect(staging / target.name) as con:
            con.execute(f"ATTACH {_literal(build)} AS build (READ_ONLY)")
            built = {
                name.upper(): name
                for (name,) in con.execute(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_catalog = 'build' AND upper(table_schema) = ?"
                    " AND table_type = 'BASE TABLE'",
                    [marts.schema],
                ).fetchall()
            }
            if missing := sorted(set(marts.tables) - set(built)):
                raise ValueError(f"{build}: no table for the marts {missing}")
            con.execute(f"CREATE SCHEMA {schema}")
            for table in sorted(marts.tables):
                con.execute(
                    f"CREATE TABLE {schema}.{_quoted(table)}"
                    f" AS FROM build.{schema}.{_quoted(built[table])}"
                )
            con.execute("DETACH build")
        (staging / target.name).replace(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def connect_marts(path: Path, *, database: str, schema: str) -> duckdb.DuckDBPyConnection:
    """Open a marts copy read-only, with file access off and the configuration locked.

    The connection reads the marts unqualified. The agent's warehouse and the gold
    SQL open the copy through here, so gold reads exactly what the agent can.

    Raises:
        ValueError: the file isn't named for the database it holds.
    """
    if path.stem != database:
        raise ValueError(f"{path}: the file must be named {database}.duckdb")
    # A locked configuration refuses every `SET`, so no run's SQL changes a setting
    # the next run inherits.
    con = duckdb.connect(
        path,
        read_only=True,
        config={"enable_external_access": False, "lock_configuration": True},
    )
    con.execute(f"USE {_quoted(database)}.{_quoted(schema)}")
    return con


class Warehouse:
    """A read-only connection to the marts copy, with DuckDB's file access off.

    With file access on, a query could list `eval/` and open any file there; off, a
    file read or an `ATTACH` fails, and the setting can't be turned back on while
    the database is open (ADR 0026).
    """

    def __init__(self, path: Path, *, database: str, schema: str, timeout_seconds: float) -> None:
        self._con = connect_marts(path, database=database, schema=schema)
        self._database = database
        self._schema = schema
        self._timeout_seconds = timeout_seconds

    def close(self) -> None:
        """Close the connection."""
        self._con.close()

    def tables(self) -> list[str]:
        """Every table and view the connection can see, as `DATABASE.SCHEMA.TABLE`."""
        rows = self._con.execute(
            "SELECT table_catalog, table_schema, table_name FROM information_schema.tables"
            " ORDER BY ALL"
        ).fetchall()
        return [".".join(row) for row in rows]

    def columns(self, table: str) -> list[tuple[str, str]]:
        """A mart table's columns and their types, in table order; empty for no such table."""
        return self._con.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_catalog = ? AND table_schema = ? AND table_name = ?"
            " ORDER BY ordinal_position",
            [self._database, self._schema, table],
        ).fetchall()

    def query(self, sql: str, max_rows: int) -> tuple[list[str], Result]:
        """Run one DuckDB statement and fetch its column names and at most `max_rows` rows.

        DuckDB has no statement timeout, so a timer interrupts the statement. Each
        statement runs on its own cursor: an interrupt that fires just after the
        statement ends lands on a cursor nothing uses again.

        Raises:
            SqlError: the statement failed, or returned no result set.
            SqlTimeout: the statement ran past the timeout.
        """
        cursor = self._con.cursor()
        timer = threading.Timer(self._timeout_seconds, cursor.interrupt)
        try:
            cursor.execute(f"USE {_quoted(self._database)}.{_quoted(self._schema)}")
            timer.start()
            cursor.execute(sql)
            if not cursor.description:
                raise SqlError("the statement returned no result set")
            names = [str(column[0]) for column in cursor.description]
            rows = tuple(tuple(row) for row in cursor.fetchmany(max_rows))
            return names, Result(len(names), rows)
        except duckdb.InterruptException:
            raise SqlTimeout(f"the statement ran past {self._timeout_seconds:g} s") from None
        except duckdb.Error as error:
            raise SqlError(str(error)) from None
        finally:
            timer.cancel()
            # An interrupt already firing finishes before the cursor closes.
            timer.join()
            cursor.close()


def main(argv: list[str] | None = None) -> None:
    """Build the agent's marts-only database from a dbt build and its manifest."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("source", choices=["sample", "fixture"])
    args = parser.parse_args(argv)
    config = load_config(ROOT / "docgap.toml").manifest
    marts, _ = read_marts(ROOT / "warehouse" / "dbt" / "target" / "manifest.json", config)
    build = ROOT / "data" / "warehouse" / args.source / f"{marts.database}.duckdb"
    target = copy_marts(build, ROOT / "data" / "agent" / args.source, marts)
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
