"""Layer 3 behavioral test: edge cases (Spec 9.1).

Adversarial and degenerate inputs that the taxonomy engine must handle
gracefully without crashing or producing NaN/Inf values.
"""

import numpy as np
import pytest
from sqlalchemy import select

from app.models import Optimization, PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine
from tests.taxonomy.conftest import EMBEDDING_DIM


@pytest.mark.asyncio
async def test_identical_embeddings_converge(db, mock_embedding, mock_provider):
    """10 optimizations with the same prompt text should converge into 1 family, not 10.

    The mock_embedding fixture derives embeddings deterministically from the
    prompt text via a hash, so using the same raw_prompt guarantees identical
    embeddings — which is the adversarial input this test exercises.
    """
    engine = TaxonomyEngine(
        embedding_service=mock_embedding, provider=mock_provider,
    )
    # Fixed embedding bytes for storage (not re-used by engine, which re-embeds)
    emb = np.random.RandomState(42).randn(EMBEDDING_DIM).astype(np.float32)
    emb /= np.linalg.norm(emb)

    # Same raw_prompt → same hash → same mock embedding vector for all 10 calls
    for i in range(10):
        opt = Optimization(
            raw_prompt="identical prompt text",
            optimized_prompt=f"output variant {i}",
            task_type="coding",
            intent_label="same",
            domain="general",
            domain_raw="general",
            strategy_used="auto",
            embedding=emb.tobytes(),
        )
        db.add(opt)
        await db.flush()
        await engine.process_optimization(opt.id, db)

    # Should have at most 2-3 families, not 10
    result = await db.execute(select(PromptCluster))
    families = result.scalars().all()
    assert len(families) <= 3, f"Expected convergence but got {len(families)} families"


@pytest.mark.asyncio
async def test_single_optimization_does_not_crash(db, mock_embedding, mock_provider):
    """A single optimization should process without error."""
    engine = TaxonomyEngine(
        embedding_service=mock_embedding, provider=mock_provider,
    )
    emb = np.random.RandomState(42).randn(EMBEDDING_DIM).astype(np.float32)
    emb /= np.linalg.norm(emb)

    opt = Optimization(
        raw_prompt="Solo prompt",
        optimized_prompt="Solo output",
        task_type="coding",
        intent_label="solo",
        domain="general",
        domain_raw="general",
        strategy_used="auto",
        embedding=emb.tobytes(),
    )
    db.add(opt)
    await db.flush()

    # Should not raise
    await engine.process_optimization(opt.id, db)


@pytest.mark.asyncio
async def test_warm_path_empty_taxonomy(db, mock_embedding, mock_provider):
    """Warm path on an empty taxonomy should return None or empty result, not crash."""
    engine = TaxonomyEngine(
        embedding_service=mock_embedding, provider=mock_provider,
    )
    result = await engine.run_warm_path(db)
    # Should gracefully handle no data
    assert result is None or result.operations_attempted == 0


@pytest.mark.asyncio
async def test_match_prompt_empty_taxonomy(db, mock_embedding, mock_provider):
    """Matching against an empty taxonomy should return no match (None or match_level='none')."""
    engine = TaxonomyEngine(
        embedding_service=mock_embedding, provider=mock_provider,
    )
    result = await engine.match_prompt("test prompt", db)
    # Engine returns either None or a sentinel PatternMatch with match_level='none'
    assert result is None or result.match_level == "none", (
        f"Expected no match but got match_level={result.match_level!r}"
    )


@pytest.mark.asyncio
async def test_no_nan_in_node_metrics(db, mock_embedding, mock_provider):
    """After processing, no taxonomy node should have NaN/Inf metrics."""
    engine = TaxonomyEngine(
        embedding_service=mock_embedding, provider=mock_provider,
    )
    rng = np.random.RandomState(42)

    for i in range(20):
        emb = rng.randn(EMBEDDING_DIM).astype(np.float32)
        emb /= np.linalg.norm(emb)
        opt = Optimization(
            raw_prompt=f"Prompt {i}",
            optimized_prompt=f"Output {i}",
            task_type="coding",
            intent_label=f"intent-{i % 3}",
            domain="general",
            domain_raw="general",
            strategy_used="auto",
            embedding=emb.tobytes(),
        )
        db.add(opt)
        await db.flush()
        await engine.process_optimization(opt.id, db)

    await engine.run_warm_path(db)

    result = await db.execute(select(PromptCluster))
    nodes = result.scalars().all()
    for node in nodes:
        if node.coherence is not None:
            assert np.isfinite(node.coherence), f"NaN coherence on {node.id}"
        if node.separation is not None:
            assert np.isfinite(node.separation), f"NaN separation on {node.id}"
