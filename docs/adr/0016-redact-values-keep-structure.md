# 0016. Redact every value but keep ordinals and type parameters, and require a stable normalization

- **Status:** Accepted
- **Date:** 2026-09-28
- **Superseded by:** none

## Context and Problem Statement

`snapshot` replaces values in query text with placeholders before anything reaches
disk, and `resolve` later reads only that normalized text. Replacing every
`Literal` node, as first planned, fails both ways on sqlglot 30.20:

- It over-redacts: `GROUP BY 1 ORDER BY 2` becomes `GROUP BY ? ORDER BY ?`, and
  the grouped column loses its `group_by` reference.
- It under-redacts: `$$text$$` and `x'ff'` are `RawString` and `HexString` nodes,
  not `Literal`, and comments are printed by default.
- `INTERVAL '1 day'` prints as `INTERVAL '? DAY'`, which normalizes a second time to
  `INTERVAL '?'`. A redacted snapshot could not replay to the same fingerprints.

Which nodes are redacted, and how is the result kept stable?

## Considered Options

1. **Replace every `Literal`.** The plan as first written.
2. **Replace every value node, keep structural numbers, and check stability.**
   Values: `Literal`, the raw, hex, bit, byte, national and Unicode strings, and
   intervals whole. Kept: integer ordinals in `GROUP BY` and `ORDER BY`, type
   parameters (`NUMBER(10, 2)`) and positional parameters (`$1`). Never print
   comments. Collapse an `IN` list of values to one placeholder. Uppercase unquoted
   identifiers. Normalize the result again and drop the query, counted, if it changes.
3. **Rely on Snowflake's `QUERY_PARAMETERIZED_HASH`.** It needs the warehouse and
   gives a hash, not text `resolve` can read.

## Decision Outcome

Chosen option: **option 2**, because it removes every value this version of sqlglot
exposes, keeps what `resolve` needs, and turns an unstable normalization into a
counted drop instead of a silent fingerprint change. Only queries are kept: a
statement that isn't one (`ALTER SESSION`, `SHOW`) is dropped and counted, since
sqlglot keeps the text of a statement it can't parse in a `Command` node.

### Consequences

- Good, because the frozen Phase 3 snapshot, stored with its text already
  normalized, replays through the same code to the same fingerprints.
- Good, because a fingerprint doesn't depend on values, identifier case or the
  length of an `IN` list.
- Bad, because the value node list follows sqlglot's node types, and a new string
  type in a later version would pass through. A test covers each value form the
  Snowflake dialect parses (string, number, raw, hex and national strings,
  interval), and a sqlglot upgrade re-runs it.
- Bad, because an interval's unit and a format string (`TO_CHAR(d, ?)`) are lost
  from the text. Neither names a column.
- Bad, because a replay holds only the kept rows: a dropped query has no redacted
  form. The live stage's counts are frozen beside the export, and the report cites
  them for the baseline's parse rate (brief, Phase 3 "Snapshot query history").
