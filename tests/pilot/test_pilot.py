"""The pilot runner makes every run of a pass in order, writes and grades each one, stops on a
harness fault with the runs it never made, and resumes within one pass's budget."""

from __future__ import annotations

import io
import itertools
import uuid
from collections.abc import Iterator, Mapping
from decimal import Decimal
from pathlib import Path

import anthropic
import httpx2
import pytest
from conftest import AGENT_MARTS
from pydantic import JsonValue

from docgap.config import AgentConfig, LlmConfig, Price, load_config
from docgap.grade import Result, grade
from docgap.llm import Budget, LlmClient, ResponseCache
from docgap.log import EventLog
from docgap.manifest import Marts
from docgap.models import GradeReason
from eval.agent.loop import Transcript
from eval.agent.tools import AgentTools
from eval.agent.warehouse import Warehouse
from eval.pilot import (
    Docs,
    PassStatus,
    PilotInputs,
    PilotManifest,
    RunGrade,
    grade_run,
    input_hashes,
    lock_pass,
    plan,
    run_pass,
    unfinished_passes,
)
from eval.questions import PILOT_QUESTIONS, Category, GoldQuestion, load_questions, read_gold

ROOT = Path(__file__).resolve().parents[2]
MODELS = ["model-a", "model-b"]
AGENT = AgentConfig(
    statement_timeout_seconds=60, row_cap=200, max_tool_calls=8, max_tokens=1000, run_attempts=3
)
COUNT = GoldQuestion(
    id="P01",
    category=Category.REGION,
    text="How many reimbursement lines are there?",
    gold_sql="SELECT COUNT(*) FROM FCT_REIMBURSEMENTS",
    ordered=False,
)
REGIONS = GoldQuestion(
    id="P02",
    category=Category.REGION,
    text="Which regions are there, by code and label?",
    gold_sql="SELECT BEN_RES_REG, BEN_RES_REG_LIB FROM DIM_REGION",
    ordered=False,
)
GOLD = {
    "P01": Result(1, ((300,),)),
    "P02": Result(2, ((11, "Ile-de-France"), (24, "Centre-Val de Loire"))),
}
RIGHT = {COUNT.text: COUNT.gold_sql, REGIONS.text: REGIONS.gold_sql}
HASHES = {"agent_db": "0" * 64}
_ids = itertools.count(1)


