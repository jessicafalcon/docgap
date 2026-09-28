# 0004. Keep the repo private until the `preregistered` tag, then make it public

- **Status:** Superseded
- **Date:** 2026-09-26
- **Superseded by:** [0017](0017-public-before-preregistration.md)

## Context and Problem Statement

docgap's headline rests on pre-registration: questions, gold SQL, split, N and
arms are fixed at the git tag `preregistered`, before any baseline run. That
claim can be checked only if someone outside the repo can see the tag before
the results exist. Git commit and tag dates are set by the committer, so the
history alone proves nothing about when. Before the tag, the repo holds setup
work that is still changing. When does `jessicafalcon/docgap` on GitHub become
public?

## Considered Options

1. **Public from the first commit.**
2. **Private until the `preregistered` tag, then public.**
3. **Private until the project is complete** (Phase 8), then public.
4. **Private permanently.** Share results without the code.

## Decision Outcome

Chosen option: **option 2**, because the tag is published before any baseline
run exists, so the order that pre-registration claims is visible outside the
repo. Option 3 leaves the tag's date resting on the committer's clock, and
option 4 fails the objective, since nobody could rerun a number. Option 1 would
witness the tag too, but adds nothing to that claim and publishes the setup
while it is still changing.

### Consequences

- Good, because from the tag on, the Phase 7 branch protection and `CODEOWNERS`
  can be enforced, and GitHub Actions on standard runners is free for a public
  repo ([Actions billing](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions),
  checked 2026-09-26).
- Bad, because the whole history before the tag becomes public at once, every
  commit message included. Before the switch, gitleaks runs over the full
  history and the private-terms guard over every commit message.
- Bad, because going public proves the tag existed at that moment only to
  someone who looks then. How the publication is recorded for later readers is
  left to `docs/EVAL_PROTOCOL.md`.
- Bad, because until the tag the repo has no branch protection (GitHub didn't
  offer it for this private repo when it was created), so the pre-PR gate holds
  by practice, and Actions minutes count against the account's 2,000-minute
  monthly quota.
- Bad, because from the tag to Phase 8 the repo is public with partial results,
  so the README says the project is in progress until then.
