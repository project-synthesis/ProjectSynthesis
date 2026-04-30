"""Claude CLI subprocess provider.

Calls the ``claude`` CLI with native ``--json-schema`` for structured output
validation and ``--effort`` for thinking control. Maps exit codes and stderr
patterns to the ProviderError hierarchy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from pydantic import BaseModel

from app.providers.base import (
    LLMProvider,
    ProviderBadRequestError,
    ProviderError,
    ProviderRateLimitError,
    TokenUsage,
)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # py < 3.9 -- not expected here, kept defensive
    ZoneInfo = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Subprocess timeout — per-LLM-call ceiling.
#
# Calibrated against live audit-class duration distribution (cycle-19→22 v2 +
# cycle-23 + Topic Probe Tier 1 integration validation, 2026-04-29):
#   median full-pipeline ~354s, p95 ~480s, max 491s.
# These are *full pipeline* (analyze + optimize + score) durations. Per-call
# is roughly 1/3 of that for non-Opus models, but the Opus 4.7 OPTIMIZE phase
# with `xhigh` effort + 80K codebase context can land at 400–500s on its own.
#
# Set to 600s (10 min) — covers p99 of the optimize phase with headroom.
# The 300s prior value caused silent retries to mask real long-running calls
# under `call_provider_with_retry`, surfacing as `network_error: timed out`
# at the script-level urlopen tier (cycle-23 prompts 3+4, ~590-642s).
_CLI_TIMEOUT_SECONDS = 600


# Claude CLI rate-limit reset wall-clock parser.
#
# Some Claude plans (currently MAX, possibly others in the future) emit
# user-facing wall-clock reset strings like
#   "HTTP 429: You've hit your limit · resets 3:40pm (America/Toronto)"
# or (newer format, observed 2026-04-29):
#   "HTTP 429: You've hit your limit · resets May 1, 8pm (America/Toronto)"
# rather than a relative Retry-After header. When this format is present,
# we parse it into a UTC datetime so the retry layer can wait the right
# amount of time and the UI can render a precise countdown.
#
# Other plans / message formats fall through cleanly: the parser returns
# ``None``, ProviderRateLimitError.reset_at stays unset, and
# ``call_provider_with_retry`` falls back to the default rate-limit
# backoff (or the ``retry_after`` field when populated by Anthropic-API
# Retry-After headers).
#
# Capturing groups: month_name, day, hour, minute, am/pm, tz_name.
# The month+day prefix is optional — handles both old ("resets 8pm")
# and new ("resets May 1, 8pm") formats.
_MONTH_ABBREVS = (
    "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
)
_CLI_RATE_LIMIT_RESET_RX = re.compile(
    r"resets\s+"
    rf"(?:({_MONTH_ABBREVS})\w*\s+(\d{{1,2}}),?\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*"
    r"(?:[·•]\s*)?\(?([A-Za-z]+(?:/[A-Za-z_]+)?)?\)?",
    re.IGNORECASE,
)

# Month name → number lookup for date-aware parsing.
_MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_cli_reset_time(message: str) -> datetime | None:
    """Parse Claude CLI rate-limit reset wall-clock time → UTC datetime.

    Plan-agnostic: handles message formats currently emitted by the MAX
    plan and any other plan that follows one of these patterns:

    - Time-only:  ``"resets 3:40pm (America/Toronto)"``
    - Date+time:  ``"resets May 1, 8pm (America/Toronto)"``

    When the message doesn't match -- e.g. a Claude API plan returning a
    structured Retry-After header instead, or a future format change --
    this returns ``None`` and the caller falls back to default rate-limit
    backoff behaviour. The pipeline degrades gracefully on either path;
    the parser exists to provide a precise countdown when possible.

    When a date prefix is present, the target day is computed from the
    month+day fields. When absent, we anchor against "today" in the
    resolved IANA timezone and roll forward 1 day if the reset time has
    already passed (limits typically reset at the same wall-clock time
    daily).
    """
    if not message:
        return None
    m = _CLI_RATE_LIMIT_RESET_RX.search(message)
    if not m:
        return None
    try:
        # Groups: month_name(1), day(2), hour(3), minute(4), ampm(5), tz(6)
        month_name = (m.group(1) or "").lower()
        day_str = m.group(2)
        hour = int(m.group(3))
        minute = int(m.group(4) or "0")
        ampm = (m.group(5) or "").lower()
        tz_name = m.group(6) or "UTC"
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        if ZoneInfo is not None:
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = timezone.utc
        else:
            tz = timezone.utc
        now_local = datetime.now(tz)

        if month_name and day_str:
            # Date-aware: "resets May 1, 8pm" — compute the explicit date.
            month_num = _MONTH_NUM.get(month_name[:3])
            if month_num is None:
                return None
            day = int(day_str)
            # Use current year; if the date has already passed this year,
            # roll forward to next year.
            year = now_local.year
            try:
                reset_local = now_local.replace(
                    year=year, month=month_num, day=day,
                    hour=hour, minute=minute, second=0, microsecond=0,
                )
            except ValueError:
                return None  # Invalid date (e.g. Feb 30)
            if reset_local <= now_local:
                # Date+time is in the past → next year's occurrence.
                try:
                    reset_local = reset_local.replace(year=year + 1)
                except ValueError:
                    return None  # Leap year edge case (Feb 29)
        else:
            # Time-only: "resets 8pm" — anchor to today, roll forward if past.
            reset_local = now_local.replace(
                hour=hour, minute=minute, second=0, microsecond=0,
            )
            if reset_local <= now_local:
                reset_local = reset_local + timedelta(days=1)

        return reset_local.astimezone(timezone.utc)
    except Exception:
        return None


class ClaudeCLIProvider(LLMProvider):
    """LLM provider that calls the claude CLI subprocess.

    Uses native CLI features for structured output and effort control:
    - ``--json-schema``: CLI validates output against the schema and returns
      the parsed result in the ``structured_output`` field of the JSON envelope.
    - ``--effort``: Controls thinking depth
      (low/medium/high/xhigh/max — xhigh is Opus 4.7 only).

    Note: ``task_budget`` and ``compaction`` (Opus 4.7 betas) accept a value
    but are currently no-ops in the CLI path — the CLI does not surface those
    knobs.  They are accepted here so the abstract ABC stays uniform across
    providers and callers don't need to branch.
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
        task_budget: int | None = None,  # accepted, currently a no-op (see class docstring)
        compaction: bool = False,  # accepted, currently a no-op (see class docstring)
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
            "--model",
            model,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
        ]

        # System prompt as arg — typically small (< 10KB).
        # User message piped via stdin — can be very large (explore prompts
        # with hundreds of files exceed the OS ARG_MAX limit as CLI args).
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        # Pass effort level when specified (low/medium/high/xhigh/max).
        # Haiku doesn't support the effort parameter — skip it to avoid errors.
        # xhigh is Opus 4.7 only; downgrade elsewhere so non-4.7 models don't 400.
        effective_effort = effort
        model_lower = model.lower()
        if effective_effort == "xhigh" and "opus-4-7" not in model_lower:
            logger.warning(
                "effort='xhigh' requires Opus 4.7 (model=%s) — downgrading to 'high'", model,
            )
            effective_effort = "high"
        if effective_effort and "haiku" not in model_lower:
            cmd.extend(["--effort", effective_effort])

        logger.debug("claude_cli executing model=%s effort=%s", model, effective_effort)

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=user_message.encode()),
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
        except OSError as exc:
            raise ProviderError(
                f"Claude CLI subprocess failed: {exc}",
                retryable=False,
            )

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace")
            stdout_text = stdout.decode(errors="replace")

            # The CLI emits structured errors to stdout as a JSON envelope even
            # on non-zero exit (``is_error: true`` + ``api_error_status`` +
            # ``result`` message). stderr is typically empty in that case, so
            # we must parse stdout to surface the real cause. Without this,
            # callers see only "Claude CLI exited with code 1: " and the
            # underlying reason ("Prompt is too long", 429, etc.) is lost.
            error_message = stderr_text
            api_status: int | None = None
            if not error_message.strip() and stdout_text.strip():
                try:
                    envelope = json.loads(stdout_text)
                    if isinstance(envelope, dict) and envelope.get("is_error"):
                        api_status = envelope.get("api_error_status")
                        result_msg = str(envelope.get("result") or "").strip()
                        status_part = f"HTTP {api_status}: " if api_status else ""
                        error_message = f"{status_part}{result_msg}" or stdout_text
                except (json.JSONDecodeError, TypeError):
                    error_message = stdout_text

            haystack = (error_message + " " + stderr_text).lower()
            is_rate_limit = (
                "rate limit" in haystack
                or "you've hit your limit" in haystack
                or "429" in haystack
                or api_status == 429
            )
            full_message = (
                f"Claude CLI exited with code {proc.returncode}: {error_message}"
            )
            if is_rate_limit:
                # Typed rate-limit error -- caller can wait until reset.
                # CLI MAX subscription emits wall-clock reset time
                # ("resets 3:40pm (America/Toronto)"); we parse it into a
                # UTC datetime so ``call_provider_with_retry`` and the
                # batch primitives can wait the right amount of time
                # (or surface reset_at to the UI when the wait exceeds
                # the in-process retry cap).
                reset_at = _parse_cli_reset_time(error_message)
                raise ProviderRateLimitError(
                    full_message,
                    reset_at=reset_at,
                    provider_name="claude_cli",
                )
            retryable = (
                "overloaded" in haystack
                or "timeout" in haystack
                or "529" in haystack
                or api_status in (529, 503)
            )
            raise ProviderError(full_message, retryable=retryable)

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
        if effective_effort:
            parts.append(f"effort={effective_effort}")
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
