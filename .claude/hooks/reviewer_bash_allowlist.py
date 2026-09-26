#!/usr/bin/env python3
"""Keep the review agents read-only: allow only inspection and check commands.

PreToolUse hook on Bash, registered twice so that either registration is enough:

- In `.claude/settings.json`, for every session, enforced only when the payload's
  `agent_type` is a review agent. Claude Code skips agent frontmatter hooks in a folder
  that isn't trusted yet, without a message; settings hooks still run there.
- In each review agent's frontmatter with `--enforce`, which enforces whatever the
  payload says, in case a future payload renames or drops `agent_type`.

Bash can write files, so removing Edit/Write isn't enough; this allowlist is what
enforces read-only. Any command outside it, any shell chaining, redirection or
expansion, or any flag that writes a file exits 2. So does a crash.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

# Keep in sync with the `name:` in each file under .claude/agents/.
REVIEW_AGENTS = {"docgap-reviewer", "docgap-coherence-auditor"}

# `uv run` only with --frozen: without it, uv may rewrite uv.lock before running.
ALLOWED = re.compile(
    r"^("
    r"uv run --frozen (--no-sync )?(pytest|pyright)( .*)?"
    r"|uv run --frozen (--no-sync )?ruff (check|format --check)( .*)?"
    r"|python3 \.claude/hooks/determinism_guard\.py( .*)?"
    r"|uv run --frozen (--no-sync )?docgap( [a-z-]+)* --help"
    r"|git (diff|log|show|status|merge-base|rev-parse|ls-files|branch --show-current)( .*)?"
    r"|gh pr (view|diff|checks)( .*)?"
    r")$"
)
# Chaining, redirection and substitution would let an allowed prefix run anything; a
# newline separates commands as `;` does.
SHELL_META = re.compile(r"[;&|<>`\n]|\$\(")
# Expansions the shell applies before the tool sees its words (variables, backslash
# escapes, brace lists) could turn a harmless word into a writing flag. Quotes are
# fine: the words are split as the shell splits them before the flag check.
SHELL_EXPANSION = re.compile(r"[$\\]|\{[^{}]*(,|\.\.)[^{}]*\}")
# Flags that write files, or point a tool at a config that could: ruff fixes, noqa
# edits, config and cache dir; golden updates; git's `--output`; pytest's basetemp
# (which it empties), junit report, ini overrides, config file and rootdir; pyright
# stubs and baselines.
WRITING_FLAGS = frozenset(
    {
        "--fix",
        "--fix-only",
        "--add-noqa",
        "--config",
        "--cache-dir",
        "--update-golden",
        "--write",
        "-w",
        "--output",
        "--output-file",
        "--basetemp",
        "--junitxml",
        "--junit-xml",
        "--override-ini",
        "--rootdir",
        "--createstub",
        "--writebaseline",
    }
)
# Short flags that take a value, alone or attached (`-o cache_dir=x`, `-ocache_dir=x`).
WRITING_SHORT_FLAGS = ("-o", "-c")


def _writes(word: str) -> bool:
    if word.startswith("--"):
        return word.partition("=")[0] in WRITING_FLAGS
    return word in WRITING_FLAGS or word.startswith(WRITING_SHORT_FLAGS)


def is_allowed(command: str) -> bool:
    """True when a review agent may run `command`.

    >>> is_allowed("git diff main...HEAD -- src/")
    True
    >>> is_allowed("git diff main...HEAD | head")
    False
    >>> is_allowed('uv run --frozen pytest "--basetemp=src"')
    False
    """
    command = command.strip()
    if not ALLOWED.match(command) or SHELL_META.search(command) or SHELL_EXPANSION.search(command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:  # unbalanced quotes
        return False
    return not any(_writes(word) for word in words)


def main() -> int:
    payload = json.load(sys.stdin)
    enforce = "--enforce" in sys.argv[1:] or payload.get("agent_type") in REVIEW_AGENTS
    if not enforce or is_allowed((payload.get("tool_input") or {}).get("command") or ""):
        return 0
    print(
        "Review agents are read-only. Allowed: uv run --frozen pytest/pyright/ruff check/"
        "ruff format --check, the determinism guard, docgap <command> --help, git diff/log/"
        "show/status/merge-base/rev-parse/ls-files/branch --show-current with no global "
        "options (no `git -C`: run from the repo root), gh pr view/diff/checks. No chaining, "
        "redirection, variables or file-writing flags. Report the finding instead of "
        "changing anything.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        Exception
    ) as error:  # exit 1 wouldn't block; a broken hook must not let a command through
        print(f"reviewer-bash-allowlist: blocked, the hook failed: {error!r}", file=sys.stderr)
        sys.exit(2)
