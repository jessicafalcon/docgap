# 0021. Draw the baseline docs lock after the questions are committed, by hashed FQN

- **Status:** Accepted
- **Date:** 2026-09-29
- **Superseded by:** none

## Context and Problem Statement

ADR 0010 picks the baseline's documented half of the mart columns by seed, so
the author has no choice that could favour top-N. The brief built that lock in
Phase 2, before Phase 3's questions exist. N counts the undocumented columns the
discovery gold SQL touches, and the question author would then write gold SQL
knowing which columns are undocumented. Questions aimed at those columns raise U
and favour top-N on holdout, and nothing in the protocol forbade it. Nothing
offline reads the lock before the tag: the pilot runs with no column docs and
with every column documented. When is the lock drawn, and by which rule?

## Considered Options

1. **Draw it in Phase 2**, as planned, before the questions.
2. **Draw it after the 40 questions and their gold SQL are committed**, before N
   is fixed.

For the rule itself:

3. **Sort the mart column FQNs by `sha256(f"{seed}:{fqn}")`** with `seed` =
   `[seeds] baseline_docs` = 3, and document the first `floor(M / 2)` of M.
4. **Keep each column whose digest falls under half the hash range**, with no
   fixed count.

## Decision Outcome

Chosen options: **2 and 3**. Option 2 removes the choice ADR 0010 meant to
remove, at no cost to any offline step. It also waits out the pilot's kill
criterion, whose remedy ("more coded columns") changes the mart columns the lock
is drawn from. The baseline text is then the locked half of the every-column
text the pilot writes, so the baseline and the ceiling arm can't word a column
two ways. Option 3 mirrors the random-N draw and gives exactly half. Adding one
column moves at most one other column across the cut. Option 4 would give a
count that varies with the column list, not 50%.

### Consequences

- Good, because the questions are written with no lock to aim at, and the git
  history shows the order: the lock's commit follows the questions' commit.
- Good, because the lock is drawn from the mart columns the questions and the
  pilot settle, so it isn't redrawn.
- Bad, because the rule can be computed before the lock is committed, so
  commit order alone can't prove it wasn't. The rule is fixed here, before any
  question exists, so it leaves no choice to make.
- Bad, because Phase 2's "baseline coverage equals the locked number" and the
  frozen `fixtures/manifest/baseline.json` wait for Phase 3's questions step.
