from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, AsyncGenerator, Awaitable, Callable  # noqa: F401 — Awaitable used by invoke_tool

if TYPE_CHECKING:
    from app.services.session_context import SessionContext

logger = logging.getLogger(__name__)


# ── Cost tracking ────────────────────────────────────────────────────────

# Per-million-token pricing (USD).  Covers current Claude 4.x model family.
# Key prefix matching: "claude-opus-4" matches "claude-opus-4-6", etc.
_MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4":   {"input": 15.0, "output": 75.0, "cache_read": 1.5,  "cache_write": 18.75},
    "claude-sonnet-4": {"input": 3.0,  "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4":  {"input": 0.80, "output": 4.0,  "cache_read": 0.08, "cache_write": 1.0},
}


def _compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> float | None:
    """Compute estimated cost in USD from token counts and model pricing.

    Uses prefix matching against ``_MODEL_PRICING`` keys so that
    "claude-opus-4-6" matches the "claude-opus-4" pricing tier.

    Returns None if no pricing data is available for the model.
    """
    pricing = None
    for prefix, rates in _MODEL_PRICING.items():
        if model.startswith(prefix):
            pricing = rates
            break
    if pricing is None:
        return None

    # Normal (non-cached) input tokens — clamp to zero so cache tokens
    # exceeding total input never produce a negative cost component.
    normal_input = max(0, input_tokens - cache_read_input_tokens - cache_creation_input_tokens)
    cost = (
        normal_input * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
        + cache_read_input_tokens * pricing["cache_read"] / 1_000_000
        + cache_creation_input_tokens * pricing["cache_write"] / 1_000_000
    )
    return round(cost, 6)


@dataclass
class CompletionUsage:
    """Token usage from a single LLM call or accumulated across calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    is_estimated: bool = False
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def estimated_cost_usd(self) -> float | None:
        """Return estimated cost in USD, or None if usage is estimated/unknown."""
        if self.is_estimated:
            return None
        return _compute_cost(
            self.model, self.input_tokens, self.output_tokens,
            self.cache_read_input_tokens, self.cache_creation_input_tokens,
        )

    def to_dict(self) -> dict:
        """Serialize to dict for SSE events and DB storage."""
        return asdict(self)

    def __iadd__(self, other: CompletionUsage) -> CompletionUsage:
        """Accumulate usage from another CompletionUsage (in-place)."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        if other.is_estimated:
            self.is_estimated = True
        if other.model and not self.model:
            self.model = other.model
        return self


