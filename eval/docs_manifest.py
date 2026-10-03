"""Build a pilot configuration's dbt manifest: the marts YAML as committed, or with every column documented."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path

import yaml
from dbt.cli.main import dbtRunner

from eval.column_docs import DOCS, MARTS_YAML

__all__ = ["DBT_PROJECT", "build_manifest", "document_columns"]

ROOT = Path(__file__).resolve().parents[1]
DBT_PROJECT = ROOT / "warehouse" / "dbt"
_MARTS_YAML = MARTS_YAML.relative_to(DBT_PROJECT)


def document_columns(marts_yaml: str, docs: Mapping[str, str]) -> str:
    """Return the marts YAML with each column's description set to its text in `docs`.

    The output feeds `dbt parse` only, so the YAML's comments and layout aren't kept.

    >>> print(document_columns(
    ...     "models:\\n- name: t\\n  columns:\\n  - name: A\\n    data_type: integer\\n",
    ...     {"A": "Année de soins"},
    ... ), end="")
    models:
    - name: t
      columns:
      - name: A
        data_type: integer
        description: Année de soins

    Raises:
        ValueError: a column has no text in `docs`.
    """
    parsed = yaml.safe_load(marts_yaml)
    for model in parsed["models"]:
        for column in model.get("columns", []):
            if column["name"] not in docs:
                raise ValueError(f"{model['name']}.{column['name']}: no text in the docs")
            column["description"] = docs[column["name"]]
    return yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False)


def build_manifest(out: Path, docs: Mapping[str, str] | None, project: Path = DBT_PROJECT) -> Path:
    """Parse a copy of the dbt project, its marts columns documented from `docs` if given.

    One path builds both configurations' manifests, the descriptions their only
    difference, so `describe()` reads each the same way (ADR 0025). `dbt parse`
    opens no database. The copy is built beside `out` and its manifest renamed over
    it, so a crash never leaves half a file.

    Raises:
        RuntimeError: `dbt parse` failed.
    """
    staging = out.parent / f".tmp-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        copy = staging / "dbt"
        shutil.copytree(project, copy, ignore=shutil.ignore_patterns("target", "logs"))
        if docs is not None:
            path = copy / _MARTS_YAML
            path.write_text(document_columns(path.read_text(), docs))
        # dbt leaves `invoke`'s keyword arguments untyped; none are passed.
        result = dbtRunner().invoke(  # pyright: ignore[reportUnknownMemberType]
            [
                "parse",
                "--project-dir", str(copy),
                "--profiles-dir", str(copy),
                "--target-path", str(staging / "target"),
                "--log-path", str(staging / "logs"),
                "--quiet",
                # The project's own flag says the same; a copy of another project may not.
                "--no-send-anonymous-usage-stats",
            ]
        )  # fmt: skip
        if not result.success:
            raise RuntimeError(f"dbt parse failed: {result.exception}")
        (staging / "target" / "manifest.json").replace(out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return out


def main(argv: list[str] | None = None) -> None:
    """Build the no-docs or the full-docs manifest under data/pilot/."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("configuration", choices=["no_docs", "full_docs"])
    args = parser.parse_args(argv)
    docs = None
    if args.configuration == "full_docs":
        docs = json.loads(DOCS.read_text())
    out = ROOT / "data" / "pilot" / args.configuration / "manifest.json"
    print(f"wrote {build_manifest(out, docs)}")


if __name__ == "__main__":
    main()
