---
name: docgap-voice
description: >
  The written voice of the docgap repo: commit messages, PR titles and bodies, code
  comments, docstrings, decision records, and every Markdown file (README, brief,
  specs, EVAL_PROTOCOL, docs/adr). Engineering register: result first, mechanism
  and consequence stated precisely, validation stated honestly, no selling. Read
  this BEFORE writing a commit, opening a PR, adding a comment or docstring, or
  writing or editing any .md file. Pairs with docgap-pr (PR structure).
---

# docgap-voice

Prose here reads like an engineer explaining a change to a colleague who will
maintain it: what changed, the mechanism, the consequence, how it was checked.
Precise over short, but never padded. If an explanation is longer than the thing
it explains, cut the explanation.

## Commit messages

Conventional Commits, lowercase type, imperative subject, no trailing period.
`(#N)` is appended on squash-merge.

- **Types:** `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `chore`, `ci`,
  `build`, `revert`. Standard names only: release tooling and commitlint parse
  them, and a `chore` never triggers a version bump, so pick the type by what the
  change *does* for a user of the tool.
- **Scope** is the area touched, comma-joined when a change spans two:
  `fix(snapshot):`, `feat(rank,report):`, `chore(deps):`, `chore(ci):`.
- **Subject: the change and its effect.** When the *why* fits, keep it in the
  subject with "so" or "instead of": it is the line people read in `git log`.
  Wrap code identifiers in backticks. Aim for ≤ 72 characters; go longer only
  when the effect would otherwise be lost.

```text
fix(snapshot): wait for ACCESS_HISTORY latency so the cross-check compares complete windows
feat(rank): break score ties by column FQN so re-runs never reorder
fix(loader): reject non-gzip responses instead of failing the checksum on an HTML page
refactor(llm): move cache keys to canonical JSON
chore(deps): bump `sqlglot` to 27.8.0
chore: release 0.3.0
```

- **Body** (optional, wrapped at 72): the mechanism, when the subject can't carry it.
- No `wip`, `fix stuff`, `nits`, `update X.md` or bare one-word messages. Every
  message names its change.

**Claude-authored commits** end with:
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Pull requests

Title: same form as a commit subject. The body follows
`.github/pull_request_template.md`; docgap-pr says what goes in each section.
This section governs the wording inside it.

A good Summary reads like this:

```text
On a snapshot window that ends less than 45 minutes ago, QUERY_HISTORY is still
filling, so the export silently missed the last runs of a baseline. `snapshot`
now refuses a window whose end is newer than `as_of - 45 min` and says so.
```

Wording inside those sections:

- **For a fix, the Summary's root cause is mechanistic.** Name the exact function, value or ordering that
  fails and why. Bold the one consequence that matters: "**every run leaked a
  warehouse session**".
- **Numbers with arrows:** `k 5 → 10`, `sqlglot 26.1 → 27.8`, `parse rate 91% → 98%`.
- **Validation is honest.** Name the command and what it showed. Say what was
  *not* run: "The live Airflow run was not repeated for this change."
- **Reasons in one sentence.** For an option discarded or a thing deliberately
  not done: "Deliberately not persisted: a flag on disk would suppress the
  prompt forever, even after the cause is fixed."

Development PR bodies written by Claude end with:
`🤖 Generated with [Claude Code](https://claude.com/claude-code)`

## Code comments

Comments carry the *why*, the invariant, or the consequence of getting it wrong,
in full sentences:

```python
# QUERY_HISTORY lags by up to 45 minutes. Exporting a window that ends later than
# that returns a partial window with no error, and the baseline would undercount.
window_end = min(requested_end, as_of - QUERY_HISTORY_LATENCY)

# Keep in sync with the GRANTs in infra/terraform/roles.tf.
AUDITOR_VIEWS = ("QUERY_HISTORY", "ACCESS_HISTORY", "COLUMNS")

# ponytail: linear scan over columns, fine below ~5k; index by FQN if a real manifest is bigger.
```

- A short label above a block (`# Snowflake session`) is fine in a long
  module; a comment restating the next line is not.
- `# ponytail:` marks a deliberate shortcut: its ceiling and the trigger to upgrade.
- "Keep in sync with X" wherever two places must change together.
- No commented-out code, no TODO without an owner or trigger.

## Docstrings

- **Module:** one line stating its job. `"""Resolve query text to fully qualified column references."""`
- **Function/class:** one imperative line. Add sections only when the signature
  isn't enough: `Raises` for data errors a caller must handle, `Examples` as a
  doctest, `Notes` for a constraint the caller can't see ("Caller must pass
  `as_of`; the core never reads the clock.").
- A `>>>` example is executed by pytest. Keep it correct.

## Markdown: README, brief, specs, protocol

- **Open with what it is**, in one sentence. Then the result or the reason to care.
  No mission statement.
- **Structure for scanning:** plain headings, tables for options and thresholds
  (e.g. "Pick the right mode" tables), numbered lists with a **bold lead term** for
  procedures and "How it works" sections, one idea per paragraph.
- **README order:** one-line description → results block → quickstart →
  "How it works" → requirements → design choices → known gap and limits →
  contributing → license.
- **Coding guidelines as checklists:** "Any change to X must land in all of:"
  followed by the file list. Parity rules are lists, not paragraphs.
- **Factual, not promotional.** "X, because Y" beats adjectives. Cut "powerful",
  "seamless", "robust", "best-in-class".
- **Name limits and non-goals explicitly**, as the project's own design choices.
- **A spec or brief has no changelog.** It states the current design as if it
  were always the plan. What changed between drafts lives in git history.
- **No meta-framing.** The documents describe what docgap does and shows. They
  never frame it as a portfolio piece, an application, or work for a specific
  organization, and they never name a company as a stylistic or strategic
  reference. The `private-terms-guard` hook blocks the known terms.
- **Cite at the point of use:** vendor docs for a limit or a latency, the
  dataset's page for a column count, with the date checked when the fact can drift.

## Decision records

One file per decision in `docs/adr/NNNN-short-title.md`, numbered, never
rewritten after acceptance (a later record supersedes it). Sections:
`## Context and Problem Statement` → `## Considered Options` →
`## Decision Outcome` ("Chosen option: X, because Y") → `### Consequences`
("Good, because…" / "Bad, because…"). Short: most records fit on one screen.
