"""Regrade runs as if each had summed the measure the gold sums: the measure-fixed accuracy.

Each final SQL has its `PRS_` measures renamed to their `FLT_` twins, and
`PRS_ACT_COG` to `FLT_PAI_MNT`, the measure pilot runs most often mistook for the
amount charged; a reimbursement-type filter is left as written. The result is
graded on the gold and accepted results. A proxy: an agent told the measure might
have explored otherwise (ADR 0036).

    PYTHONPATH=. uv run python fixtures/probe/measure_regrade.py <runs directory>...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from docgap.grade import check
from eval.agent.tools import TranspileError, to_duckdb
from eval.agent.warehouse import connect_marts
from eval.questions import (
    PILOT_GOLD,
    PILOT_QUESTIONS,
    load_questions,
    read_accepted,
    read_gold,
    to_result,
)

RENAMES = {
    "PRS_REM_MNT": "FLT_REM_MNT",
    "PRS_PAI_MNT": "FLT_PAI_MNT",
    "PRS_DEP_MNT": "FLT_DEP_MNT",
    "PRS_ACT_QTE": "FLT_ACT_QTE",
    "PRS_ACT_COG": "FLT_PAI_MNT",
}


def with_gold_measure(sql: str) -> str:
    """Rename every measure column in `RENAMES`, keeping its table qualifier.

    >>> with_gold_measure("SELECT SUM(f.PRS_REM_MNT) FROM t AS f")
    'SELECT SUM(f.FLT_REM_MNT) FROM t AS f'
    """

    def rename(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and node.name.upper() in RENAMES:
            node.set("this", exp.to_identifier(RENAMES[node.name.upper()]))
        return node

    return sqlglot.parse_one(sql, dialect="snowflake").transform(rename).sql(dialect="snowflake")


def main(directories: list[str]) -> None:
    questions = {q.id: q for q in load_questions(PILOT_QUESTIONS)}
    con = connect_marts(
        Path("data/agent/sample/ANALYTICS.duckdb"), database="ANALYTICS", schema="MARTS"
    )
    for directory in directories:
        passed = runs = 0
        for run in sorted(Path(directory).glob("P*")):
            transcript = json.loads((run / "transcript.json").read_text())
            question = questions[transcript["qid"]]
            runs += 1
            if not transcript["final_sql"]:
                continue
            try:
                sql = to_duckdb(with_gold_measure(transcript["final_sql"]))
                result = to_result(con.execute(sql).to_arrow_table())
            except (sqlglot.errors.ParseError, TranspileError, duckdb.Error):
                # A query that fails once renamed fails the run, as it would have run.
                continue
            golds = [
                read_gold(PILOT_GOLD / f"{question.id}.parquet"),
                *read_accepted(question, PILOT_GOLD),
            ]
            passed += any(check(result, g, ordered=question.ordered) is None for g in golds)
        print(f"{directory}: {passed}/{runs} measure-fixed")


if __name__ == "__main__":
    main(sys.argv[1:])
