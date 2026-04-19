"""Tests for the provider layer (base, API, CLI, detector)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.pipeline_contracts import AnalysisResult

# ---------------------------------------------------------------------------
# Base provider — thinking_config
# ---------------------------------------------------------------------------


class TestThinkingConfig:
    def test_opus_4_7_returns_adaptive_with_summarized_display(self):
        """Opus 4.7 defaults to display='omitted' (silent). We opt into
        display='summarized' so streaming UIs show reasoning progress."""
        from app.providers.base import LLMProvider

        assert LLMProvider.thinking_config("claude-opus-4-7") == {
            "type": "adaptive",
            "display": "summarized",
        }

    def test_opus_4_6_returns_adaptive_without_display(self):
        """Opus 4.6 shows thinking by default — no display override needed."""
        from app.providers.base import LLMProvider

        assert LLMProvider.thinking_config("claude-opus-4-6") == {"type": "adaptive"}

    def test_sonnet_returns_adaptive(self):
        from app.providers.base import LLMProvider

        assert LLMProvider.thinking_config("claude-sonnet-4-6") == {"type": "adaptive"}

    def test_haiku_returns_disabled(self):
        from app.providers.base import LLMProvider

        assert LLMProvider.thinking_config("claude-haiku-4-5") == {"type": "disabled"}


class TestSupportsXhighEffort:
    def test_opus_4_7_accepts_xhigh(self):
        from app.providers.base import LLMProvider

        assert LLMProvider.supports_xhigh_effort("claude-opus-4-7") is True

    def test_opus_4_6_rejects_xhigh(self):
        from app.providers.base import LLMProvider

        assert LLMProvider.supports_xhigh_effort("claude-opus-4-6") is False

    def test_sonnet_rejects_xhigh(self):
        from app.providers.base import LLMProvider

        assert LLMProvider.supports_xhigh_effort("claude-sonnet-4-6") is False

    def test_haiku_rejects_xhigh(self):
        from app.providers.base import LLMProvider

        assert LLMProvider.supports_xhigh_effort("claude-haiku-4-5") is False


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

    @pytest.mark.asyncio
    async def test_xhigh_downgraded_for_non_opus_4_7(self):
        """xhigh is Opus 4.7 only — downgrade to 'high' on other models
        so the API doesn't 400."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-sonnet-4-6",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
            effort="xhigh",
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        assert call_kwargs["output_config"]["effort"] == "high"

    @pytest.mark.asyncio
    async def test_xhigh_preserved_for_opus_4_7(self):
        """Opus 4.7 receives xhigh verbatim."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-opus-4-7",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
            effort="xhigh",
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        assert call_kwargs["output_config"]["effort"] == "xhigh"

    @pytest.mark.asyncio
    async def test_task_budget_wires_output_config_and_beta_header(self):
        """task_budget on Opus 4.7 sets output_config.task_budget AND
        attaches the task-budgets beta header."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-opus-4-7",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
            task_budget=50_000,
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        assert call_kwargs["output_config"]["task_budget"] == {
            "type": "tokens", "total": 50_000,
        }
        extra_headers = call_kwargs.get("extra_headers", {})
        assert "task-budgets-2026-03-13" in extra_headers.get("anthropic-beta", "")

    @pytest.mark.asyncio
    async def test_task_budget_clamped_to_20k_minimum(self):
        """Values below the 20k SDK minimum are clamped up — prevents 400s."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-opus-4-7",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
            task_budget=5_000,
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        assert call_kwargs["output_config"]["task_budget"]["total"] == 20_000

    @pytest.mark.asyncio
    async def test_task_budget_ignored_on_non_opus_4_7(self):
        """task_budget is Opus 4.7 only — silently dropped on other models."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-sonnet-4-6",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
            task_budget=50_000,
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        output_config = call_kwargs.get("output_config") or {}
        assert "task_budget" not in output_config
        # No beta header attached either
        extra_headers = call_kwargs.get("extra_headers", {}) or {}
        assert "task-budgets" not in extra_headers.get("anthropic-beta", "")

    @pytest.mark.asyncio
    async def test_compaction_wires_context_management_and_beta_header(self):
        """compaction=True sets context_management.edits AND attaches the
        compact beta header on Opus 4.7."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-opus-4-7",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
            compaction=True,
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        assert call_kwargs["context_management"] == {
            "edits": [{"type": "compact_20260112"}],
        }
        extra_headers = call_kwargs.get("extra_headers", {})
        assert "compact-2026-01-12" in extra_headers.get("anthropic-beta", "")

    @pytest.mark.asyncio
    async def test_compaction_allowed_on_sonnet_4_6(self):
        """Sonnet 4.6 is compaction-capable — keep the wiring."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-sonnet-4-6",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
            compaction=True,
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        assert "context_management" in call_kwargs

    @pytest.mark.asyncio
    async def test_compaction_ignored_on_haiku(self):
        """Haiku doesn't support compaction — silently dropped."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-haiku-4-5",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
            compaction=True,
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        assert "context_management" not in call_kwargs

    @pytest.mark.asyncio
    async def test_both_betas_share_single_beta_header(self):
        """When both task_budget and compaction are enabled, both beta IDs
        flow through in a single comma-joined anthropic-beta header."""
        analysis = _make_analysis_result()
        mock_resp = self._make_mock_response(analysis)

        mock_messages = MagicMock()
        mock_messages.parse = AsyncMock(return_value=mock_resp)
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        provider = self._make_provider(mock_client)
        await provider.complete_parsed(
            model="claude-opus-4-7",
            system_prompt="sys",
            user_message="msg",
            output_format=AnalysisResult,
            task_budget=30_000,
            compaction=True,
        )

        call_kwargs = mock_messages.parse.call_args.kwargs
        beta = call_kwargs["extra_headers"]["anthropic-beta"]
        assert "task-budgets-2026-03-13" in beta
        assert "compact-2026-01-12" in beta


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
    async def test_xhigh_preserved_for_opus_4_7_cli(self):
        """Opus 4.7 CLI receives xhigh via --effort flag."""
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
                model="claude-opus-4-7",
                system_prompt="sys",
                user_message="msg",
                output_format=AnalysisResult,
                effort="xhigh",
            )

        call_args = mock_exec.call_args[0]
        effort_idx = list(call_args).index("--effort")
        assert call_args[effort_idx + 1] == "xhigh"

    @pytest.mark.asyncio
    async def test_xhigh_downgraded_for_non_opus_4_7_cli(self):
        """xhigh on non-Opus-4.7 CLI downgrades to 'high' to avoid 400."""
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
                system_prompt="sys",
                user_message="msg",
                output_format=AnalysisResult,
                effort="xhigh",
            )

        call_args = mock_exec.call_args[0]
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

    @pytest.mark.asyncio
    async def test_extracts_error_from_stdout_when_stderr_empty(self):
        """When CLI exits with code 1 and empty stderr, the error message is
        surfaced from the JSON envelope in stdout (api_error_status + result).

        Reproduces the "Explore returned empty result" UX bug where the real
        cause ("Prompt is too long", HTTP 400) was lost because we only read
        stderr. Claude CLI returns the error body on stdout as a JSON envelope
        even on non-zero exit.
        """
        import json

        error_envelope = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 400,
            "result": "Prompt is too long",
            "duration_ms": 616,
            "terminal_reason": "prompt_too_long",
        }
        stdout_json = json.dumps(error_envelope).encode()

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(stdout_json, b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc

            from app.providers.base import ProviderError
            from app.providers.claude_cli import ClaudeCLIProvider

            provider = ClaudeCLIProvider()
            with pytest.raises(ProviderError) as excinfo:
                await provider.complete_parsed(
                    model="claude-haiku-4-5",
                    system_prompt="You are an analyzer.",
                    user_message="Analyze this.",
                    output_format=AnalysisResult,
                )

        # The actual error message must surface — not be swallowed.
        assert "Prompt is too long" in str(excinfo.value)
        assert "400" in str(excinfo.value)
        # "prompt_too_long" is not retryable — retrying the same oversized
        # payload will fail identically.
        assert excinfo.value.retryable is False

    @pytest.mark.asyncio
    async def test_extracts_rate_limit_from_stdout_as_retryable(self):
        """Rate-limit errors in stdout envelope must be tagged retryable=True
        so call_provider_with_retry can back off and retry.
        """
        import json

        rate_limit_envelope = {
            "type": "result",
            "is_error": True,
            "api_error_status": 429,
            "result": "Rate limit exceeded",
            "terminal_reason": "rate_limit",
        }
        stdout_json = json.dumps(rate_limit_envelope).encode()

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(stdout_json, b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc

            from app.providers.base import ProviderError
            from app.providers.claude_cli import ClaudeCLIProvider

            provider = ClaudeCLIProvider()
            with pytest.raises(ProviderError) as excinfo:
                await provider.complete_parsed(
                    model="claude-haiku-4-5",
                    system_prompt="You are an analyzer.",
                    user_message="Analyze this.",
                    output_format=AnalysisResult,
                )

        assert excinfo.value.retryable is True
        assert "Rate limit" in str(excinfo.value) or "429" in str(excinfo.value)


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
