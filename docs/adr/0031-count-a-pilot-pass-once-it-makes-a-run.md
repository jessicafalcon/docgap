# 0031. Count a pilot pass once it makes a run, and resume a stopped one in place

- **Status:** Accepted
- **Date:** 2026-10-03
- **Superseded by:** none

## Context and Problem Statement

ADR 0024 allows the pilot two reruns after its first pass, a limit "set before
any result is seen". The runner stops a pass on a harness fault, since every
later run would meet it too: the `[llm]` call or spend limit, or a permanent API
error such as a 401 or a malformed request (ADR 0029). The live pass also pauses
by design: a smoke run comes first, `[llm] max_spend_usd` is set from its cost
per run, and the same pass continues. A fault says nothing about the setup the
pilot tests, but a pass that has made runs has shown results. Which passes count
toward the two reruns?

## Considered Options

1. **Every pass started counts.** A 401 on the first call spends one of three passes.
2. **A pass never completed is void.** A stopped pass may be resumed and then
   counts; one left stopped doesn't.
3. **A pass counts once it makes a run.** A pass that has made no run is void,
   whatever stopped it. A stopped or unfinished pass is resumed in place, and no
   new pass starts while one that counts is unfinished.

## Decision Outcome

Chosen option: **option 3**, because whether a pass counts is then settled
before anyone reads its results. Under option 2, a smoke run or a pass the budget
stops shows partial accuracies, and leaving it stopped to start a pass on a
changed setup would spend no rerun. Option 1 lets an expired key decide the
pilot. The runner resumes a pass in place: it skips the runs written, starts the
budget from the calls and spend the pass recorded, and refuses to resume on
changed inputs (the questions, the gold, the docs, the agent database, the agent,
client, manifest-reader and grader code, the versions of the packages a run goes
through, the prompt, or the model calls' timeout, retries and prices). It counts
a pass from its transcripts on disk, refuses a new pass while an earlier one has
made a run and isn't completed, and starts none once three count. One lock
covers the whole pilot, so two new passes can't start side by side.

### Consequences

- Good, because the two reruns ADR 0024 bounds can't be stretched by setting
  aside a pass with results.
- Good, because one pass's `[llm]` limit covers all its processes: a resumed pass
  can't spend a second budget.
- Bad, because a fault that can't be fixed without changing a pass's inputs,
  such as a model withdrawn mid-pass, leaves a pass that counts and can't finish.
  Starting another then needs a decision record superseding this one.
- Bad, because the runner reads the working tree: a pass directory deleted
  before it is committed leaves no trace. Each process's output is committed and
  pushed before the next starts, the smoke run's included.
- Bad, because the count of calls is written after each run, and a process
  killed hard mid-run loses that run's count: at most 11 calls (9 answered, 2
  failed) and their spend. Any Python exception saves the count first.
