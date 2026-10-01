-- Spend by processing month and by the executing provider's activity, the category
-- (ADR 0023). The FLT_ measures are already filtered on the reimbursement type, so
-- they sum over every line; PRS_PAI_MNT would double count. Each sum keeps the name
-- of the dictionary variable it sums, so a draft for it can be graded (ADR 0012).
select
    cast(FLX_ANN_MOI as integer) as FLX_ANN_MOI,
    cast(PSE_ACT_SNDS as integer) as PSE_ACT_SNDS,
    cast(sum(FLT_PAI_MNT) as decimal(18, 2)) as FLT_PAI_MNT,
    cast(sum(FLT_REM_MNT) as decimal(18, 2)) as FLT_REM_MNT
from {{ ref('fct_reimbursements') }}
group by FLX_ANN_MOI, PSE_ACT_SNDS
