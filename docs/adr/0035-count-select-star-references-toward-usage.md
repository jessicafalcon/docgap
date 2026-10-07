# 0035. Count a top-level `SELECT *`'s columns toward usage

- **Status:** Accepted
- **Date:** 2026-10-07
- **Superseded by:** none

## Context and Problem Statement

`resolve` expands a top-level `SELECT *` into every column of its table, so each
gets one execution toward *u*. A row preview of the 55-column fact would then
give every fact column the same usage, and could flatten *u* across the widest
table (ADR 0020). Measured on the pilot's traffic, every `run_sql` query and
final answer in `fixtures/pilot/`, normalized and resolved as the snapshot
would, on 2026-10-07:

| Traffic | Queries | Top-level `*` | Of the fact | Column references from `*` | Top 20 by *u*, with and without |
| --- | --- | --- | --- | --- | --- |
| Pass 1, both models | 450 | 75 (16.7%) | 2 | 16.3% | the same 20 |
| Pass 1, Haiku 4.5 | 245 | 25 (10.2%) | 2 | 16.9% | the same 20 |
| Pass 2, both models | 560 | 153 (27.3%) | 0 | 15.1% | 18 of 20 shared |
| Pass 2, Haiku 4.5 | 291 | 56 (19.2%) | 0 | 10.2% | the same 20 |

Every other `*` reads a dimension's 2 columns, a code and its label, where
reading every column is the query's purpose. The two fact previews, both in
pass 1, gave 36 columns no other pass 1 query touched one or two executions. Do a
top-level `SELECT *`'s columns count toward *u*?

## Considered Options

1. **Count them**, as `resolve` does now.
2. **Leave them out of *u*:** `resolve` marks the references a `*` expanded,
   and `usage` skips them.
3. **Leave out a `*` over the fact only.**

## Decision Outcome

Chosen option: **option 1**, because in the traffic measured a `*` reads a
lookup table whole, a use like any other, and the top 20 columns by *u* don't
move for Haiku 4.5, the agent model (ADR 0034). Options 2 and 3 add a rule to
`resolve` and `usage` for an effect the pilot doesn't show. A fact preview
lifts the columns it touches from *u* = 0 to 1, above the untouched ones; that
matters only where top-N reaches columns with no other use, which the results
already report (ADR 0020).

### Consequences

- Good, because `resolve` and `usage` don't change before the tag.
- Bad, because the baseline's traffic may preview the fact more than the pilot
  did. `resolve` already counts `queries.top_level_star` in its stage record, and the
  results report it beside the top-N columns with *u* = 0.
