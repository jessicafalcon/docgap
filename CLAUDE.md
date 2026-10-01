# CLAUDE.md

Session instructions for docgap. The plan lives in [PROJECT-BRIEF.md](PROJECT-BRIEF.md);
this file is *how we work*, and it is the single authority on **when** to use each
skill, agent, hook and command. Skills hold the *how*. If a skill or agent
description ever disagrees with this file about when to run, this file wins, and
the disagreement is a finding to fix.

## Repo map

```text
src/docgap/        the tool: deterministic core + llm/ (the only model edge) + cli.py
eval/              questions + gold SQL, test agent, reference dictionary (never read by src/)
warehouse/dbt/     dbt project over Open DAMIR (Snowflake and DuckDB profiles)
infra/             bootstrap.sql (ACCOUNTADMIN, once) + terraform/
loader/            download, checksum, stage, COPY; offline sample and its DuckDB load
orchestration/     one Airflow DAG calling the CLI
fixtures/          DAMIR fixture, query history, manifest, ranking scope, profiles, model cache (offline mode)
runs/              one folder per `docgap analyze` run (gitignored, regenerated)
tests/             unit, golden, determinism, integrity, fault injection
docs/              EVAL_PROTOCOL.md, adr/, RESULTS.md (generated)
.claude/           settings, hooks/, skills/, agents/
.github/           CI workflow, PR template
```

## Principles, in priority order

1. **Evaluation integrity.** Nothing that feeds the ranking sees the holdout;
   pre-registered choices don't move after the `preregistered` tag.
2. **Determinism.** Same inputs by hash ⇒ identical canonical outputs. No clock,
   env or randomness in the core; the model only in `llm/`, always cached.
3. **Least exposure.** Read-only tool, redaction before disk, aggregate-only
   evidence, fail closed.
4. **Designed for failure.** Idempotent, resumable stages; atomic writes;
   failures recorded per item, never lost.
5. **Small, typed, tested.** One job per module, one check per non-trivial unit,
   small green commits.

## What to invoke, when

Follow this in order for every piece of work. "Skill" means invoke it with the
Skill tool *before* starting that kind of work, even if you remember its content:
the file is the current standard, and memory of it may be stale.

| Moment | Invoke | Then |
|---|---|---|
| **Fresh clone** | `uv sync`, `uv run pre-commit install` (the pre-commit and commit-msg hooks); gitleaks 8.30.1 (the version CI pins) on `PATH` | The `gitleaks-system` hook needs it, or every commit fails |
| **Offline data** needed (DuckDB work) and `data/sample/` is missing | `uv run python loader/offline_sample.py`, with the sources in `data/open_damir/` | It checks sources and outputs against the locks; `--update-lock` only under an ADR that supersedes 0013 |
| **Offline warehouse** needed and `data/warehouse/<source>/RAW.duckdb` is missing | `uv run python loader/load_duckdb.py sample` (or `fixture`, the CI fixture) | It checks each file against `loader/sample.lock` and its header against `loader/raw_prestations.sql`; a re-run loads nothing twice |
| **Session start** | Read "Current status" below and the brief's current phase | Resume from the next step listed there |
| **Planning** a phase's PR split, a design change, or anything touching the evaluation design | Skill `devils-advocate` on the plan | Bring me its verdict and "the one thing" before building |
| **Writing** Python, SQL, dbt, Terraform or the DAG; choosing a dependency | Skill `docgap-craft` | |
| ↳ and the change is in `src/`, `eval/`, dbt models or grants | + skill `docgap-correctness` | |
| ↳ and it calls Snowflake, the API, GitHub or a download, writes outside its stage directory, or changes the DAG | + skill `docgap-resilience` | |
| **Writing or changing a test**, or deciding what a change must prove | Skill `docgap-tests` | |
| **Writing prose**: a commit message, comment, docstring, ADR, PR body, or any `.md` | Skill `docgap-voice` | |
| **Every edit and write** | Hooks run automatically (table below) | Act on what they report before moving on |
| **A decision is taken or something changes** | The records rules below | Update the owning file in the same PR |
| **Before each commit** | `uv run pytest` and `uv run pre-commit run --all-files` | Commit only when both are green |
| **Reading across many files or pages** where only the conclusion matters | Skill `docgap-efficiency`, then a subagent | Keep the conclusion, not the dumps |
| **A result comes back shallow** (a missed finding, a weak root cause, a hand-wavy estimate) | Raise effort from `high` to `xhigh` for that task only | Back to `high` after; `high` is the default for everything, agents included |
| **A breakpoint** when the session has grown long (usually right after a merge) | Give me a kickoff prompt for a fresh session instead of continuing | I start the new session; no `/compact`. Never mid-task |
| **Before opening a PR** | The pre-PR gate below | |
| **After each PR and each merge** | Update "Current status" | In the PR itself; after a merge, in the next PR's first commit |
| **A durable, non-obvious fact** learned (a vendor limit, a user preference) | Project memory | Not what the repo or git history already records |

