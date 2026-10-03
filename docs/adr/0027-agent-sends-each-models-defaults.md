# 0027. Send each candidate agent model its defaults, and take the final answer as a tool

- **Status:** Accepted
- **Date:** 2026-10-01
- **Superseded by:** none

## Context and Problem Statement

The protocol runs the test agent at default sampling, with no `temperature`, so
its 3 repetitions are draws and not replays. The two pilot candidates don't share
their defaults. Per the Claude API reference bundled with the `claude-api` skill
(model table cached 2026-09-25, checked 2026-10-01):

| | Haiku 4.5 | Opus 5.5 |
| --- | --- | --- |
| Thinking when `thinking` is omitted | none; on only with a token budget | always on, adaptive; disabling it is a 400 |
| `output_config.effort` | rejected | accepted; default `medium` |
| Sampling parameters | accepted | rejected with a 400 |
| Forced `tool_choice` (`any`, `tool`) | accepted | rejected with a 400 |
| Thinking blocks in a reply | none | returned, text empty by default; passed back unchanged, and checked against an unedited history |

The agent must end with a structured `{final_sql}`. What does each request send?

## Considered Options

1. **Defaults for both, the final answer as a fourth tool.** Send `model`,
   `max_tokens`, `system`, `tools` and `messages` only; `final_answer(final_sql)`
   is a tool the system prompt asks for, under the default `tool_choice` (`auto`).
2. **Equalize reasoning.** Give Haiku 4.5 a thinking budget, or set Opus 5.5's
   effort to `low`, so both think about as much.
3. **Force the final answer.** Send a forced `tool_choice` for `final_answer`
   after the 8th tool call.
4. **Structured outputs** (`output_config.format`) for the final answer.

## Decision Outcome

Chosen option: **option 1**, because it is what each model does unless told
otherwise, the comparison the pilot exists to make: is the gap docs open on this
schema large enough on either model? Option 2 picks a reasoning level the
protocol never set, and there is no neutral one. Option 3 is a 400 on Opus 5.5.
Option 4 makes every reply JSON, the turns that call tools included, which a
tool loop doesn't need.

The loop counts every tool call but `final_answer`, which is the answer: parallel
calls each count, and so does a call to an unknown tool or with malformed input,
which gets an error result. Calls past the 8th are refused
with an error result, the 8th call's results end with "That was your last tool
call. Call final_answer now.", and a reply after that without `final_answer` ends
the run as `error`, cause `no_final_answer`. So does a reply that stops for any
other reason (`end_turn`, `max_tokens`, `refusal`), except one cut at the context
window, cause `context_exceeded` (ADR 0029). `max_tokens` is 16,000 for
both, room for Opus 5.5's thinking and the size the SDK sends without streaming.
The loop is append-only: every reply goes back as returned, thinking blocks
included, and nothing earlier is edited, as Opus 5.5's thinking check requires.

### Consequences

- Good, because the request is the same for both candidates, field for field, and
  a test pins its keys: `max_tokens`, `messages`, `system`, `tools`.
- Good, because `[call_sites.agent]` records `sampling = {}` at the tag, true for
  either model.
- Bad, because the two candidates differ in reasoning as well as in model, and a
  larger gap on Opus 5.5 may come from its thinking. The pilot's decision record
  says so beside the four accuracies.
- Bad, because a model that answers in prose instead of calling `final_answer`
  fails, with no nudge. The pilot counts `no_final_answer` apart; if it is common,
  the prompt changes before the tag, with a new prompt version.
- Bad, because Opus 5.5's reasoning is billed but not readable in the
  transcripts: `thinking.display` stays at its default, `omitted`.
