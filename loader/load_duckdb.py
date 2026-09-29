"""Load the offline sample or the CI fixture into DuckDB's `RAW.DAMIR.PRESTATIONS`.

The offline stand-in for the Snowflake PUT and `COPY`: the same table, from the same
DDL, over files pinned by `loader/sample.lock` (ADR 0013). Each file is checked
against the lock and its header against the DDL, then loaded in one transaction with
its row in `RAW.DAMIR.LOADED_FILES`, so a re-run loads nothing twice.

    uv run python loader/load_duckdb.py {sample,fixture}
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from itertools import zip_longest
from pathlib import Path

import duckdb
import sqlglot
from offline_sample import FIXTURE_DIR, ROOT, SAMPLE_DIR, SAMPLE_LOCK, Facts, digest_file, read_lock
from sqlglot import exp

__all__ = ["DDL", "check_header", "load", "locked_files", "main", "raw_columns"]

DDL = ROOT / "loader" / "raw_prestations.sql"
WAREHOUSE_DIR = ROOT / "data" / "warehouse"
SOURCES = {"sample": SAMPLE_DIR, "fixture": FIXTURE_DIR}


def raw_columns(ddl: str) -> list[str]:
    """Return the RAW table's column names, in file order.

    >>> raw_columns("CREATE TABLE T (A INTEGER, B VARCHAR)")
    ['A', 'B']
    """
    return [
        column.name for column in sqlglot.parse_one(ddl, read="snowflake").find_all(exp.ColumnDef)
    ]


def locked_files(directory: Path) -> dict[Path, Facts]:
    """Return the files `sample.lock` pins in `directory`, with their pins."""
    return {
        ROOT / name: pin
        for name, pin in read_lock(SAMPLE_LOCK).items()
        if (ROOT / name).parent == directory
    }


def check_header(path: Path, columns: list[str]) -> None:
    """Fail unless the file's header names the DDL's columns in order.

    The last column is the filler for the empty field after each line's trailing `;`,
    so the header's last field is empty (ADR 0012).

    Raises:
        ValueError: the header differs, naming the first field that does.
    """
    with path.open("rb") as f:
        found = f.readline().removesuffix(b"\n").decode("ascii").split(";")
    expected = [*columns[:-1], ""]
    for i, (a, b) in enumerate(zip_longest(found, expected)):
        if a != b:
            raise ValueError(
                f"{path.name}: header differs from {DDL.name} at field {i + 1}: {a!r} != {b!r}"
            )


def load(files: Mapping[Path, Facts], db: Path, ddl: str) -> dict[str, int]:
    """Load each file not loaded yet into `RAW.DAMIR.PRESTATIONS` in the DuckDB file `db`.

    Returns the rows loaded per file name, 0 for a file loaded before.

    Raises:
        ValueError: a file differs from its pin, its header from the DDL, its row
            count from the lock, or a loaded file of the same name had other bytes.
        duckdb.Error: a value doesn't fit its column's type; the file loads nothing.
    """
    columns = raw_columns(ddl)
    db.parent.mkdir(parents=True, exist_ok=True)
    loaded: dict[str, int] = {}
    with duckdb.connect(db) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS RAW.DAMIR")
        # One table in both warehouses: the Snowflake DDL, transpiled.
        con.execute(sqlglot.transpile(ddl, read="snowflake", write="duckdb")[0])
        # Snowflake's own load metadata skips a file `COPY` already loaded; DuckDB has none.
        con.execute(
            "CREATE TABLE IF NOT EXISTS RAW.DAMIR.LOADED_FILES "
            "(FILE_NAME VARCHAR PRIMARY KEY, SHA256 VARCHAR NOT NULL, ROWS BIGINT NOT NULL)"
        )
        for path, pin in sorted(files.items()):
            if (found := digest_file(path)) != (pin.bytes, pin.sha256):
                raise ValueError(f"{path.name}: expected {pin[1:]}, found {found}")
            check_header(path, columns)
            before = con.execute(
                "SELECT SHA256 FROM RAW.DAMIR.LOADED_FILES WHERE FILE_NAME = ?", [path.name]
            )
            if (row := before.fetchone()) is not None:
                if row[0] != pin.sha256:
                    raise ValueError(f"{path.name}: a file of that name with other bytes is loaded")
                loaded[path.name] = 0
                continue
            # An exception leaves through `with`, and closing the connection rolls the
            # open transaction back: the file and its record land together or not at all.
            con.begin()
            # Types come from the table, as in Snowflake's `COPY`: a value that doesn't
            # parse aborts the statement, like `ON_ERROR = ABORT_STATEMENT`. An empty
            # field is NULL. The path is the lock's, not user input.
            copied = con.execute(
                f"COPY RAW.DAMIR.PRESTATIONS FROM '{path.as_posix()}' (DELIMITER ';', HEADER true)"
            ).fetchone()
            rows = copied[0] if copied is not None else -1
            if rows != pin.rows:
                raise ValueError(f"{path.name}: loaded {rows} rows, the lock pins {pin.rows}")
            con.execute(
                "INSERT INTO RAW.DAMIR.LOADED_FILES VALUES (?, ?, ?)", [path.name, pin.sha256, rows]
            )
            con.commit()
            loaded[path.name] = rows
    return loaded


def main(argv: list[str] | None = None) -> None:
    """Load one source's locked files into `data/warehouse/<source>/RAW.duckdb`."""
    parser = argparse.ArgumentParser(description="Load the offline sample or the CI fixture.")
    parser.add_argument("source", choices=sorted(SOURCES))
    args = parser.parse_args(argv)
    directory = SOURCES[args.source]
    # The database is named after its file, so both sources are `RAW`, in their own directory.
    db = WAREHOUSE_DIR / args.source / "RAW.duckdb"
    for name, rows in load(locked_files(directory), db, DDL.read_text()).items():
        where = (directory / name).relative_to(ROOT)
        print(f"{where}: {f'{rows} rows loaded' if rows else 'loaded before, skipped'}")


if __name__ == "__main__":
    main()
