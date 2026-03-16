"""Provider auto-detection: CLI first, then API key, then None."""

from __future__ import annotations

import logging
import shutil

from app.config import settings
from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)


def detect_provider() -> LLMProvider | None:
    """Return the best available LLM provider, or None if none is configured.

    Detection order:
    1. claude CLI on PATH -> ClaudeCLIProvider (Max subscription, zero marginal cost)
    2. ANTHROPIC_API_KEY set -> AnthropicAPIProvider
    3. Neither available -> None
    """
    if shutil.which("claude"):
        from app.providers.claude_cli import ClaudeCLIProvider

        logger.info("Provider detected: claude_cli (CLI found on PATH)")
        return ClaudeCLIProvider()

    if settings.ANTHROPIC_API_KEY:
        from app.providers.anthropic_api import AnthropicAPIProvider

        logger.info("Provider detected: anthropic_api (API key configured)")
        return AnthropicAPIProvider(api_key=settings.ANTHROPIC_API_KEY)

    logger.warning(
        "No LLM provider detected. Install the Claude CLI or set ANTHROPIC_API_KEY."
    )
    return None
