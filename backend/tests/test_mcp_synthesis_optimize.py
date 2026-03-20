import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# We need to import the function
from app.mcp_server import synthesis_optimize
from app.schemas.mcp_models import OptimizeOutput

pytestmark = pytest.mark.asyncio

async def test_synthesis_optimize_with_provider():
    # Context Mock
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = None

    mock_provider = AsyncMock()

    with patch("app.mcp_server._provider", mock_provider), \
         patch("app.mcp_server.async_session_factory") as mock_session_factory, \
         patch("app.mcp_server.PipelineOrchestrator") as mock_orchestrator, \
         patch("app.mcp_server.notify_event_bus", new_callable=AsyncMock) as mock_notify, \
         patch("app.mcp_server._resolve_workspace_guidance", new_callable=AsyncMock) as mock_resolve, \
         patch("app.mcp_server.PreferencesService") as mock_prefs_service:
             
        mock_prefs = MagicMock()
        mock_prefs.get.return_value = False
        mock_prefs_service.return_value = mock_prefs
        
        mock_resolve.return_value = "guidance"
             
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session
        
        # Pipeline orchestrator run iterator
        async def mock_run(*args, **kwargs):
            yield MagicMock(event="optimization_complete", data={"id": "opt_123", "strategy_used": "auto", "optimized_prompt": "Hello"})
            
        mock_orchestrator.return_value.run = mock_run

        result = await synthesis_optimize(
            prompt="This is a prompt that is long enough to pass validation.",
            ctx=ctx
        )
        
        # In Pydantic we can access dict directly or via getattr
        assert getattr(result, "optimized_prompt", None) == "Hello"
        mock_notify.assert_called_once()


async def test_synthesis_optimize_force_passthrough():
    ctx = MagicMock()
    with patch("app.mcp_server.assemble_passthrough_prompt") as mock_assemble, \
         patch("app.mcp_server.PipelineOrchestrator") as mock_orchestrator, \
         patch("app.mcp_server.PreferencesService") as mock_prefs_service, \
         patch("app.mcp_server.async_session_factory") as mock_session_factory:
             
        mock_prefs = MagicMock()
        # Mock get to return force_passthrough as True
        mock_prefs.get.side_effect = lambda k: True if k == "pipeline.force_passthrough" else False
        mock_prefs_service.return_value = mock_prefs
             
        mock_assemble.return_value = ("<assembled>prompt</assembled>", "auto")
        
        # We need a db session returning dummy
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session
        
        result = await synthesis_optimize(
            prompt="This is a prompt that is long enough to pass validation.",
            ctx=ctx
        )
        
        # Returns OptimizeOutput
        assert getattr(result, "status", None) == "pending_external"
        assert getattr(result, "error", None) is None
        assert getattr(result, "optimized_prompt", None) is None
        assert "call synthesis_save_result" in getattr(result, "instructions", "")

async def test_synthesis_optimize_no_provider_but_sampling():
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = True
    
    with patch("app.mcp_server.run_sampling_pipeline", new_callable=AsyncMock) as mock_sampling, \
         patch("app.mcp_server._provider", None), \
         patch("app.mcp_server.notify_event_bus", new_callable=AsyncMock) as mock_notify, \
         patch("app.mcp_server.PreferencesService") as mock_prefs_service:
             
        mock_prefs = MagicMock()
        mock_prefs.get.return_value = False # don't force anything
        mock_prefs_service.return_value = mock_prefs
        
        mock_sampling.return_value = {"id": "opt_123", "optimized_prompt": "Hello again", "strategy_used": "auto"}
        
        result = await synthesis_optimize(
            prompt="This is a prompt that is long enough to pass validation.",
            ctx=ctx
        )
        
        assert getattr(result, "status", None) == "completed"
        assert getattr(result, "optimized_prompt", None) == "Hello again"
        # assert mock_notify.call_count == 2
        

async def test_synthesis_optimize_pipeline_error():
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = None

    mock_provider = AsyncMock()

    with patch("app.mcp_server._provider", mock_provider), \
         patch("app.mcp_server.async_session_factory") as mock_session_factory, \
         patch("app.mcp_server.PipelineOrchestrator") as mock_orchestrator, \
         patch("app.mcp_server._resolve_workspace_guidance", new_callable=AsyncMock), \
         patch("app.mcp_server.PreferencesService") as mock_prefs_service:
             
        mock_prefs = MagicMock()
        mock_prefs.get.return_value = False
        mock_prefs_service.return_value = mock_prefs
        
        mock_session_factory.return_value.__aenter__.return_value = AsyncMock()
        
        async def mock_run(*args, **kwargs):
            yield MagicMock(event="error", data={"error": "Something exploded"})
            
        mock_orchestrator.return_value.run = mock_run

        with pytest.raises(ValueError, match="Something exploded"):
            await synthesis_optimize(
                prompt="This is a prompt that is long enough to pass validation.",
                ctx=ctx
            )

