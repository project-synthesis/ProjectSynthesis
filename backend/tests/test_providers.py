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
    assert "output_config" not in call_kwargs


# ---------------------------------------------------------------------------
# P3 — ClaudeCLIProvider.complete_json() with schema uses parse_json_robust
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cli_complete_json_with_schema_uses_parse_json_robust():
    """ClaudeCLIProvider.complete_json(schema=...) calls complete() and
    parses with parse_json_robust — no API delegation."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    from app.providers.claude_cli import ClaudeCLIProvider

    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "additionalProperties": False,
    }

    real_msg = MagicMock(spec=AssistantMessage)
    real_block = MagicMock(spec=TextBlock)
    real_block.text = '{"x": "hello"}'
    real_msg.content = [real_block]

    async def mock_query(prompt, options):
        yield real_msg

    provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)
    provider._query = mock_query

    result = await provider.complete_json("sys", "user", "claude-haiku-4-5", schema=schema)
    assert result == {"x": "hello"}


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
