"""Tests for the refinement router (POST /api/refine, GET versions, POST rollback)."""

import json

import pytest

from app.models import Optimization
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
    SuggestionsOutput,
)

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


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
        optimized_prompt="Write a Python function that sorts a list in ascending order.",
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def sample_opt(db_session):
    """A completed optimization in the test DB."""
    opt = Optimization(
        id="refine-opt-1",
        raw_prompt="Write a function",
        optimized_prompt="Write a Python function...",
        task_type="coding",
        strategy_used="chain-of-thought",
        score_clarity=7.0,
        score_specificity=7.0,
        score_structure=7.0,
        score_faithfulness=7.0,
        score_conciseness=7.0,
        overall_score=7.0,
        status="completed",
        trace_id="refine-trace-1",
        provider="mock",
    )
    db_session.add(opt)
    await db_session.commit()
    return opt


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRefineSSE:
    async def test_refine_sse(self, app_client, mock_provider, sample_opt):
        """POST /api/refine returns SSE stream with expected event types."""
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(),
            _make_optimization(),
            _make_scores(),
            _make_suggestions(),
        ]

        resp = await app_client.post(
            "/api/refine",
            json={
                "optimization_id": "refine-opt-1",
                "refinement_request": "Add error handling",
            },
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        events = []
        for line in resp.text.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        event_types = {e["event"] for e in events}
        assert "status" in event_types
        assert "prompt_preview" in event_types
        assert "score_card" in event_types
        assert "suggestions" in event_types

    async def test_refine_not_found(self, app_client):
        """POST /api/refine with unknown optimization_id → 404."""
        resp = await app_client.post(
            "/api/refine",
            json={
                "optimization_id": "does-not-exist",
                "refinement_request": "Improve clarity",
            },
        )
        assert resp.status_code == 404

    async def test_refine_no_provider(self, app_client, sample_opt):
        """POST /api/refine without a provider → 503."""
        app_client._transport.app.state.provider = None
        resp = await app_client.post(
            "/api/refine",
            json={
                "optimization_id": "refine-opt-1",
                "refinement_request": "Make it shorter",
            },
        )
        assert resp.status_code == 503


class TestGetVersions:
    async def test_get_versions(self, app_client, db_session, sample_opt):
        """GET /api/refine/{id}/versions returns the version list after creating turns."""
        from app.config import PROMPTS_DIR
        from app.services.refinement_service import RefinementService

        # Seed an initial turn directly via the service
        ref_svc = RefinementService(db=db_session, provider=None, prompts_dir=PROMPTS_DIR)
        await ref_svc.create_initial_turn(
            optimization_id="refine-opt-1",
            prompt="Write a Python function...",
            scores_dict={"clarity": 7.0, "specificity": 7.0},
            strategy_used="chain-of-thought",
        )

        resp = await app_client.get("/api/refine/refine-opt-1/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["optimization_id"] == "refine-opt-1"
        assert len(data["versions"]) == 1
        assert data["versions"][0]["version"] == 1

    async def test_get_versions_not_found(self, app_client):
        """GET /api/refine/{id}/versions with unknown id → 404."""
        resp = await app_client.get("/api/refine/nonexistent-id/versions")
        assert resp.status_code == 404


class TestRollback:
    async def test_rollback(self, app_client, db_session, sample_opt):
        """POST /api/refine/{id}/rollback creates a new forked branch."""
        from app.config import PROMPTS_DIR
        from app.services.refinement_service import RefinementService

        ref_svc = RefinementService(db=db_session, provider=None, prompts_dir=PROMPTS_DIR)
        initial = await ref_svc.create_initial_turn(
            optimization_id="refine-opt-1",
            prompt="Write a Python function...",
            scores_dict={"clarity": 7.0},
            strategy_used="chain-of-thought",
        )
        original_branch_id = initial.branch_id

        resp = await app_client.post(
            "/api/refine/refine-opt-1/rollback",
            json={"to_version": 1},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["optimization_id"] == "refine-opt-1"
        assert data["forked_at_version"] == 1
        assert data["parent_branch_id"] == original_branch_id
        assert data["id"] != original_branch_id

    async def test_rollback_not_found(self, app_client, sample_opt):
        """POST rollback on version that doesn't exist → 404."""
        resp = await app_client.post(
            "/api/refine/refine-opt-1/rollback",
            json={"to_version": 999},
        )
        assert resp.status_code == 404
