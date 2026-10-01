# 0022. Freeze the mart columns' sensitivity tags at `preregistered`

- **Status:** Accepted
- **Date:** 2026-10-01
- **Superseded by:** none

## Context and Problem Statement

Each mart column's `meta.sensitivity` decides what its evidence packet may hold:
a `restricted` column gets no values at all (ADR 0003), so the drafter writes
from its name, type and lineage alone, and the gate scores that draft. The tags
therefore shape the drafts, their bands, and what each arm adds. Phase 2 tags
the beneficiary's age bracket, sex and region of residence `restricted`, and
every other column `public`, since Open DAMIR is published open data. `docgap
lint` checks only that each tag is one of the three values, so retagging a
column after the Phase 3 baseline, which shows holdout failures, would pass
every check. Neither list of values frozen at the `preregistered` tag names the
tags. Should they be frozen with the other values that pick the arms' content?

## Considered Options

1. **Freeze them at the tag**, listed with the values that pick the arms'
   content, with a test pinning the `restricted` set.
2. **Rely on the manifest hash.** A retag changes the manifest each run records,
   but each arm's manifest already differs by its descriptions, so a retag
   between arms would show only to a reader who diffed them.

## Decision Outcome

Chosen option: **1**, because a tag picks what evidence a drafted column gets,
exactly as *k* does, and *k* is already frozen.

### Consequences

- Good, because no tag can move once holdout failures are visible, and
  `tests/warehouse/test_marts.py` fails if the `restricted` set changes.
- Good, because the dimension and aggregate columns still to come are tagged
  before the tag, under the same rule.
- Bad, because a tag found wrong after the tag needs an ADR and a reported
  deviation, like any other frozen value.
