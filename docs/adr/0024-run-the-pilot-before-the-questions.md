# 0024. Run the offline pilot before writing the 40 questions, with at most two reruns

- **Status:** Accepted
- **Date:** 2026-10-01
- **Superseded by:** none

## Context and Problem Statement

The brief listed Phase 3's offline work as the 40 questions with gold SQL, then the
grader, the agent loop and the pilot. The pilot is the only step allowed to change the
setup: if neither model is eligible, the protocol's kill criterion calls for harder
questions or more coded columns, and question difficulty is adjusted only there. The
40 gold queries over coded French columns are the longest piece of the block (2–4
evenings in the brief), and written first they are written before anyone knows the
difficulty the pilot will settle, over mart columns the pilot may change. The kill
criterion also sets no limit on how many times the pilot runs again. In which order
does the offline work land, and how many reruns are allowed?

## Considered Options

1. **The 40 questions first**, then the grader, the agent loop and the pilot.
2. **The grader, the agent loop and the pilot first**, then the 40 questions, written
   at the difficulty and over the mart columns the pilot settles.

For the reruns:

3. **No limit:** change the setup until a model is eligible.
4. **At most two reruns** after the first pass; if no model is eligible after the
   third pass, that is the finding.

## Decision Outcome

Chosen options: **2 and 4**. Option 2 costs nothing: the grader and the agent loop
are needed either way, and the pilot uses its own 12 questions, not the 40. The 40
are then written once, over the marts the pilot leaves. ADR 0021 still holds: the
lock is drawn after the pilot and merged after the questions' pull request, so the
questions and every setup change come before it. Option 4 bounds the spend and the
calendar, and fixes before the first pass when "nothing makes a model eligible".

### Consequences

- Good, because no gold query is written against a difficulty or a mart the pilot
  later changes.
- Good, because the kill criterion has an end set before any result is seen.
- Bad, because the pilot's 12 questions must stand in for 40 that don't exist yet.
  They take two per category, and a test checks that none of the 40 repeats a
  pilot question's ID or text.
- Bad, because a pass costs about $30 (72 agent runs per model at the brief's
  per-run rates), so three passes cost about $90, not "a few dollars".
