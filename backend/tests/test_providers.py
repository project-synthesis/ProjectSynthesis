"""Tests for LLM provider implementations (P2-P8, T1-T11)."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# P-parse — parse_json_robust() three-strategy fallback
# ---------------------------------------------------------------------------

def test_parse_json_robust_direct_parse():
    from app.providers.base import parse_json_robust
    assert parse_json_robust('{"a": 1}') == {"a": 1}


def test_parse_json_robust_code_block():
    from app.providers.base import parse_json_robust
    text = 'Sure!\n```json\n{"b": 2}\n```\nDone.'
    assert parse_json_robust(text) == {"b": 2}


def test_parse_json_robust_regex_extract():
    from app.providers.base import parse_json_robust
    text = 'Here is the result: {"c": 3} — all done.'
    assert parse_json_robust(text) == {"c": 3}


# ---------------------------------------------------------------------------
# P-thinking — _make_extra() adaptive thinking behavior
# ---------------------------------------------------------------------------

def test_anthropic_thinking_enabled_for_opus():
    from app.providers.anthropic_api import AnthropicAPIProvider
    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    max_tok, extra = provider._make_extra("claude-opus-4-6")
    assert "thinking" in extra
    assert extra["thinking"]["type"] == "adaptive"
    assert max_tok == 16000


def test_anthropic_thinking_disabled_for_haiku():
    from app.providers.anthropic_api import AnthropicAPIProvider
    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    max_tok, extra = provider._make_extra("claude-haiku-4-5")
    assert "thinking" not in extra
    assert max_tok == 8192


def test_anthropic_thinking_disabled_warns_when_schema_provided(caplog):
    import logging

    from app.providers.anthropic_api import AnthropicAPIProvider
    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    with caplog.at_level(logging.WARNING, logger="app.providers.anthropic_api"):
        max_tok, extra = provider._make_extra("claude-opus-4-6", schema={"type": "object"})
    assert "thinking" not in extra
    assert any("thinking" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# P-agentic — complete_agentic() deeper behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ephemeral_caching_in_complete_agentic():
    """complete_agentic() must pass cache_control={"type":"ephemeral"} to the API."""
    from app.providers.anthropic_api import AnthropicAPIProvider

    end_response = MagicMock()
    end_response.stop_reason = "end_turn"
    end_response.content = [MagicMock(type="text", text="done")]

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.get_final_message = AsyncMock(return_value=end_response)

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = mock_stream

    await provider.complete_agentic("sys", "user", "claude-haiku-4-5", [])

    call_kwargs = provider._client.messages.stream.call_args.kwargs
    assert call_kwargs.get("cache_control") == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_agentic_result_output_from_submit_result():
    """When model calls submit_result tool, AgenticResult.output is populated."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.providers.base import AgenticResult

    submit_block = MagicMock()
    submit_block.type = "tool_use"
    submit_block.name = "submit_result"
    submit_block.id = "tu_submit"
    submit_block.input = {"summary": "found it", "files": ["a.py"]}

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [submit_block]

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.get_final_message = AsyncMock(return_value=response)

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = mock_stream

    output_schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}},
        },
    }
    result = await provider.complete_agentic(
        "sys", "user", "claude-haiku-4-5", [], output_schema=output_schema
    )

    assert isinstance(result, AgenticResult)
    assert result.output == {"summary": "found it", "files": ["a.py"]}


@pytest.mark.asyncio
async def test_on_tool_call_callback_invoked_with_name_and_input():
    """on_tool_call(name, input) is called for each tool invocation."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.providers.base import ToolDefinition

    calls: list[tuple] = []

    def track(name: str, inp: dict) -> None:
        calls.append((name, inp))

    async def noop_handler(args: dict) -> str:
        return "ok"

    tool = ToolDefinition(
        name="my_tool",
        description="test",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        handler=noop_handler,
    )

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "my_tool"
    tool_block.id = "tu_1"
    tool_block.input = {"x": "val"}

    turn1 = MagicMock()
    turn1.stop_reason = "tool_use"
    turn1.content = [tool_block]

    turn2 = MagicMock()
    turn2.stop_reason = "end_turn"
    turn2.content = [MagicMock(type="text", text="done")]

    call_n = 0
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)

    def side(*a, **kw):
        nonlocal call_n
        call_n += 1
        mock_stream.get_final_message = AsyncMock(
            return_value=turn1 if call_n == 1 else turn2
        )
        return mock_stream

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.side_effect = side

    await provider.complete_agentic(
        "sys", "user", "claude-haiku-4-5", [tool], on_tool_call=track
    )

    assert calls == [("my_tool", {"x": "val"})]


@pytest.mark.asyncio
async def test_complete_agentic_stops_at_max_turns():
    """Loop exits with stop_reason='max_turns' when max_turns is exhausted."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.providers.base import AgenticResult, ToolDefinition

    async def noop(args: dict) -> str:
        return "x"

    tool = ToolDefinition(
        name="t", description="t",
        input_schema={"type": "object"}, handler=noop,
    )

    # Every response is tool_use, so loop never exits via end_turn
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "t"
    tool_block.id = "tu_x"
    tool_block.input = {}

    always_tool = MagicMock()
    always_tool.stop_reason = "tool_use"
    always_tool.content = [tool_block]

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.get_final_message = AsyncMock(return_value=always_tool)
    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = mock_stream

    result = await provider.complete_agentic(
        "sys", "user", "claude-haiku-4-5", [tool], max_turns=2
    )

    assert isinstance(result, AgenticResult)
    assert result.stop_reason == "max_turns"
    assert provider._client.messages.stream.call_count == 2


@pytest.mark.asyncio
async def test_complete_returns_empty_string_on_no_content():
    """Both providers return '' when the response has no text blocks."""
    from app.providers.anthropic_api import AnthropicAPIProvider

    # AnthropicAPIProvider: response with only a non-text block (no .text attr)
    non_text_block = MagicMock(spec=[])  # spec=[] means no attributes at all
    response = MagicMock()
    response.content = [non_text_block]

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.get_final_message = AsyncMock(return_value=response)

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = mock_stream

    result = await provider.complete("sys", "user", "claude-haiku-4-5")
    assert result == ""

    # ClaudeCLIProvider: message with no content blocks
    from claude_agent_sdk import AssistantMessage

    from app.providers.claude_cli import ClaudeCLIProvider

    real_msg = MagicMock(spec=AssistantMessage)
    real_msg.content = []  # empty

    async def mock_query(prompt, options):
        yield real_msg

    cli_provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)
    cli_provider._query = mock_query
    cli_result = await cli_provider.complete("sys", "user", "claude-haiku-4-5")
    assert cli_result == ""


