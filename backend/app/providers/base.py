"""Abstract base class for LLM providers.

Defines the provider interface, error hierarchy, token usage tracking,
and the shared retry utility used by all orchestrators.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider error hierarchy
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base exception for LLM provider errors."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ProviderRateLimitError(ProviderError):
    """Rate limit exceeded — safe to retry after backoff."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message, retryable=True)
        self.retry_after = retry_after


class ProviderAuthError(ProviderError):
    """Authentication or permission failure — not retryable."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class ProviderBadRequestError(ProviderError):
    """Invalid request parameters — not retryable (fix the request)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class ProviderOverloadedError(ProviderError):
    """API overloaded (529) — safe to retry after backoff."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


# ---------------------------------------------------------------------------
# Token usage tracking
# ---------------------------------------------------------------------------


@dataclass
class TokenUsage:
    """Token usage from a single LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.input_tokens + self.output_tokens


# ---------------------------------------------------------------------------
# Provider base class
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Base class for all LLM providers.

    Subclasses must implement ``complete_parsed`` and set a ``name`` attribute.
    Token usage from the last call is available via ``last_usage``.
    """

    name: str
    last_usage: TokenUsage | None = None
    last_model: str | None = None

    @abstractmethod
    async def complete_parsed(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_format: type[T],
        max_tokens: int = 16384,
        effort: str | None = None,
        cache_ttl: str | None = None,
    ) -> T:
        """Make an LLM call and return a parsed Pydantic model.

        Raises:
            ProviderRateLimitError: Rate limited — retryable.
            ProviderAuthError: Auth failure — not retryable.
            ProviderBadRequestError: Bad request — not retryable.
            ProviderOverloadedError: API overloaded — retryable.
            ProviderError: Other errors — check ``retryable`` flag.
        """
        ...

    async def complete_parsed_streaming(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_format: type[T],
        max_tokens: int = 16384,
        effort: str | None = None,
        cache_ttl: str | None = None,
    ) -> T:
        """Streaming variant of ``complete_parsed``.

        Prevents HTTP timeouts on long outputs (e.g. Opus 128K).
        Default implementation falls back to non-streaming ``complete_parsed``.
        Providers that support native streaming (e.g. Anthropic API) should
        override this with ``messages.stream()`` + ``get_final_message()``.
        """
        return await self.complete_parsed(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            output_format=output_format,
            max_tokens=max_tokens,
            effort=effort,
            cache_ttl=cache_ttl,
        )

    @staticmethod
    def thinking_config(model: str) -> dict[str, str]:
        """Return thinking configuration for the given model.

        Opus/Sonnet 4.6+: adaptive thinking (recommended, no budget_tokens).
        Haiku: disabled (not supported).
        """
        if "haiku" in model.lower():
            return {"type": "disabled"}
        return {"type": "adaptive"}


# ---------------------------------------------------------------------------
# Shared retry utility
# ---------------------------------------------------------------------------

_DEFAULT_MAX_RETRIES = 1
_DEFAULT_RETRY_DELAY = 2.0
_MAX_RETRY_AFTER_CAP = 30  # seconds


async def call_provider_with_retry(
    provider: LLMProvider,
    *,
    model: str,
    system_prompt: str,
    user_message: str,
    output_format: type[T],
    max_tokens: int = 16384,
    effort: str | None = None,
    cache_ttl: str | None = None,
    streaming: bool = False,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
) -> T:
    """Call provider.complete_parsed with smart retry logic.

    Only retries on retryable errors (rate limits, server errors, overload).
    Non-retryable errors (bad request, auth) fail immediately.

    When ``streaming=True``, dispatches to ``complete_parsed_streaming()``
    which prevents HTTP timeouts on long outputs (e.g. Opus 128K).

    Used by both PipelineOrchestrator and RefinementService to avoid
    duplicating retry logic.
    """
    call_fn = provider.complete_parsed_streaming if streaming else provider.complete_parsed

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await call_fn(
                model=model,
                system_prompt=system_prompt,
                user_message=user_message,
                output_format=output_format,
                max_tokens=max_tokens,
                effort=effort,
                cache_ttl=cache_ttl,
            )
        except ProviderError as exc:
            if not exc.retryable:
                raise  # Bad request, auth errors — fail immediately
            last_exc = exc
            if attempt < max_retries:
                delay = retry_delay
                if isinstance(exc, ProviderRateLimitError) and exc.retry_after:
                    delay = min(float(exc.retry_after), _MAX_RETRY_AFTER_CAP)
                _logger.warning(
                    "Provider call failed (attempt %d/%d), retrying in %.0fs: %s",
                    attempt + 1, max_retries + 1, delay, exc,
                )
                await asyncio.sleep(delay)
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                _logger.warning(
                    "Provider call failed (attempt %d/%d), retrying in %.0fs: %s",
                    attempt + 1, max_retries + 1, retry_delay, exc,
                )
                await asyncio.sleep(retry_delay)
    raise last_exc  # type: ignore[misc]
