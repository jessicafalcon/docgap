# 0007. Hash the config per section, compare runs by the setup hash, and derive the run ID from it

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

ADR 0006 puts one hash per config section into the run's setup, and the setup
has its own hash. Other records still asked for a single "config hash": the
arms had to match on "model ID, prompt version and config hash", and the run ID
was `f"{interval_end:%Y%m%d}-{config_hash[:8]}"`. Should the manifest also store
a hash of the whole config, and what should the run ID and arm parity use?

## Considered Options

1. **Store a whole-config hash next to the section hashes.** The older records
   stay true as written.
2. **Section hashes only, and compare runs by the setup hash.** The records name
   the setup hash; the run ID takes the as-of timestamp and the setup hash's
   first eight hex digits.
3. **Section hashes only, and a run ID from the interval alone.** A changed
   setup re-runs its stages in the same run directory.

## Decision Outcome

Chosen option: **option 2**, because the setup hash already covers what the
older records asked the config hash to cover, and more: model IDs, prompt
versions, sampling settings, every config section and the environment. A stored
whole-config hash would be derivable from the section hashes, so it could only
ever add a second field that might disagree with them.

The section hashes cover the whole file only if every key sits in a section and
no key has a default. The config model rejects top-level keys and unknown keys,
declares no defaults, and hashes validated values, not file bytes, so a comment
or a reordered key changes nothing.

### Consequences

- Good, because arm parity is one comparison of `RunSetup.sha256()`, and when
  two setups differ, the section hashes, environment and call sites name what
  moved.
- Good, because a run ID of `f"{interval_end:%Y%m%dT%H%M%SZ}-{setup_sha256[:8]}"`
  gives each window and setup its own `runs/<run_id>/`. The full timestamp keeps
  two manual runs on one day apart; the weekly DAG's are midnights. A changed
  config, prompt or dependency starts a new run instead of overwriting the last
  one's artifacts. The per-stage `setup_sha256` check from ADR 0006 stays as a
  guard.
- Bad, because only `cli.py` can build the environment (ADR 0006), so the run
  ID exists only once the CLI has built the setup. The ID itself is a pure
  function of `as_of` and the setup, in the core. The Airflow DAG passes
  `--as-of` and lets the CLI derive the ID. Airflow's own `run_id` holds a
  colon, which the `RunId` pattern rejects anyway.
- Bad, because the evaluation arms share one setup by design, so they also
  share a run ID. The agent's run ID for an arm run carries the arm name, so its
  query tags stay distinct.
- Bad, because timeouts and budgets are in the config and can change outputs (a
  timed-out item lands in the human band). Raising a timeout after a failure
  starts a new run, including a new live snapshot. Successful model calls replay
  from the cache at no spend. A failed call is cached too, under a key without
  the timeout, so whether the new run retries it is owed by the `llm/` step.
