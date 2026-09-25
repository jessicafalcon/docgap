---
name: docgap-pr
description: >
  How pull requests are shaped in docgap: one behaviour change per PR, the title
  as the squash-merge commit, and body sections by change type. Read this BEFORE
  writing a PR title or body (step 5 of the pre-PR gate in CLAUDE.md, which owns
  the gate itself). docgap-voice governs the wording.
---

# docgap-pr

## One change per PR

A PR carries **one behaviour change**: a fix, a feature, a refactor, or a
dependency bump. Size follows the change, from a one-line fix to a 40-file
feature, but never two unrelated changes. A phase of the brief is usually several
PRs, merged in the phase's step order.

- A fix found while building something else is its own PR, or a follow-up PR
  after it ("Follow-up to #12"), not a drive-by edit in the diff.
- A pure move or rename lands before the behaviour change that needs it, so the
  behaviour diff stays readable.
- Generated files (regenerated fixtures, golden outputs, `uv.lock`) travel with
  the change that caused them, and the body says they're generated.

Branch: `<type>/<slug>` (`fix/snapshot-latency`, `feat/rank-failure-weight`).

## The title is the commit on `main`

PRs are **squash-merged**. The title becomes the single commit on `main`, with
`(#N)` appended, so it follows the commit rules in docgap-voice exactly: type,
scope, the change and its effect.

Pick the type by what the change does for someone using docgap, not by the shape
of the diff. A dependency bump that fixes a bug is `fix(…)`, not `chore(deps):`,
because `chore` never triggers a release and never shows in the changelog.

## Body

Shape by size and type (docgap-voice has the wording rules and examples):

- **Small:** one or two paragraphs (problem, then change), bullets if several
  mechanical edits, then how it was verified. No headings.
- **Bug, larger or subtle:** `## Problem` → `## Root cause` → `## Change` → `## Validation`.
- **Feature or refactor:** `## What` → `## Why` → `## Changes` → `## Test plan`.
- **`## Release note`**, only when the user-facing effect differs from the diff's
  shape, or when consumers must do something (a new required config key, a
  changed artifact schema).

Every body also covers, in its own words and only when it applies:

- **Changes per file or unit**, one line each: `` `rank.py`: ties broken by FQN ``.
- **Governance impact:** a new grant, a new field in a model's input, a new
  snapshot column. Write "none" only after checking.
- **Evaluation impact:** anything touching questions, split, N, arms, grading or
  ranking inputs after the `preregistered` tag needs an explicit justification,
  or it doesn't land.
- **Records updated:** each record file touched (brief checkbox, ADR,
  `EVAL_PROTOCOL.md`, `CLAUDE.md` current status), per the records map in
  `CLAUDE.md`. Write "none implied" only after checking the map.
- **What was not done or not run**, and why. Pending checks are `- [ ]` boxes.
- **Links:** issue, follow-up, decision record (`docs/adr/0004-…md`).

End with: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`

## Before you push

The pre-PR gate (checks, `/simplify`, which review agent, record updates, current
status, then stop) is defined in `CLAUDE.md` → "The pre-PR gate", and only there,
so the two can't drift. This skill covers the PR's shape. In the diff itself: no
debug output, commented-out code or stray files, and a non-obvious line is
explained by a comment (docgap-voice), not in the PR thread.
