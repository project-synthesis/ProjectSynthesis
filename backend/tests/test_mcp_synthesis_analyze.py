import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# We need to import the function
from app.mcp_server import synthesis_analyze
from app.schemas.pipeline_contracts import AnalysisResult, ScoreResult

pytestmark = pytest.mark.asyncio

async def test_synthesis_analyze_with_provider():
    # Context Mock
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = None

    mock_provider = AsyncMock()
    # Need to return an AnalysisResult then a ScoreResult
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
                    confidence=0.9
                )
            if kwargs["output_format"] == ScoreResult:
                return ScoreResult(
                    a_scores={"clarity": 5.0, "specificity": 5.0, "structure": 5.0, "faithfulness": 5.0, "conciseness": 5.0},
                    b_scores={"clarity": 5.0, "specificity": 5.0, "structure": 5.0, "faithfulness": 5.0, "conciseness": 5.0},
                    a_reasoning="Good",
                    b_reasoning="Good",
                    preference="A"
                )
        return MagicMock()

    mock_provider.complete_parsed.side_effect = side_effect

    with patch("app.mcp_server._provider", mock_provider), \
         patch("app.mcp_server.async_session_factory") as mock_session_factory, \
         patch("app.mcp_server.blend_scores") as mock_blend, \
         patch("app.mcp_server.PreferencesService"), \
         patch("app.mcp_server.HeuristicScorer"):
             
        mock_blend.return_value = {
            "clarity": 5.0,
            "specificity": 5.0,
            "structure": 5.0,
            "faithfulness": 5.0,
            "conciseness": 5.0,
            "composite": 5.0
        }

        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        result = await synthesis_analyze("This is a prompt that is long enough to pass validation.", ctx)
        
        assert result.task_type == "coding"
        assert result.scores["clarity"] == 5.0
        assert mock_provider.complete_parsed.call_count == 2


async def test_synthesis_analyze_no_provider_but_sampling():
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = True
    
    with patch("app.mcp_server.run_sampling_analyze", new_callable=AsyncMock) as mock_sampling, \
         patch("app.mcp_server._provider", None):
        mock_sampling.return_value = {
            "task_type": "writing",
            "intent_label": "Draft email",
            "domain": "general",
            "weaknesses": ["Vague"],
            "strengths": ["Friendly"],
            "strategy": "few-shot",
            "scores": {
                "clarity": 8.0,
                "specificity": 7.0,
                "structure": 6.0,
                "faithfulness": 5.0,
                "conciseness": 9.0,
            },
            "suggested_next_steps": "Improve",
            "optimization_ready": {
                "prompt": "Test",
                "strategy": "few-shot",
                "repo_full_name": None,
                "workspace_path": None,
                "applied_pattern_ids": None
            }
        }
        
        result = await synthesis_analyze("This is a prompt that is long enough to pass validation.", ctx)
        assert result.task_type == "writing"
        assert result.scores["clarity"] == 8.0


async def test_synthesis_analyze_no_provider_no_sampling():
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.client_params.capabilities.sampling = None
    
    with patch("app.mcp_server._provider", None):
        with pytest.raises(ValueError, match="No LLM provider available"):
            await synthesis_analyze("This is a prompt that is long enough to pass validation.", ctx)

