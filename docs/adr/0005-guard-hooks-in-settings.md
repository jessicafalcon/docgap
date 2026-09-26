# 0005. Register every hook that must always run in `.claude/settings.json`

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

The review agents are read-only because `reviewer_bash_allowlist.py` limits
their shell to checks. It was registered only in each agent's frontmatter. In
an earlier session the agents ran chained commands (`;`, `|`, `&&`) because the
hook never fired: Claude Code 2.1.283 skips agent frontmatter hooks in a folder
that isn't trusted yet, and says nothing. Hooks in `.claude/settings.json` still
ran in that session. Where must a hook be registered to be sure it runs?

## Considered Options

1. **Frontmatter only.** The hook sits with the agent it guards; it is skipped
   in an untrusted folder.
2. **Settings only.** The hook runs on every Bash call and enforces only when
   the payload's `agent_type` names a review agent.
3. **Settings, plus a frontmatter backup with `--enforce`.** As option 2, and
   the frontmatter copy blocks unconditionally inside the agent.

## Decision Outcome

Chosen option: **option 3**, because the settings registration survives an
untrusted folder, and the frontmatter backup still blocks if a payload arrives
without `agent_type`. The rule is general: any hook that must always run is
registered in `.claude/settings.json`, whatever else registers it. A later
release that runs frontmatter hooks in untrusted folders doesn't remove the
settings registration.

### Consequences

- Good, because the allowlist no longer depends on folder trust, and tests pin
  both registrations.
- Bad, because the allowlist starts a Python process on every Bash call in
  every session, including the main one, where it exits without checking.
- Bad, because two registrations must change together. Any change to the
  allowlist's registration or to a review agent's name must land in all of:
  - `.claude/settings.json`
  - `.claude/agents/docgap-reviewer.md`
  - `.claude/agents/docgap-coherence-auditor.md`
  - `REVIEW_AGENTS` in `.claude/hooks/reviewer_bash_allowlist.py`
  - `tests/hooks/test_reviewer_bash_allowlist.py`
  - the `reviewer_bash_allowlist` row of the hooks table in `CLAUDE.md`
