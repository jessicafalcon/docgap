from __future__ import annotations

from pathlib import Path

import pytest
import sqlglot
from load_duckdb import DDL, locked_files
from offline_sample import FIXTURE_DIR
from profile_sources import profile
from sqlglot import exp

FIXTURE = locked_files(FIXTURE_DIR)


def _declared() -> dict[str, tuple[exp.DataType, bool]]:
    """Each DDL column's type and whether it is NOT NULL."""
    create = sqlglot.parse_one(DDL.read_text(), read="snowflake")
    return {
        column.name: (
            column.args["kind"],
            any(isinstance(c.kind, exp.NotNullColumnConstraint) for c in column.constraints),
        )
        for column in create.find_all(exp.ColumnDef)
    }


@pytest.mark.parametrize("path", sorted(FIXTURE), ids=lambda p: p.name)
def test_every_fixture_field_fits_its_declared_type(path: Path) -> None:
    declared = _declared()
    fields = profile(path).fields

    assert list(fields) == list(declared)[:-1]
    for name, field in fields.items():
        kind, not_null = declared[name]
        if not_null:
            assert field.empty == 0, name
        if kind.is_type(exp.DataType.Type.INT):
            assert (field.non_numeric, field.scale) == (0, 0), name
        elif kind.is_type(exp.DataType.Type.DECIMAL):
            precision, scale = (int(e.name) for e in kind.expressions)
            assert field.non_numeric == 0, name
            assert field.scale <= scale, name
            assert field.integer_digits <= precision - scale, name


@pytest.mark.parametrize("path", sorted(FIXTURE), ids=lambda p: p.name)
def test_no_fixture_file_repeats_a_grain_key(path: Path) -> None:
    result = profile(path)

    assert result.rows == result.grain_keys == FIXTURE[path].rows
