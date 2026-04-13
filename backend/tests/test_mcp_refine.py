"""Tests for synthesis_refine MCP tool."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp_server import synthesis_refine
from app.schemas.mcp_models import RefineOutput
from app.schemas.pipeline_contracts import PipelineEvent
from app.services.context_enrichment import EnrichedContext
from app.services.routing import RoutingDecision

pytestmark = pytest.mark.asyncio


def _mock_routing(tier="internal", provider=None, provider_name=None):
    """Create a mock RoutingManager."""
    decision = RoutingDecision(
        tier=tier,
        provider=provider,
        provider_name=provider_name or (provider.name if provider else None),
        reason=f"test → {tier}",
    )
    rm = MagicMock()
    rm.resolve.return_value = decision
    return rm


async def test_refine_rejects_passthrough():
    """Refinement requires a provider — passthrough tier is rejected."""
    with (
        patch("app.tools._shared._routing", _mock_routing("passthrough")),
        patch("app.tools.refine.PreferencesService") as mock_prefs_cls,
    ):
        mock_prefs = MagicMock()
        mock_prefs.load.return_value = {}
        mock_prefs_cls.return_value = mock_prefs

        with pytest.raises(ValueError, match="requires a local LLM provider"):
            await synthesis_refine(
                optimization_id="opt-123",
                refinement_request="Add more examples",
            )


async def test_refine_optimization_not_found():
    """Raises ValueError when optimization doesn't exist."""
    mock_provider = AsyncMock()
    mock_provider.name = "test_provider"

    with (
        patch("app.tools._shared._routing", _mock_routing(
            "internal", provider=mock_provider, provider_name="test_provider",
        )),
        patch("app.tools.refine.PreferencesService") as mock_prefs_cls,
        patch("app.tools.refine.async_session_factory") as mock_factory,
    ):
        mock_prefs = MagicMock()
        mock_prefs.load.return_value = {}
        mock_prefs_cls.return_value = mock_prefs

        mock_db = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock empty query result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="Optimization not found"):
            await synthesis_refine(
                optimization_id="nonexistent-opt",
                refinement_request="Improve structure",
            )


async def test_refine_no_optimized_prompt():
    """Raises ValueError when optimization has no optimized prompt."""
    mock_provider = AsyncMock()
    mock_provider.name = "test_provider"

    mock_opt = MagicMock()
    mock_opt.id = "opt-123"
    mock_opt.optimized_prompt = ""  # empty = no prompt to refine
    mock_opt.status = "pending"

    with (
        patch("app.tools._shared._routing", _mock_routing(
            "internal", provider=mock_provider, provider_name="test_provider",
        )),
        patch("app.tools.refine.PreferencesService") as mock_prefs_cls,
        patch("app.tools.refine.async_session_factory") as mock_factory,
    ):
        mock_prefs = MagicMock()
        mock_prefs.load.return_value = {}
        mock_prefs_cls.return_value = mock_prefs

        mock_db = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_opt
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="has no optimized prompt"):
            await synthesis_refine(
                optimization_id="opt-123",
                refinement_request="Add error handling",
            )


