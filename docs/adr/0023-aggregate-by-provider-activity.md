# 0023. Group the monthly aggregate by provider activity and sum the pre-filtered measures

- **Status:** Accepted
- **Date:** 2026-10-01
- **Superseded by:** none

## Context and Problem Statement

`agg_monthly_spend_by_category` groups spend by processing month and a
"category", and Phase 3's questions ask for pharmacy, dental and optical spend.
Every mart column must have a dictionary entry, or no draft for it can be graded
(ADR 0012). The dictionary has no grouping of `PRS_NAT`: 1,570 codes, with
optical spread over the 35xx range among oxygen equipment, hearing aids and
orthoses. `PSE_ACT_SNDS`, the executing provider's activity, isolates pharmacies
(50) and dental surgeons (19); opticians fall under 60, suppliers, along with
every other device supplier. Across the three full months all 13 codes found
are in the dictionary's list, and each code's share holds steady from month to
month (measured on 2026-10-01). The dictionary also says
`PRS_PAI_MNT` counts every reimbursement type, so summed without
`PRS_REM_TYP = 0` it double counts: by 21% on the offline sample. The `FLT_`
measures are already filtered. Which column is the category, and which measures
does the aggregate sum?

## Considered Options

1. **`PSE_ACT_SNDS`, summing `FLT_PAI_MNT` and `FLT_REM_MNT`.** Every column has
   a dictionary entry; optical can't be a category.
2. **A project-made `PRS_NAT` → category seed** (optical, dental, pharmacy,
   other). It reads naturally and covers optical, but the category column has no
   dictionary entry, and the seed would hold more than the dictionary's
   code→label pairs.
3. **`PRS_NAT` itself.** Gradable, but a category question becomes a filter over
   labels whose code set the agent must guess, and the grader compares to the cent.
4. **`PSE_ACT_CAT`.** Gradable, but it files pharmacies with transport and device
   suppliers.

## Decision Outcome

Chosen option: **1**, because it is the only option that keeps every mart column
gradable and gives pharmacy and dental one code each. The aggregate's columns are
`FLX_ANN_MOI`, `PSE_ACT_SNDS`, `FLT_PAI_MNT` and `FLT_REM_MNT`, summed under the
names of the dictionary variables they sum. The provider dimension is built over
`PSE_ACT_SNDS`, replacing the specialty dimension, so the agent can read the
category's labels in the warehouse.

### Consequences

- Good, because the aggregate can't double count, and the trap stays in the
  fact for the agent to meet, where documentation can help.
- Good, because a code a future file adds fails the aggregate's `relationships`
  test to the provider dimension.
- Bad, because optical leaves Phase 3's categories: an optical question names one
  benefit-type label exactly, so its gold set is never a hand-picked list of codes.
- Bad, because code 0, "not recorded", covers physicians and hospitals and holds
  about 40% of spend, so a "by provider type" question has to name or exclude it.
- Bad, because physician specialty (`PSE_SPE_SNDS`) has no labels in the
  warehouse; a question that needs them would add the dimension back, before the tag.
