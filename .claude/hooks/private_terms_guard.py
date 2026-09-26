#!/usr/bin/env python3
"""Block text that must not reach the repo, a commit, or a pull request.

Two entry points, one term list:

- Claude Code PreToolUse hook (payload on stdin): text written inside the project by
  Write/Edit/MultiEdit, and Bash commands that publish text (git commit/tag/notes, also
  behind `git -C dir`; gh pr/issue create/edit/comment/review/merge/close; gh release
  create/edit), including the files they name with `-F`, `--file`, `--body-file` or
  `--notes-file`. Exits 2 on a hit.
- git hook through pre-commit (`FILE...`): the staged files at the pre-commit stage and
  the message file at the commit-msg stage. That covers what the Bash check can't see:
  files staged in the same command (`git add . && git commit`), `commit -a`, messages
  from an editor, and commits made outside Claude. Exits 1 on a hit.

The terms are case-insensitive regexes, one per line, in `.claude/private-terms.local`.
That file is gitignored, so the list itself never lands in the repo. No file, no check.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# A shell word: quoted, or up to the next space.
_WORD = r"""(?:"[^"]*"|'[^']*'|\S+)"""
# Only subcommands that publish text. Reads (`gh pr list`, `gh pr view`) stay unguarded.
# git takes global options before the subcommand (`git -C dir -c k=v commit`).
PUBLISHING_COMMAND = re.compile(
    rf"\b(git(\s+(-[Cc]\s+{_WORD}|--[\w-]+(={_WORD})?))*\s+(commit|tag|notes)"
    r"|gh\s+(pr|issue)\s+(create|edit|comment|review|merge|close)"
    r"|gh\s+release\s+(create|edit))\b"
)
# A heredoc body is text, not words: its quotes need not balance. It is scanned as part
# of the command; the flag search skips it.
HEREDOC = re.compile(r"""(<<-?\s*(['"]?)(\w+)\2)([^\n]*)\n.*?^\t*\3$""", re.DOTALL | re.MULTILINE)
# A shell word: unquoted characters and quoted strings, so a message passed in quotes is
# one word and a flag named inside it is never read as a real flag.
SHELL_WORD = re.compile(r"""(?:"(?:[^"\\]|\\.)*"|'[^']*'|[^\s"'])+""")
# The flags that take the published text from a file, across git and gh.
FILE_FLAGS = ("-F", "--file", "--body-file", "--notes-file")


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


def _unquote(word: str) -> str:
    return os.path.expandvars(word.strip("\"'"))


def flag_values(command: str, flags: tuple[str, ...]) -> list[str]:
    """Values given to `flags` in a shell command, as `flag VALUE` or `flag=VALUE`.

    >>> flag_values('gh pr create --title "use -F x" --body-file=b.md', ("-F", "--body-file"))
    ['b.md']
    """
    words = SHELL_WORD.findall(HEREDOC.sub(r"\1\4", command))
    values: list[str] = []
    for i, word in enumerate(words):
        name, equals, value = word.partition("=")
        if name in flags and (equals or i + 1 < len(words)):
            values.append(_unquote(value if equals else words[i + 1]))
    return values


def read_message_file(name: str, bases: list[Path]) -> str:
    """Read a file named by `-F`/`--body-file`, relative to the shell's directory or `-C`.

    Raises FileNotFoundError when no candidate exists: the guard fails closed rather
    than let an unread file through.
    """
    path_name = Path(name).expanduser()
    for base in bases:
        path = base / path_name  # an absolute name ignores the base
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    raise FileNotFoundError(2, "cannot read the file to check it", name)


def texts_to_check(tool: str, tool_input: dict, root: Path, cwd: Path) -> list[str]:
    if tool == "Bash":
        command = tool_input.get("command", "")
        if not PUBLISHING_COMMAND.search(command):
            return []
        bases = [cwd, *(cwd / d for d in flag_values(command, ("-C",)))]
        files = [f for f in flag_values(command, FILE_FLAGS) if f != "-"]  # - is stdin
        return [command, *(read_message_file(name, bases) for name in files)]
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


def find_hits(texts: list[str], terms: list[re.Pattern[str]]) -> list[str]:
    return sorted({m.group(0) for text in texts for term in terms for m in term.finditer(text)})


def _blocked(what: str) -> None:
    print(
        f"private-terms-guard: blocked, {what}. These terms must not appear in repo files, "
        "commits or pull requests. Rewrite without them (describe the style or fact itself, "
        "not its source).",
        file=sys.stderr,
    )


def main() -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    terms = load_terms(root)
    if not terms:
        return 0

    if len(sys.argv) > 1:  # git hook: staged files, or the commit message file
        report = []
        for name in sys.argv[1:]:
            path = Path(name)
            if not path.is_file():
                continue
            hits = find_hits([path.read_text(encoding="utf-8", errors="replace")], terms)
            if hits:
                report.append(f"{name} contains {', '.join(repr(h) for h in hits)}")
        if report:
            _blocked("; ".join(report))
        return 1 if report else 0

    payload = json.load(sys.stdin)
    cwd = Path(payload.get("cwd") or root)
    try:
        texts = texts_to_check(
            payload.get("tool_name", ""), payload.get("tool_input") or {}, root, cwd
        )
    except OSError as error:
        print(
            f"private-terms-guard: blocked, cannot read {error.filename!r} to check it. "
            "Pass an existing path, or put the text inline.",
            file=sys.stderr,
        )
        return 2
    hits = find_hits(texts, terms)
    if not hits:
        return 0
    _blocked(f"the text contains {', '.join(repr(h) for h in hits)}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
