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
async def test_re_promoted_seed_label_reclaims_brand_color(db, mock_embedding):
    """A re-promoted canonical seed label (e.g. 'backend') reclaims its
    brand-anchored color from SEED_PALETTE — never the generic OKLab
    max-distance result.  Verifies palette identity survives dissolution/
    re-promotion cycles (ADR-006 dissolves empty seed domains, but their
    visual identity must persist across cycles).
    """
    general = await _seed_general_domain(db)

    # Cluster under 'general' with 8 members all consistently tagged 'backend'
    cluster = PromptCluster(
        label="api-middleware", state="active", domain="general",
        parent_id=general.id, member_count=8, coherence=0.75,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.flush()
    for i in range(8):
        db.add(Optimization(
            raw_prompt=f"build api endpoint {i}",
            domain="general", domain_raw="backend",
            cluster_id=cluster.id, status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)
    assert "backend" in created

    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.label == "backend",
        )
    )
    backend_node = result.scalar_one()
    assert backend_node.color_hex == "#b44aff", (
        f"expected seed palette color for 'backend', got {backend_node.color_hex}"
    )


@pytest.mark.asyncio
async def test_novel_domain_still_uses_max_distance(db, mock_embedding):
    """A non-seed label (e.g. 'marketing') still routes through
    compute_max_distance_color — the seed palette lookup must not
    accidentally suppress OKLab distribution for novel domains.
    """
    general = await _seed_general_domain(db)

    cluster = PromptCluster(
        label="campaign-copy", state="active", domain="general",
        parent_id=general.id, member_count=8, coherence=0.75,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.flush()
    for i in range(8):
        db.add(Optimization(
            raw_prompt=f"write launch email {i}",
            domain="general", domain_raw="marketing",
            cluster_id=cluster.id, status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)
    assert "marketing" in created

    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.label == "marketing",
        )
    )
    marketing_node = result.scalar_one()
    # Valid hex, not the generic grey fallback, not one of the seed values
    from app.services.taxonomy.coloring import SEED_PALETTE
    assert marketing_node.color_hex is not None
    assert marketing_node.color_hex.startswith("#")
    assert marketing_node.color_hex not in SEED_PALETTE.values()


@pytest.mark.asyncio
async def test_propose_domains_pools_fragmented_clusters(db, mock_embedding):
    """Change A: 3 single-member backend clusters pool into one 'backend' domain.

    Each cluster individually fails the per-cluster member gate, but their
    collective pooled member count crosses DOMAIN_DISCOVERY_POOL_MIN_MEMBERS.
    Every contributing cluster gets re-parented to the new domain node.
    """
    general = await _seed_general_domain(db)

    cluster_ids = []
    for i in range(3):
        c = PromptCluster(
            label=f"fragment-{i}", state="active", domain="general",
            parent_id=general.id, member_count=1, coherence=0.5,
            centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        )
        db.add(c)
        await db.flush()
        cluster_ids.append(c.id)
        db.add(Optimization(
            raw_prompt=f"backend prompt {i}", domain="general",
            domain_raw="backend", cluster_id=c.id, status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)

    assert "backend" in created

    # All three contributing clusters should be re-parented
    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.label == "backend",
        )
    )
    backend_node = result.scalar_one()
    for cid in cluster_ids:
        c = await db.get(PromptCluster, cid)
        assert c.parent_id == backend_node.id, (
            f"cluster {cid} not re-parented to backend (parent={c.parent_id})"
        )
        assert c.domain == "backend"


