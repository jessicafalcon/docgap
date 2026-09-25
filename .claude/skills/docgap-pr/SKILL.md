---
name: docgap-pr
description: >
  How pull requests are shaped in docgap: one behaviour change per PR, the title
  as the squash-merge commit, and what goes in each section of the PR template.
  Read this BEFORE
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

Every PR uses `.github/pull_request_template.md`, whatever its size: the same
six sections in the same order, so a reviewer always finds impact and validation
in the same place. A small PR keeps each section to a line or two; it doesn't
drop sections. `gh pr create --body-file` replaces the template, so build the body
from a copy of the file, fill every section, and delete the HTML comments.

- **Summary.** Result first, in 2–4 sentences: the problem, then what this PR
  changes. For a fix, the root cause is mechanistic (docgap-voice). Then the
  brief step it serves and the ADR it implements, or "none".
- **Changes.** One line per file or unit: `` `rank.py`: ties broken by FQN ``.
  Generated files (`uv.lock`, golden outputs) are marked as generated.
- **Impact.** All four lines, each "none" only after checking:
  - *Evaluation:* questions, split, N, arms, grading or ranking inputs. After
    the `preregistered` tag, a change here needs an ADR with a justification, or
    it doesn't land.
  - *Governance and privacy:* a new grant, contract field or snapshot column, or
    anything new that reaches disk or a model.
  - *Determinism:* a new input to a hash, cache key or run manifest.
  - *Compatibility:* a contract `schema_version`, a config key, an artifact
    shape, or anything a consumer must do.
- **Validation.** Each box names a command or check and what it showed; an
  unticked box means not done, never "probably fine". Include the review agent's
  verdict and that its blocker and major findings are resolved. Add a line of
  prose for any check the boxes can't carry (a fresh-clone run, a positive
  control).
- **Records updated.** Each record touched, per the records map in `CLAUDE.md`,
  or "none implied" after checking the map.
- **Not done.** Deliberate omissions, options tried and discarded (one sentence
  each, with why), deferred checks and open risks, each with where it's tracked.
  "Nothing" when that's true.

End with: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`

## Before you push

The pre-PR gate (checks, `/simplify`, which review agent, record updates, current
status, then stop) is defined in `CLAUDE.md` → "The pre-PR gate", and only there,
so the two can't drift. This skill covers the PR's shape. In the diff itself: no
debug output, commented-out code or stray files, and a non-obvious line is
explained by a comment (docgap-voice), not in the PR thread.
