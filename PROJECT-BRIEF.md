# docgap: project plan

Sep 25, 2026 · @Jessica

## Objective and problem

**Objective:** docgap tells a data platform team which undocumented warehouse columns to document first, ranked by how often each column is queried (split by people and AI agents) and by how often it leads to wrong answers. It drafts the missing descriptions, sorts them by confidence, and opens one pull request for the column owner to review. The repo proves the claim on held-out questions: documenting docgap's top N columns is compared against documenting N random undocumented columns.

**The problem it solves:**

- **Agents answer from metadata.** An agent that writes SQL picks fields from table and column names and descriptions. When a column is coded (`PRS_PAI_MNT`, `BEN_CMU_TOP`) and undocumented, the agent has to guess.
- **Docs are never complete, and the headline coverage metric misleads.** "72% of columns documented" weighs a column nobody queries the same as one queried 500 times a week, so teams document by intuition or by whoever complains.
- **Wrong answers are never traced back to missing metadata.** When an agent gets a question wrong, nothing records *which* column it misread. The fix (a two-line YAML description) is cheap; knowing *where* to apply it is the expensive part.

**What success looks like:**

- One command produces a ranked list and a pull request from query history.
- The same inputs always produce the same ranking.
- On questions the tool never saw, documenting docgap's top N columns improves agent accuracy more than documenting N random undocumented columns. "More" is defined before any run: the paired bootstrap interval over holdout questions for (top-N minus random-N) lies above zero.
- The result is reported whatever it turns out to be, including a null result.

## How it solves it

The tool is a deterministic pipeline with three narrow model calls at the edges. Every step has one input, one output and one owner.

1. **Snapshot the evidence.** Export query history for a fixed window, filtered by role and query tag. Replace literals with placeholders, fingerprint each query, and freeze the result as a hashed Parquet file.
2. **Resolve columns.** Parse each query with sqlglot against the schema taken from the dbt manifest. Output: fully qualified column references. Queries that can't be resolved are counted and reported, never guessed.
3. **Grade and attribute** (evaluation set only). Compare the agent's result set to the gold query's result, with no model involved. Only failures go to a typed question: *why did it fail, and which column?*
4. **Rank.** Score = usage × failure weight × doc status, with the weights in config. Ties are broken by column name, so the order never changes between runs.
5. **Draft, gate, propose.** Build an evidence packet per column (name, type, lineage SQL, aggregate profile). Draft a description, score its support with a yes/no confidence question, sort drafts into bands, write a YAML patch, and open a pull request.

### Design principles

**Determinism**

- The core is pure: the same manifest, snapshot and config (by hash) produce byte-identical outputs, checked by golden-file tests.
- The core never reads the clock or randomness. The "as of" date and the random seed are inputs.
- Every model call uses a pinned model ID, a versioned prompt, recorded sampling settings and structured output. It is cached by `sha256(model, prompt_version, input)`, so any run replays offline.
- Determinism comes from the cache, not from temperature 0. Every call runs at the model's default sampling: Opus 5.5 rejects a non-default `temperature` with a 400 error, and the adapter sends none. The sampling settings actually sent are logged per call site.

**Data governance**