def _final(sql: str) -> JsonValue:
    use = {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": "final_answer"}
    return {
        "id": f"msg_{next(_ids)}",
        "type": "message",
        "role": "assistant",
        "model": "model",
        "content": [{**use, "input": {"final_sql": sql}}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


class FakeModel:
    """Answers each question at once with the SQL `answers` gives its text, or raises `error`."""

    def __init__(self, answers: Mapping[str, str], error: Exception | None = None) -> None:
        self._answers = answers
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def __call__(self, model: str, params: Mapping[str, JsonValue]) -> JsonValue:
        messages = params["messages"]
        assert isinstance(messages, list)
        assert isinstance(messages[0], dict)
        question = messages[0]["content"]
        assert isinstance(question, str)
        self.calls.append((model, question))
        if self._error is not None:
            raise self._error
        return _final(self._answers[question])


class Pilot:
    """A pass over the test warehouse; each `run` is a new process on the same directories."""

    def __init__(self, agent_db: Path, tmp_path: Path) -> None:
        self.warehouse = Warehouse(
            agent_db, database="ANALYTICS", schema="MARTS", timeout_seconds=60
        )
        documented = Marts(
            "ANALYTICS",
            "MARTS",
            AGENT_MARTS.tables,
            {"ANALYTICS.MARTS.DIM_REGION.BEN_RES_REG": "Région de résidence"},
        )
        self.tools = {
            Docs.NO_DOCS: AgentTools(self.warehouse, AGENT_MARTS, row_cap=AGENT.row_cap),
            Docs.FULL_DOCS: AgentTools(self.warehouse, documented, row_cap=AGENT.row_cap),
        }
        self.out = tmp_path / "pass-1"
        self.cache = tmp_path / "cache"
        self.log = io.StringIO()

    def run(
        self,
        model: FakeModel,
        *,
        limit: int | None = None,
        max_calls: int = 100,
        hashes: Mapping[str, str] = HASHES,
    ) -> PilotManifest:
        config = LlmConfig(
            timeout_seconds=5,
            max_retries=0,
            max_calls=max_calls,
            max_spend_usd=10,
            prices={name: Price(input=1, output=5) for name in MODELS},
        )
        log = EventLog(self.log, "pilot-1")
        llm = LlmClient(model, ResponseCache(self.cache), Budget(config), log)
        inputs = PilotInputs([COUNT, REGIONS], GOLD, MODELS, self.tools, hashes)
        return run_pass(
            inputs,
            llm=llm,
            agent_config=AGENT,
            out_dir=self.out,
            run_id="pilot-1",
            limit=limit,
            log=log,
        )

    def made(self) -> list[str]:
        return sorted(
            str(path.parent.relative_to(self.out)) for path in self.out.glob("*/*/transcript.json")
        )


@pytest.fixture
def pilot(agent_db: Path, tmp_path: Path) -> Iterator[Pilot]:
    pilot = Pilot(agent_db, tmp_path)
    yield pilot
    pilot.warehouse.close()


def test_runs_go_by_repetition_then_question_then_configuration() -> None:
    keys = [run.key for run in plan([COUNT, REGIONS], MODELS, 2)]
    assert keys[:5] == [
        "model-a.no_docs/P01.r1",
        "model-a.full_docs/P01.r1",
        "model-b.no_docs/P01.r1",
        "model-b.full_docs/P01.r1",
        "model-a.no_docs/P02.r1",
    ]
    assert keys[8] == "model-a.no_docs/P01.r2"
    assert len(keys) == len(set(keys)) == 16


def test_a_pass_writes_and_grades_every_run(pilot: Pilot) -> None:
    # P02 answered with one region of the two: a row count mismatch.
    wrong = {**RIGHT, REGIONS.text: "SELECT 11, 'Ile-de-France'"}
    manifest = pilot.run(FakeModel(wrong))

    assert manifest.status is PassStatus.COMPLETED
    assert manifest.not_made == []
    assert manifest.stops == []
    assert manifest.inputs == HASHES
    assert set(manifest.configurations) == {
        "model-a.no_docs",
        "model-a.full_docs",
        "model-b.no_docs",
        "model-b.full_docs",
    }
    for counts in manifest.configurations.values():
        assert (counts.runs, counts.passed, counts.not_made, counts.harness_errors) == (6, 3, 0, 0)
    run = pilot.out / "model-b.full_docs" / "P02.r3"
    transcript = Transcript.model_validate_json((run / "transcript.json").read_bytes())
    assert (transcript.qid, transcript.repetition, transcript.outcome) == ("P02", 3, "result")
    assert transcript.run_id == "pilot-1.model-b.full_docs"
    recorded = RunGrade.model_validate_json((run / "grade.json").read_bytes())
    assert recorded.grade is not None
    assert recorded.grade.reason is GradeReason.ROW_COUNT_MISMATCH
    assert read_gold(run / "rows.parquet") == Result(2, ((11, "Ile-de-France"),))
    # The full-docs run replays its no-docs twin's reply: the same first request.
    assert manifest.model_calls == 12


def test_a_smoke_run_makes_the_first_runs_in_every_configuration(pilot: Pilot) -> None:
    manifest = pilot.run(FakeModel(RIGHT), limit=4)

    assert manifest.status is PassStatus.IN_PROGRESS
    assert [counts.runs for counts in manifest.configurations.values()] == [1, 1, 1, 1]
    assert len(manifest.not_made) == 20


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CAST(1.5 AS DECIMAL(4, 2)), CAST(2.675 AS DOUBLE), CAST('2025-01-01' AS DATE)",
        "SELECT CAST('2025-01-01 10:00:00+02:00' AS TIMESTAMP_TZ), TRUE, NULL",
        "SELECT BEN_RES_REG, BEN_RES_REG_LIB FROM DIM_REGION",
        "SELECT BEN_RES_REG FROM DIM_REGION WHERE BEN_RES_REG < 0",
        "SELECT FLX_ANN_MOI, SUM(PRS_PAI_MNT) FROM FCT_REIMBURSEMENTS GROUP BY 1",
    ],
)
def test_a_grade_read_again_from_disk_equals_the_one_recorded(
    pilot: Pilot, tmp_path: Path, sql: str
) -> None:
    outcome = pilot.tools[Docs.NO_DOCS].run_final(sql)
    transcript = _transcript(len(outcome.rows))
    rows = tmp_path / "rows.parquet"
    # Gold one value off, and gold equal to the run: a failed grade and a passed one.
    for gold in (Result(outcome.width, ((Decimal(7),) * outcome.width,)), outcome):
        recorded = grade_run(transcript, outcome, gold, ordered=False, rows=rows)
        assert recorded.grade is not None
        assert grade("P01", 1, read_gold(rows), gold, ordered=False) == recorded.grade


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("SELECT INTERVAL '1 DAY'", "TypeError: cannot grade a value of type timedelta"),
        ("SELECT TO_BINARY('6162', 'HEX')", "TypeError: cannot grade a value of type bytes"),
    ],
)
def test_a_value_the_grader_refuses_is_a_harness_error(
    pilot: Pilot, tmp_path: Path, sql: str, error: str
) -> None:
    outcome = pilot.tools[Docs.NO_DOCS].run_final(sql)
    rows = tmp_path / "rows.parquet"

    recorded = grade_run(_transcript(1), outcome, GOLD["P01"], ordered=False, rows=rows)

    assert recorded == RunGrade(grade=None, harness_error=error)
    assert not rows.exists()


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (uuid.UUID(int=1), "TypeError: cannot grade a value of type UUID"),
        # The grader takes it; Parquet can't store it.
        (2**70, "OverflowError"),
    ],
)
def test_a_value_the_harness_cannot_keep_is_a_harness_error(
    tmp_path: Path, value: object, error: str
) -> None:
    rows = tmp_path / "rows.parquet"
    outcome = Result(1, ((value,),))

    recorded = grade_run(_transcript(1), outcome, GOLD["P01"], ordered=False, rows=rows)

    assert recorded.harness_error is not None
    assert recorded.harness_error.startswith(error)
    assert not rows.exists()


