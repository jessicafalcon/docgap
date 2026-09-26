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
loader/            download, checksum, stage, COPY
orchestration/     one Airflow DAG calling the CLI
fixtures/          frozen snapshot, manifest, profiles, model cache (offline mode)
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
| **Fresh clone** | `uv sync`, `uv run pre-commit install`; gitleaks 8.30.1 (the version CI pins) on `PATH` | The `gitleaks-system` hook needs it, or every commit fails |
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
| **A breakpoint** in a long session (a PR merged, exploration done) | Give me a kickoff prompt for a fresh session | I start the new session; no `/compact`. Never mid-task |
| **Before opening a PR** | The pre-PR gate below | |
| **After each PR and each merge** | Update "Current status" | In the same change |
| **A durable, non-obvious fact** learned (a vendor limit, a user preference) | Project memory | Not what the repo or git history already records |

### The pre-PR gate

In this order. Stop at the first step that fails, fix, and restart from there.

1. `uv run pytest` and `uv run pre-commit run --all-files` are green. If `infra/`
   changed: `terraform fmt -check && terraform validate`. If `warehouse/` changed:
   `dbt parse` and `docgap lint`.
2. Cleanup review with the four `/simplify` angles (reuse, simplification,
   efficiency, altitude), sized to the PR; fix or answer the findings:
   - **Docs or process only:** skip it; the auditor in step 3 covers duplication.
   - **Config or tooling:** run the four angles yourself, no subagents.
   - **Code in `src/` or `eval/`:** one subagent covering all four angles.
3. The review agent for this PR:
   - **Code changed:** `@agent-docgap-reviewer` on the branch.
   - **Docs only** (every changed path is `*.md`): `@agent-docgap-coherence-auditor`,
     scoped to the changed docs.
   - **Last PR of a phase:** also `@agent-docgap-coherence-auditor` over the whole repo.

   Give the agent the diff range, the brief step's line range and the rules at
   stake; it reads further only when a finding needs it.
4. Fix or answer every blocker and major finding, and verify each fix yourself.
   Re-run the agent only if a blocker needed a design change. Apply every record
   update the agents list; bring me each one marked **decide**.
5. Skill `docgap-pr` for the title and body, built from `.github/pull_request_template.md`;
   skill `docgap-voice` for the wording.
6. "Current status" is moved forward in this PR.
7. Push, open the PR, and **stop.** I review it; on my "merge", squash-merge it,
   pull `main` and continue with the next step.

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
| `docgap-coherence-auditor` | Gate step 3: docs-only PRs, and the last PR of each phase | Whole repo: cross-artifact drift, architecture erosion, stale records, readiness for the next phase and the trial calendar |

## Hooks (automatic) and what to do when one fires

| Hook | Fires on | When it reports |
|---|---|---|
| `private-terms-guard` | Before any write in the repo, and before commits, tags, PR/issue/release text (never files outside the repo; until `fix/hooks`, not `-F`/`--body-file` contents either) | Blocked. Rewrite without the term: describe the fact itself, not its source. The list lives in the gitignored `.claude/private-terms.local`. |
| `ruff-on-edit` | After editing a `*.py` file | Fix any unresolved lint it prints before the next edit. |
| `determinism-guard` | After editing core `src/docgap/*.py` (not `llm/`, `cli.py`, tests) | Inject the value as a parameter (`as_of`, seed, config), wrap listings in `sorted()`, use `hashlib`, or move the code to `llm/` or `cli.py`. Pre-commit runs the same check. |
| `reviewer_bash_allowlist` | Shell commands inside the two review agents | Keeps them read-only. Not used in the main session. |

## Records: every change or decision updates its owner

Each kind of fact has one owning file. A change or decision updates that file
**in the same PR**. A decision that lives only in chat, a commit message or the
code is lost to the next session, and the review agents treat it as a finding.

