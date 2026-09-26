"""The review agents' shell runs checks and reads only; every other session is untouched."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from reviewer_bash_allowlist import REVIEW_AGENTS, is_allowed

HOOK = Path(__file__).parents[2] / ".claude" / "hooks" / "reviewer_bash_allowlist.py"
AGENTS_DIR = Path(__file__).parents[2] / ".claude" / "agents"


@pytest.mark.parametrize(
    "command",
    [
        "uv run --frozen pytest -q tests/hooks",
        "uv run --frozen --no-sync pyright --project pyproject.toml",
        "uv run --frozen ruff check .claude/hooks",
        "uv run --frozen ruff format --check .",
        "python3 .claude/hooks/determinism_guard.py src/docgap/rank.py",
        "uv run --frozen docgap analyze --help",
        "git diff main...HEAD -- src/",
        "git diff -U0 --stat main...HEAD",
        "git log --oneline main..HEAD",
        "git show HEAD:CLAUDE.md",
        "git branch --show-current",
        "gh pr view 4",
    ],
)
def test_checks_and_reads_are_allowed(command: str) -> None:
    assert is_allowed(command)


@pytest.mark.parametrize(
    "command",
    [
        # uv may rewrite uv.lock without --frozen
        "uv run pytest",
        "uv run --no-sync pytest",
        "uv run ruff check .",
        # flags that write files
        "uv run --frozen ruff check --fix .",
        "uv run --frozen ruff check --fix-only .",
        "uv run --frozen ruff check --add-noqa .",
        "uv run --frozen ruff check --output-file report.txt",
        "uv run --frozen ruff check -o report.txt",
        "uv run --frozen pytest --update-golden",
        "uv run --frozen pytest --basetemp=src",
        "uv run --frozen pytest --junitxml=out.xml",
        "uv run --frozen pyright --createstub polars",
        "uv run --frozen pyright --writebaseline",
        "git diff --output=CLAUDE.md",
        "git diff --output CLAUDE.md main",
        "git log -p --output=x.patch",
        # chaining, redirection, substitution, a second line
        "git status; rm -rf src",
        "git diff main | head",
        "git log && touch x",
        "git log > log.txt",
        "git diff $(echo main)",
        "git diff `echo main`",
        "git log\nrm -rf src",
        # anything else
        "cat CLAUDE.md",
        "ls .claude",
        "git checkout main",
        "gh pr merge 4",
    ],
)
def test_writes_and_other_commands_are_blocked(command: str) -> None:
    assert not is_allowed(command)


def test_review_agents_name_real_agent_files() -> None:
    assert {p.stem for p in AGENTS_DIR.glob("*.md")} >= REVIEW_AGENTS


def _run_hook(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (fixed argv: this interpreter and the hook script)
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("agent", sorted(REVIEW_AGENTS))
def test_hook_blocks_a_chained_command_in_a_review_agent(agent: str) -> None:
    result = _run_hook({"agent_type": agent, "tool_input": {"command": "git status; echo hi"}})
    assert result.returncode == 2
    assert "Review agents are read-only" in result.stderr


def test_hook_allows_a_check_in_a_review_agent() -> None:
    payload = {"agent_type": "docgap-reviewer", "tool_input": {"command": "git log -3"}}
    assert _run_hook(payload).returncode == 0


@pytest.mark.parametrize("agent_type", [None, "general-purpose", "Explore"])
def test_hook_leaves_other_sessions_alone(agent_type: str | None) -> None:
    payload: dict[str, object] = {"tool_input": {"command": "git status; rm -rf build"}}
    if agent_type:
        payload["agent_type"] = agent_type
    assert _run_hook(payload).returncode == 0
