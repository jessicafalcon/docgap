# 0002. Parse and resolve queries with sqlglot, offline

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

`snapshot` must replace literals and fingerprint every query before its text
reaches disk, and `resolve` must turn each query into fully qualified column
references against the dbt manifest. Both run on failed queries too, since
those feed failure attribution, and both must be deterministic and run in CI
with no warehouse. Gold SQL is written for Snowflake but also runs on DuckDB
offline. What parses the SQL?

## Considered Options

1. **sqlglot, dialect `snowflake`.** A parser with scope resolution (`qualify`
   expands `*` and resolves aliases and CTEs against a schema), lineage, and
   Snowflake-to-DuckDB transpiling.
2. **`ACCESS_HISTORY` as the column source.** Snowflake's own record of the
   columns a query touched.
3. **sqlparse.** A non-validating tokenizer and splitter.
4. **Parsing inside Snowflake** (`EXPLAIN`, `query_parameterized_hash`). The
   warehouse's own view of each query.

## Decision Outcome

Chosen option: **sqlglot**, because it is the only option that covers
fingerprinting, column resolution and transpiling in one library, offline and
deterministically. `ACCESS_HISTORY` stays as a cross-check on successful
queries (target agreement ≥ 95%), and `query_parameterized_hash` as a
cross-check on fingerprints. Neither can be the source: both need the warehouse,
and `ACCESS_HISTORY` needs Enterprise edition and has no column record for a
failed query. sqlparse has no schema or scope resolution, so it can't expand
`*` or tell which table an unqualified column belongs to.

### Consequences

- Good, because resolve, the fingerprint and the transpile are unit-testable on
  fixtures, with no network.
- Good, because one parser sees the same query the same way in `snapshot`,
  `resolve` and the gold-SQL transpile.
- Bad, because dialect gaps undercount usage. Every report shows the parse and
  resolve rates, and unparseable queries are counted with a reason, never
  dropped.
- Bad, because a sqlglot upgrade can change normalized SQL and therefore
  fingerprints. It is pinned exactly in `uv.lock` when added, and a version bump
  is a change to the core's inputs, checked by the determinism tests.
