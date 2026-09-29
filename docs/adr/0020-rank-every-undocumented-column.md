# 0020. Rank every undocumented mart column, untouched ones at score 0

- **Status:** Accepted
- **Date:** 2026-09-28
- **Superseded by:** none

## Context and Problem Statement

`usage` writes a row only for the mart columns the counted traffic touches. The
protocol takes top-N as the first N rows of the ranking, with N fixed before
the tag from the gold SQL, and draws random-N from every column undocumented in
the baseline docs, touched or not. An arm is the N columns chosen. If the
ranking held only touched columns and the Phase 3 baseline touched fewer than N
undocumented ones, top-N would be shorter than random-N. Which columns does the
ranking hold, and what fills top-N then?

## Considered Options

1. **Touched columns only.** Top-N can come out shorter than N.
2. **Touched columns first, then the tail from the random arm's hash order.**
   Top-N always has N columns.
3. **Every undocumented mart column in the manifest.** A column no counted
   query touched has *u* = 0, so it scores 0 and sorts after every used column,
   by FQN, like any other tie.

## Decision Outcome

Chosen option: **option 3**, because it gives top-N and random-N one pool and
one size with no rule beyond the ranking's own order. Option 1 breaks arm size
parity. Option 2 adds a second ordering rule inside the ranking and raises the
overlap with random-N, which blurs the comparison. `rank` reads the undocumented
columns from the dbt manifest: a column whose description is empty or only
whitespace is undocumented.

### Consequences

- Good, because `ranked_gaps.parquet` is the whole gap list the product
  promises, and top-N always has N columns.
- Good, because coverage and rank read the same universe, the manifest's mart
  columns, and fail when a usage row names a column outside it.
- Bad, because if the baseline touches fewer than N undocumented columns, the
  tail of top-N is filled in FQN order, which reflects no usage. The results
  report how many top-N columns have *u* = 0 (the Phase 6 "Compare honestly"
  step).
- Bad, because one query touches several columns, so equal *u* is common and the
  FQN tie-break often decides the ranks around N. The results report the tie
  group at rank N (the same step).
- Bad, because a top-level `SELECT *` gives every column of its table the same
  usage, and agent row previews could flatten *u* across the widest table. The
  offline pilot measures how often agents do this, and the decision on whether
  those references count toward *u* is taken before the tag (Phase 3's pilot
  step).
