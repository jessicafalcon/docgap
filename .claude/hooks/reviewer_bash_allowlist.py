#!/usr/bin/env python3
"""Keep the review agents read-only: allow only inspection and check commands.

PreToolUse hook on Bash, registered in `.claude/settings.json` for every session and
enforced only when the payload's `agent_type` is one of the review agents. Registered
there rather than in the agents' frontmatter because Claude Code skips frontmatter hooks
in a folder that isn't trusted yet, without a message, and settings hooks still run.
Bash can write files, so removing Edit/Write isn't enough; this allowlist is what
enforces read-only. Any command outside it, any shell chaining or redirection, or any
flag that writes a file exits 2.
"""

from __future__ import annotations

import json
import re
import sys

# Keep in sync with the agent names in .claude/agents/.
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
# Flags that write files even under an allowed command: ruff fixes and noqa edits,
# golden updates, git's `--output=<file>`, ruff's `--output-file`, pytest's basetemp
# (which it empties) and junit report, pyright stubs and baselines.
WRITING_FLAGS = re.compile(
    r"(^|\s)(--fix|--fix-only|--add-noqa|--update-golden|--write|-w"
    r"|--output|--output-file|-o|--basetemp|--junit-?xml|--createstub|--writebaseline)"
    r"(=|\s|$)"
)


def is_allowed(command: str) -> bool:
    """True when a review agent may run `command`.

    >>> is_allowed("git diff main...HEAD -- src/")
    True
    >>> is_allowed("git diff main...HEAD | head")
    False
    """
    command = command.strip()
    return bool(
        ALLOWED.match(command)
        and not SHELL_META.search(command)
        and not WRITING_FLAGS.search(command)
    )


def main() -> int:
    payload = json.load(sys.stdin)
    if payload.get("agent_type") not in REVIEW_AGENTS:
        return 0
    if is_allowed((payload.get("tool_input") or {}).get("command") or ""):
        return 0
    print(
        "Review agents are read-only. Allowed: uv run --frozen pytest/pyright/ruff check, the "
        "determinism guard, docgap <command> --help, git diff/log/show/status/ls-files, "
        "gh pr view/diff/checks; no chaining, redirection or file-writing flags. Report the "
        "finding instead of changing anything.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
