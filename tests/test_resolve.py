"""The resolve stage: hand-checked queries, session context, and every reference counted.

The ten queries below are hand-made over `fixtures/manifest/minimal.json`, and each
expected reference was written by hand from the SQL before the code ran on it.
"""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

import pytest
from conftest import MANIFEST, MARTS, MARTS_CONFIG, SETUP, assert_golden, snapshot_records
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlglot.errors import SqlglotError

import docgap.resolve as resolve_module
from docgap.artifacts import read_rows, rows_sha256
from docgap.manifest import Marts
from docgap.models import Clause, ColumnRef, QueryRecord
from docgap.resolve import COLUMN_REFS_FILE, Reference, resolve, resolve_query, run_resolve
from docgap.snapshot import normalize

F = "ANALYTICS.MARTS.FCT_REIMBURSEMENTS."
P = "ANALYTICS.MARTS.DIM_PRESTATION."
R = "ANALYTICS.MARTS.DIM_REGION."
S, W, J, G, H, X = (
    Clause.SELECT,
    Clause.WHERE,
    Clause.JOIN,
    Clause.GROUP_BY,
    Clause.HAVING,
    Clause.OTHER,
)
Refs = set[tuple[str, Clause]]
Others = dict[Reference, int]


def _resolve(
    sql: str, database: str | None = "ANALYTICS", schema: str | None = "MARTS", marts: Marts = MARTS
) -> tuple[Refs, Others, bool]:
    refs, star = resolve_query(normalize(sql), database=database, schema=schema, marts=marts)
    resolved = {(name, clause) for kind, name, clause in refs if kind is Reference.RESOLVED}
    others = Counter(kind for kind, _, _ in refs if kind is not Reference.RESOLVED)
    return resolved, dict(others), star


# Hand-checked queries

HAND_CHECKED: list[tuple[str, str, Refs, Others, bool]] = [
    (
        "aggregate_filter_ordinals",
        "select flx_ann_moi, sum(prs_pai_mnt) from fct_reimbursements"
        " where ben_sex_cod = 2 group by 1 order by 2 desc",
        {
            (F + "FLX_ANN_MOI", S),
            (F + "FLX_ANN_MOI", G),
            (F + "PRS_PAI_MNT", S),
            (F + "BEN_SEX_COD", W),
        },
        {},
        False,
    ),
    (
        "join_aliases_having",
        "select p.prs_nat_lib, sum(f.prs_pai_mnt) from analytics.marts.fct_reimbursements as f"
        " join dim_prestation p on p.prs_nat = f.prs_nat"
        " group by p.prs_nat_lib having sum(f.prs_act_qte) > 100",
        {
            (P + "PRS_NAT_LIB", S),
            (P + "PRS_NAT_LIB", G),
            (P + "PRS_NAT", J),
            (F + "PRS_PAI_MNT", S),
            (F + "PRS_NAT", J),
            (F + "PRS_ACT_QTE", H),
        },
        {},
        False,
    ),
    (
        # The CTE's `*` counts only what the outer query reads from it.
        "cte_star_pushed_down",
        "with optical as (select * from fct_reimbursements where prs_nat = 3125)"
        " select ben_res_reg, sum(prs_pai_mnt) from optical group by ben_res_reg",
        {(F + "PRS_NAT", W), (F + "BEN_RES_REG", S), (F + "PRS_PAI_MNT", S)},
        {},
        False,
    ),
    (
        "top_level_star",
        "select * from dim_region",
        {(R + "BEN_RES_REG", S), (R + "REG_LIB", S)},
        {},
        True,
    ),
    (
        "in_subquery",
        "select sum(prs_pai_mnt) from fct_reimbursements where prs_nat in"
        " (select prs_nat from dim_prestation where prs_nat_lib like '%OPTIQUE%')",
        {(F + "PRS_PAI_MNT", S), (F + "PRS_NAT", W), (P + "PRS_NAT", S), (P + "PRS_NAT_LIB", W)},
        {},
        False,
    ),
    (
        # The outer column in the subquery is counted once, in the outer query.
        "correlated_exists",
        "select r.reg_lib from dim_region r where exists (select 1 from fct_reimbursements f"
        " where f.ben_res_reg = r.ben_res_reg and f.age_ben_snds = 4)",
        {
            (R + "REG_LIB", S),
            (R + "BEN_RES_REG", W),
            (F + "BEN_RES_REG", W),
            (F + "AGE_BEN_SNDS", W),
        },
        {},
        False,
    ),
    (
        # The union's ORDER BY names its output, not a column.
        "union_order_by",
        "select prs_nat from fct_reimbursements union all select prs_nat from dim_prestation"
        " order by prs_nat",
        {(F + "PRS_NAT", S), (P + "PRS_NAT", S)},
        {},
        False,
    ),
    (
        "information_schema_describe",
        "select column_name, comment from information_schema.columns"
        " where table_name = 'FCT_REIMBURSEMENTS'",
        set(),
        {Reference.INFORMATION_SCHEMA: 3},
        False,
    ),
    (
        "unmanaged_join_and_typo",
        "select f.prs_pai_mnt, s.prs_nat from fct_reimbursements f"
        " join analytics.staging.stg_damir__prestations s on s.prs_nat = f.prs_nat"
        " where f.prs_nat_typo = 1",
        {(F + "PRS_PAI_MNT", S), (F + "PRS_NAT", J)},
        {Reference.UNMANAGED: 2, Reference.UNRESOLVED: 1},
        False,
    ),
    (
        # `PRS_NAT` is in both tables: without a qualifier Snowflake rejects it.
        "ambiguous_name_and_qualify",
        "select prs_nat, flx_ann_moi from fct_reimbursements f join dim_prestation p"
        " on p.prs_nat = f.prs_nat"
        " qualify row_number() over (partition by prs_nat_lib order by flx_ann_moi desc) = 1",
        {
            (F + "FLX_ANN_MOI", S),
            (F + "FLX_ANN_MOI", X),
            (P + "PRS_NAT_LIB", X),
            (P + "PRS_NAT", J),
            (F + "PRS_NAT", J),
        },
        {Reference.UNRESOLVED: 1},
        False,
    ),
]


