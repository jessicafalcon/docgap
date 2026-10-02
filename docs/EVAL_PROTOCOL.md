# Evaluation protocol

This protocol fixes how docgap's claim is tested before any agent run whose
results count: the questions, the grading rules, the split, the arms, N, the
statistics and what gets reported. It is frozen at the git tag `preregistered`.
What may change after the tag, and how, is set in `CLAUDE.md` → "After
`preregistered`"; a change is reported as a deviation in the results.

## The claim

On questions docgap never saw, documenting docgap's top N undocumented columns
improves the test agent's accuracy more than documenting N random undocumented
columns. The claim holds only if the paired bootstrap interval for holdout
accuracy, top-N minus random-N, lies above zero (see [The headline](#the-headline)).
The result is reported whatever it turns out to be.

## Questions

1. **40 questions** in `eval/questions.yml`, each with `id`, English text,
   `gold_sql`, an `ordered` flag and a category. The columns a question needs
   come from running `resolve` on its gold SQL, never from a hand-written list.
2. **Gold SQL is gradable.** Its result is deterministic (an ordered question
   orders by keys that leave no ties, and no `LIMIT` cuts through ties), has at
   most 200 rows (the agent's row cap) and at most 5 columns (the grader's
   permutation bound), holds scalar values only (no list or struct), and reads
   only `ANALYTICS.MARTS`.
3. **12 pilot questions** are separate from the 40. They choose the agent
   model (see [The agent model](#the-agent-model)), and the pilot settles the
   difficulty and the mart columns the 40 are then written for. None of the 40
   repeats a pilot question's ID or text (ADR 0024).
4. **Written before the baseline docs.** `warehouse/baseline_docs.lock` is drawn
   only once the offline pilot passes the kill criterion, so the pilot's changes to
   the setup, and the 40 questions with their gold SQL, come first. The lock lands in
   its own pull request, merged after the questions' pull request, and GitHub's
   merge times witness the order, since commit dates prove nothing (ADR 0021).

## The split

25 discovery and 15 holdout questions. Each question ID is hashed as
`sha256(f"{seed}:{qid}")` with `seed` = `[seeds] split` = 1 in `docgap.toml`;
the IDs are sorted by that hex digest, and the first 25 are discovery. The seed
was set before any question existed. The results report how many needed
columns the two sets share.

## Runs

1. **The agent.** The model the pilot chose, at the call site `agent`, pinned by
   ID, at default sampling, with a versioned system prompt fixed at the tag. Its
   tools are
   `list_tables()`, `describe(table)` and `run_sql(sql)`, the last with the
   statement timeout and row cap in `[agent]` (60 s, 200 rows). After at most 8
   tool calls it gives a structured final answer `{final_sql}`.
2. **Repetitions.** 3 per question per arm, numbered 1 to 3. The repetition
   number is part of the agent's model-cache key, so each repetition is its own
   draw rather than a replay of the first. The arm is not part of the key: two
   arms share a cached response only while the agent's transcripts are
   identical, and they diverge at the first tool result that differs.
3. **The result.** The harness runs `final_sql` once as `SVC_AGENT` with the
   same timeout and the agent's query tag, `agent:<run_id>:<qid>:<rep>`,
   fetching at most 201 rows, so a result longer than any gold result fails as
   `row_count_mismatch`.
4. **Infrastructure failures.** An API error left after the retry policy, or an
   unavailable warehouse, is not an agent outcome: it is not cached, and the run
   repeats. The retry policy is `[llm]`'s: each model call times out after
   `timeout_seconds` (450 s), and a connection error, a timeout, 408, 409, 429 or
   5xx is retried `max_retries` (2) times. After `[agent] run_attempts` (3)
   attempts the run fails with reason `error`, and the results report the count
   per arm (ADR 0028).

## What "correct" means

The grader executes nothing. It compares the agent's result with the stored
gold result, and a run passes only if every check holds. Checks run in this
order, and the first that fails gives the reason code:

| Check | Rule | Reason code |
| --- | --- | --- |
| The run produced a result | No final answer within 8 tool calls, a SQL error, or, offline, a final answer sqlglot can't transpile | `error` |
| | The statement timeout was reached | `timeout` |
| Same shape | The result has exactly the gold's number of columns; names are ignored | `shape_mismatch` |
| Same row count | Exactly the gold's number of rows | `row_count_mismatch` |
| Same values | For some permutation of the result's columns (at most 5! = 120), the rows equal the gold's: as sequences if `ordered`, as multisets otherwise | `value_mismatch` |

Values are normalized before the comparison:

- **Numbers** of any type become `Decimal`, a float through its shortest
  round-trip string (`Decimal(repr(x))`), then round to 2 places with
  `ROUND_HALF_EVEN`, so a FLOAT 2.675 and a NUMBER 2.675 both give 2.68. A
  number never equals a string. A non-finite number equals only the same one:
  NaN equals NaN, and infinity never equals minus infinity.
- **Booleans** equal booleans only: TRUE is not the number 1.
- **Strings** are trimmed of leading and trailing whitespace; case is kept.
- **Dates, times and timestamps** compare as ISO 8601 strings, so they equal a
  string of the same text; a timestamp keeps its offset.
- **NULL** equals NULL and nothing else.
- **A list or a struct** in the agent's result equals no gold value, since gold
  results hold scalars only.
- **Any other type** is a harness error, never a grade.

## The arms

Every arm runs all 40 questions, 3 repetitions each, under one setup hash
(`RunSetup.sha256()`: model IDs, prompt versions, sampling, config sections,
environment). The comparison refuses to run if two arms' manifests differ in it.

| Arm | Column docs the agent reads |
| --- | --- |
| **Baseline** | The locked 50% of mart columns: the M column FQNs sorted by `sha256(f"{seed}:{fqn}")` with `seed` = `[seeds] baseline_docs` = 3, the first `floor(M / 2)` documented, frozen in `warehouse/baseline_docs.lock` after the pilot (ADR 0021) |
| **Top-N** | Baseline plus docgap's drafts for its top N columns |
| **Random-N** | Baseline plus drafts, from the same drafter and gate, for N random undocumented columns |
| **Ceiling** (optional) | Every mart column documented from the dictionary |

All arms, the baseline included, run in one session, in this recorded order:
baseline, random-N, top-N, ceiling. For each arm, `dbt build` runs first, then
the check that `information_schema` comments as `SVC_AGENT` equal that arm's
YAML, then the agent. "The baseline" in every comparison and in the README is
this session's. The Phase 3 baseline, run earlier under an earlier setup,
supplies the query traffic the ranking reads and is reported next to it; it is
not compared with the arms. Where the warehouse returns the same
tool results, the session's baseline replays it from the model cache, so a
difference between the two shows drift.

## N

`U` is the number of mart columns that are undocumented in the baseline docs
and touched by the discovery gold SQL, by `resolve`. Then:

```text
N = min(10, floor(U / 2))
```

N is computed and committed before the tag. If N = 0, there is no top-N versus
random-N comparison to run, and that is reported as the finding.

## Top-N and random-N

1. **Top-N** is the first N rows of docgap's ranking: only columns with no
   description, sorted by score descending, then by column FQN. The rank weight
   is *w* = 1, so a column whose attributed failure rate is 1 scores twice its
   usage term. The ranking holds every mart column undocumented in the baseline
   docs; one no counted query touched scores 0, so if fewer than N columns were
   touched, top-N ends with untouched columns in FQN order (ADR 0020).
2. **Ranking inputs come from discovery questions of one agent run only.** Usage
   counts only queries tagged with the Phase 3 baseline's run ID and a discovery
   `qid`, and failure attribution runs only on that run's discovery failures, so
   *u* and *r* read the same runs as the grades. Agent-tagged traffic with no
   scope fails the stage, and so does a scope no query matches (ADR 0019). A test
   feeds in a holdout-tagged query, and another run's query, and fails if any
   ranking input changes.
3. **The random-N pool** is every mart column undocumented in the baseline docs,
   whether or not a question touches it, because the claim compares docgap
   with documenting undocumented columns without it. Each column's FQN is
   hashed as
   `sha256(f"{seed}:{fqn}")` with `seed` = `[seeds] random_arm` = 2, and the N
   columns with the lowest digests are drawn. The draw ignores top-N, so the two
   can overlap; the results report the overlap.
4. **One random draw.** The interval covers question-to-question variation, not
   how a different draw would have done. The results state this as a limit.

## Drafts in the arms

1. **Unedited.** Ready and confirm-band drafts go into the arms as written;
   flagged items get no text. Owner review happens on the pull request and is
   reported as an edit rate, outside the arms.
2. **An arm is the N columns chosen**, whether or not each got a draft. The
   results report delivered drafts per band and per arm next to the headline,
   since random columns can be flagged more often than top-N columns.
3. **The two-band fallback.** If the gate's scores cluster and the per-band
   accuracy on the blind labels isn't monotone, ready and confirm merge into one
   band. `confirm_min`, the cut between drafted and flagged, stays, so the arms'
   contents don't change. The fallback is part of this protocol, not a change
   to the bands.

## Values that pick the arms' content

These decide which columns enter the arms and what text they get. The Phase 3
baseline shows holdout failures before the arms run, so they never change after
the tag (`CLAUDE.md` → "After `preregistered`"):

- the rank weight *w* = 1 (`[rank]`)
- the gate bands, 0.8 and 0.5 (`[gate]`)
- *k* = 11 (`[evidence]`)
- each mart column's `meta.sensitivity` tag, which decides whether its evidence
  carries values (ADR 0022)
- the role-to-actor mapping (`[actors]`)
- the history window the ranking reads (`[snapshot] history_window_days`)
- the mart database and schema whose columns can be ranked or drawn (`[manifest]`)
- the model at the `attribution`, `drafter` and `gate` call sites
  (`[call_sites]`) and their prompt versions (in `llm/`), both recorded in the
  run's setup
- the seeds, including the bootstrap seed below (`[seeds]`)
- the every-column docs text and the script that writes it from the dictionary,
  which give the ceiling arm its text and the baseline its locked half (ADR 0025)

## The headline

1. **Per question**, an arm's score is its pass rate over the 3 repetitions.
   An arm's accuracy on a split is the mean of its question scores.
2. **The difference.** For each holdout question, `d = top-N score − random-N
   score`. The headline is the mean of `d` over the 15 holdout questions.
3. **The interval.** 10,000 bootstrap resamples of the 15 holdout questions,
   with replacement, each giving the mean of `d`. With the holdout questions
   sorted by ID, resample `j` (0 to 9,999) takes at position `i` (0 to 14) the
   question at index `int(sha256(f"{seed}:{j}:{i}"), 16) % 15`, with `seed` =
   `[seeds] bootstrap` = 4. The interval runs from the 250th to the 9,751st of
   the 10,000 means, sorted ascending.
4. **The verdict.** Top-N improves on random-N if the lower bound is above zero,
   and does worse if the upper bound is below zero. Otherwise no difference is
   detected. No p-values.
5. **What the design can detect.** With 15 holdout questions and 3
   repetitions, a true difference of about 28 points is detected more than 80%
   of the time, and one of about 9 points about 16% of the time (ADR 0008). An
   interval that includes zero means no difference of that size was detected,
   not that the ranking doesn't matter.

## The agent model

The pilot runs the 12 pilot questions, 3 repetitions each, on DuckDB over the
offline sample pinned by `loader/sample.lock` (ADR 0013), in four configurations:
{Haiku 4.5, Opus 5.5} × {no column docs, every column documented from the
dictionary}. The every-column text is the dictionary's, verbatim and in French: it
is the ceiling arm's text, and its locked half is the baseline's (ADR 0025). The
agent writes Snowflake SQL, as in the trial, and the harness transpiles it to
DuckDB (ADR 0014). A final answer that can't be transpiled is a failed run, and
the pilot reports how many there were. For each model, the gap is full-docs
accuracy minus no-docs accuracy.

1. **Eligible:** full-docs accuracy between 50% and 90% inclusive, and a gap of
   at least 15 points.
2. **Choice:** the eligible model with the larger gap; on an equal gap, Haiku
   4.5, the cheaper.
3. **Kill criterion:** if neither model is eligible, change the setup (harder
   questions, more coded columns) and run the pilot again, at most twice;
   question difficulty is adjusted only here. If no model is eligible after the
   third pass, that is the finding, and it is reported (ADR 0024).

The four accuracies and the choice go in their own decision record. The model
not chosen is reported under limits and doesn't run as an arm. The 15-point
gap is well below what the headline can detect, so passing the pilot doesn't
promise a detectable headline.

## Reported regardless of outcome

- The headline difference, its interval and the verdict, including when
  random-N matches or beats top-N.
- Delivered drafts per band and per arm.
- Accuracy per arm on both splits, the ceiling included when it ran.
- A per-question flips table (fail → pass, pass → fail) per arm against the
  session's baseline.
- The needed columns shared by discovery and holdout, and the overlap of top-N
  and random-N.
- How many top-N columns have no usage, and the columns tied on score at rank N,
  whose order the FQN decides.
- Grade reason codes per arm, and infrastructure failures per arm.
- The Phase 3 baseline next to the session's baseline.
- Every deviation from this protocol, with its decision record.

## Witnessing the tag

Git sets tag and commit dates from the committer's clock, so they prove nothing
on their own (ADRs 0004 and 0017). The repo is public before the tag (ADR 0017), and the
tag comes after the gold results are materialized, so a gold query that breaks
the rules above is fixed before it. The order is:

1. Run gitleaks over the full history and the private-terms guard over every
   commit message.
2. Tag the commit `preregistered`.
3. Push the tag, then publish a GitHub release on it, with the tagged
   commit's SHA in its body. The release's `published_at` is set by
   GitHub when it is published; its `created_at` is the commit's date, so it
   proves nothing
   ([GitHub REST API, releases](https://docs.github.com/en/rest/releases/releases),
   checked 2026-09-26).
4. Start the Phase 3 baseline only after the release is published.

The results cite the release and its `published_at`.
