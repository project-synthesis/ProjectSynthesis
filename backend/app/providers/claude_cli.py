from __future__ import annotations

import logging
import os
from typing import AsyncGenerator, Callable

from app.providers.base import AgenticResult, LLMProvider, ToolDefinition, parse_json_robust

logger = logging.getLogger(__name__)

# The SDK's default stream-close timeout is 60 s — far too short for
# the Explore stage which runs up to 25 tool turns with network I/O.
# After 60 s, wait_for_result_and_end_input() closes stdin even while
# the CLI is still mid-conversation, causing the next transport.write()
# to raise CLIConnectionError("ProcessTransport is not ready for writing").
# Set the timeout to 10 minutes so stdin stays open for the full run.
os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "600000")


class ClaudeCLIProvider(LLMProvider):
    """LLM provider using Claude Code CLI via claude-agent-sdk.

    Uses Max subscription via CLI for zero API cost.
    init.sh unsets CLAUDECODE before launching the backend so nested-session
    issues never arise in normal operation.
    """

    def __init__(self):
        try:
            from claude_agent_sdk import query
            self._query = query
        except ImportError:
            raise ImportError(
                "claude-agent-sdk is required for ClaudeCLIProvider. "
                "Install it with: pip install claude-agent-sdk"
            )

    @property
    def name(self) -> str:
        return "claude_cli"

    async def complete(self, system: str, user: str, model: str) -> str:
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
                        if not text:
                            continue
                        # Word-boundary streaming: split on whitespace to avoid
                        # cutting tokens mid-word, then batch words into chunks
                        # of ~60 chars for a smooth progressive display.
                        # First chunk yields immediately; subsequent chunks get
                        # a 3ms pause (vs 10ms fixed — 70% less total sleep overhead).
                        CHUNK_TARGET = 60
                        words = text.split(" ")
                        current = ""
                        first = True
                        for word in words:
                            candidate = (current + " " + word).lstrip() if current else word
                            if len(candidate) >= CHUNK_TARGET and current:
                                yield current + " "
                                if not first:
                                    await asyncio.sleep(0.003)
                                first = False
                                current = word
                            else:
                                current = candidate
                        if current:
                            yield current

    async def complete_json(
        self,
        system: str,
        user: str,
        model: str,
        schema: dict | None = None,
    ) -> dict:
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
    ) -> AgenticResult:
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
                    try:
                        _on(_name, args)
                    except Exception as _cb_err:
                        logger.warning("on_tool_call callback raised: %s", _cb_err)
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
            async def _submit_tool(
                args: dict,
                _cap=captured_output,
                _on=on_tool_call,
            ) -> dict:
                if not _cap:
                    _cap.update(args)
                if _on:
                    try:
                        _on("submit_result", args)
                    except Exception as _cb_err:
                        logger.warning("on_tool_call callback raised: %s", _cb_err)
                return {"content": [{"type": "text", "text": "Result submitted. Exploration complete."}]}

            sdk_tools.append(_submit_tool)

        mcp_server = create_sdk_mcp_server(
            name="pf-tools", version="1.0.0", tools=sdk_tools
        )
        allowed = [f"mcp__pf-tools__{td.name}" for td in tools]
        if output_schema:
            allowed.append("mcp__pf-tools__submit_result")

        # Do NOT set output_format in ClaudeAgentOptions.  When output_format is
        # set, the SDK may surface structured output via ResultMessage.structured_output
        # without the model having called any exploration tools — the model fills
        # the schema from training knowledge rather than actual repository reads.
        # submit_result MCP tool is the canonical structured-output mechanism and is
        # enforced by the explore system prompt ("you MUST call the submit_result tool").
        options = ClaudeAgentOptions(
            system_prompt=system,
            model=model,
            max_turns=max_turns,
            mcp_servers={"pf-tools": mcp_server},
            allowed_tools=allowed,
        )

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
                        if on_agent_text:
                            try:
                                on_agent_text(msg_text)
                            except Exception:
                                pass
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

            if actual is not e:
                raise actual from e
            raise

        # Prefer SDK-level structured output, then submit_result capture, then text
        final_output = sdk_structured_output or (captured_output if captured_output else None)
        return AgenticResult(text=full_text, tool_calls=tool_calls, output=final_output)
