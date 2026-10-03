"""The model edge: the only package that calls a model, always through the response cache."""

from __future__ import annotations

from docgap.llm.budget import Budget, BudgetExhausted
from docgap.llm.cache import ResponseCache, cache_key
from docgap.llm.client import (
    CacheMiss,
    ContextExceeded,
    LlmClient,
    TransientError,
    Transport,
    anthropic_transport,
)

__all__ = [
    "Budget",
    "BudgetExhausted",
    "CacheMiss",
    "ContextExceeded",
    "LlmClient",
    "ResponseCache",
    "TransientError",
    "Transport",
    "anthropic_transport",
    "cache_key",
]
