"""LLM provider auto-detection.

Detects the best available LLM provider at startup:
  1. ClaudeCLIProvider (claude-agent-sdk via Max subscription)
  2. AnthropicAPIProvider (anthropic Python SDK via ANTHROPIC_API_KEY)
  3. Raise ProviderNotAvailableError with setup instructions
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from app.config import settings
from app.providers.base import LLMProvider

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


async def detect_provider() -> LLMProvider:
    """Detect the best available LLM provider.

    Detection order (per spec):
      1. Check if TESTING=True -> MockProvider (no real LLM calls)
      2. Check if `claude` CLI is available on PATH with valid credentials
         -> ClaudeCLIProvider (zero API cost via Max subscription)
      3. Check if ANTHROPIC_API_KEY env var is set
         -> AnthropicAPIProvider (direct API calls)
      4. Raise ProviderNotAvailableError with clear setup instructions

    Each provider probe completes within 5 seconds or is skipped.
    Total auto-detection does not block startup for more than 10 seconds.
    """
    # Short-circuit to MockProvider when running in TESTING mode.
    # This allows integration tests and E2E tests to run without a real LLM provider.
    if settings.TESTING:
        logger.warning(
            "TESTING=true: using MockProvider — no real LLM calls will be made"
        )
        from app.providers.mock import MockProvider
        return MockProvider()

    try:
        return await asyncio.wait_for(_detect_provider_inner(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning(
            "Provider auto-detection exceeded 10-second total timeout; "
            "no provider available."
        )
        raise ProviderNotAvailableError()


async def _probe_claude_version(path: str) -> tuple[bytes, int]:
    """Run ``claude --version`` and return (stdout, returncode).

    Handles proper subprocess cleanup if cancelled (e.g. by the 5-second
    per-probe timeout): the process is killed and reaped before re-raising
    CancelledError so it does not become a zombie.
    """
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            path, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except asyncio.CancelledError:
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
        raise
    return stdout, proc.returncode


async def _detect_provider_inner() -> LLMProvider:
    """Run provider probes sequentially. Called inside the 10-second outer timeout."""
    # ── Probe 1: Claude CLI ──────────────────────────────────────────
    # Skip if running inside an existing Claude Code session (nested sessions crash)
    in_claude_session = bool(os.environ.get("CLAUDECODE"))
    if in_claude_session:
        logger.info(
            "Running inside a Claude Code session (CLAUDECODE=%s). "
            "Skipping ClaudeCLIProvider probe — nested sessions are not supported. "
            "Will try AnthropicAPIProvider next.",
            os.environ.get("CLAUDECODE"),
        )
    else:
        try:
            claude_path = shutil.which("claude")
            if claude_path:
                # Bound both subprocess creation AND communicate() within 5 seconds
                stdout, returncode = await asyncio.wait_for(
                    _probe_claude_version(claude_path),
                    timeout=5.0,
                )
                if returncode == 0:
                    # Spec step 2: verify ~/.claude/ credential directory exists.
                    # `claude --version` exits 0 even without `claude login` — the
                    # directory is only created after successful OAuth, so its existence
                    # is a reliable signal that a Max subscription session is present.
                    claude_cred_dir = Path.home() / ".claude"
                    if not claude_cred_dir.is_dir():
                        logger.warning(
                            "Claude CLI found at %s but ~/.claude/ credential directory "
                            "is missing — run `claude login` to authenticate. "
                            "Falling through to next provider.",
                            claude_path,
                        )
                    else:
                        logger.info(
                            "Claude CLI detected at %s: %s",
                            claude_path,
                            stdout.decode().strip(),
                        )
                        try:
                            from app.providers.claude_cli import ClaudeCLIProvider
                            provider: LLMProvider = ClaudeCLIProvider()
                            logger.info("Using ClaudeCLIProvider (Max subscription, zero cost)")
                            return provider
                        except ImportError as ie:
                            logger.warning(
                                "Claude CLI found but claude-agent-sdk not installed: %s. "
                                "Install with: pip install claude-agent-sdk. "
                                "Falling through to AnthropicAPIProvider.",
                                ie,
                            )
                        except Exception as init_err:
                            logger.warning(
                                "ClaudeCLIProvider instantiation failed: %s: %s. "
                                "Falling through to AnthropicAPIProvider.",
                                type(init_err).__name__, init_err,
                            )
        except asyncio.TimeoutError:
            logger.warning("Claude CLI probe timed out after 5 seconds")
        except (FileNotFoundError, OSError) as e:
            logger.warning("Claude CLI detection failed: %s", e)

    # ── Probe 2: Anthropic API key ───────────────────────────────────
    if settings.ANTHROPIC_API_KEY:
        try:
            from app.providers.anthropic_api import AnthropicAPIProvider
            betas: list[str] = []
            if settings.CONTEXT_1M_ENABLED:
                betas.append(settings.CONTEXT_1M_BETA_STRING)
            if settings.COMPACTION_ENABLED:
                betas.append(settings.COMPACTION_BETA_STRING)
            provider = AnthropicAPIProvider(
                api_key=settings.ANTHROPIC_API_KEY,
                betas=betas if betas else None,
            )
            logger.info("Using AnthropicAPIProvider (direct API)")
            if betas:
                logger.info("API betas enabled: %s", betas)
            return provider
        except ImportError as ie:
            logger.warning(
                "ANTHROPIC_API_KEY set but anthropic package not installed: %s. "
                "Install with: pip install anthropic",
                ie,
            )

    # ── No provider available ────────────────────────────────────────
    raise ProviderNotAvailableError()
