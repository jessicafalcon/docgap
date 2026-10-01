"""Call a model through the response cache, the run's budget and the event log."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

import anthropic
from anthropic.types import Message
from pydantic import JsonValue, ValidationError

from docgap.config import LlmConfig
from docgap.llm.budget import Budget
from docgap.llm.cache import ResponseCache, cache_key
from docgap.log import EventLog

__all__ = ["CacheMiss", "LlmClient", "TransientError", "Transport", "anthropic_transport"]

# Messages API parameters other than `model`, as JSON.
type Params = Mapping[str, JsonValue]
# Sends one request and returns the response as JSON.
type Transport = Callable[[str, Params], JsonValue]

# The statuses the SDK retries, with any 5xx: keep in sync with its `_should_retry`.
# A 5xx the server marks `x-should-retry: false` isn't retried, but is still transient.
_RETRIED_STATUS = frozenset({408, 409, 429})


class TransientError(Exception):
    """An infrastructure failure left after the SDK's retries: not an answer, never cached."""


class CacheMiss(Exception):
    """An offline run asked for a response the cache doesn't hold."""


def anthropic_transport(
    config: LlmConfig, *, http_client: anthropic.DefaultHttpxClient | None = None
) -> Transport:
    """Send requests with the Anthropic SDK, under `[llm]`'s timeout and retries.

    The SDK retries a connection error, a timeout, 408, 409, 429 and 5xx with
    backoff, honouring Retry-After; what is left raises `TransientError`. Any other
    status is permanent and raises the SDK's error unchanged. `http_client` replaces
    the SDK's own, for a proxy or a test double.
    """
    client = anthropic.Anthropic(
        timeout=config.timeout_seconds, max_retries=config.max_retries, http_client=http_client
    )

    def send(model: str, params: Params) -> JsonValue:
        try:
            # The SDK types each parameter; the call sites build them as JSON.
            message: object = client.messages.create(model=model, **params)  # pyright: ignore[reportArgumentType, reportCallIssue, reportUnknownVariableType]
        except anthropic.APIConnectionError as error:
            raise TransientError(type(error).__name__) from error
        except anthropic.APIStatusError as error:
            if error.status_code in _RETRIED_STATUS or error.status_code >= 500:
                raise TransientError(type(error).__name__) from error
            raise
        if not isinstance(message, Message):
            raise TypeError("messages.create returned no Message")
        # Without None fields, so a block replayed in the transcript sends what the
        # API returned, and no null it never sent.
        return message.model_dump(mode="json", exclude_none=True)

    return send


class LlmClient:
    """The only path to a model: cache first, then the budget, then the transport.

    With no transport the client is offline, and a cache miss raises `CacheMiss`
    instead of calling a model.
    """

    def __init__(
        self, transport: Transport | None, cache: ResponseCache, budget: Budget, log: EventLog
    ) -> None:
        self._transport = transport
        self._cache = cache
        self.budget = budget
        self._log = log
        self.cache_hits = 0
        self.cache_misses = 0

    def complete(
        self,
        model: str,
        params: Params,
        *,
        prompt_version: str,
        draw: JsonValue,
        stage: str,
        item: str,
    ) -> Message:
        """Return the model's response to `params`, from the cache when it holds one.

        The key holds the model, the prompt version, the parameters and `draw`, the
        call site's extra state, such as the agent's repetition number.

        Raises:
            CacheMiss: offline, and the cache doesn't hold this call.
            BudgetExhausted: the run's call or spend limit is reached.
            TransientError: the call failed after the SDK's retries; nothing is cached.
        """
        state: JsonValue = {"params": dict(params), "draw": draw}
        key = cache_key(model, prompt_version, state)
        cached = self._cache.get(key)
        if cached is not None:
            try:
                message = Message.model_validate(cached)
            except ValidationError:
                self._cache.discard(key)
            else:
                self.cache_hits += 1
                self._log.event(stage, item, "model_call", cache="hit")
                return message
        self.cache_misses += 1
        if self._transport is None:
            raise CacheMiss(f"{stage} {item}: no cached response for key {key}")
        self.budget.reserve(model)
        started = time.monotonic()
        try:
            response = self._transport(model, params)
        except TransientError as error:
            self._log.event(
                stage,
                item,
                "model_call_failed",
                duration_ms=_since(started),
                exception_type=str(error),
            )
            raise
        message = Message.model_validate(response)
        cost = self.budget.charge(model, message.usage)
        self._cache.put(key, model, prompt_version, state, response)
        self._log.event(
            stage,
            item,
            "model_call",
            duration_ms=_since(started),
            cache="miss",
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            cost_usd=round(cost, 6),
        )
        return message


def _since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
