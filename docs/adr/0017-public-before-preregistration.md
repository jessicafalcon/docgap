# 0017. Make the repo public now, before the `preregistered` tag

- **Status:** Accepted
- **Date:** 2026-09-28
- **Superseded by:** none

## Context and Problem Statement

ADR 0004 kept `jessicafalcon/docgap` private until the `preregistered` tag, so
the setup would not be published while it was still changing. It also recorded
that going public earlier witnesses the tag just as well: what proves the tag
predates the baseline is the `published_at` of a GitHub release on it, which
GitHub sets, not the date the repo became visible. Phase 0 is done. On
2026-09-28, a `git clone --mirror` of the GitHub repo (74 commits, the
`refs/pull/*/head` of squash-merged PRs #1–#12 included) was scanned by
`gitleaks git --redact`, and every blob and commit message by the private-terms
patterns plus home paths and email addresses. PR titles, bodies, review and
issue comments, and releases, read with `gh api`, were scanned the same way.
Nothing needed removing. Should the repo wait for the tag?

## Considered Options

1. **Keep ADR 0004.** Stay private until the tag.
2. **Public now.** Every later commit, tag and pull request is public as it lands.

## Decision Outcome

Chosen option: **option 2**, because the tag's witness is the release, not the
switch, so waiting protects nothing the protocol relies on.

### Consequences

- Good, because anyone watching sees the setup settle before the tag. The
  release's `published_at` stays the only lasting witness: commit dates come
  from the committer's clock.
- Good, because GitHub Actions on standard runners is free for a public repo
  ([Actions billing](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions),
  checked 2026-09-26), and branch protection becomes available before Phase 7
  sets it up.
- Bad, because the setup is public while it still changes, so the README says
  the project is in progress and has no results until Phase 6.
- Bad, because questions and gold SQL are public from the commit that adds them,
  not from the tag. The protocol fixes them at the tag either way, and a
  change before the tag is visible in history.
- Bad, because every push is now a publication, and only local hooks run
  before it: the gitleaks pre-commit hook on staged files, and the private-terms
  guard on staged files, commit messages and PR text sent through `gh`. CI runs
  gitleaks over the full history on pull requests and pushes to `main`, after
  the content is public, so a hit there means rotating the secret. PR text
  edited in the GitHub web UI is not checked. The pre-tag scans in
  `docs/EVAL_PROTOCOL.md` stay, because a hit there can still be fixed before
  the release pins the tagged SHA.
