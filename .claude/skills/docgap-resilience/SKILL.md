---
name: docgap-resilience
description: >
  Failure design for the docgap pipeline: failure classes and what each one does,
  idempotent and resumable stages, atomic writes and commit markers, retries with
  backoff only for transient errors, timeouts and budgets on every external call,
  per-item isolation, fail-closed governance, data-quality gates, concurrency
  locks, and observability through the run manifest. Read this BEFORE writing any
  code that calls Snowflake, the Anthropic API, GitHub, a download, or the
  filesystem outside a stage's own output, and before changing the Airflow DAG.
---

# docgap-resilience

Design every stage for the day it fails halfway. The questions are always the
same: what state is left behind, can a re-run finish the job without doing it
twice, and does anyone find out?

## Classify every failure

| Class | Examples | Behavior |
|---|---|---|
| **Transient** | 429, 5xx, connection reset, warehouse resuming, network timeout | Retry with capped exponential backoff and jitter; honor `Retry-After`; give up after the budget and fail the stage |
| **Permanent** | 400/401/403, SQL compilation error, missing grant, schema mismatch | Fail immediately with the cause; never retry |
| **Data** | unparseable query, unresolved column, malformed model answer, checksum mismatch | Record per item with a reason code, continue, report counts; fail the stage only when a quality gate is crossed |
| **Bug** | assertion, contract violation inside the core | Fail loud, full traceback, no catch-all |

Catch specific exceptions. A bare `except Exception` is allowed only at a
per-item isolation boundary, and it must record the item and the exception type.

## Idempotent and resumable by construction

- **Deterministic run IDs** from inputs
  (`run_id = f"{interval_end:%Y%m%dT%H%M%SZ}-{setup_sha256[:8]}"`, ADR 0007),
  never random: a pure core function of `as_of` and the setup, which only
  `cli.py` can build, since it holds the environment.
- **Skip when done:** a stage whose output exists with the expected input hashes
  and the current setup hash in its manifest entry returns immediately. Re-running
  a finished DAG changes nothing; a changed environment, config or call site
  re-runs the stage.
- **Atomic writes:** write to `path.tmp-<pid>` in the same directory, `fsync`,
  then `os.replace`. A reader never sees half a file.
- **Commit marker last:** a stage writes its artifacts, then its manifest entry
  (hashes, counts). An artifact without a manifest entry is garbage from a crash
  and is overwritten, never trusted.
- **Model cache entries** are written atomically and hold the key's inputs next
  to the response. A corrupt or mismatched entry is deleted and treated as a miss
  (an error in `--offline`).
- **External side effects** carry an idempotency key: the PR branch is
  `docgap/<run_id>` and `open_pr` checks for an existing PR with the same
  ranked-list hash before creating one.

## Timeouts and budgets everywhere

- **Snowflake:** `STATEMENT_TIMEOUT_IN_SECONDS` per session, `login_timeout`,
  `network_timeout`. XS warehouses with 60 s auto-suspend. The resource monitor is
  the account-level ceiling.
- **Anthropic:** a per-call timeout well below the SDK default (10 min), a retry
  budget, and a **per-run spend and call budget** from config. Crossing the budget
  stops new calls and routes the remaining items to the human band, recorded.
- **Downloads and GitHub:** connect and read timeouts, bounded retries.
- **Airflow:** `execution_timeout` per task, `retries` only on `analyze` (its
  snapshot reads the warehouse) and `open_pr` (the steps with transient external
  failures),
  `retry_exponential_backoff=True`, `max_active_runs=1`.

## Isolate items, not stages

One column's failed draft, one unparseable query or one timed-out judgment must
not fail the run. Each item is processed inside its own boundary; failures become
records in the stage output with a `FailureReason`, and the structured log holds
the `exception_type` and `attempts`, which can differ between two runs of the
same inputs and so stay out of canonical artifacts. The stage
fails only when:

- a **quality gate** is crossed (config, recorded in the manifest), e.g. parse
  rate < 90%, resolve rate < 90%, more than 20% of drafts timed out, zero rows in
  a snapshot that should have traffic;
- or an **invariant** would break (redaction failure, holdout `qid` in a ranking
  input, contract violation).

A gate failure names the gate, the observed value and the threshold.

## Fail closed on governance

When in doubt, show less. If redaction fails, the query is dropped and counted,
not stored raw. If a column's sensitivity is missing, it is treated as
`restricted`. If the auditor role can read something the governance table says
it can't, the access-matrix test fails the build.

## Know the source's latency and completeness

- `QUERY_HISTORY` lags up to 45 minutes; `ACCESS_HISTORY` up to 3 hours and
  excludes failed queries. The snapshot window ends at `as_of`, so the CLI
  refuses a live export whose `as_of` is younger than the latency: checked in
  code, not by waiting and hoping.
- Record the window, row counts and latency assumption in the manifest so a short
  export is visible, not silent.

## Concurrency

One writer per run: a lock file (`runs/<run_id>/.lock` holding pid and host,
created with `O_EXCL`) guards each run directory, and a stale lock is reported,
not silently broken. Airflow's `max_active_runs=1` is the first line; the lock
covers manual CLI runs alongside it.

## Observability

- Structured logs (JSON lines) with `run_id`, `stage`, `item`, `event`,
  `duration_ms`. No query text, no sampled values.
- The run manifest is the operational record: per-stage status, counts by reason,
  gate values, retries, cache hit rate, model calls and spend, durations.
- `docgap report` surfaces failures first: a run with recorded failures says so at
  the top, not in an appendix. Structured logs and per-item failures arrive with
  the first code that has items to fail, Phase 3's agent loop and its `llm/` (the
  brief owes both there); until then a failed stage shows as its status and error
  in the manifest.

## Tests for failure paths

Every behavior above has a test (docgap-tests §Fault injection): kill mid-write
leaves no visible artifact, a re-run resumes, 429 then success retries, 401 fails
fast, a hung fake server times out into the human band, a crossed gate fails with
its values, and a second concurrent run is refused by the lock.
