import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.mcp_server import synthesis_save_result
from app.models import Optimization

pytestmark = pytest.mark.asyncio

async def test_synthesis_save_result():
    # Context Mock
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = None

    scores = {
        "clarity": 5.0,
        "specificity": 5.0,
        "structure": 5.0,
        "faithfulness": 5.0,
        "conciseness": 5.0
    }

    mock_db = AsyncMock()
    
    mock_opt = MagicMock(spec=Optimization)
    mock_opt.id = "opt_123"
    mock_opt.strategy_used = "auto"
    mock_opt.optimized_prompt = "Hello"
    
    # Needs to be a coroutine returning a mock optimization
    mock_db.scalar.return_value = mock_opt
    
    with patch("app.mcp_server.async_session_factory") as mock_session_factory, \
         patch("app.mcp_server.score_prompt", new_callable=AsyncMock) as mock_score, \
         patch("app.mcp_server.blend_scores") as mock_blend, \
         patch("app.mcp_server.notify_event_bus", new_callable=AsyncMock):
             
        mock_session_factory.return_value.__aenter__.return_value = mock_db
        mock_score.return_value = {
            "clarity": 5.0,
            "specificity": 5.0,
            "structure": 5.0,
            "faithfulness": 5.0,
            "conciseness": 5.0,
            "composite": 5.0
        }
        mock_blend.return_value = {
            "clarity": 5.0,
            "specificity": 5.0,
            "structure": 5.0,
            "faithfulness": 5.0,
            "conciseness": 5.0,
            "composite": 5.0
        }

        result = await synthesis_save_result(
            trace_id="tr_123",
            optimized_prompt="Hello again",
            task_type="coding",
            strategy_used="auto",
            scores=scores,
            changes_summary=None,
            codebase_context=None,
            model="ide_llm",
            ctx=ctx
        )
        
        assert getattr(result, "status", None) == "completed"
        assert getattr(result, "optimization_id", None) == "opt_123"
        assert getattr(result, "final_scores", None) is not None

async def test_synthesis_save_result_not_found():
    ctx = MagicMock()
    
    mock_db = AsyncMock()
    mock_db.scalar.return_value = None  # None simulated not found
    
    with patch("app.mcp_server.async_session_factory") as mock_session_factory:
        mock_session_factory.return_value.__aenter__.return_value = mock_db
        
        with pytest.raises(ValueError, match="no pending optimization"):
            await synthesis_save_result(
                trace_id="tr_missing",
                optimized_prompt="Test",
                ctx=ctx
            )