# ---------------------------------------------------------------------------
# P2 — AnthropicAPIProvider.complete_json() with schema uses output_config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_json_with_schema_uses_output_config():
    """When schema dict provided, API call must include output_config.format."""
    from app.providers.anthropic_api import AnthropicAPIProvider

    schema = {
        "type": "object",
        "properties": {"task_type": {"type": "string"}},
        "required": ["task_type"],
        "additionalProperties": False,
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text='{"task_type": "coding"}')]

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.get_final_message = AsyncMock(return_value=mock_response)

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = mock_stream

    result = await provider.complete_json("sys", "user", "claude-haiku-4-5", schema=schema)

    assert result == {"task_type": "coding"}
    call_kwargs = provider._client.messages.stream.call_args.kwargs
    assert "output_config" in call_kwargs
    assert call_kwargs["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_complete_json_without_schema_uses_parse_json_robust():
    """When no schema, falls back to parse_json_robust on streamed text."""
    from app.providers.anthropic_api import AnthropicAPIProvider

    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text='{"task_type": "general"}')]
    mock_response.stop_reason = "end_turn"

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.get_final_message = AsyncMock(return_value=mock_response)

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = mock_stream

    result = await provider.complete_json("sys", "user", "claude-haiku-4-5")

    assert result == {"task_type": "general"}
    call_kwargs = provider._client.messages.stream.call_args.kwargs
    # No schema → no json_schema format in output_config (effort may still be present)
    oc = call_kwargs.get("output_config", {})
    assert "format" not in oc


# ---------------------------------------------------------------------------
# P3 — ClaudeCLIProvider.complete_json() with schema injects schema instruction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cli_complete_json_with_schema_injects_schema_instruction():
    """ClaudeCLIProvider.complete_json(schema=...) injects the schema into
    the system prompt (CLI lacks native output_config.format support) and
    parses with parse_json_robust."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    from app.providers.claude_cli import ClaudeCLIProvider

    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "additionalProperties": False,
    }

    captured_system_prompts: list[str] = []

    real_msg = MagicMock(spec=AssistantMessage)
    real_block = MagicMock(spec=TextBlock)
    real_block.text = '{"x": "hello"}'
    real_msg.content = [real_block]

    async def mock_query(prompt, options):
        captured_system_prompts.append(options.system_prompt)
        yield real_msg

    provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)
    provider._query = mock_query

    result = await provider.complete_json("sys", "user", "claude-haiku-4-5", schema=schema)
    assert result == {"x": "hello"}
    # Verify schema was injected into the system prompt
    assert len(captured_system_prompts) == 1
    assert "JSON schema" in captured_system_prompts[0]
    assert '"additionalProperties": false' in captured_system_prompts[0]


# ---------------------------------------------------------------------------
# P4 — complete_agentic() tool handler error isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_agentic_tool_error_returns_error_to_model():
    """A tool handler exception must produce a tool_result error, not crash the loop."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.providers.base import AgenticResult, ToolDefinition

    async def failing_handler(args: dict) -> str:
        raise RuntimeError("disk full")

    tool = ToolDefinition(
        name="read_file",
        description="read",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        handler=failing_handler,
    )

    # Turn 1: model calls tool → handler raises
    # Turn 2: model receives error result → returns end_turn
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "read_file"
    tool_block.id = "tu_1"
    tool_block.input = {"path": "/etc/foo"}

    turn1_response = MagicMock()
    turn1_response.stop_reason = "tool_use"
    turn1_response.content = [tool_block]

    turn2_response = MagicMock()
    turn2_response.stop_reason = "end_turn"
    turn2_response.content = [MagicMock(type="text", text="Could not read file.")]

    call_count = 0
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.get_final_message = AsyncMock(return_value=turn1_response)

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_stream.get_final_message = AsyncMock(
            return_value=turn1_response if call_count == 1 else turn2_response
        )
        return mock_stream

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.side_effect = side_effect

    result = await provider.complete_agentic("sys", "user", "claude-haiku-4-5", [tool])

    assert isinstance(result, AgenticResult)
    assert result.text == "Could not read file."
    # Verify tool_result with is_error was sent back on turn 2.
    # messages is mutated in place; by the time we assert, it contains:
    #   [user, assistant(turn1), user(tool_result), assistant(turn2)]
    # So the tool_result content is at [-2] (second to last entry).
    turn2_messages = provider._client.messages.stream.call_args_list[1].kwargs["messages"]
    # Find the tool_result entry (role=user, content is a list with type=tool_result)
    tool_result_entries = [
        m for m in turn2_messages
        if m.get("role") == "user" and isinstance(m.get("content"), list)
        and any(isinstance(r, dict) and r.get("type") == "tool_result" for r in m["content"])
    ]
    assert tool_result_entries, "No tool_result message found in turn 2 messages"
    tool_results = tool_result_entries[0]["content"]
    assert any(r.get("is_error") is True for r in tool_results if isinstance(r, dict))


# ---------------------------------------------------------------------------
# P5 — AgenticResult.stop_reason field
# ---------------------------------------------------------------------------

def test_agentic_result_has_stop_reason():
    from app.providers.base import AgenticResult
    r = AgenticResult(text="hi")
    assert r.stop_reason == "end_turn"  # default


def test_agentic_result_max_turns_stop_reason():
    from app.providers.base import AgenticResult
    r = AgenticResult(text="", stop_reason="max_turns")
    assert r.stop_reason == "max_turns"


# ---------------------------------------------------------------------------
# P6 — parse_json_robust() logs a warning on failure
# ---------------------------------------------------------------------------

def test_parse_json_robust_logs_warning_on_failure(caplog):
    import logging

    from app.providers.base import parse_json_robust
    with caplog.at_level(logging.WARNING, logger="app.providers.base"):
        with pytest.raises(ValueError, match="Could not parse JSON"):
            parse_json_robust("this is definitely not json at all")
    assert any("parse_json_robust" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# P7 — ABC stream() is declared as async
# ---------------------------------------------------------------------------

def test_abc_stream_is_async():
    import inspect

    from app.providers.base import LLMProvider
    assert "stream" in LLMProvider.__abstractmethods__
    # Verify implementations are async generators (not sync)
    from app.providers.anthropic_api import AnthropicAPIProvider
    assert inspect.isasyncgenfunction(AnthropicAPIProvider.stream)


# ---------------------------------------------------------------------------
# P8 — ClaudeCLIProvider.stream() subprocess token-level streaming
# ---------------------------------------------------------------------------

def _make_stream_proc(lines: list[bytes], returncode: int = 0, stderr_text: str = ""):
    """Helper: build a mock subprocess for ClaudeCLIProvider.stream() tests."""
    mock_proc = AsyncMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.stdin.close = MagicMock()
    mock_proc.returncode = returncode
    mock_proc.wait = AsyncMock(return_value=returncode)
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=stderr_text.encode())
    mock_proc.kill = MagicMock()

    async def _aiter_lines():
        for line in lines:
            yield line

    mock_proc.stdout = _aiter_lines()
    return mock_proc


