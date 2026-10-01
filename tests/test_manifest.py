"""The manifest reader keeps the contracted mart models and fails on anything it can't trust."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from conftest import MANIFEST, MARTS_CONFIG

from docgap.config import ManifestConfig
from docgap.manifest import lint_manifest, load_marts

FCT = "model.docgap.fct_reimbursements"
STG = "model.docgap.stg_damir__prestations"


def _load(edit: Callable[[dict[str, Any]], object] | None = None) -> Any:
    manifest = json.loads(MANIFEST.read_bytes())
    if edit is not None:
        edit(manifest)
    return load_marts(json.dumps(manifest).encode(), MARTS_CONFIG)


def test_reads_the_mart_models_uppercased() -> None:
    marts = _load()
    assert (marts.database, marts.schema) == ("ANALYTICS", "MARTS")
    # The staging model, the seed and the test live in other schemas.
    assert sorted(marts.tables) == ["DIM_PRESTATION", "DIM_REGION", "FCT_REIMBURSEMENTS"]
    assert marts.tables["DIM_PRESTATION"] == {"PRS_NAT": "INTEGER", "PRS_NAT_LIB": "VARCHAR"}


def test_a_column_with_a_description_is_documented() -> None:
    marts = _load(_set(("nodes", FCT, "columns", "PRS_NAT", "description"), " \n"))
    # Whitespace alone is no description.
    assert marts.documented == {
        "ANALYTICS.MARTS.FCT_REIMBURSEMENTS.FLX_ANN_MOI",
        "ANALYTICS.MARTS.FCT_REIMBURSEMENTS.PRS_PAI_MNT",
    }
    assert len(marts.fqns()) == 12
    assert marts.documented <= set(marts.fqns())


def test_descriptions_are_read_for_the_agent() -> None:
    marts = _load(_set(("nodes", FCT, "description"), "  "))
    assert marts.column_descriptions == {
        "ANALYTICS.MARTS.FCT_REIMBURSEMENTS.FLX_ANN_MOI": "Processing month, YYYYMM.",
        "ANALYTICS.MARTS.FCT_REIMBURSEMENTS.PRS_PAI_MNT": "Amount paid, in euros.",
    }
    assert set(marts.column_descriptions) == marts.documented
    # A blank model description is no description, as for a column.
    assert marts.table_descriptions == {
        "DIM_PRESTATION": "dim_prestation model",
        "DIM_REGION": "dim_region model",
    }


def test_lowercase_column_names_are_uppercased() -> None:
    marts = _load(_set(("nodes", FCT, "columns", "PRS_NAT", "name"), "prs_nat"))
    assert "PRS_NAT" in marts.tables["FCT_REIMBURSEMENTS"]


def _set(path: tuple[str, ...], value: object) -> Callable[[dict[str, Any]], None]:
    def edit(manifest: dict[str, Any]) -> None:
        table = manifest
        for key in path[:-1]:
            table = table[key]
        table[path[-1]] = value

    return edit


def _seed_in_marts(manifest: dict[str, Any]) -> None:
    manifest["nodes"]["seed.docgap.prs_nat_codes"]["schema"] = "marts"


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (
            _set(
                ("metadata", "dbt_schema_version"),
                "https://schemas.getdbt.com/dbt/manifest/v11.json",
            ),
            "schema version",
        ),
        (_set(("metadata", "adapter_type"), "postgres"), "adapter 'postgres'"),
        (
            _set(("nodes", FCT, "config", "contract", "enforced"), False),
            f"{FCT}: .*enforced contract",
        ),
        (_seed_in_marts, "seed.docgap.prs_nat_codes: .*enforced contract"),
        (
            _set(("nodes", FCT, "columns", "PRS_NAT", "data_type"), None),
            f"{FCT}.PRS_NAT: no data_type",
        ),
        (_set(("nodes", FCT, "columns"), {}), f"{FCT}: no columns"),
        (_set(("nodes", FCT, "alias"), "dim_region"), "a second relation named DIM_REGION"),
        (
            _set(("nodes", FCT, "columns", "PRS_NAT", "name"), "prs nat"),
            "not an unquoted identifier",
        ),
        (_set(("metadata",), {}), "dbt manifest: "),
    ],
)
def test_untrusted_manifest_fails(edit: Callable[[dict[str, Any]], None], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _load(edit)


def test_quoted_uppercase_column_is_the_unquoted_column() -> None:
    marts = _load(_set(("nodes", FCT, "columns", "PRS_NAT", "quote"), True))
    assert "PRS_NAT" in marts.tables["FCT_REIMBURSEMENTS"]


def test_quoted_mixed_case_column_fails() -> None:
    def quoted(manifest: dict[str, Any]) -> None:
        column = manifest["nodes"][FCT]["columns"]["PRS_NAT"]
        column["name"], column["quote"] = "Prs_Nat", True

    with pytest.raises(ValueError, match="quoted mixed-case"):
        _load(quoted)


def test_a_mart_schema_with_no_relation_fails() -> None:
    config = ManifestConfig(mart_database="ANALYTICS", mart_schema="REPORTING")
    with pytest.raises(ValueError, match=r"no relation in ANALYTICS\.REPORTING"):
        load_marts(MANIFEST.read_bytes(), config)


def test_a_column_declared_twice_fails() -> None:
    def twice(manifest: dict[str, Any]) -> None:
        columns = manifest["nodes"][FCT]["columns"]
        columns["prs_nat"] = columns["PRS_NAT"] | {"name": "prs_nat"}

    with pytest.raises(ValueError, match="declared twice"):
        _load(twice)


def _lint(edit: Callable[[dict[str, Any]], object]) -> list[str]:
    manifest = json.loads(MANIFEST.read_bytes())
    edit(manifest)
    return lint_manifest(json.dumps(manifest).encode(), MARTS_CONFIG)


def test_lint_passes_a_tagged_manifest() -> None:
    assert lint_manifest(MANIFEST.read_bytes(), MARTS_CONFIG) == []


@pytest.mark.parametrize("value", [None, "", "secret", "RESTRICTED", ["restricted"]])
def test_lint_names_a_mart_column_without_a_known_sensitivity(value: object) -> None:
    meta = ("nodes", FCT, "columns", "PRS_NAT", "meta")
    edit = _set(meta, {} if value is None else {"sensitivity": value})
    assert _lint(edit) == [
        f"{FCT}.PRS_NAT: meta.sensitivity is {value!r}, "
        "expected one of ['internal', 'public', 'restricted']"
    ]


@pytest.mark.parametrize("model", [FCT, STG])
@pytest.mark.parametrize("value", [None, " ", 7])
def test_lint_names_a_model_without_an_owner(model: str, value: object) -> None:
    edit = _set(("nodes", model, "config", "meta"), {} if value is None else {"owner": value})
    assert _lint(edit) == [f"{model}: meta.owner is {value!r}, expected a non-blank name"]


def test_lint_asks_no_sensitivity_outside_the_marts() -> None:
    assert _lint(_set(("nodes", STG, "columns", "PRS_NAT", "meta"), {})) == []


def test_lint_fails_on_marts_analyze_could_not_read() -> None:
    with pytest.raises(ValueError, match="enforced contract"):
        _lint(_set(("nodes", FCT, "config", "contract", "enforced"), False))
