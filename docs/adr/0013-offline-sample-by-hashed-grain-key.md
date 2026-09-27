# 0013. Cut the offline sample by a hashed grain key, 1 line in 50

- **Status:** Accepted
- **Date:** 2026-09-27
- **Superseded by:** none

## Context and Problem Statement

Everything before the Snowflake trial runs on DuckDB: the dbt project, the grader,
the agent loop and the offline pilot that picks the agent model. That needs a slice of
the three pinned months (ADR 0012) of about 2M rows, with all three processing months,
cut by a rule anyone can re-run to the same bytes. CI needs a much smaller slice it
can commit, since `check-added-large-files` stops any file over 5 MB and CI has no
source files. What rule cuts both, and what gets committed?

## Considered Options

1. **Hash of the grain key under a threshold.** Keep a line when the SHA-256 of its
   dimension fields, the staging grain, falls under 2⁶⁴ // 50.
2. **Hash of the whole line.** The same, with measures in the key.
3. **Whole groups of one dimension**, such as a few regions.
4. **The first N lines of each file.**
5. **DuckDB `USING SAMPLE` with a seed.**

## Decision Outcome

Chosen option: **option 1**, because a line's pick depends only on its dimension
fields. Lines that repeat a dimension combination are kept or dropped together, so
staging's aggregate over the sample is exact for every combination the sample holds.
Option 2 would split repeated combinations. Option 3 empties every question about a
dropped region. Option 4 takes the order the file happens to be written in. Option 5's
repeatability depends on the DuckDB version and thread count.

The rule, in `loader/offline_sample.py`:

1. **Check the source.** Each file's size and SHA-256 must match `loader/sources.lock`.
2. **Key.** A line's fields 1–16 and 30–56, as raw bytes joined by `;`. The measures
   (17–29) and the empty field 57 are left out. `FLX_ANN_MOI` is field 1 and each file
   holds one processing month, so each month is cut on its own at the same rate.
3. **Keep** the line if the first 8 bytes of the key's SHA-256, read big-endian, are
   under 2⁶⁴ // 50. The header and the kept lines are written byte for byte, in source
   order, to `data/sample/A2025MM.csv` (gitignored).
4. **Fixture.** The same rule at 2⁶⁴ // 5,000, applied to the sample, writes
   `fixtures/damir/A2025MM.csv`. A smaller threshold keeps a subset of a larger one,
   so this is the same as cutting it from the source, and the fixture is inside the
   sample.
5. **Pin.** Each output is written to a temporary file and appears only if it matches
   `loader/sample.lock` (rows, bytes, SHA-256 of the uncompressed bytes). A month
   with no kept line fails. `--update-lock` writes the lock instead of checking it.
   An output that already matches is not cut again.

No salt: the split hashes question IDs and never rows, so nothing can correlate with
this hash. The sample hashes below were cut on 2026-09-27 in 2 min 55 s. Deleting
`A202502` and running the script again against the lock reproduced both of its files.

| File | Rows | Bytes | SHA-256 |
| --- | --- | --- | --- |
| `data/sample/A202501.csv` | 734,309 | 118,650,217 | `7cb0b74a67e3a76c0236f82cb9de2fdc3912ca215ef1979fd62c13d295283164` |
| `data/sample/A202502.csv` | 691,878 | 112,024,405 | `9083ef69baa72dbe46d3d0c7d1567f072281da5e1c72a0abdea7872b0549ff68` |
| `data/sample/A202503.csv` | 715,664 | 115,832,991 | `d5d938d2ee690c7df4d04a64b7a815d3bbd118ebce8723ac3902456dabb48787` |
| `fixtures/damir/A202501.csv` | 7,215 | 1,167,461 | `1d39bfa28184f4ff207c8f351371db067dc8003ee11aa0a2482eb24a83d4e63e` |
| `fixtures/damir/A202502.csv` | 6,929 | 1,122,743 | `db7f809e56d59cb3e1f62e725ac57ed05d438316a88a99b30f0c9251513d2428` |
| `fixtures/damir/A202503.csv` | 7,101 | 1,150,227 | `60d535ce80b2271fcc94fcd10450173271a9928cd23a7d1175b9c1e5bceb7e03` |

In all: 2,141,851 sample rows (2.00% of 107,232,480) and 21,245 fixture rows. Measured
with the commands below:

- **No repeated grain key** in the sample, the same as in the first 2M rows of each file.
- **Spend.** `FLT_PAI_MNT` × 50 over each file's total is 1.00, 0.98 and 1.09 for
  January to March: the spend is heavy-tailed, and a few large cells move it.
- **Placeholder care dates** (`SOI_ANN` `0000` or `0001`): 298 to 338 sample rows per
  month, but only 1 to 4 per fixture month.
- **Rare codes are dropped.** Of January's 886 `PRS_NAT` codes and 25 `SOI_ANN`
  years, the sample keeps 707 and 14, and the fixture 334 and 6.

```sh
cd data
for f in sample/A2025*.csv ../fixtures/damir/A2025*.csv; do
  LC_ALL=C awk -F';' -v f=$f 'NR > 1 { n++; s += $27; if ($30 == "0000" || $30 == "0001") ph++ }
    END { printf "%s rows %d spend %.0f placeholder %d\n", f, n, s, ph + 0 }' $f
  tail -n +2 $f | cut -d';' -f1-16,30-56 | LC_ALL=C sort | uniq -d | wc -l
done
for f in sample/A202501.csv ../fixtures/damir/A202501.csv; do
  LC_ALL=C awk -F';' -v f=$f 'NR > 1 { p[$40]; y[$30] } END { print f, length(p), length(y) }' $f
done
gzip -dc open_damir/A202501.csv.gz |
  LC_ALL=C awk -F';' 'NR > 1 { p[$40]; y[$30] } END { print "source", length(p), length(y) }'
```

### Consequences

- Good, because the offline world is re-cut to the same bytes from the pinned files,
  and a changed source, rule or output fails before a file appears.
- Good, because the sample keeps the source's format. The Phase 2 DDL and header
  check read it as they read the full files.
- Good, because CI checks real rows: every fixture line passes the rule, every
  fixture file matches the lock, and from Phase 2 CI runs `dbt build` on DuckDB
  over the fixture.
- Bad, because the offline pilot and every gold result before the trial see 1 row in
  50 and fewer distinct codes. A grouping that fits in 200 rows on the sample can
  exceed it on the full data, and a top N can tie or reorder. Each gold query is
  therefore also checked on DuckDB over the three full months before the trial
  (owed by "Write 40 questions with gold SQL" in the brief). The pilot compares the
  agent with gold results on the same sample, so its comparison stays fair.
- Bad, because the fixture has 1 to 4 placeholder-date rows per month and drops most
  rare codes. A test that needs one of those cases asserts it is present, or uses a
  hand-made line.
- Bad, because committing real rows makes the repo redistribute Open DAMIR. The
  Licence Ouverte allows it with the source named, which `fixtures/damir/README.md`
  does.
