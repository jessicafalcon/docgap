# 0021. Draw the baseline docs lock after the offline pilot, by hashed FQN

- **Status:** Accepted
- **Date:** 2026-09-29
- **Superseded by:** none

## Context and Problem Statement

ADR 0010 picks the baseline's documented half of the mart columns by seed, so
the author has no choice that could favour top-N. The brief built that lock in
Phase 2, before Phase 3's questions exist. N counts the undocumented columns the
discovery gold SQL touches, and the question author would then write gold SQL
knowing which columns are undocumented. Questions aimed at those columns raise U
and favour top-N on holdout, and nothing in the protocol forbade it. The
offline pilot's kill criterion can still change the questions ("harder
questions") and the mart columns ("more coded columns") after the questions are
first committed. Nothing offline reads the lock before the tag: the pilot runs
with no column docs and with every column documented. When is the lock drawn,
and by which rule?

## Considered Options

1. **Draw it in Phase 2**, as planned, before the questions.
2. **Draw it once the 40 questions and their gold SQL are committed**, before the
   pilot.
3. **Draw it once the offline pilot passes the kill criterion**, before N is
   fixed.

For the rule itself:

4. **Sort the mart column FQNs by `sha256(f"{seed}:{fqn}")`** with `seed` =
   `[seeds] baseline_docs` = 3, and document the first `floor(M / 2)` of M.
5. **Keep each column whose digest falls under half the hash range**, with no
   fixed count.

## Decision Outcome

Chosen options: **3 and 4**. Option 3 removes the choice ADR 0010 meant to
remove, at no cost to any offline step. Unlike option 2, it also waits out every
change the kill criterion allows, so no question is edited and no mart column
added once the lock exists. The baseline text is then the locked half of the
every-column text the pilot writes, so the baseline and the ceiling arm can't
word a column two ways. Option 4 mirrors the random-N draw and gives exactly
half. Adding one column moves at most one other column across the cut. Option 5
would give a count that varies with the column list, not 50%.

The lock lands in its own pull request, merged after the pull request that
commits the questions. GitHub records each merge time, so the order has a
witness the committer's clock can't set (as for the tag, `EVAL_PROTOCOL.md`,
"Witnessing the tag").

### Consequences

- Good, because the questions, the pilot's changes to them and the mart columns
  are all settled before the lock exists, and the merge times show it.
- Good, because the lock is drawn once, from the mart columns the pilot settles.
- Bad, because the rule can be computed as soon as the mart columns exist, so the
  order shows only that the lock wasn't committed first, not that nobody computed
  it. The rule is fixed here, before any question exists, so there is no choice
  to make with it.
- Bad, because Phase 2's "baseline coverage equals the locked number" and the
  frozen `fixtures/manifest/baseline.json` wait for the pilot.
