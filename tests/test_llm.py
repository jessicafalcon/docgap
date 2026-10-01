"""The model edge replays from its cache, stays offline when asked, keeps to its budget,
and never caches an infrastructure failure."""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping
from pathlib import Path

import anthropic
import httpx2
import pytest
from pydantic import JsonValue

from docgap.config import LlmConfig, Price
from docgap.llm import (
    Budget,
    BudgetExhausted,
    CacheMiss,
    LlmClient,
    ResponseCache,
    TransientError,
    Transport,
    anthropic_transport,
    cache_key,
)
from docgap.log import EventLog

MODEL = "claude-test"
PARAMS: dict[str, JsonValue] = {
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "How many rows?"}],
}


def _message(text: str = "42", *, input_tokens: int = 1000, output_tokens: int = 100) -> JsonValue:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _config(*, max_calls: int = 10, max_spend_usd: float = 1.0, max_retries: int = 2) -> LlmConfig:
    return LlmConfig(
        timeout_seconds=5,
        max_retries=max_retries,
        max_calls=max_calls,
        max_spend_usd=max_spend_usd,
        prices={MODEL: Price(input=1.0, output=5.0)},
    )


class FakeTransport:
    """Plays scripted outcomes in order: a response, or an exception to raise."""

    def __init__(self, *outcomes: JsonValue | Exception) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, JsonValue]] = []

    def __call__(self, model: str, params: Mapping[str, JsonValue]) -> JsonValue:
        self.calls.append((model, json.loads(json.dumps(params))))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(
    tmp_path: Path, transport: Transport | None, config: LlmConfig
) -> tuple[LlmClient, io.StringIO]:
    sink = io.StringIO()
    client = LlmClient(
        transport, ResponseCache(tmp_path / "cache"), Budget(config), EventLog(sink, "r1")
    )
    return client, sink


def _complete(client: LlmClient, draw: int = 1) -> str:
    message = client.complete(
        MODEL, PARAMS, prompt_version="v1", draw=draw, stage="agent", item="q01"
    )
    block = message.content[0]
    assert block.type == "text"
    return block.text


def _events(sink: io.StringIO) -> list[dict[str, JsonValue]]:
    return [json.loads(line) for line in sink.getvalue().splitlines()]


def test_second_identical_call_replays_from_the_cache(tmp_path: Path) -> None:
    transport = FakeTransport(_message("42"))
    client, sink = _client(tmp_path, transport, _config())
    assert _complete(client) == _complete(client) == "42"
    assert len(transport.calls) == 1
    assert (client.cache_hits, client.cache_misses, client.budget.calls) == (1, 1, 1)
    assert [event["cache"] for event in _events(sink)] == ["miss", "hit"]


def test_another_draw_is_its_own_call(tmp_path: Path) -> None:
    transport = FakeTransport(_message("1"), _message("2"))
    client, _ = _client(tmp_path, transport, _config())
    assert [_complete(client, draw=1), _complete(client, draw=2)] == ["1", "2"]
    assert len(transport.calls) == 2


def test_offline_replays_a_cached_call_and_refuses_a_miss(tmp_path: Path) -> None:
    online, _ = _client(tmp_path, FakeTransport(_message("42")), _config())
    _complete(online)
    offline, _ = _client(tmp_path, None, _config())
    assert _complete(offline) == "42"
    with pytest.raises(CacheMiss):
        _complete(offline, draw=2)


def test_transient_failure_is_logged_counted_and_never_cached(tmp_path: Path) -> None:
    transport = FakeTransport(TransientError("APIConnectionError"), _message("42"))
    client, sink = _client(tmp_path, transport, _config())
    with pytest.raises(TransientError):
        _complete(client)
    assert _complete(client) == "42"
    assert len(transport.calls) == 2
    assert client.budget.calls == 2
    failed = _events(sink)[0]
    assert (failed["event"], failed["exception_type"]) == (
        "model_call_failed",
        "APIConnectionError",
    )


