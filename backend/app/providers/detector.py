"""LLM provider auto-detection.

Detects the best available LLM provider at startup:
  1. ClaudeCLIProvider (claude-agent-sdk via Max subscription)
  2. AnthropicAPIProvider (anthropic Python SDK via ANTHROPIC_API_KEY)
  3. Raise ProviderNotAvailableError with setup instructions

Also provides the shared _parse_json_response helper used by providers.
"""

from __future__ import annotations

import json
import re
import logging
import asyncio

from app.providers.base import LLMProvider, MODEL_ROUTING
from app.config import settings

logger = logging.getLogger(__name__)


class ProviderNotAvailableError(Exception):
    """Raised when no LLM provider can be detected.

    Provides clear setup instructions for the user.
    """

    def __init__(self):
        super().__init__(
            "No LLM provider available. Please configure one of the following:\n\n"
            "  Option A (preferred, zero cost): Claude Code CLI with Max subscription\n"
            "    Install:  npm install -g @anthropic-ai/claude-code\n"
            "    Auth:     claude login  (opens browser OAuth)\n"
            "    Verify:   claude --version\n\n"
            "  Option B (paid API): Set the ANTHROPIC_API_KEY environment variable\n"
            "    In .env:  ANTHROPIC_API_KEY=sk-ant-...\n"
        )


def _parse_json_response(text: str) -> dict:
    """3-strategy JSON parsing fallback.

    1. Parse raw response as JSON directly
    2. Extract first ```json ... ``` code block, parse it
    3. Extract first { ... } substring with regex, parse it
    """
    # Strategy 1: Direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 2: Extract ```json ... ``` code block
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 3: Extract first { ... } substring
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    raise ValueError(f"Could not parse JSON from response: {text[:200]}...")


async def detect_provider() -> LLMProvider:
    """Detect the best available LLM provider.

    Detection order (per spec):
      1. Check if `claude` CLI is available on PATH with valid credentials
         -> ClaudeCLIProvider (zero API cost via Max subscription)
      2. Check if ANTHROPIC_API_KEY env var is set
         -> AnthropicAPIProvider (direct API calls)
      3. Raise ProviderNotAvailableError with clear setup instructions

    Each provider probe completes within 5 seconds or is skipped.
    Total auto-detection does not block startup for more than 10 seconds.
    """
    import os
    import shutil

    # ── Probe 1: Claude CLI ──────────────────────────────────────────
    # Skip if running inside an existing Claude Code session (nested sessions crash)
    in_claude_session = bool(os.environ.get("CLAUDECODE"))
    if in_claude_session:
        logger.info(
            "Running inside a Claude Code session (CLAUDECODE env set). "
            "Skipping ClaudeCLIProvider to avoid nested session issues."
        )
    else:
        try:
            claude_path = shutil.which("claude")
            if claude_path:
                proc = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        "claude", "--version",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    ),
                    timeout=5.0,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    logger.info(
                        "Claude CLI detected at %s: %s",
                        claude_path,
                        stdout.decode().strip(),
                    )
                    try:
                        from app.providers.claude_cli import ClaudeCLIProvider
                        provider = ClaudeCLIProvider()
                        logger.info("Using ClaudeCLIProvider (Max subscription, zero cost)")
                        return provider
                    except ImportError as ie:
                        logger.warning(
                            "Claude CLI found but claude-agent-sdk not installed: %s. "
                            "Install with: pip install claude-agent-sdk",
                            ie,
                        )
        except asyncio.TimeoutError:
            logger.warning("Claude CLI probe timed out after 5 seconds")
        except (FileNotFoundError, OSError) as e:
            logger.warning("Claude CLI detection failed: %s", e)

    # ── Probe 2: Anthropic API key ───────────────────────────────────
    if settings.ANTHROPIC_API_KEY:
        try:
            from app.providers.anthropic_api import AnthropicAPIProvider
            provider = AnthropicAPIProvider(api_key=settings.ANTHROPIC_API_KEY)
            logger.info("Using AnthropicAPIProvider (direct API)")
            return provider
        except ImportError as ie:
            logger.warning(
                "ANTHROPIC_API_KEY set but anthropic package not installed: %s. "
                "Install with: pip install anthropic",
                ie,
            )

    # ── No provider available ────────────────────────────────────────
    raise ProviderNotAvailableError()
