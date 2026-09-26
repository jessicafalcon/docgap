---
name: docgap-efficiency
description: >
  Token and compute efficiency for docgap. Build time: working with Opus 5.5 as the
  only model (main session and subagents), when fan-out pays, keeping the cached
  prompt prefix stable, memory, and fresh sessions. Runtime: the model-response cache,
  frozen snapshots and profiles, Snowflake cost control, and dbt selection. Read
  this when a task spans many files or turns, before delegating to subagents, and
  when wiring any external call.
---

# docgap-efficiency

Two faces: spend the model's context well while building, and never pay twice for
the same warehouse query or model call while running. Neither may weaken the
correctness invariants.

## Build time

**One model.** Opus 5.5 runs the main session and every subagent
(`.claude/settings.json` pins it). There is no cheaper model to hand work to, so
delegation is about **context**, not price.

- **Delegate fan-out, not single lookups.** Spawn a subagent when answering means
  reading many files or pages and you only need the conclusion (an audit of every
  stage's failure handling, a sweep of Snowflake docs). A known file or symbol is
  a direct read. A subagent re-reads context from scratch and costs a full Opus
  pass; it pays only when it keeps many tokens out of the main thread.
- **A fresh agent costs ~40–65k tokens on a small diff** (the agents' usage
  reports on #1 and #2, diffs of 220–370 lines), mostly reading the brief,
  `CLAUDE.md` and skills. The count of agents drives cost, not
  the diff size. Hence the gate sized by PR type (`CLAUDE.md`), one agent for all
  cleanup angles, no automatic re-review, and prompts that name the brief lines to
  read instead of the whole brief.
- **Batch independent agents in one turn**; never run the same search yourself
  while an agent does it.
- **Scope the first turn:** task, intent, constraints, files. A precise first
  message beats several clarifying rounds.
- **Effort:** when to raise it is in `CLAUDE.md` → "What to invoke, when". If
  reasoning looks shallow, raise effort rather than prompting around it.

**Keep the cached prefix stable.** Prompt caching reuses the unchanged start of
the context (system prompt, CLAUDE.md, tool list) across turns.

- `CLAUDE.md` is loaded in full into every session. Keep it short: the repo map,
  principles, the skill table, the git rules. Everything else lives in a skill,
  loaded only when invoked.
- Edits to `CLAUDE.md` apply from the next session, so batch them rather than
  editing it mid-task.
- Connecting or disconnecting MCP servers mid-session can change the tool list and
  invalidate the cache. Settle them before starting.

**Memory and fresh sessions.**

- Durable, non-obvious facts go to project memory: a decision's reason, a vendor
  limit found the hard way, a user preference. Not what the repo or git history
  already records.
- Don't re-read a file you just edited to "verify"; the harness tracks it.
  Don't re-derive a convention; invoke the skill that states it.
- When the session has grown long, at a breakpoint (usually right after a merge),
  hand the maintainer a kickoff prompt for a fresh session instead of continuing
  or compacting.
  "Current status" in `CLAUDE.md` must be enough to resume from.

## Runtime

**Model calls: pay once.**

- Every call goes through the `llm/` cache (docgap-correctness §2). Re-runs,
  CI and readers replay from `fixtures/llm_cache/` with zero calls.
- Batch by stage, not by item across stages: all attributions, then all drafts,
  then all gates. Resume after a failure re-uses every cached answer.
- Keep prompts small and stable: the versioned system prompt first, then the
  per-item state, so provider-side prompt caching can reuse the prefix across items.
- A per-run call and spend budget in config (docgap-resilience).

**Warehouse: query once, then freeze.**

- Snapshot, gold results and profiles are exported once, hashed and committed as
  fixtures. Everything after runs offline.
- Profile every mart column in one pass during the baseline session, instead of
  returning to the warehouse per stage.
- XS warehouses, 60 s auto-suspend, one per workload so cost is attributable per
  role. Snowflake's result cache makes repeated identical agent queries free; keep
  it on.
- `dbt build --select state:modified+` against the frozen baseline manifest when
  only a few models change.

**Don't build a cache framework.** A content-addressed file per entry, a dict, or
`functools.cache` on a pure function covers everything here. Keys are explicit and
stable (no clock, no random salt). Reach for more only when a profile says so.
