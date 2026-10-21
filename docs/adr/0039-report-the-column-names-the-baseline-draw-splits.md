# 0039. Report the column names the baseline draw splits across tables, and keep the draw per FQN

- **Status:** Accepted
- **Date:** 2026-10-21
- **Superseded by:** none

## Context and Problem Statement

ADR 0021 draws the baseline's documented half per column FQN. 7 of the 59 mart
column names sit in two or three tables: a dimension's code or the aggregate's
grouping and measures repeat the fact's. The draw, computed once the questions
merged (#29), documents 5 of them in one table and not another: `FLX_ANN_MOI`
and `FLT_PAI_MNT` in the aggregate, `BEN_RES_REG` and `PRS_NAT` in their
dimensions, and `PSE_ACT_SNDS` in the fact and the aggregate, each undocumented
elsewhere. Each text is the column name's every-column text (ADR 0025), so an
agent that describes the documented table reads the text of a column U and the
ranking count as undocumented. The fact's `FLX_ANN_MOI` alone is read by 30 of
the 40 gold queries. A draft for such a column adds less than for one with no
text in reach, and top-N, drawn from touched columns, is likelier to pick one
than random-N. Is the rule changed, or the split reported?

## Considered Options

1. **Report the split**, keeping ADR 0021's draw as it is.
2. **Draw per column name**, a name documented in every table or none.
3. **Leave the shared names out of the draw**, documenting them in every table
   or none.

## Decision Outcome

Chosen option: **option 1**, because options 2 and 3 change a rule fixed before
any question existed, now that the draw and the questions are both known: the
choice ADR 0021 exists to remove. The results report each undocumented column
whose name a baseline-documented column shares, and which of them top-N and
random-N picked (`EVAL_PROTOCOL.md`, "Reported regardless of outcome").

### Consequences

- Good, because the baseline follows the rule as recorded, with no choice made
  after the draw was seen.
- Bad, because the measured gain of top-N over random-N can be smaller than a
  draw by name would give, when top-N picks a split column. The results show how
  many it picked.
