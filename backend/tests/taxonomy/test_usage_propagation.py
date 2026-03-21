"""Tests for usage count propagation up the taxonomy tree (Spec 7.8)."""

import numpy as np
import pytest

from app.models import PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine
from tests.taxonomy.conftest import EMBEDDING_DIM


@pytest.mark.asyncio
async def test_increment_usage_propagates_to_parent(db, mock_embedding, mock_provider):
    """Usage increment should walk up the tree and increment each ancestor."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(42)

    # Create parent -> child node chain
    parent_centroid = rng.randn(EMBEDDING_DIM).astype(np.float32)
    parent = PromptCluster(
        label="Infrastructure",
        centroid_embedding=parent_centroid.tobytes(),
        member_count=10,
        state="active",
        usage_count=0,
    )
    db.add(parent)
    await db.flush()

    child_centroid = rng.randn(EMBEDDING_DIM).astype(np.float32)
    child = PromptCluster(
        label="API Architecture",
        parent_id=parent.id,
        centroid_embedding=child_centroid.tobytes(),
        member_count=5,
        state="active",
        usage_count=0,
    )
    db.add(child)
    await db.flush()

    # Create a family under the child node
    family = PromptCluster(
        label="REST API patterns",
        domain="REST API design",
        task_type="coding",
        parent_id=child.id,
        centroid_embedding=rng.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=3,
        usage_count=0,
    )
    db.add(family)
    await db.commit()

    # Increment usage
    await engine.increment_usage(family.id, db)

    # Verify propagation: family, child, and parent all incremented
    await db.refresh(family)
    await db.refresh(child)
    await db.refresh(parent)
    assert family.usage_count == 1
    assert child.usage_count == 1
    assert parent.usage_count == 1


@pytest.mark.asyncio
async def test_increment_usage_no_node_is_noop(db, mock_embedding, mock_provider):
    """Family with no parent_id should still increment family only."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(42)

    family = PromptCluster(
        label="Orphan family",
        domain="general",
        task_type="general",
        parent_id=None,
        centroid_embedding=rng.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=1,
        usage_count=0,
    )
    db.add(family)
    await db.commit()

    await engine.increment_usage(family.id, db)

    await db.refresh(family)
    assert family.usage_count == 1
