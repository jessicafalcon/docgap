# 0036. Write half the 40 questions on a measure with no trap, since drafts don't carry the `FLT_` filter

- **Status:** Accepted
- **Date:** 2026-10-07
- **Superseded by:** none

## Context and Problem Statement

ADR 0034 left an open risk for the 40 questions. With no docs, Haiku 4.5 scored
0% in pilot pass 2, nearly every run summing a `PRS_` measure unfiltered, so
whether the baseline's seeded half documents the measure columns might set the
baseline, and top-N might take the whole effect by documenting them. Measured on
pass 2's runs by `fixtures/probe/measure_regrade.py`, which renames each final
query's `PRS_` measure to its `FLT_` twin (and `PRS_ACT_COG` to `FLT_PAI_MNT`) and
grades it again: Haiku 4.5's no-docs runs go from 0 to 10 of 36, Sonnet 5.5's from
2 to 34. For the agent model the measure is necessary, not the whole gap.

The arms carry drafts, not the dictionary's text. The drafter reads an evidence
packet: the column's name and type, the lineage SQL, and a profile of that one
column. A fact `FLT_` column's lineage is a cast, and nothing in a packet shows
that a `PRS_` sum double counts. Usage points the same way: in pass 2, Haiku 4.5's
no-docs queries touched `PRS_REM_MNT` in 17 runs and `PRS_ACT_COG` in 11, but
`FLT_PAI_MNT` in 1 and `FLT_ACT_QTE` in none, and a column with no usage scores 0,
so top-N documents the twin the agent misused.

A probe tested it on 2026-10-07 (`fixtures/probe/`, $3.48): Opus 5.5 drafted all
67 mart columns from a stand-in packet, with no dictionary and no gate, and Haiku
4.5 answered the 12 pilot questions 3 times each, reading pass 2's no-docs
manifest plus every draft. No baseline or random-arm draw was computed.

| Column docs, Haiku 4.5 | Passed | Passed with the gold's measure |
| --- | --- | --- |
| None (pass 2) | 0/36 | 10/36 |
| Every column drafted from its packet | 6/36 | 23/36 |
| Every column from the dictionary (pass 2) | 30/36 | 31/36 |

26 of the probe's 30 failures summed another measure than the question names. The
fact `FLT_` drafts say only that each is a variant of its `PRS_` twin; the
aggregate's two `FLT_` drafts do state the filter, read from the model's SQL
comment. How are the 40 built so the arms can differ?

## Considered Options

1. **Every question on a `FLT_` measure, spread** over statutory share, amount
   charged, quantity of acts and overbilling, so no one measure sets the baseline.
2. **Half the questions on a measure with no trap**, one whose unfiltered sum the
   docs support; the other half as in option 1.
3. **Stratify the baseline lock**, documenting one twin of each measure pair.
4. **Give the arms the dictionary's text** for the columns each arm picks.

## Decision Outcome

Chosen option: **option 2**, because a measure trap the baseline leaves closed
stays closed in top-N and random-N alike, so under option 1 every question whose
measure twins both fall outside the baseline's half, about one measure in four,
adds nothing to the difference. The other half keeps the schema's largest no-docs
failure in the test, and the results say how the drafts fared on it. Option 3
changes a rule set before any question existed, with the pilot seen. Option 4
tests the ranking with text docgap doesn't write.

The 40, as `PROJECT-BRIEF.md`'s "Write 40 questions" step states them:

- **20 with no measure trap:** "the statutory and supplementary shares together"
  (`PRS_REM_MNT` unfiltered) or "the reimbursement base, every share included"
  (`PRS_REM_BSE`, which has no `FLT_` twin). Each text rules out the filtered
  readings, which the catalog in `eval/questions.py` gains.
- **20 on a `FLT_` measure,** at most 6 on any one.
- **Level 1, as the pilot settled.** About 10 column-choice traps (processing or
  care month, each region column, executing or prescribing provider), each the
  main trap of at least 4 questions: one in 4 questions is missing from the 15
  holdout questions 13.8% of the time, one in 3 23.3%.
- **At most 3 result columns,** named in the text, and no needed column whose
  docs give no codes (`ASU_NAT`, `BEN_SEX_COD`, `MDT_TYP_COD`).
- **IDs Q01 to Q40 in writing order.** Nobody computes the split or the baseline
  docs draw before the questions merge.
- **The baseline lock and the arms don't change** (ADR 0021); the ceiling stays on
  the cut list.
- **Reported, descriptive only:** each arm's accuracy and the mean difference on
  each half. The headline stays on all 15 holdout questions.

### Consequences

- Good, because whatever the baseline draw, half the questions have no trap that
  no arm can open, and the probe shows drafts carry the region, month and
  provider choices those questions turn on: 10 → 23 of 36 with the measure fixed.
- Good, because both arms draw on the same drafter, so the split favours neither.
- Bad, because the design was chosen after the probe on the pilot's questions; the
  results report it as part of the pre-registered design, with this record.
- Bad, because the probe ran a stand-in prompt with no gate, on the sample's
  profiles, 36 runs: an upper bound on what drafts do, not a measure of Phase 5.
- Bad, because packets that keep SQL comments carry what their author knew, as the
  aggregate's did; Phase 5 decides whether they keep them.
- Bad, because the 40 take 5 to 8 evenings, not 2 to 4.
