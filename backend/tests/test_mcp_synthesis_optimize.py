from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp_server import synthesis_optimize
from app.schemas.mcp_models import OptimizeOutput
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


async def test_synthesis_optimize_with_provider():
    """Internal pipeline path: provider present → runs PipelineOrchestrator."""
    mock_provider = AsyncMock()
    mock_provider.name = "mock_provider"

    with (
        patch("app.mcp_server._routing", _mock_routing(
            "internal", provider=mock_provider, provider_name="mock_provider",
        )),
        patch("app.mcp_server.async_session_factory") as mock_session_factory,
        patch("app.mcp_server.PipelineOrchestrator") as mock_orchestrator,
        patch("app.mcp_server.notify_event_bus", new_callable=AsyncMock) as mock_notify,
        patch("app.mcp_server._resolve_workspace_guidance", new_callable=AsyncMock) as mock_resolve,
        patch("app.mcp_server.PreferencesService") as mock_prefs_service,
    ):
        mock_prefs = MagicMock()
        mock_prefs.get.return_value = False
        mock_prefs.load.return_value = {}
        mock_prefs_service.return_value = mock_prefs

        mock_resolve.return_value = "guidance"

        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # Pipeline orchestrator run iterator
        async def mock_run(*args, **kwargs):
            yield MagicMock(
                event="optimization_complete",
                data={"id": "opt_123", "strategy_used": "auto", "optimized_prompt": "Hello"},
            )

        mock_orchestrator.return_value.run = mock_run

        result = await synthesis_optimize(
            prompt="This is a prompt that is long enough to pass validation.",
        )

        assert getattr(result, "optimized_prompt", None) == "Hello"
        mock_notify.assert_called_once()


async def test_synthesis_optimize_force_passthrough():
    """Passthrough tier: assembled prompt returned for external processing."""
    with (
        patch("app.mcp_server._routing", _mock_routing("passthrough")),
        patch("app.mcp_server.async_session_factory") as mock_session_factory,
        patch("app.mcp_server.PreferencesService") as mock_prefs_service,
    ):
        mock_prefs = MagicMock()
        mock_prefs.get.return_value = False
        mock_prefs.load.return_value = {}
        mock_prefs_service.return_value = mock_prefs

        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_optimize(
            prompt="This is a prompt that is long enough to pass validation.",
        )

        assert isinstance(result, OptimizeOutput)
        assert result.status == "pending_external"
        assert result.pipeline_mode == "passthrough"
        assert "synthesis_save_result" in result.instructions


async def test_synthesis_optimize_no_provider_but_sampling():
    """Sampling tier: runs sampling pipeline via IDE's LLM."""
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = True

    with (
        patch("app.mcp_server._routing", _mock_routing("sampling")),
        patch("app.mcp_server.run_sampling_pipeline", new_callable=AsyncMock) as mock_sampling,
        patch("app.mcp_server.notify_event_bus", new_callable=AsyncMock),
        patch("app.mcp_server.PreferencesService") as mock_prefs_service,
        patch("app.mcp_server._resolve_workspace_guidance", new_callable=AsyncMock, return_value=""),
    ):
        mock_prefs = MagicMock()
        mock_prefs.get.return_value = False
        mock_prefs.load.return_value = {}
        mock_prefs_service.return_value = mock_prefs

        mock_sampling.return_value = {
            "id": "opt_123",
            "optimized_prompt": "Hello again",
            "strategy_used": "auto",
        }

        result = await synthesis_optimize(
            prompt="This is a prompt that is long enough to pass validation.",
            ctx=ctx,
        )

        assert result.status == "completed"
        assert result.optimized_prompt == "Hello again"


async def test_synthesis_optimize_pipeline_error():
    """Internal pipeline error: raises ValueError."""
    mock_provider = AsyncMock()
    mock_provider.name = "mock_provider"

    with (
        patch("app.mcp_server._routing", _mock_routing(
            "internal", provider=mock_provider, provider_name="mock_provider",
        )),
        patch("app.mcp_server.async_session_factory") as mock_session_factory,
        patch("app.mcp_server.PipelineOrchestrator") as mock_orchestrator,
        patch("app.mcp_server._resolve_workspace_guidance", new_callable=AsyncMock, return_value=""),
        patch("app.mcp_server.PreferencesService") as mock_prefs_service,
    ):
        mock_prefs = MagicMock()
        mock_prefs.get.return_value = False
        mock_prefs.load.return_value = {}
        mock_prefs_service.return_value = mock_prefs

        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        async def mock_run(*args, **kwargs):
            yield MagicMock(event="error", data={"error": "Something exploded"})

        mock_orchestrator.return_value.run = mock_run

        with pytest.raises(ValueError, match="Something exploded"):
            await synthesis_optimize(
                prompt="This is a prompt that is long enough to pass validation.",
            )
