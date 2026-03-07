from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, AsyncGenerator, Callable

if TYPE_CHECKING:
    from app.providers.anthropic_api import AnthropicAPIProvider

from app.providers.base import AgenticResult, LLMProvider, ToolDefinition, parse_json_robust

logger = logging.getLogger(__name__)


class ClaudeCLIProvider(LLMProvider):
    """LLM provider using Claude Code CLI via claude-agent-sdk.

    Uses Max subscription via CLI for zero API cost.

    At call time, if the CLAUDECODE environment variable is set (meaning the
    backend is running inside an active Claude Code session), all methods
    transparently delegate to AnthropicAPIProvider to avoid nested-subprocess
    crashes (anyio ExceptionGroup from SubprocessCLITransport).
    """

    def __init__(self):
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
            self._query = query
            self._options_cls = ClaudeAgentOptions
        except ImportError:
            raise ImportError(
                "claude-agent-sdk is required for ClaudeCLIProvider. "
                "Install it with: pip install claude-agent-sdk"
            )

    @property
    def name(self) -> str:
        return "claude_cli"

    def _get_api_fallback(self) -> "AnthropicAPIProvider | None":
        """Lazily create an AnthropicAPIProvider fallback if ANTHROPIC_API_KEY is available."""
        if not hasattr(self, "_api_fallback"):
            from app.config import settings
            if settings.ANTHROPIC_API_KEY:
                from app.providers.anthropic_api import AnthropicAPIProvider
                self._api_fallback: object = AnthropicAPIProvider(api_key=settings.ANTHROPIC_API_KEY)
                logger.info("ClaudeCLIProvider: created AnthropicAPIProvider fallback for nested-session delegation")
            else:
                self._api_fallback = None
        return self._api_fallback  # type: ignore[return-value]

    def _check_nested_session(self) -> "AnthropicAPIProvider | None":
        """Return API fallback provider if running inside a Claude Code session, else None.

        When CLAUDECODE env var is set, spawning a subprocess via claude-agent-sdk
        will crash with an anyio ExceptionGroup (nested session). Delegating to the
        API provider avoids this entirely.
        """
        if os.environ.get("CLAUDECODE"):
            fallback = self._get_api_fallback()
            if fallback is None:
                raise RuntimeError(
                    "ClaudeCLIProvider cannot run inside a Claude Code session "
                    "(CLAUDECODE env is set) without a fallback API key. "
                    "Set ANTHROPIC_API_KEY in your .env file to enable automatic "
                    "delegation to AnthropicAPIProvider."
                )
            return fallback
        return None

    async def complete(self, system: str, user: str, model: str) -> str:
        if fb := self._check_nested_session():
            return await fb.complete(system, user, model)

        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock

        options = ClaudeAgentOptions(
            system_prompt=system,
            model=model,
            max_turns=1,
        )
        full_text = ""
        async for msg in self._query(prompt=user, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        full_text += block.text
        return full_text

    async def stream(self, system: str, user: str, model: str) -> AsyncGenerator[str, None]:
        """Stream LLM output in small chunks for progressive UI display.

        The claude-agent-sdk returns full TextBlocks, so we split them into
        smaller chunks to simulate token-level streaming and provide a
        responsive UI experience.
        """
        if fb := self._check_nested_session():
            async for chunk in fb.stream(system, user, model):
                yield chunk
            return

        import asyncio

        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock

        options = ClaudeAgentOptions(
            system_prompt=system,
            model=model,
            max_turns=1,
        )
        async for msg in self._query(prompt=user, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text = block.text
                        # Chunk large text blocks for progressive streaming
                        chunk_size = 80
                        if len(text) <= chunk_size:
                            yield text
                        else:
                            for i in range(0, len(text), chunk_size):
                                yield text[i:i + chunk_size]
                                await asyncio.sleep(0.01)  # Small delay for progressive display

    async def complete_json(
        self,
        system: str,
        user: str,
        model: str,
        schema: dict | None = None,
    ) -> dict:
        if fb := self._check_nested_session():
            return await fb.complete_json(system, user, model, schema)

        # When a schema dict is provided, delegate to the API provider for
        # native output_config.format enforcement (guaranteed schema compliance).
        # CLI has no mechanism for schema-enforced generation.
        if schema is not None:
            if api := self._get_api_fallback():
                return await api.complete_json(system, user, model, schema)
            # No API key — fall through to best-effort text parsing with a warning.
            logger.warning(
                "complete_json(schema=...) called on ClaudeCLIProvider with no "
                "ANTHROPIC_API_KEY — schema will NOT be enforced at generation time. "
                "Set ANTHROPIC_API_KEY in your .env file for guaranteed JSON schema compliance."
            )

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
        if fb := self._check_nested_session():
            return await fb.complete_agentic(system, user, model, tools, max_turns, on_tool_call, output_schema)

        # complete_agentic uses create_sdk_mcp_server, which communicates via the
        # CLI subprocess's stdin/stdout control protocol.  There is a persistent
        # race condition in the SDK: _handle_control_request tasks (MCP tool
        # responses) run concurrently with stream_input, which closes stdin as
        # soon as _first_result_event fires.  If a tool-response write lands just
        # after end_input(), the transport raises CLIConnectionError regardless of
        # whether we use a string prompt or an async generator.
        #
        # The API provider uses the Anthropic messages API with native tool_use —
        # no subprocess transport, no race condition.  Always prefer it for agentic
        # calls; fall back to the CLI path only when no API key is available.
        if api := self._get_api_fallback():
            return await api.complete_agentic(system, user, model, tools, max_turns, on_tool_call, output_schema)

        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
            create_sdk_mcp_server,
            tool,
        )

        # tool_calls must be declared before the loop so _tool_fn closures
        # can reference it via default argument capture.
        tool_calls: list[dict] = []
        sdk_tools = []
        for td in tools:
            handler = td.handler
            name = td.name

            @tool(td.name, td.description, td.input_schema)
            async def _tool_fn(
                args: dict,
                _handler=handler,
                _name=name,
                _on=on_tool_call,
                _calls=tool_calls,
            ) -> dict:
                result = await _handler(args)
                result_str = result if isinstance(result, str) else str(result)
                if _on:
                    _on(_name, args)
                _calls.append({
                    "name": _name,
                    "input": args,
                    "output": result_str[:500] if result_str else "",
                })
                return {"content": [{"type": "text", "text": result_str}]}

            sdk_tools.append(_tool_fn)

        # Inject submit_result MCP tool for structured output (universal fallback).
        # The closure captures the output so we can read it after the loop.
        captured_output: dict = {}
        if output_schema:
            @tool(
                "submit_result",
                (
                    "Submit your final structured result. Call this tool exactly once "
                    "when you have finished all exploration and are ready to return your "
                    "complete findings. Do not call any other tools after this."
                ),
                output_schema,
            )
            async def _submit_tool(args: dict, _cap=captured_output) -> dict:
                _cap.update(args)
                return {"content": [{"type": "text", "text": "Result submitted. Exploration complete."}]}

            sdk_tools.append(_submit_tool)

        mcp_server = create_sdk_mcp_server(
            name="pf-tools", version="1.0.0", tools=sdk_tools
        )
        allowed = [f"mcp__pf-tools__{td.name}" for td in tools]
        if output_schema:
            allowed.append("mcp__pf-tools__submit_result")

        # Use SDK-native output_format for schema-enforced structured output.
        # Falls back to captured_output from the submit_result MCP tool if the
        # SDK version doesn't support output_format.
        options_kwargs: dict = dict(
            system_prompt=system,
            model=model,
            max_turns=max_turns,
            mcp_servers={"pf-tools": mcp_server},
            allowed_tools=allowed,
        )
        if output_schema:
            options_kwargs["output_format"] = {"type": "json_schema", "schema": output_schema}

        options = ClaudeAgentOptions(**options_kwargs)

        full_text = ""
        sdk_structured_output: dict | None = None

        # Use AsyncIterable[dict] prompt — query() signature: str | AsyncIterable[dict].
        # SDK >=0.1.46 fixed the string-prompt race (PR #630: stdin was closed before MCP
        # server initialization completed). The async-generator path has been safe since
        # 0.1.45: stream_input() detects sdk_mcp_servers and calls
        # wait_for_result_and_end_input() (waits for _first_result_event before closing
        # stdin), keeping stdin open for the full MCP tool-calling loop.
        async def _prompt_stream():
            yield {
                "type": "user",
                "session_id": "",
                "message": {"role": "user", "content": user},
                "parent_tool_use_id": None,
            }

        try:
            async for msg in self._query(prompt=_prompt_stream(), options=options):
                if isinstance(msg, AssistantMessage):
                    msg_text = "".join(
                        block.text for block in msg.content if isinstance(block, TextBlock)
                    )
                    if msg_text:
                        full_text = msg_text
                else:
                    # Check for ResultMessage with structured_output (SDK output_format support)
                    structured = getattr(msg, "structured_output", None)
                    if structured:
                        sdk_structured_output = structured
        except BaseException as e:
            # Unwrap anyio ExceptionGroup to expose the actual sub-exception.
            actual = e
            if hasattr(e, "exceptions") and e.exceptions:
                actual = e.exceptions[0]
                logger.error(
                    "ClaudeCLIProvider agentic loop failed: %s: %s",
                    type(actual).__name__,
                    actual,
                )

            # CLIConnectionError means the subprocess/MCP transport failed to start.
            # This happens when the CLI can't open an interactive session (nested session,
            # auth issue, or MCP subprocess conflict). Fall back to API provider if available.
            if type(actual).__name__ in ("CLIConnectionError", "ProcessError"):
                fallback = self._get_api_fallback()
                if fallback is not None:
                    logger.warning(
                        "Falling back to AnthropicAPIProvider for agentic call "
                        "due to %s: %s",
                        type(actual).__name__,
                        actual,
                    )
                    return await fallback.complete_agentic(
                        system, user, model, tools, max_turns, on_tool_call, output_schema
                    )

            if actual is not e:
                raise actual from e
            raise

        # Prefer SDK-level structured output, then submit_result capture, then text
        final_output = sdk_structured_output or (captured_output if captured_output else None)
        return AgenticResult(text=full_text, tool_calls=tool_calls, output=final_output)
