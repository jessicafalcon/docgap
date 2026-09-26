# 0008. Keep the 25/15 split and state the smallest effect it can detect

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

The headline is the paired bootstrap interval over the 15 holdout questions for
top-N minus random-N accuracy, and the claim holds only if it excludes zero. A
simulation of that design shows how often it would: a true difference of about
19 points is detected about half the time, one of about 28 points more than 80%
of the time. A null is a likely outcome even if the ranking works. Should the
design change before the `preregistered` tag, and if not, how is a null read?

## Considered Options

1. **Keep 25 discovery / 15 holdout, and state the detectable effect** in the
   protocol before any run.
2. **Rebalance the 40 questions to 20/20.** In the same simulation, a difference
   of about 21 points is detected 74% of the time, against 47% for about 19
   points at 15. No new questions or runs.
3. **Write 55 questions, split 25/30.** A difference of about 19 points is
   detected 81% of the time, for about 1.5 more evenings of gold SQL and 45 more
   runs per arm.

## Decision Outcome

Chosen option: **option 1**, because the ranking is only as good as the
discovery traffic it reads, and 20 discovery questions (60 runs) give it less
evidence than 25 (75 runs); fewer discovery columns also lower N, since
N = min(10, floor(U / 2)). Option 3 adds question-writing time the calendar
doesn't have. Stating the detectable effect keeps a null honest: it reads as "no
difference of that size", not as "the ranking doesn't matter".

### Consequences

- Good, because the design and the evidence behind the ranking stay as the
  brief planned them, and the protocol names what a null can and can't show.
- Bad, because a real difference of 10–20 points is more likely missed than
  detected. The README's limits say so.
- Bad, because the simulation's numbers rest on its assumptions, below. The
  real questions may be more or less deterministic than modeled.

### The simulation

3 repetitions per arm, 1,500 simulated experiments per row, 4,000 bootstrap
resamples each. A question that neither arm's docs fix draws its pass
probability from Beta(0.3, 0.3), mostly near 0 or 1, and each arm samples it
independently. A question that only top-N's docs fix passes with probability
0.85 in top-N and 0.15 in random-N.

The table is `power(15, k)` for k = 2 to 7, called in that order after
`default_rng(0)`. The option figures come from `power(20, 4)` then
`power(20, 6)`, and from `power(30, 6)` then `power(30, 8)`, each pair after a
fresh `default_rng(0)`; the second call of each pair is the one quoted.

| Holdout questions only top-N fixes (of 15) | True difference | Interval excludes zero |
| --- | --- | --- |
| 2 | +9 points | 16% |
| 3 | +14 points | 29% |
| 4 | +19 points | 47% |
| 5 | +23 points | 64% |
| 6 | +28 points | 84% |
| 7 | +33 points | 91% |

```python
import numpy as np
rng = np.random.default_rng(0)
def power(n_q, k, sims=1500, B=4000, reps=3):
    hits = 0
    for _ in range(sims):
        pt = rng.beta(0.3, 0.3, n_q); pr = pt.copy()
        pt[:k], pr[:k] = 0.85, 0.15
        d = rng.binomial(reps, pt) / reps - rng.binomial(reps, pr) / reps
        boot = d[rng.integers(0, n_q, (B, n_q))].mean(1)
        hits += np.percentile(boot, 2.5) > 0
    return hits / sims
```
