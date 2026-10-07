# Blind checks of the pilot questions

Before a pilot pass, an Opus 5.5 subagent, not a candidate model, answers each
question with one SQL query, so a question two careful readers would read two
ways is found before any spend (ADR 0033). Its answers are graded against the
gold and the accepted results; each mismatch is a question defect, fixed, or an
agent error, left.

What the subagent was given, in a scratch directory outside the repo:

- the agent's system prompt, the marts' tables and every column with the
  full-docs text, as `list_tables` and `describe` return them;
- the questions' texts, and nothing else from `eval/pilot_questions.yml`;
- a script running a query through the agent's own `run_sql` on
  `data/agent/sample/`.

It was told to read nothing else and to open nothing in the repo, so it never
saw the gold SQL or `eval/pilot_gold/`. That rests on the instruction, not on a
sandbox.

`pass-2.json` holds, per question, the subagent's SQL and its notes on the
readings it weighed: `first` for all 12, then `p02_reworded` for P02 alone after
its text was fixed. Both runs passed every question.
