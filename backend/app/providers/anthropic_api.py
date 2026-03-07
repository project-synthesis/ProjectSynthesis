from __future__ import annotations

import json
import logging
from typing import AsyncGenerator, Callable

from app.providers.base import AgenticResult, LLMProvider, ToolDefinition, parse_json_robust

logger = logging.getLogger(__name__)

# Models that support adaptive thinking via thinking: {type: "adaptive"}.
# budget_tokens is deprecated on these models — adaptive thinking replaces it.
# Haiku 4.5 is NOT in this set (no thinking support).
_THINKING_MODELS: frozenset[str] = frozenset({"claude-opus-4-6", "claude-sonnet-4-6"})

# max_tokens for turns that may include thinking blocks (thinking + output must fit).
# Haiku / non-thinking models keep the standard 8192.
_MAX_TOKENS_THINKING = 16000
_MAX_TOKENS_DEFAULT = 8192


class AnthropicAPIProvider(LLMProvider):
    """LLM provider using the Anthropic Python SDK with direct API calls."""

    def __init__(self, api_key: str):
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    @property
    def name(self) -> str:
        return "anthropic_api"

    def _make_extra(self, model: str, *, schema: dict | None = None) -> tuple[int, dict]:
        """Return (max_tokens, extra_kwargs) for messages.stream() calls.

        When ``schema`` is provided, thinking is suppressed — the Anthropic API
        rejects requests that combine output_config.json_schema with thinking.
        """
        use_thinking = model in _THINKING_MODELS
        max_tokens = _MAX_TOKENS_THINKING if use_thinking else _MAX_TOKENS_DEFAULT
        if schema is not None:
            # output_config.json_schema is incompatible with adaptive thinking.
            # Requires: additionalProperties=False on all objects (Anthropic requirement).
            extra: dict = {"output_config": {"format": {"type": "json_schema", "schema": schema}}}
        elif use_thinking:
            extra = {"thinking": {"type": "adaptive"}}
        else:
            extra = {}
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
        output_schema: dict | None = None,
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

        # Adaptive thinking: enabled for Opus 4.6 and Sonnet 4.6 (both GA, no beta header).
        # budget_tokens is deprecated on these models — adaptive thinking replaces it.
        # Streaming (get_final_message) prevents HTTP timeouts on long thinking+tool turns.
        max_tokens, extra = self._make_extra(model)

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
            ) as stream:
                response = await stream.get_final_message()
            # Append full content including any thinking blocks — the API requires
            # thinking blocks to be preserved in conversation history when using thinking.
            messages.append({"role": "assistant", "content": response.content})  # type: ignore[dict-item]

            if response.stop_reason == "tool_use":
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
                        if on_tool_call:
                            on_tool_call(block.name, block.input)

                        # Isolate tool handler errors: return an error tool_result
                        # so the model can try an alternate strategy instead of
                        # crashing the entire agentic loop.
                        is_error = False
                        try:
                            if block.name not in tool_map:
                                raise KeyError(
                                    f"Model requested unknown tool {block.name!r}. "
                                    f"Available: {list(tool_map)}"
                                )
                            result_str = await tool_map[block.name](block.input)
                        except Exception as tool_exc:
                            logger.warning(
                                "Tool %r raised %s: %s — returning error result to model",
                                block.name, type(tool_exc).__name__, tool_exc,
                            )
                            result_str = f"Error: {type(tool_exc).__name__}: {tool_exc}"
                            is_error = True

                        all_tool_calls.append({
                            "name": block.name,
                            "input": block.input,
                            "output": result_str[:500] if result_str else "",
                        })
                        tool_result: dict = {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
                        }
                        if is_error:
                            tool_result["is_error"] = True
                        results.append(tool_result)
                messages.append({"role": "user", "content": results})  # type: ignore[dict-item]
            else:
                # "end_turn", "max_tokens", "stop_sequence", or any other reason —
                # extract whatever text the model produced and return it.
                text = next(
                    (b.text for b in response.content if hasattr(b, "text")),
                    "",
                )
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

        logger.warning(f"Agentic loop hit max_turns ({max_turns})")
        return AgenticResult(text="", tool_calls=all_tool_calls, stop_reason="max_turns")