@pytest.mark.asyncio
async def test_pooled_pass_skips_inconsistent_cluster(db, mock_embedding):
    """Change A: clusters whose internal consistency < 60% contribute 0 members."""
    general = await _seed_general_domain(db)

    # Cluster 1: all backend (consistent) — contributes 1 member
    c1 = PromptCluster(
        label="consistent", state="active", domain="general",
        parent_id=general.id, member_count=1, coherence=0.5,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(c1)
    await db.flush()
    db.add(Optimization(
        raw_prompt="consistent backend", domain="general",
        domain_raw="backend", cluster_id=c1.id, status="completed",
    ))

    # Cluster 2: 50/50 backend/frontend (inconsistent) — contributes nothing
    c2 = PromptCluster(
        label="mixed", state="active", domain="general",
        parent_id=general.id, member_count=2, coherence=0.5,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(c2)
    await db.flush()
    db.add(Optimization(
        raw_prompt="mixed 1", domain="general",
        domain_raw="backend", cluster_id=c2.id, status="completed",
    ))
    db.add(Optimization(
        raw_prompt="mixed 2", domain="general",
        domain_raw="frontend", cluster_id=c2.id, status="completed",
    ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)

    # Only 1 backend member (pool floor = 3) — no promotion
    assert "backend" not in created
    assert "frontend" not in created


@pytest.mark.asyncio
async def test_pooled_pass_respects_ceiling(db, mock_embedding):
    """Change A: pooled promotions respect DOMAIN_COUNT_CEILING."""
    general = await _seed_general_domain(db)

    # Fill to ceiling (30 domains already)
    for i in range(30):
        db.add(PromptCluster(
            label=f"domain-{i}", state="domain",
            domain=f"domain-{i}", persistence=1.0,
        ))
    await db.flush()

    # Eligible pooled evidence (3 single-member clusters)
    for i in range(3):
        c = PromptCluster(
            label=f"fragment-{i}", state="active", domain="general",
            parent_id=general.id, member_count=1, coherence=0.5,
            centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        )
        db.add(c)
        await db.flush()
        db.add(Optimization(
            raw_prompt=f"newdomain {i}", domain="general",
            domain_raw="newdomain", cluster_id=c.id, status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)
    assert created == []


@pytest.mark.asyncio
async def test_pooled_does_not_duplicate_per_cluster_pass(db, mock_embedding):
    """Change A: labels promoted by per-cluster pass are skipped by pooled pass."""
    general = await _seed_general_domain(db)

    # Big cluster that WILL promote via per-cluster pass
    big = PromptCluster(
        label="big-backend", state="active", domain="general",
        parent_id=general.id, member_count=8, coherence=0.75,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(big)
    await db.flush()
    for i in range(8):
        db.add(Optimization(
            raw_prompt=f"big backend {i}", domain="general",
            domain_raw="backend", cluster_id=big.id, status="completed",
        ))

    # Additional small clusters that ALSO resolve to "backend"
    for i in range(3):
        c = PromptCluster(
            label=f"small-{i}", state="active", domain="general",
            parent_id=general.id, member_count=1, coherence=0.5,
            centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        )
        db.add(c)
        await db.flush()
        db.add(Optimization(
            raw_prompt=f"small backend {i}", domain="general",
            domain_raw="backend", cluster_id=c.id, status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)

    # "backend" appears exactly once — no duplicate from pooled pass
    assert created.count("backend") == 1

    # Only one backend domain node exists
    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.label == "backend",
        )
    )
    backend_nodes = result.scalars().all()
    assert len(backend_nodes) == 1


@pytest.mark.asyncio
async def test_bootstrap_threshold_relaxes_when_db_sparse(db, mock_embedding):
    """Change B: total_opts<20 → per-cluster floor relaxes 3→2.

    A single 2-member backend cluster on a sparse DB crosses the relaxed
    per-cluster member gate and promotes via the per-cluster pass.
    """
    general = await _seed_general_domain(db)

    cluster = PromptCluster(
        label="small-but-consistent", state="active", domain="general",
        parent_id=general.id, member_count=2, coherence=0.7,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.flush()
    for i in range(2):
        db.add(Optimization(
            raw_prompt=f"backend prompt {i}", domain="general",
            domain_raw="backend", cluster_id=cluster.id, status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)
    assert "backend" in created


@pytest.mark.asyncio
async def test_adaptive_threshold_restores_at_scale(db, mock_embedding):
    """Change B: total_opts>=20 → per-cluster floor restored to 3.

    A single 2-member cluster on a large DB fails the per-cluster gate.  The
    cross-cluster pooled pass (Change A) also requires >=3 pooled members,
    so a single 2-member cluster cannot promote by either path.
    """
    general = await _seed_general_domain(db)

    # Fill DB with 25 unrelated optimizations to cross the bootstrap threshold
    decoy = PromptCluster(
        label="decoy", state="active", domain="general",
        parent_id=general.id, member_count=25, coherence=0.5,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(decoy)
    await db.flush()
    for i in range(25):
        db.add(Optimization(
            raw_prompt=f"decoy {i}", domain="general",
            domain_raw="general", cluster_id=decoy.id, status="completed",
        ))

    # Candidate cluster: 2 backend members (below restored floor of 3)
    cluster = PromptCluster(
        label="two-backend", state="active", domain="general",
        parent_id=general.id, member_count=2, coherence=0.7,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.flush()
    for i in range(2):
        db.add(Optimization(
            raw_prompt=f"backend {i}", domain="general",
            domain_raw="backend", cluster_id=cluster.id, status="completed",
        ))
    await db.commit()

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider_resolver=lambda: None,
    )
    created = await engine._propose_domains(db)
    # 2 pooled backend members < POOL_MIN_MEMBERS(3) — no promotion
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