@pytest.mark.asyncio
async def test_cli_stream_yields_text_deltas_from_subprocess():
    """stream() should parse text_delta events and skip non-text events."""
    from app.providers.claude_cli import ClaudeCLIProvider

    _delta = '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":'
    lines = [
        b'{"type":"stream_event","event":{"type":"content_block_start","index":0}}\n',
        (_delta + '{"type":"text_delta","text":"Hello "}}}\n').encode(),
        (_delta + '{"type":"text_delta","text":"world"}}}\n').encode(),
        (_delta + '{"type":"thinking_delta","thinking":"internal"}}}\n').encode(),
        (_delta + '{"type":"signature_delta","signature":"sig"}}}\n').encode(),
        b'not valid json\n',
        b'\n',
        b'{"type":"result","result":"done"}\n',
    ]
    mock_proc = _make_stream_proc(lines)
    provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        chunks = []
        async for chunk in provider.stream("sys", "user", "claude-haiku-4-5"):
            chunks.append(chunk)

    assert chunks == ["Hello ", "world"]
    mock_proc.stdin.drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_cli_stream_logs_warning_on_nonzero_exit(caplog):
    """Non-zero exit code should be logged as warning, not raised."""
    from app.providers.claude_cli import ClaudeCLIProvider

    lines = [
        b'{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"partial"}}}\n',
    ]
    mock_proc = _make_stream_proc(lines, returncode=1, stderr_text="some error")
    provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        chunks = []
        with caplog.at_level(logging.WARNING, logger="app.providers.claude_cli"):
            async for chunk in provider.stream("sys", "user", "claude-haiku-4-5"):
                chunks.append(chunk)

    # Partial output is still yielded
    assert chunks == ["partial"]
    assert "exited with code 1" in caplog.text
    assert "some error" in caplog.text


@pytest.mark.asyncio
async def test_cli_stream_kills_subprocess_on_cancellation():
    """Subprocess must be killed when the generator is cancelled mid-stream."""
    import asyncio

    from app.providers.claude_cli import ClaudeCLIProvider

    # Use an event to block the generator mid-stream so we can cancel it
    stall = asyncio.Event()

    async def _stalling_lines():
        _d = '{"type":"stream_event","event":{"type":"content_block_delta"'
        yield (_d + ',"index":0,"delta":{"type":"text_delta","text":"tok1"}}}\n').encode()
        await stall.wait()  # Block here until cancelled

    mock_proc = AsyncMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.stdin.close = MagicMock()
    mock_proc.returncode = None  # Still running
    mock_proc.wait = AsyncMock(return_value=-9)
    mock_proc.kill = MagicMock()
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"")
    mock_proc.stdout = _stalling_lines()

    provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)

    async def _consume():
        async for _ in provider.stream("sys", "user", "claude-haiku-4-5"):
            pass

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        task = asyncio.create_task(_consume())
        await asyncio.sleep(0.01)  # Let the task start and yield first token
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    mock_proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# P9 — ClaudeCLIProvider.complete_agentic() must not set output_format
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cli_complete_agentic_does_not_pass_output_format_to_options():
    """ClaudeCLIProvider.complete_agentic must not set output_format in
    ClaudeAgentOptions even when output_schema is provided.

    output_format instructs the SDK to produce structured output directly,
    which can cause the model to skip tool-calling rounds and return data
    from training knowledge rather than actual repository reads.
    submit_result MCP tool is the canonical structured-output mechanism
    and is already enforced by the explore system prompt.
    """
    from app.providers.claude_cli import ClaudeCLIProvider

    captured_kwargs: dict = {}

    class MockClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    async def empty_query(*args, **kwargs):
        # Empty async generator — simulates no messages from the model
        if False:
            yield  # pragma: no cover

    output_schema = {
        "type": "object",
        "properties": {"tech_stack": {"type": "array", "items": {"type": "string"}}},
        "required": ["tech_stack"],
    }

    provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)
    provider._query = empty_query

    with patch("claude_agent_sdk.ClaudeAgentOptions", MockClaudeAgentOptions), \
         patch("claude_agent_sdk.create_sdk_mcp_server", return_value=MagicMock()), \
         patch("claude_agent_sdk.tool", side_effect=lambda name, desc, schema: (lambda f: f)):
        await provider.complete_agentic(
            "sys", "user", "claude-haiku-4-5", [], output_schema=output_schema
        )

    assert "output_format" not in captured_kwargs, (
        "ClaudeCLIProvider must not pass output_format to ClaudeAgentOptions — "
        "it can cause the model to skip exploration tool calls. "
        "Rely on submit_result MCP tool for structured output instead."
    )


# ---------------------------------------------------------------------------
# T-submit-1: submit_result must fire on_tool_call (UI activity feed visibility)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_tool_fires_on_tool_call():
    """_submit_tool must call on_tool_call when invoked.

    When the model submits its final structured result via submit_result, the
    on_tool_call callback must fire so the UI activity feed shows the submission
    event. Without this, submit_result is invisible to the frontend even when
    the model correctly calls it — the user sees no "submit result" entry in
    the explore activity log.
    """
    from app.providers.claude_cli import ClaudeCLIProvider

    on_tool_call_calls: list[tuple[str, dict]] = []
    captured_submit_fn: dict = {}

    def on_tool_call(name: str, args: dict) -> None:
        on_tool_call_calls.append((name, args))

    def fake_tool(name, desc, schema):
        def decorator(f):
            if name == "submit_result":
                captured_submit_fn["fn"] = f
            return f
        return decorator

    async def empty_query(*args, **kwargs):
        if False:
            yield  # pragma: no cover

    output_schema = {
        "type": "object",
        "properties": {"tech_stack": {"type": "array", "items": {"type": "string"}}},
        "required": ["tech_stack"],
        "additionalProperties": False,
    }

    provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)
    provider._query = empty_query

    with patch("claude_agent_sdk.ClaudeAgentOptions", MagicMock()), \
         patch("claude_agent_sdk.create_sdk_mcp_server", return_value=MagicMock()), \
         patch("claude_agent_sdk.tool", side_effect=fake_tool):
        await provider.complete_agentic(
            "sys", "user", "claude-haiku-4-5", [],
            on_tool_call=on_tool_call,
            output_schema=output_schema,
        )

    assert "fn" in captured_submit_fn, (
        "submit_result tool function was not registered via the @tool decorator. "
        "Check that output_schema path in complete_agentic creates the _submit_tool."
    )

    # Directly invoke the submit_result handler (simulates model calling the tool)
    submit_args = {"tech_stack": ["Python", "FastAPI"]}
    await captured_submit_fn["fn"](submit_args)

    # The on_tool_call callback must have fired with "submit_result"
    fired_names = [name for name, _ in on_tool_call_calls]
    assert "submit_result" in fired_names, (
        f"on_tool_call was not called with 'submit_result'. Calls seen: {fired_names}. "
        "_submit_tool must call on_tool_call('submit_result', args) so the UI "
        "activity feed shows when the model submits its structured result."
    )


