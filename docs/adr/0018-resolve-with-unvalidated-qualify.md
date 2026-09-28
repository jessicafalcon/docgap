# 0018. Resolve columns with an unvalidated `qualify`, pushed-down projections and rows for mart columns only

- **Status:** Accepted
- **Date:** 2026-09-28
- **Superseded by:** none

## Context and Problem Statement

`resolve` turns each normalized snapshot query into the mart columns it reads, which
`usage`, `coverage` and `rank` count. Agent queries are often wrong: a misspelled
column, a table outside the marts, a `describe()` on `INFORMATION_SCHEMA`. With
sqlglot 30.20, `qualify` validates by default and raises on the first unknown name,
and even with validation off it raises on a qualified unknown column (`F.TYPO`)
unless partial qualification is allowed. A `SELECT *` in a CTE expands to every
column of the table, although the outer query may read one. The dbt manifest lists
only columns declared in YAML. How does `resolve` qualify a query, and what does it
keep?

## Considered Options

1. **Validated `qualify`.** A query with one unknown name is dropped and counted whole.
2. **Unvalidated `qualify`, then `pushdown_projections`, each reference classified.**
   `validate_qualify_columns=False` and `allow_partial_qualification=True`; every
   reference is resolved, unmanaged, an `INFORMATION_SCHEMA` read or unresolved, and
   counted. Only mart columns get a `ColumnRef` row. The marts are one configured
   schema, and each relation in it must be a model with an enforced contract.
3. **`ACCESS_HISTORY` as the source of columns.** Snowflake's own column lineage.

## Decision Outcome

Chosen option: **option 2**, because one wrong name in a query then costs only that
reference, and the query's other columns still count. Option 1 would drop most
failed agent queries, which the ranking needs most. Option 3 needs a warehouse, lags
by 3 hours and excludes failed queries; it stays a cross-check in Phase 3.

The details that follow from it:

- **Unmanaged** is a relation qualified in full outside the marts, staging models
  included: the agent can't read them, so their columns are never ranked. A table
  the session context can't qualify is **unresolved**.
- A name that `qualify` ties to no source counts as unmanaged or `INFORMATION_SCHEMA`
  when every relation in its scope is one, and unresolved otherwise (an unknown or
  ambiguous name).
- A correlated column counts once, in the scope that owns it; a set operation's
  `ORDER BY` names its output and counts nothing; a column read through a CTE or
  subquery counts where the base table is read.
- `ColumnRef.managed` is dropped, since every row is a mart column, and
  `RunSetup.schema_version` moves 2 → 3.
- `[manifest] mart_database` and `mart_schema` name the marts in `docgap.toml`.

### Consequences

- Good, because a failed query still shows which columns the agent reached for, and
  every reference that isn't a mart column is counted by kind in the stage record.
- Good, because a `SELECT *` in a CTE counts only what the outer query reads, so it
  doesn't inflate every column of a wide fact table.
- Bad, because the clause is the one in the scope reading the base table: a column
  grouped on through a CTE counts as `select`.
- Bad, because the behaviour depends on how sqlglot 30.20 qualifies. The lockfile
  pins sqlglot, and the ten hand-checked queries in `tests/test_resolve.py` fail on
  a change. They are hand-made; the check on real gold SQL is owed by Phase 3, and
  a read of a real manifest by Phase 2 (both in the brief).
