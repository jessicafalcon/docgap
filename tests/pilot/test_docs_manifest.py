"""Both pilot configurations' manifests come from one `dbt parse` path and differ only in column docs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docgap.config import ManifestConfig
from docgap.manifest import Marts, read_marts
from eval.column_docs import DOCS
from eval.docs_manifest import build_manifest, document_columns

CONFIG = ManifestConfig(mart_database="ANALYTICS", mart_schema="MARTS")


@pytest.fixture(scope="module")
def manifests(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    out = tmp_path_factory.mktemp("manifests")
    docs = json.loads(DOCS.read_text())
    return build_manifest(out / "no_docs.json", None), build_manifest(out / "full_docs.json", docs)


@pytest.fixture(scope="module")
def configurations(manifests: tuple[Path, Path]) -> tuple[Marts, Marts]:
    no_docs, full_docs = (read_marts(path, CONFIG)[0] for path in manifests)
    return no_docs, full_docs


def test_a_manifest_names_no_absolute_path(manifests: tuple[Path, Path]) -> None:
    # dbt's `root_path` would name the temporary copy, and a committed manifest the checkout.
    for path in manifests:
        text = path.read_text()
        assert "root_path" not in text
        assert '"/' not in text


def test_no_docs_documents_no_column(configurations: tuple[Marts, Marts]) -> None:
    no_docs, _ = configurations
    assert len(no_docs.fqns()) == 67
    assert not no_docs.documented


def test_full_docs_gives_every_column_its_dictionary_text(
    configurations: tuple[Marts, Marts],
) -> None:
    _, full_docs = configurations
    docs = json.loads(DOCS.read_text())
    assert full_docs.column_descriptions == {
        fqn: docs[fqn.rsplit(".", 1)[1]] for fqn in full_docs.fqns()
    }


def test_the_configurations_differ_only_in_column_docs(
    configurations: tuple[Marts, Marts],
) -> None:
    no_docs, full_docs = configurations
    assert (no_docs.database, no_docs.schema) == (full_docs.database, full_docs.schema)
    assert no_docs.tables == full_docs.tables
    assert no_docs.table_descriptions == full_docs.table_descriptions


def test_a_column_missing_from_the_docs_fails() -> None:
    yaml = "models:\n- name: t\n  columns:\n  - name: A\n  - name: B\n"
    with pytest.raises(ValueError, match=r"t\.B: no text"):
        document_columns(yaml, {"A": "a"})


def test_a_failed_parse_raises_and_leaves_no_output(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: broken\n")
    out = tmp_path / "out" / "manifest.json"
    with pytest.raises(RuntimeError, match="dbt parse failed"):
        build_manifest(out, None, project)
    assert not out.exists()
    assert list(out.parent.iterdir()) == []
