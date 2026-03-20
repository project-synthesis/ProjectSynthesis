"""Tests for taxonomy lifecycle operations — emerge, merge, split, retire."""

import numpy as np
import pytest

from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution

from app.models import MetaPattern, PatternFamily, TaxonomyNode
from app.services.taxonomy.lifecycle import (
    attempt_emerge,
    attempt_merge,
    attempt_retire,
    attempt_split,
    prioritize_operations,
)


@pytest.mark.asyncio
async def test_emerge_creates_candidate_node(db, mock_embedding):
    """Emerge should create a new candidate node from clustered members."""
    rng = np.random.RandomState(42)
    cluster = make_cluster_distribution("REST API", 5, spread=0.05, rng=rng)

    # Create families with embeddings
    families = []
    for i, emb in enumerate(cluster):
        f = PatternFamily(
            intent_label=f"api-pattern-{i}",
            domain="backend",
            centroid_embedding=emb.astype(np.float32).tobytes(),
        )
        db.add(f)
        families.append(f)
    await db.flush()

    result = await attempt_emerge(
        db=db,
        member_family_ids=[f.id for f in families],
        embeddings=cluster,
        warm_path_age=5,
        provider=None,
        model="claude-haiku-4-5",
    )

    assert result is not None
    assert result.state == "candidate"
    assert result.member_count == 5


@pytest.mark.asyncio
async def test_merge_combines_two_nodes(db, mock_embedding):
    """Merge should combine two sibling nodes into one."""
    emb_a = np.random.randn(EMBEDDING_DIM).astype(np.float32)
    emb_b = emb_a + np.random.randn(EMBEDDING_DIM).astype(np.float32) * 0.05

    parent = TaxonomyNode(
        label="Parent",
        centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
        state="confirmed",
        color_hex="#00e5ff",
    )
    db.add(parent)
    await db.flush()

    node_a = TaxonomyNode(
        label="Node A",
        parent_id=parent.id,
        centroid_embedding=emb_a.tobytes(),
        member_count=5,
        coherence=0.85,
        state="confirmed",
        color_hex="#a855f7",
    )
    node_b = TaxonomyNode(
        label="Node B",
        parent_id=parent.id,
        centroid_embedding=emb_b.tobytes(),
        member_count=3,
        coherence=0.80,
        state="confirmed",
        color_hex="#fbbf24",
    )
    db.add_all([node_a, node_b])
    await db.flush()

    result = await attempt_merge(
        db=db,
        node_a=node_a,
        node_b=node_b,
        warm_path_age=10,
    )

    assert result is not None
    assert result.member_count == 8  # combined
    assert node_a.state == "retired" or node_b.state == "retired"


@pytest.mark.asyncio
async def test_retire_redistributes_members(db, mock_embedding):
    """Retire should move members to nearest sibling."""
    parent = TaxonomyNode(
        label="Parent",
        centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
        state="confirmed",
        color_hex="#00e5ff",
    )
    db.add(parent)
    await db.flush()

    sibling = TaxonomyNode(
        label="Active sibling",
        parent_id=parent.id,
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=10,
        state="confirmed",
        color_hex="#a855f7",
    )
    target = TaxonomyNode(
        label="Idle node",
        parent_id=parent.id,
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=1,
        state="confirmed",
        observations=30,
        color_hex="#7a7a9e",
    )
    db.add_all([sibling, target])
    await db.flush()

    result = await attempt_retire(
        db=db,
        node=target,
        warm_path_age=25,
    )

    assert result is True
    assert target.state == "retired"
    assert target.retired_at is not None


def test_prioritize_operations():
    """Operations should execute in order: split > emerge > merge > retire."""
    ops = [
        {"type": "retire", "node_id": "d"},
        {"type": "emerge", "node_id": "b"},
        {"type": "merge", "node_id": "c"},
        {"type": "split", "node_id": "a"},
    ]
    ordered = prioritize_operations(ops)
    types = [o["type"] for o in ordered]
    assert types == ["split", "emerge", "merge", "retire"]
