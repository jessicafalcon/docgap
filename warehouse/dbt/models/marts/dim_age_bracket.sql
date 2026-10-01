select
    cast(AGE_BEN_SNDS as integer) as AGE_BEN_SNDS,
    cast(AGE_BEN_SNDS_LIB as varchar) as AGE_BEN_SNDS_LIB
from {{ ref('age_ben_snds') }}
