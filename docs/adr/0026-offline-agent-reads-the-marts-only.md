# 0026. Give the offline agent a marts-only database with DuckDB's file access off

- **Status:** Accepted
- **Date:** 2026-10-01
- **Superseded by:** none

## Context and Problem Statement

On Snowflake the test agent runs as `SVC_AGENT`, which reads `ANALYTICS.MARTS` and
nothing else. Offline, its queries run on DuckDB, where dbt builds the marts, the
staging view and the seeds into one `ANALYTICS.duckdb`. A DuckDB connection can also
read files: on the offline sample's warehouse, DuckDB 1.5.6, a default read-only
connection returned the dictionary's path for
`SELECT count(*) FROM glob('eval/reference/*')` and opened the workbook with
`read_xlsx('eval/reference/<the descriptor>.xlsx')` (probed on 2026-10-01). An agent
that reads the dictionary in the no-docs configuration narrows the gap the pilot
measures, and reads the ground truth drafts are graded against. What can the
offline agent reach?

## Considered Options

1. **The build's `ANALYTICS.duckdb` as is**, with the prompt naming the marts.
2. **A copy holding `ANALYTICS.MARTS` only**, opened read-only with
   `enable_external_access` off.
3. **A query filter** that refuses any statement whose tables `resolve` places
   outside the marts.

## Decision Outcome

Chosen option: **option 2**, because it matches `SVC_AGENT`'s reach by construction.
With `enable_external_access` off, the same probes fail with a permission error, and
so do `read_csv` over `warehouse/dbt/seeds/` and an `ATTACH` of `RAW.duckdb`;
`SET enable_external_access = true` is refused while the database runs. A copy
holding the 6 mart tables, opened as the main database from a file named
`ANALYTICS.duckdb`, resolves `ANALYTICS.MARTS.FCT_REIMBURSEMENTS` and lists no
other schema, where the build's file also lists 5 STAGING relations (all probed
on 2026-10-01). Option 1 relies on the agent obeying the prompt. Option 3 depends
on `resolve` seeing every table function, which it isn't built to do.

### Consequences

- Good, because the offline agent can reach what the trial's agent can, and nothing
  more.
- Good, because a test can check it: `list_tables()` returns the marts only, and a
  file read fails.
- Bad, because the copy is one more step after each `dbt build`, and the
  full-docs configuration's manifest is built apart from it (ADR 0025).
- Bad, because the copy must open as the main database from a file named
  `ANALYTICS.duckdb`, since DuckDB names the catalog after the file, for the
  agent's `ANALYTICS.MARTS` names to resolve (owed by the agent loop's step).
