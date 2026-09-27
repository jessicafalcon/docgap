# 0014. Have the agent write Snowflake SQL offline too, transpiled to DuckDB

- **Status:** Accepted
- **Date:** 2026-09-27
- **Superseded by:** none

## Context and Problem Statement

The offline pilot runs the test agent on DuckDB and picks the model that later
writes queries on Snowflake (`EVAL_PROTOCOL.md`, "The agent model"). Phase 5 is
built on the pilot's failures, and `resolve` parses the agent's SQL with sqlglot in
dialect `snowflake` (ADR 0002). dbt-duckdb names its database after the file and
prefixes custom schemas, so its column names don't match the `Fqn` contract
(`DATABASE.SCHEMA.TABLE.COLUMN`, uppercase) unless the project sets them. Which SQL
does the agent write offline, and under which names?

## Considered Options

1. **Snowflake SQL in both worlds.** The prompt says Snowflake. Offline, the harness
   transpiles each `run_sql` call and the final answer to DuckDB with sqlglot, as
   it does gold SQL. The DuckDB profile builds into `ANALYTICS.STAGING` and
   `ANALYTICS.MARTS`.
2. **DuckDB SQL offline, Snowflake SQL in the trial.** No transpile step for the
   agent's SQL, but two prompts.

## Decision Outcome

Chosen option: **option 1**, because the pilot then measures the agent the trial
runs, with one prompt, and its queries reach Phase 5 in the dialect `resolve`
parses. With option 2, the model is chosen on a prompt the trial doesn't use, and
the pilot's failures would need a second parse path.

### Consequences

- Good, because the agent's prompt, the gold SQL, the column FQNs and the dbt
  manifest are the same offline and on Snowflake.
- Bad, because a query sqlglot can't transpile fails offline and might have run on
  Snowflake. A final answer that can't be transpiled fails the run, and the pilot
  reports the count apart from wrong answers; the transpile of every gold query is
  tested (owed by Phase 3's "Offline first").
- Bad, because the DuckDB profile must set the database and schema names by hand
  (owed by Phase 2's "Offline first").
