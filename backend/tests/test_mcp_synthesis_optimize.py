from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp_server import synthesis_optimize
from app.schemas.mcp_models import OptimizeOutput
from app.services.context_enrichment import EnrichedContext
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


def _mock_context_service(guidance="", **overrides):
    """Create a mock ContextEnrichmentService that returns an EnrichedContext."""
    enrichment = EnrichedContext(
        raw_prompt="mock",
        workspace_guidance=overrides.get("workspace_guidance", guidance or None),
        codebase_context=overrides.get("codebase_context"),
        adaptation_state=overrides.get("adaptation_state"),
        applied_patterns=overrides.get("applied_patterns"),
        analysis=overrides.get("analysis"),
        context_sources=overrides.get("context_sources", {}),
    )
    svc = AsyncMock()
    svc.enrich.return_value = enrichment
    return svc


async def test_synthesis_optimize_with_provider():
    """Internal pipeline path: provider present → runs PipelineOrchestrator."""
    mock_provider = AsyncMock()
    mock_provider.name = "mock_provider"

    with (
        patch("app.tools._shared._routing", _mock_routing(
            "internal", provider=mock_provider, provider_name="mock_provider",
        )),
        patch("app.tools._shared._context_service", _mock_context_service(guidance="guidance")),
        patch("app.tools.optimize.async_session_factory") as mock_session_factory,
        patch("app.tools.optimize.PipelineOrchestrator") as mock_orchestrator,
        patch("app.tools.optimize.notify_event_bus", new_callable=AsyncMock) as mock_notify,
        patch("app.tools.optimize.PreferencesService") as mock_prefs_service,
    ):
        mock_prefs = MagicMock()
        mock_prefs.get.return_value = False
        mock_prefs.load.return_value = {}
        mock_prefs_service.return_value = mock_prefs

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
        # optimization_created only (taxonomy_changed emitted by engine after extraction)
        assert mock_notify.call_count == 1
        mock_notify.assert_any_call("optimization_created", {
            "id": "opt_123",
            "task_type": "",
            "intent_label": "general",
            "domain": "general",
            "domain_raw": "general",
            "strategy_used": "auto",
            "overall_score": None,
            "provider": "mock_provider",
            "status": "completed",
        })
        # taxonomy_changed is now emitted by engine.process_optimization()
        # after the extraction listener processes the optimization_created event,
        # not prematurely by the MCP server.


async def test_synthesis_optimize_force_passthrough():
    """Passthrough tier: assembled prompt returned for external processing."""
    with (
        patch("app.tools._shared._routing", _mock_routing("passthrough")),
        patch("app.tools._shared._context_service", _mock_context_service()),
        patch("app.tools.optimize.async_session_factory") as mock_session_factory,
        patch("app.tools.optimize.PreferencesService") as mock_prefs_service,
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
        patch("app.tools._shared._routing", _mock_routing("sampling")),
        patch("app.tools._shared._context_service", _mock_context_service()),
        patch("app.tools.optimize.run_sampling_pipeline", new_callable=AsyncMock) as mock_sampling,
        patch("app.tools.optimize.notify_event_bus", new_callable=AsyncMock),
        patch("app.tools.optimize.PreferencesService") as mock_prefs_service,
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
        patch("app.tools._shared._routing", _mock_routing(
            "internal", provider=mock_provider, provider_name="mock_provider",
        )),
        patch("app.tools._shared._context_service", _mock_context_service()),
        patch("app.tools.optimize.async_session_factory") as mock_session_factory,
        patch("app.tools.optimize.PipelineOrchestrator") as mock_orchestrator,
        patch("app.tools.optimize.PreferencesService") as mock_prefs_service,
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
