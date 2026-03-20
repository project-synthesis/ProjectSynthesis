import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp_server import synthesis_analyze
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    ScoreResult,
)
from app.services.routing import RoutingDecision

pytestmark = pytest.mark.asyncio


def _mock_routing(tier="passthrough", provider=None, provider_name=None):
    """Create a mock RoutingManager that always resolves to the given tier."""
    decision = RoutingDecision(
        tier=tier,
        provider=provider,
        provider_name=provider_name or (provider.name if provider else None),
        reason=f"test → {tier}",
    )
    rm = MagicMock()
    rm.resolve.return_value = decision
    return rm


async def test_synthesis_analyze_with_provider():
    """Internal tier: uses local provider for analysis + scoring."""
    mock_provider = AsyncMock()
    mock_provider.name = "mock_provider"

    def side_effect(*args, **kwargs):
        if "output_format" in kwargs:
            if kwargs["output_format"] == AnalysisResult:
                return AnalysisResult(
                    task_type="coding",
                    intent_label="Fix a bug",
                    domain="backend",
                    weaknesses=["vague"],
                    strengths=["clear"],
                    selected_strategy="auto",
                    strategy_rationale="testing",
                    confidence=0.9,
                )
            if kwargs["output_format"] == ScoreResult:
                return ScoreResult(
                    prompt_a_scores=DimensionScores(
                        clarity=5.0, specificity=5.0, structure=5.0,
                        faithfulness=5.0, conciseness=5.0,
                    ),
                    prompt_b_scores=DimensionScores(
                        clarity=5.0, specificity=5.0, structure=5.0,
                        faithfulness=5.0, conciseness=5.0,
                    ),
                )
        return MagicMock()

    mock_provider.complete_parsed.side_effect = side_effect

    with (
        patch("app.mcp_server._routing", _mock_routing("internal", provider=mock_provider, provider_name="mock_provider")),
        patch("app.mcp_server.async_session_factory") as mock_session_factory,
        patch("app.mcp_server.blend_scores") as mock_blend,
        patch("app.mcp_server.PreferencesService") as mock_prefs_service,
        patch("app.mcp_server.HeuristicScorer") as mock_heuristic,
    ):
        mock_blend_result = MagicMock()
        mock_blend_result.to_dimension_scores.return_value = DimensionScores(
            clarity=5.0, specificity=5.0, structure=5.0,
            faithfulness=5.0, conciseness=5.0,
        )
        mock_blend_result.divergence_flags = []
        mock_blend.return_value = mock_blend_result

        mock_heuristic.score_prompt.return_value = {
            "clarity": 5.0, "specificity": 5.0, "structure": 5.0,
            "faithfulness": 5.0, "conciseness": 5.0,
        }

        mock_prefs = MagicMock()
        mock_prefs.load.return_value = {}
        mock_prefs_service.return_value = mock_prefs

        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_analyze(
            "This is a prompt that is long enough to pass validation.",
        )

        assert result.task_type == "coding"
        assert result.baseline_scores["clarity"] == 5.0
        assert mock_provider.complete_parsed.call_count == 2


async def test_synthesis_analyze_no_provider_but_sampling():
    """Sampling tier: falls back to MCP sampling."""
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.create_message = AsyncMock()
    ctx.session.client_params = None

    with (
        patch("app.mcp_server._routing", _mock_routing("sampling")),
        patch("app.mcp_server.run_sampling_analyze", new_callable=AsyncMock) as mock_sampling,
        patch("app.mcp_server.PreferencesService") as mock_prefs_service,
    ):
        mock_prefs = MagicMock()
        mock_prefs.load.return_value = {}
        mock_prefs_service.return_value = mock_prefs

        # Return dict that matches AnalyzeOutput schema
        mock_sampling.return_value = {
            "optimization_id": "opt_123",
            "task_type": "writing",
            "intent_label": "Draft email",
            "domain": "general",
            "weaknesses": ["Vague"],
            "strengths": ["Friendly"],
            "selected_strategy": "few-shot",
            "strategy_rationale": "Good for creative tasks",
            "confidence": 0.85,
            "baseline_scores": {
                "clarity": 8.0, "specificity": 7.0, "structure": 6.0,
                "faithfulness": 5.0, "conciseness": 9.0,
            },
            "overall_score": 7.0,
            "duration_ms": 1200,
            "next_steps": ["Improve specificity"],
            "scores": {
                "clarity": 8.0, "specificity": 7.0, "structure": 6.0,
                "faithfulness": 5.0, "conciseness": 9.0,
            },
            "optimization_ready": {
                "prompt": "Test",
                "strategy": "few-shot",
            },
        }

        result = await synthesis_analyze(
            "This is a prompt that is long enough to pass validation.",
            ctx,
        )
        assert result.task_type == "writing"
        assert result.baseline_scores["clarity"] == 8.0


async def test_synthesis_analyze_no_provider_no_sampling():
    """Passthrough tier: analysis rejected (requires a provider)."""
    with (
        patch("app.mcp_server._routing", _mock_routing("passthrough")),
        patch("app.mcp_server.PreferencesService") as mock_prefs_service,
    ):
        mock_prefs = MagicMock()
        mock_prefs.load.return_value = {}
        mock_prefs_service.return_value = mock_prefs

        with pytest.raises(ValueError, match="requires a local provider"):
            await synthesis_analyze(
                "This is a prompt that is long enough to pass validation.",
            )
