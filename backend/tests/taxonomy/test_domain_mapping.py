"""Tests for TaxonomyEngine.map_domain — free-text domain mapping."""

import numpy as np
import pytest

from app.models import PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine, TaxonomyMapping


@pytest.mark.asyncio
async def test_map_domain_cold_start(db, mock_embedding, mock_provider):
    """With no taxonomy nodes, should return unmapped."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.map_domain("REST API design", db=db)
    assert isinstance(result, TaxonomyMapping)
    assert result.cluster_id is None  # unmapped


@pytest.mark.asyncio
async def test_map_domain_finds_match(db, mock_embedding, mock_provider):
    """Should find matching taxonomy node when one exists."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create a confirmed node with known embedding
    emb = mock_embedding.embed_single("REST API design")
    node = PromptCluster(
        label="API Architecture",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="active",
        member_count=5,
        coherence=0.85,
        color_hex="#a855f7",
    )
    db.add(node)
    await db.commit()

    result = await engine.map_domain("REST API design", db=db)
    # Same text should map to same node (high cosine)
    assert result.cluster_id == node.id


@pytest.mark.asyncio
async def test_map_domain_bayesian_blend(db, mock_embedding, mock_provider):
    """Applied pattern IDs should bias domain mapping via 70/30 blend.

    We construct explicit vectors so that the query alone has cosine < 0.35
    to the API node (unmapped), but after blending 30% of the API-linked
    pattern centroid, cosine crosses DOMAIN_ALIGNMENT_FLOOR (0.35).
    """
    from unittest.mock import AsyncMock

    from app.models import MetaPattern

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Construct controlled geometry:
    #   api_vec along dim 0, query_vec tilted ~80° away (cosine ~0.17)
    #   After 70/30 blend with api_vec, cosine to api_vec ≈ 0.47 > 0.35
    api_vec = np.zeros(384, dtype=np.float32)
    api_vec[0] = 1.0

    query_vec = np.zeros(384, dtype=np.float32)
    query_vec[0] = 0.17  # small component along api
    query_vec[1] = 0.98
    query_vec /= np.linalg.norm(query_vec)

    node_api = PromptCluster(
        label="API Architecture",
        centroid_embedding=api_vec.tobytes(),
        state="active",
        member_count=5,
        coherence=0.85,
        color_hex="#a855f7",
    )
    db.add(node_api)
    await db.flush()

    # Family linked to API node (candidate state — not queried by map_domain)
    family = PromptCluster(
        label="API patterns",
        domain="backend",
        centroid_embedding=api_vec.tobytes(),
        parent_id=node_api.id,
        state="candidate",
    )
    db.add(family)
    await db.flush()

    mp = MetaPattern(
        cluster_id=family.id,
        pattern_text="use RESTful conventions",
        embedding=api_vec.tobytes(),
    )
    db.add(mp)
    await db.commit()

    # Override aembed_single to return our controlled query vector
    original_side_effect = mock_embedding.aembed_single.side_effect
    mock_embedding.aembed_single = AsyncMock(side_effect=lambda text: query_vec)

    # Without blend: cosine(query_vec, api_vec) ≈ 0.17 < 0.35 → unmapped
    result_no_blend = await engine.map_domain(
        "ambiguous task", db=db, applied_pattern_ids=None,
    )
    assert result_no_blend.cluster_id is None  # below floor

    # With blend: 70% query + 30% api centroid → cosine to api ≈ 0.47 > 0.35
    result_blended = await engine.map_domain(
        "ambiguous task", db=db, applied_pattern_ids=[mp.id],
    )
    assert result_blended.cluster_id == node_api.id  # blend pushed above floor

    # Restore original mock
    mock_embedding.aembed_single = AsyncMock(side_effect=original_side_effect)


