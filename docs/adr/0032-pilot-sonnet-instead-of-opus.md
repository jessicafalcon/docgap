# 0032. Pilot Sonnet 5.5 instead of Opus 5.5 as the larger agent candidate

- **Status:** Accepted
- **Date:** 2026-10-03
- **Superseded by:** none

## Context and Problem Statement

The pilot compares a small model and a larger one, each with no docs and with
every column documented, and the protocol's rule picks the agent model from the
four accuracies (ADR 0024). The larger candidate was Opus 5.5, at $4 / $20 per
million input / output tokens; Sonnet 5.5 costs $2 / $10 (Claude API reference
bundled with the `claude-api` skill, model table cached 2026-09-25, checked
2026-10-03). The chosen model then runs about 600 more agent runs: the baseline
and the Phase 6 arms. No pilot pass has run, so the candidates can still change
without spending a rerun (ADR 0031). Which model is the larger candidate?

## Considered Options

1. **Keep Opus 5.5.**
2. **Sonnet 5.5** in its place.
3. **Three candidates:** Haiku 4.5, Sonnet 5.5 and Opus 5.5, 72 more runs a pass.

## Decision Outcome

Chosen option: **option 2**, because it halves the larger candidate's price per
token, in the pilot and in every later run if it is chosen, and the pilot still
compares a small model with a larger one. Option 3 costs a third model's runs in
every pass for a choice the protocol's rule doesn't need.

ADR 0027's request stands field for field: Sonnet 5.5 takes the same request as
Opus 5.5 did. Its defaults, from the same reference:

| | Sonnet 5.5 |
| --- | --- |
| Thinking when `thinking` is omitted | always on, adaptive; `disabled` is a 400 |
| `output_config.effort` | accepted; default `high` |
| Sampling parameters | a non-default value is a 400 |
| Forced `tool_choice` (`any`, `tool`) | a 400 |
| Thinking blocks in a reply | returned, text empty by default; passed back unchanged, and checked against an unedited history |
| Prompt caching (ADR 0030) | reads at 0.1× the input rate; prefixes under 512 tokens aren't cached |

The account's limits for both candidates, read from the response headers on
2026-10-03, are 10,000 requests, 10M input tokens and 2M output tokens a minute.
The runner makes one call at a time, far below them.

### Consequences

- Good, because a pass's estimate falls from about $30 to about $17, and the
  agent's later runs from about $200 to about $100 if Sonnet 5.5 is chosen.
- Good, because nothing counted moves: no pass has made a run, and `[pilot]`
  enters the pass's input hashes.
- Bad, because `[agent] max_tokens` = 16,000 was sized for Opus 5.5 thinking at
  `medium`, and Sonnet 5.5 thinks at `high`. A reply that reaches the cap ends the
  run as `no_final_answer`, counted with answers given in prose, and would lower
  Sonnet 5.5's accuracy for a reason docs don't touch. The smoke run's report
  counts the runs whose detail says `stop_reason max_tokens`; any one of them
  settles the cap or streaming before the pass continues, since a changed `[agent]`
  makes the runner refuse to resume.
- Bad, because the reasoning gap between the candidates widens: Haiku 4.5 doesn't
  think, Sonnet 5.5 thinks at `high`. ADR 0027's caveat applies with more force,
  and the pilot's decision record states it beside the four accuracies.
- Bad, because half the price per token isn't half the cost per run: thinking at
  `high` makes more output tokens. The smoke run measures the cost per run, and
  `[llm] max_spend_usd` is set from it.
- Bad, because Sonnet 5.5's safety classifiers can decline a request, and a
  refusal ends the run as `no_final_answer`. The API's model fallback is
  deliberately not sent: it would finish the run on another model, and the pilot
  credits each run to one.
