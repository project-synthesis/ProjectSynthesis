"""Tests for pattern matching — cascade search, cold-start, adaptive thresholds."""

import numpy as np
import pytest

from app.models import MetaPattern, PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_match_prompt_empty_taxonomy(db, mock_embedding, mock_provider):
    """Phase 0: No nodes -> returns None immediately."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.match_prompt("Build a REST API", db=db)
    assert result is None or result.match_level == "none"


@pytest.mark.asyncio
async def test_match_prompt_family_level(db, mock_embedding, mock_provider):
    """Family-level match: cosine >= 0.72 against leaf family."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create a confirmed node + family with known embedding
    emb = mock_embedding.embed_single("REST API endpoint design")
    node = PromptCluster(
        label="API Architecture",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="active",
        member_count=10,
        coherence=0.85,
        color_hex="#a855f7",
    )
    db.add(node)
    await db.flush()

    family = PromptCluster(
        label="REST API patterns",
        domain="backend",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        parent_id=node.id,
        member_count=5,
    )
    db.add(family)
    await db.flush()

    mp = MetaPattern(cluster_id=family.id, pattern_text="Use RESTful naming conventions")
    db.add(mp)
    await db.commit()

    # Same text should match at family level
    result = await engine.match_prompt("REST API endpoint design", db=db)
    assert result is not None
    assert result.match_level == "family"
    assert result.similarity > 0.7
    assert len(result.meta_patterns) > 0


@pytest.mark.asyncio
async def test_match_prompt_candidate_strict_threshold(db, mock_embedding, mock_provider):
    """Cold-start Phase 1: candidate families use strict 0.80 threshold."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    emb = mock_embedding.embed_single("test prompt")
    node = PromptCluster(
        label="Test",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="candidate",  # not confirmed yet
        member_count=2,
        coherence=0.5,
        color_hex="#7a7a9e",
    )
    db.add(node)

    family = PromptCluster(
        label="Test patterns",
        domain="general",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        parent_id=node.id,
    )
    db.add(family)
    await db.commit()

    # Exact match still works even with strict threshold
    result = await engine.match_prompt("test prompt", db=db)
    # Should match (cosine ~= 1.0 > 0.80)
    assert result is not None


@pytest.mark.asyncio
async def test_match_prompt_cluster_level_fallback(db, mock_embedding, mock_provider):
    """Cluster-level match when no leaf family matches."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create parent cluster with child families
    parent_emb = mock_embedding.embed_single("API related topics")
    parent = PromptCluster(
        label="API Architecture",
        centroid_embedding=parent_emb.astype(np.float32).tobytes(),
        state="active",
        member_count=20,
        coherence=0.70,
        color_hex="#a855f7",
    )
    db.add(parent)
    await db.flush()

    # Create child families with DIFFERENT embeddings
    child_emb = mock_embedding.embed_single("GraphQL subscriptions")
    child = PromptCluster(
        label="GraphQL patterns",
        parent_id=parent.id,
        centroid_embedding=child_emb.astype(np.float32).tobytes(),
        state="active",
        member_count=5,
        coherence=0.90,
        color_hex="#fbbf24",
    )
    db.add(child)

    family = PromptCluster(
        label="GraphQL subs",
        domain="backend",
        centroid_embedding=child_emb.astype(np.float32).tobytes(),
        parent_id=child.id,
    )
    db.add(family)
    await db.commit()

    # Query that matches parent but not child leaf
    result = await engine.match_prompt("API related topics", db=db)
    # Should match — either at cluster level (parent centroid) or family level
    assert result is not None
    assert result.match_level in ("family", "cluster")
    if result.match_level == "cluster":
        assert result.cluster is not None
