# 0009. Run the baseline with the arms, and fix what picks arm content at the tag

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

Arms are compared only under one setup hash (ADR 0007). The baseline runs in
Phase 3, before later steps add docgap code and config, so its setup hash can't
equal the Phase 6 arms'. The same Phase 3 run reports holdout pass rates per
question, weeks before the arms are built, while the rank weight *w*, the gate
bands and the drafter and gate prompts could still change. And the model cache
keys the agent on `{model, prompt_version, state, questions}`: if the state
holds only the transcript, repetitions 2 and 3 replay repetition 1. How is the
baseline made comparable, and what must be fixed before anyone sees a holdout
result?

## Considered Options

For the baseline:

1. **Re-run the baseline in the Phase 6 session**, under the arms' setup hash.
2. **Compare the Phase 3 baseline on the agent's settings only**, a narrower
   parity check than the setup hash.

For what can still move after the holdout results are visible:

3. **Fix *w*, the bands, *k* and the judgment call sites at the tag.**
4. **Seal the Phase 3 holdout grades** until the arms are graded.

## Decision Outcome

Chosen options: **1 and 3**, because both keep one rule for every comparison
(the setup hash) and one moment for every fixed value (the tag). A second,
narrower parity check would be a second rule to keep in sync with the first,
and sealing grades on the author's own machine can't be checked by anyone
else, while values committed at a public tag can.

The agent's cache key holds the repetition number and not the arm. Repetitions
are then independent draws. Arms share a cached response only while their
transcripts are identical, which a valid draw allows, and the Phase 6 baseline
replays the Phase 3 one wherever the warehouse returns the same results.

### Consequences

- Good, because the arms' headline and the baseline comparisons all rest on the
  one parity check the tests already require.
- Good, because the Phase 6 baseline costs little model spend where it replays,
  and any difference from the Phase 3 baseline shows drift.
- Bad, because the drafter and gate prompts are fixed before they meet the real
  snapshot's drafts, built and tested only on the pilot's outputs and the
  offline sample. A prompt that drafts badly on live data stays as it is and is
  reported.
- Bad, because the Phase 6 session runs 120 more agent runs, 360–480 in all.
  Where the baseline replays from the cache they cost warehouse time, not model
  spend.
