from __future__ import annotations

import asyncio
import json as _json
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
        """Stream LLM output via claude CLI subprocess with true token-level streaming.

        Uses --output-format stream-json --include-partial-messages to get raw
        Anthropic API streaming events (content_block_delta / text_delta) directly
        from the CLI, bypassing the Agent SDK's message-level buffering.
        """
        # Build environment: unset CLAUDECODE to avoid nested-session error
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        cmd = [
            "claude", "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--no-session-persistence",
            "--system-prompt", system,
            "--model", model,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Write prompt to stdin and close.  drain() flushes the write buffer
        # so large prompts (>64 KB pipe buffer) don't silently truncate.
        assert proc.stdin is not None
        proc.stdin.write(user.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        # Parse streaming JSON events from stdout.  The try/finally ensures
        # the subprocess is always cleaned up — even when the optimizer's
        # timeout cancels the consuming task mid-stream.
        assert proc.stdout is not None
        try:
            async for line_bytes in proc.stdout:
                line = line_bytes.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    event = _json.loads(line)
                except _json.JSONDecodeError:
                    continue

                # Only yield text content deltas; skip thinking, signatures, etc.
                if event.get("type") != "stream_event":
                    continue
                inner = event.get("event", {})
                if inner.get("type") != "content_block_delta":
                    continue
                delta = inner.get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield delta["text"]
        finally:
            # Kill the subprocess if it's still running (e.g. generator cancelled)
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass  # Already exited between our check and kill
            await proc.wait()
            if proc.returncode and proc.returncode != 0:
                stderr_output = ""
                if proc.stderr:
                    stderr_bytes = await proc.stderr.read()
                    stderr_output = stderr_bytes.decode("utf-8", errors="replace")[:500]
                logger.warning(
                    "claude CLI stream exited with code %d: %s",
                    proc.returncode, stderr_output,
                )

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
