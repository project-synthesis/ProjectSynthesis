"""Tests for warm-path domain discovery."""


import numpy as np
import pytest
from sqlalchemy import select

from app.models import Optimization, PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine


# Helper to create a "general" domain node
async def _seed_general_domain(db):
    node = PromptCluster(
        label="general", state="domain", domain="general", persistence=1.0,
        color_hex="#7a7a9e", cluster_metadata={"source": "seed"},
    )
    db.add(node)
    await db.flush()
    return node


@pytest.mark.asyncio
async def test_propose_domains_creates_domain_node(db, mock_embedding):
    """When a cluster under 'general' has consistent domain_raw, a new domain is created."""
    general = await _seed_general_domain(db)

    # Create a cluster under general with enough members and coherence
    cluster = PromptCluster(
        label="marketing-emails", state="active", domain="general",
        parent_id=general.id, member_count=8, coherence=0.75,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.flush()

    # Create optimizations with consistent domain_raw
    for i in range(8):
        db.add(Optimization(
            raw_prompt=f"marketing prompt {i}",
            domain="general", domain_raw="marketing: email",
            cluster_id=cluster.id,
            status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)

    assert "marketing" in created

    # Verify domain node was created
    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.label == "marketing",
        )
    )
    domain_node = result.scalar_one()
    assert domain_node.persistence == 1.0
    assert domain_node.color_hex is not None
    assert domain_node.cluster_metadata["source"] == "discovered"


@pytest.mark.asyncio
async def test_propose_domains_skips_below_member_threshold(db, mock_embedding):
    """Clusters with fewer than DOMAIN_DISCOVERY_MIN_MEMBERS are skipped."""
    general = await _seed_general_domain(db)
    cluster = PromptCluster(
        label="tiny", state="active", domain="general",
        parent_id=general.id, member_count=3, coherence=0.8,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)
    assert created == []


@pytest.mark.asyncio
async def test_propose_domains_skips_inconsistent_primaries(db, mock_embedding):
    """Skip when domain_raw values are too diverse."""
    general = await _seed_general_domain(db)
    cluster = PromptCluster(
        label="mixed", state="active", domain="general",
        parent_id=general.id, member_count=6, coherence=0.7,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.flush()

    # Mixed domain_raw values — no single primary reaches 60%
    for i, domain_raw in enumerate(
        ["marketing", "legal", "education", "marketing", "legal", "research"]
    ):
        db.add(Optimization(
            raw_prompt=f"prompt {i}", domain="general",
            domain_raw=domain_raw, cluster_id=cluster.id, status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)
    assert created == []


@pytest.mark.asyncio
async def test_propose_domains_skips_existing_domain(db, mock_embedding):
    """Don't create duplicate domain nodes."""
    general = await _seed_general_domain(db)

    # "backend" domain already exists
    db.add(PromptCluster(
        label="backend", state="domain", domain="backend", persistence=1.0,
    ))
    await db.flush()

    cluster = PromptCluster(
        label="api-stuff", state="active", domain="general",
        parent_id=general.id, member_count=8, coherence=0.7,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.flush()

    for i in range(8):
        db.add(Optimization(
            raw_prompt=f"api prompt {i}", domain="general",
            domain_raw="backend: api", cluster_id=cluster.id, status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)
    assert "backend" not in created


@pytest.mark.asyncio
async def test_domain_ceiling_blocks_discovery(db, mock_embedding):
    """When DOMAIN_COUNT_CEILING is reached, no new domains are created."""
    general = await _seed_general_domain(db)

    # Create 30 domain nodes (at ceiling)
    for i in range(30):
        db.add(PromptCluster(
            label=f"domain-{i}", state="domain",
            domain=f"domain-{i}", persistence=1.0,
        ))
    await db.flush()

    # Eligible cluster
    cluster = PromptCluster(
        label="should-not-emerge", state="active", domain="general",
        parent_id=general.id, member_count=10, coherence=0.8,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.flush()
    for i in range(10):
        db.add(Optimization(
            raw_prompt=f"prompt {i}", domain="general",
            domain_raw="newdomain", cluster_id=cluster.id, status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)
    assert created == []
