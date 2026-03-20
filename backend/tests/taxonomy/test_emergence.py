"""Behavioral tests — distinct prompt domains produce distinct clusters.

Reference: Spec Section 9.1, Layer 3.
"""

import pytest
from sqlalchemy import select

from app.models import Optimization, PatternFamily
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_distinct_domains_produce_distinct_clusters(db, mock_embedding, mock_provider):
    """Three distinct prompt domains should form separate families.

    Each domain's prompts produce deterministic embeddings via mock_embedding's
    hash-based generator.  Prompts sharing a domain prefix hash to nearby vectors,
    while different domains hash to distant regions — driving family separation.
    """
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # 15 optimizations: 5 per domain, each with a unique raw_prompt
    domain_texts = ["REST API design", "SQL optimization", "React components"]
    for domain_text in domain_texts:
        for i in range(5):
            opt = Optimization(
                raw_prompt=f"{domain_text} prompt {i}",
                optimized_prompt=f"optimized {i}",
                status="completed",
                intent_label=domain_text,
                domain_raw=domain_text,
            )
            db.add(opt)
    await db.commit()

    # Process all optimizations
    all_opts = (await db.execute(select(Optimization))).scalars().all()
    for opt in all_opts:
        await engine.process_optimization(opt.id, db)

    # Run warm path to crystallize clusters
    await engine.run_warm_path(db)

    # Check families were created
    families = (await db.execute(select(PatternFamily))).scalars().all()
    assert len(families) >= 3, f"Expected >=3 families, got {len(families)}"


@pytest.mark.asyncio
async def test_identical_prompts_converge(db, mock_embedding, mock_provider):
    """Identical prompts should join the same family, not proliferate."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    for i in range(5):
        opt = Optimization(
            raw_prompt="Build a REST API with FastAPI and PostgreSQL",
            optimized_prompt=f"optimized {i}",
            status="completed",
            intent_label="REST API",
            domain_raw="REST API design",
        )
        db.add(opt)
    await db.commit()

    all_opts = (await db.execute(select(Optimization))).scalars().all()
    for opt in all_opts:
        await engine.process_optimization(opt.id, db)

    families = (await db.execute(select(PatternFamily))).scalars().all()
    # All 5 identical prompts should converge into 1 family
    assert len(families) == 1
    assert families[0].member_count == 5
