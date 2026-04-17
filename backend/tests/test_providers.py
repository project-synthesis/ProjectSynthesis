"""Tests for the provider layer (base, API, CLI, detector)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.pipeline_contracts import AnalysisResult

# ---------------------------------------------------------------------------
# Base provider — thinking_config
# ---------------------------------------------------------------------------


class TestThinkingConfig:
    def test_opus_returns_adaptive(self):
        from app.providers.base import LLMProvider

        assert LLMProvider.thinking_config("claude-opus-4-7") == {"type": "adaptive"}

    def test_sonnet_returns_adaptive(self):
        from app.providers.base import LLMProvider

        assert LLMProvider.thinking_config("claude-sonnet-4-6") == {"type": "adaptive"}

    def test_haiku_returns_disabled(self):
        from app.providers.base import LLMProvider

        assert LLMProvider.thinking_config("claude-haiku-4-5") == {"type": "disabled"}


# ---------------------------------------------------------------------------
# AnthropicAPIProvider
# ---------------------------------------------------------------------------


def _make_analysis_result() -> AnalysisResult:
    return AnalysisResult(
        task_type="coding",
        weaknesses=["vague scope"],
        strengths=["clear goal"],
        selected_strategy="chain-of-thought",
        strategy_rationale="Requires step-by-step reasoning.",
        confidence=0.85,
    )


class TestAnthropicAPIProvider:
    def _make_provider(self, mock_client: MagicMock):
        """Create an AnthropicAPIProvider with a pre-injected mock client."""
        from app.providers.anthropic_api import AnthropicAPIProvider

        provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
        provider._client = mock_client
        return provider

    def test_disables_sdk_builtin_retries(self):
        """AsyncAnthropic client is created with max_retries=0 to prevent double retry."""
        with patch("app.providers.anthropic_api.AsyncAnthropic") as mock_cls:
            from app.providers.anthropic_api import AnthropicAPIProvider

            # Without API key
            AnthropicAPIProvider()
            mock_cls.assert_called_with(max_retries=0)

            mock_cls.reset_mock()

            # With API key
            AnthropicAPIProvider(api_key="sk-test")
            mock_cls.assert_called_with(api_key="sk-test", max_retries=0)

    def _make_mock_response(self, parsed_output: AnalysisResult) -> MagicMock:
        """Create a mock ParsedMessage with given parsed_output."""
        mock_resp = MagicMock()
        mock_resp.parsed_output = parsed_output
        mock_resp.usage = MagicMock(input_tokens=100, output_tokens=50)
        return mock_resp

    @pytest.mark.asyncio
    async def test_calls_messages_parse_with_correct_args(self):
        """complete_parsed calls client.messages.parse with the expected arguments."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        result = await provider.complete_parsed(
            model="claude-opus-4-7",
            system_prompt="You are an analyzer.",
            user_message="Analyze this prompt.",
            output_format=AnalysisResult,
            max_tokens=1024,
        )

        assert result is analysis
        mock_messages.parse.assert_called_once()
        call_kwargs = mock_messages.parse.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-7"
        assert call_kwargs["max_tokens"] == 1024
        assert call_kwargs["output_format"] is AnalysisResult
        assert call_kwargs["messages"] == [{"role": "user", "content": "Analyze this prompt."}]

    @pytest.mark.asyncio
    async def test_sets_cache_control_on_system_prompt(self):
        """System prompt is passed as a list with cache_control: ephemeral."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-sonnet-4-6",
            system_prompt="You are an analyzer.",
            user_message="Analyze this.",
            output_format=AnalysisResult,
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        system_arg = call_kwargs["system"]
        # Must be a list containing a dict with cache_control
        assert isinstance(system_arg, list)
        assert len(system_arg) == 1
        block = system_arg[0]
        assert block["type"] == "text"
        assert block["text"] == "You are an analyzer."
        assert block["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_streaming_calls_messages_stream(self):
        """complete_parsed_streaming uses messages.stream() instead of parse()."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        # Build an async context manager mock for messages.stream()
        mock_stream = AsyncMock()
        mock_stream.get_final_message = AsyncMock(return_value=mock_resp)
        mock_stream_cm = MagicMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=False)

        mock_messages = MagicMock()
        mock_messages.stream = MagicMock(return_value=mock_stream_cm)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        result = await provider.complete_parsed_streaming(
            model="claude-opus-4-7",
            system_prompt="You are an optimizer.",
            user_message="Optimize this prompt.",
            output_format=AnalysisResult,
            max_tokens=131072,
        )

        assert result is analysis
        mock_messages.stream.assert_called_once()
        call_kwargs = mock_messages.stream.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-7"
        assert call_kwargs["max_tokens"] == 131072
        assert call_kwargs["output_format"] is AnalysisResult

    @pytest.mark.asyncio
    async def test_does_not_pass_effort_for_haiku(self):
        """Effort is NOT included in output_config for Haiku models."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-haiku-4-5",
            system_prompt="You are an analyzer.",
            user_message="Analyze this.",
            output_format=AnalysisResult,
            effort="high",
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        # output_config should not be present, or should not contain "effort"
        output_config = call_kwargs.get("output_config")
        if output_config is not None:
            assert "effort" not in output_config


# ---------------------------------------------------------------------------
# call_provider_with_retry — streaming dispatch
# ---------------------------------------------------------------------------


class TestCallProviderWithRetryStreaming:
    @pytest.mark.asyncio
    async def test_dispatches_to_streaming_method(self):
        """When streaming=True, call_provider_with_retry uses complete_parsed_streaming."""
        from app.providers.base import LLMProvider, call_provider_with_retry

        analysis = _make_analysis_result()
        provider = MagicMock(spec=LLMProvider)
        provider.complete_parsed = AsyncMock(return_value=analysis)
        provider.complete_parsed_streaming = AsyncMock(return_value=analysis)

        result = await call_provider_with_retry(
            provider,
            model="claude-opus-4-7",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
            streaming=True,
        )

        assert result is analysis
        provider.complete_parsed_streaming.assert_called_once()
        provider.complete_parsed.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_to_non_streaming_by_default(self):
        """When streaming=False (default), uses complete_parsed."""
        from app.providers.base import LLMProvider, call_provider_with_retry

        analysis = _make_analysis_result()
        provider = MagicMock(spec=LLMProvider)
        provider.complete_parsed = AsyncMock(return_value=analysis)
        provider.complete_parsed_streaming = AsyncMock(return_value=analysis)

        result = await call_provider_with_retry(
            provider,
            model="claude-opus-4-7",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
        )

        assert result is analysis
        provider.complete_parsed.assert_called_once()
        provider.complete_parsed_streaming.assert_not_called()


# ---------------------------------------------------------------------------
# ClaudeCLIProvider
# ---------------------------------------------------------------------------


class TestClaudeCLIProvider:
    @pytest.mark.asyncio
    async def test_parses_structured_output(self):
        """Prefers the structured_output field from --json-schema response."""
        import json

        analysis = _make_analysis_result()
        envelope = {
            "type": "result",
            "subtype": "success",
            "result": "",
            "structured_output": analysis.model_dump(),
            "duration_ms": 1234,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        stdout_json = json.dumps(envelope).encode()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(stdout_json, b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc

            from app.providers.claude_cli import ClaudeCLIProvider

            provider = ClaudeCLIProvider()
            result = await provider.complete_parsed(
                model="claude-haiku-4-5",
                system_prompt="You are an analyzer.",
                user_message="Analyze this prompt.",
                output_format=AnalysisResult,
            )

        assert isinstance(result, AnalysisResult)
        assert result.task_type == "coding"
        assert result.confidence == 0.85

        # Verify --json-schema and --output-format json are in the command
        call_args = mock_exec.call_args[0]
        assert "--json-schema" in call_args
        assert "--output-format" in call_args

        # Verify token usage was tracked
        assert provider.last_usage is not None
        assert provider.last_usage.input_tokens == 100
        assert provider.last_usage.output_tokens == 50

    @pytest.mark.asyncio
    async def test_falls_back_to_result_field(self):
        """Falls back to parsing the result field for older CLI versions."""
        import json

        analysis = _make_analysis_result()
        envelope = {
            "type": "result",
            "result": json.dumps(analysis.model_dump()),
            "duration_ms": 500,
            "usage": {},
        }
        stdout_json = json.dumps(envelope).encode()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(stdout_json, b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc

            from app.providers.claude_cli import ClaudeCLIProvider

            provider = ClaudeCLIProvider()
            result = await provider.complete_parsed(
                model="claude-haiku-4-5",
                system_prompt="You are an analyzer.",
                user_message="Analyze this.",
                output_format=AnalysisResult,
            )

        assert isinstance(result, AnalysisResult)
        assert result.task_type == "coding"

    @pytest.mark.asyncio
    async def test_passes_effort_flag(self):
        """Passes --effort flag when effort parameter is provided (non-Haiku)."""
        import json

        analysis = _make_analysis_result()
        envelope = {
            "type": "result",
            "structured_output": analysis.model_dump(),
            "usage": {},
        }
        stdout_json = json.dumps(envelope).encode()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(stdout_json, b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc

            from app.providers.claude_cli import ClaudeCLIProvider

            provider = ClaudeCLIProvider()
            await provider.complete_parsed(
                model="claude-sonnet-4-6",
                system_prompt="You are an analyzer.",
                user_message="Analyze this.",
                output_format=AnalysisResult,
                effort="high",
            )

        call_args = mock_exec.call_args[0]
        assert "--effort" in call_args
        effort_idx = list(call_args).index("--effort")
        assert call_args[effort_idx + 1] == "high"

    @pytest.mark.asyncio
    async def test_skips_effort_flag_for_haiku(self):
        """Haiku doesn't support effort — flag must be omitted."""
        import json

        analysis = _make_analysis_result()
        envelope = {
            "type": "result",
            "structured_output": analysis.model_dump(),
            "usage": {},
        }
        stdout_json = json.dumps(envelope).encode()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(stdout_json, b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc

            from app.providers.claude_cli import ClaudeCLIProvider

            provider = ClaudeCLIProvider()
            await provider.complete_parsed(
                model="claude-haiku-4-5",
                system_prompt="You are an analyzer.",
                user_message="Analyze this.",
                output_format=AnalysisResult,
                effort="high",
            )

        call_args = mock_exec.call_args[0]
        assert "--effort" not in call_args

    @pytest.mark.asyncio
    async def test_raises_provider_error_on_subprocess_failure(self):
        """Raises ProviderError when subprocess exits with non-zero code."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"something went wrong"))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc

            from app.providers.base import ProviderError
            from app.providers.claude_cli import ClaudeCLIProvider

            provider = ClaudeCLIProvider()
            with pytest.raises(ProviderError):
                await provider.complete_parsed(
                    model="claude-haiku-4-5",
                    system_prompt="You are an analyzer.",
                    user_message="Analyze this.",
                    output_format=AnalysisResult,
                )


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class TestDetector:
    def test_returns_cli_provider_when_claude_on_path(self):
        """detect_provider returns ClaudeCLIProvider when claude is on PATH."""
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            from app.providers import detector as det_module

            provider = det_module.detect_provider()

        from app.providers.claude_cli import ClaudeCLIProvider

        assert isinstance(provider, ClaudeCLIProvider)

    def test_returns_api_provider_when_only_api_key_set(self):
        """detect_provider returns AnthropicAPIProvider when API key is set and no CLI."""
        mock_settings = MagicMock()
        mock_settings.ANTHROPIC_API_KEY = "sk-ant-test-key"

        with patch("shutil.which", return_value=None):
            with patch("app.providers.detector.settings", mock_settings):
                from app.providers import detector as det_module

                provider = det_module.detect_provider()

        from app.providers.anthropic_api import AnthropicAPIProvider

        assert isinstance(provider, AnthropicAPIProvider)

    def test_returns_none_when_nothing_available(self):
        """detect_provider returns None when neither CLI nor API key is available."""
        mock_settings = MagicMock()
        mock_settings.ANTHROPIC_API_KEY = ""

        with patch("shutil.which", return_value=None):
            with patch("app.providers.detector.settings", mock_settings):
                from app.providers import detector as det_module

                provider = det_module.detect_provider()

        assert provider is None
