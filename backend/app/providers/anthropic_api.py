from __future__ import annotations

import contextvars
import json
import logging
from typing import AsyncGenerator, Callable

from app.providers.base import AgenticResult, CompletionUsage, LLMProvider, ToolDefinition, invoke_tool, parse_json_robust

logger = logging.getLogger(__name__)

# Task-local usage tracking — safe for concurrent asyncio tasks sharing one provider.
_usage_var: contextvars.ContextVar[CompletionUsage | None] = contextvars.ContextVar(
    "_anthropic_usage", default=None,
)

# Models that support adaptive thinking via thinking: {type: "adaptive"}.
# budget_tokens is deprecated on these models — adaptive thinking replaces it.
# Haiku 4.5 supports manual thinking (budget_tokens) but NOT adaptive thinking,
# so it is excluded from this set.
_THINKING_MODELS: frozenset[str] = frozenset({"claude-opus-4-6", "claude-sonnet-4-6"})

# max_tokens for turns that may include thinking blocks (thinking + output must fit).
# Haiku / non-thinking models keep the standard 8192.
_MAX_TOKENS_THINKING = 16000
_MAX_TOKENS_DEFAULT = 8192

# L2: Default effort level per model family.  Reduces token spend on
# cost-sensitive stages (Haiku synthesis, Sonnet classification) while
# preserving full thinking depth for creative Opus work.
_EFFORT_BY_MODEL_PREFIX: dict[str, str] = {
    "claude-opus-4": "high",
    "claude-sonnet-4": "medium",
    "claude-haiku-4": "low",
}


