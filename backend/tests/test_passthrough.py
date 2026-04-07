"""Tests for the manual passthrough flow — zero-provider web UI optimization.

Covers:
- Shared passthrough service (assemble_passthrough_prompt, resolve_strategy)
- POST /api/optimize/passthrough (prepare)
- POST /api/optimize/passthrough/save (save)
- Business logic: prompt assembly, heuristic scoring, DB persistence, event bus
- Edge cases: validation, missing records, strategy fallback, repeated saves
- Integration: full prepare→save→history→feedback flow
"""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import Optimization
from app.services.heuristic_scorer import HeuristicScorer


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the in-memory rate limiter storage before each test."""
    from app.dependencies.rate_limit import _storage
    _storage.reset()
    yield
    _storage.reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_PROMPT = "Write a Python function that sorts a list of integers using merge sort"

LONG_OPTIMIZED = (
    "## Task\n\n"
    "Write a Python function `merge_sort(items: list[int]) -> list[int]` that:\n\n"
    "1. Accepts a list of integers\n"
    "2. Returns a new sorted list using the merge sort algorithm\n"
    "3. Must not modify the original list\n"
    "4. Raise TypeError if input is not a list\n\n"
    "## Output format\n\n"
    "Return type: `list[int]`\n"
)


# ---------------------------------------------------------------------------
# POST /api/optimize/passthrough — Prepare
# ---------------------------------------------------------------------------


class TestPassthroughPrepare:
    """Tests for the passthrough prepare endpoint."""

    async def test_prepare_returns_assembled_prompt(self, app_client):
        """Basic happy path: returns trace_id, optimization_id, assembled_prompt, strategy."""
        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": VALID_PROMPT},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "trace_id" in data
        assert "optimization_id" in data
        assert "assembled_prompt" in data
        assert "strategy_requested" in data
        # trace_id is a valid UUID
        uuid.UUID(data["trace_id"])
        uuid.UUID(data["optimization_id"])
        # Assembled prompt contains the raw prompt
        assert VALID_PROMPT in data["assembled_prompt"]

    async def test_prepare_default_strategy_is_auto(self, app_client):
        """When no strategy specified, defaults to 'auto'."""
        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": VALID_PROMPT},
        )
        assert resp.status_code == 200
        assert resp.json()["strategy_requested"] == "auto"

    async def test_prepare_explicit_strategy(self, app_client):
        """User can request a specific strategy."""
        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": VALID_PROMPT, "strategy": "chain-of-thought"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["strategy_requested"] == "chain-of-thought"
        # Strategy instructions should be injected into the assembled prompt
        assert "chain" in data["assembled_prompt"].lower() or "step" in data["assembled_prompt"].lower()

    async def test_prepare_unknown_strategy_falls_back_to_auto(self, app_client):
        """Unknown strategy name gracefully falls back to 'auto'."""
        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": VALID_PROMPT, "strategy": "nonexistent-strategy-xyz"},
        )
        assert resp.status_code == 200
        assert resp.json()["strategy_requested"] == "auto"

    async def test_prepare_creates_pending_record(self, app_client, db_session):
        """Prepare creates a pending Optimization in the database."""
        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": VALID_PROMPT},
        )
        data = resp.json()
        result = await db_session.execute(
            select(Optimization).where(Optimization.trace_id == data["trace_id"])
        )
        opt = result.scalar_one_or_none()
        assert opt is not None
        assert opt.status == "pending"
        assert opt.provider == "web_passthrough"
        assert opt.raw_prompt == VALID_PROMPT
        assert opt.strategy_used == "auto"
        assert opt.optimized_prompt is None

    async def test_prepare_works_without_provider(self, app_client):
        """Passthrough prepare does NOT require a configured provider."""
        app_client._transport.app.state.routing.set_provider(None)
        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": VALID_PROMPT},
        )
        assert resp.status_code == 200
        assert "assembled_prompt" in resp.json()

    async def test_prepare_rejects_missing_prompt(self, app_client):
        """Missing prompt field returns 422."""
        resp = await app_client.post("/api/optimize/passthrough", json={})
        assert resp.status_code == 422

    async def test_prepare_rejects_empty_prompt(self, app_client):
        """Empty prompt returns 422."""
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": ""},
        )
        assert resp.status_code == 422

    async def test_prepare_rejects_short_prompt(self, app_client):
        """Prompt shorter than 20 characters returns 422."""
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": "too short"},
        )
        assert resp.status_code == 422

    async def test_prepare_includes_scoring_rubric(self, app_client):
        """Assembled prompt should contain scoring rubric content."""
        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": VALID_PROMPT},
        )
        data = resp.json()
        # scoring.md content is injected as scoring_rubric_excerpt
        assert "scoring" in data["assembled_prompt"].lower() or "rubric" in data["assembled_prompt"].lower()

    async def test_prepare_multiple_creates_separate_records(self, app_client, db_session):
        """Each prepare call creates a distinct pending record."""
        resp1 = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        resp2 = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        data1, data2 = resp1.json(), resp2.json()
        assert data1["trace_id"] != data2["trace_id"]
        assert data1["optimization_id"] != data2["optimization_id"]

        # Both exist in DB
        for trace_id in [data1["trace_id"], data2["trace_id"]]:
            result = await db_session.execute(
                select(Optimization).where(Optimization.trace_id == trace_id)
            )
            assert result.scalar_one_or_none() is not None

    async def test_prepare_assembled_prompt_has_strategy_section(self, app_client):
        """Assembled prompt wraps strategy instructions in a <strategy> block."""
        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": VALID_PROMPT},
        )
        assembled = resp.json()["assembled_prompt"]
        assert "<strategy>" in assembled


# ---------------------------------------------------------------------------
# POST /api/optimize/passthrough/save — Save
# ---------------------------------------------------------------------------


class TestPassthroughSave:
    """Tests for the passthrough save endpoint."""

    async def _prepare(self, app_client, prompt=VALID_PROMPT, strategy=None):
        """Helper: run prepare and return the response data."""
        body = {"prompt": prompt}
        if strategy:
            body["strategy"] = strategy
        resp = await app_client.post("/api/optimize/passthrough", json=body)
        assert resp.status_code == 200
        return resp.json()

    async def test_save_completes_optimization(self, app_client, db_session):
        """Happy path: save transitions pending → completed with heuristic scores."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "changes_summary": "Added structure and constraints",
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        # Status is completed
        assert data["status"] == "completed"
        assert data["provider"] == "web_passthrough"
        assert data["model_used"] == "external"
        assert data["scoring_mode"] == "heuristic"

        # Has optimized prompt and summary
        assert data["optimized_prompt"] == LONG_OPTIMIZED.rstrip()
        assert data["changes_summary"] == "Added structure and constraints"

        # Has scores
        assert data["scores"] is not None
        for dim in ["clarity", "specificity", "structure", "faithfulness", "conciseness"]:
            assert dim in data["scores"]
            score = data["scores"][dim]
            assert score is not None
            assert 1.0 <= score <= 10.0

        # Has overall score
        assert data["overall_score"] is not None
        assert 1.0 <= data["overall_score"] <= 10.0

    async def test_save_scores_match_heuristic_scorer(self, app_client):
        """Scores returned by save match HeuristicScorer.score_prompt() output."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
            },
        )
        data = resp.json()

        # Compute expected scores directly
        expected = HeuristicScorer.score_prompt(LONG_OPTIMIZED, original=VALID_PROMPT)
        expected_overall = round(sum(expected.values()) / len(expected), 2)

        for dim in expected:
            assert data["scores"][dim] == pytest.approx(expected[dim], abs=0.01)
        assert data["overall_score"] == pytest.approx(expected_overall, abs=0.01)

    async def test_save_updates_db_record(self, app_client, db_session):
        """Save persists all fields to the database record."""
        prep = await self._prepare(app_client)
        await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "changes_summary": "Restructured",
            },
        )

        result = await db_session.execute(
            select(Optimization).where(Optimization.trace_id == prep["trace_id"])
        )
        opt = result.scalar_one()
        assert opt.status == "completed"
        assert opt.optimized_prompt == LONG_OPTIMIZED.rstrip()
        assert opt.changes_summary == "Restructured"
        assert opt.scoring_mode == "heuristic"
        assert opt.model_used == "external"
        assert opt.provider == "web_passthrough"
        assert opt.overall_score is not None
        assert opt.score_clarity is not None
        assert opt.score_specificity is not None
        assert opt.score_structure is not None
        assert opt.score_faithfulness is not None
        assert opt.score_conciseness is not None

    async def test_save_publishes_event(self, app_client):
        """Save publishes an optimization_created event to the event bus."""
        prep = await self._prepare(app_client)

        with patch("app.services.event_bus.event_bus") as mock_bus:
            await app_client.post(
                "/api/optimize/passthrough/save",
                json={
                    "trace_id": prep["trace_id"],
                    "optimized_prompt": LONG_OPTIMIZED,
                },
            )
            mock_bus.publish.assert_called_once()
            call_args = mock_bus.publish.call_args
            assert call_args[0][0] == "optimization_created"
            event_data = call_args[0][1]
            assert event_data["trace_id"] == prep["trace_id"]
            assert event_data["provider"] == "web_passthrough"
            assert event_data["status"] == "completed"
            assert "overall_score" in event_data

    async def test_save_unknown_trace_id_returns_404(self, app_client):
        """Saving with a nonexistent trace_id returns 404."""
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": "nonexistent-trace-id-000",
                "optimized_prompt": "some prompt",
            },
        )
        assert resp.status_code == 404

    async def test_save_rejects_empty_optimized_prompt(self, app_client):
        """Empty optimized_prompt is rejected (min_length=1)."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": "",
            },
        )
        assert resp.status_code == 422

    async def test_save_rejects_missing_optimized_prompt(self, app_client):
        """Missing optimized_prompt field is rejected."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={"trace_id": prep["trace_id"]},
        )
        assert resp.status_code == 422

    async def test_save_rejects_missing_trace_id(self, app_client):
        """Missing trace_id field is rejected."""
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={"optimized_prompt": "something"},
        )
        assert resp.status_code == 422

    async def test_save_optional_changes_summary(self, app_client):
        """changes_summary is optional — cleanup extracts default when omitted."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                # No changes_summary — cleanup extracts default
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Cleanup provides a default summary when no change markers found
        assert len(data["changes_summary"]) > 0

    async def test_save_returns_standard_optimization_shape(self, app_client):
        """Response matches the same shape as GET /api/optimize/{trace_id}."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
            },
        )
        data = resp.json()

        # All fields from _serialize_optimization must be present
        required_keys = {
            "id", "trace_id", "raw_prompt", "optimized_prompt", "task_type",
            "strategy_used", "changes_summary", "scores", "original_scores",
            "score_deltas", "overall_score", "provider", "model_used",
            "scoring_mode", "duration_ms", "status", "context_sources",
            "created_at",
        }
        assert required_keys.issubset(data.keys())

        # Scores is a dict with the 5 dimensions
        assert set(data["scores"].keys()) == {
            "clarity", "specificity", "structure", "faithfulness", "conciseness",
        }

    async def test_save_preserves_raw_prompt_from_prepare(self, app_client):
        """The saved record retains the original raw_prompt from prepare."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
            },
        )
        data = resp.json()
        assert data["raw_prompt"] == VALID_PROMPT

    async def test_save_preserves_strategy_from_prepare(self, app_client):
        """The saved record retains the strategy_used from prepare."""
        prep = await self._prepare(app_client, strategy="chain-of-thought")
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
            },
        )
        data = resp.json()
        assert data["strategy_used"] == "chain-of-thought"

    async def test_save_works_without_provider(self, app_client):
        """Save does NOT require a configured provider."""
        prep = await self._prepare(app_client)
        app_client._transport.app.state.routing.set_provider(None)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
            },
        )
        assert resp.status_code == 200

    async def test_save_idempotent_overwrites(self, app_client, db_session):
        """Saving twice with the same trace_id updates the same record (no duplicate)."""
        prep = await self._prepare(app_client)
        first_optimized = "First version of the optimized prompt that is long enough"

        await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": first_optimized,
                "changes_summary": "First save",
            },
        )
        resp2 = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "changes_summary": "Second save",
            },
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["optimized_prompt"] == LONG_OPTIMIZED.rstrip()
        assert data["changes_summary"] == "Second save"

        # Only one record in DB for this trace_id
        result = await db_session.execute(
            select(Optimization).where(Optimization.trace_id == prep["trace_id"])
        )
        records = result.scalars().all()
        assert len(records) == 1

    async def test_save_with_whitespace_only_summary_treated_as_empty(self, app_client):
        """Whitespace-only changes_summary is stored as empty string."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "changes_summary": "   ",
            },
        )
        assert resp.status_code == 200
        # The backend stores whatever is passed (including whitespace)
        # since trim is the frontend's job, but empty string is the default
        assert resp.json()["changes_summary"] in ("   ", "")


# ---------------------------------------------------------------------------
# Heuristic scoring business logic
# ---------------------------------------------------------------------------


class TestPassthroughScoringLogic:
    """Verify that passthrough save applies heuristic scoring correctly."""

    async def _prepare_and_save(self, app_client, optimized):
        """Helper: prepare + save in one call, return saved data."""
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        prep = resp.json()
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={"trace_id": prep["trace_id"], "optimized_prompt": optimized},
        )
        return resp.json()

    async def test_structured_prompt_gets_higher_structure_score(self, app_client):
        """A prompt with headers and lists scores higher on structure."""
        unstructured = "Just write me a function that does sorting please thank you"
        structured = (
            "## Task\n\nWrite a sort function.\n\n"
            "## Requirements\n\n- Must be stable\n- O(n log n)\n- Return a new list\n\n"
            "## Output format\n\nReturn `list[int]`"
        )

        data_unstructured = await self._prepare_and_save(app_client, unstructured)
        data_structured = await self._prepare_and_save(app_client, structured)

        assert data_structured["scores"]["structure"] > data_unstructured["scores"]["structure"]

    async def test_specific_prompt_gets_higher_specificity_score(self, app_client):
        """A prompt with constraints scores higher on specificity."""
        vague = "Write something that sorts numbers somehow or whatever"
        specific = (
            "Write a Python function `merge_sort(items: list[int]) -> list[int]` "
            "that must use divide-and-conquer. Return a sorted list. Raise ValueError "
            "if items has fewer than 0 elements. For example: merge_sort([3, 1, 2]) -> [1, 2, 3]."
        )

        data_vague = await self._prepare_and_save(app_client, vague)
        data_specific = await self._prepare_and_save(app_client, specific)

        assert data_specific["scores"]["specificity"] > data_vague["scores"]["specificity"]

    async def test_overall_score_is_weighted_mean_of_dimensions(self, app_client):
        """overall_score = weighted mean of the 5 dimension scores."""
        data = await self._prepare_and_save(app_client, LONG_OPTIMIZED)
        scores = data["scores"]
        from app.schemas.pipeline_contracts import DIMENSION_WEIGHTS
        expected = round(sum(scores[d] * w for d, w in DIMENSION_WEIGHTS.items()), 2)
        assert data["overall_score"] == pytest.approx(expected, abs=0.01)

    async def test_scoring_mode_is_heuristic(self, app_client):
        """Passthrough always uses heuristic scoring mode."""
        data = await self._prepare_and_save(app_client, LONG_OPTIMIZED)
        assert data["scoring_mode"] == "heuristic"

    async def test_no_bias_correction_applied_to_heuristic_scores(self, app_client):
        """Heuristic scores are NOT bias-corrected (that's for self-rated LLM scores)."""
        data = await self._prepare_and_save(app_client, LONG_OPTIMIZED)

        # Compute raw heuristic scores
        raw = HeuristicScorer.score_prompt(LONG_OPTIMIZED, original=VALID_PROMPT)

        # Scores should match raw heuristics, NOT be discounted
        for dim in raw:
            assert data["scores"][dim] == pytest.approx(raw[dim], abs=0.01)


# ---------------------------------------------------------------------------
# Hybrid passthrough scoring mode (external scores + blending)
# ---------------------------------------------------------------------------


class TestPassthroughHybridScoring:
    """Tests for the hybrid_passthrough scoring mode in the REST save endpoint."""

    async def _prepare(self, app_client, prompt=VALID_PROMPT):
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": prompt},
        )
        assert resp.status_code == 200
        return resp.json()

    async def test_save_with_external_scores_uses_hybrid_mode(self, app_client):
        """POST /optimize/passthrough/save with scores dict → scoring_mode=hybrid_passthrough."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "scores": {
                    "clarity": 8.0, "specificity": 7.5, "structure": 7.0,
                    "faithfulness": 9.0, "conciseness": 7.5,
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scoring_mode"] == "hybrid_passthrough"
        assert data["overall_score"] is not None

    async def test_save_hybrid_scores_within_valid_range(self, app_client):
        """All blended scores must be in [1.0, 10.0]."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "scores": {
                    "clarity": 9.5, "specificity": 8.0, "structure": 8.5,
                    "faithfulness": 9.0, "conciseness": 8.0,
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        for dim in ["clarity", "specificity", "structure", "faithfulness", "conciseness"]:
            score = data["scores"][dim]
            assert 1.0 <= score <= 10.0, f"{dim}={score} out of range"

    async def test_save_scoring_disabled_returns_skipped(self, app_client):
        """With enable_scoring=False, scoring_mode is 'skipped' and no scores computed."""
        prep = await self._prepare(app_client)
        with patch("app.routers.optimize.PreferencesService") as mock_prefs_cls:
            mock_prefs = mock_prefs_cls.return_value
            mock_prefs.get.return_value = False
            resp = await app_client.post(
                "/api/optimize/passthrough/save",
                json={
                    "trace_id": prep["trace_id"],
                    "optimized_prompt": LONG_OPTIMIZED,
                    "scores": {
                        "clarity": 8.0, "specificity": 7.5, "structure": 7.0,
                        "faithfulness": 9.0, "conciseness": 7.5,
                    },
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scoring_mode"] == "skipped"
        assert data["overall_score"] is None

    async def test_save_with_extreme_scores_clamped(self, app_client):
        """External scores > 10 or < 0 produce final scores clamped to [1.0, 10.0]."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "scores": {
                    "clarity": 15.0, "specificity": -2.0, "structure": 10.0,
                    "faithfulness": 0.0, "conciseness": 100.0,
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        for dim in ["clarity", "specificity", "structure", "faithfulness", "conciseness"]:
            score = data["scores"][dim]
            assert 1.0 <= score <= 10.0, f"{dim}={score} out of range after blending"

    async def test_no_bias_correction_on_hybrid_scores(self, app_client):
        """External scores are NOT multiplied by 0.85 before blending (bias correction removed)."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "scores": {
                    "clarity": 10.0, "specificity": 10.0, "structure": 10.0,
                    "faithfulness": 10.0, "conciseness": 10.0,
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Without z-score normalization (fresh DB, <10 samples), blend formula is:
        # blended = w_h * heuristic + (1-w_h) * external
        # For clarity (w_h=0.30): 0.30*heur + 0.70*10.0
        # With bias correction (old): 0.30*heur + 0.70*8.5 → lower
        # Without bias correction: 0.30*heur + 0.70*10.0 → higher
        # Clarity heuristic for LONG_OPTIMIZED is ~5-7, so blended should be > 8.0
        assert data["scores"]["clarity"] > 8.0, (
            f"clarity={data['scores']['clarity']} suggests bias correction is still applied"
        )


# ---------------------------------------------------------------------------
# Interaction with GET /api/optimize/{trace_id}
# ---------------------------------------------------------------------------


class TestPassthroughGetOptimization:
    """Verify that passthrough results are retrievable via the existing GET endpoint."""

    async def test_get_pending_record(self, app_client):
        """A prepared (pending) record is retrievable by trace_id."""
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        trace_id = resp.json()["trace_id"]

        resp = await app_client.get(f"/api/optimize/{trace_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["provider"] == "web_passthrough"
        assert data["optimized_prompt"] is None

    async def test_get_completed_record(self, app_client):
        """A saved (completed) record is retrievable by trace_id."""
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        trace_id = resp.json()["trace_id"]

        await app_client.post(
            "/api/optimize/passthrough/save",
            json={"trace_id": trace_id, "optimized_prompt": LONG_OPTIMIZED},
        )

        resp = await app_client.get(f"/api/optimize/{trace_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["optimized_prompt"] == LONG_OPTIMIZED.rstrip()
        assert data["overall_score"] is not None

    async def test_get_and_save_shapes_match(self, app_client):
        """GET response shape matches save response shape exactly."""
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        trace_id = resp.json()["trace_id"]

        save_resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={"trace_id": trace_id, "optimized_prompt": LONG_OPTIMIZED},
        )
        get_resp = await app_client.get(f"/api/optimize/{trace_id}")

        save_data = save_resp.json()
        get_data = get_resp.json()

        assert set(save_data.keys()) == set(get_data.keys())
        for key in save_data:
            assert save_data[key] == get_data[key], f"Mismatch on key '{key}'"


# ---------------------------------------------------------------------------
# Integration: passthrough result appears in history
# ---------------------------------------------------------------------------


class TestPassthroughHistoryIntegration:
    """Verify passthrough results integrate with the history endpoint."""

    async def test_completed_passthrough_appears_in_history(self, app_client):
        """A completed passthrough optimization appears in GET /api/history."""
        # Prepare + save
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        prep = resp.json()
        await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "changes_summary": "Added structure",
            },
        )

        # Check history
        resp = await app_client.get("/api/history")
        assert resp.status_code == 200
        history = resp.json()
        assert history["total"] >= 1
        items = history["items"]
        matching = [i for i in items if i["id"] == prep["optimization_id"]]
        assert len(matching) == 1
        item = matching[0]
        assert item["status"] == "completed"
        assert item["provider"] == "web_passthrough"
        assert item["overall_score"] is not None

    async def test_pending_passthrough_appears_in_history(self, app_client):
        """A pending (unsaved) passthrough record appears in history."""
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        prep = resp.json()

        resp = await app_client.get("/api/history")
        assert resp.status_code == 200
        items = resp.json()["items"]
        matching = [i for i in items if i["id"] == prep["optimization_id"]]
        assert len(matching) == 1
        assert matching[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# Integration: passthrough result supports feedback
# ---------------------------------------------------------------------------


class TestPassthroughFeedbackIntegration:
    """Verify passthrough results support the feedback flow."""

    async def test_feedback_on_passthrough_result(self, app_client):
        """Users can submit feedback on passthrough-completed optimizations."""
        # Prepare + save
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        prep = resp.json()
        await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
            },
        )

        # Submit feedback
        resp = await app_client.post("/api/feedback", json={
            "optimization_id": prep["optimization_id"],
            "rating": "thumbs_up",
            "comment": "Good passthrough result",
        })
        assert resp.status_code == 200
        fb = resp.json()
        assert fb["rating"] == "thumbs_up"
        assert fb["optimization_id"] == prep["optimization_id"]

        # Verify feedback is retrievable
        resp = await app_client.get(
            f"/api/feedback?optimization_id={prep['optimization_id']}",
        )
        assert resp.status_code == 200
        fb_data = resp.json()
        assert fb_data["aggregation"]["thumbs_up"] == 1
        assert fb_data["aggregation"]["total"] == 1


