---
name: docgap-pr
description: >
  How pull requests are shaped in docgap: one coherent change per PR, the title
  as the squash-merge commit, and what goes in each section of the PR template.
  Read this BEFORE writing a PR title or body (step 5 of the pre-PR gate in
  CLAUDE.md, which owns the gate itself). docgap-voice governs the wording.
---

# docgap-pr

## One change per PR

A PR carries **one coherent change**: a fix, a feature, a refactor, a
dependency bump, or related changes to the same area that land together (two
fixes to the guard hooks). Size follows the change, from a one-line fix to a
40-file feature, but never two unrelated changes: each gate run costs tokens, so
batch what belongs together and split what doesn't. A phase of the brief is usually several
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

Every development PR fills all six sections of `.github/pull_request_template.md`,
in order, whatever its size; a small PR keeps each to a line or two. The PRs
docgap itself opens (`docgap/<run_id>`) have their own body, set in the brief's
Phase 6. Build the body from the template and delete its HTML comments: GitHub
hides them, but they stay in the raw body that `gh pr view` shows. Pass it with
`--body-file` and an absolute path, or inline; the private-terms guard reads both.

- **Summary.** The problem, then what this PR changes. Then the brief step it
  serves, the ADR it implements, and any follow-up or issue link, each "none"
  when there isn't one.
- **Changes.** One line per file or unit: `` `rank.py`: ties broken by FQN ``.
  Generated files are marked as generated.
- **Impact.** All four lines, each "none" only after checking. An Evaluation
  change after the `preregistered` tag follows `CLAUDE.md` → "After
  `preregistered`", or it doesn't land. Compatibility includes anything a
  consumer must do.
- **Validation.** One box per pre-PR gate step that applied (two for step 3 on a
  phase's last PR, one per agent), naming the command or agent and what it showed. An unticked box means not done. Add a line of
  prose for a check a box can't carry (a fresh-clone run, a positive control).
- **Records updated.** Each record touched, per the records map in `CLAUDE.md`,
  or "none implied" after checking the map.
- **Not done.** Deliberate omissions, options tried and discarded (one sentence
  each, with why), deferred checks and open risks, each with where it's tracked.
  "Nothing" when that's true.

## Before you push

The pre-PR gate (checks, cleanup review, which review agent, record updates, current
status, then stop) is defined in `CLAUDE.md` → "The pre-PR gate", and only there,
so the two can't drift. This skill covers the PR's shape. In the diff itself: no
debug output, commented-out code or stray files, and a non-obvious line is
explained by a comment (docgap-voice), not in the PR thread.
