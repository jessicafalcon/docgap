# 0037. Check the 40 on the full months before they merge, since the split can be computed after

- **Status:** Accepted
- **Date:** 2026-10-20
- **Superseded by:** none

## Context and Problem Statement

The gold results are stored from the offline sample, 1 line in 50 with rare codes
dropped, so each gold query is also checked on the three full months (ADR 0013).
There, a grouping can pass the 200-row cap, a top N can tie, and a swap that gives
the gold's result on the sample can change it. A swap that changes the result and
that the question doesn't declare grades a reading the docs support as wrong.
With the IDs Q01 to Q40 fixed, the split is `sha256("1:Qnn")`, and the rule not to
compute it holds only until the questions merge (ADR 0036). After that, a question
fixed because of this check would be fixed with its holdout membership knowable.
The full months are 107,232,480 rows from 2.85 GB of gzipped files, too much for
CI. When does the check run?

## Considered Options

1. **Before the trial**, after the questions merge, as the brief first set it.
2. **Before the baseline docs lock**, after the questions merge.
3. **Before the questions merge:** the full months load offline in their own pull
   request first, and the questions' pull request runs the check in its gate.

## Decision Outcome

Chosen option: **option 3**, because only a fix made before the merge is made with
the split unknown. Option 2 keeps the lock after the questions (ADR 0021), but not
the split out of reach.

`loader/load_duckdb.py full` loads `data/open_damir/` after checking each file
against `loader/sources.lock`, which now pins each file's data rows beside its
size and SHA-256, so the load checks its row count as the sample's does. dbt's
`full` target builds over it, `eval.agent.warehouse full` copies its marts, and
`eval.questions check full` runs every gold query and every reading of it there.

### Consequences

- Good, because every gold query and every reading of it is checked on the rows
  the baseline is graded on, while no question's split is known.
- Good, because the Snowflake `COPY` can check its loaded rows against the same
  lock.
- Bad, because the check runs on a laptop, not in CI: the gate runs it, and a
  later change to the questions is checked only if the gate runs it again.
- Bad, because the full months take 20 GB on disk: 6.9 GB in `RAW.duckdb` and
  6.7 GB each in the build and the agent's copy. On 2026-10-20, on 16 GiB of RAM,
  the load took 55 s, `dbt build` 71 s with its 33 nodes passing, the copy 18 s,
  and the pilot's 12 gold queries 5 s, with no problem.
- Bad, because loading the full months first adds 1 to 2 evenings to the 40's
  budget before any question is written.