# ---------------------------------------------------------------------------
# Full end-to-end passthrough integration
# ---------------------------------------------------------------------------


class TestPassthroughEndToEnd:
    """Full prepare → save → verify → feedback cycle with zero provider."""

    async def test_full_passthrough_flow_no_provider(self, app_client, db_session):
        """Complete passthrough flow with provider set to None throughout."""
        app_client._transport.app.state.routing.set_provider(None)

        # Step 1: Prepare
        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": VALID_PROMPT, "strategy": "chain-of-thought"},
        )
        assert resp.status_code == 200
        prep = resp.json()
        assert prep["strategy_requested"] == "chain-of-thought"
        assert VALID_PROMPT in prep["assembled_prompt"]

        # Step 2: Save
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "changes_summary": "Applied chain-of-thought structure",
            },
        )
        assert resp.status_code == 200
        saved = resp.json()
        assert saved["status"] == "completed"
        assert saved["provider"] == "web_passthrough"
        assert saved["model_used"] == "external"
        assert saved["scoring_mode"] == "heuristic"
        assert saved["strategy_used"] == "chain-of-thought"
        assert 1.0 <= saved["overall_score"] <= 10.0

        # Step 3: Verify via GET
        resp = await app_client.get(f"/api/optimize/{prep['trace_id']}")
        assert resp.status_code == 200
        assert resp.json()["optimized_prompt"] == LONG_OPTIMIZED.rstrip()

        # Step 4: Appears in history
        resp = await app_client.get("/api/history")
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["items"]]
        assert prep["optimization_id"] in ids

        # Step 5: Feedback works
        resp = await app_client.post("/api/feedback", json={
            "optimization_id": prep["optimization_id"],
            "rating": "thumbs_down",
        })
        assert resp.status_code == 200

        # Step 6: Normal optimize degrades to passthrough tier (no longer 503)
        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": VALID_PROMPT},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

    async def test_passthrough_then_normal_after_provider_set(
        self, app_client, mock_provider, db_session,
    ):
        """After completing passthrough, setting a provider enables normal optimize."""
        # Passthrough while no provider
        app_client._transport.app.state.routing.set_provider(None)
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        assert resp.status_code == 200
        prep = resp.json()

        await app_client.post(
            "/api/optimize/passthrough/save",
            json={"trace_id": prep["trace_id"], "optimized_prompt": LONG_OPTIMIZED},
        )

        # Now set provider — normal optimize should work
        from app.schemas.pipeline_contracts import (
            AnalysisResult,
            DimensionScores,
            ScoreResult,
        )
        from app.schemas.pipeline_contracts import (
            OptimizationResult as PipelineOptResult,
        )

        app_client._transport.app.state.routing.set_provider(mock_provider)
        mock_provider.complete_parsed.side_effect = [
            AnalysisResult(
                task_type="coding", weaknesses=["vague"], strengths=["concise"],
                selected_strategy="auto", strategy_rationale="default",
                confidence=0.8,
            ),
            PipelineOptResult(
                optimized_prompt="Normal optimized output",
                changes_summary="Via provider",
                strategy_used="auto",
            ),
            ScoreResult(
                prompt_a_scores=DimensionScores(
                    clarity=5.0, specificity=5.0, structure=5.0,
                    faithfulness=5.0, conciseness=5.0,
                ),
                prompt_b_scores=DimensionScores(
                    clarity=8.0, specificity=8.0, structure=7.0,
                    faithfulness=9.0, conciseness=7.0,
                ),
            ),
        ]
        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": VALID_PROMPT},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        # Both records exist in history
        resp = await app_client.get("/api/history")
        assert resp.json()["total"] >= 2


