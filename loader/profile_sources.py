"""Profile Open DAMIR files: what each type in `raw_prestations.sql` rests on.

Per field: empty values, values that aren't plain decimal numbers, and the widest
integer part and scale. Per file: rows and distinct grain keys, so a repeated
dimension combination shows (the staging grain, ADR 0013). Every field is read as
text, so nothing here depends on the DDL it checks.

    uv run python loader/profile_sources.py data/open_damir/*.csv.gz
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

import duckdb

__all__ = ["Field", "Profile", "main", "profile"]


class Field(NamedTuple):
    """One field of a file: empty values, non-numeric values, widest integer part and scale."""

    empty: int
    non_numeric: int
    integer_digits: int
    scale: int


class Profile(NamedTuple):
    """One file: its data rows, its distinct grain keys, and each named field."""

    rows: int
    grain_keys: int
    fields: dict[str, Field]


def profile(path: Path) -> Profile:
    """Profile the file at `path`, plain or gzipped."""
    with duckdb.connect() as con:
        con.execute(
            "CREATE TABLE t AS SELECT * FROM read_csv(?, delim = ';', header = true, all_varchar = true)",
            [path.as_posix()],
        )
        # The header's last field is the empty one after the trailing `;` (ADR 0012).
        names = [row[0] for row in con.execute("DESCRIBE t").fetchall()][:-1]
        fields: dict[str, Field] = {}
        for name in names:
            # `name` comes from DESCRIBE and is quoted; values stay in the table.
            [row] = con.execute(
                f"""SELECT
                  count(*) FILTER (WHERE "{name}" IS NULL),
                  count(*) FILTER (WHERE NOT regexp_full_match("{name}", '-?[0-9]*(\\.[0-9]+)?')),
                  coalesce(max(length(split_part(ltrim("{name}", '-'), '.', 1))), 0),
                  coalesce(max(length(split_part("{name}", '.', 2))), 0)
                FROM t"""  # noqa: S608
            ).fetchall()
            fields[name] = Field(*row)
        # Fields 1-16 and 30-56, as in `offline_sample.grain_key`; keep the two in sync.
        # A 64-bit hash can only merge keys, never split one, so equal counts prove
        # that no key repeats.
        grain = ", ".join(f'"{name}"' for name in names[:16] + names[29:56])
        [(rows, grain_keys)] = con.execute(
            f"SELECT count(*), count(DISTINCT hash({grain})) FROM t"  # noqa: S608
        ).fetchall()
    return Profile(rows, grain_keys, fields)


def main(argv: list[str] | None = None) -> None:
    """Print each file's rows and grain keys, then each field across all files."""
    paths = [Path(arg) for arg in (sys.argv[1:] if argv is None else argv)]
    profiles = [profile(path) for path in paths]
    for path, p in zip(paths, profiles, strict=True):
        print(f"{path.name}: {p.rows} rows, {p.grain_keys} distinct grain keys")
    print("field          empty  non-numeric  integer digits  scale")
    for name in profiles[0].fields:
        each = [p.fields[name] for p in profiles]
        print(
            f"{name:<14}{sum(f.empty for f in each):>6}{sum(f.non_numeric for f in each):>13}"
            f"{max(f.integer_digits for f in each):>16}{max(f.scale for f in each):>7}"
        )


if __name__ == "__main__":
    main()
