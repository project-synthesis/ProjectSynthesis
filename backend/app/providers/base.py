"""Abstract base class for LLM providers.

Defines the provider interface, error hierarchy, token usage tracking,
and the shared retry utility used by all orchestrators.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
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
    """Rate limit exceeded — safe to retry after backoff.

    Attributes:
        retry_after: Seconds to wait, parsed from ``Retry-After`` header
            (Anthropic API) when available.
        reset_at: Absolute UTC time when the rate limit resets, parsed
            from provider-specific messages when present (e.g. some
            Claude CLI plans emit ``"resets 3:40pm (America/Toronto)"``).
            Distinct from ``retry_after`` because some providers emit
            wall-clock reset times rather than relative seconds. ``None``
            when the provider doesn't surface a reset wall-clock.
        provider_name: Name of the provider that raised this — used in
            user-facing rate-limit messages so multi-provider deployments
            can identify which limit was hit. Plan-agnostic (the same
            ``"claude_cli"`` value covers Pro / Team / Enterprise / MAX /
            Bedrock / Vertex — the limit message format may differ but
            the user-facing label does not).

    Use ``estimated_wait_seconds`` to get a unified wait estimate
    regardless of which field the provider populated.
    """

    def __init__(
        self,
        message: str,
        retry_after: int | None = None,
        *,
        reset_at: "datetime | None" = None,  # forward-ref; datetime imported lazily
        provider_name: str = "",
    ) -> None:
        super().__init__(message, retryable=True)
        self.retry_after = retry_after
        self.reset_at = reset_at
        self.provider_name = provider_name

    @property
    def estimated_wait_seconds(self) -> int | None:
        """Best estimate of wait time before retry succeeds.

        Returns:
            ``reset_at`` delta from now if present, else ``retry_after``,
            else ``None`` (caller decides default backoff).
        """
        if self.reset_at is not None:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            delta = (self.reset_at - now).total_seconds()
            return max(0, int(delta))
        return self.retry_after


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
        task_budget: int | None = None,
        compaction: bool = False,
    ) -> T:
        """Make an LLM call and return a parsed Pydantic model.

        Args:
            task_budget: Opt-in Task Budget (beta, Opus 4.7). Integer token
                total for the agentic loop. Model sees a running countdown and
                self-moderates. Minimum 20,000 — values below are clamped up
                by the provider. Distinct from ``max_tokens`` (hard ceiling).
            compaction: Opt-in server-side Compaction (beta, Opus 4.7/4.6 +
                Sonnet 4.6). Automatic context summarization when approaching
                the trigger threshold. Only useful for long-running
                conversations; no-op for single-shot calls.

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
        task_budget: int | None = None,
        compaction: bool = False,
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
            task_budget=task_budget,
            compaction=compaction,
        )

    @staticmethod
    def thinking_config(model: str) -> dict[str, str]:
        """Return thinking configuration for the given model.

        - Haiku: disabled (not supported).
        - Opus 4.7: adaptive with ``display="summarized"``. Without this,
          4.7 defaults to ``display="omitted"``, which silently hides
          thinking content — streams look like long pauses before output.
        - Opus 4.6 / Sonnet 4.6: adaptive (default display).
        """
        # Local import to keep the ABC module free of eager imports.
        from app.providers.capabilities import supports_thinking

        if not supports_thinking(model):
            return {"type": "disabled"}
        if "opus-4-7" in model.lower():
            return {"type": "adaptive", "display": "summarized"}
        return {"type": "adaptive"}

    @staticmethod
    def supports_xhigh_effort(model: str) -> bool:
        """Whether the given model accepts ``effort="xhigh"``.

        Delegates to ``capabilities.effort_support`` — xhigh appears in the
        support list only for Opus 4.7.
        """
        from app.providers.capabilities import effort_support

        return "xhigh" in effort_support(model)


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
    task_budget: int | None = None,
    compaction: bool = False,
    streaming: bool = False,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
) -> T:
    """Call provider.complete_parsed with smart retry logic.

    Only retries on retryable errors (rate limits, server errors, overload).
    Non-retryable errors (bad request, auth) fail immediately.

    When ``streaming=True``, dispatches to ``complete_parsed_streaming()``
    which prevents HTTP timeouts on long outputs (e.g. Opus 128K).

    ``task_budget`` and ``compaction`` are forwarded verbatim to the provider
    (see ``LLMProvider.complete_parsed`` for semantics — they are no-ops on
    providers that don't support them, e.g. the CLI).

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
                task_budget=task_budget,
                compaction=compaction,
            )
        except ProviderError as exc:
            if not exc.retryable:
                raise  # Bad request, auth errors — fail immediately
            last_exc = exc
            # Rate-limit handling: prefer the provider's own wait estimate
            # (``reset_at`` -> seconds-until-reset, or ``retry_after``) over
            # a constant backoff. Three cases:
            #
            # 1. Wait is known and <= cap: use it as the retry delay.
            # 2. Wait is known and > cap: propagate immediately — caller
            #    takes user-facing action (pause batch, surface reset_at).
            # 3. Wait is unknown (None): propagate immediately — the limit
            #    is almost certainly long-lived (hours, daily cap) and
            #    retrying with a 2s backoff is guaranteed to fail. Before
            #    this fix, unknown-duration rate limits burned an extra
            #    CLI subprocess + 429 round-trip per retry.
            if isinstance(exc, ProviderRateLimitError):
                est = exc.estimated_wait_seconds
                if est is None or est > _MAX_RETRY_AFTER_CAP:
                    _logger.warning(
                        "Rate limit %s — propagating immediately "
                        "(provider=%s, reset_at=%s)",
                        f"wait {est}s exceeds cap {_MAX_RETRY_AFTER_CAP}s"
                        if est is not None else "unknown duration",
                        exc.provider_name, exc.reset_at,
                    )
                    raise
            if attempt < max_retries:
                delay = retry_delay
                if isinstance(exc, ProviderRateLimitError):
                    est = exc.estimated_wait_seconds
                    if est is not None:
                        # Add 2s jitter so retry doesn't fire the moment
                        # the limit lifts (the provider's clock skew vs ours
                        # can leave the limit still in effect for a beat).
                        delay = min(float(est) + 2.0, _MAX_RETRY_AFTER_CAP)
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
