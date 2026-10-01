"""The grader applies every comparison rule of the protocol, and each check gives its reason code."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

import pytest

from docgap.grade import MAX_COLUMNS, Result, check, grade
from docgap.models import Grade, GradeReason

GOLD = Result(2, (("pharmacie", Decimal("10.50")), ("dentaire", Decimal("3.00"))))


def _one(value: object) -> Result:
    return Result(1, ((value,),))


@pytest.mark.parametrize("reason", [GradeReason.ERROR, GradeReason.TIMEOUT])
def test_a_run_without_a_result_fails_with_its_reason(
    reason: Literal[GradeReason.ERROR, GradeReason.TIMEOUT],
) -> None:
    assert check(reason, GOLD, ordered=False) is reason


@pytest.mark.parametrize(
    "result",
    [Result(1, (("pharmacie",), ("dentaire",))), Result(3, ()), Result(1, ())],
)
def test_a_different_column_count_is_a_shape_mismatch(result: Result) -> None:
    assert check(result, GOLD, ordered=False) is GradeReason.SHAPE_MISMATCH


def test_column_names_play_no_part_and_an_empty_gold_matches_an_empty_result() -> None:
    assert check(Result(2, ()), Result(2, ()), ordered=True) is None


@pytest.mark.parametrize("rows", [(("pharmacie", 10.5),), (*GOLD.rows, ("optique", 1))])
def test_a_different_row_count_is_a_row_count_mismatch(
    rows: tuple[tuple[object, ...], ...],
) -> None:
    assert check(Result(2, rows), GOLD, ordered=False) is GradeReason.ROW_COUNT_MISMATCH


def test_shape_is_checked_before_row_count() -> None:
    assert check(Result(1, ()), GOLD, ordered=False) is GradeReason.SHAPE_MISMATCH


def test_a_wrong_value_is_a_value_mismatch() -> None:
    result = Result(2, (("pharmacie", 10.51), ("dentaire", 3)))
    assert check(result, GOLD, ordered=False) is GradeReason.VALUE_MISMATCH


def test_the_result_columns_may_come_in_any_order() -> None:
    result = Result(2, ((10.5, "pharmacie"), (3, "dentaire")))
    assert check(result, GOLD, ordered=True) is None


def test_five_columns_are_matched_under_any_of_their_orders() -> None:
    gold = Result(MAX_COLUMNS, ((1, 2, 3, 4, 5), (6, 7, 8, 9, 10)))
    result = Result(MAX_COLUMNS, ((5, 3, 1, 4, 2), (10, 8, 6, 9, 7)))
    assert check(result, gold, ordered=True) is None


def test_one_column_order_must_fit_every_row() -> None:
    gold = Result(2, ((1, 2), (3, 4)))
    result = Result(2, ((1, 2), (4, 3)))
    assert check(result, gold, ordered=False) is GradeReason.VALUE_MISMATCH


def test_unordered_rows_compare_as_multisets() -> None:
    shuffled = Result(2, tuple(reversed(GOLD.rows)))
    assert check(shuffled, GOLD, ordered=False) is None
    assert check(shuffled, GOLD, ordered=True) is GradeReason.VALUE_MISMATCH


def test_a_multiset_counts_repeated_rows() -> None:
    gold = Result(1, (("a",), ("a",), ("b",)))
    assert (
        check(Result(1, (("a",), ("b",), ("b",))), gold, ordered=False)
        is GradeReason.VALUE_MISMATCH
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2.675, Decimal("2.675")),  # a float through its shortest text: 2.68 both
        (Decimal("0.125"), Decimal("0.12")),  # half-even rounds down to the even cent
        (Decimal("0.135"), Decimal("0.14")),  # and up to it
        (3, Decimal("3.00")),
        (1.004, 1),
        (-0.001, 0),
        (1e300, Decimal("1e300")),  # beyond the default 28-digit precision
        (2**120, Decimal(2**120)),
        (float("inf"), Decimal("Infinity")),
        (None, None),
        ("  Pharmacie ", "Pharmacie"),
        (date(2025, 1, 1), "2025-01-01"),
        (datetime(2025, 1, 1, 8, 30, tzinfo=UTC), "2025-01-01T08:30:00+00:00"),
        ([1, 2], [1, 2]),
    ],
)
def test_values_that_normalize_alike_match(value: object, expected: object) -> None:
    assert check(_one(value), _one(expected), ordered=True) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, "1"),  # a number never equals a string
        (Decimal("1.00"), "1.00"),
        (True, 1),  # a boolean is no number
        (None, 0),  # NULL equals NULL and nothing else
        (None, ""),
        ("pharmacie", "PHARMACIE"),  # case is kept
        (
            1.005,
            1.01,
        ),  # 1.005 is 1.00499… as a float, but its shortest text rounds half-even to 1.00
        ([1, 2], (1, 2)),
    ],
)
def test_values_that_normalize_apart_mismatch(value: object, expected: object) -> None:
    assert check(_one(value), _one(expected), ordered=True) is GradeReason.VALUE_MISMATCH


def test_gold_wider_than_the_permutation_bound_is_refused() -> None:
    wide = Result(MAX_COLUMNS + 1, ())
    with pytest.raises(ValueError, match="6 columns"):
        check(wide, wide, ordered=False)


def test_a_row_of_the_wrong_width_is_refused() -> None:
    with pytest.raises(ValueError, match="every row needs 2 values"):
        Result(2, (("a",),))


def test_grade_records_a_pass_or_the_failed_check() -> None:
    assert grade("q01", 2, GOLD, GOLD, ordered=True) == Grade(
        qid="q01", repetition=2, passed=True, reason=None
    )
    assert grade("q01", 3, GradeReason.TIMEOUT, GOLD, ordered=True) == Grade(
        qid="q01", repetition=3, passed=False, reason=GradeReason.TIMEOUT
    )