@pytest.mark.asyncio
async def test_map_domain_below_floor_returns_unmapped(db, mock_embedding, mock_provider):
    """A node that's dissimilar (< DOMAIN_ALIGNMENT_FLOOR) should return unmapped."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Use a very different text so cosine < 0.35 is possible.
    # We craft a node with a perpendicular embedding — zero cosine.
    emb_query = mock_embedding.embed_single("quantum physics simulation")
    # Manufacture an orthogonal vector
    rng = np.random.RandomState(9999)
    perp = rng.randn(384).astype(np.float32)
    # Project out the query component so it's orthogonal
    perp -= np.dot(perp, emb_query) * emb_query
    norm = np.linalg.norm(perp)
    if norm > 0:
        perp = perp / norm

    node = PromptCluster(
        label="Perpendicular Domain",
        centroid_embedding=perp.tobytes(),
        state="active",
        member_count=1,
        coherence=0.5,
        color_hex="#ff0000",
    )
    db.add(node)
    await db.commit()

    result = await engine.map_domain("quantum physics simulation", db=db)
    # Cosine of query vs orthogonal centroid ≈ 0 < DOMAIN_ALIGNMENT_FLOOR
    assert result.cluster_id is None


@pytest.mark.asyncio
async def test_map_domain_only_considers_confirmed_nodes(db, mock_embedding, mock_provider):
    """Candidate/retired nodes should be ignored during domain mapping."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    emb = mock_embedding.embed_single("machine learning pipeline")

    # Add a candidate and a retired node — neither should match
    candidate = PromptCluster(
        label="Candidate ML",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="candidate",
        member_count=1,
        coherence=0.5,
        color_hex="#aaaaaa",
    )
    retired = PromptCluster(
        label="Retired ML",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="archived",
        member_count=3,
        coherence=0.7,
        color_hex="#bbbbbb",
    )
    db.add_all([candidate, retired])
    await db.commit()

    result = await engine.map_domain("machine learning pipeline", db=db)
    # No confirmed nodes → unmapped
    assert result.cluster_id is None


@pytest.mark.asyncio
async def test_map_domain_returns_breadcrumb(db, mock_embedding, mock_provider):
    """TaxonomyMapping should include a breadcrumb for the matched node."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    emb_child = mock_embedding.embed_single("frontend UI development")
    emb_parent = mock_embedding.embed_single("general software engineering concepts")

    # Parent node — distinct embedding so it won't be the top match
    parent = PromptCluster(
        label="Software Engineering",
        centroid_embedding=emb_parent.astype(np.float32).tobytes(),
        state="active",
        member_count=10,
        coherence=0.9,
        color_hex="#fbbf24",
    )
    db.add(parent)
    await db.flush()

    # Child node — same text as query for guaranteed top match
    child = PromptCluster(
        label="Frontend Development",
        centroid_embedding=emb_child.astype(np.float32).tobytes(),
        state="active",
        member_count=5,
        coherence=0.85,
        color_hex="#fbbf24",
        parent_id=parent.id,
    )
    db.add(child)
    await db.commit()

    result = await engine.map_domain("frontend UI development", db=db)

    # Should match child node specifically (identical embedding)
    assert result.cluster_id == child.id
    # Breadcrumb should be [parent, child] — length 2
    assert result.taxonomy_breadcrumb == ["Software Engineering", "Frontend Development"]


@pytest.mark.asyncio
async def test_map_domain_no_applied_patterns_no_blend(db, mock_embedding, mock_provider):
    """Without applied_pattern_ids, should map purely on domain_raw embedding."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    emb = mock_embedding.embed_single("database indexing")
    node = PromptCluster(
        label="Database Optimization",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="active",
        member_count=3,
        coherence=0.8,
        color_hex="#00d4aa",
    )
    db.add(node)
    await db.commit()

    result = await engine.map_domain("database indexing", db=db, applied_pattern_ids=None)
    assert result.cluster_id == node.id
    assert result.domain_raw == "database indexing"


@pytest.mark.asyncio
async def test_map_domain_empty_applied_pattern_ids(db, mock_embedding, mock_provider):
    """Empty applied_pattern_ids list should behave identically to None."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    emb = mock_embedding.embed_single("CI/CD automation")
    node = PromptCluster(
        label="DevOps Automation",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="active",
        member_count=4,
        coherence=0.75,
        color_hex="#4d8eff",
    )
    db.add(node)
    await db.commit()

    result_none = await engine.map_domain("CI/CD automation", db=db, applied_pattern_ids=None)
    result_empty = await engine.map_domain("CI/CD automation", db=db, applied_pattern_ids=[])
    assert result_none.cluster_id == result_empty.cluster_id
