# 0012. Load three processing months of Open DAMIR, `A202501` to `A202503`

- **Status:** Accepted
- **Date:** 2026-09-27
- **Superseded by:** none

Supersedes [0011](0011-load-one-month-of-open-damir.md).

## Context and Problem Statement

ADR 0011 loaded one month, on the grounds that three months would triple the
download, load and credits for only a processing-month trend. That cost was
overstated. Snowflake runs one load operation per file and loads files in
parallel ([preparing data files](https://docs.snowflake.com/en/user-guide/data-load-considerations-prepare),
checked 2026-09-27), so three files take about as long to `COPY` as one. Load
credits are a small share of the trial's balance. What three months really add
is about 1.9 GB more to download and upload, and 3× the rows under every query.
With the cost that low, should the warehouse carry a trend, and how do questions
stay unambiguous once it does?

Measured on 2026-09-27 with the command below; the checks of ADR 0011 (57 fields
per line, empty trailing field, ASCII, LF, no `,`) hold on every line of all three.

| File | Bytes gzipped | Rows | Uncompressed | `FLX_ANN_MOI` | Prefiltered spend `FLT_PAI_MNT` |
| --- | --- | --- | --- | --- | --- |
| `A202501.csv.gz` | 973,594,858 | 36,640,259 | 5.92 GB | `202501` only | 14.05 bn € |
| `A202502.csv.gz` | 923,432,287 | 34,710,236 | 5.62 GB | `202502` only | 13.39 bn € |
| `A202503.csv.gz` | 952,744,750 | 35,881,985 | 5.81 GB | `202503` only | 13.86 bn € |
| **Total** | 2,849,771,895 | 107,232,480 | 17.35 GB | | |

The header is byte-identical in the three files (SHA-256 `e0fa2c1a…eee5844`).
Placeholder care dates: 48,250 rows `0000`-`00` and 25 rows `0001`-`01`.

Each file holds about 48% of its rows as care in its own month, 28–30% as care
the month before, and a tail of older care. So summed by care month, the three
files give 14.06, 11.87 and 8.00 bn € for January, February and March 2025 care.
That is a lag artifact: March care has had one month of processing, January care
three. January care is about 95% complete: care three or more months old is 5.5%
of the January file's spend. By processing month, spend stays within 5%.

ADR 0011's other findings stand: the descriptor has no entry for `ETB_DCS_MCO`
and six pre-2015 `*_ZEAT` variables the files lack; download links carry a
`token=` parameter; the 2025 list names `A202501` to `A202512`. Its duplicate-key
check, re-run on the first 2M rows of `A202502` and `A202503`, finds none either.
The field positions below are from the shared header: `$1` `FLX_ANN_MOI`, `$27`
`FLT_PAI_MNT`, `$30` `SOI_ANN`, `$31` `SOI_MOI`.

```sh
for f in A202501 A202502 A202503; do
  wc -c < $f.csv.gz; shasum -a 256 $f.csv.gz; gzip -dc $f.csv.gz | head -1 | shasum -a 256
  gzip -dc $f.csv.gz | sed -n '2,2000001p' | cut -d';' -f1-16,30-56 | LC_ALL=C sort | uniq -d | wc -l
  gzip -dc $f.csv.gz | LC_ALL=C awk -F';' -v f=$f 'NR > 1 {
    n++; b += length($0) + 1; if (NF != 57 || $57 != "") bad++; if ($0 ~ /[\200-\377]|\r|,/) enc++
    flx[$1]++; k = $30 "-" $31; rows[k]++; spend[k] += $27; total += $27 }
    END { printf "%s rows %d bytes %d bad %d enc %d spend %.0f\n", f, n, b, bad + 0, enc + 0, total
          for (m in flx) print f, "FLX", m, flx[m]
          for (k in rows) printf "%s SOI %s %d %.0f\n", f, k, rows[k], spend[k] }'
done
```

## Considered Options

1. **One month, `A202501`** (ADR 0011). No spend trend.
2. **Three processing months, `A202501` to `A202503`.** About 107M rows, a
   three-point trend by processing month, and January 2025 care nearly complete.
3. **Twelve months.** About 430M rows and 4× more scan under every agent query
   than option 2, for a longer trend that the questions don't need.

## Decision Outcome

Chosen option: **three processing months**, because month-over-month is a question
a business user asks, and three months add it with no binding cost: the files load
in parallel and credits are not the constraint. Questions follow two rules, so that no question has two defensible
gold answers, which would fail in every arm and add noise the docs can't fix:

- A trend is read by processing month (`FLX_ANN_MOI`). A care-month total summed
  across processing months names January 2025 only, the one care month near
  complete. A question inside one processing month ("reimbursed in March for care
  delivered in February") is fine.
- Question text says which month it means in plain words ("reimbursed in",
  "care delivered in"). Mapping those words to `FLX_ANN_MOI` or `SOI_ANN` and
  `SOI_MOI` is what the documentation has to make possible.

### Consequences

- Good, because questions can ask about trends, and the difference between the
  processing month and the care month depends on documentation.
- Good, because three files load in parallel, so the load stays one evening.
- Bad, because every agent query scans about 107M fact rows on an XS warehouse
  under a 60 s timeout, and the offline pilot on DuckDB can't show that. Gold
  queries are timed when they are materialized, with the result cache off, and
  the times are stored next to the gold results' hashes. One over 20 s moves
  `WH_AGENT` and `WH_AUDIT` from XS to S before the `preregistered` tag. A bigger
  warehouse changes nothing the agent sees, so the pilot stands; a pre-aggregated
  mart or fewer months would change the mart columns or the questions and need
  the pilot again. The check is engineering, not a protocol rule (owed by
  "Materialize gold results" in the brief).
- Bad, because the offline sample has to be drawn within each processing month so
  all three appear, and it is cut from 107M rows instead of 37M (owed by "Define
  the offline sample").
