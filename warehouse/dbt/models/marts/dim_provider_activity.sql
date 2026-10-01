-- The executing provider's activity, the category of `agg_monthly_spend_by_category`
-- (ADR 0023): 50 pharmacies, 19 dental surgeons.
select
    cast(PSE_ACT_SNDS as integer) as PSE_ACT_SNDS,
    cast(PSE_ACT_SNDS_LIB as varchar) as PSE_ACT_SNDS_LIB
from {{ ref('pse_act_snds') }}
