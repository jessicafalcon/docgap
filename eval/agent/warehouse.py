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

__all__ = ["SqlError", "SqlTimeout", "Warehouse", "copy_marts"]

ROOT = Path(__file__).resolve().parents[2]


class SqlError(Exception):
    """The statement failed: its message goes back to the agent."""


class SqlTimeout(Exception):
    """The statement ran past the timeout and was interrupted."""


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def copy_marts(build: Path, out_dir: Path, *, database: str, schema: str) -> Path:
    """Copy the mart tables of a dbt build into `<out_dir>/<database>.duckdb`.

    DuckDB names a database after its file, so the copy resolves the agent's
    `ANALYTICS.MARTS` names only as `ANALYTICS.duckdb` (ADR 0026). Table names are
    uppercased, as Snowflake stores dbt's unquoted model names. The copy is built
    beside the target and renamed over it, so a crash never leaves half a warehouse.

    Raises:
        ValueError: the build holds no table in the schema.
    """
    target = out_dir / f"{database}.duckdb"
    staging = out_dir / f".tmp-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with duckdb.connect(staging / target.name) as con:
            con.execute(f"ATTACH {_literal(build)} AS build (READ_ONLY)")
            tables = [
                name
                for (name,) in con.execute(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_catalog = 'build' AND upper(table_schema) = ?"
                    " AND table_type = 'BASE TABLE' ORDER BY table_name",
                    [schema],
                ).fetchall()
            ]
            if not tables:
                raise ValueError(f"{build}: no table in schema {schema}")
            con.execute(f"CREATE SCHEMA {_quoted(schema)}")
            for name in tables:
                con.execute(
                    f"CREATE TABLE {_quoted(schema)}.{_quoted(name.upper())}"
                    f" AS FROM build.{_quoted(schema)}.{_quoted(name)}"
                )
            con.execute("DETACH build")
        (staging / target.name).replace(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


class Warehouse:
    """A read-only connection to the marts copy, with DuckDB's file access off.

    With file access on, a query could list `eval/` and open any file there; off, a
    file read or an `ATTACH` fails, and the setting can't be turned back on while
    the database is open (ADR 0026).
    """

    def __init__(self, path: Path, *, database: str, schema: str, timeout_seconds: float) -> None:
        if path.stem != database:
            raise ValueError(f"{path}: the file must be named {database}.duckdb")
        self._con = duckdb.connect(path, read_only=True, config={"enable_external_access": False})
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
            cursor.close()


def main(argv: list[str] | None = None) -> None:
    """Build the agent's marts-only database from a dbt build's `ANALYTICS.duckdb`."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("source", choices=["sample", "fixture"])
    args = parser.parse_args(argv)
    marts = load_config(ROOT / "docgap.toml").manifest
    build = ROOT / "data" / "warehouse" / args.source / f"{marts.mart_database}.duckdb"
    target = copy_marts(
        build,
        ROOT / "data" / "agent" / args.source,
        database=marts.mart_database,
        schema=marts.mart_schema,
    )
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
