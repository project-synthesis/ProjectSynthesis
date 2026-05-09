"""Tests for MetaPattern.global_source_count field."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MetaPattern, PromptCluster
from app.services.taxonomy.warm_phases import phase_refresh


def _make_engine():
    """Create a minimal mock engine for phase_refresh."""
    engine = MagicMock()
    engine._provider = None  # no LLM
    engine._prompt_loader = MagicMock()
    engine._embedding = MagicMock()

    async def _embed(text):
        import hashlib

        h = hashlib.md5(text.encode()).digest()
        vec = np.frombuffer(h * 24, dtype=np.float32)[:768]
        return vec / (np.linalg.norm(vec) + 1e-9)

    engine._embedding.aembed_single = _embed
    return engine


@pytest.mark.asyncio
async def test_meta_pattern_has_global_source_count_field(db: AsyncSession):
    """MetaPattern should have a global_source_count integer field defaulting to 0."""
    cluster = PromptCluster(
        label="test-cluster",
        state="active",
        domain="general",
    )
    db.add(cluster)
    await db.flush()

    mp = MetaPattern(
        cluster_id=cluster.id,
        pattern_text="Use chain-of-thought reasoning",
        source_count=5,
    )
    db.add(mp)
    await db.flush()

    result = await db.execute(select(MetaPattern).where(MetaPattern.id == mp.id))
    loaded = result.scalar_one()
    assert loaded.global_source_count == 0  # default
    assert isinstance(loaded.global_source_count, int)


@pytest.mark.asyncio
async def test_global_source_count_can_be_set(db: AsyncSession):
    """global_source_count should be writable."""
    cluster = PromptCluster(label="test", state="active", domain="general")
    db.add(cluster)
    await db.flush()

    mp = MetaPattern(
        cluster_id=cluster.id,
        pattern_text="Test pattern",
        source_count=1,
        global_source_count=7,
    )
    db.add(mp)
    await db.flush()

    loaded = (await db.execute(select(MetaPattern).where(MetaPattern.id == mp.id))).scalar_one()
    assert loaded.global_source_count == 7


@pytest.mark.asyncio
async def test_global_source_count_computed_for_similar_patterns(db: AsyncSession):
    """Patterns with identical text across clusters get global_source_count > 1."""
    # Create 3 clusters
    clusters = []
    for i in range(3):
        c = PromptCluster(
            label=f"cluster-{i}",
            state="active",
            domain="general",
            member_count=10,
        )
        db.add(c)
        clusters.append(c)
    await db.flush()

    # Same embedding for all 3 (identical pattern)
    shared_emb = np.random.RandomState(42).randn(768).astype(np.float32)
    shared_emb = shared_emb / np.linalg.norm(shared_emb)

    for c in clusters:
        mp = MetaPattern(
            cluster_id=c.id,
            pattern_text="Use chain-of-thought reasoning",
            embedding=shared_emb.tobytes(),
            source_count=5,
            global_source_count=0,
        )
        db.add(mp)

    # Unique pattern in cluster 0 with a very different embedding
    unique_emb = np.zeros(768, dtype=np.float32)
    unique_emb[0] = 1.0  # orthogonal to shared
    unique = MetaPattern(
        cluster_id=clusters[0].id,
        pattern_text="Format as YAML",
        embedding=unique_emb.tobytes(),
        source_count=2,
        global_source_count=0,
    )
    db.add(unique)
    await db.commit()

    engine = _make_engine()
    await phase_refresh(engine, db)

    # Reload
    all_patterns = (await db.execute(select(MetaPattern))).scalars().all()
    shared = [p for p in all_patterns if "chain-of-thought" in p.pattern_text]
    assert len(shared) == 3
    for p in shared:
        assert p.global_source_count == 3

    unique_loaded = [p for p in all_patterns if "YAML" in p.pattern_text]
    assert len(unique_loaded) == 1
    assert unique_loaded[0].global_source_count == 1


@pytest.mark.asyncio
async def test_global_source_count_single_pattern(db: AsyncSession):
    """A single pattern gets global_source_count=1."""
    c = PromptCluster(
        label="solo",
        state="active",
        domain="general",
        member_count=5,
    )
    db.add(c)
    await db.flush()

    emb = np.random.RandomState(99).randn(768).astype(np.float32)
    emb = emb / np.linalg.norm(emb)
    mp = MetaPattern(
        cluster_id=c.id,
        pattern_text="Solo pattern",
        embedding=emb.tobytes(),
        source_count=1,
        global_source_count=0,
    )
    db.add(mp)
    await db.commit()

    engine = _make_engine()
    await phase_refresh(engine, db)

    loaded = (
        await db.execute(select(MetaPattern).where(MetaPattern.id == mp.id))
    ).scalar_one()
    assert loaded.global_source_count == 1
