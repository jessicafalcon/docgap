# 0001. Use a Snowflake Enterprise trial, started when the offline pilot passes

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

docgap reads query history and column comments from the warehouse the agent
queries, so it needs a real Snowflake account. A trial account is free but ends
after 30 days or when its free balance (about $400) runs out, whichever comes
first ([trial accounts](https://docs.snowflake.com/en/user-guide/admin-trial-account),
checked 2026-09-26), and that window is the project's binding constraint.
`ACCESS_HISTORY`, the optional cross-check on column resolution, needs
Enterprise edition ([Account Usage](https://docs.snowflake.com/en/sql-reference/account-usage),
checked 2026-09-26). Which account, and when does its clock start?

## Considered Options

1. **Enterprise trial, signed up when the offline pilot passes.** Build and
   pilot on DuckDB first; the trigger is readiness, not a date.
2. **Enterprise trial, signed up at the start.** Simplest; the clock runs while
   the offline work is built.
3. **Standard edition trial.** Same window, no `ACCESS_HISTORY`.
4. **Paid on-demand account.** No deadline, but a bill for every credit.

## Decision Outcome

Chosen option: **option 1**, because it spends the trial only on work that
needs the warehouse. The trigger is the one in the brief's Phase 1: the dbt
project builds on DuckDB, the grader and agent pass their tests, the Phase 5
code runs on pilot outputs, and the kill criterion is met. The region is AWS
`eu-west-3` (Paris), the EU region next to the French source data. The trial end
date and credit balance shown at signup go in a new record then.

### Consequences

- Good, because most of the project's evenings spend no trial days.
- Good, because `ACCESS_HISTORY` is available for the resolve cross-check.
- Bad, because every stage needs an offline path first: a DuckDB dbt profile, a
  frozen sample, and gold SQL transpiled from Snowflake with a tested transpile.
- Bad, because the in-trial schedule leaves a small buffer at 4 evenings a week
  and none at 3. The brief's cut list is the mitigation.
