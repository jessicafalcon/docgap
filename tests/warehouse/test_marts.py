"""The mart YAML keeps the evaluation's rules: no early docs, no ungradable column, fixed tags."""

from __future__ import annotations

from typing import Any

import yaml
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


def test_the_marts_hold_no_column_without_a_dictionary_entry() -> None:
    # ADR 0012: no draft for such a column could be graded.
    names = {name.split(".")[1] for name in _columns()}
    assert names.isdisjoint({"ETB_DCS_MCO", "FILLER", "PRESTATION_KEY"})


def test_only_the_beneficiary_demographics_are_restricted() -> None:
    # The tags decide which columns' evidence carries no values; they are frozen at
    # the `preregistered` tag (ADR 0022).
    restricted = {
        name
        for name, column in _columns().items()
        if column["config"]["meta"]["sensitivity"] == "restricted"
    }
    assert restricted == {
        "fct_reimbursements.AGE_BEN_SNDS",
        "fct_reimbursements.BEN_RES_REG",
        "fct_reimbursements.BEN_SEX_COD",
    }
