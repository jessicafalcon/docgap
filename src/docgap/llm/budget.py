"""Count a run's live model calls and spend, and refuse a call past either limit."""

from __future__ import annotations

from anthropic.types import Usage

from docgap.config import LlmConfig

__all__ = ["Budget", "BudgetExhausted"]

_PER_TOKEN = 1e-6


class BudgetExhausted(Exception):
    """The run reached its call or spend limit: no new call starts."""


class Budget:
    """A run's call and spend limits from `[llm]`, and what it has used so far.

    >>> from docgap.config import Price
    >>> config = LlmConfig(timeout_seconds=60, max_retries=2, max_calls=2, max_spend_usd=1.0,
    ...                    prices={"m": Price(input=1.0, output=5.0)})
    >>> budget = Budget(config)
    >>> budget.reserve("m")
    >>> budget.charge("m", Usage(input_tokens=200_000, output_tokens=20_000))
    0.3
    >>> budget.calls, round(budget.spend_usd, 6)
    (1, 0.3)
    """

    def __init__(self, config: LlmConfig) -> None:
        self._config = config
        self.calls = 0
        self.spend_usd = 0.0

    def reserve(self, model: str) -> None:
        """Count one call about to start.

        A failed call counts too, though it adds no spend: the API reports no usage
        for it, so the call limit is what bounds failed calls.

        Raises:
            BudgetExhausted: the call or spend limit is reached.
            ValueError: the model has no price, so its spend can't be counted.
        """
        if model not in self._config.prices:
            raise ValueError(f"no price in [llm.prices] for {model}")
        if self.calls >= self._config.max_calls:
            raise BudgetExhausted(f"{self.calls} calls reached the limit {self._config.max_calls}")
        if self.spend_usd >= self._config.max_spend_usd:
            raise BudgetExhausted(
                f"${self.spend_usd:.2f} reached the limit ${self._config.max_spend_usd:.2f}"
            )
        self.calls += 1

    def charge(self, model: str, usage: Usage) -> float:
        """Add one response's cost to the spend and return it."""
        price = self._config.prices[model]
        # Bounds that never undercount: a cache read costs at most the input rate,
        # and a cache write at most twice it (the 1-hour cache's rate).
        input_tokens = (
            usage.input_tokens
            + (usage.cache_read_input_tokens or 0)
            + 2 * (usage.cache_creation_input_tokens or 0)
        )
        cost = (input_tokens * price.input + usage.output_tokens * price.output) * _PER_TOKEN
        self.spend_usd += cost
        return cost
