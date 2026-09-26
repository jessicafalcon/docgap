# 0010. Choose the baseline docs by seed, and put drafts into the arms unedited

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

The evaluation protocol fixes the baseline docs and the drafts that enter the
arms, and both are frozen at the `preregistered` tag. The baseline docs are
needed first, in Phase 2, when the dbt YAML is written. Which columns start
documented, and does the column owner's review change the drafts the arms
measure?

## Considered Options

For the baseline docs:

1. **Seeded random 50% of mart columns** (`[seeds] baseline_docs` = 3).
2. **What a busy team documents first**, chosen by hand.

For the drafts in the arms:

3. **Unedited drafts in every arm**, with the owner's edit rate reported
   separately.
4. **Reviewed drafts in the top-N arm**, and the same review for random-N.

## Decision Outcome

Chosen options: **1 and 3**, because both leave no choice to the author that
could favour top-N. A hand-picked baseline is more realistic but easier to call
rigged, and reviewed drafts would measure the tool plus the reviewer, who by
then has seen the Phase 3 baseline's holdout failures.

### Consequences

- Good, because the "before" state and the arms' contents follow from seeds and
  the tool's output alone, and anyone can regenerate them.
- Bad, because a random 50% is not how a real team's docs look; the README
  states it.
- Bad, because an unedited draft that the owner would have fixed still enters
  its arm. The edit rate from the pull-request review shows how often that
  happens.