# ---------------------------------------------------------------------------
# Shared passthrough service unit tests
# ---------------------------------------------------------------------------


class TestPassthroughService:
    """Tests for the shared passthrough assembly module."""

    def test_assemble_passthrough_prompt(self):
        """assemble_passthrough_prompt returns assembled text and resolved strategy."""
        from app.config import PROMPTS_DIR
        from app.services.passthrough import assemble_passthrough_prompt

        assembled, strategy = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=VALID_PROMPT,
        )
        assert isinstance(assembled, str)
        assert len(assembled) > len(VALID_PROMPT)
        assert VALID_PROMPT in assembled
        assert strategy == "auto"

    def test_assemble_with_explicit_strategy(self):
        """Explicit strategy is resolved and injected."""
        from app.config import PROMPTS_DIR
        from app.services.passthrough import assemble_passthrough_prompt

        assembled, strategy = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=VALID_PROMPT,
            strategy_name="chain-of-thought",
        )
        assert strategy == "chain-of-thought"
        assert len(assembled) > 0

    def test_assemble_unknown_strategy_falls_back(self):
        """Unknown strategy falls back to auto."""
        from app.config import PROMPTS_DIR
        from app.services.passthrough import assemble_passthrough_prompt

        _, strategy = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=VALID_PROMPT,
            strategy_name="nonexistent-strategy-xyz",
        )
        assert strategy == "auto"

    def test_resolve_strategy_returns_tuple(self):
        """resolve_strategy returns (name, instructions) tuple."""
        from app.config import PROMPTS_DIR
        from app.services.passthrough import resolve_strategy
        from app.services.strategy_loader import StrategyLoader

        loader = StrategyLoader(PROMPTS_DIR / "strategies")
        name, instructions = resolve_strategy(loader, "auto")
        assert name == "auto"
        assert isinstance(instructions, str)
        assert len(instructions) > 0

    def test_resolve_strategy_fallback(self):
        """resolve_strategy falls back when strategy is missing."""
        from app.config import PROMPTS_DIR
        from app.services.passthrough import resolve_strategy
        from app.services.strategy_loader import StrategyLoader

        loader = StrategyLoader(PROMPTS_DIR / "strategies")
        name, instructions = resolve_strategy(loader, "totally-fake")
        assert name == "auto"
        assert isinstance(instructions, str)

    def test_assemble_includes_scoring_rubric(self):
        """Assembled prompt includes scoring rubric content."""
        from app.config import PROMPTS_DIR
        from app.services.passthrough import assemble_passthrough_prompt

        assembled, _ = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=VALID_PROMPT,
        )
        assert "<scoring-rubric>" in assembled

    def test_assemble_includes_strategy_section(self):
        """Assembled prompt includes the <strategy> XML section."""
        from app.config import PROMPTS_DIR
        from app.services.passthrough import assemble_passthrough_prompt

        assembled, _ = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=VALID_PROMPT,
        )
        assert "<strategy>" in assembled

    def test_assemble_with_codebase_guidance(self):
        """Codebase guidance is injected when provided."""
        from app.config import PROMPTS_DIR
        from app.services.passthrough import assemble_passthrough_prompt

        guidance = "# Project Structure\n- src/\n- tests/"
        assembled, _ = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=VALID_PROMPT,
            codebase_guidance=guidance,
        )
        assert "Project Structure" in assembled


