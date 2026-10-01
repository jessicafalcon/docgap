-- One row per processing month and provider activity, each summing to the fact's
-- totals for that pair: a pair the aggregate drops, counts twice or misattributes
-- returns here.
with fact as (
    select
        FLX_ANN_MOI,
        PSE_ACT_SNDS,
        sum(FLT_PAI_MNT) as FLT_PAI_MNT,
        sum(FLT_REM_MNT) as FLT_REM_MNT
    from {{ ref('fct_reimbursements') }}
    group by FLX_ANN_MOI, PSE_ACT_SNDS
),

agg as (
    select
        FLX_ANN_MOI,
        PSE_ACT_SNDS,
        sum(FLT_PAI_MNT) as FLT_PAI_MNT,
        sum(FLT_REM_MNT) as FLT_REM_MNT,
        count(*) as row_count
    from {{ ref('agg_monthly_spend_by_category') }}
    group by FLX_ANN_MOI, PSE_ACT_SNDS
)

select
    coalesce(fact.FLX_ANN_MOI, agg.FLX_ANN_MOI) as FLX_ANN_MOI,
    coalesce(fact.PSE_ACT_SNDS, agg.PSE_ACT_SNDS) as PSE_ACT_SNDS
from fact
full outer join agg
    on agg.FLX_ANN_MOI = fact.FLX_ANN_MOI and agg.PSE_ACT_SNDS = fact.PSE_ACT_SNDS
where agg.FLX_ANN_MOI is null
    or fact.FLX_ANN_MOI is null
    or agg.row_count > 1
    or agg.FLT_PAI_MNT <> fact.FLT_PAI_MNT
    or agg.FLT_REM_MNT <> fact.FLT_REM_MNT