def parse_json_robust(text: str) -> dict:
    """3-strategy JSON parsing used by all providers and the explore stage.

    Claude models often wrap JSON in markdown code blocks. This handles all
    common output formats without losing structured data.

    1. Direct parse — model returned bare JSON
    2. Extract ```json ... ``` or ``` ... ``` code block, then parse
    3. Extract first { ... } substring via regex, then parse
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    logger.warning(
        "parse_json_robust: all 3 strategies failed. Input excerpt: %r",
        text[:300],
    )
    raise ValueError(f"Could not parse JSON from response: {text[:200]}...")


MODEL_ROUTING = {
    "explore": "claude-haiku-4-5",
    "analyze": "claude-sonnet-4-6",
    "strategy": "claude-opus-4-6",
    "optimize": "claude-opus-4-6",
    "validate": "claude-sonnet-4-6",
}

# Complexity-based model downgrade rules.  When Analyze reports "simple",
# these stages swap from the default (Opus) to a cheaper model.
_COMPLEXITY_DOWNGRADE: dict[str, dict[str, str]] = {
    "strategy": {"simple": "claude-sonnet-4-6"},  # Opus → Sonnet for simple prompts
    "optimize": {"simple": "claude-sonnet-4-6"},  # Opus → Sonnet for simple prompts
}


def select_model(
    stage: str,
    complexity: str = "moderate",
    user_override: str | None = None,
) -> str:
    """Select the model for a pipeline stage based on complexity.

    Priority: user_override > complexity downgrade > MODEL_ROUTING default.
    """
    if user_override:
        return user_override
    downgrade = _COMPLEXITY_DOWNGRADE.get(stage, {}).get(complexity)
    if downgrade:
        return downgrade
    return MODEL_ROUTING[stage]


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Awaitable[str]]


@dataclass
class AgenticResult:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    output: dict | None = None  # Structured output captured via submit_result tool or SDK output_format
    stop_reason: str = "end_turn"
    """Why the agentic loop terminated.

    Values:
      "end_turn"    — model completed naturally (most common)
      "pause_turn"  — server-side tool hit its iteration limit; the loop
                      automatically re-sends and this never appears as a
                      final stop_reason under normal operation
      "max_turns"   — loop hit the max_turns limit without completing
      "tool_error"  — a tool call failed after exhausting retries
      "cancelled"   — loop was cancelled externally
    """
    session_id: str | None = None  # H3: SDK session_id for resume support


async def invoke_tool(
    name: str,
    input_data: dict,
    handler: Callable[[dict], Awaitable[str]],
    tool_calls: list[dict],
    on_tool_call: Callable[[str, dict], None] | None = None,
) -> tuple[str, bool]:
    """Execute a tool handler, log errors, truncate output, and append to tool_calls.

    Shared by AnthropicAPIProvider and ClaudeCLIProvider to avoid
    duplicating the try/except/log/truncate/append pattern.

    Args:
        name: Tool name.
        input_data: Tool input arguments dict.
        handler: Async callable that executes the tool.
        tool_calls: Mutable list to append the call record to.
        on_tool_call: Optional sync callback fired after execution.

    Returns:
        (result_str, is_error) tuple.
    """
    is_error = False
    try:
        result_str = await handler(input_data)
        if not isinstance(result_str, str):
            result_str = str(result_str)
    except Exception as tool_exc:
        logger.warning(
            "Tool %r raised %s: %s — returning error result to model",
            name, type(tool_exc).__name__, tool_exc,
        )
        result_str = f"Error: {type(tool_exc).__name__}: {tool_exc}"
        is_error = True

    tool_calls.append({
        "name": name,
        "input": input_data,
        "output": result_str[:500] if result_str else "",
    })

    if on_tool_call:
        try:
            on_tool_call(name, input_data)
        except Exception as cb_err:
            logger.warning("on_tool_call callback raised: %s", cb_err)

    return result_str, is_error


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name identifier."""
        ...

    def get_last_usage(self) -> CompletionUsage | None:
        """Return token usage from the most recent LLM call.

        Implemented via ``contextvars.ContextVar`` on each provider so
        concurrent asyncio tasks (e.g. parallel Explore + Analyze) get
        independent usage tracking without races.

        Returns None by default (provider doesn't track usage).
        """
        return None

    async def complete_with_session(
        self,
        system: str,
        user: str,
        model: str,
        session: "SessionContext | None" = None,
        schema: dict | None = None,
    ) -> "tuple[str, SessionContext]":
        """Completion with session continuity. Returns (response, updated_session).

        Default: delegates to complete/complete_json, returns fresh SessionContext.
        CLI and API providers override with session-aware behavior.
        """
        from app.services.session_context import SessionContext as SC

        if schema:
            response = await self.complete_json(system, user, model, schema)
            text = json.dumps(response) if isinstance(response, dict) else str(response)
        else:
            text = await self.complete(system, user, model)

        from datetime import datetime, timezone
        new_session = SC(
            provider_type=self.name,
            created_at=session.created_at if session else datetime.now(timezone.utc),
            turn_count=(session.turn_count + 1) if session else 1,
        )
        return text, new_session

    @abstractmethod
    async def complete(self, system: str, user: str, model: str) -> str:
        """Single-shot completion. Returns full response text."""
        ...

    @abstractmethod
    async def stream(self, system: str, user: str, model: str) -> AsyncGenerator[str, None]:
        """Streaming completion. Yields text chunks as they arrive.

        AnthropicAPIProvider: true token-level streaming via SDK text_stream.
        ClaudeCLIProvider: true token-level streaming via CLI subprocess with
        --output-format stream-json --include-partial-messages (text_delta events).
        """
        ...

    @abstractmethod
    async def complete_json(
        self,
        system: str,
        user: str,
        model: str,
        schema: dict | None = None,
    ) -> dict:
        """Structured JSON output.

        When ``schema`` is provided (a JSON Schema dict with
        ``additionalProperties: false`` on all objects), providers SHOULD use
        native schema enforcement where available:

        - **AnthropicAPIProvider**: uses ``output_config.format`` with
          ``json_schema`` type — server-side enforcement, guaranteed compliance.
        - **ClaudeCLIProvider**: native schema enforcement is unavailable (CLI
          limitation). Injects the schema into the system prompt as an instruction
          and falls back to ``parse_json_robust()`` text extraction.

        When ``schema`` is None, falls back to 3-strategy text parsing:
        1. Parse raw response as JSON
        2. Extract first ```json ... ``` code block, parse it
        3. Extract first { ... } substring via regex, parse it
        """
        ...

    @abstractmethod
    async def complete_agentic(
        self,
        system: str,
        user: str,
        model: str,
        tools: list[ToolDefinition],
        max_turns: int = 20,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_agent_text: Callable[[str], None] | None = None,
        output_schema: dict | None = None,
        resume_session_id: str | None = None,
    ) -> AgenticResult:
        """Agentic tool-calling loop.

        If output_schema is provided, a reserved 'submit_result' tool is injected
        automatically. When the model calls it, its input (matching the schema) is
        returned as AgenticResult.output — no text parsing required. This is the
        canonical Anthropic pattern for structured output from agentic loops.

        on_tool_call: optional sync callback fired after each tool execution with
            (tool_name, tool_input). Used to stream tool events to the client.

        on_agent_text: optional sync callback fired for assistant text produced
            during the loop. Surfaces Claude's intermediate reasoning
            (e.g. "Let me check the main file first") for real-time UI display.
            Granularity varies by provider: AnthropicAPIProvider fires once per
            text content block; ClaudeCLIProvider fires once per message (may
            concatenate multiple text blocks). Thinking blocks are excluded.
        """
        ...
