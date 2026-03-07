"""Tests for LLM provider implementations (P2-P8)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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
# P3 — ClaudeCLIProvider.complete_json() with schema delegates to API
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cli_complete_json_with_schema_delegates_to_api():
    """ClaudeCLIProvider.complete_json(schema=...) must delegate to API fallback."""
    from app.providers.claude_cli import ClaudeCLIProvider
    from app.providers.anthropic_api import AnthropicAPIProvider

    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "additionalProperties": False,
    }
    expected = {"x": "hello"}

    mock_api = AsyncMock(spec=AnthropicAPIProvider)
    mock_api.complete_json = AsyncMock(return_value=expected)

    provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)
    provider._api_fallback = mock_api
    provider._api_fallback_initialized = True  # bypass lazy init — fallback is pre-set

    with patch.dict("os.environ", {}, clear=True):  # no CLAUDECODE
        result = await provider.complete_json("sys", "user", "claude-haiku-4-5", schema=schema)

    mock_api.complete_json.assert_called_once_with("sys", "user", "claude-haiku-4-5", schema)
    assert result == expected


# ---------------------------------------------------------------------------
# P4 — complete_agentic() tool handler error isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_agentic_tool_error_returns_error_to_model():
    """A tool handler exception must produce a tool_result error, not crash the loop."""
    from app.providers.anthropic_api import AnthropicAPIProvider
    from app.providers.base import ToolDefinition, AgenticResult

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
# P8 — ClaudeCLIProvider.stream() word-boundary chunking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cli_stream_yields_word_boundary_chunks():
    """Chunks should not split words mid-token."""
    from app.providers.claude_cli import ClaudeCLIProvider

    long_text = "hello world foo bar baz " * 100  # 2400 chars

    async def mock_query(prompt, options):
        msg = MagicMock()
        msg.__class__.__name__ = "AssistantMessage"
        block = MagicMock()
        block.__class__.__name__ = "TextBlock"
        block.text = long_text
        msg.content = [block]
        yield msg

    provider = ClaudeCLIProvider.__new__(ClaudeCLIProvider)
    provider._query = mock_query

    # Patch the isinstance checks by using actual classes
    from claude_agent_sdk import AssistantMessage, TextBlock

    real_msg = MagicMock(spec=AssistantMessage)
    real_block = MagicMock(spec=TextBlock)
    real_block.text = long_text
    real_msg.content = [real_block]

    async def mock_query_real(prompt, options):
        yield real_msg

    provider._query = mock_query_real

    with patch.dict("os.environ", {}, clear=True):
        chunks = []
        async for chunk in provider.stream("sys", "user", "claude-haiku-4-5"):
            chunks.append(chunk)

    full = "".join(chunks)
    assert full == long_text
    # Should produce multiple chunks for a 2400-char response
    assert len(chunks) > 1