def test_a_harness_error_is_recorded_and_the_pass_goes_on(pilot: Pilot) -> None:
    # Gold's shape, so the grader reaches the value it refuses.
    manifest = pilot.run(FakeModel({**RIGHT, COUNT.text: "SELECT INTERVAL '1 DAY'"}))

    assert manifest.status is PassStatus.COMPLETED
    for counts in manifest.configurations.values():
        assert (counts.runs, counts.passed, counts.harness_errors) == (6, 3, 3)


def test_the_budget_stops_the_pass_and_lists_the_runs_not_made(pilot: Pilot) -> None:
    # Live calls 1-3 make runs 1-6, the full-docs ones from the cache; run 7 finds none left.
    manifest = pilot.run(FakeModel(RIGHT), max_calls=3)

    assert manifest.status is PassStatus.STOPPED
    assert manifest.stops == ["BudgetExhausted: 3 calls reached the limit 3"]
    assert manifest.model_calls == 3
    assert manifest.not_made[0] == "model-b.no_docs/P02.r1"
    assert len(manifest.not_made) == 18
    assert [counts.runs for counts in manifest.configurations.values()] == [2, 2, 1, 1]
    assert len(pilot.made()) == 6


def test_a_resumed_pass_skips_written_runs_and_counts_from_the_recorded_budget(
    pilot: Pilot,
) -> None:
    pilot.run(FakeModel(RIGHT), limit=2)
    model = FakeModel(RIGHT)

    # One call recorded, so 2 of the 3 remain: runs 3-6 are made and run 7 is refused.
    manifest = pilot.run(model, max_calls=3)

    assert model.calls == [("model-b", COUNT.text), ("model-a", REGIONS.text)]
    assert manifest.model_calls == 3
    assert manifest.status is PassStatus.STOPPED
    assert len(pilot.made()) == 6


def test_a_stopped_pass_resumed_to_the_end_is_completed(pilot: Pilot) -> None:
    pilot.run(FakeModel(RIGHT), max_calls=3)

    manifest = pilot.run(FakeModel(RIGHT))

    assert manifest.status is PassStatus.COMPLETED
    assert manifest.stops == ["BudgetExhausted: 3 calls reached the limit 3"]
    assert manifest.model_calls == 12


def test_a_permanent_api_error_stops_the_pass(pilot: Pilot) -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=request), body=None
    )

    manifest = pilot.run(FakeModel(RIGHT, error))

    assert manifest.status is PassStatus.STOPPED
    assert manifest.stops == ["AuthenticationError: invalid x-api-key"]
    assert len(manifest.not_made) == 24
    assert pilot.made() == []


def test_an_unexpected_error_saves_the_calls_counted_and_propagates(pilot: Pilot) -> None:
    pilot.run(FakeModel(RIGHT), limit=1)

    with pytest.raises(RuntimeError, match="connection pool broke"):
        pilot.run(FakeModel(RIGHT, RuntimeError("connection pool broke")))

    manifest = PilotManifest.model_validate_json((pilot.out / "manifest.json").read_bytes())
    assert manifest.status is PassStatus.IN_PROGRESS
    # The first run's call, and the second run's call that failed.
    assert manifest.model_calls == 2
    assert manifest.stops == []


