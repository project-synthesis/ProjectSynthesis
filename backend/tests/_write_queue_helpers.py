"""Shared test helpers for WriteQueue + bulk_persist tests.

Extracted from ``test_batch_persistence.py`` cycle 2 OPERATE phase so cycles
3+ can pre-stage parent-row FKs (cluster assignment, pattern injection) and
build canonical ``PendingOptimization`` rows without duplicating fixture
construction across modules.

Helpers:

* ``_make_passing_pending`` — build a minimal ``PendingOptimization`` that
  passes the ID-shape gate (valid uuid4) AND the quality gate
  (overall_score >= 5.0). ``with_embedding`` (cycle 3+) populates
  ``embedding``/``optimized_embedding``/``transformation_embedding`` with
  zero-vector bytes so taxonomy-assign callers don't repeat the inline
  ``np.zeros(384, ...).tobytes()`` construction.
* ``_make_failing_pending`` — build a ``PendingOptimization`` whose score
  the quality gate must reject. Used by stress tests verifying the
  rejection still fires under concurrent load.
* ``create_prestaged_cluster`` — create a ``PromptCluster`` row directly
  on a writer engine for FK-dependent tests (e.g. provenance writes,
  taxonomy assignment). Idempotent on caller-supplied ``cluster_id``.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.services.batch_pipeline import PendingOptimization


def _make_passing_pending(
    batch_id: str = "test-batch",
    *,
    opt_id: str | None = None,
    with_embedding: bool = False,
) -> PendingOptimization:
    """Build a minimal ``PendingOptimization`` passing ID-shape + quality gates.

    Per plan task 2.1 — fields chosen so ``bulk_persist`` accepts the row:
    valid uuid4 ``id``, ``status='completed'``, ``overall_score >= 5.0``.

    ``opt_id`` allows callers (e.g. idempotency tests) to pin the row's UUID
    so two parallel ``bulk_persist`` calls collide on the same primary key.

    ``with_embedding`` (cycle 3+) populates the three embedding fields with
    a zero-vector ``bytes`` payload (384-dim, float32). ``batch_taxonomy_assign``
    filters on ``r.embedding`` truthiness, so taxonomy-assign tests need
    this set to avoid silent skip. Defaulting to ``False`` keeps every cycle
    1-2 caller's behavior unchanged.
    """
    from app.services.batch_pipeline import PendingOptimization
    embedding_bytes: bytes | None = None
    if with_embedding:
        embedding_bytes = np.zeros(384, dtype=np.float32).tobytes()
    return PendingOptimization(
        id=opt_id or str(_uuid.uuid4()),
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
        embedding=embedding_bytes,
        optimized_embedding=embedding_bytes,
        transformation_embedding=embedding_bytes,
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


def _make_failing_pending(
    batch_id: str = "test-batch", overall_score: float = 3.0,
) -> PendingOptimization:
    """Build a ``PendingOptimization`` that the quality gate must reject.

    Used by ``test_bulk_persist_quality_gate_under_load`` (and any cycle 3+
    tests that need a sub-5.0 row) to verify the score < 5.0 rejection still
    fires under concurrent load.
    """
    p = _make_passing_pending(batch_id=batch_id)
    p.overall_score = overall_score
    return p


async def create_prestaged_cluster(
    target_engine: AsyncEngine,
    *,
    cluster_id: str | None = None,
    label: str = "test-cluster",
    state: str = "active",
    domain: str = "general",
    task_type: str = "general",
) -> str:
    """Create a ``PromptCluster`` row for FK-dependent tests. Returns
    ``cluster_id``.

    Used by provenance + taxonomy assignment tests across cycles 2-7. Goes
    through the ORM so model-level NOT NULL defaults (e.g. ``member_count``,
    ``weighted_member_sum``, ``scored_count``, etc.) are populated without
    every test having to spell them out in raw SQL.

    Idempotent on caller-supplied ``cluster_id``: if the row already exists
    the existing id is returned. Callers that need a guaranteed-fresh row
    omit ``cluster_id`` so a uuid4 is generated.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models import PromptCluster

    cid = cluster_id or str(_uuid.uuid4())
    sf = async_sessionmaker(
        target_engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with sf() as setup_db:
        existing = await setup_db.get(PromptCluster, cid)
        if existing is not None:
            return cid
        setup_db.add(PromptCluster(
            id=cid,
            label=label,
            state=state,
            domain=domain,
            task_type=task_type,
        ))
        await setup_db.commit()
    return cid


__all__ = [
    "_make_failing_pending",
    "_make_passing_pending",
    "create_prestaged_cluster",
]
