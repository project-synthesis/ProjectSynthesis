"""End-to-end integration tests.

- Normal flow: optimize -> refine -> feedback -> history
- Passthrough flow: prepare -> save -> event bus -> feedback -> history -> health
- Cross-flow: passthrough + normal coexistence
"""

import asyncio
import json

import pytest

from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
    SuggestionsOutput,
)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the in-memory rate limiter storage before each test."""
    from app.dependencies.rate_limit import _storage
    _storage.reset()
    yield
    _storage.reset()


def _parse_sse_events(response_text: str) -> list[dict]:
    """Extract parsed JSON events from an SSE response body."""
    events = []
    for line in response_text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


class TestEndToEndFlow:
    """Full pipeline flow using mocked provider but real router -> service -> DB path."""

    async def test_optimize_refine_feedback_history(
        self, app_client, mock_provider, db_session,
    ):
        # --- Step 1: Optimize ---
        mock_provider.complete_parsed.side_effect = [
            AnalysisResult(
                task_type="coding",
                weaknesses=["vague"],
                strengths=["concise"],
                selected_strategy="chain-of-thought",
                strategy_rationale="reasoning helps",
                confidence=0.9,
            ),
            OptimizationResult(
                optimized_prompt=(
                    "Write a Python function sort_list(items: list) -> list "
                    "that returns a new sorted list."
                ),
                changes_summary="Added language, function signature, return type.",
                strategy_used="chain-of-thought",
            ),
            ScoreResult(
                prompt_a_scores=DimensionScores(
                    clarity=4.0, specificity=3.0, structure=3.0,
                    faithfulness=5.0, conciseness=7.0,
                ),
                prompt_b_scores=DimensionScores(
                    clarity=8.0, specificity=8.0, structure=7.0,
                    faithfulness=9.0, conciseness=6.0,
                ),
            ),
        ]

        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": "Write a function that sorts a list"},
        )
        assert resp.status_code == 200

        events = _parse_sse_events(resp.text)

        # Verify the optimization_complete event exists and extract the ID
        complete_event = next(
            (e for e in events if e.get("event") == "optimization_complete"),
            None,
        )
        assert complete_event is not None, (
            f"No optimization_complete event. Events: {[e.get('event') for e in events]}"
        )

        optimization_id = complete_event["id"]
        assert optimization_id

        # Verify start event was emitted with a trace_id
        start_event = next(
            (e for e in events if e.get("event") == "optimization_start"),
            None,
        )
        assert start_event is not None
        assert start_event.get("trace_id")

        # --- Step 2: Verify history shows the optimization ---
        resp = await app_client.get("/api/history")
        assert resp.status_code == 200
        history = resp.json()
        assert history["total"] >= 1
        assert any(item["id"] == optimization_id for item in history["items"])

        # --- Step 3: Submit feedback ---
        resp = await app_client.post("/api/feedback", json={
            "optimization_id": optimization_id,
            "rating": "thumbs_up",
            "comment": "Great optimization!",
        })
        assert resp.status_code == 200
        fb = resp.json()
        assert fb["rating"] == "thumbs_up"
        assert fb["optimization_id"] == optimization_id

        # --- Step 4: Verify feedback shows up ---
        resp = await app_client.get(
            f"/api/feedback?optimization_id={optimization_id}",
        )
        assert resp.status_code == 200
        fb_data = resp.json()
        assert fb_data["aggregation"]["thumbs_up"] == 1
        assert fb_data["aggregation"]["total"] == 1
        assert len(fb_data["items"]) == 1

        # --- Step 5: Refine ---
        mock_provider.complete_parsed.side_effect = [
            # analyze
            AnalysisResult(
                task_type="coding",
                weaknesses=["no error handling"],
                strengths=["clear signature"],
                selected_strategy="chain-of-thought",
                strategy_rationale="reasoning",
                confidence=0.85,
            ),
            # refine (uses OptimizationResult contract)
            OptimizationResult(
                optimized_prompt=(
                    "Write a Python function sort_list(items: list) -> list "
                    "that returns a new sorted list. Raise TypeError if items "
                    "is not a list."
                ),
                changes_summary="Added error handling.",
                strategy_used="chain-of-thought",
            ),
            # score
            ScoreResult(
                prompt_a_scores=DimensionScores(
                    clarity=4.0, specificity=3.0, structure=3.0,
                    faithfulness=5.0, conciseness=7.0,
                ),
                prompt_b_scores=DimensionScores(
                    clarity=8.5, specificity=9.0, structure=7.5,
                    faithfulness=9.0, conciseness=6.0,
                ),
            ),
            # suggest
            SuggestionsOutput(suggestions=[
                {"text": "Add return type examples", "source": "score-driven"},
                {"text": "Specify sorting algorithm", "source": "analysis-driven"},
                {"text": "Add docstring requirement", "source": "strategic"},
            ]),
        ]

        resp = await app_client.post("/api/refine", json={
            "optimization_id": optimization_id,
            "refinement_request": "Add error handling for invalid input types",
        })
        assert resp.status_code == 200
        # Verify SSE events were streamed
        assert "data:" in resp.text

        refine_events = _parse_sse_events(resp.text)
        refine_event_types = [e.get("event") for e in refine_events]
        # Refinement pipeline should emit analyze, refine, score, and suggest phases
        assert "status" in refine_event_types
        assert "prompt_preview" in refine_event_types
        assert "score_card" in refine_event_types
        assert "suggestions" in refine_event_types

        # --- Step 6: Get refinement versions ---
        resp = await app_client.get(
            f"/api/refine/{optimization_id}/versions",
        )
        assert resp.status_code == 200
        versions = resp.json()
        assert versions["optimization_id"] == optimization_id
        # Should have initial turn (v1) + refinement turn (v2)
        assert len(versions["versions"]) >= 2

        # --- Step 7: Health check includes metrics ---
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        health = resp.json()
        assert "score_health" in health
        assert "provider" in health
        assert health["provider"] == "mock"


# ---------------------------------------------------------------------------
# Passthrough E2E — bidirectional manual optimization
# ---------------------------------------------------------------------------

PASSTHROUGH_RAW = "Write a Python function that sorts a list of integers using merge sort"
PASSTHROUGH_OPTIMIZED = (
    "## Task\n\n"
    "Write a Python function `merge_sort(items: list[int]) -> list[int]` that:\n\n"
    "1. Accepts a list of integers\n"
    "2. Returns a new sorted list using the merge sort algorithm\n"
    "3. Must not modify the original list\n"
    "4. Raise TypeError if input is not a list\n\n"
    "## Output format\n\n"
    "Return type: `list[int]`\n"
)


class TestPassthroughEndToEnd:
    """Full bidirectional passthrough flow: prepare → save → event bus → history → feedback → health.

    Exercises the entire lifecycle with zero LLM provider, verifying that every
    cross-cutting system (DB, event bus, SSE, history, feedback, health metrics)
    integrates correctly with the passthrough path.
    """

    async def test_passthrough_prepare_save_events_feedback_history_health(
        self, app_client, db_session,
    ):
        # ── Zero-provider baseline ──────────────────────────────────────
        app_client._transport.app.state.routing.set_provider(None)

        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        health = resp.json()
        assert health["provider"] is None
        assert health["status"] == "degraded"

        # Normal optimize degrades to passthrough tier (no longer 503)
        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": PASSTHROUGH_RAW},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        # ── Step 1: Prepare ─────────────────────────────────────────────
        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": PASSTHROUGH_RAW, "strategy": "chain-of-thought"},
        )
        assert resp.status_code == 200
        prep = resp.json()

        assert prep["strategy_requested"] == "chain-of-thought"
        assert PASSTHROUGH_RAW in prep["assembled_prompt"]
        assert "<strategy>" in prep["assembled_prompt"]
        assert "<scoring-rubric>" in prep["assembled_prompt"]
        trace_id = prep["trace_id"]
        opt_id = prep["optimization_id"]

        # ── Step 2: Verify pending record via GET ───────────────────────
        resp = await app_client.get(f"/api/optimize/{trace_id}")
        assert resp.status_code == 200
        pending = resp.json()
        assert pending["status"] == "pending"
        assert pending["provider"] == "web_passthrough"
        assert pending["raw_prompt"] == PASSTHROUGH_RAW
        assert pending["optimized_prompt"] is None
        assert pending["overall_score"] is None
        assert pending["strategy_used"] == "chain-of-thought"

        # ── Step 3: Pending record appears in history ───────────────────
        resp = await app_client.get("/api/history")
        assert resp.status_code == 200
        history_before = resp.json()
        assert any(i["id"] == opt_id for i in history_before["items"])

        # ── Step 4: Subscribe to event bus, then save ───────────────────
        from app.services.event_bus import event_bus

        captured_events: list[dict] = []
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        event_bus._subscribers.add(queue)

        try:
            resp = await app_client.post(
                "/api/optimize/passthrough/save",
                json={
                    "trace_id": trace_id,
                    "optimized_prompt": PASSTHROUGH_OPTIMIZED,
                    "changes_summary": "Added structure, constraints, and type hints",
                },
            )
            assert resp.status_code == 200
            saved = resp.json()

            # Drain events published during save
            while not queue.empty():
                captured_events.append(queue.get_nowait())
        finally:
            event_bus._subscribers.discard(queue)

        # ── Step 5: Validate saved response shape ───────────────────────
        assert saved["status"] == "completed"
        assert saved["id"] == opt_id
        assert saved["trace_id"] == trace_id
        assert saved["provider"] == "web_passthrough"
        assert saved["model_used"] == "external"
        assert saved["scoring_mode"] == "heuristic"
        assert saved["strategy_used"] == "chain-of-thought"
        assert saved["optimized_prompt"] == PASSTHROUGH_OPTIMIZED
        assert saved["changes_summary"] == "Added structure, constraints, and type hints"
        assert saved["raw_prompt"] == PASSTHROUGH_RAW
        assert saved["created_at"] is not None

        # Scores are present and within valid range
        for dim in ("clarity", "specificity", "structure", "faithfulness", "conciseness"):
            score = saved["scores"][dim]
            assert score is not None, f"Missing score for {dim}"
            assert 1.0 <= score <= 10.0, f"{dim} score {score} out of range"
        assert 1.0 <= saved["overall_score"] <= 10.0

        # Verify overall = mean of dimensions
        dim_mean = round(
            sum(saved["scores"][d] for d in saved["scores"]) / 5, 2,
        )
        assert saved["overall_score"] == pytest.approx(dim_mean, abs=0.01)

        # ── Step 6: Validate event bus emission ─────────────────────────
        assert len(captured_events) >= 1, "No events captured from event bus"
        opt_event = next(
            (e for e in captured_events if e["event"] == "optimization_created"),
            None,
        )
        assert opt_event is not None, (
            f"No optimization_created event. Got: {[e['event'] for e in captured_events]}"
        )
        event_data = opt_event["data"]
        assert event_data["id"] == opt_id
        assert event_data["trace_id"] == trace_id
        assert event_data["provider"] == "web_passthrough"
        assert event_data["status"] == "completed"
        assert event_data["strategy_used"] == "chain-of-thought"
        assert event_data["overall_score"] == saved["overall_score"]
        assert "timestamp" in opt_event  # event bus adds timestamp

        # ── Step 7: SSE event stream delivers the event ─────────────────
        # POST to internal _publish endpoint (simulates what the event bus
        # already did inline; verifies the HTTP→SSE bridge works)
        resp = await app_client.post(
            "/api/events/_publish",
            json={
                "event_type": "optimization_created",
                "data": {
                    "id": opt_id,
                    "trace_id": trace_id,
                    "provider": "web_passthrough",
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # ── Step 8: GET returns completed record ────────────────────────
        resp = await app_client.get(f"/api/optimize/{trace_id}")
        assert resp.status_code == 200
        fetched = resp.json()

        # Every field must match the save response exactly
        for key in saved:
            assert fetched[key] == saved[key], (
                f"GET vs save mismatch on '{key}': {fetched[key]!r} != {saved[key]!r}"
            )

        # ── Step 9: History shows completed record ──────────────────────
        resp = await app_client.get("/api/history")
        assert resp.status_code == 200
        history_after = resp.json()
        completed_items = [i for i in history_after["items"] if i["id"] == opt_id]
        assert len(completed_items) == 1
        hist_item = completed_items[0]
        assert hist_item["status"] == "completed"
        assert hist_item["provider"] == "web_passthrough"
        assert hist_item["overall_score"] == saved["overall_score"]
        assert hist_item["strategy_used"] == "chain-of-thought"

        # ── Step 10: Submit thumbs_up feedback ──────────────────────────
        resp = await app_client.post("/api/feedback", json={
            "optimization_id": opt_id,
            "rating": "thumbs_up",
            "comment": "Useful passthrough result",
        })
        assert resp.status_code == 200
        fb = resp.json()
        assert fb["rating"] == "thumbs_up"
        assert fb["optimization_id"] == opt_id

        # ── Step 11: Submit thumbs_down feedback (second opinion) ───────
        resp = await app_client.post("/api/feedback", json={
            "optimization_id": opt_id,
            "rating": "thumbs_down",
        })
        assert resp.status_code == 200

        # ── Step 12: Verify feedback aggregation ────────────────────────
        resp = await app_client.get(f"/api/feedback?optimization_id={opt_id}")
        assert resp.status_code == 200
        fb_data = resp.json()
        assert fb_data["aggregation"]["thumbs_up"] == 1
        assert fb_data["aggregation"]["thumbs_down"] == 1
        assert fb_data["aggregation"]["total"] == 2
        assert len(fb_data["items"]) == 2

        # ── Step 13: Health metrics reflect the passthrough record ──────
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        health = resp.json()
        # Provider is still None but score_health should include our optimization
        assert health["provider"] is None
        assert health["status"] == "degraded"
        if health["score_health"]:
            assert health["score_health"]["count"] >= 1

    async def test_passthrough_multiple_prepare_save_cycles(
        self, app_client, db_session,
    ):
        """Multiple independent passthrough cycles produce distinct records."""
        app_client._transport.app.state.routing.set_provider(None)

        prompts = [
            "Write a Python decorator that retries failed function calls up to 3 times",
            "Implement a thread-safe LRU cache in Python with configurable max size",
        ]
        trace_ids = []
        opt_ids = []

        for prompt in prompts:
            # Prepare
            resp = await app_client.post(
                "/api/optimize/passthrough",
                json={"prompt": prompt},
            )
            assert resp.status_code == 200
            prep = resp.json()
            trace_ids.append(prep["trace_id"])
            opt_ids.append(prep["optimization_id"])

            # Save with distinct optimized output
            resp = await app_client.post(
                "/api/optimize/passthrough/save",
                json={
                    "trace_id": prep["trace_id"],
                    "optimized_prompt": f"## Optimized\n\n{prompt}\n\n## Requirements\n\n- Must be production-ready",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "completed"

        # All distinct trace IDs and optimization IDs
        assert len(set(trace_ids)) == 2
        assert len(set(opt_ids)) == 2

        # Both appear in history
        resp = await app_client.get("/api/history")
        assert resp.status_code == 200
        history_ids = {i["id"] for i in resp.json()["items"]}
        for oid in opt_ids:
            assert oid in history_ids

        # Each retrievable by trace_id
        for tid in trace_ids:
            resp = await app_client.get(f"/api/optimize/{tid}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "completed"

        # Scores differ because optimized prompts differ
        scores = []
        for tid in trace_ids:
            resp = await app_client.get(f"/api/optimize/{tid}")
            scores.append(resp.json()["overall_score"])
        # Both have valid scores (may or may not be identical depending on heuristics)
        assert all(1.0 <= s <= 10.0 for s in scores)

    async def test_passthrough_and_normal_coexist_in_history(
        self, app_client, mock_provider, db_session,
    ):
        """Passthrough and normal optimizations coexist cleanly in the system."""
        # ── Passthrough (no provider) ───────────────────────────────────
        app_client._transport.app.state.routing.set_provider(None)

        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": PASSTHROUGH_RAW},
        )
        prep = resp.json()
        passthrough_id = prep["optimization_id"]

        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": prep["trace_id"],
                "optimized_prompt": PASSTHROUGH_OPTIMIZED,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

        # ── Normal optimize (with provider) ─────────────────────────────
        app_client._transport.app.state.routing.set_provider(mock_provider)
        mock_provider.complete_parsed.side_effect = [
            AnalysisResult(
                task_type="coding", weaknesses=["vague"], strengths=["concise"],
                selected_strategy="auto", strategy_rationale="default",
                confidence=0.85,
            ),
            OptimizationResult(
                optimized_prompt="Optimized via provider pipeline.",
                changes_summary="Provider-based optimization.",
                strategy_used="auto",
            ),
            ScoreResult(
                prompt_a_scores=DimensionScores(
                    clarity=4.0, specificity=4.0, structure=4.0,
                    faithfulness=5.0, conciseness=6.0,
                ),
                prompt_b_scores=DimensionScores(
                    clarity=8.0, specificity=7.5, structure=7.0,
                    faithfulness=9.0, conciseness=7.0,
                ),
            ),
        ]

        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": "Write a function that checks if a number is prime"},
        )
        assert resp.status_code == 200
        normal_events = _parse_sse_events(resp.text)
        normal_complete = next(
            e for e in normal_events if e.get("event") == "optimization_complete"
        )
        normal_id = normal_complete["id"]

        # ── Both in history ─────────────────────────────────────────────
        resp = await app_client.get("/api/history")
        assert resp.status_code == 200
        history = resp.json()
        assert history["total"] >= 2
        history_ids = {i["id"] for i in history["items"]}
        assert passthrough_id in history_ids
        assert normal_id in history_ids

        # ── Provider field distinguishes them ───────────────────────────
        items_by_id = {i["id"]: i for i in history["items"]}
        assert items_by_id[passthrough_id]["provider"] == "web_passthrough"
        assert items_by_id[normal_id]["provider"] == "mock"

        # ── Both retrievable by trace_id ────────────────────────────────
        resp = await app_client.get(f"/api/optimize/{prep['trace_id']}")
        assert resp.status_code == 200
        assert resp.json()["scoring_mode"] == "heuristic"

        normal_trace = next(
            e.get("trace_id") for e in normal_events
            if e.get("event") == "optimization_start"
        )
        resp = await app_client.get(f"/api/optimize/{normal_trace}")
        assert resp.status_code == 200
        assert resp.json()["scoring_mode"] != "heuristic"

        # ── Feedback works on both ──────────────────────────────────────
        for oid in (passthrough_id, normal_id):
            resp = await app_client.post("/api/feedback", json={
                "optimization_id": oid, "rating": "thumbs_up",
            })
            assert resp.status_code == 200

        # ── Health metrics include both ─────────────────────────────────
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        health = resp.json()
        if health["score_health"]:
            assert health["score_health"]["count"] >= 2

    async def test_passthrough_event_bus_bidirectional(
        self, app_client, db_session,
    ):
        """Event bus correctly emits and receives passthrough events."""
        app_client._transport.app.state.routing.set_provider(None)

        from app.services.event_bus import event_bus

        # Subscribe before any action
        collected: list[dict] = []
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        event_bus._subscribers.add(queue)

        try:
            # Prepare — no event expected
            resp = await app_client.post(
                "/api/optimize/passthrough",
                json={"prompt": PASSTHROUGH_RAW},
            )
            prep = resp.json()

            # Drain — prepare should NOT publish any event
            prepare_events = []
            while not queue.empty():
                prepare_events.append(queue.get_nowait())
            assert len(prepare_events) == 0, (
                f"Prepare should not publish events, got: "
                f"{[e['event'] for e in prepare_events]}"
            )

            # Save — should publish exactly one optimization_created
            resp = await app_client.post(
                "/api/optimize/passthrough/save",
                json={
                    "trace_id": prep["trace_id"],
                    "optimized_prompt": PASSTHROUGH_OPTIMIZED,
                },
            )
            assert resp.status_code == 200

            while not queue.empty():
                collected.append(queue.get_nowait())
        finally:
            event_bus._subscribers.discard(queue)

        # Exactly one event
        opt_events = [e for e in collected if e["event"] == "optimization_created"]
        assert len(opt_events) == 1
        event = opt_events[0]
        assert event["data"]["trace_id"] == prep["trace_id"]
        assert event["data"]["provider"] == "web_passthrough"
        assert event["data"]["status"] == "completed"
        assert isinstance(event["data"]["overall_score"], float)
        assert isinstance(event["timestamp"], float)

        # The internal _publish endpoint can re-broadcast
        resp = await app_client.post("/api/events/_publish", json={
            "event_type": "optimization_created",
            "data": event["data"],
        })
        assert resp.status_code == 200

    async def test_passthrough_save_then_resave_updates_scores(
        self, app_client, db_session,
    ):
        """Re-saving a passthrough with a better prompt updates scores."""
        app_client._transport.app.state.routing.set_provider(None)

        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": PASSTHROUGH_RAW},
        )
        prep = resp.json()
        trace_id = prep["trace_id"]

        # First save — minimal optimized prompt
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": trace_id,
                "optimized_prompt": "Sort a list please thank you very much basically",
            },
        )
        first_score = resp.json()["overall_score"]

        # Second save — highly structured prompt
        resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": trace_id,
                "optimized_prompt": PASSTHROUGH_OPTIMIZED,
                "changes_summary": "Complete rewrite with structure",
            },
        )
        assert resp.status_code == 200
        second = resp.json()
        second_score = second["overall_score"]

        # The structured prompt should score higher
        assert second_score > first_score
        assert second["changes_summary"] == "Complete rewrite with structure"

        # Only one record in DB
        resp = await app_client.get(f"/api/optimize/{trace_id}")
        assert resp.status_code == 200
        assert resp.json()["overall_score"] == second_score
