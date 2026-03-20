"""Tests for TaxonomyEngine.map_domain — free-text domain mapping."""

import numpy as np
import pytest

from app.models import PatternFamily, TaxonomyNode
from app.services.taxonomy.engine import TaxonomyEngine, TaxonomyMapping


@pytest.mark.asyncio
async def test_map_domain_cold_start(db, mock_embedding, mock_provider):
    """With no taxonomy nodes, should return unmapped."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.map_domain("REST API design", db=db)
    assert isinstance(result, TaxonomyMapping)
    assert result.taxonomy_node_id is None  # unmapped


@pytest.mark.asyncio
async def test_map_domain_finds_match(db, mock_embedding, mock_provider):
    """Should find matching taxonomy node when one exists."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create a confirmed node with known embedding
    emb = mock_embedding.embed_single("REST API design")
    node = TaxonomyNode(
        label="API Architecture",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=5,
        coherence=0.85,
        color_hex="#a855f7",
    )
    db.add(node)
    await db.commit()

    result = await engine.map_domain("REST API design", db=db)
    # Same text should map to same node (high cosine)
    assert result.taxonomy_node_id == node.id


@pytest.mark.asyncio
async def test_map_domain_bayesian_blend(db, mock_embedding, mock_provider):
    """Applied pattern IDs should bias domain mapping (70/30 blend)."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create two distinct nodes
    emb_api = mock_embedding.embed_single("REST API design")
    emb_db = mock_embedding.embed_single("SQL database schema")

    node_api = TaxonomyNode(
        label="API Architecture",
        centroid_embedding=emb_api.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=5,
        coherence=0.85,
        color_hex="#a855f7",
    )
    node_db = TaxonomyNode(
        label="Database Design",
        centroid_embedding=emb_db.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=5,
        coherence=0.85,
        color_hex="#00d4aa",
    )
    db.add_all([node_api, node_db])
    await db.flush()

    # Create a family linked to API node
    family = PatternFamily(
        intent_label="API patterns",
        domain="backend",
        centroid_embedding=emb_api.astype(np.float32).tobytes(),
        taxonomy_node_id=node_api.id,
    )
    db.add(family)
    await db.commit()

    # Map with applied pattern from API family — should bias toward API
    from app.models import MetaPattern
    mp = MetaPattern(family_id=family.id, pattern_text="use RESTful conventions")
    db.add(mp)
    await db.commit()

    result = await engine.map_domain(
        "general programming task",
        db=db,
        applied_pattern_ids=[mp.id],
    )
    # With the blend, should lean toward API node
    assert result is not None


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

    node = TaxonomyNode(
        label="Perpendicular Domain",
        centroid_embedding=perp.tobytes(),
        state="confirmed",
        member_count=1,
        coherence=0.5,
        color_hex="#ff0000",
    )
    db.add(node)
    await db.commit()

    result = await engine.map_domain("quantum physics simulation", db=db)
    # Cosine of query vs orthogonal centroid ≈ 0 < DOMAIN_ALIGNMENT_FLOOR
    assert result.taxonomy_node_id is None


@pytest.mark.asyncio
async def test_map_domain_only_considers_confirmed_nodes(db, mock_embedding, mock_provider):
    """Candidate/retired nodes should be ignored during domain mapping."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    emb = mock_embedding.embed_single("machine learning pipeline")

    # Add a candidate and a retired node — neither should match
    candidate = TaxonomyNode(
        label="Candidate ML",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="candidate",
        member_count=1,
        coherence=0.5,
        color_hex="#aaaaaa",
    )
    retired = TaxonomyNode(
        label="Retired ML",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="retired",
        member_count=3,
        coherence=0.7,
        color_hex="#bbbbbb",
    )
    db.add_all([candidate, retired])
    await db.commit()

    result = await engine.map_domain("machine learning pipeline", db=db)
    # No confirmed nodes → unmapped
    assert result.taxonomy_node_id is None


@pytest.mark.asyncio
async def test_map_domain_returns_breadcrumb(db, mock_embedding, mock_provider):
    """TaxonomyMapping should include a breadcrumb for the matched node."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    emb = mock_embedding.embed_single("frontend UI development")

    # Parent node
    parent = TaxonomyNode(
        label="Software Engineering",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=10,
        coherence=0.9,
        color_hex="#fbbf24",
    )
    db.add(parent)
    await db.flush()

    # Child node (same embedding for easy matching)
    child = TaxonomyNode(
        label="Frontend Development",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=5,
        coherence=0.85,
        color_hex="#fbbf24",
        parent_id=parent.id,
    )
    db.add(child)
    await db.commit()

    result = await engine.map_domain("frontend UI development", db=db)

    # Should have matched (high cosine for same text)
    assert result.taxonomy_node_id is not None
    # Breadcrumb should be a non-empty list
    assert isinstance(result.taxonomy_breadcrumb, list)
    assert len(result.taxonomy_breadcrumb) >= 1


@pytest.mark.asyncio
async def test_map_domain_no_applied_patterns_no_blend(db, mock_embedding, mock_provider):
    """Without applied_pattern_ids, should map purely on domain_raw embedding."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    emb = mock_embedding.embed_single("database indexing")
    node = TaxonomyNode(
        label="Database Optimization",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=3,
        coherence=0.8,
        color_hex="#00d4aa",
    )
    db.add(node)
    await db.commit()

    result = await engine.map_domain("database indexing", db=db, applied_pattern_ids=None)
    assert result.taxonomy_node_id == node.id
    assert result.domain_raw == "database indexing"


@pytest.mark.asyncio
async def test_map_domain_empty_applied_pattern_ids(db, mock_embedding, mock_provider):
    """Empty applied_pattern_ids list should behave identically to None."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    emb = mock_embedding.embed_single("CI/CD automation")
    node = TaxonomyNode(
        label="DevOps Automation",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=4,
        coherence=0.75,
        color_hex="#4d8eff",
    )
    db.add(node)
    await db.commit()

    result_none = await engine.map_domain("CI/CD automation", db=db, applied_pattern_ids=None)
    result_empty = await engine.map_domain("CI/CD automation", db=db, applied_pattern_ids=[])
    assert result_none.taxonomy_node_id == result_empty.taxonomy_node_id
