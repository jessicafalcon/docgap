# 0029. Make a failed model call again in place, and end a context overflow as `error`

- **Status:** Accepted
- **Date:** 2026-10-03
- **Superseded by:** none

## Context and Problem Statement

The protocol repeats an agent run whose model call fails after the retry policy.
Restarting the run from its first turn replays the cached replies, but it runs
every earlier tool query again: on Snowflake they go out a second time under the
same `agent:<run_id>:<qid>:<rep>` tag, so `usage` counts them twice and a column's
*u* depends on API luck. Offline, a re-run `GROUP BY` can return its rows in
another order, the cache misses, and the repeat becomes a fresh draw. Separately,
a conversation can outgrow the model's context window: past it, the input is a
400 `invalid_request_error` ("prompt is too long") on every model, and a reply
that reaches it stops with `model_context_window_exceeded` on 4.5 and newer
models (Claude API "Context windows", read 2026-10-03). Previously that 400
stopped the whole pilot pass. How does a run repeat, and how does an overflow end?

## Considered Options

1. **Make the failed call again in place**, the conversation kept; the run's 3rd
   failed call ends it as `error`. A context overflow is the run's `error`, cause
   `context_exceeded`, cached like a reply.
2. **Restart the run and tag each attempt**, so the snapshot drops earlier ones.
3. **Restart the run and dedupe** repeated queries in the snapshot.
4. **Stop the pass on any permanent API error**, the overflow included.

## Decision Outcome

Chosen option: **option 1**, because no tool query runs twice, so usage and the
offline cache need no repair afterwards. Options 2 and 3 change the tag format or
add logic to the core, and leave the offline fresh draw. An overflow comes from
the agent's own choices, such as several 200-row previews of a wide table, so it
is graded like a final answer that fails. Option 4 would stop a pass on one
question. Any other permanent API error still stops the pass: it is a harness
fault, and every later run would fail the same way.

### Consequences

- Good, because a run's queries in the history are exactly the ones the agent
  chose, whatever the API did.
- Good, because an overflow replays offline: the client caches it as the call's
  outcome and raises it again on a hit.
- Bad, because the overflow 400 is recognised by its message text, the one the
  docs give, and a client test pins it. If the API changes the text, the 400
  stops the pass instead, which is loud.
