-- RAW.DAMIR.PRESTATIONS: the Open DAMIR monthly files as delivered, one declared type
-- per field and nothing inferred. Snowflake SQL; loader/load_duckdb.py transpiles it
-- for the offline warehouse, so both build the same table.
--
-- Types were measured on the three pinned files, 107,232,480 rows (2026-09-29):
-- - Codes are integers of at most 6 digits, except SOI_ANN and SOI_MOI, which keep
--   their zero padding (`0000`, `01`) as codes (ADR 0012), and ETB_DCS_MCO (letters).
-- - Amounts, coefficients and the rate have at most 2 decimals and 8 integer digits,
--   some written without a leading zero (`.61`). A third decimal would be rounded
--   silently, so NUMBER(18, 2) leaves room without hiding one.
-- - Only PRS_ACT_NBR and FLT_ACT_NBR are ever empty (about 10% of rows); every other
--   field is NOT NULL, so a future file with a gap fails its load.
-- - FILLER takes the empty field after each line's trailing `;`; staging asserts it
--   is NULL.
-- Keep the column order in sync with the files' header: the loader checks it.
CREATE TABLE IF NOT EXISTS RAW.DAMIR.PRESTATIONS (
    FLX_ANN_MOI INTEGER NOT NULL,
    ORG_CLE_REG INTEGER NOT NULL,
    AGE_BEN_SNDS INTEGER NOT NULL,
    BEN_RES_REG INTEGER NOT NULL,
    BEN_CMU_TOP INTEGER NOT NULL,
    BEN_QLT_COD INTEGER NOT NULL,
    BEN_SEX_COD INTEGER NOT NULL,
    DDP_SPE_COD INTEGER NOT NULL,
    ETE_CAT_SNDS INTEGER NOT NULL,
    ETE_REG_COD INTEGER NOT NULL,
    ETE_TYP_SNDS INTEGER NOT NULL,
    ETP_REG_COD INTEGER NOT NULL,
    ETP_CAT_SNDS INTEGER NOT NULL,
    MDT_TYP_COD INTEGER NOT NULL,
    MFT_COD INTEGER NOT NULL,
    PRS_FJH_TYP INTEGER NOT NULL,
    PRS_ACT_COG NUMBER(18, 2) NOT NULL,
    PRS_ACT_NBR INTEGER,
    PRS_ACT_QTE INTEGER NOT NULL,
    PRS_DEP_MNT NUMBER(18, 2) NOT NULL,
    PRS_PAI_MNT NUMBER(18, 2) NOT NULL,
    PRS_REM_BSE NUMBER(18, 2) NOT NULL,
    PRS_REM_MNT NUMBER(18, 2) NOT NULL,
    FLT_ACT_COG NUMBER(18, 2) NOT NULL,
    FLT_ACT_NBR INTEGER,
    FLT_ACT_QTE INTEGER NOT NULL,
    FLT_PAI_MNT NUMBER(18, 2) NOT NULL,
    FLT_DEP_MNT NUMBER(18, 2) NOT NULL,
    FLT_REM_MNT NUMBER(18, 2) NOT NULL,
    SOI_ANN VARCHAR(4) NOT NULL,
    SOI_MOI VARCHAR(2) NOT NULL,
    ASU_NAT INTEGER NOT NULL,
    ATT_NAT INTEGER NOT NULL,
    CPL_COD INTEGER NOT NULL,
    CPT_ENV_TYP INTEGER NOT NULL,
    DRG_AFF_NAT INTEGER NOT NULL,
    ETE_IND_TAA INTEGER NOT NULL,
    EXO_MTF INTEGER NOT NULL,
    MTM_NAT INTEGER NOT NULL,
    PRS_NAT INTEGER NOT NULL,
    PRS_PPU_SEC INTEGER NOT NULL,
    PRS_REM_TAU NUMBER(18, 2) NOT NULL,
    PRS_REM_TYP INTEGER NOT NULL,
    PRS_PDS_QCP INTEGER NOT NULL,
    EXE_INS_REG INTEGER NOT NULL,
    PSE_ACT_SNDS INTEGER NOT NULL,
    PSE_ACT_CAT INTEGER NOT NULL,
    PSE_SPE_SNDS INTEGER NOT NULL,
    PSE_STJ_SNDS INTEGER NOT NULL,
    PRE_INS_REG INTEGER NOT NULL,
    PSP_ACT_SNDS INTEGER NOT NULL,
    PSP_ACT_CAT INTEGER NOT NULL,
    PSP_SPE_SNDS INTEGER NOT NULL,
    PSP_STJ_SNDS INTEGER NOT NULL,
    TOP_PS5_TRG INTEGER NOT NULL,
    ETB_DCS_MCO VARCHAR(1) NOT NULL,
    FILLER VARCHAR
);
