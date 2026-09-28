# 0019. Require a ranking scope for any agent-tagged traffic, not only for several runs

- **Status:** Accepted
- **Date:** 2026-09-28
- **Superseded by:** none

## Context and Problem Statement

[ADR 0015](0015-rank-from-one-agent-run.md) keeps each query's agent run ID and
session context, and ranks from one agent run: `usage` takes a ranking scope, one
run ID and its discovery `qid`s, and fails when tagged traffic from more than one
run arrives with no scope. The baseline
snapshot the ranking reads is exported by the baseline's tag prefix, so it holds
exactly one run, and every one of its 40 questions, the 15 holdout ones included.
Called without a scope, `usage` passed that snapshot, and all holdout traffic
reached *u* with no error and no counter. The protocol forbids that: ranking inputs
come from discovery questions only. When must `usage` refuse to run without a scope?

## Considered Options

1. **Keep ADR 0015's rule, and make `cli.py` require a scope** whenever the snapshot
   holds tagged traffic.
2. **`usage` fails on any agent-tagged traffic with no scope.** Untagged traffic
   still runs without one.

## Decision Outcome

Chosen option: **option 2**, because the stage that counts holdout traffic is the
one that must refuse it, whoever calls it. Option 1 puts the guard in the one caller
that exists today, and a test harness or the DAG calling `usage` directly would
skip it. ADR 0015's other decisions stand: `QueryRecord` keeps `run_id`,
`database_name` and `schema_name`, and the ranking reads one agent run.

The scope becomes the `RankingScope` contract in `models.py`, with its JSON Schema
committed, since `cli.py` reads it from a file. Its `qid`s are sorted and
deduplicated, so equal scopes hash the same, and the hash is a `usage` input. Two
checks come with it, so a wrong scope can't pass as a result: `usage` fails when
the counted traffic touches no mart column, which would rank every column at zero,
and it counts the scope's `qid`s that match no query.

### Consequences

- Good, because a holdout query can reach `usage` only through a scope that names
  its `qid`, and a test pins both the one-run and the two-run case.
- Good, because an adopter's untagged traffic runs as before, with no scope.
- Bad, because a snapshot of mixed traffic, agent runs and people, needs a scope,
  which then leaves the people's queries out. Phase 6's arms and the baseline don't
  mix them; an adopter with both would need the scope to allow untagged traffic,
  which no step needs yet.
