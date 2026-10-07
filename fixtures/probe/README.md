# Drafter probe

Can descriptions written from an evidence packet alone, with no dictionary, move
the agent model on the pilot questions? Run once on 2026-10-07, before the 40
questions were written; its result set their measure mix (ADR 0036). It is not a
pilot pass and counts toward none.

| Column docs, Haiku 4.5 | Passed | Passed with the gold's measure |
| --- | --- | --- |
| None (pilot pass 2) | 0/36 | 10/36 |
| Every column drafted from its packet (here) | 6/36 | 23/36 |
| Every column from the dictionary (pilot pass 2) | 30/36 | 31/36 |

## What ran

1. **Packets.** For each of the 67 mart columns, `probe.py draft` built a stand-in
   for Phase 5's evidence packet: the column's name and type, its model's
   description, the compiled SQL of its model and every model upstream (from
   `warehouse/dbt/target/manifest.json`), and a profile on the offline sample
   (null rate, distinct count, values carried by at least *k* = 11 rows, minimum
   and maximum clipped to the 11th). A `restricted` column's profile holds no
   value. `packets.json`.
2. **Drafts.** Opus 5.5, at default sampling, wrote one description per packet in
   French, at most 200 characters, under a stand-in prompt that forbids claims the
   packet doesn't support. No gate: every draft went in, so the result is an upper
   bound on what drafts do. 67 calls, $1.79. `drafts.json`.
3. **Runs.** `probe.py run` gave Haiku 4.5 pass 2's no-docs manifest with every
   draft as its column descriptions, and graded each of the 12 pilot questions × 3
   repetitions on the gold and accepted results, as the pilot does. 36 runs,
   $1.69. `claude-haiku-4-5-20251001.drafts/<qid>.r<n>/`.

The second column of the table comes from `measure_regrade.py`, which runs no
model:

```sh
PYTHONPATH=. uv run python fixtures/probe/measure_regrade.py \
  fixtures/pilot/pass-2/claude-haiku-4-5-20251001.no_docs \
  fixtures/probe/claude-haiku-4-5-20251001.drafts \
  fixtures/pilot/pass-2/claude-haiku-4-5-20251001.full_docs
```

Both scripts ran from a scratch directory. `probe.py` was moved here with its
paths changed, a newline ending each JSON file and its two checks raising errors;
replayed from the response cache in the gitignored `data/probe/`, it rewrites
`packets.json` and `drafts.json` byte for byte and makes no model call.
`measure_regrade.py` renames columns with sqlglot where the scratch version used
regular expressions, and gives the scratch version's counts: 10, 23 and 31 of 36.
