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
    """Create a mock RoutingManager.

    P4-integration-review (2026-05-10): Cycle 2's restructured refine path
    reads ``routing.state.provider`` BEFORE calling ``routing.resolve()``.
    Pin ``rm.state.provider`` explicitly (defaulting to ``provider`` arg) so
    the test stub doesn't return an auto-MagicMock (truthy) when callers
    expect ``None`` (the passthrough rejection branch checks ``is None``).
    """
    decision = RoutingDecision(
        tier=tier,
        provider=provider,
        provider_name=provider_name or (provider.name if provider else None),
        reason=f"test → {tier}",
    )
    rm = MagicMock()
    rm.resolve.return_value = decision
    rm.state = MagicMock(provider=provider)
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
    # P4-integration-review (2026-05-10): Cycle 2's restructure renamed
    # `create_refinement_turn` → `invoke_refinement_pipeline` and expanded
    # the complete-event payload to 6 keys (the persist closure in
    # ``app/tools/refine.py:_persist_refinement_turn`` consumes all of them).
    # Compute expected `overall` for the scores dict — the production
    # `_persist_refinement_turn` reads `scores["overall"]` to populate
    # `event_payload["overall_score"]`.
    from app.schemas.pipeline_contracts import DIMENSION_WEIGHTS as _DW
    _expected_overall = round(sum(
        {"clarity": 8.5, "specificity": 7.5, "structure": 8.0,
         "faithfulness": 8.5, "conciseness": 7.0}[d] * w
        for d, w in _DW.items()
    ), 2)

    complete_event = PipelineEvent(
        event="refinement_complete",
        data={
            "optimized_prompt": "Improved prompt with better structure and clarity.",
            "scores": {
                "clarity": 8.5,
                "specificity": 7.5,
                "structure": 8.0,
                "faithfulness": 8.5,
                "conciseness": 7.0,
                "overall": _expected_overall,
            },
            "deltas_from_prev": {
                "clarity": 1.5,
                "specificity": 1.0,
                "structure": 0.5,
                "faithfulness": 0.5,
                "conciseness": 1.0,
            },
            "deltas_from_original": {
                "clarity": 1.5,
                "specificity": 1.0,
                "structure": 0.5,
                "faithfulness": 0.5,
                "conciseness": 1.0,
            },
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

    # Sequence of db.execute returns for the 6 queries in handle_refine
    # (P4 Cycle 2 restructure — see ``app/tools/refine.py``):
    # STEP 1 read session:
    #   1. _load_optimization (SELECT Optimization WHERE id = ...)
    #   2. _check_initial_turn_missing (SELECT COUNT(RefinementTurn))
    # STEP 1c read session (post-seed):
    #   3. _load_optimization (re-load fresh row)
    #   4. _resolve_branch (SELECT RefinementBranch ORDER BY created_at DESC LIMIT 1)
    #   5. _load_latest_turn (SELECT RefinementTurn ORDER BY version DESC LIMIT 1)
    #   6. _fetch_historical_stats (P4 Cycle 1 shared helper —
    #      OptimizationService.get_score_distribution aggregate, uses .one())
    # The new turn is persisted via WriteQueue.submit(), NOT via the read
    # session — so mock_new_turn is no longer fetched via db.execute.
    mock_results = []
    for obj in [mock_opt, mock_initial_turn, mock_branch, mock_latest_turn]:
        r = MagicMock()
        r.scalar_one_or_none.return_value = obj
        mock_results.append(r)

    # Insert the STEP 1c _load_optimization re-load BEFORE branch (index 2):
    # The new flow calls _load_optimization twice (STEP 1 + STEP 1c reload).
    reload_opt_r = MagicMock()
    reload_opt_r.scalar_one_or_none.return_value = mock_opt
    mock_results.insert(2, reload_opt_r)

    # Append the historical_stats no-data row: get_score_distribution calls
    # .one() returning a 6-column × 3-aggregate (count, avg, avg^2) tuple. All
    # zeros = no historical data — _fetch_historical_stats returns the dict but
    # downstream blending degrades gracefully when counts are zero.
    historical_stats_r = MagicMock()
    historical_stats_r.one.return_value = (0, 0.0, 0.0) * 6
    mock_results.append(historical_stats_r)

    # Mock context enrichment service
    mock_enrichment = EnrichedContext(raw_prompt="mock")
    mock_ctx_svc = AsyncMock()
    mock_ctx_svc.enrich.return_value = mock_enrichment

    # P4-integration-review (2026-05-10): Cycle 2's restructure routes the
    # final-turn persist through ``get_write_queue().submit()``. Build a fake
    # WriteQueue whose submit() invokes the callback directly so the test
    # doesn't need a real worker. The handler also calls
    # ``_check_initial_turn_missing`` before reaching the seed path; since
    # mock_initial_turn is "present" we want that submit gate skipped — the
    # fake submit handles it transparently if the gate ever fires.
    async def _fake_submit(work, *, timeout=None, operation_label=None):
        # The persist callback returns a dict — call it with a stub write_db
        # that captures the RefinementTurn add for shape verification.
        # ``write_db.add`` is sync — use MagicMock for it to avoid the
        # "coroutine was never awaited" warning. ``commit`` is async.
        stub_write_db = MagicMock()
        stub_write_db.commit = AsyncMock()
        return await work(stub_write_db)

    fake_wq = MagicMock()
    fake_wq.submit = AsyncMock(side_effect=_fake_submit)

    with (
        patch("app.tools._shared._routing", _mock_routing(
            "internal", provider=mock_provider, provider_name="test_provider",
        )),
        patch("app.tools._shared._context_service", mock_ctx_svc),
        patch("app.tools._shared._write_queue", fake_wq),
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
        # P4 Cycle 2 rename: `create_refinement_turn` → `invoke_refinement_pipeline`
        mock_svc.invoke_refinement_pipeline = _mock_refinement_generator
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
