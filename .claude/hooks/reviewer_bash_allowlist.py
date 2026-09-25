#!/usr/bin/env python3
"""Keep the review agents read-only: allow only inspection and check commands.

PreToolUse hook scoped to docgap-reviewer and docgap-coherence-auditor (see
.claude/agents/). Bash can write files, so removing Edit/Write isn't enough; this
allowlist is what enforces read-only. Any command outside it, or any shell chaining or redirection, exits 2.
"""

from __future__ import annotations

import json
import re
import sys

ALLOWED = re.compile(
    r"^("
    r"uv run (--no-sync )?(pytest|pyright)( .*)?"
    r"|uv run (--no-sync )?ruff (check|format --check)( .*)?"
    r"|python3 \.claude/hooks/determinism_guard\.py( .*)?"
    r"|uv run (--no-sync )?docgap( [a-z-]+)* --help"
    r"|git (diff|log|show|status|merge-base|rev-parse|ls-files|branch --show-current)( .*)?"
    r"|gh pr (view|diff|checks)( .*)?"
    r")$"
)
# Chaining, redirection and substitution would let an allowed prefix run anything.
SHELL_META = re.compile(r"[;&|<>`]|\$\(")
# Checks that would rewrite files even under an allowed command.
WRITING_FLAGS = re.compile(r"(^|\s)(--fix|--update-golden|--write|-w)(\s|$)")


def main() -> int:
    command = ((json.load(sys.stdin).get("tool_input") or {}).get("command") or "").strip()
    if ALLOWED.match(command) and not SHELL_META.search(command) and not WRITING_FLAGS.search(command):
        return 0
    print(
        "Review agents are read-only. Allowed: uv run pytest/pyright/ruff check, the determinism guard, "
        "docgap <command> --help, git diff/log/show/status/ls-files, gh pr view/diff/checks; "
        "no chaining, redirection or fix flags. Report the finding instead of changing anything.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
