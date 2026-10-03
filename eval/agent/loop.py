"""The test agent: a tool-calling loop that answers one question with one SQL query."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self

from anthropic.types import Message
from pydantic import BaseModel, JsonValue, NonNegativeInt, PositiveInt, model_validator

from docgap.config import AgentConfig
from docgap.grade import Outcome, Result
from docgap.llm import ContextExceeded, LlmClient, TransientError
from docgap.log import EventLog
from docgap.models import CONTRACT_CONFIG, CallSite, GradeReason, ModelSettings, Qid, RunId
from eval.agent.tools import FINAL_ANSWER, AgentTools, TranspileError, tool_definitions
from eval.agent.warehouse import SqlError, SqlTimeout

__all__ = [
    "PROMPT_VERSION",
    "AgentRun",
    "ErrorCause",
    "Question",
    "Transcript",
    "call_site",
    "run_agent",
    "system_prompt",
]

# The name of the system prompt and the tool definitions together: the version the
# protocol freezes at the tag and each transcript records. Change either text and this
# changes too; `call_site` hashes both, and a test pins the hash to this version.
PROMPT_VERSION = "agent-1"


def system_prompt(max_tool_calls: int) -> str:
    """The agent's system prompt: the task, the tool budget and how the answer is judged."""
    return (
        "You answer a question about the data in a Snowflake warehouse by writing one SQL"
        " query.\n\n"
        f"Explore with list_tables, describe and run_sql: at most {max_tool_calls} tool"
        " calls in all. Then call final_answer with one Snowflake SQL query whose result"
        " answers the question.\n\n"
        "The answer is judged by its result alone: the number of columns, the number of"
        " rows and the values must match the expected result. Column names don't matter,"
        " and row order matters only when the question asks for one. Return only the"
        " columns the question asks for."
    )


class ErrorCause(StrEnum):
    """Why a run ended as `error`; the pilot's report counts each cause apart."""

    NO_FINAL_ANSWER = "no_final_answer"
    MALFORMED_ANSWER = "malformed_answer"
    TRANSPILE = "transpile"
    SQL = "sql"
    # The conversation outgrew the model's context window (ADR 0029).
    CONTEXT_EXCEEDED = "context_exceeded"
    INFRASTRUCTURE = "infrastructure"


@dataclass(frozen=True, slots=True)
class Question:
    """A question the agent answers: its ID and its text."""

    qid: str
    text: str


class Transcript(BaseModel):
    """One agent run as it happened: the conversation, the final SQL and how it ended."""

    model_config = CONTRACT_CONFIG

    run_id: RunId
    qid: Qid
    repetition: PositiveInt
    # The call site as it ran: the pilot's candidate, or `[call_sites.agent]`.
    call_site: CallSite
    # Model calls that failed after the retries and were sent again.
    failed_calls: NonNegativeInt
    # The Messages API conversation, the tool results and their SQL included.
    messages: list[JsonValue]
    tool_calls: NonNegativeInt
    final_sql: str | None
    outcome: Literal["result", "error", "timeout"]
    error_cause: ErrorCause | None
    error_detail: str | None
    result_rows: NonNegativeInt | None

    @model_validator(mode="after")
    def _cause_iff_error(self) -> Self:
        if (self.error_cause is None) == (self.outcome == "error"):
            raise ValueError("an error run has a cause and any other run has none")
        if (self.result_rows is None) == (self.outcome == "result"):
            raise ValueError("a run with a result counts its rows and any other run has none")
        return self


def call_site(site: ModelSettings, config: AgentConfig) -> CallSite:
    """The agent's call site as it runs: the model's settings and the prompt it sends."""
    prompt = {
        "system": system_prompt(config.max_tool_calls),
        "tools": tool_definitions(
            row_cap=config.row_cap, timeout_seconds=config.statement_timeout_seconds
        ),
    }
    digest = hashlib.sha256(json.dumps(prompt, sort_keys=True).encode()).hexdigest()
    return CallSite(
        model=site.model,
        sampling=site.sampling,
        prompt_version=PROMPT_VERSION,
        prompt_sha256=digest,
    )


@dataclass(frozen=True, slots=True)
class AgentRun:
    """A run's transcript and the outcome the grader reads."""

    transcript: Transcript
    outcome: Outcome


@dataclass(frozen=True, slots=True)
class _End:
    outcome: Outcome
    final_sql: str | None = None
    cause: ErrorCause | None = None
    detail: str | None = None


def _error(cause: ErrorCause, detail: str) -> _End:
    return _End(GradeReason.ERROR, cause=cause, detail=detail)


@dataclass(frozen=True, slots=True)
class _Context:
    question: Question
    repetition: int
    site: ModelSettings
    llm: LlmClient
    tools: AgentTools
    config: AgentConfig
    log: EventLog

    @property
    def item(self) -> str:
        return f"{self.question.qid}:{self.repetition}"


@dataclass(slots=True)
class _Conversation:
    """The run's messages so far, its tool calls and its failed model calls."""

    messages: list[JsonValue]
    tool_calls: int = 0
    failed_calls: int = 0


def _blocks(message: Message) -> list[JsonValue]:
    # Every block goes back as returned, thinking blocks included: Opus 5.5 checks
    # that the conversation before each of its thinking blocks is unchanged.
    return [block.model_dump(mode="json", exclude_none=True) for block in message.content]


def _label(outcome: Outcome) -> Literal["result", "error", "timeout"]:
    return "result" if isinstance(outcome, Result) else outcome.value


