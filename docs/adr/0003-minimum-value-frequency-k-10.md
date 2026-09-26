# 0003. Show a value to a model only if it occurs at least k = 10 times

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

The drafter and the draft gate see an aggregate profile of each column: null
rate, distinct count, min and max for numbers, and top values. A value that
occurs only a few times can single out a person or a small group, and once it
reaches a prompt it can reach a draft, a pull request and the model-response
cache. Open DAMIR is already anonymized open data, so here the rule shows the
mechanism working; it is written as it would be for data that is not. What
minimum frequency must a value have before any model call sees it?

## Considered Options

1. **No threshold.** Rely on the source's own anonymization.
2. **k = 5.** Suppress values seen fewer than 5 times.
3. **k = 10.** Suppress values seen fewer than 10 times.
4. **Noise on counts** (differential privacy). Perturb every count instead of
   suppressing rare ones.

## Decision Outcome

Chosen option: **k = 10**, because it is the stricter of the two thresholds and
costs the drafter nothing it needs. A description is written from the values
that dominate a column, and in a month of about 37M rows a code seen fewer than
10 times says nothing about what the column means. No threshold leaves the
mechanism untested; noise would change the numbers the drafter describes and
add a privacy budget and a seed to the core. `restricted` columns get no values
at any k.

### Consequences

- Good, because one value in `docgap.toml` governs the profile queries, the
  evidence packets and the drafter's view, and the rule is unit-tested.
- Bad, because the offline sample (about 2M rows) has smaller counts than a full
  month, so more values fall under k offline and pilot drafts see fewer values
  than live ones.
- Bad, because profiles are frozen with a hash in Phase 3. Changing k after
  that means re-profiling every mart column and a record that supersedes this
  one.
