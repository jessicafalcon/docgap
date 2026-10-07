# 0036. Write half the 40 questions on a measure with no trap, since drafts don't carry the fact's `FLT_` filter

- **Status:** Accepted
- **Date:** 2026-10-07
- **Superseded by:** none

## Context and Problem Statement

ADR 0034 left an open risk: with no docs, Haiku 4.5 scored 0% in pilot pass 2,
nearly every run summing a `PRS_` measure unfiltered, so whether the baseline's
seeded half documents the measures might decide the arms. The arms carry drafts,
not the dictionary's text, and a fact `FLT_` column's evidence packet (its name,
its lineage, a cast, and a profile of that one column) can't show that a `PRS_`
sum double counts. In pass 2's committed transcripts, Haiku 4.5's no-docs queries
touched `PRS_REM_MNT` in 17 runs and `FLT_ACT_QTE` in none, and a column with no
usage scores 0, so top-N documents the twin the agent misused.

A probe on 2026-10-07 (`fixtures/probe/`, $3.48) had Opus 5.5 draft all 67 mart
columns from a stand-in packet, with no dictionary and no gate, then ran Haiku 4.5
on the 12 pilot questions 3 times each with every draft as its docs. No split or
draw was computed. `measure_regrade.py` renames each final query's `PRS_` measure
to its `FLT_` twin and grades it again; it leaves `PRS_REM_BSE` as written, so its
counts are lower bounds:

| Column docs, Haiku 4.5 | Passed | Passed, `PRS_` measure renamed |
| --- | --- | --- |
| None (pass 2) | 0/36 | 10/36 |
| Every column drafted | 6/36 | 23/36 |
| Every column from the dictionary (pass 2) | 30/36 | 31/36 |

26 of the probe's 30 failures summed another measure than the question names. The
fact `FLT_` drafts call each a variant of its `PRS_` twin; the aggregate's `FLT_`
drafts state the filter and the provider dimension's draft its codes, both read
from their models' SQL comments. How are the 40 built so the arms can differ?

## Considered Options

1. **Every question on a `FLT_` measure, spread** over four, so no one measure
   sets the baseline.
2. **Half the questions on a measure with no trap**, one whose unfiltered sum the
   docs support; the other half as in option 1.
3. **Stratify the baseline lock**, documenting one twin of each measure pair.
4. **Give the arms the dictionary's text** for the columns each arm picks.

## Decision Outcome

Chosen option: **option 2**, because a question whose measure twins are both
undocumented in the baseline fails in top-N and random-N alike, about one measure
in four, so under option 1 those questions add nothing to the difference. Option 3
changes a rule set before any question existed, with the pilot seen; option 4
tests the ranking with text docgap doesn't write. The 40, as the brief's "Write 40
questions" step states them in full:

- **20 with no measure trap:** the statutory and supplementary shares together
  (`PRS_REM_MNT` unfiltered) or the reimbursement base with every share
  (`PRS_REM_BSE`, which has no `FLT_` twin), the text ruling out the filtered
  readings. **20 on a `FLT_` measure,** at most 6 on any one.
- **Level 1.** About 10 column-choice traps, each the main trap of at least 4
  questions: a trap that is the main one of 4 questions is missing from the 15
  holdout questions with probability 13.8%, of 3 questions 23.3%.
- **IDs Q01 to Q40 in writing order,** and nobody computes the split or the
  baseline docs draw before the questions merge.
- **Reported, descriptive only:** each arm's accuracy and mean difference per half.
  The baseline lock, the arms and the headline don't change.

### Consequences

- Good, because half the questions turn on choices the probe's drafts carried:
  10 → 23 of 36 with the measure renamed.
- Good, because both arms draw on the same drafter, so the mix favours neither.
- Bad, because the mix was chosen after the probe on the pilot's questions; the
  results report it with this record.
- Bad, because with the IDs fixed, the split is `sha256("1:Q01")` onward, so the
  writer could know which questions are holdout before writing them; only the rule
  not to compute it stops that, as ADR 0021 accepted for the baseline draw.
- Bad, because the probe ran a stand-in prompt with no gate on the sample's
  profiles: an upper bound on what drafts do, unpinned by any test.
- Bad, because a packet that keeps SQL comments carries what their author knew;
  Phase 5 decides whether packets keep them, before the tag.
- Bad, because the 40 take 5 to 8 evenings, not 2 to 4.