async def test_refine_happy_path():
    """Successful refinement returns RefineOutput with scores and suggestions."""
    mock_provider = AsyncMock()
    mock_provider.name = "test_provider"

    opt_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())

    # Parent optimization with scores
    mock_opt = MagicMock()
    mock_opt.id = opt_id
    mock_opt.optimized_prompt = "Optimize this prompt for clarity and structure."
    mock_opt.status = "completed"
    mock_opt.strategy_used = "chain-of-thought"
    mock_opt.score_clarity = 7.0
    mock_opt.score_specificity = 6.5
    mock_opt.score_structure = 7.5
    mock_opt.score_faithfulness = 8.0
    mock_opt.score_conciseness = 6.0

    # Existing initial turn (so create_initial_turn is skipped)
    mock_initial_turn = MagicMock()
    mock_initial_turn.version = 1

    # Existing branch
    mock_branch = MagicMock()
    mock_branch.id = branch_id
    mock_branch.optimization_id = opt_id
    mock_branch.created_at = MagicMock()

    # Latest turn on branch
    mock_latest_turn = MagicMock()
    mock_latest_turn.version = 1

    # New turn created by refinement
    mock_new_turn = MagicMock()
    mock_new_turn.version = 2
    mock_new_turn.prompt = "Improved prompt with better structure and clarity."
    mock_new_turn.scores = {
        "clarity": 8.5,
        "specificity": 7.5,
        "structure": 8.0,
        "faithfulness": 8.5,
        "conciseness": 7.0,
    }
    mock_new_turn.deltas = {
        "clarity": 1.5,
        "specificity": 1.0,
        "structure": 0.5,
        "faithfulness": 0.5,
        "conciseness": 1.0,
    }

    # Refinement complete event with suggestions
    complete_event = PipelineEvent(
        event="refinement_complete",
        data={
            "strategy_used": "chain-of-thought",
            "suggestions": [
                {"text": "Add concrete examples", "source": "scorer"},
                {"text": "Reduce repetition", "source": "scorer"},
            ],
        },
    )

    async def _mock_refinement_generator(*args, **kwargs):
        yield PipelineEvent(event="phase_start", data={"phase": "refine"})
        yield PipelineEvent(event="phase_complete", data={"phase": "refine"})
        yield complete_event

    # Sequence of db.execute returns for the 5 queries in handle_refine:
    # 1. Load optimization (SELECT Optimization WHERE id = ...)
    # 2. Check existing turns (SELECT RefinementTurn LIMIT 1)
    # 3. Resolve branch (SELECT RefinementBranch ORDER BY created_at DESC LIMIT 1)
    # 4. Get latest turn on branch (SELECT RefinementTurn ORDER BY version DESC LIMIT 1)
    # 5. Fetch new turn after refinement (SELECT RefinementTurn ORDER BY version DESC LIMIT 1)
    mock_results = []
    for obj in [mock_opt, mock_initial_turn, mock_branch, mock_latest_turn, mock_new_turn]:
        r = MagicMock()
        r.scalar_one_or_none.return_value = obj
        mock_results.append(r)

    # Mock context enrichment service
    mock_enrichment = EnrichedContext(raw_prompt="mock")
    mock_ctx_svc = AsyncMock()
    mock_ctx_svc.enrich.return_value = mock_enrichment

    with (
        patch("app.tools._shared._routing", _mock_routing(
            "internal", provider=mock_provider, provider_name="test_provider",
        )),
        patch("app.tools._shared._context_service", mock_ctx_svc),
        patch("app.tools.refine.PreferencesService") as mock_prefs_cls,
        patch("app.tools.refine.async_session_factory") as mock_factory,
        patch("app.tools.refine.RefinementService") as mock_svc_cls,
        patch("app.tools.refine.notify_event_bus", new_callable=AsyncMock) as mock_notify,
    ):
        mock_prefs = MagicMock()
        mock_prefs.load.return_value = {}
        mock_prefs_cls.return_value = mock_prefs

        mock_db = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_db.execute = AsyncMock(side_effect=mock_results)
        mock_db.commit = AsyncMock()

        mock_svc = MagicMock()
        mock_svc.create_refinement_turn = _mock_refinement_generator
        mock_svc_cls.return_value = mock_svc

        result = await synthesis_refine(
            optimization_id=opt_id,
            refinement_request="Add more concrete examples and improve structure",
        )

    # Verify output model
    assert isinstance(result, RefineOutput)
    assert result.optimization_id == opt_id
    assert result.version == 2
    assert result.branch_id == branch_id
    assert result.refined_prompt == "Improved prompt with better structure and clarity."
    assert result.scores is not None
    assert result.scores["clarity"] == 8.5
    assert result.score_deltas is not None
    assert result.score_deltas["clarity"] == 1.5
    assert result.overall_score is not None
    from app.schemas.pipeline_contracts import DIMENSION_WEIGHTS
    expected_overall = round(sum(
        {"clarity": 8.5, "specificity": 7.5, "structure": 8.0, "faithfulness": 8.5, "conciseness": 7.0}[d] * w
        for d, w in DIMENSION_WEIGHTS.items()
    ), 2)
    assert result.overall_score == expected_overall
    assert result.strategy_used == "chain-of-thought"
    assert len(result.suggestions) == 2
    assert result.suggestions[0]["text"] == "Add concrete examples"

    # Verify event bus notification
    mock_notify.assert_awaited_once()
    notify_args = mock_notify.call_args
    assert notify_args[0][0] == "refinement_turn"
    assert notify_args[0][1]["optimization_id"] == opt_id
    assert notify_args[0][1]["version"] == 2
