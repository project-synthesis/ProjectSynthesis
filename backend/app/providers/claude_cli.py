"""Claude CLI subprocess provider.

Calls the ``claude`` CLI with native ``--json-schema`` for structured output
validation and ``--effort`` for thinking control. Maps exit codes and stderr
patterns to the ProviderError hierarchy.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TypeVar

from pydantic import BaseModel

from app.providers.base import (
    LLMProvider,
    ProviderBadRequestError,
    ProviderError,
    TokenUsage,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Subprocess timeout — 5 minutes per call
_CLI_TIMEOUT_SECONDS = 300


class ClaudeCLIProvider(LLMProvider):
    """LLM provider that calls the claude CLI subprocess.

    Uses native CLI features for structured output and effort control:
    - ``--json-schema``: CLI validates output against the schema and returns
      the parsed result in the ``structured_output`` field of the JSON envelope.
    - ``--effort``: Controls thinking depth (low/medium/high/max).
    """

    name = "claude_cli"

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
        """Run claude CLI and parse JSON output as a Pydantic model.

        Uses ``--json-schema`` for native structured output validation.
        Falls back to parsing the ``result`` field if ``structured_output``
        is not present (older CLI versions).

        Raises:
            ProviderError: CLI not found, timeout, or non-zero exit.
            ProviderBadRequestError: Response is not valid JSON or fails validation.
        """
        schema = output_format.model_json_schema()

        cmd = [
            "claude",
            "-p",
            user_message,
            "--model",
            model,
            "--system-prompt",
            system_prompt,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
        ]

        # Pass effort level when specified (low/medium/high/max).
        # Haiku doesn't support the effort parameter — skip it to avoid errors.
        if effort and "haiku" not in model.lower():
            cmd.extend(["--effort", effort])

        logger.debug("claude_cli executing model=%s effort=%s", model, effort)

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_CLI_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass  # Best-effort zombie reaping
            raise ProviderError(
                f"Claude CLI timed out after {_CLI_TIMEOUT_SECONDS}s",
                retryable=True,
            )
        except FileNotFoundError:
            raise ProviderError(
                "Claude CLI not found on PATH. "
                "Install with: npm install -g @anthropic-ai/claude-code",
                retryable=False,
            )

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace")
            lower = stderr_text.lower()
            retryable = (
                "rate limit" in lower
                or "overloaded" in lower
                or "timeout" in lower
                or "429" in stderr_text
                or "529" in stderr_text
            )
            raise ProviderError(
                f"Claude CLI exited with code {proc.returncode}: {stderr_text}",
                retryable=retryable,
            )

        # Parse the CLI JSON envelope
        try:
            raw = json.loads(stdout.decode())
        except json.JSONDecodeError as exc:
            raise ProviderBadRequestError(
                f"Claude CLI returned invalid JSON: {exc}"
            ) from exc

        # Extract content: prefer structured_output (native --json-schema),
        # fall back to parsing the result field (older CLI or no schema match)
        content = self._extract_content(raw)

        # Validate against the Pydantic model
        try:
            result = output_format.model_validate(content)
        except Exception as exc:
            raise ProviderBadRequestError(
                f"Response validation failed: {exc}"
            ) from exc

        # Track token usage from CLI metadata
        usage_data = raw.get("usage", {}) if isinstance(raw, dict) else {}
        self.last_usage = TokenUsage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            cache_read_tokens=usage_data.get("cache_read_input_tokens", 0),
            cache_creation_tokens=usage_data.get("cache_creation_input_tokens", 0),
        )
        self.last_model = raw.get("model", model) if isinstance(raw, dict) else model

        # Log with duration and cost
        duration = raw.get("duration_ms", "?") if isinstance(raw, dict) else "?"
        cost = raw.get("total_cost_usd") if isinstance(raw, dict) else None
        parts = [f"model={model}", f"duration_ms={duration}"]
        if effort:
            parts.append(f"effort={effort}")
        if cost is not None:
            parts.append(f"cost=${cost:.4f}")
        if self.last_usage.cache_read_tokens:
            parts.append(f"cache_read={self.last_usage.cache_read_tokens}")

        logger.info("claude_cli complete_parsed %s", " ".join(parts))
        return result

    @staticmethod
    def _extract_content(raw: dict | list) -> dict | list:
        """Extract the structured content from the CLI JSON envelope.

        Priority:
        1. ``structured_output`` — native ``--json-schema`` validated output
        2. ``result`` string — parse as JSON (legacy / fallback)
        3. Raw envelope itself — if neither field exists
        """
        if not isinstance(raw, dict):
            return raw

        # 1. Native structured output from --json-schema
        if "structured_output" in raw and raw["structured_output"] is not None:
            return raw["structured_output"]

        # 2. Legacy: result field contains a JSON string
        if "result" in raw and isinstance(raw["result"], str):
            content_str = raw["result"].strip()
            if not content_str:
                raise ProviderBadRequestError("Claude CLI returned empty result")
            # Strip markdown code fencing if present
            if content_str.startswith("```"):
                first_newline = content_str.find("\n")
                if first_newline != -1:
                    content_str = content_str[first_newline + 1:]
                if content_str.rstrip().endswith("```"):
                    content_str = content_str.rstrip()[:-3].rstrip()
            try:
                return json.loads(content_str)
            except json.JSONDecodeError:
                logger.error("Failed to parse CLI result as JSON: %s", content_str[:200])
                raise ProviderBadRequestError(
                    f"Claude CLI returned invalid JSON in result field. "
                    f"First 200 chars: {content_str[:200]}"
                )

        # 3. Envelope itself (unexpected shape)
        return raw
