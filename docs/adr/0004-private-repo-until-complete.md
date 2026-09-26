# 0004. Keep the repo private until the project is complete, then make it public

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

`jessicafalcon/docgap` on GitHub needs a visibility while it is built. The
project's claims are checked by rerunning it: the README's numbers are
generated, and a fresh clone must reproduce them offline. Until the last phase,
the repo holds half-built stages and no results. Who can read it, and when?

## Considered Options

1. **Public from the first commit.** Every step, including the
   `preregistered` tag, is visible as it lands.
2. **Private until complete, then public.** "Complete" is the brief's Phase 8
   "Done when": a fresh clone reproduces every README number offline.
3. **Private permanently.** Share results without the code.

## Decision Outcome

Chosen option: **option 2**, because the first public state is then one in
which every claim in the README can be rerun and checked. Permanently private
fails the project's objective, since nobody could verify a number.

### Consequences

- Good, because readers never meet a README whose numbers are missing or
  stale.
- Bad, because GitHub offers no branch protection on a private repo on this
  plan, so the pre-PR gate in `CLAUDE.md` holds by practice, not by
  enforcement.
- Bad, because GitHub Actions minutes on a private repo count against the
  account's monthly quota; public repos run on standard runners for free.
- Bad, because while the repo is private nobody outside it witnesses when the
  `preregistered` tag was made, and git commit dates are set by the committer.
  How the tag's date is made checkable is open for `docs/EVAL_PROTOCOL.md`.