# ---------------------------------------------------------------------------
# T-model-1: explore stage must use claude-haiku-4-5
# ---------------------------------------------------------------------------

def test_explore_model_routing_uses_haiku():
    """MODEL_ROUTING['explore'] must be claude-haiku-4-5."""
    from app.providers.base import MODEL_ROUTING

    explore_model = MODEL_ROUTING["explore"]
    assert explore_model == "claude-haiku-4-5", (
        f"MODEL_ROUTING['explore'] is '{explore_model}'. "
        "Expected claude-haiku-4-5."
    )


# ---------------------------------------------------------------------------
# T-tools-1: list_repo_files must not claim "first" priority over get_repo_summary
# ---------------------------------------------------------------------------

def test_list_repo_files_description_has_no_conflicting_priority():
    """list_repo_files must not say 'Call this first' when get_repo_summary exists.

    Both list_repo_files and get_repo_summary previously contained 'Call this
    first' in their descriptions. This ambiguity caused the model to pick
    list_repo_files (first in the list) and never call get_repo_summary —
    depriving the explore stage of the README, package manifests, and entry
    points needed to ground the optimized prompt.

    Only get_repo_summary should claim 'Call this first' priority.
    """
    from app.services.codebase_tools import build_codebase_tools

    # build_codebase_tools() creates ToolDefinitions — handlers are closures
    # that need GitHub access, but the *descriptions* are set at construction
    # time with no network calls. Pass dummy values.
    tools = build_codebase_tools(
        token="fake-token",
        repo_full_name="owner/repo",
        repo_branch="main",
    )

    tool_map = {t.name: t for t in tools}
    assert "list_repo_files" in tool_map, "list_repo_files tool not found"
    assert "get_repo_summary" in tool_map, "get_repo_summary tool not found"

    list_desc = tool_map["list_repo_files"].description
    assert "Call this first" not in list_desc, (
        "list_repo_files description says 'Call this first', conflicting with "
        "get_repo_summary which also says 'Call this first'. This causes the model "
        "to pick list_repo_files and skip get_repo_summary entirely. "
        "Remove 'Call this first' from list_repo_files — only get_repo_summary "
        "should claim first-call priority."
    )


# ---------------------------------------------------------------------------
# T-tools-2: get_repo_summary must be the FIRST tool in build_codebase_tools
# ---------------------------------------------------------------------------

def test_get_repo_summary_is_first_tool():
    """get_repo_summary must be the first ToolDefinition returned by build_codebase_tools.

    Models process tool lists in order and are more likely to call tools listed
    first. With get_repo_summary buried at index 4 (after list_repo_files,
    read_file, read_multiple_files, search_code), the model always picks the
    familiar file-read tools first and skips the orientation step entirely.

    Moving get_repo_summary to index 0 signals to the model that this is the
    intended starting point.
    """
    from app.services.codebase_tools import build_codebase_tools

    tools = build_codebase_tools(
        token="fake-token",
        repo_full_name="owner/repo",
        repo_branch="main",
    )

    assert len(tools) > 0, "build_codebase_tools returned empty list"
    assert tools[0].name == "get_repo_summary", (
        f"First tool is '{tools[0].name}', expected 'get_repo_summary'. "
        "get_repo_summary must be the first tool so the model calls it before "
        "diving into individual file reads. Models preferentially use tools "
        "listed earlier in the allowed_tools list."
    )


# ---------------------------------------------------------------------------
# T-reasoning-1 through T-explore-1: REMOVED (2026-03-10)
#
# These tests validated the old agentic explore loop (build_codebase_tools,
# _describe_tool_call, complete_agentic, submit_result).  The explore phase
# has been replaced with semantic retrieval + single-shot synthesis.
#
# Equivalent coverage now lives in tests/test_explore_phase.py:
#   - TestExploreFlow.test_sse_event_sequence — verifies event ordering
#   - TestExploreFlow.test_cache_hit_returns_immediately — cache path
#   - TestExploreFlow.test_token_resolution_error — error path
#
# The codebase_tools module itself (build_codebase_tools, etc.) is still
# tested above via T-tools-1 and T-tools-2 — those remain valid since the
# module is kept for MCP use.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# H2 — CompletionUsage dataclass and cost computation
# ---------------------------------------------------------------------------

def test_completion_usage_total_tokens():
    from app.providers.base import CompletionUsage
    u = CompletionUsage(input_tokens=100, output_tokens=50)
    assert u.total_tokens == 150


def test_completion_usage_estimated_cost_returns_none_when_estimated():
    from app.providers.base import CompletionUsage
    u = CompletionUsage(input_tokens=1000, output_tokens=500, is_estimated=True, model="claude-opus-4-6")
    assert u.estimated_cost_usd() is None


def test_completion_usage_estimated_cost_computes_for_api():
    from app.providers.base import CompletionUsage
    u = CompletionUsage(input_tokens=1_000_000, output_tokens=100_000, model="claude-sonnet-4-6")
    cost = u.estimated_cost_usd()
    assert cost is not None
    # Sonnet: $3/M input + $15/M output = $3 + $1.5 = $4.5
    assert abs(cost - 4.5) < 0.01


def test_completion_usage_cost_with_cache():
    from app.providers.base import CompletionUsage
    u = CompletionUsage(
        input_tokens=1_000_000, output_tokens=100_000,
        cache_read_input_tokens=500_000, cache_creation_input_tokens=200_000,
        model="claude-opus-4-6",
    )
    cost = u.estimated_cost_usd()
    assert cost is not None
    # Opus: 300K normal input * $15/M + 100K output * $75/M + 500K cache_read * $1.5/M + 200K cache_write * $18.75/M
    # = 4.5 + 7.5 + 0.75 + 3.75 = 16.5
    assert abs(cost - 16.5) < 0.01


def test_completion_usage_iadd():
    from app.providers.base import CompletionUsage
    a = CompletionUsage(input_tokens=100, output_tokens=50, model="claude-opus-4-6")
    b = CompletionUsage(input_tokens=200, output_tokens=100, is_estimated=True)
    a += b
    assert a.input_tokens == 300
    assert a.output_tokens == 150
    assert a.is_estimated is True  # Tainted by estimated usage
    assert a.model == "claude-opus-4-6"


