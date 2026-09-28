"""The manifest reader keeps the contracted mart models and fails on anything it can't trust."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from conftest import MANIFEST, MARTS_CONFIG

from docgap.config import ManifestConfig
from docgap.manifest import load_marts

FCT = "model.docgap.fct_reimbursements"


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
