"""Tests for the refinement service."""

from unittest.mock import AsyncMock

import pytest

from app.models import Optimization, RefinementBranch, RefinementTurn
from app.providers.base import LLMProvider
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
    SuggestionsOutput,
)
from app.services.refinement_service import RefinementService


@pytest.fixture
def prompts_dir(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    s = d / "strategies"
    s.mkdir()
    (d / "agent-guidance.md").write_text("system")
    (d / "analyze.md").write_text("{{raw_prompt}}\n{{available_strategies}}")
    (d / "refine.md").write_text(
        "{{current_prompt}}\n{{refinement_request}}\n{{original_prompt}}\n{{strategy_instructions}}"
    )
    (d / "scoring.md").write_text("score these")
    (d / "suggest.md").write_text(
        "{{optimized_prompt}}\n{{scores}}\n{{weaknesses}}\n{{strategy_used}}"
    )
    (d / "manifest.json").write_text(
        '{"analyze.md":{"required":["raw_prompt","available_strategies"],"optional":[]},'
        '"refine.md":{"required":["current_prompt","refinement_request","original_prompt","strategy_instructions"],"optional":["codebase_guidance","codebase_context","adaptation_state"]},'
        '"suggest.md":{"required":["optimized_prompt","scores","weaknesses","strategy_used"],"optional":[]},'
        '"scoring.md":{"required":[],"optional":[]}}'
    )
    (s / "auto.md").write_text("auto strategy")
    (s / "chain-of-thought.md").write_text("think step by step")
    return d


@pytest.fixture
async def sample_opt(db_session):
    opt = Optimization(
        id="ref-opt-1",
        raw_prompt="Write a function",
        optimized_prompt="Write a Python function...",
        task_type="coding",
        strategy_used="chain-of-thought",
        overall_score=7.5,
        status="completed",
        trace_id="ref-trace-1",
        provider="mock",
    )
    db_session.add(opt)
    await db_session.commit()
    return opt


@pytest.fixture
def mock_provider():
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "mock"
    return provider


@pytest.fixture
def service(db_session, mock_provider, prompts_dir):
    return RefinementService(db=db_session, provider=mock_provider, prompts_dir=prompts_dir)


def _make_analysis(**overrides):
    defaults = dict(
        task_type="coding",
        weaknesses=["vague"],
        strengths=["concise"],
        selected_strategy="chain-of-thought",
        strategy_rationale="good for coding",
        confidence=0.9,
    )
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def _make_optimization(**overrides):
    defaults = dict(
        optimized_prompt="Write a Python function that sorts a list.",
        changes_summary="Added specificity.",
        strategy_used="chain-of-thought",
    )
    defaults.update(overrides)
    return OptimizationResult(**defaults)


def _make_scores():
    return ScoreResult(
        prompt_a_scores=DimensionScores(
            clarity=4.0, specificity=3.0, structure=5.0, faithfulness=5.0, conciseness=6.0,
        ),
        prompt_b_scores=DimensionScores(
            clarity=8.0, specificity=8.0, structure=7.0, faithfulness=9.0, conciseness=7.0,
        ),
    )


def _make_suggestions():
    return SuggestionsOutput(
        suggestions=[
            {"text": "Add error handling", "source": "score-driven"},
            {"text": "Include examples", "source": "analysis-driven"},
            {"text": "Use step markers", "source": "strategic"},
        ]
    )


class TestRefinementService:
    async def test_create_initial_turn(self, service, sample_opt, db_session):
        """Creates branch + v1 turn."""
        turn = await service.create_initial_turn(
            optimization_id=sample_opt.id,
            prompt=sample_opt.optimized_prompt,
            scores_dict={"clarity": 8.0, "specificity": 7.0, "overall": 7.5},
            strategy_used="chain-of-thought",
        )

        assert isinstance(turn, RefinementTurn)
        assert turn.version == 1
        assert turn.prompt == sample_opt.optimized_prompt
        assert turn.scores == {"clarity": 8.0, "specificity": 7.0, "overall": 7.5}
        assert turn.strategy_used == "chain-of-thought"
        assert turn.branch_id is not None

        # Verify the branch was created
        from sqlalchemy import select
        result = await db_session.execute(
            select(RefinementBranch).where(RefinementBranch.id == turn.branch_id)
        )
        branch = result.scalar_one()
        assert branch.optimization_id == sample_opt.id
        assert branch.parent_branch_id is None

    async def test_get_versions(self, service, sample_opt, db_session):
        """Returns ordered list after creating turns."""
        turn1 = await service.create_initial_turn(
            optimization_id=sample_opt.id,
            prompt="v1 prompt",
            scores_dict={"clarity": 6.0},
            strategy_used="auto",
        )

        # Manually add a second turn on the same branch
        turn2 = RefinementTurn(
            optimization_id=sample_opt.id,
            version=2,
            branch_id=turn1.branch_id,
            parent_version=1,
            refinement_request="make it better",
            prompt="v2 prompt",
            scores={"clarity": 8.0},
            strategy_used="auto",
        )
        db_session.add(turn2)
        await db_session.commit()

        versions = await service.get_versions(sample_opt.id, branch_id=turn1.branch_id)
        assert len(versions) == 2
        assert versions[0].version == 1
        assert versions[1].version == 2

    async def test_rollback_creates_fork(self, service, sample_opt, db_session):
        """Creates new branch from version."""
        turn1 = await service.create_initial_turn(
            optimization_id=sample_opt.id,
            prompt="v1 prompt",
            scores_dict={"clarity": 6.0},
            strategy_used="auto",
        )
        original_branch_id = turn1.branch_id

        # Add v2
        turn2 = RefinementTurn(
            optimization_id=sample_opt.id,
            version=2,
            branch_id=turn1.branch_id,
            parent_version=1,
            prompt="v2 prompt",
            scores={"clarity": 8.0},
            strategy_used="auto",
        )
        db_session.add(turn2)
        await db_session.commit()

        # Rollback to version 1
        new_branch = await service.rollback(sample_opt.id, to_version=1)

        assert isinstance(new_branch, RefinementBranch)
        assert new_branch.id != original_branch_id
        assert new_branch.parent_branch_id == original_branch_id
        assert new_branch.forked_at_version == 1

    async def test_get_branches(self, service, sample_opt, db_session):
        """Lists branches for an optimization."""
        await service.create_initial_turn(
            optimization_id=sample_opt.id,
            prompt="v1 prompt",
            scores_dict={"clarity": 6.0},
            strategy_used="auto",
        )

        # Create a fork
        await service.rollback(sample_opt.id, to_version=1)

        branches = await service.get_branches(sample_opt.id)
        assert len(branches) == 2

    async def test_create_refinement_turn_emits_events(
        self, service, sample_opt, mock_provider, db_session
    ):
        """Mock provider side_effect for 4 calls, collect events, verify SSE types."""
        # Set up initial turn
        turn1 = await service.create_initial_turn(
            optimization_id=sample_opt.id,
            prompt=sample_opt.optimized_prompt,
            scores_dict={"clarity": 7.0, "specificity": 7.0},
            strategy_used="chain-of-thought",
        )

        mock_provider.complete_parsed.side_effect = [
            _make_analysis(),         # analyze
            _make_optimization(),     # refine
            _make_scores(),           # score
            _make_suggestions(),      # suggest
        ]

        events = []
        async for event in service.create_refinement_turn(
            optimization_id=sample_opt.id,
            branch_id=turn1.branch_id,
            refinement_request="Add error handling",
        ):
            events.append(event)

        event_types = [e.event for e in events]
        assert "status" in event_types
        assert "prompt_preview" in event_types
        assert "score_card" in event_types
        assert "suggestions" in event_types
        assert mock_provider.complete_parsed.call_count == 4

    async def test_suggestions_in_turn(
        self, service, sample_opt, mock_provider, db_session
    ):
        """Verify turn has suggestions after refinement."""
        turn1 = await service.create_initial_turn(
            optimization_id=sample_opt.id,
            prompt=sample_opt.optimized_prompt,
            scores_dict={"clarity": 7.0},
            strategy_used="chain-of-thought",
        )

        mock_provider.complete_parsed.side_effect = [
            _make_analysis(),
            _make_optimization(),
            _make_scores(),
            _make_suggestions(),
        ]

        async for _ in service.create_refinement_turn(
            optimization_id=sample_opt.id,
            branch_id=turn1.branch_id,
            refinement_request="Improve clarity",
        ):
            pass

        # Fetch the latest turn
        versions = await service.get_versions(sample_opt.id, branch_id=turn1.branch_id)
        latest = versions[-1]
        assert latest.version == 2
        assert latest.suggestions is not None
        assert len(latest.suggestions) == 3
        assert latest.suggestions[0]["text"] == "Add error handling"