def _extract_usage(response, model: str) -> CompletionUsage:
    """Extract CompletionUsage from an Anthropic SDK response object."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return CompletionUsage(is_estimated=True, model=model)
    return CompletionUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        is_estimated=False,
        model=model,
    )


class AnthropicAPIProvider(LLMProvider):
    """LLM provider using the Anthropic Python SDK with direct API calls."""

    def __init__(self, api_key: str, *, betas: list[str] | None = None):
        import anthropic
        extra_headers: dict[str, str] = {}
        if betas:
            extra_headers["anthropic-beta"] = ",".join(betas)
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            default_headers=extra_headers if extra_headers else None,
        )

    @property
    def name(self) -> str:
        return "anthropic_api"

    def get_last_usage(self) -> CompletionUsage | None:
        """Return token usage from the most recent LLM call (task-local)."""
        return _usage_var.get(None)

    async def validate_key(self) -> tuple[bool, str]:
        """Validate the API key with a minimal API call.

        Returns:
            (valid, message) — True + success message, or False + error detail.
        """
        import anthropic

        try:
            await self._client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            return True, "API key is valid"
        except anthropic.AuthenticationError:
            return False, "Invalid API key — authentication failed"
        except anthropic.PermissionDeniedError:
            return False, "API key lacks required permissions"
        except Exception as e:
            return False, f"Key saved but validation failed: {type(e).__name__}: {e}"

    def _make_extra(
        self, model: str, *, schema: dict | None = None, effort: str | None = None,
    ) -> tuple[int, dict]:
        """Return (max_tokens, extra_kwargs) for messages.stream() calls.

        When ``schema`` is provided, thinking is suppressed — the Anthropic API
        rejects requests that combine output_config.json_schema with thinking.

        ``effort`` controls thinking depth via ``output_config.effort``.
        When None, a model-family default is applied from ``_EFFORT_BY_MODEL_PREFIX``.
        """
        use_thinking = model in _THINKING_MODELS
        max_tokens = _MAX_TOKENS_THINKING if use_thinking else _MAX_TOKENS_DEFAULT
        if schema is not None:
            # output_config.json_schema is incompatible with adaptive thinking.
            # Requires: additionalProperties=False on all objects (Anthropic requirement).
            if use_thinking:
                logger.warning(
                    "Adaptive thinking disabled for model %s because schema was provided "
                    "(JSON schema output is incompatible with extended thinking).",
                    model,
                )
            extra: dict = {"output_config": {"format": {"type": "json_schema", "schema": schema}}}
        elif use_thinking:
            extra = {"thinking": {"type": "adaptive"}}
        else:
            extra = {}

        # L2: Resolve effort — explicit > model-family default
        resolved_effort = effort
        if resolved_effort is None:
            for prefix, eff in _EFFORT_BY_MODEL_PREFIX.items():
                if model.startswith(prefix):
                    resolved_effort = eff
                    break
        if resolved_effort is not None:
            extra.setdefault("output_config", {})["effort"] = resolved_effort

        return max_tokens, extra

    async def _call_stream(
        self, model: str, system: str, user: str, max_tokens: int, extra: dict
    ) -> str:
        """Single-shot stream call; returns the first text block from the final message.

        Streaming even for single-shot calls prevents HTTP timeouts on large
        thinking outputs and is required by the SDK for Opus 4.6 with high max_tokens.
        """
        async with self._client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            **extra,
        ) as stream:
            response = await stream.get_final_message()
        _usage_var.set(_extract_usage(response, model))
        # Use next() — thinking blocks may precede the text block.
        return next((b.text for b in response.content if hasattr(b, "text")), "")

    async def complete(self, system: str, user: str, model: str) -> str:
        max_tokens, extra = self._make_extra(model)
        return await self._call_stream(model, system, user, max_tokens, extra)

    async def stream(self, system: str, user: str, model: str) -> AsyncGenerator[str, None]:
        max_tokens, extra = self._make_extra(model)
        async with self._client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            **extra,
        ) as stream:
            # text_stream automatically skips thinking deltas — only yields text tokens.
            async for chunk in stream.text_stream:
                yield chunk
            # Capture usage after stream is fully consumed
            response = await stream.get_final_message()
            _usage_var.set(_extract_usage(response, model))

    async def complete_json(
        self,
        system: str,
        user: str,
        model: str,
        schema: dict | None = None,
    ) -> dict:
        """Structured JSON output.

        When ``schema`` is provided, uses ``output_config.format`` for guaranteed
        schema-compliant output (thinking is suppressed — the two are incompatible
        per the Anthropic API spec). The API enforces the schema server-side.

        When ``schema`` is None, delegates to complete() and applies
        parse_json_robust() 3-strategy fallback.
        """
        if schema is not None:
            max_tokens, extra = self._make_extra(model, schema=schema)
            raw_text = await self._call_stream(model, system, user, max_tokens, extra)
            try:
                return json.loads(raw_text)
            except (json.JSONDecodeError, TypeError):
                return parse_json_robust(raw_text)

        raw = await self.complete(system, user, model)
        return parse_json_robust(raw)

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
        api_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]
        tool_map = {t.name: t.handler for t in tools}

        # Anthropic best practice: inject a 'submit_result' tool when structured
        # output is needed. The model calls it with its findings instead of producing
        # free-form text — the tool input IS the structured output, already parsed
        # by the API as a dict. Zero regex, guaranteed schema compliance.
        if output_schema:
            api_tools.append({
                "name": "submit_result",
                "description": (
                    "Submit your final structured result. Call this tool exactly once "
                    "when you have finished all exploration and are ready to return your "
                    "complete findings. Do not call any other tools after this."
                ),
                "input_schema": output_schema,
            })

        messages = [{"role": "user", "content": user}]
        all_tool_calls: list[dict] = []
        turns = 0
        accumulated_usage = CompletionUsage(model=model)

        # Adaptive thinking: enabled for Opus 4.6 and Sonnet 4.6 (both GA, no beta header).
        # budget_tokens is deprecated on these models — adaptive thinking replaces it.
        # Streaming (get_final_message) prevents HTTP timeouts on long thinking+tool turns.
        max_tokens, extra = self._make_extra(model)

        # M6: Compaction beta — automatic context summarization for long agentic loops.
        from app.config import settings as _cfg
        compaction_kwargs: dict = {}
        if _cfg.COMPACTION_ENABLED:
            compaction_kwargs["context_management"] = {
                "edits": [{"type": "compact_20260112"}]
            }

        while turns < max_turns:
            turns += 1
            async with self._client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                # Automatic prompt caching: caches the last cacheable block in the request
                # (the system prompt). Turn 1 pays full price; turns 2+ save ~90% on system
                # prompt tokens. Particularly impactful in the explore loop (15 turns).
                cache_control={"type": "ephemeral"},
                system=system,
                tools=api_tools,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
                **extra,
                **compaction_kwargs,
            ) as stream:
                response = await stream.get_final_message()
            # Accumulate usage from this turn
            accumulated_usage += _extract_usage(response, model)
            _usage_var.set(accumulated_usage)
            # Append full content including any thinking blocks — the API requires
            # thinking blocks to be preserved in conversation history when using thinking.
            messages.append({"role": "assistant", "content": response.content})  # type: ignore[dict-item]

            if response.stop_reason == "tool_use":
                # Emit any text blocks (reasoning text before tool calls) as agent_text events.
                # Claude often narrates its intent before calling a tool — surfaces this to the UI.
                if on_agent_text:
                    for block in response.content:
                        if hasattr(block, "text") and block.type == "text" and block.text:
                            try:
                                on_agent_text(block.text)
                            except Exception:
                                pass

                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        if block.name == "submit_result":
                            # Structured output delivered — return immediately.
                            # block.input is a dict already validated against output_schema.
                            return AgenticResult(
                                text="",
                                tool_calls=all_tool_calls,
                                output=block.input,
                            )
                        if block.name not in tool_map:
                            # Unknown tool — return error result to model
                            result_str = (
                                f"Error: KeyError: Model requested unknown tool {block.name!r}. "
                                f"Available: {list(tool_map)}"
                            )
                            is_error = True
                            all_tool_calls.append({
                                "name": block.name,
                                "input": block.input,
                                "output": result_str[:500],
                            })
                            if on_tool_call:
                                try:
                                    on_tool_call(block.name, block.input)
                                except Exception as cb_err:
                                    logger.warning("on_tool_call callback raised: %s", cb_err)
                        else:
                            result_str, is_error = await invoke_tool(
                                block.name, block.input, tool_map[block.name],
                                all_tool_calls, on_tool_call,
                            )

                        tool_result: dict = {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
                        }
                        if is_error:
                            tool_result["is_error"] = True
                        results.append(tool_result)
                messages.append({"role": "user", "content": results})  # type: ignore[dict-item]
            elif response.stop_reason == "pause_turn":
                # L1: Server-side tool hit its iteration limit — the model wants
                # to continue.  Assistant content is already appended; re-send.
                logger.info(
                    "Agentic loop received pause_turn after %d turn(s); continuing",
                    turns,
                )
                continue
            else:
                # "end_turn", "max_tokens", "stop_sequence", or any other reason —
                # extract whatever text the model produced and return it.
                text = next(
                    (b.text for b in response.content if hasattr(b, "text")),
                    "",
                )
                if on_agent_text and text:
                    try:
                        on_agent_text(text)
                    except Exception:
                        pass
                if response.stop_reason != "end_turn":
                    logger.warning(
                        "Agentic loop ended with stop_reason=%r after %d turn(s)",
                        response.stop_reason,
                        turns,
                    )
                return AgenticResult(
                    text=text,
                    tool_calls=all_tool_calls,
                    stop_reason=response.stop_reason or "end_turn",
                )

        logger.warning("Agentic loop hit max_turns (%d)", max_turns)
        return AgenticResult(text="", tool_calls=all_tool_calls, stop_reason="max_turns")
