"""The evaluation's 40 questions follow the rules of ADRs 0036 and 0038 and the protocol's "Questions"."""

from __future__ import annotations

from collections import Counter

import pytest
import yaml

from eval.column_docs import MARTS_YAML
from eval.questions import (
    AGGREGATE,
    AGGREGATE_GRAIN,
    EVAL_GOLD,
    EVAL_QUESTIONS,
    FLT_MEASURES,
    NO_TRAP_MEASURES,
    PILOT_QUESTIONS,
    TRAPS,
    Category,
    EvalQuestion,
    GoldQuestion,
    fact_columns,
    gold_files,
    load_questions,
    measure,
    read_gold,
)

QUESTIONS = load_questions(EVAL_QUESTIONS, EvalQuestion)
PILOT = load_questions(PILOT_QUESTIONS, GoldQuestion)

# The fact columns a question may need: each one's codes are given by the docs or by
# a dimension's labels, so its text can name them in words. A column whose docs give
# no codes, such as `ASU_NAT`, `BEN_SEX_COD` or `MDT_TYP_COD`, is left out.
ALLOWED = {
    "FLX_ANN_MOI",
    "SOI_ANN",
    "SOI_MOI",
    "BEN_RES_REG",
    "EXE_INS_REG",
    "PRE_INS_REG",
    "ORG_CLE_REG",
    "ETE_REG_COD",
    "ETP_REG_COD",
    "AGE_BEN_SNDS",
    "PSE_ACT_SNDS",
    "PSP_ACT_SNDS",
    *NO_TRAP_MEASURES,
    *FLT_MEASURES,
}


def test_ids_run_q01_to_q40_in_order() -> None:
    assert [question.id for question in QUESTIONS] == [f"Q{n:02}" for n in range(1, 41)]


def test_half_the_questions_have_no_measure_trap() -> None:
    measures = Counter(measure(question.gold_sql) for question in QUESTIONS)
    assert sum(measures[name] for name in NO_TRAP_MEASURES) == 20
    assert sum(measures[name] for name in FLT_MEASURES) == 20


def test_no_flt_measure_has_more_than_6_questions() -> None:
    measures = Counter(measure(question.gold_sql) for question in QUESTIONS)
    assert max(measures[name] for name in FLT_MEASURES) <= 6


def test_each_category_has_at_least_5_questions() -> None:
    categories = Counter(question.category for question in QUESTIONS)
    assert min(categories[category] for category in Category) >= 5


def test_each_trap_is_the_main_one_of_at_least_4_questions() -> None:
    # A trap that is the main one of 4 questions is missing from the 15 holdout
    # questions with probability 13.8% (ADR 0036).
    traps = Counter(question.trap for question in QUESTIONS)
    assert min(traps[trap] for trap in TRAPS) >= 4


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_a_gold_result_has_at_most_3_columns(question: EvalQuestion) -> None:
    for path in gold_files(question, EVAL_GOLD).values():
        assert read_gold(path).width <= 3


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_a_question_needs_only_columns_its_text_can_name(question: EvalQuestion) -> None:
    assert fact_columns(question.gold_sql) <= ALLOWED


def test_no_question_repeats_a_pilot_id_or_text() -> None:
    assert not {q.id for q in QUESTIONS} & {q.id for q in PILOT}
    assert not {q.text.casefold() for q in QUESTIONS} & {q.text.casefold() for q in PILOT}


def test_the_aggregate_grain_matches_its_model() -> None:
    # A changed grain would let a `PRS_REM_MNT` question through that the aggregate answers.
    marts = yaml.safe_load(MARTS_YAML.read_text())
    (aggregate,) = [m for m in marts["models"] if m["name"].upper() == AGGREGATE]
    columns = {column["name"] for column in aggregate["columns"]}
    assert columns - FLT_MEASURES == AGGREGATE_GRAIN
