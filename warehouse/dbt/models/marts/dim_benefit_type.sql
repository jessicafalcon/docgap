-- One row per benefit type the dictionary lists, plus each code the fact holds that
-- the 2024 dictionary lacks, with no label, so a join to this table drops no spend.
-- `relationships` from the fact can't fail on those codes, so a test caps the share
-- of fact rows that carry one.
select
    cast(PRS_NAT as integer) as PRS_NAT,
    cast(PRS_NAT_LIB as varchar) as PRS_NAT_LIB
from {{ ref('prs_nat') }}

union all

select distinct
    cast(fact.PRS_NAT as integer) as PRS_NAT,
    cast(null as varchar) as PRS_NAT_LIB
from {{ ref('fct_reimbursements') }} as fact
where not exists (
    select 1 from {{ ref('prs_nat') }} as listed where listed.PRS_NAT = fact.PRS_NAT
)
