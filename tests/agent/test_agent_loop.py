"""The agent loop answers with one SQL query inside its tool budget, records how each run
ended, repeats a run after an infrastructure failure, and replays from the model cache."""

from __future__ import annotations

import io
import itertools
import json
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from conftest import SLOW_SQL
from pydantic import JsonValue, ValidationError

from docgap.config import AgentConfig, LlmConfig, Price, load_config
from docgap.grade import Result
from docgap.llm import Budget, ContextExceeded, LlmClient, ResponseCache, TransientError
from docgap.log import EventLog
from docgap.manifest import Marts
from docgap.models import GradeReason, ModelSettings
from eval.agent.loop import (
    PROMPT_VERSION,
    AgentRun,
    ErrorCause,
    Question,
    Transcript,
    call_site,
    run_agent,
)
from eval.agent.tools import AgentTools
from eval.agent.warehouse import Warehouse

MODEL = "claude-test"
QUESTION = Question("p01", "How many reimbursement lines are there?")
CONFIG = AgentConfig(
    statement_timeout_seconds=60, row_cap=200, max_tool_calls=8, max_tokens=1000, run_attempts=3
)
COUNT = "SELECT COUNT(*) FROM FCT_REIMBURSEMENTS"
FCT = "ANALYTICS.MARTS.FCT_REIMBURSEMENTS"
_ids = itertools.count(1)
ROOT = Path(__file__).resolve().parents[2]
PROMPT_SHA256 = "b98b368046aec315913359dc1810817a60be6156edfec2a66cfd9ca1f5a5dea6"


def _docs(text: str | None) -> Marts:
    described = {f"{FCT}.FLX_ANN_MOI": text} if text else {}
    return Marts(
        "ANALYTICS",
        "MARTS",
        {"FCT_REIMBURSEMENTS": {}, "DIM_REGION": {}},
        described,
    )