| Fact | Owner |
|---|---|
| The plan: objective, phases and steps (checkboxes), "Done when", stack, timeline, risks, open decisions | `PROJECT-BRIEF.md` |
| How we work, and when to invoke each skill, agent, hook and command; current status | `CLAUDE.md` |
| Why a non-obvious choice was made, and what else was considered | `docs/adr/NNNN-*.md` |
| Evaluation rules, pre-registered at the `preregistered` tag (changes per "After `preregistered`" below) | `docs/EVAL_PROTOCOL.md` |
| How code, tests and prose are written | `.claude/skills/docgap-*` |
| What a user sees: quickstart, results (generated), limits | `README.md` |

- **A step lands:** tick its checkbox in the brief; move "Current status".
- **A decision is taken:** write an ADR, write the outcome into every part of the
  brief it affects (as the current design, not as a change), and remove it from
  "Open decisions".
- **A threshold, key or command changes:** update every record that states it.
- **A new skill, agent, hook or command:** add it here, with when to invoke it.
- **After `preregistered`:** questions, gold SQL, grading rules, split, N, arms
  and the agent model never change. Any other `EVAL_PROTOCOL.md` change needs an
  ADR with a justification, and the results report the deviation. Other files
  point here rather than restating the list.
- **Code and a record disagree** and it's unclear which is right: ask me. Never
  make one match the other silently.

## Git

- One behaviour change per branch and PR (`<type>/<slug>`), squash-merged. A phase
  is usually several PRs in step order. Start from an up-to-date `main`.
- Branch commits are small and green; the PR title becomes the commit on `main`.
- Confirm before force-push, history rewrite, creating the GitHub repo, or any
  first push.

## Communication

Result first: what changed, passed or failed, then details. Plain, short
sentences. After a task: files touched, commands run and results, records
updated, open risks, next step.

## Current status

Update after every PR and merge, in the same change. A new session resumes from here.

- **Phase:** 0 (foundations and pre-registration), in progress.
- **Repo:** `jessicafalcon/docgap` on GitHub, private until the project is
  complete, then public. No branch protection (not available on a private repo).
- **Merged:** #1 `build/tooling-and-ci`, as a merge commit: a one-off. The repo
  now allows squash merges only, with the PR title as the commit title.
  #2 `docs/pr-template`.
- **Open PRs:** `docs/lean-gate` (the gate sized by PR type; fewer, larger PRs).
- **Phase 0 PR order** (approved; PRs 2–3 and 8–9 merged into one each to cut
  gate runs):
  1. ~~`build/tooling-and-ci`~~ (#1), then ~~`docs/pr-template`~~ (#2) and
     `docs/lean-gate`.
  2–3. `fix/hooks`: the determinism guard flags listings and `.sample()` on any
     receiver, plus `os.getcwd()` and `Path.cwd()`, with tests for every rule;
     the private-terms guard covers staged files, commit messages, `-F` and
     `--body-file` files, `git -C`. Then drop the `--body-file` caveats in the
     hooks table and `docgap-pr` (Body).
  4. `docs/decision-log`: ADR template with Status and Superseded-by; first records,
     including repo visibility (private until complete, then public).
  5. `feat/contracts`: `models.py`, one committed schema per contract;
     `RunManifest` splits a canonical part from an operational part.
  6. `feat/config`: `docgap.toml`, no defaults in code, canonical hash per section.
  7. `docs/eval-protocol`: every brief item, plus pinned split, random-N pool,
     N rounding, bootstrap settings, pilot fallthrough; devils-advocate first.
  8–9. `chore/data`: first commit, CI fails if `src/` references
     `eval/reference/`; then measured file facts with their commands,
     `loader/sources.lock`, the dictionary in `eval/reference/`, months ADR.
  10. `feat/offline-sample`: sampling by hashed dimension key, tests, ADR with
      the output hash.
- **Open for PR 7:** (a) the two-band fallback: merge "ready" and "confirm"
  (arm membership unchanged, recommended) or absorb the flagged band;
  (b) report delivered drafts per arm next to the headline (recommended).
- **Owed by the first local fake server** (fault-injection tests): a test that a Unix
  socket and a marker-opted localhost server still work under `--disable-socket`.
- **Next step:** PRs 2–3 `fix/hooks`, after `docs/lean-gate` merges.
