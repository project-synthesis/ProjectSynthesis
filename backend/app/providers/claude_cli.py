from __future__ import annotations

import json
import re
import logging
from typing import AsyncGenerator, Callable

from app.providers.base import LLMProvider, ToolDefinition, AgenticResult

logger = logging.getLogger(__name__)


class ClaudeCLIProvider(LLMProvider):
    """LLM provider using Claude Code CLI via claude-agent-sdk.

    Uses Max subscription via CLI for zero API cost.
    """

    def __init__(self):
        try:
            from claude_agent_sdk import query, ClaudeAgentOptions
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

    async def complete(self, system: str, user: str, model: str) -> str:
        from claude_agent_sdk import ClaudeAgentOptions, AssistantMessage, TextBlock

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
        from claude_agent_sdk import ClaudeAgentOptions, AssistantMessage, TextBlock

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

    async def complete_json(self, system: str, user: str, model: str, schema: type | None = None) -> dict:
        raw = await self.complete(system, user, model)
        return _parse_json(raw)

    async def complete_agentic(
        self,
        system: str,
        user: str,
        model: str,
        tools: list[ToolDefinition],
        max_turns: int = 20,
        on_tool_call: Callable[[str, dict], None] | None = None,
    ) -> AgenticResult:
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            AssistantMessage,
            TextBlock,
            tool,
            create_sdk_mcp_server,
        )

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
            ) -> dict:
                result = await _handler(args)
                if _on:
                    _on(_name, args)
                return {"content": [{"type": "text", "text": result}]}

            sdk_tools.append(_tool_fn)

        mcp_server = create_sdk_mcp_server(
            name="pf-tools", version="1.0.0", tools=sdk_tools
        )
        allowed = [f"mcp__pf-tools__{td.name}" for td in tools]

        async def _prompt_stream():
            yield {"type": "user", "message": {"role": "user", "content": user}}

        options = ClaudeAgentOptions(
            system_prompt=system,
            model=model,
            max_turns=max_turns,
            mcp_servers={"pf-tools": mcp_server},
            allowed_tools=allowed,
        )

        full_text = ""
        tool_calls = []
        async for msg in self._query(prompt=_prompt_stream(), options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        full_text = block.text

        return AgenticResult(text=full_text, tool_calls=tool_calls)


def _parse_json(text: str) -> dict:
    """3-strategy JSON parsing fallback."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
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

    raise ValueError(f"Could not parse JSON from response: {text[:200]}...")
