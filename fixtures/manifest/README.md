# Hand-made dbt manifest

`minimal.json` is written by hand in the shape of a dbt 1.10 `manifest.json`
(schema v12, DuckDB adapter): three mart models with enforced contracts
(`fct_reimbursements`, `dim_prestation`, `dim_region`), a staging model without
one, a seed and a test. It carries only the fields `docgap.manifest` reads, plus a
few it ignores. The columns match the queries in `fixtures/query_history/basic.jsonl`
and the hand-checked queries in `tests/test_resolve.py`.

The unit tests keep this file. Phase 2's "Freeze the manifest" step adds a test
that reads the manifest the real dbt project builds.
