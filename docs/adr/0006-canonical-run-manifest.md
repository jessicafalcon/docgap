# 0006. Hash only the canonical part of the run manifest, and put what ran in it

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

`run_manifest.json` backs two claims: equal inputs give equal outputs, and the
evaluation arms ran under the same setup, checked by comparing manifests. Some
of its fields differ between two runs of the same inputs (run ID, timings, cache
hits, spend), so a hash over the whole file proves nothing. The inputs also
leave out the code and the dependencies. A `uv lock --upgrade` that moves
sqlglot can change fingerprints (ADR 0002), and two runs weeks apart would then
show equal inputs and different outputs, with only CI's golden tests to notice.
Which fields go into the hash, and how is what ran recorded?

## Considered Options

1. **Hash the whole manifest.** Inputs are data and config; code and dependencies go unrecorded.
2. **Canonical part with the `uv.lock` hash as an input.** Records the lockfile bytes.
3. **Canonical part with the environment by value.** The Python version, the
   installed runtime dependency closure as sorted `name==version`, and a hash
   of docgap's own package files; git SHA and timings in the operational part.
   The environment sits in a setup block with the contract version, config
   section hashes and model call sites, and the setup has its own hash.

## Decision Outcome

Chosen option: **option 3**, because it pins exactly what can change an output
and nothing else. `uv.lock` also pins dev tools, so a ruff bump would mark every
run as changed. It records what should have been installed, not what ran, and
a wheel install has no lockfile. The git SHA stays operational for the same
reason: the arms run from branches that differ only in dbt YAML, and a
docs-only commit changes the SHA without changing any output.

### Consequences

- Good, because a dependency change or a change to docgap's code between the
  baseline and the arms changes the setup hash, and the environment names the
  package that moved.
- Good, because each comparison has its own hash. `RunSetup.sha256()` checks arm
  parity, since arms share a setup but differ in outputs by design.
  `RunManifest.canonical_sha256()` checks that a re-run of the same inputs
  reproduced every output, and the pull request body cites it.
- Good, because each stage record carries the setup hash it ran under, and a
  manifest refuses a stage recorded under another setup. A resume after
  `uv lock --upgrade` re-runs the stage instead of relabeling its output.
- Bad, because the code hash covers docgap's package only, not the evaluation
  harness in `eval/`. The harness's grading rules are frozen at the
  `preregistered` tag, and its prompts are pinned by their hashes in the call
  sites.
- Bad, because a change that alters no output (a docstring, a patch release)
  still marks the environment as changed. Its answer is the golden tests: equal
  golden outputs mean the move was harmless.
- Bad, because only `cli.py` can read the installed environment, so the core
  trusts the value it is handed. The function that builds it lands with the
  Phase 4 run manifest step.
- The model-cache key stays `{model, prompt_version, state, questions}`, so a
  dependency bump never invalidates paid responses.
