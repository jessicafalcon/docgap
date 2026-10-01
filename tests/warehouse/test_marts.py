"""The mart YAML keeps the evaluation's rules: no early docs, no ungradable column, fixed tags."""

from __future__ import annotations

from typing import Any

import yaml
from code_list_seeds import VARIABLE_SHEET, VARIABLES, sheet_rows
from offline_sample import ROOT

MARTS = ROOT / "warehouse" / "dbt" / "models" / "marts"


def _columns() -> dict[str, dict[str, Any]]:
    columns: dict[str, dict[str, Any]] = {}
    for path in sorted(MARTS.glob("*.yml")):
        for model in yaml.safe_load(path.read_text())["models"]:
            for column in model["columns"]:
                columns[f"{model['name']}.{column['name']}"] = column
    return columns


def test_no_mart_column_is_documented_before_the_baseline_lock() -> None:
    # ADR 0021: the baseline's documented half is drawn after the offline pilot. The
    # lock's pull request adds the first mart column descriptions and replaces this test.
    assert not (ROOT / "warehouse" / "baseline_docs.lock").exists()
    assert [name for name, column in _columns().items() if column.get("description")] == []


def test_every_mart_column_has_a_dictionary_entry() -> None:
    # ADR 0012: no draft for any other column could be graded. A dimension's label
    # column is graded by the line that opens its code list, which the seeds leave out.
    variables = {row[0] for row in sheet_rows(VARIABLE_SHEET) if row and isinstance(row[0], str)}
    gradable = variables | {f"{name}_LIB" for name in VARIABLES}
    assert {name.split(".")[1] for name in _columns()} <= gradable


def test_only_the_beneficiary_demographics_are_restricted() -> None:
    # The tags decide which columns' evidence carries no values; they are frozen at
    # the `preregistered` tag (ADR 0022).
    restricted = {
        name
        for name, column in _columns().items()
        if column["config"]["meta"]["sensitivity"] == "restricted"
    }
    assert restricted == {
        "dim_age_bracket.AGE_BEN_SNDS",
        "dim_age_bracket.AGE_BEN_SNDS_LIB",
        "dim_region.BEN_RES_REG",
        "dim_region.BEN_RES_REG_LIB",
        "fct_reimbursements.AGE_BEN_SNDS",
        "fct_reimbursements.BEN_RES_REG",
        "fct_reimbursements.BEN_SEX_COD",
    }
