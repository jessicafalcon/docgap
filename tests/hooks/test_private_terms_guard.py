"""The private-terms guard reads every text a commit or PR publishes, and fails closed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from private_terms_guard import find_hits, flag_values, load_terms, texts_to_check

REPO = Path(__file__).parents[2]
GUARD = REPO / ".claude" / "hooks" / "private_terms_guard.py"
TERM = "zorblax"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    terms = tmp_path / ".claude" / "private-terms.local"
    terms.parent.mkdir()
    terms.write_text(f"# one regex per line\n\n{TERM}\nqu+x\n")
    return tmp_path


def _bash(command: str, root: Path, cwd: Path | None = None) -> list[str]:
    return texts_to_check("Bash", {"command": command}, root, cwd or root)


def test_terms_skip_comments_and_blanks_and_ignore_case(root: Path) -> None:
    terms = load_terms(root)
    assert [t.pattern for t in terms] == [TERM, "qu+x"]
    assert find_hits(["Zorblax and QUUUX"], terms) == ["QUUUX", "Zorblax"]


def test_no_terms_file_means_no_terms(tmp_path: Path) -> None:
    assert load_terms(tmp_path) == []


def test_writes_inside_the_project_are_checked(root: Path) -> None:
    tool_input = {"file_path": str(root / "README.md"), "content": "text"}
    assert "text" in texts_to_check("Write", tool_input, root, root)


def test_writes_outside_the_project_are_not(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("scratch") / "notes.md"
    assert texts_to_check("Write", {"file_path": str(outside), "content": TERM}, root, root) == []


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'x'",
        "git -C ../other commit -m 'x'",
        'git -C "dir with space" -c user.name=x commit -m "x"',
        "git --no-pager tag -a v1 -m 'x'",
        "git -P notes add -m 'x'",
        "git --git-dir .git tag -a v1 -m 'x'",
        "gh pr create --title x --body y",
        "gh release edit v1 --notes y",
    ],
)
def test_publishing_commands_are_checked(root: Path, command: str) -> None:
    assert _bash(command, root) == [command]


@pytest.mark.parametrize("command", ["git log --oneline", "gh pr view 3", "git -C x status"])
def test_reads_are_not(root: Path, command: str) -> None:
    assert _bash(command, root) == []


@pytest.mark.parametrize(
    ("command", "name"),
    [
        ("git commit -F msg.txt", "msg.txt"),
        ("git tag -a v1 --file=msg.txt", "msg.txt"),
        ("gh pr create --title t --body-file msg.txt", "msg.txt"),
        ('gh pr comment 3 -F "msg.txt"', "msg.txt"),
        ("gh release create v1 --notes-file msg.txt", "msg.txt"),
        ("gh pr create -t t -Fmsg.txt", "msg.txt"),
        ("gh pr create -t t -dF msg.txt", "msg.txt"),
        ("git commit -aF msg.txt", "msg.txt"),
    ],
)
def test_message_files_are_read(root: Path, command: str, name: str) -> None:
    (root / name).write_text(f"body naming {TERM}")
    assert f"body naming {TERM}" in _bash(command, root)


def test_message_file_resolves_against_the_shell_directory(root: Path) -> None:
    shell_dir = root / "sub"
    shell_dir.mkdir()
    (shell_dir / "msg.txt").write_text("from sub")
    assert "from sub" in _bash("gh pr create -F msg.txt", root, cwd=shell_dir)


def test_message_file_resolves_against_git_dash_c(root: Path) -> None:
    (root / "repo").mkdir()
    (root / "repo" / "msg.txt").write_text("from -C")
    assert "from -C" in _bash("git -C repo commit -F msg.txt", root)


def test_missing_message_file_fails_closed(root: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _bash("gh pr create --body-file absent.md", root)


@pytest.mark.parametrize(
    "command",
    ["cat body.md | gh pr create --body-file -", "gh pr create -F - < body.md"],
)
def test_stdin_from_a_pipe_or_redirect_fails_closed(root: Path, command: str) -> None:
    with pytest.raises(FileNotFoundError):
        _bash(command, root)


def test_double_quoted_variables_are_expanded(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODY_DIR", str(root))
    (root / "msg.txt").write_text("from a variable")
    assert "from a variable" in _bash('gh pr create --body-file "$BODY_DIR/msg.txt"', root)


def test_single_quoted_variables_are_not_expanded(root: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"\$HOME"):
        _bash("gh pr create --body-file '$HOME/msg.txt'", root)


def test_stdin_message_is_not_a_file(root: Path) -> None:
    assert _bash("git commit -F - <<'EOF'\nbody\nEOF", root) == [
        "git commit -F - <<'EOF'\nbody\nEOF"
    ]


@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "cover -F and --body-file files"',
        "git commit -m 'read --body-file=x'",
        # Quotes inside a heredoc need not balance; its words are not flags.
        "git commit -m \"$(cat <<'EOF'\nit's \"-F x\nEOF\n)\"",
    ],
)
def test_flags_inside_a_message_are_not_read_as_flags(root: Path, command: str) -> None:
    assert flag_values(command, ("-F", "--body-file")) == []
    assert _bash(command, root) == [command]


def _run_guard(root: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (fixed argv: this interpreter and the guard script)
        [sys.executable, str(GUARD), *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=root,
        env={"CLAUDE_PROJECT_DIR": str(root)},
        check=False,
    )


def _payload(command: str, cwd: Path) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})


def test_hook_blocks_a_term_in_a_body_file(root: Path) -> None:
    (root / "body.md").write_text(f"Built for {TERM}.")
    result = _run_guard(root, stdin=_payload("gh pr create --body-file body.md", root))
    assert result.returncode == 2
    assert repr(TERM) in result.stderr


def test_hook_blocks_an_unreadable_body_file(root: Path) -> None:
    result = _run_guard(root, stdin=_payload("gh pr create --body-file absent.md", root))
    assert result.returncode == 2
    assert "cannot read 'absent.md'" in result.stderr


def test_hook_allows_clean_text(root: Path) -> None:
    assert (
        _run_guard(root, stdin=_payload("git commit -m 'fix(rank): tie order'", root)).returncode
        == 0
    )


def test_git_hook_mode_blocks_a_staged_file_or_message(root: Path) -> None:
    clean, dirty = root / "clean.md", root / "COMMIT_EDITMSG"
    clean.write_text("nothing here")
    dirty.write_text(f"feat: add {TERM.upper()} support")
    result = _run_guard(root, str(clean), str(dirty))
    assert result.returncode == 1
    assert "COMMIT_EDITMSG contains 'ZORBLAX'" in result.stderr
    assert "clean.md" not in result.stderr


def test_a_guard_crash_blocks(root: Path) -> None:
    result = _run_guard(root, stdin="not json")
    assert result.returncode == 2
    assert "the guard failed" in result.stderr


def test_git_hooks_run_the_guard_on_staged_files_and_messages() -> None:
    config = (REPO / ".pre-commit-config.yaml").read_text()
    assert "default_install_hook_types: [pre-commit, commit-msg]" in config
    hook = config.split("id: private-terms")[1]
    assert "private_terms_guard.py" in hook
    assert "stages: [pre-commit, commit-msg]" in hook


def test_settings_run_the_guard_before_writes_and_commands() -> None:
    settings = json.loads((REPO / ".claude" / "settings.json").read_text())
    matchers = [
        entry["matcher"]
        for entry in settings["hooks"]["PreToolUse"]
        for hook in entry["hooks"]
        if hook["command"].endswith('private_terms_guard.py"')
    ]
    assert matchers == ["Write|Edit|MultiEdit|Bash"]


def test_without_a_terms_file_nothing_is_checked(tmp_path: Path) -> None:
    (tmp_path / "msg").write_text(TERM)
    assert _run_guard(tmp_path, str(tmp_path / "msg")).returncode == 0