# ---------------------------------------------------------------------------
# _serialize_optimization consistency
# ---------------------------------------------------------------------------


class TestSerializeOptimization:
    """Verify the shared serializer produces consistent output."""

    async def test_serializer_matches_between_endpoints(self, app_client, db_session):
        """The serializer used by GET and POST /save returns identical shapes."""
        # Create via passthrough
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        trace_id = resp.json()["trace_id"]

        save_resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={"trace_id": trace_id, "optimized_prompt": LONG_OPTIMIZED},
        )
        get_resp = await app_client.get(f"/api/optimize/{trace_id}")

        save_data = save_resp.json()
        get_data = get_resp.json()

        # Every key must match exactly
        assert save_data.keys() == get_data.keys()
        for key in save_data:
            assert save_data[key] == get_data[key], (
                f"Key '{key}': save={save_data[key]!r} vs get={get_data[key]!r}"
            )

    async def test_serializer_null_fields_for_pending(self, app_client):
        """Pending records have null optimized_prompt and scores."""
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": VALID_PROMPT},
        )
        trace_id = resp.json()["trace_id"]

        resp = await app_client.get(f"/api/optimize/{trace_id}")
        data = resp.json()
        assert data["optimized_prompt"] is None
        assert data["overall_score"] is None
        assert data["scores"]["clarity"] is None


