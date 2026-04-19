"""Anthropic API provider — direct API with prompt caching.

Uses ``messages.parse()`` for structured output with automatic Pydantic
validation. System prompts are cached via ``cache_control: ephemeral``.
Adaptive thinking for Opus/Sonnet, disabled for Haiku.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

import anthropic
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.providers.base import (
    LLMProvider,
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderError,
    ProviderOverloadedError,
    ProviderRateLimitError,
    TokenUsage,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class AnthropicAPIProvider(LLMProvider):
    """LLM provider that calls the Anthropic API directly."""

    name = "anthropic_api"

    def __init__(self, api_key: str | None = None) -> None:
        # Disable SDK built-in retries (default max_retries=2) so the
        # app-level call_provider_with_retry() is the sole retry controller.
        # Without this, retries compound: up to 6 total attempts with
        # conflicting backoff strategies (SDK exponential vs app fixed).
        if api_key:
            self._client = AsyncAnthropic(api_key=api_key, max_retries=0)
        else:
            self._client = AsyncAnthropic(max_retries=0)

    # ------------------------------------------------------------------
    # Shared helpers — eliminate duplication between parse / stream paths
    # ------------------------------------------------------------------

    @staticmethod
    def _build_kwargs(
        model: str,
        system_prompt: str,
        user_message: str,
        output_format: type[T],
        max_tokens: int,
        effort: str | None,
        cache_ttl: str | None = None,
        task_budget: int | None = None,
        compaction: bool = False,
    ) -> dict[str, Any]:
        """Assemble kwargs common to both parse() and stream() calls.

        Opus 4.7 features (beta, opt-in per call):
          - ``task_budget``: integer token total (min 20_000, clamped up).
            Appears in ``output_config.task_budget`` and requires the
            ``task-budgets-2026-03-13`` beta header. Opus 4.7 only — skipped
            with a warning on other models.
          - ``compaction``: server-side context summarization. Appears in
            ``context_management.edits`` and requires the
            ``compact-2026-01-12`` beta header. Opus 4.7 / 4.6 and
            Sonnet 4.6 only.

        Effort gating:
          - Haiku: effort dropped entirely (not supported).
          - ``xhigh``: Opus 4.7 only — downgraded to ``high`` with a warning
            on other models to avoid 400s.
        """
        cache_control: dict[str, str] = {"type": "ephemeral"}
        if cache_ttl is not None:
            cache_control["ttl"] = cache_ttl
        system = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": cache_control,
            }
        ]

        thinking = LLMProvider.thinking_config(model)

        model_lower = model.lower()
        is_haiku = "haiku" in model_lower
        is_opus_47 = "opus-4-7" in model_lower
        is_compaction_capable = is_opus_47 or "opus-4-6" in model_lower or "sonnet-4-6" in model_lower

        # Effort gating — xhigh is Opus 4.7 only; downgrade silently-safe.
        effective_effort = effort
        if effective_effort == "xhigh" and not is_opus_47:
            logger.warning(
                "effort='xhigh' requires Opus 4.7 (model=%s) — downgrading to 'high'", model,
            )
            effective_effort = "high"

        output_config: dict[str, Any] = {}
        if effective_effort is not None and not is_haiku:
            output_config["effort"] = effective_effort

        # Task budget — Opus 4.7 only; clamp to SDK minimum of 20_000.
        beta_headers: list[str] = []
        if task_budget is not None:
            if not is_opus_47:
                logger.warning(
                    "task_budget requires Opus 4.7 (model=%s) — ignoring", model,
                )
            else:
                total = max(int(task_budget), 20_000)
                output_config["task_budget"] = {"type": "tokens", "total": total}
                beta_headers.append("task-budgets-2026-03-13")

        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
            thinking=thinking,
            output_format=output_format,
        )
        if output_config:
            kwargs["output_config"] = output_config

        # Compaction — server-managed context edits; requires beta header.
        if compaction:
            if not is_compaction_capable:
                logger.warning(
                    "compaction requires Opus 4.7/4.6 or Sonnet 4.6 (model=%s) — ignoring",
                    model,
                )
            else:
                kwargs["context_management"] = {
                    "edits": [{"type": "compact_20260112"}]
                }
                beta_headers.append("compact-2026-01-12")

        if beta_headers:
            kwargs["extra_headers"] = {"anthropic-beta": ",".join(beta_headers)}

        return kwargs

    def _track_usage(self, response: Any, *, streaming: bool = False) -> None:
        """Extract token usage from response and log it."""
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0

        self.last_usage = TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )
        self.last_model = response.model

        parts = [
            f"model={response.model}",
            f"in={usage.input_tokens}",
            f"out={usage.output_tokens}",
        ]
        if streaming:
            parts.append("streaming=true")
        if cache_read:
            parts.append(f"cache_read={cache_read}")
        if cache_creation:
            parts.append(f"cache_write={cache_creation}")
        if response.stop_reason and response.stop_reason != "end_turn":
            parts.append(f"stop={response.stop_reason}")

        method = "complete_parsed_streaming" if streaming else "complete_parsed"
        logger.info("anthropic_api %s %s", method, " ".join(parts))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

        - System prompt cached via ``cache_control: ephemeral``
        - Adaptive thinking for Opus/Sonnet (Opus 4.7 uses ``display=summarized``)
        - Effort parameter via ``output_config`` (non-Haiku only).
          ``xhigh`` is Opus 4.7 only and is downgraded to ``high`` elsewhere.
        - ``task_budget``: Opus 4.7 Task Budgets beta (``output_config.task_budget``
          + beta header ``task-budgets-2026-03-13``). Clamped to minimum 20_000.
        - ``compaction``: Server-side Compaction beta (``context_management.edits``
          + beta header ``compact-2026-01-12``) — Opus 4.7/4.6 + Sonnet 4.6 only.
        - Typed error handling mapped to ProviderError hierarchy.
        """
        kwargs = self._build_kwargs(
            model, system_prompt, user_message, output_format, max_tokens, effort, cache_ttl,
            task_budget=task_budget, compaction=compaction,
        )

        try:
            response = await self._client.messages.parse(**kwargs)
        except anthropic.APIError as exc:
            _raise_provider_error(exc)

        self._track_usage(response)
        return response.parsed_output  # type: ignore[return-value]

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
        """Streaming variant — prevents HTTP timeouts on long outputs.

        Uses ``messages.stream()`` with ``get_final_message()`` to collect
        the complete response. Recommended for high ``max_tokens`` calls
        (e.g. Opus optimize phase with up to 128K output tokens).

        Same error handling, caching, thinking config, and beta-feature
        wiring as ``complete_parsed`` — Task Budgets and Compaction flow
        through identically.
        """
        kwargs = self._build_kwargs(
            model, system_prompt, user_message, output_format, max_tokens, effort, cache_ttl,
            task_budget=task_budget, compaction=compaction,
        )

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                response = await stream.get_final_message()
        except anthropic.APIError as exc:
            _raise_provider_error(exc)

        self._track_usage(response, streaming=True)
        return response.parsed_output  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _raise_provider_error(exc: anthropic.APIError) -> None:
    """Map any ``anthropic.APIError`` to the appropriate ``ProviderError``.

    Always raises — return type is ``None`` only for the type checker.
    """
    if isinstance(exc, anthropic.RateLimitError):
        retry_after = _extract_retry_after(exc)
        raise ProviderRateLimitError(
            f"Rate limited: {exc.message}",
            retry_after=retry_after,
        ) from exc

    if isinstance(exc, anthropic.AuthenticationError):
        raise ProviderAuthError(
            f"Authentication failed: {exc.message}"
        ) from exc

    if isinstance(exc, anthropic.PermissionDeniedError):
        raise ProviderAuthError(
            f"Permission denied: {exc.message}"
        ) from exc

    if isinstance(exc, anthropic.BadRequestError):
        raise ProviderBadRequestError(
            f"Invalid request: {exc.message}"
        ) from exc

    if isinstance(exc, anthropic.APIConnectionError):
        raise ProviderError(
            f"Connection error: {exc}",
            retryable=True,
        ) from exc

    # APIStatusError (includes 529 overloaded)
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code == 529:
            raise ProviderOverloadedError(
                f"API overloaded: {exc.message}"
            ) from exc
        raise ProviderError(
            f"API error ({exc.status_code}): {exc.message}",
            retryable=exc.status_code >= 500,
        ) from exc

    # Catch-all for any future APIError subclasses
    raise ProviderError(
        f"Unexpected API error: {exc}",
        retryable=True,
    ) from exc


def _extract_retry_after(exc: anthropic.RateLimitError) -> int | None:
    """Extract retry-after seconds from rate limit response headers."""
    try:
        if exc.response and exc.response.headers:
            raw = exc.response.headers.get("retry-after")
            if raw:
                return int(raw)
    except (ValueError, AttributeError):
        pass
    return None