def test_completion_usage_cost_clamps_negative_normal_input():
    """Cache tokens exceeding total input should not produce negative cost."""
    from app.providers.base import CompletionUsage
    u = CompletionUsage(
        input_tokens=100, output_tokens=50,
        cache_read_input_tokens=80, cache_creation_input_tokens=50,
        model="claude-sonnet-4-6",
    )
    cost = u.estimated_cost_usd()
    assert cost is not None
    assert cost >= 0  # Normal input clamped to 0, no negative component


def test_completion_usage_unknown_model_returns_none_cost():
    from app.providers.base import CompletionUsage
    u = CompletionUsage(input_tokens=100, output_tokens=50, model="gpt-4o")
    assert u.estimated_cost_usd() is None


def test_mock_provider_get_last_usage():
    from app.providers.mock import MockProvider
    p = MockProvider()
    usage = p.get_last_usage()
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.is_estimated is True


def test_anthropic_get_last_usage_returns_none_initially():
    from app.providers.anthropic_api import AnthropicAPIProvider
    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    assert provider.get_last_usage() is None


@pytest.mark.asyncio
async def test_anthropic_complete_captures_usage():
    """After complete(), get_last_usage() returns real token counts."""
    from app.providers.anthropic_api import AnthropicAPIProvider

    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="hello")]
    mock_usage = MagicMock()
    mock_usage.input_tokens = 42
    mock_usage.output_tokens = 17
    mock_usage.cache_read_input_tokens = 0
    mock_usage.cache_creation_input_tokens = 0
    mock_response.usage = mock_usage

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.get_final_message = AsyncMock(return_value=mock_response)

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = mock_stream

    await provider.complete("sys", "user", "claude-haiku-4-5")

    usage = provider.get_last_usage()
    assert usage is not None
    assert usage.input_tokens == 42
    assert usage.output_tokens == 17
    assert usage.is_estimated is False