def test_call_limit_refuses_the_next_live_call(tmp_path: Path) -> None:
    transport = FakeTransport(_message())
    client, _ = _client(tmp_path, transport, _config(max_calls=1))
    _complete(client)
    _complete(client)  # a hit: the budget counts live calls only
    with pytest.raises(BudgetExhausted, match="1 calls"):
        _complete(client, draw=2)
    assert len(transport.calls) == 1


def test_spend_limit_refuses_the_next_live_call(tmp_path: Path) -> None:
    # 100,000 input tokens at $1 per million and 20,000 output at $5: $0.20.
    transport = FakeTransport(_message(input_tokens=100_000, output_tokens=20_000))
    client, _ = _client(tmp_path, transport, _config(max_spend_usd=0.15))
    _complete(client)
    assert client.budget.spend_usd == pytest.approx(0.2)  # past the limit by one call
    with pytest.raises(BudgetExhausted, match=r"\$0.20"):
        _complete(client, draw=2)


def test_unpriced_model_is_refused_before_any_call(tmp_path: Path) -> None:
    transport = FakeTransport(_message())
    client, _ = _client(tmp_path, transport, _config())
    with pytest.raises(ValueError, match="no price"):
        client.complete("other", PARAMS, prompt_version="v1", draw=1, stage="s", item="i")
    assert transport.calls == []


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda entry: b"{not json", id="not-json"),
        pytest.param(lambda entry: entry.replace(b'"v1"', b'"v2"'), id="inputs-not-the-key"),
        pytest.param(
            lambda entry: entry.replace(b'"role":"assistant"', b'"role":"user"'),
            id="not-a-message",
        ),
    ],
)
def test_corrupt_entry_is_a_miss(tmp_path: Path, corrupt: Callable[[bytes], bytes]) -> None:
    online, _ = _client(tmp_path, FakeTransport(_message()), _config())
    _complete(online)
    key = cache_key(MODEL, "v1", {"params": PARAMS, "draw": 1})
    path = tmp_path / "cache" / f"{key}.json"
    path.write_bytes(corrupt(path.read_bytes()))
    offline, _ = _client(tmp_path, None, _config())
    with pytest.raises(CacheMiss):
        _complete(offline)
    assert not path.exists()


def _sdk_transport(
    monkeypatch: pytest.MonkeyPatch, statuses: list[int], config: LlmConfig
) -> tuple[Transport, list[int]]:
    """The real transport over the SDK, its HTTP answered in-process by `statuses`, in order."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen: list[int] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        status = statuses[len(seen)]
        seen.append(status)
        if status == 200:
            return httpx2.Response(200, json=_message())
        error = {"type": "error", "error": {"type": "api_error", "message": "injected"}}
        # The shortest wait the SDK honours, so the test doesn't sleep through backoff.
        return httpx2.Response(status, headers={"retry-after-ms": "1"}, json=error)

    http_client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handle))
    return anthropic_transport(config, http_client=http_client), seen


def test_rate_limit_then_success_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    send, seen = _sdk_transport(monkeypatch, [429, 200], _config())
    response = send(MODEL, PARAMS)
    assert isinstance(response, dict)
    assert response["stop_reason"] == "end_turn"
    assert "stop_sequence" not in response  # None fields are left out
    assert seen == [429, 200]


def test_server_errors_past_the_retries_are_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    send, seen = _sdk_transport(monkeypatch, [500, 529, 503], _config(max_retries=2))
    with pytest.raises(TransientError, match="Error"):
        send(MODEL, PARAMS)
    assert seen == [500, 529, 503]


@pytest.mark.parametrize("status", [400, 401, 404])
def test_permanent_error_fails_on_the_first_attempt(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    send, seen = _sdk_transport(monkeypatch, [status], _config())
    with pytest.raises(anthropic.APIStatusError):
        send(MODEL, PARAMS)
    assert seen == [status]


def test_connection_error_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    http_client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(refuse))
    send = anthropic_transport(_config(max_retries=0), http_client=http_client)
    with pytest.raises(TransientError, match="APIConnectionError"):
        send(MODEL, PARAMS)
