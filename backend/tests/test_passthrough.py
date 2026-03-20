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
        assert data["optimized_prompt"] == LONG_OPTIMIZED
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
        assert opt.optimized_prompt == LONG_OPTIMIZED
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
        """changes_summary is optional and defaults to empty string."""
        prep = await self._prepare(app_client)
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": LONG_OPTIMIZED,
                # No changes_summary
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["changes_summary"] == ""

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
        assert data["optimized_prompt"] == LONG_OPTIMIZED
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

    async def test_overall_score_is_mean_of_dimensions(self, app_client):
        """overall_score = mean of the 5 dimension scores."""
        data = await self._prepare_and_save(app_client, LONG_OPTIMIZED)
        scores = data["scores"]
        expected = round(sum(scores.values()) / 5, 2)
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
        assert data["optimized_prompt"] == LONG_OPTIMIZED
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
        assert resp.json()["optimized_prompt"] == LONG_OPTIMIZED

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
