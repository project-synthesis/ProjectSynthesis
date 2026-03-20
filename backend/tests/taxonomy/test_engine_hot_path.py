"""Tests for TaxonomyEngine hot path — process_optimization."""

import pytest

from app.models import Optimization, PatternFamily
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_process_optimization_embeds_and_assigns(db, mock_embedding, mock_provider):
    """process_optimization should embed prompt and assign to nearest family."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    opt = Optimization(
        raw_prompt="Build a REST API with FastAPI",
        optimized_prompt="Build a REST API...",
        status="completed",
        intent_label="REST API",
        domain="backend",
        domain_raw="REST API design",
    )
    db.add(opt)
    await db.commit()

    await engine.process_optimization(opt.id, db)

    # Optimization should have embedding set
    assert opt.embedding is not None


@pytest.mark.asyncio
async def test_process_optimization_skips_non_completed(db, mock_embedding, mock_provider):
    """Should skip optimizations that aren't 'completed'."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    opt = Optimization(raw_prompt="test", status="failed")
    db.add(opt)
    await db.commit()

    await engine.process_optimization(opt.id, db)
    assert opt.embedding is None  # not processed


@pytest.mark.asyncio
async def test_process_optimization_idempotent(db, mock_embedding, mock_provider):
    """Second call should be a no-op."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    opt = Optimization(
        raw_prompt="test prompt",
        status="completed",
        domain_raw="backend",
    )
    db.add(opt)
    await db.commit()

    await engine.process_optimization(opt.id, db)
    first_embedding = opt.embedding

    # Process again — should skip
    await engine.process_optimization(opt.id, db)
    assert opt.embedding == first_embedding


@pytest.mark.asyncio
async def test_process_optimization_creates_family(db, mock_embedding, mock_provider):
    """process_optimization should create a PatternFamily for the optimization."""
    from sqlalchemy import select

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    opt = Optimization(
        raw_prompt="Write unit tests for a Python service",
        optimized_prompt="Write comprehensive unit tests...",
        status="completed",
        intent_label="Unit Testing",
        domain="backend",
        domain_raw="test automation",
    )
    db.add(opt)
    await db.commit()

    await engine.process_optimization(opt.id, db)

    # A PatternFamily should have been created
    result = await db.execute(select(PatternFamily))
    families = result.scalars().all()
    assert len(families) == 1
    assert families[0].domain == "backend"


@pytest.mark.asyncio
async def test_process_optimization_merges_into_existing_family(
    db, mock_embedding, mock_provider
):
    """Second optimization with similar embedding should merge into existing family."""
    from sqlalchemy import select

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Use the same text twice to guarantee cosine ≥ FAMILY_MERGE_THRESHOLD
    identical_prompt = "Build a FastAPI REST service with CRUD endpoints"

    opt1 = Optimization(
        raw_prompt=identical_prompt,
        optimized_prompt="...",
        status="completed",
        intent_label="REST API",
        domain="backend",
        domain_raw="REST API",
    )
    db.add(opt1)
    await db.commit()
    await engine.process_optimization(opt1.id, db)

    opt2 = Optimization(
        raw_prompt=identical_prompt,
        optimized_prompt="...",
        status="completed",
        intent_label="REST API",
        domain="backend",
        domain_raw="REST API",
    )
    db.add(opt2)
    await db.commit()
    await engine.process_optimization(opt2.id, db)

    result = await db.execute(select(PatternFamily))
    families = result.scalars().all()
    # Both should collapse into a single family (identical embeddings)
    assert len(families) == 1
    assert families[0].member_count == 2


@pytest.mark.asyncio
async def test_process_optimization_writes_join_record(db, mock_embedding, mock_provider):
    """A 'source' OptimizationPattern record should be created on success."""
    from sqlalchemy import select

    from app.models import OptimizationPattern

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    opt = Optimization(
        raw_prompt="Design a secure authentication system",
        optimized_prompt="...",
        status="completed",
        intent_label="Auth",
        domain="security",
        domain_raw="authentication",
    )
    db.add(opt)
    await db.commit()

    await engine.process_optimization(opt.id, db)

    result = await db.execute(
        select(OptimizationPattern).where(
            OptimizationPattern.optimization_id == opt.id,
            OptimizationPattern.relationship == "source",
        )
    )
    join = result.scalar_one_or_none()
    assert join is not None


@pytest.mark.asyncio
async def test_process_optimization_skips_missing_id(db, mock_embedding, mock_provider):
    """process_optimization with a nonexistent ID should return silently."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    # Should not raise — just log and return
    await engine.process_optimization("nonexistent-id-xxxx", db)


@pytest.mark.asyncio
async def test_process_optimization_cross_domain_creates_new_family(
    db, mock_embedding, mock_provider
):
    """Same embedding but different domain should create a new family (not merge)."""
    from sqlalchemy import select

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    identical_prompt = "Implement data pipeline with transformations"

    opt1 = Optimization(
        raw_prompt=identical_prompt,
        optimized_prompt="...",
        status="completed",
        intent_label="Data Pipeline",
        domain="backend",
        domain_raw="data processing",
    )
    db.add(opt1)
    await db.commit()
    await engine.process_optimization(opt1.id, db)

    # Same text, different domain
    opt2 = Optimization(
        raw_prompt=identical_prompt,
        optimized_prompt="...",
        status="completed",
        intent_label="Data Pipeline",
        domain="database",
        domain_raw="database pipeline",
    )
    db.add(opt2)
    await db.commit()
    await engine.process_optimization(opt2.id, db)

    result = await db.execute(select(PatternFamily))
    families = result.scalars().all()
    # Cross-domain merge prevention → 2 families
    assert len(families) == 2
