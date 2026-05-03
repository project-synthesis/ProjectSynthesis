"""Tests for ``batch_persistence.bulk_persist`` write-queue routing.

v0.4.13 cycle 2 RED phase: pin the new
``bulk_persist(results, write_queue, batch_id)`` signature that GREEN will
implement. Under the v0.4.12 ``session_factory`` signature the test fails
with ``TypeError`` — confirming the migration target before any production
code changes.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import uuid as _uuid

import pytest


def _make_passing_pending(batch_id: str = "test-batch"):
    """Build a minimal ``PendingOptimization`` passing ID-shape + quality gates.

    Per plan task 2.1 — fields chosen so ``bulk_persist`` accepts the row:
    valid uuid4 ``id``, ``status='completed'``, ``overall_score >= 5.0``.
    """
    from app.services.batch_pipeline import PendingOptimization
    return PendingOptimization(
        id=str(_uuid.uuid4()),
        trace_id=str(_uuid.uuid4()),
        raw_prompt="test prompt",
        optimized_prompt="optimized test prompt",
        task_type="general",
        strategy_used="auto",
        changes_summary="test",
        score_clarity=7.0,
        score_specificity=7.0,
        score_structure=7.0,
        score_faithfulness=7.0,
        score_conciseness=7.0,
        overall_score=7.0,
        improvement_score=1.0,
        scoring_mode="hybrid",
        intent_label="test",
        domain="general",
        domain_raw="general",
        embedding=None,
        optimized_embedding=None,
        transformation_embedding=None,
        models_by_phase={},
        original_scores={},
        score_deltas={},
        duration_ms=100,
        status="completed",
        provider="test",
        model_used="test-model",
        routing_tier="internal",
        heuristic_flags={},
        suggestions=[],
        repo_full_name=None,
        project_id=None,
        context_sources={"batch_id": batch_id},
        auto_injected_patterns=[],
        auto_injected_cluster_ids=[],
        auto_injected_similarity_map={},
    )


class TestBulkPersistViaWriteQueue:
    @pytest.mark.asyncio
    async def test_bulk_persist_routes_through_write_queue(
        self, write_queue_inmem, monkeypatch, db_session,
    ):
        """RED: bulk_persist must call write_queue.submit with operation_label='bulk_persist'.

        Currently FAILS because ``bulk_persist`` still has its v0.4.12
        ``session_factory`` signature. GREEN will swap the signature so
        the queue receives the work.
        """
        from app.services import batch_persistence

        captured: list[str] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label or "")
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)

        pending = [_make_passing_pending(batch_id="rt-test")]
        # Migration target: bulk_persist now takes write_queue, not session_factory
        inserted = await batch_persistence.bulk_persist(
            pending, write_queue_inmem, batch_id="rt-test",
        )
        assert "bulk_persist" in captured
        assert inserted == 1
