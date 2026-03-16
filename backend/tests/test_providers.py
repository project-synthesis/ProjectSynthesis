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

        assert LLMProvider.thinking_config("claude-opus-4-6") == {"type": "adaptive"}

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
            model="claude-opus-4-6",
            system_prompt="You are an analyzer.",
            user_message="Analyze this prompt.",
            output_format=AnalysisResult,
            max_tokens=1024,
        )

        assert result is analysis
        mock_messages.parse.assert_called_once()
        call_kwargs = mock_messages.parse.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-6"
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
# ClaudeCLIProvider
# ---------------------------------------------------------------------------


class TestClaudeCLIProvider:
    @pytest.mark.asyncio
    async def test_calls_subprocess_and_parses_json(self):
        """complete_parsed runs the claude CLI and parses stdout as JSON."""
        analysis = _make_analysis_result()
        stdout_json = analysis.model_dump_json().encode()

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
        mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_runtime_error_on_subprocess_failure(self):
        """Raises RuntimeError when subprocess exits with non-zero code."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"something went wrong"))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc

            from app.providers.claude_cli import ClaudeCLIProvider

            provider = ClaudeCLIProvider()
            with pytest.raises(RuntimeError):
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
