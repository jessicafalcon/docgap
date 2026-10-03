# 0028. Freeze the model calls' timeout and retries at the tag

- **Status:** Accepted
- **Date:** 2026-10-02
- **Superseded by:** none

## Context and Problem Statement

An agent run's model call that fails after the retry policy is made again, and the
run's 3rd failed call ends it as `error` (protocol "Runs" item 4, ADR 0029). The
policy is set by `[llm] timeout_seconds` (450 s) and `max_retries` (2), and the
attempts by `[agent] run_attempts` (3). Together they decide which runs end as `error`, so they
move accuracy. The setup hash keeps them equal across the arms of one session, but
nothing stopped them changing between the `preregistered` tag and the baseline.
Should they be frozen with the agent?

## Considered Options

1. **Freeze them at the tag**, with the `[agent]` limits, and name them in the protocol.
2. **Leave them free**, relying on the setup hash for arm parity.

## Decision Outcome

Chosen option: **option 1**, because a value that changes which runs fail belongs
with the agent's other limits, and the baseline and the arms should fail the same
way as the setup at the tag. `run_attempts` sits in `[agent]`, already frozen;
`CLAUDE.md` → "After `preregistered`" now lists the two `[llm]` keys, and protocol
"Runs" item 4 names all three values.

### Consequences

- Good, because a slow API day can't be answered by a longer timeout between the
  tag and the baseline without an ADR and a reported deviation.
- Bad, because if the trial's API latency makes 450 s too tight, raising it is a
  protocol deviation. The budget keys (`max_calls`, `max_spend_usd`, prices) stay
  free: they stop a run, they never grade one.
