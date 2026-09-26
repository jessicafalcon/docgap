---
name: docgap-correctness
description: >
  What makes a change correct and safe in docgap: the invariants (determinism of the
  core, model calls only at the edge and always cached, read-only governance and
  redaction, evaluation integrity with no holdout leakage, nothing dropped
  silently), validation at trust boundaries, and a review checklist. Read this
  BEFORE landing any change to src/docgap, the eval harness, dbt models or
  Terraform grants, and when reviewing code.
---

# docgap-correctness

The product promise is a ranking anyone can reproduce and an accuracy result
nobody can call rigged. A change that computes the right number but breaks
reproducibility, leaks the holdout, or widens what the model or the tool can see
is a **defect**, whatever the output looks like.

## The five invariants (a change that breaks one does not land)

### 1. Determinism of the core

Same manifest, snapshot, config and cache (by hash) ⇒ byte-identical canonical
outputs and run manifest. In `src/docgap/` outside `llm/` and `cli.py`:

- No clock, environment, `uuid4`, unseeded randomness, or builtin `hash()` of
  strings (salted per process). `as_of` and seeds are parameters.
- No filesystem-order or hash-order leaks: `sorted()` around directory listings,
  sort frames by a total key before writing, ties broken by FQN.
- The `determinism-guard` hook flags these on edit; pre-commit runs the same script.

### 2. Model calls live only in `llm/`, and every call is replayable

- Pinned model ID, versioned prompt, structured output, recorded sampling settings.
- Cache key = `sha256(canonical_json({model, prompt_version, state, questions}))`.
- `--offline`: a cache miss is an error, never a live call. CI is always offline.
- The model never sees more than the "What each model call can see" table in the
  brief allows. Adding a field to a prompt's state is a governance change.

### 3. Read-only and least exposure

- docgap never writes to the warehouse. Changes reach it only through a reviewed
  PR and dbt.
- Query text is normalized (literals → placeholders) **before** it touches disk.
  If redaction fails for a query, the query is counted and dropped, never stored raw.
- Profiles are aggregates only; values seen fewer than *k* times are suppressed;
  `restricted` columns get no values at all.
- `eval/reference/` (the dictionary) is ground truth for grading and is never
  imported or read by `src/`.

### 4. Evaluation integrity

- What is frozen at the `preregistered` tag, and how anything else in the
  protocol may change, is set in `CLAUDE.md` → "After `preregistered`".
- **Ranking inputs come from discovery questions only.** Usage and attribution
  filter on discovery `qid`s; a test proves a holdout-tagged query changes nothing.
- All arms use the same model ID, prompt version and config hash, verified by
  comparing run manifests before the comparison is computed.
- Results are generated from run files, never typed. Every README number traces
  to a file and a hash.

### 5. Nothing disappears silently

Unparseable queries, unresolved columns, unmanaged relations, timeouts, redaction
failures and rejected drafts are **counted with a reason** and reported. A stage
that drops a row without a counter is a bug.

## Validate at trust boundaries

- **Snowflake exports:** validate columns and types against the contract before
  use; an unexpected column or dtype fails the stage with the column named.
- **dbt manifest:** check `metadata.dbt_schema_version` and the adapter type; fail
  on an unknown version rather than guess the shape.
- **Downloads:** size, gzip magic bytes, SHA-256 before anything reads the file.
- **Model output:** parsed into the typed answer; a malformed answer is a
  recorded failure routed to the human band, not a retry-until-it-parses loop.
- **Config:** loaded into a pydantic model with ranges (`0 < k`, bands ordered);
  its hash goes into every manifest.

Fail loud on programmer error. Fail *recorded* on data error.

## Review checklist

When reviewing, report every finding (including uncertain or low-severity ones)
with a confidence and a severity, then rank. Only skip what ruff and pyright
already enforce. Check in order:

1. **Invariants** 1–5 above.
2. **Failure behavior** — what happens on timeout, partial write, retry, re-run
   and concurrent run? (docgap-resilience)
3. **Boundaries** — empty snapshot, zero failures, a column in two tables with the
   same name, mixed-case identifiers, `SELECT *`, CTE shadowing, a query touching
   no mart column, N larger than the number of gaps.
4. **Reproducibility** — would a second run on another machine give identical
   canonical output? Any ordering, clock or float-formatting leak?
5. **The check** — is there a test that fails if this logic breaks?
6. **Over-engineering** — anything to delete, inline, or replace with sqlglot,
   Polars or the stdlib?

When a fix applies broadly, say so ("apply the latency guard to ACCESS_HISTORY
too") and fix it once at the shared point, not per caller.
