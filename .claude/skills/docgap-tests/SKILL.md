---
name: docgap-tests
description: >
  The testing standard for docgap: what to test and to what bar. Doctests as
  documentation, unit tests, golden-file and determinism tests, evaluation-integrity
  tests (holdout leakage, dictionary isolation), fault-injection tests for the
  failure paths, contract tests at boundaries, and the offline/no-network rule.
  Read this BEFORE writing or changing any test, or when deciding what a change
  needs to prove.
---

# docgap-tests

The claims docgap makes (same inputs, same ranking; a clean holdout; nothing
dropped silently; failures recorded, not lost) are only as good as the tests that
pin them. Tests are deterministic and offline: `uv run pytest` needs no network,
no Snowflake, no model, no clock.

## The bar

- **One runnable check per non-trivial unit:** the smallest test that fails when
  the logic breaks. No suites for one-liners.
- **Every invariant and every failure behavior has a test that fails when it's
  violated.** A stage without one is unfinished.
- Fast: the whole suite runs in well under a minute on fixtures.

## Pick the lightest form

1. **Doctest:** proves behavior and documents usage. Preferred for pure functions.
   ```python
   >>> normalize("SELECT * FROM t WHERE id = 42")
   'SELECT * FROM t WHERE id = ?'
   ```
2. **Unit test:** error paths, parametrized edge cases, float tolerances. Arrange,
   act, assert. Name the behavior:
   `test_tie_on_score_orders_by_fqn`, `test_holdout_query_never_reaches_usage`.
3. **Golden file:** fixture in → exact expected output in `tests/golden/`,
   compared on canonical content (sorted rows, fixed column order). A behavior
   change shows up as a reviewable diff. Regenerate only with an explicit
   `--update-golden` flag, never automatically.
4. **Property test** (hypothesis): shuffled input rows or a second run give
   identical output hashes. Keep a small example budget so CI stays fast.

## Determinism

- Run `analyze --offline` twice on the same fixtures; assert identical canonical
  outputs and run manifests.
- Inject `as_of` and seeds; a test that needs a timestamp passes a fixed one.
- Run the determinism test again in a subprocess with a different
  `PYTHONHASHSEED`, to catch set- or hash-order leaks.

## Evaluation integrity

- **Holdout isolation:** add a holdout-tagged query and a holdout failure to the
  fixtures; assert that usage, attribution and ranking are unchanged.
- **Dictionary isolation:** `src/` never imports or opens `eval/reference/`
  (static import check plus a test that fails if the path appears in `src/`).
- **Arm parity:** the comparison refuses to run if two arms' manifests differ in
  model ID, prompt version or config hash.
- **Pre-registration:** questions, split, N and arms hash to the values committed
  at the `preregistered` tag.

## Contract tests at boundaries

- Snowflake export fixtures with an extra column, a missing column, and a wrong
  dtype must each fail validation with the column named.
- A manifest with an unknown `dbt_schema_version` fails loudly.
- Model answers: a malformed answer, a label outside the allowed set, and a
  probability map that doesn't sum to 1 each become recorded failures.

## Fault injection

For each behavior in docgap-resilience:

- **Crash mid-write:** interrupt between the temp write and `os.replace`; assert
  no visible artifact and that a re-run completes.
- **Resume:** run stages 1–3, delete stage 4's output, re-run; stages 1–3 are
  skipped by hash and stage 4 runs.
- **Transient vs permanent:** a fake server returning 429 then 200 is retried and
  succeeds; 401 fails on the first attempt; the retry budget is respected.
- **Timeout:** a fake server that never replies trips the per-call timeout; the
  item lands in the human band and the manifest counts one timeout.
- **Budget:** a spend budget crossed mid-run stops new calls and records the rest.
- **Gates:** a snapshot with a 50% parse rate fails with the gate, value and threshold.
- **Lock:** a second run on the same `run_id` is refused while the first holds the lock.
- **Governance fail-closed:** a query that fails redaction is dropped and counted,
  and never appears on disk.

Fakes are local (an in-process HTTP server or a stub client) and recorded
cassettes for provider formats. Never the real API in tests.

## Where tests live and how they run

- `tests/` mirrors `src/docgap/`; `tests/golden/` holds expected outputs;
  `fixtures/` holds the frozen snapshot, manifest, profiles and model cache.
- `pyproject.toml` enables `--doctest-modules`; README `>>>` blocks run too.
- CI runs `uv sync --locked && uv run --frozen pytest` with no credentials in the
  environment, so an accidental live call fails.
