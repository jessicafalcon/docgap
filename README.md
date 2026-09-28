# docgap

docgap tells a data platform team which undocumented warehouse columns to
document first. It ranks them by how often they are queried, split by AI agents
and people, and by how often they lead an agent to a wrong answer. Then it
drafts the missing descriptions and opens one pull request for the column
owners to review.

**Status: in progress, no results yet.** The foundations and the evaluation
protocol are on `main`, and the deterministic core runs offline on hand-made
fixtures. No ranking has been evaluated. This README gains a results block,
generated from run files, when the experiment has run.

## The claim, and how it will be tested

On questions docgap never saw, documenting its top N undocumented columns
improves the agent's accuracy more than documenting N random undocumented
columns.

The test is pre-registered in [`docs/EVAL_PROTOCOL.md`](docs/EVAL_PROTOCOL.md):

- **Questions:** 40 questions with gold SQL over
  [Open DAMIR](https://www.data.gouv.fr/datasets/open-damir-base-complete-sur-les-depenses-dassurance-maladie-interregimes),
  French health-insurance spending data whose columns are coded (`PRS_PAI_MNT`,
  `BEN_CMU_TOP`). A seed splits them into discovery (25) and holdout (15).
- **Ranking inputs:** only discovery questions feed the ranking, and a test
  enforces it.
- **Arms:** a baseline with half the columns documented; the baseline plus
  drafts for docgap's top N columns; and the baseline plus drafts, from the same
  drafter and gate, for N random undocumented columns.
- **Headline:** holdout accuracy, top-N minus random-N, with a paired bootstrap
  interval. It counts as an improvement only if the interval lies above zero.
- **Reporting:** the result is published whatever it is, a null result included.

The protocol is frozen at the git tag `preregistered`, created before any
baseline run and witnessed by a GitHub release on it
([how](docs/EVAL_PROTOCOL.md#witnessing-the-tag)).

## Try it

It needs [uv](https://docs.astral.sh/uv/) 0.12.5 exactly, which
`pyproject.toml` pins. The analysis runs offline on the hand-made fixtures:

```sh
uv sync
uv run docgap analyze --offline \
  --history fixtures/query_history/basic.jsonl \
  --manifest fixtures/manifest/minimal.json \
  --scope fixtures/ranking_scope/basic.json \
  --as-of 2026-09-21T00:00:00Z
```

It writes `runs/<run_id>/`: `report.md` with both coverage numbers and the
ranked gaps, `rank/ranked_gaps.parquet`, and `run_manifest.json`. Run it again
and every stage is skipped, since its record still holds. The tests run offline
too; opening a network socket fails:

```sh
uv run pytest
```

## How it works

A deterministic pipeline, with the model calls at the edges:

1. **Snapshot.** Export query history for a fixed window, replace every value
   with a placeholder, fingerprint each query, and freeze the result as Parquet.
   Raw query text never reaches disk
   (ADR [0016](docs/adr/0016-redact-values-keep-structure.md)).
2. **Resolve.** Parse each query with sqlglot against the schema from the dbt
   manifest, to fully qualified column references. Queries that can't be
   resolved are counted, never guessed.
3. **Attribute.** In the evaluation, grade each agent answer against the gold
   result with no model involved. For each discovery failure of the ranked run,
   ask a typed question: why did it fail, and which column?
4. **Rank.** Measure coverage two ways, by columns and by executions, then
   score every undocumented column by usage and attributed failure rate. Ties
   break by fully qualified column name, so a re-run never reorders.
5. **Draft and propose.** Draft a description from aggregate-only evidence,
   score its support, sort drafts into confidence bands, and open a pull request
   against the dbt YAML.

The same inputs, by hash, produce byte-identical outputs: the core never reads
the clock or randomness, and every model call is cached by model, prompt version
and input, so a run replays offline.

## Stack

Python 3.12 (uv, typer, pydantic, pyarrow, sqlglot), dbt on Snowflake with DuckDB for
offline work, Terraform for roles and grants, Airflow for the weekly run, and
the Anthropic API for the agent, drafter and judgments.

## Repo map

| Path | Holds |
| --- | --- |
| `src/docgap/` | The tool: contracts, config, and the stages built so far |
| `eval/` | Evaluation material; `eval/reference/` holds the official dictionary, which `src/` never reads |
| `loader/` | Source checksums and the offline sample cut |
| `fixtures/` | The Open DAMIR fixture and query-history fixtures for offline tests |
| `docs/` | The evaluation protocol and the decision records in `docs/adr/` |
| `PROJECT-BRIEF.md` | The plan: phases, steps, risks and open decisions |

## Limits known now

- The query traffic is the evaluation's own agent traffic, not organic usage,
  and there is no human traffic unless it is generated.
- The holdout has 15 questions, so a top-N minus random-N difference under about
  20 points is more likely missed than detected (ADR
  [0008](docs/adr/0008-holdout-size-and-detectable-effect.md)).
- Open DAMIR is already anonymized, so its sensitivity tags demonstrate the
  mechanism rather than protect real personal data.

## License

The code is under the [MIT licence](LICENSE). Two data files are not: both are
published by the Caisse nationale de l'Assurance Maladie under the
[Licence Ouverte](https://www.etalab.gouv.fr/licence-ouverte-open-licence).

- **The Open DAMIR fixture** in `fixtures/damir/`: source and last update in
  [`fixtures/damir/README.md`](fixtures/damir/README.md).
- **The variable dictionary** in `eval/reference/`, from the same
  [dataset page](https://www.data.gouv.fr/datasets/open-damir-base-complete-sur-les-depenses-dassurance-maladie-interregimes),
  served with `Last-Modified: 11 Feb 2025`.
