-- The codes the 2024 dictionary lacks carry 9,732 of the 107,232,480 rows in the
-- three full months, under 0.01%. Over 0.1%, the dictionary no longer describes the
-- data well enough for benefit-type questions, and this returns the share.
select unlabeled_share
from (
    select avg(case when dim.PRS_NAT_LIB is null then 1.0 else 0.0 end) as unlabeled_share
    from {{ ref('fct_reimbursements') }} as fact
    inner join {{ ref('dim_benefit_type') }} as dim on dim.PRS_NAT = fact.PRS_NAT
) as shares
where unlabeled_share > 0.001
