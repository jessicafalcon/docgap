# 0034. Choose Haiku 4.5 as the agent model, from pilot pass 2

- **Status:** Accepted
- **Date:** 2026-10-07
- **Superseded by:** none

## Context and Problem Statement

The protocol picks the agent model from the offline pilot: a model is eligible
when its full-docs accuracy is between 50% and 90% inclusive and its gap over no
docs is at least 15 points, and the eligible model with the larger gap is chosen.
Two passes ran, each 12 questions × 3 repetitions in four configurations on the
offline sample, committed under `fixtures/pilot/`:

| Pass | Graded on | Haiku 4.5, no → full docs | Sonnet 5.5, no → full docs | Calls, spend |
| --- | --- | --- | --- | --- |
| 1 | the gold, as run | 13.9% → 58.3% | 8.3% → 41.7% | 546, $5.76 |
| 1 | the readings the docs support (`--pass 1 --regrade`) | 25.0% → 88.9% | 75.0% → 100% | none |
| 2 | the gold and accepted readings | 0.0% → 83.3% | 5.6% → 100% | 598, $6.95 |
| 2 | the gold alone | 0.0% → 72.2% | 5.6% → 97.2% | none |

Pass 1's gold rejected a reading the full docs support, so pass 2 ran as a
disclosed deviation, with its outcomes fixed before it ran (ADR 0033). Pass 2
had no error, no harness error and no reply cut at `max_tokens`. Before it, a
blind check by an Opus 5.5 subagent, not a candidate, answered all 12 questions
with the gold or an accepted result; its one ambiguity, whether P02 counts the
unknown region, was fixed in the text and P02 passed again alone
(`eval/blind_check/pass-2.json`). Which model is the agent?

## Considered Options

1. **Haiku 4.5:** eligible in pass 2, gap 83.3 points.
2. **Sonnet 5.5:** not eligible, 100% with full docs.

## Decision Outcome

Chosen option: **Haiku 4.5**, by the protocol's rule on pass 2, the first
outcome in ADR 0033's table: no pass 3 runs. It stays eligible graded on the
gold alone (72.2%, gap 72.2), so the choice doesn't rest on the accepted
readings. `[call_sites.agent]` records it at the tag. Sonnet 5.5 is reported
under limits and runs as no arm.

How the accuracies are counted:

- **A run with a harness error** (a value the grader refuses, such as an
  interval) counts as failed in its configuration's accuracy and is reported
  apart. None occurred in either pass.
- **A model's two docs settings are paired.** The cache key holds the
  repetition and not the docs setting, so a question's no-docs and full-docs
  runs share replies until the first tool result that differs, usually the
  first `describe` (protocol "Runs" item 2). The gap compares paired runs, not
  independent samples.
- **The reasoning gap:** Haiku 4.5 doesn't think; Sonnet 5.5 thinks at its
  default `high` effort (ADR 0027, ADR 0032). Part of Sonnet 5.5's 100% may be
  that reasoning rather than the docs.

The full-docs text copies the dictionary verbatim (ADR 0025), with three defects:

- `PSP_SPE_SNDS`, the prescriber's specialty, is labelled as the executing
  provider's.
- `PSE_ACT_SNDS`'s comment cites activities 53 and 54, which its code list lacks.
- The `FLT_` measures are called filtered on reimbursement type 0, but they are
  filled on type 99, unknown, too (ADR 0033).

### Consequences

- Good, because the chosen model has room both ways: 0% without docs and 83.3%
  with every column documented.
- Good, because Haiku 4.5 is the cheaper candidate per token, for the ~600 later
  runs (ADR 0032).
- Bad, because the brief expects the chosen model's no-docs accuracy far from 0%,
  and it is 0%. Nearly every no-docs run in both models failed on one choice:
  summing a `PRS_` measure unfiltered. So most of the gap is the docs of one
  column family. The baseline documents a seeded half of the mart columns
  (ADR 0021): whether the measure columns fall in that half may set the
  baseline near 0% or near the ceiling, and a top-N arm that documents them may
  take the whole effect. This is an open risk for the 40 questions, weighed
  before they are written (the brief's "Write 40 questions" step).
- Bad, because the pilot ran twice, the second time as a deviation with an
  eligible model in hand; the results report both passes.
