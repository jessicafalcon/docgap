#!/usr/bin/env python3
"""Flag non-determinism in the docgap core (src/docgap/, outside llm/ and cli.py).

Two entry points, one rule set:

- Claude Code PostToolUse hook: reads the hook payload on stdin, checks the edited
  file, exits 2 with the findings so the model fixes them.
- pre-commit / CI: `python3 .claude/hooks/determinism_guard.py FILE...`, exits 1 on
  findings, so edits made outside Claude are held to the same rule.

AST-based rather than grep: comments and docstrings never match, and aliased imports
(`from datetime import datetime as dt`) are resolved. See docgap-correctness §1.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

CORE_PREFIX = "src/docgap/"
# The model edge and the CLI entrypoint are where the clock, env and model are allowed;
# they inject values into the core instead of the core reading them.
EXEMPT_PREFIXES = ("src/docgap/llm/",)
EXEMPT_FILES = ("src/docgap/cli.py",)

MODEL_PACKAGES = {"anthropic", "openai", "system_one_adapter", "typesafe_sdk"}

FORBIDDEN_CALLS = {
    "datetime.datetime.now": "wall-clock read; take `as_of` as a parameter",
    "datetime.datetime.utcnow": "wall-clock read; take `as_of` as a parameter",
    "datetime.datetime.today": "wall-clock read; take `as_of` as a parameter",
    "datetime.date.today": "wall-clock read; take `as_of` as a parameter",
    "time.time": "wall-clock read; take `as_of` as a parameter",
    "time.time_ns": "wall-clock read; take `as_of` as a parameter",
    "uuid.uuid1": "non-deterministic id; derive it from the inputs (content hash)",
    "uuid.uuid4": "non-deterministic id; derive it from the inputs (content hash)",
    "os.getenv": "environment read; pass configuration in explicitly",
    "os.listdir": "filesystem order is not stable; wrap in sorted()",
    "os.scandir": "filesystem order is not stable; wrap in sorted()",
    "glob.glob": "filesystem order is not stable; wrap in sorted()",
    "hash": "builtin hash() of str is salted per process; use hashlib",
}
UNSORTED_METHODS = {"iterdir", "glob", "rglob"}
SEEDED_METHODS = {"sample", "shuffle"}


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names to fully qualified ones (`dt` -> `datetime.datetime`)."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def check_source(source: str, rel_path: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # ruff reports syntax errors; nothing to judge yet
    names = _aliases(tree)
    seeded = _seeded_rng_names(tree, names)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    findings: list[str] = []

    def resolve(dotted: str) -> str:
        head, _, rest = dotted.partition(".")
        full = names.get(head, head)
        return f"{full}.{rest}" if rest else full

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for module in modules:
                if module.split(".")[0] in MODEL_PACKAGES:
                    findings.append(
                        f"line {node.lineno}: model client import ({module}); model calls live in llm/ only"
                    )
        elif (
            isinstance(node, (ast.Attribute, ast.Name))
            and resolve(_dotted(node) or "") == "os.environ"
        ):
            findings.append(
                f"line {node.lineno}: environment read; pass configuration in explicitly"
            )
        elif isinstance(node, ast.Call):
            dotted = _dotted(node.func)
            if dotted is None:
                continue
            full = resolve(dotted)
            if full in FORBIDDEN_CALLS and not (
                full in {"os.listdir", "os.scandir", "glob.glob"} and _in_sorted(node, parents)
            ):
                findings.append(f"line {node.lineno}: {full}(): {FORBIDDEN_CALLS[full]}")
            elif full.startswith(("random.", "numpy.random.")) and not _is_seeded_rng(full, node):
                findings.append(
                    f"line {node.lineno}: {full}(): unseeded randomness; use random.Random(seed) from config"
                )
            elif isinstance(node.func, ast.Attribute):
                method = node.func.attr
                if method in UNSORTED_METHODS and not _in_sorted(node, parents):
                    findings.append(
                        f"line {node.lineno}: .{method}(): filesystem order is not stable; wrap in sorted()"
                    )
                elif (
                    method in SEEDED_METHODS
                    and not any(k.arg == "seed" for k in node.keywords)
                    and _dotted(node.func.value) not in seeded
                ):
                    findings.append(
                        f"line {node.lineno}: .{method}() without seed=; pass the seed from config"
                    )
    return sorted(set(findings), key=lambda f: int(f.split()[1].rstrip(":")))


def _seeded_rng_names(tree: ast.Module, names: dict[str, str]) -> set[str | None]:
    """Names bound to a seeded RNG (`rng = random.Random(seed)`); their methods are fine."""
    bound: set[str | None] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            dotted = _dotted(node.value.func) or ""
            head, _, rest = dotted.partition(".")
            full = f"{names.get(head, head)}.{rest}" if rest else names.get(head, head)
            if _is_seeded_rng(full, node.value):
                bound.update(_dotted(target) for target in node.targets)
    return bound


def _in_sorted(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    while isinstance(parent, (ast.Call, ast.GeneratorExp, ast.ListComp, ast.comprehension)):
        if isinstance(parent, ast.Call) and _dotted(parent.func) == "sorted":
            return True
        parent = parents.get(parent)
    return False


def _is_seeded_rng(full: str, node: ast.Call) -> bool:
    if full in {"random.Random", "numpy.random.default_rng", "numpy.random.Generator"}:
        return bool(node.args or node.keywords)
    return False


def in_scope(rel_path: str) -> bool:
    return (
        rel_path.startswith(CORE_PREFIX)
        and rel_path.endswith(".py")
        and not Path(rel_path).name.startswith("test_")
        and not rel_path.startswith(EXEMPT_PREFIXES)
        and rel_path not in EXEMPT_FILES
    )


def main() -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    hook_mode = len(sys.argv) == 1
    if hook_mode:
        payload = json.load(sys.stdin)
        paths = [(payload.get("tool_input") or {}).get("file_path", "")]
    else:
        paths = sys.argv[1:]

    report: list[str] = []
    for raw in filter(None, paths):
        path = Path(raw).resolve()
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if not in_scope(rel) or not path.is_file():
            continue
        for finding in check_source(path.read_text(encoding="utf-8"), rel):
            report.append(f"{rel} {finding}")

    if not report:
        return 0
    print(
        "determinism-guard: the core must be reproducible (docgap-correctness §1).", file=sys.stderr
    )
    print("\n".join(f"  - {line}" for line in report), file=sys.stderr)
    print("Inject the value as a parameter, or move the code to llm/ or cli.py.", file=sys.stderr)
    return 2 if hook_mode else 1


if __name__ == "__main__":
    sys.exit(main())
