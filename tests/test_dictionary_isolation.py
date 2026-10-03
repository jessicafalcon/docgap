"""Neither the tool nor the test agent reaches the dictionary: nothing under `src/` or
`eval/agent/` imports or names `eval/reference/`."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"
AGENT = ROOT / "eval" / "agent"

# The dictionary is the ground truth that drafts are graded against. A draft written
# with it in reach would score well for the wrong reason, and an agent reading it would
# narrow the gap the pilot measures. Any line naming `eval` and then `reference`
# matches, so a path split across a join or concatenation is caught, and so is the
# descriptor's file name; a path assembled from variables gets past it, which review
# catches.
REFERENCE = re.compile(r"\beval\b.*\breference\b|descriptif")


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
        '"eval/" + "reference"',
        'Path("eval").joinpath("reference")',
        'Path("eval") / Path("reference")',
        'root.rglob("*descriptif*")',
        "eval.reference.labels",
        "eval\\reference\\codes.xls",
    ],
)
def test_pattern_catches_each_spelling_of_the_path(text: str) -> None:
    assert REFERENCE.search(text)


def test_import_check_catches_an_eval_import() -> None:
    tree = ast.parse("import eval.reference\nfrom eval import reference")
    assert _eval_imports(tree) == ["eval.reference", "eval"]


@pytest.mark.parametrize("root", [SRC, AGENT], ids=["src", "eval-agent"])
def test_never_names_the_dictionary_path(root: Path) -> None:
    files = sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    assert files, f"no files under {root}"
    hits = [
        f"{path.relative_to(root)}:{lineno}"
        for path in files
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if REFERENCE.search(line)
    ]
    assert not hits, f"{root.name}/ names eval/reference/: {hits}"


def test_src_never_imports_from_eval() -> None:
    hits = {
        str(path.relative_to(SRC)): imports
        for path in sorted(SRC.rglob("*.py"))
        if (imports := _eval_imports(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert not hits, f"src/ imports from eval: {hits}"


def test_agent_imports_only_the_agent_from_eval() -> None:
    hits = {
        str(path.relative_to(AGENT)): outside
        for path in sorted(AGENT.rglob("*.py"))
        if (
            outside := [
                m
                for m in _eval_imports(ast.parse(path.read_text(encoding="utf-8")))
                if not m.startswith("eval.agent")
            ]
        )
    }
    assert not hits, f"eval/agent/ imports from eval outside the agent: {hits}"
