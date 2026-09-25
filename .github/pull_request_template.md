## Summary

<!-- Result first: the problem and what this PR changes, in 2–4 sentences.
     For a fix, name the root cause: the exact function, value or ordering. -->

Brief step: Phase N, "<step>" · Decision: `docs/adr/NNNN-….md` or none

## Changes

<!-- One line per file or unit. Mark generated files (uv.lock, golden outputs). -->

- `path`: what changed

## Impact

<!-- Write "none" only after checking. -->

- **Evaluation:** none | questions, split, N, arms, grading or ranking inputs touched, and why that's allowed
- **Governance and privacy:** none | a new grant, contract field or snapshot column, or what now reaches disk or a model
- **Determinism:** none | a new input to a hash, cache key or run manifest
- **Compatibility:** none | a contract `schema_version`, config key or artifact shape

## Validation

<!-- Each box names a command or check and what it showed. Unticked means not done. -->

- [ ] `uv run pytest`: N passed
- [ ] `uv run pre-commit run --all-files`: all hooks pass
- [ ] Review: `docgap-reviewer` or `docgap-coherence-auditor`, its verdict, findings resolved
- [ ] CI green

## Records updated

<!-- Per the records map in CLAUDE.md, or "none implied". -->

- `file`: what changed

## Not done

<!-- Deliberate omissions, options tried and discarded, deferred checks and open
     risks, each with where it's tracked. Or "nothing". -->