- Read-only by construction: the tool's Snowflake role can't write, and changes reach the warehouse only through a reviewed pull request and dbt.
- Least privilege, defined in Terraform: one role per job (load, transform, agent, audit). `ACCOUNTADMIN` appears only in the one-time `bootstrap.sql`, for the few objects only it can create.
- Minimal exposure to the model: query literals are stripped before storage, and profiles are aggregates only. Values carried by fewer than *k* fact rows are suppressed, numeric min and max are clipped to the *k*-th value, and columns tagged sensitive get no sample values at all.
- An audit trail per run: input hashes, the environment that ran (Python, runtime dependencies, docgap's code), git SHA, model versions, prompt hashes, thresholds and output hash in `run_manifest.json`.

**Coherence**

- One source of truth for metadata. dbt YAML is persisted to Snowflake comments (`persist_docs`), and the agent reads those comments. What docgap proposes is exactly what the agent will read.
- One identifier everywhere: `DATABASE.SCHEMA.TABLE.COLUMN`, uppercased, as Snowflake stores it.
- Honest measurement:
  - The questions, gold SQL, N and comparison arms are committed before the baseline run.
  - The discovery/holdout split is fixed by seed.
  - Everything that feeds the ranking (usage and failure attribution) comes from discovery questions only, enforced by a test.
  - Accuracy is reported on both splits, with holdout top-N versus random-N as the headline.

## Stack

Five tools are core (Python, Snowflake, dbt, Terraform, Airflow), each with one job. DuckDB runs the offline development loop before the Snowflake trial starts. Metabase is an optional stretch; AWS/GCP is left out.

| Tool | Job in docgap | Why this choice |
| --- | --- | --- |
| Snowflake (trial, Enterprise edition) | Warehouse, source of query history, holds column comments the agent reads | Query history and column comments live next to the data. Enterprise unlocks ACCESS\_HISTORY as an optional cross-check |
| Terraform (`snowflakedb/snowflake` provider) | Databases, warehouses, roles, service users, grants, incl. the `SNOWFLAKE` database roles | Least privilege as reviewable code; `terraform plan` is the governance diff |
| dbt Core + dbt-snowflake | Models over Open DAMIR, YAML docs, `meta` (owner, sensitivity), tests, model contracts, `persist_docs` | One source of truth for metadata; the manifest is docgap's schema |
| DuckDB + dbt-duckdb | The same dbt project over a recorded sample, for building and piloting before the trial | Most of the work happens without the trial clock running; gold SQL is written for Snowflake and transpiled with sqlglot |
| Python 3.12 + uv | The docgap CLI (typer), typed models (pydantic), Parquet I/O (pyarrow/polars) | Locked dependencies = reproducible installs |
| sqlglot (dialect `snowflake`) | Parse and qualify queries, strip literals, fingerprint, extract column references | Deterministic, no warehouse round-trip, testable offline |
| ruamel.yaml | Write descriptions into dbt YAML without reordering or losing comments | Minimal, readable pull-request diffs |
| system-one-adapter on Anthropic | The two typed judgments: failure attribution (Choice) and draft support (Noul) | A drop-in for the `system_one` API in typesafe-sdk, backed by Claude. The typed questions and response types are the SDK's own, so moving to the hosted client changes only client setup |
| Anthropic API: Opus 5.5 (drafter, judgments); agent model chosen by the pilot | The description drafter, the two typed judgments, and the test agent that writes SQL | One provider keeps prompts and caching consistent. The low-volume calls whose quality is the product use the strongest model; the agent is the experiment's subject, so it's chosen for how clearly it shows the effect of docs |
| Airflow (local, Docker Compose) | One weekly DAG: snapshot, analyze, open pull request | A scheduler most data teams already run; the DAG only calls the CLI, so logic stays testable outside Airflow |
| GitHub Actions + `gh` | CI (lint, offline tests on fixtures, `dbt parse`, `terraform validate`) and opening pull requests | Where the team already reviews changes |
| pytest, ruff, pyright | Tests, lint, types | Standard; CI must be green before anything is published |

**Left out on purpose:**

- **AWS/GCP.** A Snowflake internal stage is enough to load open data. An S3 stage would add IAM setup without adding to the story.
- **Agent frameworks and vector stores.** The test agent is a short tool-calling loop with two tools, so every step stays inspectable.
- **Metabase (stretch only).** Its saved questions could be a second, human traffic source in a later phase.

## Architecture

The architecture is a closed loop: the tool's output is a change to the same YAML the agent later reads as Snowflake comments.

&#91;embedded content: docgap architecture · warehouse side on top, docgap side below\]

The top row is a normal dbt-on-Snowflake setup. The bottom row is docgap; it reads the dbt manifest for the schema and never writes to the warehouse.

**Deterministic core vs model edges.** Resolve, rank, redact, profile, patch and report are pure Python functions over files. The three model calls (test agent, attribution, draft + gate) sit at the edges behind one `llm/` module. That module owns pinning, prompts, caching and timeouts, so no other code talks to a model.

**Repo layout**

```text
docgap/
  infra/terraform/          # roles, grants, warehouses, service users
  warehouse/dbt/            # dbt project over Open DAMIR (models, YAML docs, tests)
  loader/                   # download + checksum + PUT/COPY into RAW
  eval/questions.yml        # pre-registered questions + gold SQL + split
  eval/agent/               # test agent (2 tools: list/describe, run_sql)
  src/docgap/
    snapshot.py             # export + redact + fingerprint query history
    resolve.py              # sqlglot -> column references
    grade.py                # result-set comparison
    rank.py                 # scoring, stable ordering
    evidence.py             # aggregate-only profiles, k-threshold
    llm/                    # adapter client, prompts, cache, timeouts
    patch.py                # ruamel.yaml edits -> PR
    report.py               # report.md + run_manifest.json
  fixtures/                 # frozen snapshot, manifest, cached responses
  orchestration/airflow/    # one DAG calling the CLI
  .github/workflows/        # CI
```

Every stage writes one artifact under `runs/<run_id>/`. The next stage reads only that artifact, so any stage can be re-run or tested alone.

## Governance model

Every actor gets one role and one service user, all defined in Terraform. Nothing in the project can write to the warehouse except the loader and dbt.

### Roles and access

| Role | Service user | Can read | Can write | Warehouse |
| --- | --- | --- | --- | --- |
| `LOADER` | `SVC_LOADER` | Stage in `RAW.DAMIR` | Tables in `RAW.DAMIR` | `WH_BUILD` (XS) |
| `TRANSFORMER` | `SVC_DBT` | `RAW` | Objects in `ANALYTICS` (owned by dbt) | `WH_BUILD` (XS) |
| `AGENT_READER` | `SVC_AGENT` | `ANALYTICS.MARTS` only (plus future grants) | Nothing | `WH_AGENT` (XS) |
| `DOCGAP_AUDITOR` | `SVC_DOCGAP` | Query history and column metadata (`SNOWFLAKE` database roles `GOVERNANCE_VIEWER` + `OBJECT_VIEWER`), `ANALYTICS.MARTS` for aggregate profiles | Nothing | `WH_AUDIT` (XS) |

- **Service users** are `TYPE = SERVICE` with key-pair authentication only: no passwords, no MFA prompts in pipelines. Keys live in `.env` locally and in GitHub Actions secrets, never in the repo; gitleaks runs in pre-commit and CI.
- **Terraform runs under its own user** holding `SYSADMIN` (objects) and `SECURITYADMIN` (roles and grants).
- **`ACCOUNTADMIN` appears only in the one-time bootstrap script**, documented in the README. It covers the objects only `ACCOUNTADMIN` can manage:
  - the resource monitor and its warehouse assignments
  - the `SNOWFLAKE` database role grants, if `SECURITYADMIN` turns out not to be able to make them
- **One warehouse per workload**, auto-suspend at 60 s. Cost and query history are attributable per actor with no extra tagging.
- **Every docgap query is tagged** (`query_tag = docgap:<run_id>:<stage>`) and excluded from traffic by role. Its own reads are auditable and never counted as usage.

### Metadata contract in dbt

Every mart column carries `meta.owner` and `meta.sensitivity` (`public` | `internal` | `restricted`). A column with no description is "missing"; with a docgap draft awaiting review, "drafted".

- `docgap lint` fails CI if a mart column has no `sensitivity` or a model has no `owner`. Governance is checked the same way as tests.
- Open DAMIR is already anonymized open data, so `restricted` here demonstrates the mechanism on demographic columns (age bracket, sex, region). The README says this plainly.

### What each model call can see

| Model call | Sees | Never sees |
| --- | --- | --- |
| Test agent | Table and column names and comments via `information_schema`; results of its own queries (capped at 200 rows) | `RAW`, `STAGING`, anything outside `MARTS` |
| Failure attribution | Question, agent SQL, gold SQL, the first 20 rows of both results, current docs of the columns involved | Row-level data beyond those samples |
| Drafter | Column name and type, table description, compiled lineage SQL, aggregate profile | Raw rows; sample values of `restricted` columns; any value carried by fewer than *k* = 11 fact rows |
| Draft gate | The draft and the same evidence packet | Anything the drafter didn't see |

**Redaction before storage.** Query text is normalized by sqlglot (literals become placeholders) before it touches disk, and raw text is never persisted. Snowflake's `query_parameterized_hash` serves as a cross-check on the fingerprints.

**Audit record per run.** `run_manifest.json` has a canonical part and an operational part (ADR 0006). The canonical part holds the as-of date, input hashes (snapshot, manifest, config per section), the environment (Python version, installed runtime dependencies, a hash of docgap's code), model IDs and prompt versions per call site, and per stage the output hashes, counts and gate values with their thresholds. The operational part holds run ID, git SHA, stage status and timings, cache hit rate and spend. The environment, config section hashes and call sites form the run's setup, whose hash arms are compared by and which each stage record carries. Two runs with equal inputs must have equal canonical parts; CI checks this, and the pull request cites the canonical hash.

## Phase 0: Foundations and pre-registration

Fix the rules before touching data, so no later result can be accused of being tuned. No Snowflake needed yet.

- [x] **Create the repo.** Repo `docgap`, private until the `preregistered` tag, then public, MIT license, `uv init`, Python pinned in `.python-version`, ruff + pyright + pytest config, pre-commit with ruff and gitleaks. *Expect:* `uv run pytest` passes, with one smoke test pinning that sockets and DNS are blocked; CI is green.
- [x] **Write the data contracts first.** Pydantic models in `src/docgap/models.py`: `ColumnRef`, `QueryRecord`, `ColumnUsage`, `Grade`, `Attribution`, `EvidencePacket`, `Draft`, `GateResult`, `RankedGap`, `RunManifest`. Each has one JSON Schema committed in `src/docgap/schemas/`, and a test fails when it drifts from the model. Parquet artifacts declare their dtypes from the same models when the first stage writes them. *Expect:* every later stage imports these types, so stage boundaries can't drift.
- [x] **Create one config file.** `docgap.toml`, loaded by `src/docgap/config.py`: history window, role-to-actor mapping (agent/human), *k* = 11, band thresholds, model IDs and sampling settings per call site, the agent tool's timeout and row cap, and the split, random-arm and baseline-docs seeds. Every key sits in a section and has no default in code; each later step adds the keys it reads, in a section named for what reads them (ADR 0007). *Expect:* one hash per section goes into the run's setup; no magic numbers in code.
- [x] **Start a decision log.** `docs/adr/`, one numbered record per choice, copied from `docs/adr/template.md`: status, context, options considered, the outcome and its consequences. A later record supersedes an earlier one; accepted records are never rewritten. The first records cover the Snowflake edition and trial timing, why sqlglot, why *k* = 11, repo visibility, and where guard hooks are registered; the months of data have theirs from "Study the data dictionary" (ADR 0011). *Expect:* reviewers see the reasoning, and later changes are explicit.
- [x] **Write the evaluation protocol.** `docs/EVAL_PROTOCOL.md` defines:
  - **What "correct" means**: the result-set match rules, including gold results of at most 200 rows and 5 columns.
  - **Repetitions**: 3 per question per arm. The repetition number is part of the agent's model-cache key, so each repetition is its own draw; the arm is not.
  - **The split**: 25 discovery / 15 holdout, by hashing question IDs with the split seed (ADR 0008).
  - **The comparison arms**, each run on all 40 questions under one setup hash, all in the Phase 6 session, the baseline included (ADR 0009):
    - **Baseline**: the locked 50% docs.
    - **Top-N**: baseline plus docgap's drafts for its top N columns.
    - **Random-N**: baseline plus drafts, from the same drafter and gate, for N columns drawn by the random-arm seed from every column undocumented in the baseline.
    - **Ceiling** (optional): every mart column documented from the dictionary.
  - **N**: N = min(10, floor(U / 2)), where U is the number of undocumented columns the discovery gold SQL touches, computed before the `preregistered` tag. A fixed N stops "top N" from quietly becoming "every column the questions touch".
  - **Ranking inputs come from discovery questions only.** Usage counts only queries tagged with a discovery `qid`, and failure attribution runs only on discovery failures. A test fails if a holdout `qid` reaches any ranking input.
  - **Drafts go into the arms unedited.** Ready and confirm-band drafts are used as-is and flagged items get none, so the experiment measures the tool rather than your edits. Owner review happens on the pull request and is reported as an edit rate. Delivered drafts per arm are reported next to the headline.
  - **What picks the arms' content is fixed at the tag**, the rank weight *w* = 1 among it, as listed in `CLAUDE.md` → "After `preregistered`". The Phase 3 baseline shows holdout failures before the arms run, so nothing it shows can tune them (ADR 0009).
  - **The headline**: holdout accuracy for top-N versus random-N, with a paired percentile bootstrap interval over questions (10,000 resamples drawn by hashing with seed 4). It counts as an improvement only if the interval lies above zero. No p-values. The protocol states what the design can detect: about a 28-point difference more than 80% of the time (ADR 0008).
  - **The agent model**: chosen by the pilot rule in Phase 3 and fixed at the tag, with the choice and the pilot numbers in its own decision record.
  - **A kill criterion**: if neither candidate agent model is eligible in the offline pilot (Phase 3), meaning full-docs accuracy between 50% and 90% and full docs beating no docs by at least 15 points, change the setup (harder questions, more coded columns) before tagging. If nothing fixes it, report that as the finding.
  - **What gets reported regardless of outcome**, including when random-N matches top-N.
  - **How the tag is witnessed**: the tag comes after the gold results are materialized, and a GitHub release on it is published when the repo goes public, before the baseline runs; its `published_at` is set by GitHub, not the committer.

  *Expect:* committed before any agent run.
- [x] **Study the data dictionary.** Download the Open DAMIR variable descriptor (an `.xlsx` workbook) and the monthly file list, and choose how many months to load. *Expect:* a decision entry with file names, sizes and SHA-256 checksums.

  ADR 0011 loads one month, `A202501.csv.gz`, and records what was measured on it, with the commands:
  - 973,594,858 bytes gzipped, 36,640,259 rows, 5.92 GB uncompressed; ASCII, LF, `.` decimals.
  - 56 named columns plus a trailing `;`, so 57 fields on every line.
  - One processing month, and care months back to 2001: 76% of rows are care in December 2024 or January 2025.
  - The descriptor has no entry for `ETB_DCS_MCO`, and six pre-2015 `*_ZEAT` variables the file lacks.
  - Download links carry a session token. No duplicate dimension keys in the first 2M rows.

- [ ] **Define the offline sample.** A deterministic slice of one month (for example, rows whose hashed surrogate key falls under a threshold, about 2M rows) becomes the DuckDB dataset and the source of CI fixtures. The rule and its output hash go in the decision log. *Expect:* the offline world is small, reproducible and derived by rule, like everything else.
- [x] **Keep the dictionary out of the tool's reach.** It is ground truth for grading drafts, stored in `eval/reference/` only; docgap never reads it. *Expect:* a CI check that `src/` never imports from `eval/reference/`: `tests/test_dictionary_isolation.py` fails on an `eval` import or on the path spelled anywhere under `src/`.

**Done when:** repo, contracts, config, protocol and decision log are on `main`, CI is green, the months of data are chosen, and the offline sample is defined.

## Phase 1: Snowflake foundations with Terraform

Every object and permission is declared in code, and a test proves each role can do exactly what the governance table says.

- [ ] **Sign up for the Snowflake trial only when the offline pilot passes.** The trigger is readiness, not the calendar: the dbt project builds on DuckDB, the grader and agent pass their tests, the Phase 5 code runs on pilot outputs, and the kill criterion is met. Pick Enterprise edition on AWS `eu-west-3` (Paris). Record the trial end date and credit allowance shown at signup in the decision log.

  The trial ends at 30 days or when the free balance (about $400) runs out, whichever comes first. After that the account is suspended: you can log in, but you can't run queries.

  *Expect:* the date that bounds every live step.
- [ ] **Bootstrap once, by hand.** `infra/bootstrap.sql` uses `ACCOUNTADMIN` to:
  - create a `TERRAFORM` service user with key-pair auth and grant it `SYSADMIN` + `SECURITYADMIN`
  - create the resource monitor (a credit quota that suspends warehouses at 90%) and assign the warehouses to it after Terraform creates them, since only `ACCOUNTADMIN` can do either

  In the first 10 minutes, test whether `SECURITYADMIN` can grant `SNOWFLAKE.GOVERNANCE_VIEWER`. The docs say only the owner of the `SNOWFLAKE` database can grant its database roles. If the test fails, move those grants into bootstrap too and log it.

  *Expect:* the only manual step in the project, documented line by line.
- [ ] **Configure the provider.** `snowflakedb/snowflake` with an exact version pin and key-pair auth from environment variables. State stays local and gitignored; the README notes that a team would use a remote backend. *Expect:* `terraform init` + `plan` run clean on an empty account.
- [ ] **Declare objects.**
  - databases `RAW` and `ANALYTICS`
  - schemas `RAW.DAMIR`, `ANALYTICS.STAGING`, `ANALYTICS.MARTS`
  - three XS warehouses (auto-suspend 60 s, initially suspended)

  *Expect:* warehouse compute has a ceiling from day one through the bootstrap resource monitor. Serverless features aren't covered by it, and this design uses none.
- [ ] **Declare roles and grants** exactly as in the governance table:
  - future grants on `MARTS` for `AGENT_READER`
  - the `SNOWFLAKE` database roles `GOVERNANCE_VIEWER` (for `QUERY_HISTORY` and `ACCESS_HISTORY`) and `OBJECT_VIEWER` (for `COLUMNS`) for `DOCGAP_AUDITOR`, unless the bootstrap test moved them to bootstrap

  *Expect:* `terraform plan` reads like an access review.
- [ ] **Create the service users.** Four `TYPE = SERVICE` users. RSA keys are generated locally with `openssl`; only public keys go into Terraform variables. *Expect:* no secret in state that isn't already in your keychain.
- [ ] **Write the access-matrix test.** `infra/verify_access.py` connects as each service user and runs one allowed and one forbidden statement per privilege (for example, the agent selecting from `RAW` must fail). *Expect:* a printed matrix, every cell as expected; its output goes into the README as governance proof.
- [ ] **Add infra CI.** `terraform fmt -check` and `terraform validate` on every pull request. *Expect:* malformed infra never merges.

**Done when:** `terraform apply` succeeds, a second `plan` shows no changes, and the access matrix passes.

## Phase 2: Data layer

Build a small, realistic warehouse over Open DAMIR: 56-variable monthly reimbursement files, open licence, already anonymized. The docs start deliberately incomplete, by a recorded rule.

**Offline first.** Everything in this phase except the Snowflake load, the live `dbt build` and the `persist_docs` check is built on `dbt-duckdb` over the offline sample before the trial starts. On Snowflake it's then one load evening and one build evening.

- [ ] **Write a pinned, checksummed loader.** `loader/sources.lock` lists each file's name, size and SHA-256, not its URL: download links carry a session token, so a pinned URL rots. The loader then:
  - resolves the token at download time
  - rejects any response that isn't gzip, since an expired token returns an HTML page
  - verifies the checksum and aborts on mismatch
  - PUTs the file to an internal stage and runs `COPY INTO RAW.DAMIR.PRESTATIONS`, with an explicit file format (delimiter, encoding and header checked on the first file) and `ON_ERROR = ABORT_STATEMENT`

  Budget a full evening for the first month: about 1 GB to download and about 37M rows to load.

  *Expect:* re-running loads nothing new, and loaded row counts equal file line counts minus headers, logged per file.
- [ ] **Type every raw column explicitly.** All 56 columns get declared types in the `RAW` DDL, plus one declared trailing filler column for the final `;`, asserted empty in staging. Nothing is inferred. The loader compares each file's header with the DDL before `COPY`. *Expect:* a schema change in a future file fails loudly instead of silently becoming `VARCHAR`, and the trailing delimiter doesn't abort the first load.
- [ ] **Turn code lists into seeds.** A deterministic script converts the dictionary's code-to-label tables (benefit type, provider specialty, region, age bracket) into dbt seeds. Labels become lookup tables in the warehouse; the definitions of the columns themselves stay in `eval/reference/`. The code lists are in the descriptor's `MOD OPEN DAMIR` sheet. *Expect:* seed CSVs regenerate byte-identically from the `.xlsx`.
- [ ] **Staging model.** `stg_damir__prestations` casts types, trims, and keeps source column codes: many real warehouses do, and it's what makes documentation matter. Check the grain: if dimension combinations repeat, aggregate measures by all dimensions here. `SOI_ANN` and `SOI_MOI` stay codes, never cast to a date: 15,615 rows carry `0000`/`00` or `0001`/`01` (ADR 0011). *Expect:* one row per unique dimension combination, with a surrogate key tested `unique` + `not_null`.
- [ ] **Marts with enforced contracts.** `fct_reimbursements`, 4 dimensions from seeds, and `agg_monthly_spend_by_category`, roughly 60–80 mart columns in total. `ETB_DCS_MCO` stays out of the marts: the dictionary has no entry for it, so no draft for it could be graded (ADR 0011). Contracts are enforced, with `not_null`/`unique` on keys, `relationships` fact → dims, and `accepted_values` from seeds. *Expect:* `dbt build` fails if any contract or test breaks.
- [ ] **Metadata on every mart column.** `meta.owner` on models; `meta.sensitivity` on columns (demographic columns `restricted`). `docgap lint` enforces both. *Expect:* lint is green; removing one tag turns CI red.
- [ ] **Set the baseline docs by a recorded rule.** All models get descriptions. Column descriptions exist for a subset chosen by seeded random sampling (50% of mart columns, seed in config), written from the dictionary in your own words. The chosen list is frozen in `warehouse/baseline_docs.lock`. *Expect:* a neutral, reproducible "before" state that nobody can call rigged.
- [ ] **Persist docs and verify the agent sees them.** `persist_docs: {relation: true, columns: true}`. A check connects as `SVC_AGENT` and reads `information_schema.columns.comment`. Mixed-case column names only persist with `quote: true`; the uppercase DAMIR codes avoid this, and the check catches it if not. *Expect:* comments visible to the agent match the YAML exactly.
- [ ] **Freeze the manifest.** Copy `target/manifest.json` to `fixtures/manifest_baseline.json` with its hash. *Expect:* docgap's offline mode has the exact schema the warehouse had.

**Done when:** `dbt build` passes on DuckDB (before the trial) and on Snowflake with zero failures and all contracts enforced, lint is green, the agent reads comments, and baseline coverage equals the locked number.

## Phase 3: Agent harness and baseline

Create real agent traffic against the warehouse, grade it without a model, and freeze the evidence. This phase produces the "before" number.

**Offline first.** The questions, grader, agent loop and pilot all run on DuckDB before the trial. Gold SQL is written in Snowflake SQL and transpiled to DuckDB with sqlglot, and the transpile is tested. In offline mode, the agent's `describe()` reads descriptions from the dbt manifest. On Snowflake, the phase is then the gold results, one baseline evening and the snapshot.

- [ ] **Write 40 questions with gold SQL.** `eval/questions.yml` holds, per question: `id`, English text, `gold_sql`, `ordered` flag, category. Categories: optical, dental, pharmacy spend; by region, age bracket, provider type; care month versus processing month (`SOI_ANN`/`SOI_MOI` against `FLX_ANN_MOI`). The one loaded month has no spend trend: each care month in it is only the part processed in January 2025 (ADR 0011). The columns each question needs are derived by running `resolve` on the gold SQL, not listed by hand. Each gold result is deterministic, with at most 200 rows and 5 columns, so the protocol's grader can match it. Budget 2–4 evenings: gold SQL over coded French columns is slow to get right. *Expect:* questions a business user would ask, answerable only from `MARTS`.
- [ ] **Offline pilot: can docs move the number at all?** This is a go/no-go check, run before the trial with a few dollars of API spend.
  - Write 12 pilot questions, excluded from the 40.
  - Run the agent on them on DuckDB, 3 repetitions each, in four configurations: {Haiku 4.5, Opus 5.5} × {no column docs, every column documented}.
  - **Choose the agent model:** the one with the largest gap between no docs and full docs, provided its full-docs accuracy falls between 50% and 90% and the gap is at least 15 points; on an equal gap, Haiku 4.5. A frontier model can read coded columns from names and values, or recall the public dataset from training, which narrows the gap docgap is meant to close. Too weak a model fails for reasons docs can't fix, which adds noise. The pilot measures both effects instead of guessing.
  - Record the four accuracies and the choice in `docs/adr/`. The model not chosen is reported under limits, not run as a full arm.
  - Adjust difficulty only now.

  *Expect:* for the chosen model, accuracy with no docs far from 0% and 100%, and a gap to full docs large enough to pass the kill criterion in the protocol. If the gap is small for both models, the experiment can't show anything, and it's better to know before the trial starts.
- [ ] **Materialize gold results.** Run each gold query once as `DOCGAP_AUDITOR` (tagged `gold:<qid>`, excluded from traffic) and store the results as Parquet with hashes. *Expect:* grading runs offline from then on.
- [ ] **Fix N, split and tag the pre-registration.** Assign the 25 discovery / 15 holdout split by hashing question IDs with the config seed, and report how many columns the two sets share. Run `resolve` on the discovery gold SQL, count the undocumented columns it touches, and fix N by the protocol's rule. Once the gold results are materialized and every gold query meets the protocol's rules, commit questions, gold SQL, split, N, arms, protocol and the pilot's agent model as `[call_sites.agent]` in `docgap.toml`. Run gitleaks over the full history and the private-terms guard over every commit message, create the git tag `preregistered`, make the repo public, and publish a GitHub release on the tag with the tagged commit's SHA in its body, all before any baseline run. *Expect:* anyone can verify nothing changed after the baseline.
- [ ] **Build the test agent.** A short tool-calling loop in `eval/agent/`:
  - `list_tables()` and `describe(table)` read `information_schema` names and comments
  - `run_sql(sql)` runs as `SVC_AGENT` with `query_tag = agent:<run_id>:<qid>:<rep>`, a 60 s statement timeout and a 200-row cap
  - at most 8 tool calls, then a structured final answer `{final_sql}`

  The model chosen by the pilot, pinned by ID, default sampling for both candidates (no `temperature`), versioned system prompt. Same sampling keeps the pilot comparison fair, and it's what gives the 3 repetitions meaning: at temperature 0 they would mostly repeat each other. For the same reason, the agent's cache key holds the repetition number, and not the arm. The harness runs `final_sql` once with the agent's query tag, fetching at most 201 rows. An API error left after the retry policy, or an unavailable warehouse, is not cached: the run repeats, and after 3 attempts it fails with `error`, counted per arm in the run manifest. *Expect:* each transcript saved as JSON (tool calls, SQL, truncated results).
- [ ] **Write the deterministic grader.** `grade.py` executes nothing: it compares the agent's final result to the gold result.
  - Numbers are cast to Decimal (floats through `repr`) and rounded half-even to 2 places; strings are trimmed.
  - Columns are matched to gold by the best permutation (at most 5 columns).
  - Rows compare as multisets unless `ordered`.

  Output: pass/fail plus a reason code (`error`, `timeout`, `shape_mismatch`, `row_count_mismatch`, `value_mismatch`). *Expect:* unit tests cover every rule and reason code.
- [ ] **Run the baseline.** 40 questions × 3 repetitions = 120 runs, only after the tag's GitHub release exists. Report accuracy for discovery and holdout separately, with per-question pass rates. Its discovery traffic is what the ranking reads; the arms are compared with a baseline re-run in the Phase 6 session, under their setup. *Expect:* `grades.parquet` under the baseline's run ID, and a summary in the run report.
- [ ] **Snapshot query history.** Wait at least 45 minutes (the `QUERY_HISTORY` latency), then export the run window filtered by role and tag. Keep the `qid` from each query tag so ranking can use discovery queries only. Redact (Phase 4 code) and save as `fixtures/query_snapshot_baseline.parquet` with its hash.

  If `ACCESS_HISTORY` is used as a cross-check, wait at least 3 hours: that's its latency. Compare on successful queries only, since `ACCESS_HISTORY` excludes failed ones.

  *Expect:* everything after this point can run without Snowflake, except the arm rebuilds in Phase 6.
- [ ] **Profile every mart column in the same session.** As `DOCGAP_AUDITOR`, run the aggregate profile queries (Phase 5 rules: *k* = 11 fact rows, nothing for `restricted`) for all mart columns, not just the top N, and freeze them with a hash. *Expect:* evidence packets for both the top-N and random-N arms are built offline; Phase 5 needs no warehouse.

**Done when:** the `preregistered` tag exists, 120 graded runs are stored, baseline accuracy is reported for both sets, and the snapshot and profile fixtures are committed.

## Phase 4: docgap deterministic core

The part a data team would actually adopt: from query history and a dbt manifest to a ranked list, with no model involved. It can be built on hand-made fixtures before the trial starts.

- [ ] **`snapshot`: one code path, two sources.** Read from Snowflake (`--live`) or from a fixture (`--offline`); after loading, the code is identical. Parse with sqlglot (`dialect="snowflake"`), replace every literal with a placeholder, and fingerprint as SHA-256 of the normalized SQL. Unparseable queries are counted with a reason, never dropped silently. As the first stage to write Parquet, it declares its dtypes from the `QueryRecord` contract, and every later Parquet writer does the same from its own contract. *Expect:* raw query text never reaches disk; the report shows the parse rate.
- [ ] **`resolve`: queries to columns.** Build a sqlglot schema from the manifest (relation → columns → types), `qualify` each query (expands `*`, resolves aliases and CTEs), then collect every `Column` node as `DATABASE.SCHEMA.TABLE.COLUMN` with its clause (select, where, join, group by). Columns outside dbt relations are reported as "unmanaged". *Expect:* validated against 10 hand-checked gold queries. With `ACCESS_HISTORY`, report the agreement rate on successful queries (target ≥ 95%).
- [ ] **`usage`: per-column counts.** Per column: executions, distinct fingerprints, distinct questions and distinct agent runs (question × repetition, the denominator of *r*), split by actor class from the role mapping in config. In the evaluation, the counts that feed `rank` include discovery `qid`s only; a test feeds in a holdout-tagged query and asserts that no ranking input changes. *Expect:* `column_usage.parquet`, one row per column, sorted by FQN.
- [ ] **`coverage`: the headline governance metric.** Plain coverage = documented columns ÷ all mart columns. Usage-weighted coverage = executions on documented columns ÷ all executions. *Expect:* two numbers side by side. The gap between them is the story ("72% documented, but only 41% of what agents query").
- [ ] **`rank`: explicit, stable scoring.** Only columns with no description are ranked. Score:

```latex
\text{score}_c = \ln(1 + u_c) \times (1 + w \cdot r_c)
```

Here *u* = executions, *r* = attributed failure rate (0 until Phase 5), and *w* = 1, set by the protocol, comes from config, in a `[rank]` section this step adds. The log dampens one heavy query from dominating. Sort by score descending, then by column FQN, so ties never reorder. *Expect:* `ranked_gaps.parquet` + a Markdown table.

- [ ] **Golden-file tests.** Fixture in → exact expected outputs checked into `tests/golden/`. *Expect:* any behaviour change shows as a diff in review.
- [ ] **Property tests for determinism.** Shuffling input rows, or running twice, must give identical output hashes (hypothesis). *Expect:* order-dependence bugs caught before they reach a ranking.
- [ ] **`run_manifest.json` from the first stage on.** The `RunManifest` contract: input hashes, config section hashes, environment, git SHA, output hashes, counts (parsed, unresolved, unmanaged). `cli.py` builds the environment from the installed packages and passes it in, and derives the run ID as `<as-of as YYYYMMDDTHHMMSSZ>-<first 8 hex digits of the setup hash>` with a pure core function (ADR 0007). *Expect:* every run is auditable, even before any model is involved.

**Done when:** `docgap analyze --offline` reproduces the committed ranking byte for byte in CI, and the two coverage numbers appear in the report.

## Phase 5: System One-style judgments

Add exactly two typed judgments and one drafter, all behind `llm/`, all replayable from cache. The design follows System One's principles: deterministic first, typed questions, one call per item, risk bands, replay.

**Offline first.** The whole phase is built and tested against the pilot's failures and the DuckDB sample before the trial. After the baseline, it runs on the frozen snapshot and profiles, so it needs no warehouse.

**What the "probabilities" are.** With `llm_answer_mode="probabilities"`, the model writes a probability for each label into its JSON answer, and sums that don't reach 1 are rescaled. They are not token log-probabilities. Expect clustered values (0.9, 0.95), treat them as ordinal, and check them against the deterministic signals below.

- [ ] **Build the `llm/` module first.** It's the only place that talks to a model.
  - `SystemOneAdapterClient(structured_outputs=True, llm_answer_mode="probabilities")` with a pinned model, a `RetryPolicy` and a per-call timeout
  - system-one-adapter and typesafe-sdk pinned to exact versions in `uv.lock` on day 1. The adapter is young and has already shipped one breaking release (v0.2.0), so the typed question definitions live in docgap's own `llm/` module.
  - cache key = SHA-256 of canonical JSON `{model, prompt_version, state, questions}`, stored under `fixtures/llm_cache/`
  - `--offline` makes a cache miss an error, so CI can never call a model
  - a per-run spend budget, from a section this step adds to `docgap.toml`
  - a call that fails (timeout, refusal, malformed answer) is cached with its `FailureReason`, so an offline replay reproduces the failure instead of treating it as a miss. The test agent's infrastructure failures are the exception: the protocol repeats those runs instead. Decide here whether a live run retries a cached failure after its call site's timeout was raised, since the key doesn't hold the timeout (ADR 0007)

  *Expect:* a second run makes zero model calls and returns identical outputs.
- [ ] **Handle the timeout gap.** The adapter's providers don't expose a configurable timeout yet. They build the Anthropic client with the SDK's default 10-minute timeout, so a hung call is bounded, just far too loosely. Pin your fork's commit or add a thin provider subclass with a tight per-call limit, set per call site in `docgap.toml`, and document it accurately in the README's "Known gap" section with a link to your open issue. On timeout, the item goes to the human band. *Expect:* a test with a never-replying fake server: the run completes, the item is flagged, and the manifest counts one timeout. This is the first local fake server, so the same PR first tests that a Unix socket and a marker-opted localhost server still work under pytest's `--disable-socket`.
- [ ] **Failure attribution (Choice).** Only for failed runs on discovery questions. State = question, agent SQL, gold SQL, grader reason code, first 20 rows of both results, current docs of the involved columns. Two questions in one call (sketch below). *Expect:* `attributions.parquet` with full probability maps, not just the top label.
- [ ] **Deterministic cross-check: confusion pairs.** For each failure, compare the columns resolved from the agent's SQL with those resolved from the gold SQL. The difference is a model-free attribution that costs nothing, given `resolve`. *Expect:* the report shows how often the LLM's column choice agrees with the confusion pair.
- [ ] **Soft failure rate per column.** Using probabilities as soft counts, over failures *f* and the runs that touched column *c*:

```latex
r_c = \frac{\sum_f P_f(\text{cause} = \text{meaning}) \cdot P_f(\text{column} = c)}{\text{runs touching } c}
```

*Expect:* `rank` now uses *r*; the re-ranking shows which heavily used columns also cause wrong answers.

- [ ] **Evidence packets for the top-N and random-N columns.** Compiled lineage SQL from the manifest, the column's upstream expression via sqlglot lineage, and the frozen profile from Phase 3: null rate, distinct count, min/max for numbers clipped to the *k*-th smallest and largest value, and top values carried by at least *k* = 11 fact rows. `restricted` columns get no values at all. The stage checks that each profile's `k` equals the configured *k*, since the contract can only check values against the profile's own `k`. *Expect:* packets stored and hashed; the governance rule is unit-tested.
- [ ] **Drafter.** Anthropic API, Opus 5.5 pinned by ID (as are both judgments), default sampling, cached. Structured output `{description (≤ 200 chars), unknowns[]}`. The prompt forbids claims the evidence doesn't support and asks for unknowns instead. *Expect:* short, cautious drafts that name what they couldn't infer.
- [ ] **Draft gate (Noul).** State = evidence packet + draft. One Noul: *the description is fully supported by the evidence and makes no unsupported claim*. Bands from config: ≥ 0.8 → ready, 0.5–0.8 → owner must confirm, < 0.5 or timeout → no draft, flagged. A draft the drafter failed to write is flagged without a gate call. *Expect:* each gap carries a band and the probability behind it.
- [ ] **Blind labels for calibration.** Before looking at gate scores, label each draft against the official dictionary as correct, partial or wrong, and commit `eval/draft_labels.yml` with the drafter and gate prompt versions it graded. Those prompts were fixed at the `preregistered` tag. Then report accuracy per band. The labels score correctness against the dictionary, while the gate scores support by the evidence, so the table checks whether the bands are useful, not whether the gate does its stated job. *Expect:* an honest table. With about 20–30 drafts it's evidence, not calibration, and the README says so. If the scores cluster and the per-band accuracy isn't monotone, fall back to two bands by merging "ready" and "owner confirms", which leaves the arms' contents unchanged, and log the decision.

**Typed questions (sketch, verified against typesafe-sdk 0.7.1):**

```python
from system_one_adapter import SystemOneAdapterClient, Noul, Choice

attribution = {
    "cause": Choice(
        instructions="Why did the agent's SQL produce a wrong result?",
        criteria={
            "column_meaning": "Misread what a column contains or how it is coded",
            "wrong_table": "Queried the wrong table",
            "wrong_join": "Joined incorrectly",
            "wrong_filter": "Filtered on the wrong values or period",
            "wrong_grain": "Aggregated at the wrong level",
            "other": "None of the above",
        },
    ),
    # criteria built from resolve(): the closed set of columns in either query
    "column": Choice(instructions="Which column was misunderstood?", criteria=candidate_columns),
}

gate = {"supported": Noul(instructions="The description is fully supported by the evidence.")}
```

**Done when:** attributions, drafts and gate results replay from cache in CI, the ranking includes *r*, and the per-band accuracy and confusion-pair agreement tables exist.

## Phase 6: Close the loop

Turn the ranking into a reviewed change, then measure each arm with the exact same setup. This phase produces the "after" numbers, and it's the one live step the whole project depends on. Schedule it with at least a week of trial left.

- [ ] **`patch`: minimal YAML edits.** ruamel.yaml writes each approved or confirm-band draft into the right `schema.yml` under its column, preserving order and comments. Flagged items get no text. Provenance (run ID, band, probability, evidence hash) goes in the pull-request body, not the YAML, so the docs stay clean. *Expect:* a diff that touches only description lines.
- [ ] **Open the pull request with `gh`.** Branch `docgap/<run_id>`. The body holds the ranked table, both coverage numbers (now and projected), each item's band, a checkbox per column for the reviewer, and the run-manifest hash. *Expect:* the screenshot for the README.
- [ ] **Review as the column owner.** Accept, edit or reject each draft, and record the decisions in `eval/review_decisions.yml`. Edits are kept as edits, never overwritten. *Expect:* merged docs you'd defend, and an edit rate for the report; the decisions add to the gate's label set.
- [ ] **Build each arm from its own branch.** The baseline arm runs from the baseline YAML, `arm/top-n` and `arm/random-n` each hold the baseline YAML plus that arm's unedited drafts, and `arm/ceiling` is optional. For each arm, in the order baseline, random-N, top-N, ceiling:
  1. Run `dbt build`.
  2. Re-run the Phase 2 check as `SVC_AGENT`, confirming `information_schema` comments equal that arm's YAML.
  3. Run the agent: same 40 questions, 3 repetitions, same setup hash (model IDs, prompt versions, sampling, config sections, environment), checked by comparing manifests. The agent's run ID carries the arm name, so each arm's query tags stay distinct.

  Run all arms in the same session so nothing else drifts between them. That's 360–480 runs on an XS warehouse, one to two evenings; where the baseline replays the Phase 3 run from the model cache, it costs warehouse time only. *Expect:* `grades.parquet` under each arm's run ID, and the change reached the agent through the same path real docs would.
- [ ] **Compare honestly.** Holdout accuracy for top-N versus random-N is the headline, with the pre-registered paired bootstrap interval over questions. The protocol's bootstrap values go into `docgap.toml` here, as `[seeds] bootstrap = 4` and `[compare] resamples = 10000`, with their `config.py` fields and tests. Report alongside it:
  - baseline and ceiling, on both splits, and the Phase 3 baseline next to the session's
  - delivered drafts per band and per arm
  - a per-question flips table (fail → pass, pass → fail) per arm, against the session's baseline
  - the column overlap between discovery and holdout, and between top-N and random-N

  *Expect:* a result you report whatever its size and sign, as the protocol promised.
- [ ] **Re-run docgap on the new snapshot** (optional, second on the cut list). A fresh history snapshot after the top-N run. *Expect:* usage-weighted coverage before → after, and the next ranked list, showing the tool is a loop, not a one-off.
- [ ] **Generate the results, never type them.** `docgap report` renders `docs/RESULTS.md` and the README's results block from run files. *Expect:* every number in the README traces to a file and a hash.

**Done when:** the pull request is merged, comments are verified in Snowflake for each arm, grades exist for every arm, and the README results block is generated.

## Phase 7: Orchestration and CI

Show how a team would run docgap unattended: weekly in Airflow, guarded by CI. The DAG only calls the CLI, so no logic lives in Airflow.

- [ ] **One DAG, `docgap_weekly`.** Tasks: `snapshot → resolve → usage → rank → evidence → draft_gate → open_pr`, each a `@task` shelling out to `docgap <stage> --as-of {{ data_interval_end }}`. The CLI derives the run ID (ADR 0007); Airflow's own `run_id` holds a colon, which the `RunId` pattern rejects. *Expect:* the logical date, never `now()`, sets the window, so a backfill of any week gives the same result.
- [ ] **Idempotent by design.** The run ID derives from the interval and the setup hash; artifacts go to `runs/<run_id>/`; stages skip when the output hash already matches. No data passes through XCom, only paths. Retries apply only to `snapshot`. *Expect:* clearing and re-running a task changes nothing.
- [ ] **No pull request when nothing changed.** If the ranked list and drafts equal the last open pull request's (by hash), `open_pr` short-circuits. *Expect:* no weekly noise for reviewers.
- [ ] **Run Airflow locally with Docker Compose,** with the connection defined in environment variables for `SVC_DOCGAP` only. *Expect:* one live run recorded if trial time remains (logs + screenshot in `docs/`), otherwise a recorded fixture-mode run. The live run is first on the cut list.
- [ ] **Test the DAG in pytest.** Import check, task order, and a `dag.test()` run in `--offline` mode. *Expect:* DAG breakage fails CI, not Monday morning.
- [ ] **CI on every pull request** (GitHub Actions):
  - ruff, pyright, pytest (offline, cache-only models)
  - `dbt parse` with dummy credentials, then `docgap lint`
  - `terraform fmt -check` + `validate`, gitleaks
  - determinism check: run `analyze --offline` twice and compare output hashes

  *Expect:* a green badge that means something.
- [ ] **Protect the docs path.** `CODEOWNERS` on `warehouse/dbt/models/**/*.yml` and branch protection on `main`. *Expect:* docgap can propose, but only an owner can merge.

**Done when:** CI is green on `main`, the DAG test passes, and one DAG run is recorded (live if the trial allowed it).

## Phase 8: README and packaging

The repo is read, not run, so the README must deliver the result in 30 seconds and prove it on demand.

- [ ] **Above the fold.** In this order:
  - one-line pitch
  - the generated results block: holdout accuracy for the Phase 6 session's baseline, random-N, top-N (and ceiling), the top-N minus random-N interval, usage-weighted coverage before → after, and columns documented
  - the pull-request screenshot
  - a three-command offline quickstart (`uv sync`, `docgap analyze --offline`, `docgap report`)

  *Expect:* a reviewer gets the point without scrolling.
- [ ] **"Run it on your warehouse."** The config values needed (account, role, manifest path, role-to-actor mapping) and a reusable Terraform module `infra/terraform/modules/docgap_auditor` that creates only the read-only role and user. Include the access-matrix output. *Expect:* the adoption cost is visibly small and safe.
- [ ] **How it works.** The architecture diagram, the five steps, and the deterministic-core / model-edges split. *Expect:* engineers see where the models are and aren't.
- [ ] **Design choices, briefly.** Read-only by construction, redaction before storage, aggregate-only evidence, pre-registration, generated numbers, the typed judgments and bands (the questions use the typesafe-sdk types, so moving from the adapter to the hosted client changes only client setup). *Expect:* one line each, linked to its record in `docs/adr/`.
- [ ] **Known gap and limits, stated plainly.**
  - the adapter timeout: no configurable per-call limit, only the SDK's 10-minute default (fork pin, open issue link)
  - small n: holdout is only 15 questions, so a top-N versus random-N difference under about 20 points is more likely missed than detected, and one of about 28 points is detected more than 80% of the time
  - one random draw for random-N; the interval doesn't cover how another draw would have done
  - the query traffic is the evaluation's own agent traffic, not organic usage, and there is no human traffic unless it was generated (see open decisions)
  - the judgment "probabilities" are written by the model, not log-probabilities, so the bands are ordinal
  - open anonymized data, so sensitivity tags demonstrate a mechanism
  - the model may know public Open DAMIR definitions from training, which is why the gate scores support by the evidence, not correctness
  - one agent model for the headline; the other candidate's pilot numbers are reported, but it isn't run on the full evaluation

  *Expect:* credibility; reviewers trust results whose limits are named.
- [ ] **Fresh-clone rehearsal.** On a clean machine: clone, offline quickstart, `pytest`. Everything passes in under 5 minutes and the README numbers match the regenerated ones. *Expect:* no "works on my machine".

**Done when:** a fresh clone reproduces every README number offline, and the top of the README reads in 30 seconds.

## Timeline, risks and open decisions

About 30–44 evenings of 2–3 hours, 36 likely. The range includes time for the risks listed below. The Snowflake trial window is the binding constraint, so everything that doesn't need the warehouse is built first on DuckDB and fixtures. The trial starts when the offline pilot passes, not on a set date.

| Order | Phase | Needs live Snowflake | Estimate (evenings) |
| --- | --- | --- | --- |
| 1 | 0 · Foundations and pre-registration | No | 2–3 |
| 2 | 4 · Deterministic core, on hand-made fixtures | No | 4–6 |
| 3 | 2 · Data layer on DuckDB (offline sample) | No | 2–3 |
| 4 | 3 · Questions, grader, agent loop, offline pilot | No | 4–6 |
| 5 | 5 · Judgments, built on pilot outputs | No | 4–6 |
| | **Go/no-go: pilot passes the kill criterion → sign up for the trial** | | |
| 6 | 1 · Terraform foundations and bootstrap | Yes | 2–3 |
| 7 | 2 · Load one month, `dbt build`, `persist_docs` check | Yes | 2–3 |
| 8 | 3 · Gold results, `preregistered` tag, baseline, snapshot, profiles | Yes | 2 |
| 9 | 5 · Run judgments on the frozen snapshot | No (inside trial window) | 1 |
| 10 | 6 · Pull request, arm builds and re-runs | Yes | 3–4 |
| 11 | 7 · Orchestration and CI (live run if time remains) | Partly | 2–4 |
| 12 | 8 · README and packaging | No | 2–3 |

About 12–16 evenings fall inside the trial. At 4 evenings a week, that's roughly 3–4 weeks, leaving a small buffer before day 30. At 3 evenings a week there is no buffer, and the cut list below starts on day 1 of the trial.

**Cut list, in order,** if the schedule slips. The experiment and the README come before everything on it.

1. The live Airflow run: keep the DAG file and the `dag.test()`, record a fixture-mode run.
2. Re-running docgap on the post-change snapshot (Phase 6).
3. The `ACCESS_HISTORY` cross-check.
4. The ceiling arm.
5. Hypothesis property tests: keep "run twice and compare hashes".

### Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Trial ends before the arm re-runs | No "after" numbers, so no headline | Everything that doesn't need the warehouse is built offline first; signup gated on the offline pilot; Phase 6 scheduled with a week of trial left; cut list |
| Documenting any N columns helps as much as docgap's top N | The ranking claim is unsupported | Random-N arm pre-registered; the result is published either way |
| Holdout traffic or failures reach the ranking | The holdout is no longer "never seen" | Ranking inputs use discovery `qid`s only; a test enforces it |
| Docs barely move accuracy on this schema | No measurable change in any arm | Offline pilot (no docs vs full docs) before the trial, with a kill criterion |
| Objects only `ACCOUNTADMIN` can manage | `terraform apply` fails on the resource monitor, and maybe on `SNOWFLAKE` role grants | Resource monitor in `bootstrap.sql`; grant tested in the first 10 minutes of the trial |
| Open DAMIR months are large (about 37M rows, 1 GB gzipped each) | Slow loads, credit burn | One month; XS warehouses; resource monitor at 90%; a full evening budgeted for the first load |
| Download links carry a session token | The lock file rots; an HTML page fails the checksum | Lock name, size and SHA-256; resolve the token at download; reject non-gzip responses |
| Source grain has no natural key | Tests fail | Aggregate by all dimensions in staging; surrogate key tested (no duplicates seen in a 2M-row sample) |
| Small n | Noisy result; a real difference under about 20 points is more likely missed than detected | Detectable effect stated in the protocol before any run (ADR 0008); 3 repetitions, holdout headline, paired bootstrap interval, flips table |
| sqlglot misses some queries | Usage undercounted | Parse and resolve rates in every report; `ACCESS_HISTORY` cross-check on successful queries |
| Model-written probabilities cluster | Bands don't separate drafts; *r* collapses to "columns in failed queries" | Treat as ordinal; confusion-pair cross-check; blind labels and per-band accuracy; fall back to two bands |
| Model knows Open DAMIR from training | Drafts right for the wrong reason | Gate scores support by evidence; stated in limits |
| Adapter has no configurable timeout | A hung call stalls a run for up to 10 minutes (SDK default) | Fork pin or subclass with a tight limit; timeout goes to the human band; tested |
| Adapter interface changes (one breaking release so far) | Judgments break after an upgrade | Exact pins in `uv.lock`; typed questions owned in `llm/` |

### Open decisions

- [ ] **Agent model:** Haiku 4.5 or Opus 5.5, settled by the Phase 3 pilot rule before the `preregistered` tag. The drafter, attribution and gate use Opus 5.5. Every call site runs at default sampling; config records the model ID, the settings actually sent, and a per-run budget.

  Rough cost for about 620–740 agent runs of up to 8 tool calls (pilot, the Phase 3 baseline, and the Phase 6 arms with their baseline), before prompt caching and the Phase 6 baseline's cache replays: on the order of $200 on Opus 5.5 and $55 on Haiku 4.5. The drafter and judgments add a few dollars. Either fits; the calendar is the constraint, not money.
- [ ] **Where "people" traffic comes from:** generate it (a `HUMAN_ANALYST` role and service user running templated or Metabase queries) or state that the demo traffic is agent-only. Decide before Phase 1, because the first option adds a role and a user in Terraform.
- [ ] **Evenings per week during the trial:** 4 or more keeps a buffer; at 3, the cut list is active from the start.

### Sources

- [Open DAMIR, Assurance Maladie](https://www.assurance-maladie.ameli.fr/etudes-et-donnees/open-damir-depenses-sante-interregimes) and [data.gouv.fr listing](https://www.data.gouv.fr/datasets/open-damir-base-complete-sur-les-depenses-dassurance-maladie-interregimes)
- [Snowflake Account Usage views](https://docs.snowflake.com/en/sql-reference/account-usage) and [SNOWFLAKE database roles](https://docs.snowflake.com/en/sql-reference/snowflake-db-roles)
- [Snowflake resource monitors](https://docs.snowflake.com/en/user-guide/resource-monitors) and [trial accounts](https://docs.snowflake.com/en/user-guide/admin-trial-account)
- [Terraform provider snowflakedb/snowflake](https://registry.terraform.io/providers/snowflakedb/snowflake/latest/docs/resources/grant_privileges_to_database_role)
- [dbt `persist_docs`](https://docs.getdbt.com/reference/resource-configs/persist_docs)
- [system-one-adapter-python](https://github.com/typesafe-ai/system-one-adapter-python)
