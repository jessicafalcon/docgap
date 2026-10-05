"""Grade an agent run by comparing its result with the gold result, executing nothing."""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from datetime import date, time
from decimal import ROUND_HALF_EVEN, Context, Decimal
from itertools import permutations
from typing import Literal

from docgap.models import Grade, GradeReason

__all__ = ["MAX_COLUMNS", "Outcome", "Result", "check", "grade", "normalize"]

# The protocol's permutation bound: 5! = 120 column orders at most.
MAX_COLUMNS = 5

_CENT = Decimal("0.01")
_COMPOSITE = ("composite",)
# Quantizing needs the integer digits plus 2: a float reaches 309 of them and a DuckDB
# HUGEINT 39, so 400 never overflows.
_CONTEXT = Context(prec=400, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class Result:
    """A query's result set: its column count, kept for a result with no rows, and its rows."""

    width: int
    rows: tuple[tuple[object, ...], ...]

    def __post_init__(self) -> None:
        if self.width < 1:
            raise ValueError("a result has at least one column")
        if any(len(row) != self.width for row in self.rows):
            raise ValueError(f"every row needs {self.width} values")


# What an agent run ends with: a result, or the reason it produced none.
type Outcome = Result | Literal[GradeReason.ERROR, GradeReason.TIMEOUT]


def normalize(value: object) -> Hashable:
    """Map a value to what the protocol compares, tagged so a number never equals a string.

    >>> normalize(2.675) == normalize(Decimal("2.675")) == ("number", Decimal("2.68"))
    True
    >>> normalize(" PHARMACIE "), normalize(date(2025, 1, 1)), normalize(None)
    (('string', 'PHARMACIE'), ('string', '2025-01-01'), ('null',))
    >>> normalize(True) == normalize(1)
    False

    Raises:
        TypeError: a value of no type the protocol names, such as a NumPy integer: the
            harness passed it unconverted, and grading it would fail a correct answer.
    """
    match value:
        case None:
            return ("null",)
        # Before the numbers: `bool` is an `int`, and a boolean is no number.
        case bool():
            return ("bool", value)
        case int() | float() | Decimal():
            # A float through its shortest round-trip text, so FLOAT 2.675 rounds as typed;
            # `float()` first, since a subclass's repr (NumPy's) isn't a number's text.
            exact = Decimal(repr(float(value))) if isinstance(value, float) else Decimal(value)
            if not exact.is_finite():
                return ("nonfinite", str(exact))
            return ("number", exact.quantize(_CENT, context=_CONTEXT))
        case str():
            return ("string", value.strip())
        case date() | time():
            return ("string", value.isoformat())
        case list() | tuple() | dict():
            # A list or a struct the agent selected. Gold results hold scalars only, so
            # this equals no gold value, whatever it holds.
            return _COMPOSITE
        case _:
            raise TypeError(f"cannot grade a value of type {type(value).__name__}")


def _normalized(result: Result) -> list[tuple[Hashable, ...]]:
    return [tuple(normalize(value) for value in row) for row in result.rows]


def check(
    outcome: Outcome,
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
        ValueError: gold has more than `MAX_COLUMNS` columns, or a list or a struct.
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
    if any(value == _COMPOSITE for row in expected for value in row):
        raise ValueError("gold results hold scalars only")
    expected_counts = Counter(expected)
    for order in permutations(range(gold.width)):
        rows = [tuple(row[i] for i in order) for row in actual]
        if (rows == expected) if ordered else (Counter(rows) == expected_counts):
            return None
    return GradeReason.VALUE_MISMATCH


def grade(
    qid: str,
    repetition: int,
    outcome: Outcome,
    gold: Result,
    *,
    ordered: bool,
    accepted: Sequence[Result] = (),
) -> Grade:
    """Grade one agent run against its question's gold result and the results it accepts.

    A run that matches an accepted result passes; one that matches none fails with
    the gold's reason code.

    >>> gold, other = Result(1, ((1,),)), Result(1, ((2,),))
    >>> grade("Q1", 1, other, gold, ordered=False, accepted=[other]).passed
    True
    """
    reason = check(outcome, gold, ordered=ordered)
    if reason is not None and any(check(outcome, a, ordered=ordered) is None for a in accepted):
        reason = None
    return Grade(qid=qid, repetition=repetition, passed=reason is None, reason=reason)
