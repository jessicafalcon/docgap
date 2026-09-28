# docgap report

As of 2026-09-21T00:00:00Z.

## Coverage

| Coverage | Documented | Total | Share |
| --- | ---: | ---: | ---: |
| Mart columns | 2 | 12 | 16.7% |
| Executions | 4 | 6 | 66.7% |

An execution is one counted query touching one mart column, so a query that
reads five columns counts five times.

## Gates

| Gate | Value | Threshold | Passed |
| --- | ---: | ---: | ---: |
| snapshot.parse_rate | 0.916667 | 0.9 | True |
| snapshot.rows_kept | 9 | 1 | True |

## Counts

| Stage | Count | Value |
| --- | --- | ---: |
| snapshot | agent_run_ids | 2 |
| snapshot | dropped.malformed_tag | 1 |
| snapshot | dropped.multiple_statements | 1 |
| snapshot | dropped.not_a_query | 1 |
| snapshot | dropped.outside_window | 2 |
| snapshot | dropped.parse_error | 1 |
| snapshot | dropped.unmapped_role | 1 |
| snapshot | dropped.unstable_normalization | 0 |
| snapshot | kept | 9 |
| snapshot | read | 16 |
| resolve | columns.information_schema | 0 |
| resolve | columns.resolved | 20 |
| resolve | columns.unmanaged | 0 |
| resolve | columns.unresolved | 8 |
| resolve | queries.qualify_failed | 0 |
| resolve | queries.read | 9 |
| resolve | queries.top_level_star | 1 |
| usage | columns | 4 |
| usage | queries.in_scope | 4 |
| usage | queries.out_of_scope.other_question | 2 |
| usage | queries.out_of_scope.other_run | 2 |
| usage | queries.out_of_scope.untagged | 1 |
| usage | queries.read | 9 |
| usage | scope.qids_without_queries | 0 |
| coverage | columns.documented | 2 |
| coverage | columns.total | 12 |
| coverage | executions.documented | 4 |
| coverage | executions.total | 6 |
| rank | columns.ranked | 10 |
| rank | columns.ranked_untouched | 8 |

## Ranked gaps

Every mart column with no description, by score, then by column FQN. A column
no counted query touched scores 0.

| Rank | Column | Score | Executions | Failure rate |
| --- | --- | ---: | ---: | ---: |
| 1 | `ANALYTICS.MARTS.FCT_REIMBURSEMENTS.BEN_SEX_COD` | 0.6931 | 1 | 0.00 |
| 2 | `ANALYTICS.MARTS.FCT_REIMBURSEMENTS.PRS_NAT` | 0.6931 | 1 | 0.00 |
| 3 | `ANALYTICS.MARTS.DIM_PRESTATION.PRS_NAT` | 0.0000 | 0 | 0.00 |
| 4 | `ANALYTICS.MARTS.DIM_PRESTATION.PRS_NAT_LIB` | 0.0000 | 0 | 0.00 |
| 5 | `ANALYTICS.MARTS.DIM_REGION.BEN_RES_REG` | 0.0000 | 0 | 0.00 |
| 6 | `ANALYTICS.MARTS.DIM_REGION.REG_LIB` | 0.0000 | 0 | 0.00 |
| 7 | `ANALYTICS.MARTS.FCT_REIMBURSEMENTS.AGE_BEN_SNDS` | 0.0000 | 0 | 0.00 |
| 8 | `ANALYTICS.MARTS.FCT_REIMBURSEMENTS.BEN_RES_REG` | 0.0000 | 0 | 0.00 |
| 9 | `ANALYTICS.MARTS.FCT_REIMBURSEMENTS.PRS_ACT_QTE` | 0.0000 | 0 | 0.00 |
| 10 | `ANALYTICS.MARTS.FCT_REIMBURSEMENTS.SOI_ANN` | 0.0000 | 0 | 0.00 |
