# 0031. Void a pilot pass a harness fault stops, unless it is resumed to completion

- **Status:** Accepted
- **Date:** 2026-10-03
- **Superseded by:** none

## Context and Problem Statement

ADR 0024 allows the pilot two reruns after its first pass. The runner stops a
pass on a harness fault, since every later run would meet it too: the `[llm]`
call or spend limit, or a permanent API error such as a 401 or a malformed
request (ADR 0029). The live pass is also stopped by design: a smoke run comes
first, `[llm] max_spend_usd` is set from its cost per run, and the same pass
continues. A fault says nothing about the setup the pilot tests. Does a pass a
harness fault stops count toward the two reruns?

## Considered Options

1. **Every pass started counts.** A 401 on the first call spends one of three passes.
2. **A stopped pass is void unless resumed to completion; a completed pass always
   counts.** The runner resumes a stopped pass in place: it skips the runs written,
   starts the budget from the calls and spend the pass recorded, and refuses to
   resume on changed inputs.
3. **A stopped pass is void and starts again from its first run.**

## Decision Outcome

Chosen option: **option 2**, because a harness fault is no result of the setup,
and resuming keeps the runs already made instead of drawing them a second time
at a second pass's cost. Option 1 lets an expired key or a budget set too low
decide the pilot. Option 3 pays for the same runs twice. A pass whose fault can't
be fixed without changing its inputs (the questions, the gold, the docs, the
agent database, the prompt or the model calls' timeout, retries and prices)
can't be resumed, since the runner refuses it, and stays void. Its runs are kept
and reported, and the next pass starts in a new directory.

### Consequences

- Good, because the reruns ADR 0024 bounds are spent only on setups that ran
  in full.
- Good, because one pass's `[llm]` limit covers all its processes: a resumed pass
  can't spend a second budget.
- Bad, because a pass could be left stopped after a partial result nobody likes,
  and a fresh one started. The pilot's decision record reports every pass, a
  void one with its partial counts and its stops, so that choice is visible.
- Bad, because the count of calls is written after each run, so a process killed
  mid-run leaves that run's calls uncounted: at most one run's, 9 calls.
