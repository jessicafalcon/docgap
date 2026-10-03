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
bookkeeping for no gain in a loop that only appends. A run's next call starts when
the reply and one tool call are done, usually well inside the 5-minute TTL; a long
Opus 5.5 reply or a failed call can outlast it, and the prefix is then written again.

This adds one key to ADR 0027's request; everything else in it stands. The fixed
request keys (`system`, `tools`, `max_tokens`, `cache_control`) are built in one
place and hashed into the call site's `prompt_sha256`, so the run's setup covers this
key, and `PROMPT_VERSION` moves to `agent-2`.

### Consequences

- Good, because once a run's prefix passes the model's minimum, most of each later
  request's input is billed as a cache read. On Haiku 4.5 the first turns are below
  4,096 tokens and aren't cached.
- Good, because `[llm]`'s budget stays an upper bound: it charges cache writes at
  twice the input rate and reads at the full rate.
- Bad, because those bounds make the counted spend higher than without caching,
  while the real spend falls: each written token is counted once more at the input
  rate. On a pilot pass estimated at $30 before caching, that is a few dollars,
  inside `max_spend_usd = 45`.
- Bad, because the setting is part of the request and so of the cache key: a
  response cached before this change doesn't replay after it. No pilot response is
  cached yet.
