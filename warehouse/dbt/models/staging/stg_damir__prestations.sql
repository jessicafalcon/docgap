-- One row per source line. No two lines of the three pinned months share a grain key
-- (loader/profile_sources.py, 2026-09-29), so there is nothing to aggregate, and the
-- key's `unique` test fails on a future file that repeats one. RAW already declares
-- every type (loader/raw_prestations.sql): staging trims the text codes and adds the key.

-- The grain: every field but the measures, 1 to 16 and 30 to 56, the fields
-- `grain_key` in loader/offline_sample.py cuts the sample by;
-- tests/warehouse/test_staging.py checks the two lists match.
{%- set grain = [
    'FLX_ANN_MOI', 'ORG_CLE_REG', 'AGE_BEN_SNDS', 'BEN_RES_REG', 'BEN_CMU_TOP',
    'BEN_QLT_COD', 'BEN_SEX_COD', 'DDP_SPE_COD', 'ETE_CAT_SNDS', 'ETE_REG_COD',
    'ETE_TYP_SNDS', 'ETP_REG_COD', 'ETP_CAT_SNDS', 'MDT_TYP_COD', 'MFT_COD',
    'PRS_FJH_TYP', 'SOI_ANN', 'SOI_MOI', 'ASU_NAT', 'ATT_NAT', 'CPL_COD',
    'CPT_ENV_TYP', 'DRG_AFF_NAT', 'ETE_IND_TAA', 'EXO_MTF', 'MTM_NAT', 'PRS_NAT',
    'PRS_PPU_SEC', 'PRS_REM_TAU', 'PRS_REM_TYP', 'PRS_PDS_QCP', 'EXE_INS_REG',
    'PSE_ACT_SNDS', 'PSE_ACT_CAT', 'PSE_SPE_SNDS', 'PSE_STJ_SNDS', 'PRE_INS_REG',
    'PSP_ACT_SNDS', 'PSP_ACT_CAT', 'PSP_SPE_SNDS', 'PSP_STJ_SNDS', 'TOP_PS5_TRG',
    'ETB_DCS_MCO'
] %}

with source as (

    select
        FLX_ANN_MOI,
        ORG_CLE_REG,
        AGE_BEN_SNDS,
        BEN_RES_REG,
        BEN_CMU_TOP,
        BEN_QLT_COD,
        BEN_SEX_COD,
        DDP_SPE_COD,
        ETE_CAT_SNDS,
        ETE_REG_COD,
        ETE_TYP_SNDS,
        ETP_REG_COD,
        ETP_CAT_SNDS,
        MDT_TYP_COD,
        MFT_COD,
        PRS_FJH_TYP,
        PRS_ACT_COG,
        PRS_ACT_NBR,
        PRS_ACT_QTE,
        PRS_DEP_MNT,
        PRS_PAI_MNT,
        PRS_REM_BSE,
        PRS_REM_MNT,
        FLT_ACT_COG,
        FLT_ACT_NBR,
        FLT_ACT_QTE,
        FLT_PAI_MNT,
        FLT_DEP_MNT,
        FLT_REM_MNT,
        trim(SOI_ANN) as SOI_ANN,
        trim(SOI_MOI) as SOI_MOI,
        ASU_NAT,
        ATT_NAT,
        CPL_COD,
        CPT_ENV_TYP,
        DRG_AFF_NAT,
        ETE_IND_TAA,
        EXO_MTF,
        MTM_NAT,
        PRS_NAT,
        PRS_PPU_SEC,
        PRS_REM_TAU,
        PRS_REM_TYP,
        PRS_PDS_QCP,
        EXE_INS_REG,
        PSE_ACT_SNDS,
        PSE_ACT_CAT,
        PSE_SPE_SNDS,
        PSE_STJ_SNDS,
        PRE_INS_REG,
        PSP_ACT_SNDS,
        PSP_ACT_CAT,
        PSP_SPE_SNDS,
        PSP_STJ_SNDS,
        TOP_PS5_TRG,
        trim(ETB_DCS_MCO) as ETB_DCS_MCO
    from {{ source('damir', 'PRESTATIONS') }}

)

select
    -- `concat_ws` skips a NULL on DuckDB but returns NULL on Snowflake, which would null
    -- the whole key there, so each field is coalesced. No grain field is nullable in RAW.
    md5(concat_ws('|',
        {%- for column in grain %}
        coalesce(cast({{ column }} as varchar), ''){{ ',' if not loop.last }}
        {%- endfor %}
    )) as PRESTATION_KEY,
    *
from source