@pytest.mark.asyncio
async def test_cli_complete_captures_estimated_usage():
    """After complete(), ClaudeCLIProvider.get_last_usage() returns estimated counts."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    from app.providers.claude_cli import ClaudeCLIProvider

    real_msg = MagicMock(spec=AssistantMessage)
    real_block = MagicMock(spec=TextBlock)
    real_block.text = "hello world"
    real_msg.content = [real_block]

    async def mock_query(prompt, options):
        yield real_msg

    provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)
    provider._query = mock_query

    await provider.complete("system prompt", "user message", "claude-haiku-4-5")

    usage = provider.get_last_usage()
    assert usage is not None
    assert usage.is_estimated is True
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0


# ---------------------------------------------------------------------------
# H4 — select_model() dynamic model routing
# ---------------------------------------------------------------------------

def test_select_model_default():
    from app.providers.base import MODEL_ROUTING, select_model
    assert select_model("strategy") == MODEL_ROUTING["strategy"]
    assert select_model("optimize") == MODEL_ROUTING["optimize"]


def test_select_model_simple_downgrade():
    from app.providers.base import select_model
    assert select_model("strategy", "simple") == "claude-sonnet-4-6"
    assert select_model("optimize", "simple") == "claude-sonnet-4-6"


def test_select_model_moderate_no_downgrade():
    from app.providers.base import MODEL_ROUTING, select_model
    assert select_model("strategy", "moderate") == MODEL_ROUTING["strategy"]


def test_select_model_user_override_takes_precedence():
    from app.providers.base import select_model
    assert select_model("strategy", "simple", "claude-opus-4-6") == "claude-opus-4-6"


def test_select_model_validate_no_downgrade():
    """Validate stage has no downgrade rules — always uses default."""
    from app.providers.base import MODEL_ROUTING, select_model
    assert select_model("validate", "simple") == MODEL_ROUTING["validate"]


# ---------------------------------------------------------------------------
# H3 — AgenticResult.session_id
# ---------------------------------------------------------------------------

def test_agentic_result_session_id_defaults_to_none():
    from app.providers.base import AgenticResult
    r = AgenticResult(text="hi")
    assert r.session_id is None


def test_agentic_result_session_id_set():
    from app.providers.base import AgenticResult
    r = AgenticResult(text="hi", session_id="sess_123")
    assert r.session_id == "sess_123"


# ---------------------------------------------------------------------------
# H5 — TOOL_CATEGORIES covers all registered tools
# ---------------------------------------------------------------------------

def test_tool_categories_covers_all_tools():
    """TOOL_CATEGORIES must have an entry for every tool registered in the MCP server."""
    from app.mcp_server import TOOL_CATEGORIES
    expected_tools = {
        "synthesis_optimize", "synthesis_retry",
        "synthesis_get_optimization", "synthesis_list_optimizations",
        "synthesis_search_optimizations", "synthesis_get_by_project",
        "synthesis_get_stats", "synthesis_tag_optimization",
        "synthesis_delete_optimization", "synthesis_batch_delete",
        "synthesis_list_trash", "synthesis_restore",
        "synthesis_github_list_repos", "synthesis_github_read_file",
        "synthesis_github_search_code", "synthesis_submit_feedback",
        "synthesis_get_branches", "synthesis_get_adaptation_state",
    }
    assert set(TOOL_CATEGORIES.keys()) == expected_tools


# ---------------------------------------------------------------------------
# H6 — AnthropicAPIProvider accepts betas parameter
# ---------------------------------------------------------------------------

def test_anthropic_provider_accepts_betas():
    """AnthropicAPIProvider.__init__ accepts betas and sets the header."""
    from unittest.mock import patch

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        from app.providers.anthropic_api import AnthropicAPIProvider
        AnthropicAPIProvider(api_key="sk-ant-test", betas=["context-1m-2025-08-07"])
        call_kwargs = mock_cls.call_args.kwargs
        assert "default_headers" in call_kwargs
        assert call_kwargs["default_headers"]["anthropic-beta"] == "context-1m-2025-08-07"


def test_anthropic_provider_no_betas_no_header():
    """When betas is None, no extra headers are set."""
    from unittest.mock import patch

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        from app.providers.anthropic_api import AnthropicAPIProvider
        AnthropicAPIProvider(api_key="sk-ant-test")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs.get("default_headers") is None


# ---------------------------------------------------------------------------
# H2 — PipelineAccumulator usage accumulation
# ---------------------------------------------------------------------------

def test_pipeline_accumulator_usage_accumulation():
    """PipelineAccumulator accumulates usage from stage events."""
    from app.services.optimization_service import PipelineAccumulator
    acc = PipelineAccumulator()

    # Simulate analyze stage complete event with usage
    acc.process_event("stage", {
        "stage": "analyze",
        "status": "complete",
        "duration_ms": 500,
        "token_count": 1000,
        "usage": {
            "input_tokens": 800,
            "output_tokens": 200,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "is_estimated": False,
            "model": "claude-sonnet-4-6",
        },
    })

    assert acc.usage_totals.input_tokens == 800
    assert acc.usage_totals.output_tokens == 200
    assert acc.usage_totals.is_estimated is False

    # Simulate optimize stage with estimated usage
    acc.process_event("stage", {
        "stage": "optimize",
        "status": "complete",
        "duration_ms": 2000,
        "token_count": 3000,
        "usage": {
            "input_tokens": 2000,
            "output_tokens": 1000,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 0,
            "is_estimated": True,
            "model": "claude-opus-4-6",
        },
    })

    assert acc.usage_totals.input_tokens == 2800
    assert acc.usage_totals.output_tokens == 1200
    assert acc.usage_totals.is_estimated is True  # Tainted


def test_pipeline_accumulator_finalize_writes_cost_columns():
    """finalize() populates cost columns when usage is present."""
    import time

    from app.services.optimization_service import PipelineAccumulator
    acc = PipelineAccumulator()

    acc.process_event("stage", {
        "stage": "analyze",
        "status": "complete",
        "duration_ms": 500,
        "token_count": 1000,
        "usage": {
            "input_tokens": 800,
            "output_tokens": 200,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "is_estimated": False,
            "model": "claude-sonnet-4-6",
        },
    })

    updates = acc.finalize("mock", time.time())
    assert "total_input_tokens" in updates
    assert updates["total_input_tokens"] == 800
    assert updates["total_output_tokens"] == 200
    assert updates["estimated_cost_usd"] is not None
    assert updates["usage_is_estimated"] is False


# ---------------------------------------------------------------------------
# H3-fix — PipelineAccumulator retry_history persistence
# ---------------------------------------------------------------------------

def test_pipeline_accumulator_retry_history_accumulation():
    """PipelineAccumulator accumulates retry_diagnostics into retry_history."""
    import time

    from app.services.optimization_service import PipelineAccumulator
    acc = PipelineAccumulator()

    diag1 = {
        "attempt": 1,
        "overall_score": 4.5,
        "threshold": 6.0,
        "action": "retry",
        "reason": "Below threshold",
        "focus_areas": ["clarity"],
        "gate": "threshold",
        "momentum": 0.0,
        "best_attempt_index": 0,
        "best_score": 4.5,
    }
    diag2 = {
        "attempt": 2,
        "overall_score": 7.2,
        "threshold": 6.0,
        "action": "accept",
        "reason": "Score 7.2 >= threshold 6.0",
        "focus_areas": [],
        "gate": "threshold",
        "momentum": 2.7,
        "best_attempt_index": 1,
        "best_score": 7.2,
    }

    acc.process_event("retry_diagnostics", diag1)
    acc.process_event("retry_diagnostics", diag2)

    assert len(acc._retry_diagnostics_log) == 2
    assert acc._retry_diagnostics_log[0]["attempt"] == 1
    assert acc._retry_diagnostics_log[1]["overall_score"] == 7.2

    updates = acc.finalize("mock", time.time())
    assert "retry_history" in updates

    import json
    parsed = json.loads(updates["retry_history"])
    assert len(parsed) == 2
    assert parsed[0]["action"] == "retry"
    assert parsed[1]["action"] == "accept"


def test_pipeline_accumulator_no_retry_history_when_no_diagnostics():
    """PipelineAccumulator omits retry_history when no retry_diagnostics events."""
    import time

    from app.services.optimization_service import PipelineAccumulator
    acc = PipelineAccumulator()

    acc.process_event("analysis", {"task_type": "instruction"})
    updates = acc.finalize("mock", time.time())
    assert "retry_history" not in updates


def test_pipeline_accumulator_retry_best_selected_stored():
    """PipelineAccumulator stores retry_best_selected in results."""
    from app.services.optimization_service import PipelineAccumulator
    acc = PipelineAccumulator()

    best_data = {
        "best_attempt_index": 0,
        "best_score": 7.5,
        "selected_attempt": 1,
        "total_attempts": 2,
        "reason": "Accept best",
    }
    acc.process_event("retry_best_selected", best_data)
    assert acc.results["retry_best_selected"] == best_data


def test_retry_history_entry_validates_diagnostics_shape():
    """RetryHistoryEntry schema validates real get_diagnostics() output."""
    from app.schemas.feedback import RetryHistoryEntry

    # Shape matching RetryOracle.get_diagnostics()
    data = {
        "attempt": 2,
        "overall_score": 7.2,
        "threshold": 6.0,
        "action": "accept",
        "reason": "Score 7.2 >= threshold 6.0",
        "focus_areas": ["specificity"],
        "gate": "threshold",
        "momentum": 2.7,
        "best_attempt_index": 1,
        "best_score": 7.2,
    }
    entry = RetryHistoryEntry.model_validate(data)
    assert entry.attempt == 2
    assert entry.overall_score == 7.2
    assert entry.gate == "threshold"
    assert entry.best_score == 7.2

    # Roundtrip
    dumped = entry.model_dump()
    re_entry = RetryHistoryEntry.model_validate(dumped)
    assert re_entry == entry


# ---------------------------------------------------------------------------
# L1 — complete_agentic() handles pause_turn stop reason
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_agentic_handles_pause_turn():
    """pause_turn should cause the loop to re-send, not terminate."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.providers.base import AgenticResult

    # Turn 1: pause_turn with partial text
    pause_response = MagicMock()
    pause_response.stop_reason = "pause_turn"
    pause_response.content = [MagicMock(type="text", text="thinking...")]

    # Turn 2: end_turn with final text
    end_response = MagicMock()
    end_response.stop_reason = "end_turn"
    end_response.content = [MagicMock(type="text", text="done")]

    call_n = 0
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)

    def side(*a, **kw):
        nonlocal call_n
        call_n += 1
        mock_stream.get_final_message = AsyncMock(
            return_value=pause_response if call_n == 1 else end_response
        )
        return mock_stream

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.side_effect = side

    result = await provider.complete_agentic("sys", "user", "claude-haiku-4-5", [])

    assert isinstance(result, AgenticResult)
    assert result.text == "done"
    assert result.stop_reason == "end_turn"
    # Stream was called twice: once for pause_turn, once for end_turn
    assert provider._client.messages.stream.call_count == 2


# ---------------------------------------------------------------------------
# L2 — _make_extra() effort parameter
# ---------------------------------------------------------------------------

