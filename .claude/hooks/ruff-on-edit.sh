#!/usr/bin/env bash
# PostToolUse: format + lint-fix an edited Python file so every file lands in house
# style. Non-blocking; exits quietly until ruff is installed. Config lives in
# pyproject.toml. See docgap-craft.
set -euo pipefail

file_path="$(python3 -c 'import json,sys; print((json.load(sys.stdin).get("tool_input") or {}).get("file_path",""))' 2>/dev/null || true)"
case "$file_path" in
  *.py) ;;
  *) exit 0 ;;
esac
[ -f "$file_path" ] || exit 0

# The venv binary first: `uv run` would re-sync the environment on every edit, which
# is slow and can touch the network. --no-sync keeps the fallback read-only.
venv_ruff="${CLAUDE_PROJECT_DIR:-.}/.venv/bin/ruff"
if [ -x "$venv_ruff" ]; then
  ruff() { "$venv_ruff" "$@"; }
elif command -v uv >/dev/null 2>&1 && uv run --no-sync ruff --version >/dev/null 2>&1; then
  ruff() { uv run --no-sync ruff "$@"; }
elif command -v ruff >/dev/null 2>&1; then
  ruff() { command ruff "$@"; }
else
  exit 0
fi

ruff format "$file_path" >/dev/null 2>&1 || true
# Keep unused imports (F401) mid-edit: stripping them breaks add-import-then-use
# sequences. pre-commit still removes them before anything is committed.
ruff check --fix --unfixable F401 "$file_path" >/dev/null 2>&1 || true

remaining="$(ruff check --output-format concise "$file_path" 2>/dev/null || true)"
if [ -n "$remaining" ] && ! printf '%s' "$remaining" | grep -q 'All checks passed'; then
  echo "ruff: unresolved lint in ${file_path##*/}:" >&2
  printf '%s\n' "$remaining" >&2
fi
exit 0
