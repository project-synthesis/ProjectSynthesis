"""Tests verifying prompt caching on the API provider path."""

from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.pipeline_contracts import AnalysisResult


class TestPromptCaching:
    async def test_api_provider_sets_cache_control(self):
        """Verify system prompt includes cache_control for prompt caching."""
        with patch("app.providers.anthropic_api.AsyncAnthropic") as mock_cls:
            client = MagicMock()
            mock_cls.return_value = client

            mock_response = MagicMock()
            mock_response.parsed_output = AnalysisResult(
                task_type="coding", weaknesses=[], strengths=[],
                selected_strategy="auto", strategy_rationale="", confidence=0.5,
            )
            mock_response.usage.input_tokens = 100
            mock_response.usage.output_tokens = 50
            client.messages.parse = AsyncMock(return_value=mock_response)

            from app.providers.anthropic_api import AnthropicAPIProvider

            provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
            provider._client = client

            await provider.complete_parsed(
                model="claude-sonnet-4-6",
                system_prompt="You are an expert.",
                user_message="Analyze this.",
                output_format=AnalysisResult,
            )

            call_kwargs = client.messages.parse.call_args.kwargs
            system = call_kwargs["system"]
            assert isinstance(system, list)
            assert len(system) == 1
            assert system[0]["cache_control"] == {"type": "ephemeral"}

    async def test_cache_control_present_across_models(self):
        """All model calls should include cache_control on system prompt."""
        with patch("app.providers.anthropic_api.AsyncAnthropic") as mock_cls:
            client = MagicMock()
            mock_cls.return_value = client

            mock_response = MagicMock()
            mock_response.parsed_output = AnalysisResult(
                task_type="writing", weaknesses=[], strengths=[],
                selected_strategy="auto", strategy_rationale="", confidence=0.5,
            )
            mock_response.usage.input_tokens = 50
            mock_response.usage.output_tokens = 30
            client.messages.parse = AsyncMock(return_value=mock_response)

            from app.providers.anthropic_api import AnthropicAPIProvider

            provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
            provider._client = client

            for model in ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"]:
                await provider.complete_parsed(
                    model=model,
                    system_prompt="System prompt here.",
                    user_message="Message.",
                    output_format=AnalysisResult,
                )

            assert client.messages.parse.call_count == 3
            for call in client.messages.parse.call_args_list:
                system = call.kwargs["system"]
                assert system[0]["cache_control"] == {"type": "ephemeral"}