### The pre-PR gate

In this order. Stop at the first step that fails, fix, and restart from there;
after any fix, step 1 always runs again.

1. `uv run pytest` and `uv run pre-commit run --all-files` are green. If `infra/`
   changed: `terraform fmt -check && terraform validate`. If `warehouse/` changed:
   `dbt parse` and `docgap lint`.
2. Cleanup review with the four `/simplify` angles (reuse, simplification,
   efficiency, altitude), sized by what the PR changes; fix or answer the findings:
   - **Docs only** (every changed path is `*.md`): skip it; the auditor in step 3
     covers duplication.
   - **Code** (any changed `*.py`, `*.sql`, `*.tf` or dbt YAML, hooks included):
     one subagent covering all four angles.
   - **Anything else** (config, CI, settings): run the four angles yourself, no
     subagents.
3. The review agent for this PR:
   - **Not docs only:** `@agent-docgap-reviewer` on the branch.
   - **Docs only** (every changed path is `*.md`): `@agent-docgap-coherence-auditor`,
     scoped to the changed docs.
   - **Last PR of a phase:** also `@agent-docgap-coherence-auditor` over the whole repo.

   Give the agent the diff range, the brief step's line range and the rules at
   stake; it reads further only when a finding needs it.
4. Fix or answer every blocker and major finding, and verify each fix yourself.
   Re-run the step 3 agent only if a blocker needed a design change. Apply every record
   update the agents list; bring me each one marked **decide**.
5. Skill `docgap-pr` for the title and body, built from `.github/pull_request_template.md`;
   skill `docgap-voice` for the wording.
6. "Current status" is moved forward in this PR.
7. Push, open the PR, and **stop.** I review it; on my "merge", squash-merge it,
   pull `main` and continue with the next step, or, if the session has grown long,
   give me a kickoff prompt for a fresh one.

A phase is done when its "Done when" holds **and** the whole-repo audit has no
blockers, with its record updates applied.

## Skills

| Skill | Holds |
|---|---|
| `docgap-craft` | Code idioms: stage shape, typing, contracts, Polars, SQL, dbt, Terraform, Airflow, tooling |
| `docgap-correctness` | The five invariants, boundary validation, the review checklist |
| `docgap-resilience` | Failure classes, idempotency, atomic writes, timeouts, budgets, gates, locks |
| `docgap-tests` | Test forms, determinism, integrity, contract and fault-injection tests |
| `docgap-voice` | Commits, PRs, comments, docstrings, Markdown, decision records |
| `docgap-pr` | PR size, title, body sections |
| `docgap-efficiency` | Delegation, cached context, memory, runtime caching |
| `devils-advocate` (user-level) | Calibrated critique of a plan before it's built |

## Agents

Both are read-only (a hook limits their shell to checks), run on a fresh context,
and report findings plus the record updates they imply. Neither edits. When they
disagree with the code or a record and can't tell which is right, they mark it
**decide** and I choose.

| Agent | When | Scope |
|---|---|---|
| `docgap-reviewer` | Gate step 3, every code PR | The branch diff: coherence with the brief, records, invariants, failure design, tests |
| `docgap-coherence-auditor` | Gate step 3: docs-only PRs, and the last PR of each phase | Docs-only PRs: the changed docs. Phase exit: the whole repo, for cross-artifact drift, architecture erosion, stale records, readiness for the next phase and the trial calendar |

## Hooks (automatic) and what to do when one fires

| Hook | Fires on | When it reports |
|---|---|---|
| `private-terms-guard` | Before any write in the repo (not files outside it); before commits, tags and PR/issue/release text, with the files they name by `-F` or `--body-file`; and through pre-commit on staged files and every commit message | Blocked. Rewrite without the term: describe the fact itself, not its source. A named file it can't read blocks too: give an absolute path, not a variable set in the same command. A stdin body (`-F -`) is read only as a heredoc; piped or redirected stdin blocks. The list lives in the gitignored `.claude/private-terms.local`. |
| `ruff-on-edit` | After editing a `*.py` file | Fix any unresolved lint it prints before the next edit. |
| `determinism-guard` | After editing core `src/docgap/*.py` (not `llm/`, `cli.py`, tests) | Inject the value as a parameter (`as_of`, seed, config), wrap listings in `sorted()`, use `hashlib`, or move the code to `llm/` or `cli.py`. Pre-commit runs the same check. |
| `reviewer_bash_allowlist` | Every Bash call; enforced only when `agent_type` is one of the two review agents | Keeps them read-only. Registered in `.claude/settings.json`, because Claude Code skips agent frontmatter hooks in an untrusted folder, and in each review agent's frontmatter with `--enforce` as a backup. |