def test_a_second_process_is_refused_by_the_lock(tmp_path: Path) -> None:
    lock = lock_pass(tmp_path / "work")

    with pytest.raises(FileExistsError, match="another process runs this pass"):
        lock_pass(tmp_path / "work")

    pid, host = lock.read_text().split()
    assert pid.isdigit()
    assert host


def test_a_pass_that_made_a_run_holds_back_a_new_pass_until_completed(pilot: Pilot) -> None:
    pass_2 = pilot.out.parent / "pass-2"
    error = anthropic.AuthenticationError(
        "invalid x-api-key",
        response=httpx2.Response(401, request=httpx2.Request("POST", "https://api.anthropic.com")),
        body=None,
    )
    # Stopped before its first run: void, so it holds nothing back.
    pilot.run(FakeModel(RIGHT, error))
    assert unfinished_passes(pilot.out.parent, pass_2) == []

    pilot.run(FakeModel(RIGHT), limit=1)
    assert unfinished_passes(pilot.out.parent, pass_2) == ["pass-1"]
    # The pass itself may always resume.
    assert unfinished_passes(pilot.out.parent, pilot.out) == []

    pilot.run(FakeModel(RIGHT))
    assert unfinished_passes(pilot.out.parent, pass_2) == []


def test_a_resume_refuses_other_inputs(pilot: Pilot) -> None:
    pilot.run(FakeModel(RIGHT), limit=1)

    with pytest.raises(ValueError, match=r"other inputs \['agent_db', 'gold.P01'\]"):
        pilot.run(FakeModel(RIGHT), hashes={"agent_db": "1" * 64, "gold.P01": "2" * 64})


def test_a_run_a_crash_left_without_a_transcript_is_made_again(pilot: Pilot) -> None:
    run = pilot.out / "model-a.no_docs" / "P01.r1"
    run.mkdir(parents=True)
    (run / "rows.parquet").write_bytes(b"half a file")

    pilot.run(FakeModel({**RIGHT, COUNT.text: "SELECT nope FROM FCT_REIMBURSEMENTS"}), limit=1)

    transcript = Transcript.model_validate_json((run / "transcript.json").read_bytes())
    assert transcript.outcome == "error"
    assert not (run / "rows.parquet").exists()


def test_the_input_hashes_ignore_the_budget_but_not_the_timeout(
    agent_db: Path, tmp_path: Path
) -> None:
    config = load_config(ROOT / "docgap.toml")
    manifests = {docs: tmp_path / f"{docs}.json" for docs in Docs}
    for docs, path in manifests.items():
        path.write_text(docs)
    questions = load_questions(PILOT_QUESTIONS)

    def hashes(llm: LlmConfig) -> dict[str, str]:
        changed = config.model_copy(update={"llm": llm})
        return input_hashes(changed, manifests=manifests, agent_db=agent_db, questions=questions)

    base = hashes(config.llm)
    assert {key.split(".", 1)[0] for key in base} == {
        "agent_db",
        "call_site",
        "code",
        "column_docs",
        "config",
        "gold",
        "manifest",
        "questions",
        "sample_lock",
    }
    assert {key for key in base if key.startswith(("config.", "call_site."))} == {
        "config.agent",
        "config.llm",
        "config.pilot",
        *(f"call_site.{model}" for model in config.pilot.models),
    }
    assert {"code.src/docgap/grade.py", "code.eval/agent/loop.py", "code.uv.lock"} <= set(base)
    assert base == hashes(config.llm.model_copy(update={"max_calls": 1, "max_spend_usd": 1.0}))
    assert base != hashes(config.llm.model_copy(update={"timeout_seconds": 1.0}))
    assert {f"gold.{q.id}" for q in questions} <= set(base)
    assert base["manifest.no_docs"] != base["manifest.full_docs"]


def _transcript(rows: int) -> Transcript:
    return Transcript.model_validate(
        {
            "run_id": "pilot-1",
            "qid": "P01",
            "repetition": 1,
            "call_site": {
                "model": "model-a",
                "sampling": {},
                "prompt_version": "agent-2",
                "prompt_sha256": "0" * 64,
            },
            "failed_calls": 0,
            "messages": [],
            "tool_calls": 0,
            "final_sql": "SELECT 1",
            "outcome": "result",
            "error_cause": None,
            "error_detail": None,
            "result_rows": rows,
        },
        strict=False,
    )
