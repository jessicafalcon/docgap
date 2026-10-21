# Blind checks of the questions

Before a question set is used, an Opus 5.5 subagent, not a candidate model,
answers each question with one SQL query, so a question two careful readers would
read two ways is found before any spend (ADR 0033). Its answers are graded against
the gold and the accepted results; each mismatch is a question defect, fixed, or an
agent error, left.

What the subagent was given, in a scratch directory outside the repo:

- the agent's system prompt, the marts' tables and every column with the
  full-docs text, as `list_tables` and `describe` return them;
- the questions' texts, and nothing else from the questions file;
- a script running a query through the agent's own `run_sql` on
  `data/agent/sample/`.

It was told to read nothing else and to open nothing in the repo, so it never
saw the gold SQL or the stored gold results. That rests on the instruction, not
on a sandbox.

Each file holds, per question, the subagent's SQL and its notes on the readings
it weighed:

| File | Questions | Runs | Result |
| --- | --- | --- | --- |
| `pass-2.json` | The pilot's 12, before pass 2 | `first` for all 12, then `p02_reworded` for P02 alone after its text was fixed | Both runs passed every question |
| `eval.json` | The evaluation's 40 | `first` for all 40 | Passed every question |

On the 40, the subagent flagged readings that change no answer it gave: a
`FLT_` measure or its `PRS_` twin filtered on the reimbursement type (accepted
beside the gold), activity 99 kept beside the excluded 0, dentists coded outside
activity 19 (the texts say "recorded as dental surgery"), and the docs' advice
to recompute fees above the agreed rate as paid minus base for medical supplies,
which the subagent, like the gold, left aside for the stored column.