def test_make_extra_effort_opus_high():
    """Opus models default to effort='high'."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    _, extra = provider._make_extra("claude-opus-4-6")
    assert extra.get("output_config", {}).get("effort") == "high"


def test_make_extra_effort_sonnet_medium():
    """Sonnet models default to effort='medium'."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    _, extra = provider._make_extra("claude-sonnet-4-6")
    assert extra.get("output_config", {}).get("effort") == "medium"


def test_make_extra_effort_haiku_low():
    """Haiku models default to effort='low'."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    _, extra = provider._make_extra("claude-haiku-4-5")
    assert extra.get("output_config", {}).get("effort") == "low"


def test_make_extra_effort_with_schema_preserves_both():
    """Schema + Opus → output_config has both format and effort."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    _, extra = provider._make_extra("claude-opus-4-6", schema={"type": "object"})
    oc = extra["output_config"]
    assert "format" in oc
    assert oc["effort"] == "high"


def test_make_extra_effort_explicit_override():
    """Explicit effort='low' on Opus overrides the model-family default."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    _, extra = provider._make_extra("claude-opus-4-6", effort="low")
    assert extra.get("output_config", {}).get("effort") == "low"


# ---------------------------------------------------------------------------
# M2 — Pipeline DB updates include row_version guard
# ---------------------------------------------------------------------------

def test_pipeline_update_includes_row_version_guard():
    """The optimize router's success-path UPDATE must include row_version == 0."""
    import inspect

    from app.routers import optimize as opt_mod
    source = inspect.getsource(opt_mod.optimize_prompt)
    # All three update paths (success, timeout, exception) should have the guard
    assert "Optimization.row_version == 0" in source
    assert "row_version=1" in source


def test_pipeline_update_logs_on_version_conflict():
    """When rowcount==0, the pipeline should log an error for version conflict."""
    # We test the logging pattern by verifying the log message format exists
    # in the module source — the actual DB path requires full integration setup.
    import inspect

    from app.routers import optimize as opt_mod
    source = inspect.getsource(opt_mod.optimize_prompt)
    assert "Pipeline version conflict for opt %s" in source


# ---------------------------------------------------------------------------
# M6 — Compaction support
# ---------------------------------------------------------------------------

def test_compaction_enabled_passes_context_management():
    """When COMPACTION_ENABLED=True, complete_agentic builds context_management kwargs."""
    import inspect

    from app.providers import anthropic_api as api_mod
    source = inspect.getsource(api_mod.AnthropicAPIProvider.complete_agentic)
    assert "context_management" in source
    assert "compact_20260112" in source


def test_compaction_disabled_no_context_management():
    """When COMPACTION_ENABLED=False (default), no context_management kwargs."""
    from app.config import settings
    assert settings.COMPACTION_ENABLED is False  # Default is False


def test_detector_includes_compaction_beta():
    """detector.py includes compaction beta string when enabled."""
    import inspect

    from app.providers import detector as det_mod
    source = inspect.getsource(det_mod._detect_provider_inner)
    assert "COMPACTION_ENABLED" in source
    assert "COMPACTION_BETA_STRING" in source


# ---------------------------------------------------------------------------
# L8 — complete_parsed() provider tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_parsed_calls_sdk_parse():
    """AnthropicAPIProvider.complete_parsed() calls _client.messages.parse() with output_format."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.schemas.pipeline_outputs import AnalyzeOutput

    parsed_output = AnalyzeOutput(task_type="instruction", complexity="simple")
    mock_response = MagicMock()
    mock_response.parsed_output = parsed_output
    mock_response.content = []

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.parse = AsyncMock(return_value=mock_response)

    result = await provider.complete_parsed("sys", "user", "claude-haiku-4-5", AnalyzeOutput)

    provider._client.messages.parse.assert_called_once()
    call_kwargs = provider._client.messages.parse.call_args.kwargs
    assert call_kwargs["output_format"] is AnalyzeOutput
    assert call_kwargs["max_tokens"] == 8192
    assert isinstance(result, AnalyzeOutput)


@pytest.mark.asyncio
async def test_complete_parsed_returns_typed_instance():
    """complete_parsed() returns a Pydantic model instance, not a dict."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.schemas.pipeline_outputs import StrategyOutput

    parsed_output = StrategyOutput(primary_framework="CRISPE", rationale="good fit")
    mock_response = MagicMock()
    mock_response.parsed_output = parsed_output
    mock_response.content = []

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.parse = AsyncMock(return_value=mock_response)

    result = await provider.complete_parsed("sys", "user", "claude-sonnet-4-6", StrategyOutput)

    assert isinstance(result, StrategyOutput)
    assert result.primary_framework == "CRISPE"
    assert not isinstance(result, dict)


@pytest.mark.asyncio
async def test_complete_parsed_no_thinking():
    """complete_parsed() does NOT pass thinking to parse (incompatible with JSON schema)."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.schemas.pipeline_outputs import ValidateOutput

    parsed_output = ValidateOutput(clarity_score=8)
    mock_response = MagicMock()
    mock_response.parsed_output = parsed_output
    mock_response.content = []

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.parse = AsyncMock(return_value=mock_response)

    await provider.complete_parsed("sys", "user", "claude-opus-4-6", ValidateOutput)

    call_kwargs = provider._client.messages.parse.call_args.kwargs
    assert "thinking" not in call_kwargs


@pytest.mark.asyncio
async def test_complete_parsed_effort_by_model():
    """complete_parsed() sets effort via output_config — Haiku low, Sonnet medium, Opus high."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.schemas.pipeline_outputs import AnalyzeOutput

    parsed_output = AnalyzeOutput()
    mock_response = MagicMock()
    mock_response.parsed_output = parsed_output
    mock_response.content = []

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.parse = AsyncMock(return_value=mock_response)

    # Haiku → low
    await provider.complete_parsed("sys", "user", "claude-haiku-4-5", AnalyzeOutput)
    kwargs = provider._client.messages.parse.call_args.kwargs
    assert kwargs.get("output_config", {}).get("effort") == "low"

    # Sonnet → medium
    await provider.complete_parsed("sys", "user", "claude-sonnet-4-6", AnalyzeOutput)
    kwargs = provider._client.messages.parse.call_args.kwargs
    assert kwargs.get("output_config", {}).get("effort") == "medium"

    # Opus → high
    await provider.complete_parsed("sys", "user", "claude-opus-4-6", AnalyzeOutput)
    kwargs = provider._client.messages.parse.call_args.kwargs
    assert kwargs.get("output_config", {}).get("effort") == "high"


