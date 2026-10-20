# 0038. Keep the aggregate out of the gold, and count a trap only when it changes the result

- **Status:** Accepted
- **Date:** 2026-10-20
- **Superseded by:** none

## Context and Problem Statement

ADR 0036 writes 20 of the 40 on a measure with no trap, most on `PRS_REM_MNT`
unfiltered, "the statutory and supplementary shares together". But
`agg_monthly_spend_by_category` sums `FLT_REM_MNT` and `FLT_PAI_MNT` alone, by
processing month and executing provider activity (ADR 0023). A no-trap question
that sets only those two can be answered from the aggregate, so an agent that
reads it sums the filtered share in every arm, the trap the half was written to
remove. The catalog's swaps rewrite only the fact's columns, so a gold query on
the aggregate would leave its readings unchecked. ADR 0036 also names a main trap
per question, at least 4 per trap, so each trap is likely in the holdout. A
label counts toward that only if its swap changes the gold result. Today
`readings` accepts a ruled-out swap that ties the gold, and so does the
substring check on the phrase that rules it out. Which questions may the 40
hold, and when does a trap count?

## Considered Options

1. **Leave it to the writer:** the rules stay a writing habit, unchecked.
2. **Drop the aggregate from the agent's database.**
3. **Check three rules on the questions:**
   - no gold query reads the aggregate;
   - a no-trap question on `PRS_REM_MNT` needs a fact column the aggregate lacks;
   - a question's main trap changes its gold result on the sample.

## Decision Outcome

Chosen option: **option 3**, because each rule is one check on the gold SQL or
its readings, and the aggregate stays in the marts, as the baseline lock and the
ranking expect. Option 2 changes the marts after the pilot chose its model on
them.

A no-trap question on `PRS_REM_MNT` therefore needs a region, the age bracket, the
care month or the prescribing provider. A `PRS_REM_BSE` one is free of the
aggregate already. `eval/questions.py` checks the rules, added with the 40, and
the protocol states them under Questions item 6.

### Consequences

- Good, because no question in the no-trap half carries the filtered share
  through the aggregate.
- Good, because the trap counts in ADR 0036's coverage bound are traps that
  bite on the sample.
- Bad, because a trap that bites on the sample is checked again on the full
  months only as a swap the question declares (ADR 0037), not as a main trap.
- Bad, because a no-trap question by processing month and executing activity
  alone, the aggregate's grain, can't be asked on `PRS_REM_MNT`.
- Bad, because the aggregate, built for category-spend questions (ADR 0023),
  serves no gold query, though an agent may still read it.
- Bad, because the rules were set after the pilot and the probe: the results
  report them with this record.
