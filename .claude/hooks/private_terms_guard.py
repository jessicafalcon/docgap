#!/usr/bin/env python3
"""Block text that must not reach the repo, a commit, or a pull request.

PreToolUse hook for Write/Edit/MultiEdit (text written inside the project) and Bash
(only commands that publish text: git commit/tag/notes, gh pr/issue create/edit/
comment/review/merge/close, gh release create/edit). The terms are
case-insensitive regexes, one per line, in `.claude/private-terms.local`. That file
is gitignored, so the list itself never lands in the repo. No file, no check.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Only subcommands that publish text. Reads (`gh pr list`, `gh pr view`) stay unguarded.
PUBLISHING_COMMAND = re.compile(
    r"\b(git\s+(commit|tag|notes)"
    r"|gh\s+(pr|issue)\s+(create|edit|comment|review|merge|close)"
    r"|gh\s+release\s+(create|edit))\b"
)


def load_terms(root: Path) -> list[re.Pattern[str]]:
    terms_file = root / ".claude" / "private-terms.local"
    if not terms_file.is_file():
        return []
    lines = terms_file.read_text(encoding="utf-8").splitlines()
    return [
        re.compile(line.strip(), re.IGNORECASE)
        for line in lines
        if line.strip() and not line.startswith("#")
    ]


def texts_to_check(tool: str, tool_input: dict, root: Path) -> list[str]:
    if tool == "Bash":
        command = tool_input.get("command", "")
        return [command] if PUBLISHING_COMMAND.search(command) else []
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return []
    try:
        Path(file_path).resolve().relative_to(root)
    except ValueError:
        return []  # outside the project: memory, scratchpad
    edits = tool_input.get("edits") or []
    return [
        tool_input.get("content", ""),
        tool_input.get("new_string", ""),
        *(edit.get("new_string", "") for edit in edits),
    ]


def main() -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    terms = load_terms(root)
    if not terms:
        return 0
    payload = json.load(sys.stdin)
    texts = texts_to_check(payload.get("tool_name", ""), payload.get("tool_input") or {}, root)
    hits = sorted({m.group(0) for text in texts for term in terms for m in term.finditer(text)})
    if not hits:
        return 0
    print(
        f"private-terms-guard: blocked, the text contains {', '.join(repr(h) for h in hits)}. "
        "These terms must not appear in repo files, commits or pull requests. Rewrite without them "
        "(describe the style or fact itself, not its source).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