def _since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _finish(context: _Context, final_sql: str) -> _End:
    started = time.monotonic()
    try:
        result = context.tools.run_final(final_sql)
    except TranspileError as error:
        end = _End(GradeReason.ERROR, final_sql, ErrorCause.TRANSPILE, str(error))
    except SqlError as error:
        end = _End(GradeReason.ERROR, final_sql, ErrorCause.SQL, str(error))
    except SqlTimeout as error:
        end = _End(GradeReason.TIMEOUT, final_sql, detail=str(error))
    else:
        end = _End(result, final_sql)
    context.log.event(
        "agent",
        context.item,
        "final_sql",
        duration_ms=_since(started),
        outcome=_label(end.outcome),
    )
    return end


def _ask(
    context: _Context, conversation: _Conversation, settings: dict[str, JsonValue]
) -> Message | _End:
    """Send the conversation so far, sending it again after an infrastructure failure.

    The conversation is kept, so no tool query runs twice: on Snowflake a repeated
    query would count twice toward usage. The run's `run_attempts`-th failed call
    ends it as `error`.
    """
    while True:
        try:
            return context.llm.complete(
                context.site,
                {**settings, "messages": list(conversation.messages)},
                prompt_version=PROMPT_VERSION,
                draw=context.repetition,
                stage="agent",
                item=context.item,
            )
        except TransientError as error:
            conversation.failed_calls += 1
            context.log.event(
                "agent", context.item, "model_call_repeated", exception_type=error.exception_type
            )
            if conversation.failed_calls >= context.config.run_attempts:
                return _error(ErrorCause.INFRASTRUCTURE, error.exception_type)
        except ContextExceeded:
            # One text, live or replayed: the API's message isn't cached.
            return _error(ErrorCause.CONTEXT_EXCEEDED, "the input is past the context window")


def _converse(context: _Context, conversation: _Conversation) -> _End:
    """Run the conversation until a final answer or the end of the tool budget."""
    messages = conversation.messages
    limit = context.config.max_tool_calls
    settings: dict[str, JsonValue] = {
        # Automatic prompt caching: changes the cost, never the reply (ADR 0030).
        "cache_control": {"type": "ephemeral"},
        "max_tokens": context.config.max_tokens,
        "system": system_prompt(limit),
        "tools": tool_definitions(
            row_cap=context.config.row_cap,
            timeout_seconds=context.config.statement_timeout_seconds,
        ),
    }
    while True:
        message = _ask(context, conversation, settings)
        if isinstance(message, _End):
            return message
        messages.append({"role": "assistant", "content": _blocks(message)})
        if message.stop_reason == "model_context_window_exceeded":
            return _error(ErrorCause.CONTEXT_EXCEEDED, "the reply reached the context window")
        if message.stop_reason != "tool_use":
            return _error(
                ErrorCause.NO_FINAL_ANSWER, f"stopped with stop_reason {message.stop_reason}"
            )
        uses = [block for block in message.content if block.type == "tool_use"]
        final = next((use for use in uses if use.name == FINAL_ANSWER), None)
        if final is not None:
            sql = final.input.get("final_sql")
            if not isinstance(sql, str):
                return _error(ErrorCause.MALFORMED_ANSWER, "final_sql is no string")
            return _finish(context, sql)
        if conversation.tool_calls >= limit:
            return _error(ErrorCause.NO_FINAL_ANSWER, f"no final answer within {limit} tool calls")
        results: list[JsonValue] = []
        for use in uses:
            if conversation.tool_calls >= limit:
                text, is_error = f"Tool call limit of {limit} reached.", True
            else:
                conversation.tool_calls += 1
                started = time.monotonic()
                result = context.tools.call(use.name, use.input)
                text, is_error = result.text, result.is_error
                context.log.event(
                    "agent",
                    context.item,
                    "tool_call",
                    duration_ms=_since(started),
                    tool=use.name,
                    failed=is_error,
                )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": use.id,
                    "content": text,
                    "is_error": is_error,
                }
            )
        if conversation.tool_calls >= limit:
            results.append(
                {"type": "text", "text": "That was your last tool call. Call final_answer now."}
            )
        messages.append({"role": "user", "content": results})


def run_agent(
    question: Question,
    repetition: int,
    *,
    run_id: str,
    site: ModelSettings,
    llm: LlmClient,
    tools: AgentTools,
    config: AgentConfig,
    log: EventLog,
) -> AgentRun:
    """Answer one question once with the call site's model, and run the final SQL it gives.

    A model call that fails after the retries is an infrastructure failure, not the
    agent's: the call is sent again, and the run's `config.run_attempts`-th failed
    call ends it as `error` (protocol "Runs" item 4).

    Raises:
        ValueError: the call site sets sampling; the agent runs at each model's defaults.
        CacheMiss: offline, and the cache lacks a call.
        BudgetExhausted: the run's call or spend limit is reached.
    """
    if site.sampling:
        raise ValueError(
            f"the agent sends no sampling settings (ADR 0027): {sorted(site.sampling)}"
        )
    context = _Context(question, repetition, site, llm, tools, config, log)
    conversation = _Conversation([{"role": "user", "content": question.text}])
    end = _converse(context, conversation)
    outcome = end.outcome
    transcript = Transcript(
        run_id=run_id,
        qid=question.qid,
        repetition=repetition,
        call_site=call_site(site, config),
        failed_calls=conversation.failed_calls,
        messages=conversation.messages,
        tool_calls=conversation.tool_calls,
        final_sql=end.final_sql,
        outcome=_label(outcome),
        error_cause=end.cause,
        error_detail=end.detail,
        result_rows=len(outcome.rows) if isinstance(outcome, Result) else None,
    )
    log.event(
        "agent",
        context.item,
        "run_done",
        outcome=transcript.outcome,
        error_cause=end.cause,
        failed_calls=conversation.failed_calls,
    )
    return AgentRun(transcript, outcome)
