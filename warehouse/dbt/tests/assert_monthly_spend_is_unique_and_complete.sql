-- One row per processing month and provider activity, summing to the fact's totals:
-- a month or activity the aggregate drops, or counts twice, returns its month here.
with fact as (
    select FLX_ANN_MOI, sum(FLT_PAI_MNT) as FLT_PAI_MNT, sum(FLT_REM_MNT) as FLT_REM_MNT
    from {{ ref('fct_reimbursements') }}
    group by FLX_ANN_MOI
),

agg as (
    select
        FLX_ANN_MOI,
        sum(FLT_PAI_MNT) as FLT_PAI_MNT,
        sum(FLT_REM_MNT) as FLT_REM_MNT,
        count(*) as row_count,
        count(distinct PSE_ACT_SNDS) as activities
    from {{ ref('agg_monthly_spend_by_category') }}
    group by FLX_ANN_MOI
)

select coalesce(fact.FLX_ANN_MOI, agg.FLX_ANN_MOI) as FLX_ANN_MOI
from fact
full outer join agg on agg.FLX_ANN_MOI = fact.FLX_ANN_MOI
where agg.FLX_ANN_MOI is null
    or fact.FLX_ANN_MOI is null
    or agg.row_count <> agg.activities
    or agg.FLT_PAI_MNT <> fact.FLT_PAI_MNT
    or agg.FLT_REM_MNT <> fact.FLT_REM_MNT