@pytest.mark.asyncio
async def test_complete_parsed_fallback_on_none_parsed_output():
    """When parsed_output is None, falls back to parse_json_robust + model_validate."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.schemas.pipeline_outputs import AnalyzeOutput

    text_block = MagicMock()
    text_block.text = '{"task_type": "debugging", "complexity": "complex"}'
    text_block.type = "text"

    mock_response = MagicMock()
    mock_response.parsed_output = None
    mock_response.content = [text_block]

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.parse = AsyncMock(return_value=mock_response)

    result = await provider.complete_parsed("sys", "user", "claude-haiku-4-5", AnalyzeOutput)
    assert isinstance(result, AnalyzeOutput)
    assert result.task_type == "debugging"
    assert result.complexity == "complex"


@pytest.mark.asyncio
async def test_complete_parsed_usage_tracked():
    """complete_parsed() sets _usage_var after the call."""
    from app.providers.anthropic_api import AnthropicAPIProvider, _usage_var
    from app.schemas.pipeline_outputs import AnalyzeOutput

    usage_mock = MagicMock()
    usage_mock.input_tokens = 100
    usage_mock.output_tokens = 50
    usage_mock.cache_read_input_tokens = 0
    usage_mock.cache_creation_input_tokens = 0

    parsed_output = AnalyzeOutput()
    mock_response = MagicMock()
    mock_response.parsed_output = parsed_output
    mock_response.usage = usage_mock
    mock_response.content = []

    provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
    provider._client = MagicMock()
    provider._client.messages.parse = AsyncMock(return_value=mock_response)

    await provider.complete_parsed("sys", "user", "claude-haiku-4-5", AnalyzeOutput)

    usage = _usage_var.get(None)
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50


@pytest.mark.asyncio
async def test_complete_parsed_default_impl():
    """Base class default calls complete_json + model_validate."""
    from app.providers.base import LLMProvider
    from app.schemas.pipeline_outputs import AnalyzeOutput

    # Create a minimal concrete subclass
    class TestProvider(LLMProvider):
        @property
        def name(self): return "test"
        async def complete(self, system, user, model): return ""
        async def stream(self, system, user, model):
            yield ""
        async def complete_json(self, system, user, model, schema=None):
            return {"task_type": "api_design", "complexity": "complex"}
        async def complete_agentic(self, *a, **kw):
            raise NotImplementedError

    provider = TestProvider()
    result = await provider.complete_parsed("sys", "user", "model", AnalyzeOutput)

    assert isinstance(result, AnalyzeOutput)
    assert result.task_type == "api_design"
    assert result.complexity == "complex"


@pytest.mark.asyncio
async def test_mock_complete_parsed_filters_extra_fields():
    """MockProvider.complete_parsed() filters superset dict so extra='forbid' doesn't reject."""
    from app.providers.mock import MockProvider
    from app.schemas.pipeline_outputs import AnalyzeOutput

    provider = MockProvider()
    result = await provider.complete_parsed("sys", "user", "mock", AnalyzeOutput)

    assert isinstance(result, AnalyzeOutput)
    # MockProvider returns fields like "overall_score", "framework" etc.
    # that are NOT in AnalyzeOutput — they should be filtered out, not raise
    assert result.task_type == "instruction"


# ---------------------------------------------------------------------------
# L8 — extract_json_with_fallback() with output_type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_json_with_output_type_validates():
    """When output_type is provided and streaming parse succeeds, Pydantic validates the result."""
    from app.schemas.pipeline_outputs import AnalyzeOutput
    from app.services.stage_runner import extract_json_with_fallback

    provider = MagicMock()

    result = await extract_json_with_fallback(
        provider=provider,
        system_prompt="sys",
        user_message="user",
        model="model",
        timeout_seconds=10,
        stage_name="test",
        full_text='{"task_type": "instruction", "complexity": "simple"}',
        stream_ok=True,
        quality_key="quality",
        quality_value_success="full",
        output_type=AnalyzeOutput,
    )

    assert result["task_type"] == "instruction"
    assert result["complexity"] == "simple"
    assert result["quality"] == "full"
    # Should NOT have called complete_parsed (streaming parse succeeded)
    provider.complete_parsed.assert_not_called()


@pytest.mark.asyncio
async def test_extract_json_output_type_fallback_to_complete_parsed():
    """On Pydantic validation failure, falls through to complete_parsed."""
    from app.schemas.pipeline_outputs import ValidateOutput
    from app.services.stage_runner import extract_json_with_fallback

    # Streamed text has score=0 which violates ge=1 — should fail validation
    invalid_json = '{"clarity_score": 0, "specificity_score": 5}'

    mock_parsed = ValidateOutput(clarity_score=7, specificity_score=8)
    provider = MagicMock()
    provider.complete_parsed = AsyncMock(return_value=mock_parsed)

    result = await extract_json_with_fallback(
        provider=provider,
        system_prompt="sys",
        user_message="user",
        model="model",
        timeout_seconds=10,
        stage_name="test",
        full_text=invalid_json,
        stream_ok=True,
        quality_key="quality",
        quality_value_success="full",
        quality_value_fallback_json="fallback",
        output_type=ValidateOutput,
    )

    # Should have fallen back to complete_parsed
    provider.complete_parsed.assert_called_once()
    assert result["clarity_score"] == 7
    assert result["quality"] == "fallback"


@pytest.mark.asyncio
async def test_extract_json_without_output_type_unchanged():
    """output_type=None preserves existing complete_json behavior."""
    from app.services.stage_runner import extract_json_with_fallback

    provider = MagicMock()
    provider.complete_json = AsyncMock(return_value={"task_type": "general"})

    result = await extract_json_with_fallback(
        provider=provider,
        system_prompt="sys",
        user_message="user",
        model="model",
        timeout_seconds=10,
        stage_name="test",
        full_text="not valid json {",
        stream_ok=True,
        quality_key="quality",
        quality_value_success="full",
        quality_value_fallback_json="fallback",
        output_type=None,
    )

    # Should have fallen back to complete_json (not complete_parsed)
    provider.complete_json.assert_called_once()
    provider.complete_parsed.assert_not_called()
    assert result["quality"] == "fallback"


@pytest.mark.asyncio
async def test_extract_json_complete_parsed_failure_uses_default():
    """When complete_parsed also fails, falls back to default_result."""
    from app.schemas.pipeline_outputs import AnalyzeOutput
    from app.services.stage_runner import extract_json_with_fallback

    provider = MagicMock()
    provider.complete_parsed = AsyncMock(side_effect=Exception("API down"))

    default = {"task_type": "general", "quality": "failed"}

    result = await extract_json_with_fallback(
        provider=provider,
        system_prompt="sys",
        user_message="user",
        model="model",
        timeout_seconds=10,
        stage_name="test",
        full_text="",
        stream_ok=False,
        quality_key="quality",
        quality_value_success="full",
        default_result=default,
        output_type=AnalyzeOutput,
    )

    assert result["task_type"] == "general"
    assert result["quality"] == "failed"