## Records: every change or decision updates its owner

Each kind of fact has one owning file. A change or decision updates that file
**in the same PR**. A decision that lives only in chat, a commit message or the
code is lost to the next session, and the review agents treat it as a finding.

| Fact | Owner |
|---|---|
| The plan: objective, phases and steps (checkboxes), "Done when", stack, timeline, risks, open decisions | `PROJECT-BRIEF.md` |
| How we work, and when to invoke each skill, agent, hook and command; current status | `CLAUDE.md` |
| Why a non-obvious choice was made, and what else was considered | `docs/adr/NNNN-*.md`, from `docs/adr/template.md` |
| Evaluation rules, pre-registered at the `preregistered` tag (changes per "After `preregistered`" below) | `docs/EVAL_PROTOCOL.md` |
| How code, tests and prose are written | `.claude/skills/docgap-*` |
| What a user sees: quickstart, results (generated), limits | `README.md` |

- **A step lands:** tick its checkbox in the brief; move "Current status".
- **Work owed by a later step** (found while building another): a line in the
  brief step that owes it, in the same PR. "Current status" holds only the
  decisions open for the next PRs.
- **A decision is taken:** write an ADR, write the outcome into every part of the
  brief it affects (as the current design, not as a change), and remove it from
  "Open decisions".
- **A threshold, key or command changes:** update every record that states it.
- **A new skill, agent, hook or slash command:** add it here, with when to invoke
  it. A `docgap` command is product behaviour, and the README documents it.
- **After `preregistered`:** questions, gold SQL, grading rules, split, N, arms,
  the agent (model, prompt version, 8 tool calls, `[agent]` limits), and the
  values that pick the arms' content (the rank weight, gate bands, *k*, seeds,
  `[actors]`, `[manifest]`, the history window, and the model and prompt version
  at the attribution, drafter and gate call sites) never change. A change to the
  snapshot, manifest, resolve, usage, rank or evidence code that changes its
  golden outputs, and any other `EVAL_PROTOCOL.md` change,
  needs an ADR with a justification, and the results report the deviation.
  Other files point here rather than restating the list.
- **Code and a record disagree** and it's unclear which is right: ask me. Never
  make one match the other silently.

## Git

- One coherent change per branch and PR (`<type>/<slug>`), squash-merged: one
  behaviour change, or related changes to the same area that land together. A phase
  is usually several PRs in step order. Start from an up-to-date `main`.
- Branch commits are small and green; the PR title becomes the commit on `main`.
- Confirm before force-push, history rewrite, creating the GitHub repo, changing
  its visibility, or any first push.

## Communication

Result first: what changed, passed or failed, then details. Plain, short
sentences. After a task: files touched, commands run and results, records
updated, open risks, next step.

## Current status

Update after every PR and merge, in the same change. A new session resumes from here.

- **Phase:** 0 is done (#1–#11), and so is 4 (#12, #14, #15): each one's "Done
  when" holds and its phase-exit audit found no blockers. Phase 2's offline block
  (the brief's timeline row 3) is in progress. Its "Done when" needs Snowflake, so
  the phase and its exit audit close in timeline row 7. Phase 1 waits for the
  offline pilot, per the brief's timeline.
- **Repo:** `jessicafalcon/docgap` on GitHub, public since 2026-09-28 (ADR 0017),
  so every push is a publication. Squash merges only, with the PR title as the
  commit title. No branch protection yet; Phase 7 sets it up. The in-progress
  README merged in #13; Phase 8 replaces it.
- **Open PRs:** none.
- **Phase 2 offline PR order** (approved after `devils-advocate`):
  - **PR 1:** `feat/raw-load`, merged (#16). The typed RAW DDL (one spec for
    DuckDB and Snowflake; only `PRS_ACT_NBR` and `FLT_ACT_NBR` nullable), the
    offline DuckDB load over `data/sample/` or `fixtures/damir/`, and
    `loader/profile_sources.py`. It also moves the baseline docs lock after
    Phase 3's offline pilot (ADR 0021).
  - **PR 2:** `feat/staging-fact-lint`, in progress. The dbt project on DuckDB, the staging
    model (one row per source line: the full three months repeat no grain key), the
    contracted `fct_reimbursements`, `docgap lint` calling `load_marts`, and CI
    `dbt build` over `fixtures/damir/`.
  - **PR 3:** `feat/dims-agg`. Seeds (code→label pairs only), the 4 dimensions
    with the dictionary's missing codes and a cap on their share of fact rows,
    and `agg_monthly_spend_by_category`, whose category needs a decision and an
    ADR, brought to review first.
  - **Later, before the go/no-go:** `feat/loader-snowflake` (download, PUT/COPY,
    Snowflake profile, `persist_docs` check), tested offline and run in the
    trial.
- **Next step:** finish PR 2, `feat/staging-fact-lint`, and open it for review.