def _use(name: str, **arguments: JsonValue) -> JsonValue:
    return {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": arguments}


def _reply(*blocks: JsonValue, stop: str = "tool_use") -> JsonValue:
    return {
        "id": f"msg_{next(_ids)}",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": list(blocks),
        "stop_reason": stop,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


class ScriptedModel:
    """Plays scripted replies in order; a live call past the script fails the test."""

    def __init__(self, *outcomes: JsonValue | Exception) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[Mapping[str, JsonValue]] = []

    def __call__(self, model: str, params: Mapping[str, JsonValue]) -> JsonValue:
        self.requests.append(json.loads(json.dumps(params)))
        assert self._outcomes, "a live model call the script didn't expect"
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Harness:
    """The agent's dependencies over one warehouse and one model cache."""

    def __init__(self, agent_db: Path, cache: Path, *, timeout_seconds: float = 60) -> None:
        self.warehouse = Warehouse(
            agent_db, database="ANALYTICS", schema="MARTS", timeout_seconds=timeout_seconds
        )
        self.cache = cache
        self.log = io.StringIO()

    def run(
        self,
        model: ScriptedModel | None,
        *,
        repetition: int = 1,
        docs: Marts | None = None,
        site: ModelSettings | None = None,
    ) -> AgentRun:
        llm_config = LlmConfig(
            timeout_seconds=5,
            max_retries=0,
            max_calls=100,
            max_spend_usd=10,
            prices={MODEL: Price(input=1, output=5)},
        )
        llm = LlmClient(
            model, ResponseCache(self.cache), Budget(llm_config), EventLog(self.log, "r1")
        )
        tools = AgentTools(self.warehouse, docs or _docs(None), row_cap=CONFIG.row_cap)
        return run_agent(
            QUESTION,
            repetition,
            run_id="r1",
            site=site or ModelSettings(model=MODEL, sampling={}),
            llm=llm,
            tools=tools,
            config=CONFIG,
            log=EventLog(self.log, "r1"),
        )


@pytest.fixture
def harness(agent_db: Path, tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(agent_db, tmp_path / "cache")
    yield harness
    harness.warehouse.close()


def test_final_answer_is_run_and_the_run_is_transcribed(harness: Harness) -> None:
    model = ScriptedModel(
        _reply({"type": "text", "text": "Let me look."}, _use("list_tables")),
        _reply(_use("run_sql", sql="SELECT 1")),
        _reply(_use("final_answer", final_sql=COUNT)),
    )
    run = harness.run(model)
    assert run.outcome == Result(1, ((300,),))
    transcript = run.transcript
    assert (transcript.outcome, transcript.tool_calls, transcript.failed_calls) == ("result", 2, 0)
    assert (transcript.final_sql, transcript.result_rows, transcript.error_cause) == (
        COUNT,
        1,
        None,
    )
    roles = [turn["role"] for turn in transcript.messages if isinstance(turn, dict)]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]
    # The SQL and its result are in the transcript; the event log holds neither.
    assert "SELECT 1" in transcript.model_dump_json()
    assert "SELECT" not in harness.log.getvalue()


def test_request_sends_default_settings_only(harness: Harness) -> None:
    # No sampling, thinking, effort or tool choice: each model runs at its defaults
    # (ADR 0027), and the final answer is a tool the prompt asks for. The one addition
    # is automatic prompt caching, which changes the cost and never the reply (ADR 0030).
    model = ScriptedModel(_reply(_use("final_answer", final_sql=COUNT)))
    harness.run(model)
    request = model.requests[0]
    assert sorted(request) == ["cache_control", "max_tokens", "messages", "system", "tools"]
    assert request["cache_control"] == {"type": "ephemeral"}
    tools = request["tools"]
    assert isinstance(tools, list)
    assert [tool["name"] for tool in tools if isinstance(tool, dict)] == [
        "list_tables",
        "describe",
        "run_sql",
        "final_answer",
    ]


def test_tool_budget_ends_without_a_final_answer(harness: Harness) -> None:
    calls = [_reply(_use("run_sql", sql="SELECT 1")) for _ in range(7)]
    # The 8th turn asks for three calls: one fits the budget, two are refused.
    eighth = _reply(*(_use("run_sql", sql="SELECT 1") for _ in range(3)))
    ninth = _reply(_use("run_sql", sql="SELECT 2"))
    run = harness.run(ScriptedModel(*calls, eighth, ninth))
    transcript = run.transcript
    assert run.outcome is GradeReason.ERROR
    assert (transcript.error_cause, transcript.tool_calls) == (ErrorCause.NO_FINAL_ANSWER, 8)
    assert transcript.error_detail == "no final answer within 8 tool calls"
    last_results = transcript.messages[-2]
    assert isinstance(last_results, dict)
    content = last_results["content"]
    assert isinstance(content, list)
    assert [block["is_error"] for block in content[:3] if isinstance(block, dict)] == [
        False,
        True,
        True,
    ]
    assert content[3] == {
        "type": "text",
        "text": "That was your last tool call. Call final_answer now.",
    }


def test_answer_in_text_is_no_final_answer(harness: Harness) -> None:
    run = harness.run(ScriptedModel(_reply({"type": "text", "text": COUNT}, stop="end_turn")))
    assert run.outcome is GradeReason.ERROR
    assert run.transcript.error_cause is ErrorCause.NO_FINAL_ANSWER
    assert run.transcript.error_detail == "stopped with stop_reason end_turn"


@pytest.mark.parametrize(
    ("final_sql", "cause"),
    [
        ("SELECT COUNT(* FROM FCT_REIMBURSEMENTS", ErrorCause.TRANSPILE),
        ("SELECT NO_SUCH_COLUMN FROM FCT_REIMBURSEMENTS", ErrorCause.SQL),
        (3, ErrorCause.MALFORMED_ANSWER),
    ],
)
def test_failed_final_answer_is_an_error_with_its_cause(
    harness: Harness, final_sql: JsonValue, cause: ErrorCause
) -> None:
    run = harness.run(ScriptedModel(_reply(_use("final_answer", final_sql=final_sql))))
    assert run.outcome is GradeReason.ERROR
    assert run.transcript.error_cause is cause
    assert run.transcript.error_detail


def test_final_answer_past_the_timeout_is_a_timeout(agent_db: Path, tmp_path: Path) -> None:
    harness = Harness(agent_db, tmp_path / "cache", timeout_seconds=0.2)
    try:
        run = harness.run(ScriptedModel(_reply(_use("final_answer", final_sql=SLOW_SQL))))
    finally:
        harness.warehouse.close()
    assert run.outcome is GradeReason.TIMEOUT
    assert (run.transcript.outcome, run.transcript.error_cause) == ("timeout", None)


def test_infrastructure_failure_sends_the_failed_call_again(harness: Harness) -> None:
    model = ScriptedModel(
        _reply(_use("run_sql", sql="SELECT 1")),
        TransientError("InternalServerError"),
        _reply(_use("final_answer", final_sql=COUNT)),
    )
    run = harness.run(model)
    assert run.outcome == Result(1, ((300,),))
    assert (run.transcript.failed_calls, len(model.requests)) == (1, 3)
    # The failed call is sent again unchanged, and the query before it ran once only.
    assert model.requests[1] == model.requests[2]
    assert harness.log.getvalue().count('"tool":"run_sql"') == 1


def test_third_failed_call_ends_the_run_as_error(harness: Harness) -> None:
    model = ScriptedModel(
        TransientError("APIConnectionError"),
        _reply(_use("list_tables")),
        TransientError("APIConnectionError"),
        TransientError("APITimeoutError"),
    )
    run = harness.run(model)
    assert run.outcome is GradeReason.ERROR
    transcript = run.transcript
    assert (transcript.error_cause, transcript.failed_calls) == (ErrorCause.INFRASTRUCTURE, 3)
    assert transcript.error_detail == "APITimeoutError"


def test_input_past_the_context_window_is_an_error_that_replays(harness: Harness) -> None:
    model = ScriptedModel(_reply(_use("list_tables")), ContextExceeded("prompt is too long"))
    online = harness.run(model)
    assert online.outcome is GradeReason.ERROR
    assert online.transcript.error_cause is ErrorCause.CONTEXT_EXCEEDED
    # The overflow is cached like a reply, so an offline rerun reproduces it.
    assert harness.run(None).transcript == online.transcript


def test_reply_cut_at_the_context_window_is_an_error(harness: Harness) -> None:
    cut = _reply({"type": "text", "text": "Let me"}, stop="model_context_window_exceeded")
    run = harness.run(ScriptedModel(cut))
    assert run.transcript.error_cause is ErrorCause.CONTEXT_EXCEEDED


def test_thinking_blocks_go_back_unchanged(harness: Harness) -> None:
    thinking: JsonValue = {"type": "thinking", "thinking": "", "signature": "c2lnbmF0dXJl"}
    model = ScriptedModel(
        _reply(thinking, _use("list_tables")), _reply(_use("final_answer", final_sql=COUNT))
    )
    harness.run(model)
    sent = model.requests[1]["messages"]
    assert isinstance(sent, list)
    assistant = sent[1]
    assert isinstance(assistant, dict)
    content = assistant["content"]
    assert isinstance(content, list)
    assert content[0] == thinking


def test_final_answer_beside_a_tool_call_ends_the_run(harness: Harness) -> None:
    reply = _reply(_use("run_sql", sql="SELECT 1"), _use("final_answer", final_sql=COUNT))
    run = harness.run(ScriptedModel(reply))
    assert run.outcome == Result(1, ((300,),))
    # The other call is neither run nor counted.
    assert run.transcript.tool_calls == 0
    assert '"tool":"run_sql"' not in harness.log.getvalue()


def test_call_site_with_sampling_is_refused(harness: Harness) -> None:
    with pytest.raises(ValueError, match="ADR 0027"):
        harness.run(ScriptedModel(), site=ModelSettings(model=MODEL, sampling={"temperature": 0}))


def test_repetition_is_in_the_cache_key_and_the_arm_is_not(harness: Harness) -> None:
    script = (_reply(_use("list_tables")), _reply(_use("final_answer", final_sql=COUNT)))
    first = harness.run(ScriptedModel(*script), docs=_docs(None))
    # Another arm's docs, but the agent never read them: every call replays.
    other_arm = harness.run(ScriptedModel(), docs=_docs("Mois de traitement"))
    assert other_arm.transcript == first.transcript
    # Another repetition is its own draw.
    second = ScriptedModel(*script)
    harness.run(second, repetition=2)
    assert len(second.requests) == 2


def test_arms_diverge_at_the_first_tool_result_that_differs(harness: Harness) -> None:
    script = (
        _reply(_use("describe", table="FCT_REIMBURSEMENTS")),
        _reply(_use("final_answer", final_sql=COUNT)),
    )
    harness.run(ScriptedModel(*script), docs=_docs(None))
    other_arm = ScriptedModel(script[1])
    harness.run(other_arm, docs=_docs("Mois de traitement"))
    # The first call replays; the second follows a different `describe` result.
    assert len(other_arm.requests) == 1


def test_offline_rerun_reproduces_the_transcript(harness: Harness) -> None:
    online = harness.run(
        ScriptedModel(
            _reply(_use("run_sql", sql="SELECT 1")), _reply(_use("final_answer", final_sql=COUNT))
        )
    )
    offline = harness.run(None)
    assert offline.transcript.model_dump_json() == online.transcript.model_dump_json()
    assert offline.outcome == online.outcome


def test_transcript_records_the_call_site_as_it_ran(harness: Harness) -> None:
    run = harness.run(ScriptedModel(_reply(_use("final_answer", final_sql=COUNT))))
    site = run.transcript.call_site
    assert (site.model, site.sampling, site.prompt_version) == (MODEL, {}, PROMPT_VERSION)


def test_prompt_version_names_the_prompt_text() -> None:
    # The hash of every request key but the conversation, under the committed
    # `[agent]` limits. A change to any of them fails here: give it a new
    # `PROMPT_VERSION`, then update the hash.
    config = load_config(ROOT / "docgap.toml").agent
    site = call_site(ModelSettings(model=MODEL, sampling={}), config)
    assert (site.prompt_version, site.prompt_sha256) == (PROMPT_VERSION, PROMPT_SHA256)


@pytest.mark.parametrize(
    ("outcome", "cause", "rows"),
    [
        ("error", None, None),
        ("result", ErrorCause.SQL, 1),
        ("result", None, None),
        ("timeout", None, 3),
    ],
)
def test_transcript_ties_its_cause_and_rows_to_its_outcome(
    harness: Harness, outcome: str, cause: ErrorCause | None, rows: int | None
) -> None:
    run = harness.run(ScriptedModel(_reply(_use("final_answer", final_sql=COUNT))))
    fields = run.transcript.model_dump() | {
        "outcome": outcome,
        "error_cause": cause,
        "result_rows": rows,
    }
    with pytest.raises(ValidationError):
        Transcript.model_validate_json(json.dumps(fields, default=str))