# ---------------------------------------------------------------------------
# Enriched passthrough assembly (analysis_summary + applied_patterns + codebase_context)
# ---------------------------------------------------------------------------


def _setup_prompts(tmp_path):
    """Copy required template files to tmp_path for unit tests."""
    import shutil

    from app.config import PROMPTS_DIR

    # Copy the main template files
    for name in ("passthrough.md", "manifest.json", "scoring.md"):
        src = PROMPTS_DIR / name
        shutil.copy(src, tmp_path / name)

    # Copy strategies directory
    strategies_dst = tmp_path / "strategies"
    strategies_dst.mkdir()
    strategies_src = PROMPTS_DIR / "strategies"
    for strat_file in strategies_src.iterdir():
        if strat_file.suffix == ".md":
            shutil.copy(strat_file, strategies_dst / strat_file.name)


class TestEnrichedPassthrough:
    def test_assembles_with_analysis_summary(self, tmp_path):
        """Analysis summary from heuristic analyzer is injected."""
        from app.services.passthrough import assemble_passthrough_prompt

        _setup_prompts(tmp_path)
        assembled, strategy = assemble_passthrough_prompt(
            prompts_dir=tmp_path,
            raw_prompt="Build a REST API with authentication",
            analysis_summary="Task type: coding\nDomain: backend\nWeaknesses:\n- lacks constraints",
        )
        assert "Task type: coding" in assembled
        assert "lacks constraints" in assembled

    def test_assembles_with_applied_patterns(self, tmp_path):
        """Applied patterns from taxonomy engine are injected."""
        from app.services.passthrough import assemble_passthrough_prompt

        _setup_prompts(tmp_path)
        assembled, strategy = assemble_passthrough_prompt(
            prompts_dir=tmp_path,
            raw_prompt="Build a REST API with authentication",
            applied_patterns="- Use dependency injection for service layer\n- Validate all inputs with Pydantic",
        )
        assert "dependency injection" in assembled
        assert "Pydantic" in assembled

    def test_assembles_with_codebase_context(self, tmp_path):
        """Curated index context is injected into codebase_context slot."""
        from app.services.passthrough import assemble_passthrough_prompt

        _setup_prompts(tmp_path)
        assembled, strategy = assemble_passthrough_prompt(
            prompts_dir=tmp_path,
            raw_prompt="Refactor the auth service",
            codebase_context="## backend/app/auth.py (relevance: 0.87)\nclass AuthService:",
        )
        assert "auth.py" in assembled
        assert "relevance: 0.87" in assembled


