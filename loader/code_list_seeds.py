"""Write the dbt seeds: four code lists of the Open DAMIR dictionary, as code→label pairs.

The lists are in the descriptor's `MOD OPEN DAMIR` sheet. Each opens with a line
holding the variable's name and the dictionary's definition of its label, then one
code and label per line, and ends at a blank line. Only the pairs are written, never
that first line: the warehouse gets labels, while every definition stays in
`eval/reference/`, the ground truth drafts are graded against.

    uv run python loader/code_list_seeds.py
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from pathlib import Path

import openpyxl

__all__ = [
    "DICTIONARY",
    "SEED_DIR",
    "SHEET",
    "VARIABLES",
    "code_lists",
    "main",
    "render",
    "seeds",
    "sheet_rows",
]

ROOT = Path(__file__).resolve().parents[1]
DICTIONARY = ROOT / "eval" / "reference" / "2024_descriptif-variables_open-damir-base-complete.xlsx"
SHEET = "MOD OPEN DAMIR"
SEED_DIR = ROOT / "warehouse" / "dbt" / "seeds"

# Age bracket, region of residence, benefit type and provider activity: the four
# dimensions (ADR 0023). Keep in sync with the dimension models in warehouse/dbt.
VARIABLES = ("AGE_BEN_SNDS", "BEN_RES_REG", "PRS_NAT", "PSE_ACT_SNDS")


def code_lists(
    rows: Iterable[Sequence[object]], variables: Iterable[str] = VARIABLES
) -> dict[str, list[tuple[int, str]]]:
    """Return each variable's code→label pairs from the sheet's rows, sorted by code.

    Labels are trimmed; the line that opens a list is skipped.

    >>> code_lists(
    ...     [("AGE_BEN_SNDS", "Libellé Tranche d'Age"), (99, "AGE INCONNU "), (0, "0-19 ANS"), ()],
    ...     ["AGE_BEN_SNDS"],
    ... )
    {'AGE_BEN_SNDS': [(0, '0-19 ANS'), (99, 'AGE INCONNU')]}

    Raises:
        ValueError: a variable has no list or two, or its list holds a code that
            isn't an integer, a blank label, or a code twice.
    """
    wanted = set(variables)
    lists: dict[str, dict[int, str]] = {}
    current: str | None = None
    for number, row in enumerate(rows, start=1):
        code = row[0] if row else None
        if current is None:
            if isinstance(code, str) and code in wanted:
                if code in lists:
                    raise ValueError(f"{SHEET} row {number}: a second code list for {code}")
                current = code
                lists[current] = {}
            continue
        if code is None:
            current = None
            continue
        where = f"{SHEET} row {number} ({current})"
        label = row[1] if len(row) > 1 else None
        # bool is an int subclass; a code read as True would pass as 1.
        if type(code) is not int:
            raise ValueError(f"{where}: code {code!r} is not an integer")
        if not (isinstance(label, str) and label.strip()):
            raise ValueError(f"{where}: no label for code {code}")
        if code in lists[current]:
            raise ValueError(f"{where}: code {code} listed twice")
        lists[current][code] = label.strip()
    if missing := sorted(wanted - lists.keys()):
        raise ValueError(f"{SHEET}: no code list for {', '.join(missing)}")
    return {name: sorted(pairs.items()) for name, pairs in sorted(lists.items())}


def render(variable: str, pairs: Iterable[tuple[int, str]]) -> bytes:
    """Return a seed CSV: a `<variable>,<variable>_LIB` header, then one line per pair.

    >>> render("BEN_SEX_COD", [(1, "MASCULIN"), (2, "FEMININ, AUTRE")]).decode()
    'BEN_SEX_COD,BEN_SEX_COD_LIB\\n1,MASCULIN\\n2,"FEMININ, AUTRE"\\n'
    """
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([variable, f"{variable}_LIB"])
    writer.writerows(pairs)
    return out.getvalue().encode()


def sheet_rows(sheet: str, dictionary: Path = DICTIONARY) -> list[tuple[object, ...]]:
    """Return the cell values of each row of `sheet` in the dictionary at `dictionary`."""
    workbook = openpyxl.load_workbook(dictionary, read_only=True, data_only=True)
    try:
        return [tuple(row) for row in workbook[sheet].iter_rows(values_only=True)]
    finally:
        workbook.close()


def seeds(dictionary: Path = DICTIONARY) -> dict[str, bytes]:
    """Return each seed's file name and bytes, read from the dictionary at `dictionary`."""
    lists = code_lists(sheet_rows(SHEET, dictionary))
    return {f"{name.lower()}.csv": render(name, pairs) for name, pairs in lists.items()}


def main() -> None:
    """Write every seed into the dbt project."""
    SEED_DIR.mkdir(exist_ok=True)
    for name, content in seeds().items():
        (SEED_DIR / name).write_bytes(content)
        print(f"{(SEED_DIR / name).relative_to(ROOT).as_posix()}: {content.count(b'\n') - 1} codes")


if __name__ == "__main__":
    main()
