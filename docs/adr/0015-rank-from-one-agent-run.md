# 0015. Keep the run ID and session context in each query record, and rank from one agent run

- **Status:** Superseded
- **Date:** 2026-09-28
- **Superseded by:** [0019](0019-scope-all-tagged-agent-traffic.md)

## Context and Problem Statement

An agent query's tag is `agent:<run_id>:<qid>:<rep>`. `ColumnUsage.runs` counts the
runs that touched a column, and it is the denominator of the failure rate *r*. One
history window can hold several agent runs of the same questions and repetitions: a
baseline restarted under a new as-of, and in Phase 6 the baseline and every arm,
whose run IDs carry the arm name. Without the run ID those runs merge. With it
alone they still add up, while the failures in *r*'s numerator come from one run's
grades, since `Grade` and `Attribution` carry no run ID: two runs in scope halve
*r* and double *u*. Separately, an agent writes `FROM FCT_REIMBURSEMENTS`, and
`resolve` can only qualify that name with the session's database and schema, which
`QUERY_HISTORY` records as `DATABASE_NAME` and `SCHEMA_NAME`, "specified in the
context of the query at compilation" ([Snowflake docs](https://docs.snowflake.com/en/sql-reference/account-usage/query_history),
checked 2026-09-28). What does a query record keep, and which traffic does the
ranking read?

## Considered Options

1. **Drop the run ID.** Count runs as (qid, repetition) and rely on the export's
   tag filter to hold one run.
2. **Keep the run ID and count runs as (run_id, qid, repetition).** Report how many
   run IDs a snapshot holds.
3. **Keep the run ID and rank from one run.** `usage` takes a ranking scope, one
   agent run ID and its discovery `qid`s, and fails when tagged traffic from more
   than one run arrives with no scope.

## Decision Outcome

Chosen option: **option 3**, because it is the only one where *u* and *r* read the
same run as the grades whatever the window holds. Option 1 depends on an export
filter the offline path doesn't have. Option 2 makes a second run visible but still
adds it in. `QueryRecord` also keeps `database_name` and `schema_name`, nullable
for a session with no context. The snapshot counts the distinct agent run IDs it
keeps. `RunSetup.schema_version` moves 1 → 2.

### Consequences

- Good, because a restarted baseline or the Phase 6 arms can share a window
  without changing the ranking, and a test pins it.
- Good, because an unqualified table name resolves as Snowflake resolved it.
- Bad, because every evaluation run of `usage` must name the agent run. For an
  adopter's untagged traffic nothing changes: with no agent tags there is nothing
  to scope.
- Bad, because Phase 5 must take attributions from the same run, which the brief's
  soft failure rate step now states.
