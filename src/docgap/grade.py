"""Grade an agent run by comparing its result with the gold result, executing nothing."""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable
from dataclasses import dataclass
from datetime import date, time
from decimal import ROUND_HALF_EVEN, Context, Decimal
from itertools import permutations
from typing import Literal

from docgap.models import Grade, GradeReason

__all__ = ["MAX_COLUMNS", "Result", "check", "grade", "normalize"]

# The protocol's permutation bound: 5! = 120 column orders at most.
MAX_COLUMNS = 5

_CENT = Decimal("0.01")
# Quantizing needs the integer digits plus 2: a float reaches 309 of them and a DuckDB
# HUGEINT 39, so 400 never overflows.
_CONTEXT = Context(prec=400, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class Result:
    """A query's result set: its column count, kept for a result with no rows, and its rows."""

    width: int
    rows: tuple[tuple[object, ...], ...]

    def __post_init__(self) -> None:
        if any(len(row) != self.width for row in self.rows):
            raise ValueError(f"every row needs {self.width} values")


def normalize(value: object) -> Hashable:
    """Map a value to what the protocol compares, tagged so a number never equals a string.

    >>> normalize(2.675) == normalize(Decimal("2.675")) == ("number", Decimal("2.68"))
    True
    >>> normalize(" PHARMACIE "), normalize(date(2025, 1, 1)), normalize(None)
    (('string', 'PHARMACIE'), ('string', '2025-01-01'), ('null',))
    >>> normalize(True) == normalize(1)
    False
    """
    match value:
        case None:
            return ("null",)
        # Before the numbers: `bool` is an `int`, and a boolean is no number.
        case bool():
            return ("bool", value)
        case int() | float() | Decimal():
            # A float through its shortest round-trip text, so FLOAT 2.675 rounds as typed.
            exact = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
            if not exact.is_finite():
                return ("nonfinite", str(exact))
            return ("number", exact.quantize(_CENT, context=_CONTEXT))
        case str():
            return ("string", value.strip())
        case date() | time():
            return ("string", value.isoformat())
        case _:
            # A list or a struct the agent selected: equal only to the same value.
            return ("other", type(value).__name__, repr(value))


def _normalized(result: Result) -> list[tuple[Hashable, ...]]:
    return [tuple(normalize(value) for value in row) for row in result.rows]


def check(
    outcome: Result | Literal[GradeReason.ERROR, GradeReason.TIMEOUT],
    gold: Result,
    *,
    ordered: bool,
) -> GradeReason | None:
    """Return the first protocol check the run fails, or None when it matches gold.

    The checks run in the protocol's order: the run produced a result, the same
    column count, the same row count, then the same values under some order of
    the result's columns, as sequences if `ordered` and as multisets otherwise.

    >>> gold = Result(2, (("q1", 1.5), ("q2", 2)))
    >>> check(Result(2, ((2.0, "q2"), (1.5, "q1"))), gold, ordered=False) is None
    True
    >>> check(Result(2, ((2.0, "q2"), (1.5, "q1"))), gold, ordered=True)
    <GradeReason.VALUE_MISMATCH: 'value_mismatch'>

    Raises:
        ValueError: gold has more than `MAX_COLUMNS` columns.
    """
    if gold.width > MAX_COLUMNS:
        raise ValueError(f"gold has {gold.width} columns; the protocol allows {MAX_COLUMNS}")
    if isinstance(outcome, GradeReason):
        return outcome
    if outcome.width != gold.width:
        return GradeReason.SHAPE_MISMATCH
    if len(outcome.rows) != len(gold.rows):
        return GradeReason.ROW_COUNT_MISMATCH
    expected, actual = _normalized(gold), _normalized(outcome)
    expected_counts = Counter(expected)
    for order in permutations(range(gold.width)):
        rows = [tuple(row[i] for i in order) for row in actual]
        if (rows == expected) if ordered else (Counter(rows) == expected_counts):
            return None
    return GradeReason.VALUE_MISMATCH


def grade(
    qid: str,
    repetition: int,
    outcome: Result | Literal[GradeReason.ERROR, GradeReason.TIMEOUT],
    gold: Result,
    *,
    ordered: bool,
) -> Grade:
    """Grade one agent run against its question's gold result."""
    reason = check(outcome, gold, ordered=ordered)
    return Grade(qid=qid, repetition=repetition, passed=reason is None, reason=reason)
