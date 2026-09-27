# 0011. Load one month of Open DAMIR, `A202501.csv.gz`

- **Status:** Superseded
- **Date:** 2026-09-26
- **Superseded by:** [0012](0012-load-three-processing-months-of-open-damir.md)

## Context and Problem Statement

Open DAMIR publishes one gzipped CSV per processing month, about 0.8–1 GB and
37M rows each. Every month loaded adds a download, a `COPY` and warehouse credits
inside the trial window, the binding constraint. Three months would allow
month-over-month questions, unless one file already spans several care months.
How many months does the warehouse load?

Measured on 2026-09-26 on `A202501.csv.gz`, with the commands below:

| Fact | Value |
| --- | --- |
| Size, SHA-256 | 973,594,858 bytes, `3d28be96…72efe0f` (full hash in `loader/sources.lock`) |
| Rows (excluding the header), uncompressed size | 36,640,259 rows, 5.92 GB |
| Fields per line | 57 on every line, header included: 56 named columns and an empty trailing field after the final `;` |
| Encoding | ASCII only, LF line endings, no `,` anywhere (`.` is the decimal separator) |
| Processing month `FLX_ANN_MOI` | `202501` on every row |
| Care month `SOI_ANN`-`SOI_MOI`, share of rows / of prefiltered spend `FLT_PAI_MNT` | 2025-01: 48.1% / 62.2%; 2024-12: 27.9% / 26.3%; 2024-11: 8.2% / 6.0%; 2024-10: 4.2% / 2.2%; older months back to 2001, placeholders included: 11.6% / 3.4% |
| Placeholder care dates | 15,607 rows `0000`-`00`, 8 rows `0001`-`01` |
| Dimension keys (columns 1–16 and 30–56, all but the 13 measures) | No duplicate in the first 2M rows |
| Download links | Each file link on the yearly list page carries a `token=` query parameter |

The 2025 file list names `A202501` to `A202512`, from 815,025,319 to 973,594,858
bytes by HTTP `Content-Length`; the 2026 list was empty. Source:
[Open DAMIR on data.gouv.fr](https://www.data.gouv.fr/datasets/open-damir-base-complete-sur-les-depenses-dassurance-maladie-interregimes),
published by the Caisse nationale de l'Assurance Maladie under the
[Licence Ouverte](https://www.etalab.gouv.fr/licence-ouverte-open-licence), which
asks reusers to name the source and its last update, as this record does.

The variable descriptor, `2024_descriptif-variables_open-damir-base-complete.xlsx`
(113,672 bytes, SHA-256 `c243a9f8de420db5909a8d99c7419f779a102d0f12cd8a1098b1f23bfd035a7e`,
served with `Last-Modified: 11 Feb 2025`), is an `.xlsx` workbook. Sheet `OPEN DAMIR`
defines 61 variables and sheet `MOD OPEN DAMIR` holds their code lists. Against
the file header, it has six `*_ZEAT` variables the file lacks (regions before
2015) and no entry for `ETB_DCS_MCO`, which the file has. Sheet `IR_PHA_R`
describes a different table.

```sh
shasum -a 256 A202501.csv.gz
gzip -dc A202501.csv.gz | head -1 | tr ';' '\n'   # header: 56 names, then an empty 57th field
gzip -dc A202501.csv.gz | sed -n '2,2000001p' | cut -d';' -f1-16,30-56 | LC_ALL=C sort | uniq -d | wc -l
gzip -dc A202501.csv.gz | LC_ALL=C awk -F';' 'NR > 1 {
  n++; b += length($0) + 1; if (NF != 57) bad++; if ($57 != "") tail++
  if ($0 ~ /[\200-\377]/) hi++; if ($0 ~ /\r$/) cr++; if (index($0, ",")) comma++
  flx[$1]++; k = $30 "-" $31; rows[k]++; spend[k] += $27; total += $27 }
  END { print n, b, bad + 0, tail + 0, hi + 0, cr + 0, comma + 0
        for (m in flx) print "FLX", m, flx[m]
        for (k in rows) print "SOI", k, rows[k], rows[k] / n, spend[k] / total }'
```

## Considered Options

1. **One month, `A202501`.** About 37M rows, one load evening.
2. **Three months, `A202501` to `A202503`.** About 110M rows and three times the
   download, load and credits, for month-over-month trends by processing month.

## Decision Outcome

Chosen option: **one month, `A202501`**, because the file spans care months
already, and two more loads inside the trial would buy only a processing-month
trend. Its care months are not a trend: each holds only the part of that month's
care processed in January 2025, so they show processing lag. Questions can use
that distinction, since a processing month and a care month are exactly what an
undocumented `FLX_ANN_MOI` and `SOI_MOI` leave an agent to guess. `A202501` is the
month the facts above were measured on; the other 2025 files are 815–974 MB,
none larger.

### Consequences

- Good, because the load stays one evening and about 37M rows, as the risk table
  plans, and the offline sample is cut from the same file.
- Good, because the questions get a care-month versus processing-month category
  whose answer depends on documentation.
- Bad, because there are no spend trends across months. Phase 3's month-over-month
  category becomes care month versus processing month.
- Bad, because 15,615 rows carry placeholder care dates, so staging must keep
  `SOI_ANN` and `SOI_MOI` as codes and never cast them to a date (owed by the
  staging step in the brief).
- Bad, because `ETB_DCS_MCO` has no dictionary entry, so no draft for it can be
  graded. It stays out of the marts (owed by the marts step in the brief).
