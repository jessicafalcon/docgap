# 0033. Rerun the pilot on questions that name their measure, graded on every reading the docs support

- **Status:** Accepted
- **Date:** 2026-10-05
- **Superseded by:** none

## Context and Problem Statement

Pilot pass 1 graded Haiku 4.5 13.9% → 58.3% and Sonnet 5.5 8.3% → 41.7% (no docs →
full docs), so Haiku 4.5 was eligible. Its grades mostly record which spend measure
the agent summed. Every gold query sums a `FLT_` measure, every question says only
"amount reimbursed" or "amount charged", and the full-docs text says `PRS_REM_MNT`
"peut s'utiliser sans filtres": the statutory share plus the supplementary ones.
Regraded from the committed results, also accepting `PRS_REM_MNT` unfiltered for
`FLT_REM_MNT`, pass 1 gives Haiku 4.5 25.0% → 88.9% and Sonnet 5.5 75.0% → 100%.
`PRS_PAI_MNT` and `PRS_ACT_QTE` unfiltered aren't readings the docs support: they
double count, as the docs say ("doublés"), by 14% and 7% on the sample.

A second reading breaks the gold too, though no pass 1 run took it.
`PRS_REM_TYP` 99, "VALEUR INCONNUE", is on 863,066 of the sample's 2,141,851 rows,
and the `FLT_` measures are filled there, though the dictionary calls them
"préfiltré sur le type de remboursement (PRS_REM_TYP) 0". So the `PRS_` filters the
docs give return other totals than `FLT_`. Measured on `data/agent/sample/` on
2026-10-05 (the fixture agrees):

| Measure | `FLT_` | `PRS_` as the docs filter it | Gap, of `FLT_` |
| --- | --- | --- | --- |
| Amount charged | 845,819,733.50 | `PRS_PAI_MNT`, type 0: 798,168,484.50 | 5.6% |
| Statutory share | 789,705,557.05 | `PRS_REM_MNT`, types 0 and 1: 753,972,819.21 | 4.5% |
| Quantity of acts | 65,609,924 | `PRS_ACT_QTE`, type 0: 64,612,881 | 1.5% |

No business wording separates the two. The protocol reruns the pilot only when no
model is eligible. How do pass 2's questions, gold and grading change, and on what
grounds does it run?

## Considered Options

1. **Choose Haiku 4.5 from pass 1, regraded** with the reading the docs support:
   88.9% is in the band, and no rerun is spent.
2. **Rerun with questions that name their measure, graded on every reading the
   docs support,** as a disclosed deviation.
3. **The same, with a new rerun trigger** in the kill criterion: a pass whose
   questions admit two readings is rerun.

## Decision Outcome

Chosen option: **option 2**. Pass 1's gold rejected a reading the full docs
support, so its grades don't measure what the docs do, under the rule or under a
regrade chosen after seeing it. Option 1 picks the model on a grading changed once
the results were known, one run inside the 90% bound. Option 3 dresses a decision
taken after pass 1 as a rule written before it.

Pass 1 counts toward the three passes; pass 2 is rerun 1 of 2. Its setup:

- **Each question names its measure** in business words: "Health Insurance's
  statutory share" (`FLT_REM_MNT`), "amount charged" (`FLT_PAI_MNT`), "quantity of
  acts" (`FLT_ACT_QTE`), never a column name.
- **The gold accepts every reading the docs support.** A question declares each
  swap of the catalog in `eval/questions.py` that changes its result: accepted,
  its result stored beside the gold, or ruled out by a phrase its text holds. A
  run passes on the gold or on any accepted result. The `PRS_` filters above are
  accepted; `PRS_REM_MNT` unfiltered is ruled out by "statutory share".
  `resolve` reads the gold SQL alone.
- **Harder by one rule, level 1.** Beyond the measure, the catalog's column
  choices fall in three families the docs settle and the names don't: the month
  (processing or care), the region (the beneficiary's, the executing
  professional's, the prescriber's, the paying fund's) and the provider
  (executing or prescribing). At level *n*, each question's text rules out
  readings in at least *n* + 1 families, a test counts them. Pass 1's questions
  ruled out one at least, so they are level 0 with the measure named; pass 2 is
  level 1. Never a code value the docs don't hold, and no harder SQL. Its
  strength was chosen with pass 1's results seen.
- **A blind check before the pass:** an Opus 5.5 subagent, not a candidate, writes
  each question's SQL from the question, the marts and the full-docs text alone.
  Each mismatch with the accepted results is a question defect, fixed, or an
  agent error, left; the model's decision record lists both.

Pass 2's outcomes, fixed before it runs:

| Pass 2 | Then |
| --- | --- |
| A model is eligible | The protocol's rule picks it, Haiku 4.5 or Sonnet 5.5; no pass 3 |
| The larger-gap model above 90% with full docs | Pass 3 at level 2: all three families per question |
| The larger-gap model below 50% with full docs | Pass 3 at level 0: one family per question, the measure named |
| Its full docs in the band, gap under 15 points | Pass 3 at level 2 |
| A question defect found after the pass | Pass 3 with the defect fixed, same level |

After pass 3, no eligible model is the finding (ADR 0024).

### Consequences

- Good, because a run that follows the docs can't fail on the measure it chose,
  and an unnamed reading in the catalog fails `gold --check` before any spend.
- Good, because the response to every pass 2 outcome is set before it is seen.
- Bad, because the pilot reruns with an eligible model in hand, a deviation the
  results report.
- Bad, because the catalog holds the readings foreseen; the blind check covers
  others with one reader, not proof.
- Bad, because the model chosen may be Sonnet 5.5, about twice Haiku 4.5's cost
  per run over the ~600 later runs.
- Bad, because the 40 inherit the rule: a question whose measure has two readings
  stores both results.
