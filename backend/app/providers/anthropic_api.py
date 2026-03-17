"""Anthropic API provider — direct API with prompt caching.

Uses ``messages.parse()`` for structured output with automatic Pydantic
validation. System prompts are cached via ``cache_control: ephemeral``.
Adaptive thinking for Opus/Sonnet, disabled for Haiku.
"""

from __future__ import annotations

import logging
from typing import TypeVar

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
        self._client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()

    async def complete_parsed(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_format: type[T],
        max_tokens: int = 16384,
        effort: str | None = None,
    ) -> T:
        """Make an LLM call and return a parsed Pydantic model.

        - System prompt cached via ``cache_control: ephemeral``
        - Adaptive thinking for Opus/Sonnet, disabled for Haiku
        - Effort parameter via ``output_config`` (non-Haiku only)
        - Typed error handling mapped to ProviderError hierarchy
        """
        system = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        thinking = self.thinking_config(model)

        is_haiku = "haiku" in model.lower()
        output_config: dict | None = None
        if effort is not None and not is_haiku:
            output_config = {"effort": effort}

        kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
            thinking=thinking,
            output_format=output_format,
        )
        if output_config is not None:
            kwargs["output_config"] = output_config

        try:
            response = await self._client.messages.parse(**kwargs)
        except anthropic.RateLimitError as exc:
            retry_after = _extract_retry_after(exc)
            raise ProviderRateLimitError(
                f"Rate limited: {exc.message}",
                retry_after=retry_after,
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise ProviderAuthError(
                f"Authentication failed: {exc.message}"
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderAuthError(
                f"Permission denied: {exc.message}"
            ) from exc
        except anthropic.BadRequestError as exc:
            raise ProviderBadRequestError(
                f"Invalid request: {exc.message}"
            ) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 529:
                raise ProviderOverloadedError(
                    f"API overloaded: {exc.message}"
                ) from exc
            raise ProviderError(
                f"API error ({exc.status_code}): {exc.message}",
                retryable=exc.status_code >= 500,
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(
                f"Connection error: {exc}",
                retryable=True,
            ) from exc

        # Track token usage including prompt cache stats
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0

        self.last_usage = TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

        # Build log message with cache and stop reason details
        parts = [
            f"model={model}",
            f"in={usage.input_tokens}",
            f"out={usage.output_tokens}",
        ]
        if cache_read:
            parts.append(f"cache_read={cache_read}")
        if cache_creation:
            parts.append(f"cache_write={cache_creation}")
        if response.stop_reason and response.stop_reason != "end_turn":
            parts.append(f"stop={response.stop_reason}")

        logger.info("anthropic_api complete_parsed %s", " ".join(parts))

        return response.parsed_output


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
