# 0030. Cache the agent's prompt prefix with automatic prompt caching

- **Status:** Accepted
- **Date:** 2026-10-03
- **Superseded by:** none

## Context and Problem Statement

Each agent run makes up to 9 model calls, and each call sends the whole
conversation again: the system prompt, the tools, and every earlier turn,
including `run_sql` results of up to 200 rows. ADR 0027 limits each request to
`max_tokens`, `system`, `tools` and `messages`, so every call pays full input price
for a prefix the call before it already sent. About 700 agent runs remain before
the results: the pilot, the baseline and the arms. Should the agent cache its
prompt prefix?

## Considered Options

1. **Automatic caching:** a top-level `cache_control: {"type": "ephemeral"}`.
2. **Explicit breakpoints** on the system prompt and on the latest turn.
3. **No caching**, as ADR 0027 left it.

## Decision Outcome

Chosen option: **option 1**, because one key covers a growing conversation. The
API puts a 5-minute breakpoint on the last cacheable block and moves it forward
each turn, so a call reads every earlier turn from the cache. Per the Claude API
reference bundled with the `claude-api` skill (checked 2026-10-03), a cache write
costs 1.25× the input rate and a read 0.1× (0.05× on Opus 5.5). A prefix shorter
than the model's minimum (4,096 tokens on Haiku 4.5, 512 on Opus 5.5) is simply
not cached, with no error. Caching changes what the input costs, not the reply,
so the comparison ADR 0027 protects is unchanged. Option 2 needs breakpoint
bookkeeping for no gain in a loop that only appends. The agent's calls within a
run start seconds apart, well inside the 5-minute TTL.

This adds one key to ADR 0027's request; everything else in it stands.

### Consequences

- Good, because from the second call of a run on, most of each request's input is
  billed as a cache read.
- Good, because `[llm]`'s budget stays an upper bound: it charges cache writes at
  twice the input rate and reads at the full rate.
- Bad, because the setting is part of the request and so of the cache key: a
  response cached before this change doesn't replay after it. No pilot response is
  cached yet.
