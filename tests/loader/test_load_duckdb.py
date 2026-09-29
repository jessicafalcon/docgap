from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest
from load_duckdb import DDL, check_header, load, locked_files, raw_columns
from offline_sample import FIELDS, FIXTURE_DIR, Facts, digest_file

FIXTURE = locked_files(FIXTURE_DIR)
COLUMNS = raw_columns(DDL.read_text())


def _rows(db: Path) -> tuple[int, int]:
    """The rows in the RAW table and in the load record."""
    with duckdb.connect(db, read_only=True) as con:
        table = con.execute("SELECT count(*) FROM RAW.DAMIR.PRESTATIONS").fetchone()
        record = con.execute("SELECT count(*) FROM RAW.DAMIR.LOADED_FILES").fetchone()
    assert table is not None
    assert record is not None
    return table[0], record[0]


def _write(path: Path, lines: list[bytes]) -> dict[Path, Facts]:
    """Write `lines` to `path` and pin the file as it is."""
    path.write_bytes(b"".join(lines))
    return {path: Facts(len(lines) - 1, *digest_file(path))}


def _lines(n: int) -> list[bytes]:
    """The header and the first `n` data lines of the January fixture file."""
    with (FIXTURE_DIR / "A202501.csv").open("rb") as f:
        return [f.readline() for _ in range(n + 1)]


def test_ddl_declares_56_fields_and_the_trailing_filler() -> None:
    assert len(COLUMNS) == FIELDS
    assert COLUMNS[0] == "FLX_ANN_MOI"
    assert COLUMNS[-1] == "FILLER"


def test_fixture_loads_the_rows_its_lock_pins(tmp_path: Path) -> None:
    db = tmp_path / "RAW.duckdb"

    loaded = load(FIXTURE, db, DDL.read_text())

    assert len(FIXTURE) == 3
    assert loaded == {path.name: pin.rows for path, pin in FIXTURE.items()}
    assert _rows(db) == (sum(pin.rows for pin in FIXTURE.values()), len(FIXTURE))


def test_a_rerun_loads_nothing(tmp_path: Path) -> None:
    db = tmp_path / "RAW.duckdb"
    load(FIXTURE, db, DDL.read_text())
    before = _rows(db)

    loaded = load(FIXTURE, db, DDL.read_text())

    assert set(loaded.values()) == {0}
    assert _rows(db) == before


def test_a_file_that_differs_from_its_pin_loads_nothing(tmp_path: Path) -> None:
    source, pin = next(iter(sorted(FIXTURE.items())))
    changed = tmp_path / source.name
    shutil.copyfile(source, changed)
    with changed.open("ab") as f:
        f.write(b"\n")
    db = tmp_path / "RAW.duckdb"

    with pytest.raises(ValueError, match="expected"):
        load({changed: pin}, db, DDL.read_text())
    assert _rows(db) == (0, 0)


def test_a_renamed_header_field_fails_before_any_row_loads(tmp_path: Path) -> None:
    header, *rows = _lines(3)
    files = _write(tmp_path / "A202501.csv", [header.replace(b"AGE_BEN_SNDS", b"AGE_BEN"), *rows])
    db = tmp_path / "RAW.duckdb"

    with pytest.raises(ValueError, match="at field 3"):
        load(files, db, DDL.read_text())
    assert _rows(db) == (0, 0)


def test_a_missing_trailing_field_fails_the_header_check(tmp_path: Path) -> None:
    header, *rows = _lines(1)
    path = tmp_path / "A202501.csv"
    _write(path, [header.replace(b";\n", b"\n"), *rows])

    with pytest.raises(ValueError, match="at field 57"):
        check_header(path, COLUMNS)


def test_a_value_that_does_not_fit_its_type_loads_nothing(tmp_path: Path) -> None:
    header, first, *rest = _lines(3)
    # `FLX_ANN_MOI` is the first field, typed as a number.
    files = _write(tmp_path / "A202501.csv", [header, b"2025X1" + first[6:], *rest])
    db = tmp_path / "RAW.duckdb"

    with pytest.raises(duckdb.Error):
        load(files, db, DDL.read_text())
    assert _rows(db) == (0, 0)


def test_a_row_count_other_than_the_lock_loads_nothing(tmp_path: Path) -> None:
    [(path, pin)] = _write(tmp_path / "A202501.csv", _lines(3)).items()
    db = tmp_path / "RAW.duckdb"

    with pytest.raises(ValueError, match="the lock pins 4"):
        load({path: pin._replace(rows=4)}, db, DDL.read_text())
    assert _rows(db) == (0, 0)


def test_other_bytes_under_a_loaded_name_fail(tmp_path: Path) -> None:
    db = tmp_path / "RAW.duckdb"
    path = tmp_path / "A202501.csv"
    load(_write(path, _lines(2)), db, DDL.read_text())

    with pytest.raises(ValueError, match="other bytes is loaded"):
        load(_write(path, _lines(3)), db, DDL.read_text())
    assert _rows(db) == (2, 1)
