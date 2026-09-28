# Hand-made dbt manifest

`minimal.json` is written by hand in the shape of a dbt 1.10 `manifest.json`
(schema v12, DuckDB adapter): three mart models with enforced contracts
(`fct_reimbursements`, `dim_prestation`, `dim_region`), a staging model without
one, a seed and a test. It carries only the fields `docgap.manifest` reads, plus a
few it ignores. The columns match the queries in `fixtures/query_history/basic.jsonl`
and the hand-checked queries in `tests/test_resolve.py`.

A manifest built by the real dbt project replaces it in Phase 2's "Freeze the
manifest" step.