@pytest.mark.parametrize(
    ("sql", "resolved", "others", "star"),
    [pytest.param(*case[1:], id=case[0]) for case in HAND_CHECKED],
)
def test_hand_checked_query(sql: str, resolved: Refs, others: Others, star: bool) -> None:
    assert _resolve(sql) == (resolved, others, star)


# Session context


def test_unqualified_table_takes_the_session_context() -> None:
    sql = "select prs_nat from fct_reimbursements"
    assert _resolve(sql)[0] == {(F + "PRS_NAT", S)}
    # The same name in another schema is another relation, outside the marts.
    assert _resolve(sql, schema="STAGING")[:2] == (set(), {Reference.UNMANAGED: 1})
    # With no session context, the table can't be qualified.
    assert _resolve(sql, database=None, schema=None)[:2] == (set(), {Reference.UNRESOLVED: 1})


@pytest.mark.parametrize(
    ("sql", "others"),
    [
        ("select a, b from other_db.x.t", {Reference.UNMANAGED: 2}),
        ("select * from other_db.x.t", {Reference.UNMANAGED: 1}),
        (
            "select t.*, s.prs_nat from other_db.x.t t, analytics.staging.stg s",
            {Reference.UNMANAGED: 2},
        ),
        ('select "prs_nat" from fct_reimbursements', {Reference.UNRESOLVED: 1}),
        ("select x.prs_nat from fct_reimbursements", {Reference.UNRESOLVED: 1}),
        (
            "with c as (select prs_nat from fct_reimbursements) select c.typo from c",
            {Reference.UNRESOLVED: 1},
        ),
        ("select a from fct_reimbursements, other_db.x.t", {Reference.UNRESOLVED: 1}),
        # A name sqlglot can't place is counted in the scope that holds it, however nested.
        (
            "with c as (select prs_nat, typo from fct_reimbursements) select prs_nat, typo from c",
            {Reference.UNRESOLVED: 1},
        ),
        (
            "select typo from fct_reimbursements union all select prs_nat from dim_prestation",
            {Reference.UNRESOLVED: 1},
        ),
        (
            "select a from (select typo as a from fct_reimbursements) as d",
            {Reference.UNRESOLVED: 1},
        ),
        ("with s as (select a from other_db.x.t) select a from s", {Reference.UNMANAGED: 1}),
        (
            "select prs_nat from fct_reimbursements where prs_nat in (select a from other_db.x.t)",
            {Reference.UNMANAGED: 1},
        ),
        # A CTE named like a mart table hides it.
        (
            "with fct_reimbursements as (select prs_nat from analytics.staging.stg)"
            " select prs_nat from fct_reimbursements",
            {Reference.UNMANAGED: 1},
        ),
    ],
)
def test_references_outside_the_marts_are_counted_not_kept(sql: str, others: Others) -> None:
    resolved, counted, _ = _resolve(sql)
    assert counted == others
    assert all(name.startswith("ANALYTICS.MARTS.") for name, _ in resolved)


def test_order_by_counts_a_column_the_query_does_not_select() -> None:
    # An ordinal or a selected column in ORDER BY becomes an alias reference, so the
    # column counts under SELECT only (ADR 0018).
    sql = "select prs_nat from fct_reimbursements order by flx_ann_moi, 1"
    assert _resolve(sql)[0] == {(F + "PRS_NAT", S), (F + "FLX_ANN_MOI", Clause.ORDER_BY)}


# The stage


def test_resolve_matches_golden(
    snapshot_parquet: Path, tmp_path: Path, update_golden: bool
) -> None:
    stage = run_resolve(
        snapshot_parquet,
        MANIFEST,
        config=MARTS_CONFIG,
        setup_sha256=SETUP,
        out_dir=tmp_path / "resolve",
    )
    rows = read_rows(tmp_path / "resolve" / COLUMN_REFS_FILE, ColumnRef)
    assert_golden("resolve", "column_refs", rows, stage, update_golden)
    assert stage.outputs == {"column_refs": rows_sha256(rows)}
    # The snapshot's input hash is the hash the snapshot stage recorded as its output.
    assert stage.inputs["query_snapshot"] == rows_sha256(snapshot_records())


def test_every_reference_is_kept_or_counted() -> None:
    records = snapshot_records()
    rows, counts = resolve(records, MARTS)
    assert counts["columns.resolved"] == len(rows)
    assert counts["queries.read"] == len(records)
    assert counts["columns.unresolved"] > 0


def test_a_query_sqlglot_cannot_qualify_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: object, **__: object) -> None:
        raise SqlglotError("cannot qualify")

    monkeypatch.setattr(resolve_module, "qualify", fail)
    records = snapshot_records()
    rows, counts = resolve(records, MARTS)
    assert rows == []
    assert counts["queries.qualify_failed"] == counts["queries.read"] == len(records)


@settings(max_examples=25, deadline=None)
@given(st.randoms(use_true_random=False))
def test_record_order_does_not_change_the_refs(rng: random.Random) -> None:
    records: list[QueryRecord] = snapshot_records()
    shuffled = records.copy()
    rng.shuffle(shuffled)
    assert resolve(shuffled, MARTS) == resolve(records, MARTS)
