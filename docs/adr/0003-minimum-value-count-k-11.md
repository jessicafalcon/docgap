# 0003. Show a value to a model only if at least k = 11 fact rows carry it

- **Status:** Accepted
- **Date:** 2026-09-26
- **Superseded by:** none

## Context and Problem Statement

The drafter and the draft gate see an aggregate profile of each column: null
rate, distinct count, min and max for numbers, and top values. A value carried
by only a few rows can single out a person or a small group, and once it reaches
a prompt it can reach a draft, a pull request and the model-response cache. Open
DAMIR is already anonymized open data, so here the rule shows the mechanism
working; it is written as it would be for data that is not. Three things need
fixing: the threshold, what a value's count is, and how min and max are shown.

## Considered Options

For the threshold:

1. **No threshold.** Rely on the source's own anonymization.
2. **k = 10.** A round number with no published rule behind it.
3. **k = 11**, the small-count rule of the source's own ecosystem: open data
   derived from the French national health data system masks counts under 11
   individuals ([SNDS documentation, "Open Data en santé"](https://www.documentation-snds.health-data-hub.fr/snds/aller_plus_loin/open_data/),
   section 2.2, checked 2026-09-26). The US CMS policy draws the same line: no
   cell of 1 to 10 ([ResDAC](https://resdac.org/articles/cms-cell-size-suppression-policy),
   checked 2026-09-26).
4. **Noise on counts** (differential privacy). Perturb every count instead of
   suppressing small ones.

For the count: the rows of the column's own table, or the fact rows behind the
value. For min and max: show them as they are, drop them, or clip them.

## Decision Outcome

Chosen option: **k = 11, counted in fact rows, with min and max clipped**,
because it applies the rule Open DAMIR's own publisher follows, to the rows that
describe care. The count unit is fact rows because Open DAMIR has no person
identifier. A dimension seed or the monthly aggregate holds one row per value,
so counting a column's own rows would hide every value in those tables. Min and
max are reported as the 11th smallest and 11th largest values, since a raw
extreme is usually carried by one row. No threshold leaves the mechanism
untested; noise would change the numbers the drafter describes and add a privacy
budget and a seed to the core. `restricted` columns get no values at any k.

### Consequences

- Good, because the threshold can be traced to a published rule, not a choice
  made for this project.
- Good, because one value in `docgap.toml` governs the profile queries, the
  evidence packets and the drafter's view, and the rule is unit-tested.
- Bad, because profiling a dimension or aggregate column joins back to the fact
  table, which makes the profile queries heavier than a per-table scan.
- Bad, because the offline sample (about 2M rows) has smaller counts than a full
  month, so more values fall under k offline and pilot drafts see fewer values
  than live ones.
- Bad, because profiles are frozen with a hash in Phase 3. Changing k or the
  count after that means re-profiling every mart column and a record that
  supersedes this one.
