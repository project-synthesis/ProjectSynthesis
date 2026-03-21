"""Performance tests — latency assertions per execution tier.

Reference: Spec Section 9.1, Layer 4.
"""

import time

import numpy as np
import pytest

from app.models import Optimization, PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine
from tests.taxonomy.conftest import EMBEDDING_DIM


@pytest.mark.asyncio
async def test_hot_path_under_500ms(db, mock_embedding, mock_provider):
    """process_optimization should complete in < 500ms.

    Budget is wide to absorb cold-start overhead (SQLAlchemy init, first async I/O).
    """
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    opt = Optimization(
        raw_prompt="Build a REST API with FastAPI",
        optimized_prompt="Build a REST API...",
        status="completed",
        domain_raw="REST API design",
    )
    db.add(opt)
    await db.commit()

    t0 = time.monotonic()
    await engine.process_optimization(opt.id, db)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert elapsed_ms < 500, f"Hot path took {elapsed_ms:.0f}ms (budget: 500ms)"


@pytest.mark.asyncio
async def test_match_prompt_under_100ms(db, mock_embedding, mock_provider):
    """match_prompt should be fast (no DB write, read-only)."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create taxonomy nodes and families so the matching code exercises
    # the full search path (match_prompt filters on parent_id IS NOT NULL).
    rng = np.random.RandomState(0)
    for i in range(10):
        centroid = rng.randn(EMBEDDING_DIM).astype(np.float32)
        centroid /= np.linalg.norm(centroid) + 1e-9

        node = PromptCluster(
            label=f"node-{i}",
            centroid_embedding=centroid.tobytes(),
            member_count=5,
            state="active",
        )
        db.add(node)
        await db.flush()

        f = PromptCluster(
            label=f"family-{i}",
            domain="general",
            centroid_embedding=centroid.tobytes(),
            parent_id=node.id,
        )
        db.add(f)
    await db.commit()

    t0 = time.monotonic()
    await engine.match_prompt("test prompt", db=db)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert elapsed_ms < 100, f"match_prompt took {elapsed_ms:.0f}ms (budget: 100ms)"
