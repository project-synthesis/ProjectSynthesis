"""Tests for MCP server tools."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp_server import (
    synthesis_analyze,
    synthesis_optimize,
    synthesis_prepare_optimization,
    synthesis_save_result,
)
from app.schemas.mcp_models import (
    AnalyzeOutput,
    OptimizeOutput,
    PrepareOutput,
    SaveResultOutput,
)
from app.services.routing import RoutingDecision


def _mock_routing(tier: str = "passthrough", provider=None, provider_name: str | None = None):
    """Create a mock RoutingManager that always resolves to the given tier."""
    decision = RoutingDecision(
        tier=tier,
        provider=provider,
        provider_name=provider_name or (provider.name if provider else None),
        reason=f"test mock → {tier}",
    )
    rm = MagicMock()
    rm.resolve.return_value = decision
    return rm

# ---------------------------------------------------------------------------
# synthesis_prepare_optimization
# ---------------------------------------------------------------------------


async def test_prepare_returns_model(db_session) -> None:
    """synthesis_prepare_optimization returns a PrepareOutput model."""
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_prepare_optimization(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
        )

    assert isinstance(result, PrepareOutput)
    assert len(result.trace_id) == 36  # UUID format
    assert len(result.assembled_prompt) > 0
    assert result.context_size_tokens > 0
    assert result.strategy_requested == "auto"  # default when none specified


async def test_prepare_with_explicit_strategy(db_session) -> None:
    """Explicit strategy name is reflected in the returned strategy_requested."""
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_prepare_optimization(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
            strategy="chain-of-thought",
        )

    assert result.strategy_requested == "chain-of-thought"
    # Strategy instructions should be embedded in the assembled prompt
    assert len(result.assembled_prompt) > 100


async def test_prepare_falls_back_to_auto_for_unknown_strategy(db_session) -> None:
    """Unknown strategy falls back to auto without raising."""
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_prepare_optimization(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
            strategy="nonexistent-strategy-xyz",
        )

    assert result.strategy_requested == "auto"


async def test_prepare_rejects_short_prompt() -> None:
    """Prompts under 20 characters are rejected."""
    with pytest.raises(ValueError, match="too short"):
        await synthesis_prepare_optimization(prompt="short")


# ---------------------------------------------------------------------------
# synthesis_save_result
# ---------------------------------------------------------------------------


async def test_save_result_returns_model(db_session) -> None:
    """synthesis_save_result returns a SaveResultOutput model."""
    raw_scores = {
        "clarity": 8.0,
        "specificity": 8.0,
        "structure": 8.0,
        "faithfulness": 8.0,
        "conciseness": 8.0,
    }

    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_save_result(
            trace_id="test-trace-id",
            optimized_prompt=(
                "## Task\nWrite a well-structured Python function.\n\n"
                "## Requirements\n- Must validate input\n- Must return bool"
            ),
            changes_summary="Added structure and constraints",
            task_type="coding",
            strategy_used="structured-output",
            scores=raw_scores,
            model="claude-opus-4",
        )

    assert isinstance(result, SaveResultOutput)
    assert result.optimization_id
    assert result.scoring_mode == "hybrid_passthrough"
    assert result.strategy_compliance == "matched"
    assert result.overall_score is not None

    # Hybrid blending combines bias-corrected IDE scores with heuristics
    # Scores should be lower than raw input (bias correction + heuristic blending)
    for dim, score_value in result.scores.items():
        assert score_value < raw_scores[dim], (
            f"{dim}: blended {score_value} should be < raw {raw_scores[dim]}"
        )


async def test_save_result_without_scores(db_session) -> None:
    """Saving without IDE scores falls back to heuristic scoring."""
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_save_result(
            trace_id="test-trace-no-scores",
            optimized_prompt="A simple prompt without scores provided by the caller.",
        )

    # Falls back to heuristic scoring when no IDE scores are provided
    assert result.scoring_mode == "heuristic"
    assert result.scores  # non-empty — heuristic scores computed
    assert result.overall_score is not None
    assert result.heuristic_flags == []
    assert result.strategy_compliance == "unknown"


# ---------------------------------------------------------------------------
# synthesis_optimize
# ---------------------------------------------------------------------------


async def test_optimize_validates_prompt_too_short() -> None:
    """synthesis_optimize rejects prompts under 20 characters."""
    with pytest.raises(ValueError, match="too short"):
        await synthesis_optimize(prompt="hi")


async def test_optimize_validates_prompt_too_long() -> None:
    """synthesis_optimize rejects prompts over 200000 characters."""
    with pytest.raises(ValueError, match="too long"):
        await synthesis_optimize(prompt="x" * 200001)


async def test_optimize_returns_model(db_session) -> None:
    """synthesis_optimize returns an OptimizeOutput model (passthrough path)."""
    with (
        patch("app.mcp_server._routing", _mock_routing("passthrough")),
        patch("app.mcp_server.async_session_factory") as mock_factory,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_optimize(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
        )

    assert isinstance(result, OptimizeOutput)
    assert result.status == "pending_external"
    assert result.pipeline_mode == "passthrough"
    assert result.trace_id
    assert len(result.trace_id) == 36
    assert result.assembled_prompt
    assert len(result.assembled_prompt) > 100
    assert result.instructions
    assert "synthesis_save_result" in result.instructions
    assert result.strategy_used == "auto"


# ---------------------------------------------------------------------------
# synthesis_save_result — codebase_context
# ---------------------------------------------------------------------------


async def test_save_result_stores_codebase_context(db_session) -> None:
    """Passing codebase_context persists it (truncated) on the optimization record."""
    context_text = "Project uses FastAPI + SvelteKit. Key patterns: async, runes."

    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_save_result(
            trace_id="test-trace-with-context",
            optimized_prompt="A well-structured prompt with clear requirements.",
            strategy_used="auto",
            codebase_context=context_text,
        )

    assert result.optimization_id

    # Verify the record was persisted with codebase_context_snapshot
    from sqlalchemy import select

    from app.models import Optimization

    row = await db_session.execute(
        select(Optimization).where(Optimization.trace_id == "test-trace-with-context")
    )
    opt = row.scalar_one()
    assert opt.codebase_context_snapshot == context_text


# ---------------------------------------------------------------------------
# synthesis_optimize — passthrough mode (no provider)
# ---------------------------------------------------------------------------


async def test_optimize_passthrough_includes_strategy(db_session) -> None:
    """Passthrough mode includes the requested strategy in the assembled prompt."""
    with (
        patch("app.mcp_server._routing", _mock_routing("passthrough")),
        patch("app.mcp_server.async_session_factory") as mock_factory,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_optimize(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
            strategy="chain-of-thought",
        )

    assert result.status == "pending_external"
    assert result.strategy_used == "chain-of-thought"


async def test_optimize_passthrough_then_save_full_flow(db_session) -> None:
    """Full passthrough flow: optimize (pending) → save_result (completed)."""
    with (
        patch("app.mcp_server._routing", _mock_routing("passthrough")),
        patch("app.mcp_server.async_session_factory") as mock_factory,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # Step 1: Get assembled prompt
        pending = await synthesis_optimize(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
        )

    assert pending.status == "pending_external"
    trace_id = pending.trace_id

    # Step 2: Save the external LLM's result
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        saved = await synthesis_save_result(
            trace_id=trace_id,
            optimized_prompt=(
                "## Task\nValidate email addresses using RFC 5322.\n\n"
                "## Requirements\n- Use re module\n- Return bool\n- Handle None input"
            ),
            changes_summary="Added structure, constraints, edge cases",
            task_type="coding",
            strategy_used="auto",
            scores={
                "clarity": 8.5,
                "specificity": 8.0,
                "structure": 9.0,
                "faithfulness": 8.5,
                "conciseness": 7.5,
            },
        )

    assert saved.optimization_id
    assert saved.scoring_mode == "hybrid_passthrough"
    assert saved.overall_score is not None
    assert saved.scores  # non-empty
    assert saved.strategy_compliance == "matched"


async def test_optimize_passthrough_save_without_scores(db_session) -> None:
    """Passthrough save without IDE scores falls back to heuristic."""
    with (
        patch("app.mcp_server._routing", _mock_routing("passthrough")),
        patch("app.mcp_server.async_session_factory") as mock_factory,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        pending = await synthesis_optimize(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
        )

    trace_id = pending.trace_id

    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        saved = await synthesis_save_result(
            trace_id=trace_id,
            optimized_prompt="A well-structured prompt without IDE scores.",
        )

    assert saved.scoring_mode == "heuristic"
    assert saved.overall_score is not None
    assert saved.scores


# ---------------------------------------------------------------------------
# synthesis_analyze — sampling fallback
# ---------------------------------------------------------------------------


def _make_text_result(text: str, model: str = "claude-sonnet-4-6"):
    """Build a mock CreateMessageResult with text content."""
    content = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=content, model=model)


async def test_analyze_sampling_fallback(db_session) -> None:
    """When no provider, synthesis_analyze falls back to sampling."""
    from app.schemas.pipeline_contracts import AnalysisResult, DimensionScores, ScoreResult

    analysis_json = AnalysisResult(
        task_type="coding",
        weaknesses=["vague"],
        strengths=["clear"],
        selected_strategy="auto",
        strategy_rationale="General task",
        confidence=0.8,
        intent_label="test task",
        domain="backend",
    ).model_dump_json()

    score_json = ScoreResult(
        prompt_a_scores=DimensionScores(
            clarity=7.0, specificity=7.0, structure=7.0,
            faithfulness=7.0, conciseness=7.0,
        ),
        prompt_b_scores=DimensionScores(
            clarity=7.0, specificity=7.0, structure=7.0,
            faithfulness=7.0, conciseness=7.0,
        ),
    ).model_dump_json()

    call_count = 0
    responses = [
        _make_text_result(analysis_json),
        _make_text_result(score_json),
    ]

    async def mock_create_message(**kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.create_message = AsyncMock(side_effect=mock_create_message)
    ctx.session.client_params = None

    with (
        patch("app.mcp_server._routing", _mock_routing("sampling")),
        patch("app.services.sampling_pipeline.async_session_factory") as mock_factory,
    ):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_analyze(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
            ctx=ctx,
        )

    assert isinstance(result, AnalyzeOutput)
    assert result.task_type == "coding"
    assert result.optimization_id
    assert result.overall_score > 0
    assert result.intent_label == "test task"
    assert result.domain == "backend"