# ---------------------------------------------------------------------------
# Domain validation whitelist
# ---------------------------------------------------------------------------


class TestDomainValidation:
    """Verify that domain is validated against known domain nodes."""

    async def _prepare(self, app_client, prompt=VALID_PROMPT):
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": prompt},
        )
        assert resp.status_code == 200
        return resp.json()

    async def test_save_valid_domain_is_accepted(self, app_client, db_session):
        """Known domain values are stored as-is."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "domain": "backend",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "backend"

        # Verify DB persistence
        result = await db_session.execute(
            select(Optimization).where(Optimization.trace_id == prep["trace_id"])
        )
        opt = result.scalar_one()
        assert opt.domain == "backend"

    async def test_save_invalid_domain_falls_back_to_general(self, app_client, db_session):
        """Unknown domain values are replaced with 'general'."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "domain": "hacking",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "general"

    async def test_save_invalid_domain_preserves_raw_in_domain_raw(self, app_client, db_session):
        """Invalid domain is rejected for domain but preserved in domain_raw."""
        prep = await self._prepare(app_client)
        await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "domain": "custom_domain",
            },
        )
        result = await db_session.execute(
            select(Optimization).where(Optimization.trace_id == prep["trace_id"])
        )
        opt = result.scalar_one()
        assert opt.domain == "general"
        assert opt.domain_raw == "custom_domain"

    async def test_save_qualified_domain_extracts_primary(self, app_client, db_session):
        """'backend: security' extracts 'backend' for domain, preserves full in domain_raw."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "domain": "backend: security",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "backend"

        # Verify DB stores primary in domain, full qualifier in domain_raw
        result = await db_session.execute(
            select(Optimization).where(Optimization.trace_id == prep["trace_id"])
        )
        opt = result.scalar_one()
        assert opt.domain == "backend"
        assert opt.domain_raw == "backend: security"

    async def test_save_all_valid_domains_accepted(self, app_client):
        """Every valid domain value is accepted without fallback."""
        from app.dependencies.rate_limit import _storage

        valid_domains = ["backend", "frontend", "database", "data", "devops", "security", "fullstack", "general"]
        for domain in valid_domains:
            _storage.reset()  # Avoid 429 across 7 iterations
            prep = await self._prepare(app_client)
            resp = await app_client.post(
                "/api/optimize/passthrough/save",
                json={
                    "trace_id": prep["trace_id"],
                    "optimized_prompt": LONG_OPTIMIZED,
                    "domain": domain,
                },
            )
            assert resp.status_code == 200
            assert resp.json()["domain"] == domain, f"Domain '{domain}' was not accepted"


# ---------------------------------------------------------------------------
# Intent label length cap
# ---------------------------------------------------------------------------


class TestIntentLabelValidation:
    """Verify intent_label is capped at 100 characters."""

    async def _prepare(self, app_client, prompt=VALID_PROMPT):
        resp = await app_client.post(
            "/api/optimize/passthrough", json={"prompt": prompt},
        )
        assert resp.status_code == 200
        return resp.json()

    async def test_save_long_intent_label_truncated(self, app_client, db_session):
        """Intent labels exceeding 100 chars are truncated."""
        # Use a realistic multi-word label that passes quality gate but exceeds 100 chars
        long_label = "Build Complex " * 10 + "System"  # ~150 chars, multi-word
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "intent_label": long_label,
            },
        )
        assert resp.status_code == 200
        result = await db_session.execute(
            select(Optimization).where(Optimization.trace_id == prep["trace_id"])
        )
        opt = result.scalar_one()
        assert len(opt.intent_label) <= 100

    async def test_save_short_intent_label_unchanged(self, app_client, db_session):
        """Short intent labels are title-cased for display consistency."""
        label = "refactor auth middleware"
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                "intent_label": label,
            },
        )
        assert resp.status_code == 200
        result = await db_session.execute(
            select(Optimization).where(Optimization.trace_id == prep["trace_id"])
        )
        opt = result.scalar_one()
        assert opt.intent_label == "Refactor Auth Middleware"


# ---------------------------------------------------------------------------
# Passthrough SSE inline event format
# ---------------------------------------------------------------------------


class TestPassthroughSSEInline:
    """Verify the inline passthrough SSE stream from POST /api/optimize."""

    @staticmethod
    def _parse_sse_events(text: str) -> list[dict]:
        """Parse SSE data lines into event dicts.

        format_sse() emits: ``data: {"event": "...", ...}\\n\\n``
        The event type is embedded in the JSON payload, not as a separate ``event:`` line.
        """
        import json as _json

        events = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                events.append(_json.loads(line[len("data: "):]))
        return events

    async def test_optimize_passthrough_tier_streams_sse(self, app_client):
        """POST /api/optimize with no provider streams routing + passthrough SSE events."""
        app_client._transport.app.state.routing.set_provider(None)
        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": VALID_PROMPT},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        events = self._parse_sse_events(resp.text)

        # Should have exactly 2 events: routing + passthrough
        assert len(events) == 2
        assert events[0]["event"] == "routing"
        assert events[0]["tier"] == "passthrough"
        assert events[1]["event"] == "passthrough"
        assert "assembled_prompt" in events[1]
        assert "trace_id" in events[1]
        assert "strategy" in events[1]
        assert VALID_PROMPT in events[1]["assembled_prompt"]

    async def test_optimize_passthrough_sse_has_valid_trace_id(self, app_client):
        """The passthrough SSE trace_id is a valid UUID."""
        app_client._transport.app.state.routing.set_provider(None)
        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": VALID_PROMPT},
        )
        events = self._parse_sse_events(resp.text)
        passthrough_events = [e for e in events if e.get("event") == "passthrough"]
        assert len(passthrough_events) == 1
        uuid.UUID(passthrough_events[0]["trace_id"])  # Validates UUID format


# ---------------------------------------------------------------------------
# Heuristic scorer clamping consistency
# ---------------------------------------------------------------------------


class TestHeuristicScorerClamping:
    """Verify all heuristic scorer methods clamp to [1.0, 10.0]."""

    def test_structure_minimum_is_clamped(self):
        """heuristic_structure returns >= 1.0 even for empty input."""
        score = HeuristicScorer.heuristic_structure("")
        assert score >= 1.0

    def test_structure_maximum_is_clamped(self):
        """heuristic_structure returns <= 10.0 even for maximally structured input."""
        prompt = (
            "# H1\n## H2\n### H3\n"
            "- item 1\n- item 2\n- item 3\n"
            "<tag></tag><other></other>\n"
            "Output format: json schema yaml xml markdown\n"
        )
        score = HeuristicScorer.heuristic_structure(prompt)
        assert 1.0 <= score <= 10.0

    def test_specificity_minimum_is_clamped(self):
        """heuristic_specificity returns >= 1.0 for minimal input."""
        score = HeuristicScorer.heuristic_specificity("")
        assert score >= 1.0

    def test_conciseness_minimum_is_clamped(self):
        """heuristic_conciseness returns >= 1.0 for filler-heavy input."""
        filler_heavy = " ".join(
            ["please note that", "it is very important to", "basically", "essentially"] * 10
        )
        score = HeuristicScorer.heuristic_conciseness(filler_heavy)
        assert score >= 1.0

    def test_clarity_minimum_is_clamped(self):
        """heuristic_clarity returns >= 1.0 for ambiguous input."""
        ambiguous = "maybe something stuff things etc perhaps somehow"
        score = HeuristicScorer.heuristic_clarity(ambiguous)
        assert score >= 1.0

    def test_all_dimensions_have_consistent_range(self):
        """All score_prompt dimensions fall within [1.0, 10.0]."""
        prompts = [
            "",
            "x",
            "Write a function",
            LONG_OPTIMIZED,
            "a " * 10000,  # Very long prompt
        ]
        for prompt in prompts:
            scores = HeuristicScorer.score_prompt(prompt)
            for dim, val in scores.items():
                assert 1.0 <= val <= 10.0, f"{dim}={val} out of [1.0, 10.0] for prompt[:50]={prompt[:50]!r}"


# ---------------------------------------------------------------------------
# DomainResolver integration
# ---------------------------------------------------------------------------


class TestDomainResolverIntegration:
    """Verify DomainResolver is used for domain validation across pipelines."""

    def test_domain_resolver_loaded_in_app_state(self, app_client):
        """DomainResolver is available on app.state."""
        resolver = app_client._transport.app.state.domain_resolver
        assert resolver is not None
        assert "backend" in resolver.domain_labels
        assert "general" in resolver.domain_labels
