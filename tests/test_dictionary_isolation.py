"""The tool never reaches the dictionary: nothing under `src/` imports or names `eval/reference/`."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).parents[1] / "src"

# The dictionary is the ground truth that drafts are graded against. A draft written
# with it in reach would score well for the wrong reason. The pattern also catches the
# path split across a join, such as `Path("eval") / "reference"` or `joinpath("eval",
# "reference")`; a path assembled from variables gets past it, which review catches.
REFERENCE = re.compile(r"""\beval['")]*\s*[/\\.,]\s*['"]*reference\b""")


def _eval_imports(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return [m for m in modules if m == "eval" or m.startswith("eval.")]


@pytest.mark.parametrize(
    "text",
    [
        'open("eval/reference/dictionary.csv")',
        'Path("eval") / "reference"',
        'root.joinpath("eval", "reference")',
        "eval.reference.labels",
        "eval\\reference\\codes.xls",
    ],
)
def test_pattern_catches_each_spelling_of_the_path(text: str) -> None:
    assert REFERENCE.search(text)


def test_import_check_catches_an_eval_import() -> None:
    tree = ast.parse("import eval.reference\nfrom eval import reference")
    assert _eval_imports(tree) == ["eval.reference", "eval"]


def test_src_never_names_the_dictionary_path() -> None:
    files = sorted(p for p in SRC.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    assert files, f"no files under {SRC}"
    hits = [
        f"{path.relative_to(SRC)}:{lineno}"
        for path in files
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if REFERENCE.search(line)
    ]
    assert not hits, f"src/ names eval/reference/: {hits}"


def test_src_never_imports_from_eval() -> None:
    hits = {
        str(path.relative_to(SRC)): imports
        for path in sorted(SRC.rglob("*.py"))
        if (imports := _eval_imports(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert not hits, f"src/ imports from eval: {hits}"
