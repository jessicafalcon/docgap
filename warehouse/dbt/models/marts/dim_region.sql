select
    cast(BEN_RES_REG as integer) as BEN_RES_REG,
    cast(BEN_RES_REG_LIB as varchar) as BEN_RES_REG_LIB
from {{ ref('ben_res_reg') }}
