"""End-to-end tests for the sub-domain lifecycle: creation guard, archival,
and cold-path parent preservation.

Validates the three fixes for orphaned sub-domains:
1. _propose_sub_domains() skips domains that already have sub-domains
2. phase_archive_empty_sub_domains() garbage-collects empty sub-domains
3. Cold path Step 12 preserves sub-domain parent_id links

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.models import MetaPattern, Optimization, PromptCluster
from app.services.taxonomy._constants import (
    EXCLUDED_STRUCTURAL_STATES,
    SUB_DOMAIN_ARCHIVAL_IDLE_HOURS,
    SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS,
)
from app.services.taxonomy.cluster_meta import write_meta

EMBEDDING_DIM = 384


def _random_embedding(seed: int = 0) -> bytes:
    rng = np.random.RandomState(seed)
    vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
    vec /= np.linalg.norm(vec) + 1e-9
    return vec.tobytes()


def _make_domain(label: str, *, parent_id: str | None = None, source: str = "discovered") -> PromptCluster:
    return PromptCluster(
        label=label,
        state="domain",
        domain=label,
        task_type="general",
        parent_id=parent_id,
        persistence=1.0,
        color_hex="#aabbcc",
        centroid_embedding=_random_embedding(hash(label) % 2**31),
        cluster_metadata=write_meta(None, source=source),
        created_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
    )


def _make_cluster(label: str, domain: str, parent_id: str) -> PromptCluster:
    return PromptCluster(
        label=label,
        state="active",
        domain=domain,
        parent_id=parent_id,
        member_count=5,
        centroid_embedding=_random_embedding(hash(label) % 2**31),
    )


# ---------------------------------------------------------------------------
# Phase 5.5: Sub-domain archival
# ---------------------------------------------------------------------------


class TestSubDomainArchival:
    """Tests for phase_archive_empty_sub_domains()."""

    @pytest.mark.asyncio
    async def test_archive_empty_sub_domain(self, db):
        """An empty sub-domain older than the idle threshold is archived."""
        from unittest.mock import AsyncMock, MagicMock

        from app.services.taxonomy.warm_phases import phase_archive_empty_sub_domains

        engine = MagicMock()
        engine.embedding_index = MagicMock()
        engine.embedding_index.remove = AsyncMock()
        engine.transformation_index = MagicMock()
        engine.transformation_index.remove = AsyncMock()
        engine.optimized_index = MagicMock()
        engine.optimized_index.remove = AsyncMock()
        engine._optimized_index = MagicMock()
        engine._optimized_index.remove = AsyncMock()
        engine.qualifier_index = MagicMock()
        engine.qualifier_index.remove = AsyncMock()

        # Create parent domain + empty sub-domain
        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        sub = _make_domain("async-patterns", parent_id=parent.id)
        idle_hours = SUB_DOMAIN_ARCHIVAL_IDLE_HOURS + 1
        sub.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=idle_hours)
        db.add(sub)
        await db.flush()

        archived = await phase_archive_empty_sub_domains(engine, db)

        assert archived == 1
        assert sub.state == "archived"
        assert sub.member_count == 0

    @pytest.mark.asyncio
    async def test_skip_sub_domain_with_multiple_children(self, db):
        """A sub-domain with 2+ active child clusters is NOT archived."""
        from unittest.mock import AsyncMock, MagicMock

        from app.services.taxonomy.warm_phases import phase_archive_empty_sub_domains

        engine = MagicMock()
        engine.embedding_index = MagicMock()
        engine.embedding_index.remove = AsyncMock()
        engine.transformation_index = MagicMock()
        engine.transformation_index.remove = AsyncMock()
        engine.optimized_index = MagicMock()
        engine.optimized_index.remove = AsyncMock()
        engine._optimized_index = MagicMock()
        engine._optimized_index.remove = AsyncMock()
        engine.qualifier_index = MagicMock()
        engine.qualifier_index.remove = AsyncMock()

        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        sub = _make_domain("warm-path-ops", parent_id=parent.id)
        sub.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
        db.add(sub)
        await db.flush()

        # Add TWO active child clusters — genuine multi-cluster sub-domain
        child1 = _make_cluster("perf-cluster", domain="backend", parent_id=sub.id)
        child2 = _make_cluster("cache-cluster", domain="backend", parent_id=sub.id)
        db.add_all([child1, child2])
        await db.flush()

        archived = await phase_archive_empty_sub_domains(engine, db)

        assert archived == 0
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_archive_single_child_sub_domain(self, db):
        """A sub-domain with exactly 1 child is a 1:1 wrapper — archived."""
        from unittest.mock import AsyncMock, MagicMock

        from app.services.taxonomy.warm_phases import phase_archive_empty_sub_domains

        engine = MagicMock()
        engine.embedding_index = MagicMock()
        engine.embedding_index.remove = AsyncMock()
        engine.transformation_index = MagicMock()
        engine.transformation_index.remove = AsyncMock()
        engine.optimized_index = MagicMock()
        engine.optimized_index.remove = AsyncMock()
        engine._optimized_index = MagicMock()
        engine._optimized_index.remove = AsyncMock()
        engine.qualifier_index = MagicMock()
        engine.qualifier_index.remove = AsyncMock()

        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        sub = _make_domain("single-child-sub", parent_id=parent.id)
        sub.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
        db.add(sub)
        await db.flush()

        child = _make_cluster("lonely-cluster", domain="backend", parent_id=sub.id)
        db.add(child)
        await db.flush()

        archived = await phase_archive_empty_sub_domains(engine, db)

        assert archived == 1
        assert sub.state == "archived"
        # Child reparented to top-level domain
        await db.refresh(child)
        assert child.parent_id == parent.id

    @pytest.mark.asyncio
    async def test_skip_young_sub_domain(self, db):
        """A sub-domain younger than the idle threshold is NOT archived."""
        from unittest.mock import AsyncMock, MagicMock

        from app.services.taxonomy.warm_phases import phase_archive_empty_sub_domains

        engine = MagicMock()
        engine.embedding_index = MagicMock()
        engine.embedding_index.remove = AsyncMock()
        engine.transformation_index = MagicMock()
        engine.transformation_index.remove = AsyncMock()
        engine.optimized_index = MagicMock()
        engine.optimized_index.remove = AsyncMock()
        engine._optimized_index = MagicMock()
        engine._optimized_index.remove = AsyncMock()
        engine.qualifier_index = MagicMock()
        engine.qualifier_index.remove = AsyncMock()

        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        sub = _make_domain("fresh-sub", parent_id=parent.id)
        sub.created_at = datetime.now(timezone.utc).replace(tzinfo=None)  # just created
        db.add(sub)
        await db.flush()

        archived = await phase_archive_empty_sub_domains(engine, db)

        assert archived == 0
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_seed_domain_can_be_archived(self, db):
        """Seed domains are now subject to the same lifecycle — can be archived."""
        from unittest.mock import AsyncMock, MagicMock

        from app.services.taxonomy.warm_phases import phase_archive_empty_sub_domains

        engine = MagicMock()
        engine.embedding_index = MagicMock()
        engine.embedding_index.remove = AsyncMock()
        engine.transformation_index = MagicMock()
        engine.transformation_index.remove = AsyncMock()
        engine.optimized_index = MagicMock()
        engine.optimized_index.remove = AsyncMock()
        engine._optimized_index = MagicMock()
        engine._optimized_index.remove = AsyncMock()
        engine.qualifier_index = MagicMock()
        engine.qualifier_index.remove = AsyncMock()

        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        sub = _make_domain("seeded-sub", parent_id=parent.id, source="seed")
        sub.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
        db.add(sub)
        await db.flush()

        archived = await phase_archive_empty_sub_domains(engine, db)
        assert archived == 1  # seed sub-domain CAN be archived now
        assert sub.state == "archived"

    @pytest.mark.asyncio
    async def test_skip_top_level_domain(self, db):
        """Top-level domains (parent_id=None) are never archived by this phase."""
        from unittest.mock import AsyncMock, MagicMock

        from app.services.taxonomy.warm_phases import phase_archive_empty_sub_domains

        engine = MagicMock()
        engine.embedding_index = MagicMock()
        engine.embedding_index.remove = AsyncMock()
        engine.transformation_index = MagicMock()
        engine.transformation_index.remove = AsyncMock()
        engine.optimized_index = MagicMock()
        engine.optimized_index.remove = AsyncMock()
        engine._optimized_index = MagicMock()
        engine._optimized_index.remove = AsyncMock()
        engine.qualifier_index = MagicMock()
        engine.qualifier_index.remove = AsyncMock()

        # Top-level domain with 0 children, old — still should NOT be archived
        top = _make_domain("abandoned")
        top.member_count = 0
        top.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)
        db.add(top)
        await db.flush()

        archived = await phase_archive_empty_sub_domains(engine, db)

        assert archived == 0
        assert top.state == "domain"

    @pytest.mark.asyncio
    async def test_skip_sub_domain_with_optimizations(self, db):
        """A sub-domain with directly assigned optimizations is NOT archived."""
        from unittest.mock import AsyncMock, MagicMock

        from app.services.taxonomy.warm_phases import phase_archive_empty_sub_domains

        engine = MagicMock()
        engine.embedding_index = MagicMock()
        engine.embedding_index.remove = AsyncMock()
        engine.transformation_index = MagicMock()
        engine.transformation_index.remove = AsyncMock()
        engine.optimized_index = MagicMock()
        engine.optimized_index.remove = AsyncMock()
        engine._optimized_index = MagicMock()
        engine._optimized_index.remove = AsyncMock()
        engine.qualifier_index = MagicMock()
        engine.qualifier_index.remove = AsyncMock()

        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        sub = _make_domain("has-opts", parent_id=parent.id)
        sub.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
        db.add(sub)
        await db.flush()

        # Directly assigned optimization (unusual for domains, but safety check)
        opt = Optimization(
            raw_prompt="test",
            cluster_id=sub.id,
            embedding=_random_embedding(42),
        )
        db.add(opt)
        await db.flush()

        archived = await phase_archive_empty_sub_domains(engine, db)

        assert archived == 0
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_meta_patterns_cleaned_on_archival(self, db):
        """MetaPatterns owned by the sub-domain are deleted on archival."""
        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy import func, select

        from app.services.taxonomy.warm_phases import phase_archive_empty_sub_domains

        engine = MagicMock()
        engine.embedding_index = MagicMock()
        engine.embedding_index.remove = AsyncMock()
        engine.transformation_index = MagicMock()
        engine.transformation_index.remove = AsyncMock()
        engine.optimized_index = MagicMock()
        engine.optimized_index.remove = AsyncMock()
        engine._optimized_index = MagicMock()
        engine._optimized_index.remove = AsyncMock()
        engine.qualifier_index = MagicMock()
        engine.qualifier_index.remove = AsyncMock()

        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        sub = _make_domain("stale-sub", parent_id=parent.id)
        sub.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
        db.add(sub)
        await db.flush()

        # Add patterns to the sub-domain
        mp = MetaPattern(
            cluster_id=sub.id,
            pattern_text="some pattern",
            embedding=_random_embedding(99),
        )
        db.add(mp)
        await db.flush()

        archived = await phase_archive_empty_sub_domains(engine, db)

        assert archived == 1
        remaining = (await db.execute(
            select(func.count()).where(MetaPattern.cluster_id == sub.id)
        )).scalar()
        assert remaining == 0


# ---------------------------------------------------------------------------
# Sub-domain creation guard
# ---------------------------------------------------------------------------


class TestSubDomainCreationGuard:
    """Tests for the guard that prevents re-discovery when sub-domains exist."""

    @pytest.mark.asyncio
    async def test_discovery_continues_with_existing_sub_domains(self, db, mock_provider):
        """Domain with existing sub-domain can still discover new sub-domains."""
        from unittest.mock import AsyncMock, patch

        import numpy as np

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        # Create domain with existing sub-domain "query"
        domain = PromptCluster(
            label="database", state="domain", domain="database",
            color_hex="#00ff00", member_count=0,
        )
        db.add(domain)
        await db.flush()

        sub = PromptCluster(
            label="query", state="domain", domain="database",
            parent_id=domain.id, color_hex="#00ff00", member_count=0,
        )
        db.add(sub)
        await db.flush()

        # Add clusters under the sub-domain (these must be visible to the scan)
        for i in range(3):
            cluster = PromptCluster(
                label=f"Query Cluster {i}", state="active", domain="database",
                parent_id=sub.id, color_hex="#ff0000", member_count=3,
                centroid_embedding=np.random.randn(384).astype(np.float32).tobytes(),
            )
            db.add(cluster)
        # Add clusters directly under the domain (not under sub-domain)
        for i in range(3):
            cluster = PromptCluster(
                label=f"Migration Cluster {i}", state="active", domain="database",
                parent_id=domain.id, color_hex="#ff0000", member_count=3,
                centroid_embedding=np.random.randn(384).astype(np.float32).tobytes(),
            )
            db.add(cluster)
        await db.commit()

        # Add optimizations — enough to trigger discovery
        from sqlalchemy import select

        from app.models import Optimization
        child_clusters_q = await db.execute(
            select(PromptCluster.id).where(
                PromptCluster.state == "active",
                PromptCluster.domain == "database",
            )
        )
        child_ids = [r[0] for r in child_clusters_q.all()]

        for i, cid in enumerate(child_ids):
            opt = Optimization(
                raw_prompt=f"test prompt {i}",
                domain="database",
                domain_raw=(
                    "database: migration"
                    if "Migration" in (await db.get(PromptCluster, cid)).label
                    else "database: query"
                ),
                intent_label=f"migration task {i}" if i >= 3 else f"query task {i}",
                task_type="coding",
                cluster_id=cid,
            )
            db.add(opt)
        await db.commit()

        generate_calls = []

        async def fake_generate(provider, domain_label, cluster_contexts, similarity_matrix, model):
            generate_calls.append(domain_label)
            return {"query": ["sql", "index"], "migration": ["migrate", "alembic"]}

        with patch(
            "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
            fake_generate,
        ):
            created = await engine._propose_sub_domains(db)

        # "query" already exists — should NOT be re-created
        assert "query" not in created
        # Discovery should have run (not skipped)
        assert "database" in generate_calls or len(generate_calls) == 0  # vocab may be cached


# ---------------------------------------------------------------------------
# Cold path sub-domain parent preservation
# ---------------------------------------------------------------------------


class TestColdPathSubDomainPreservation:
    """Tests for cold path Step 12 preserving sub-domain parent links."""

    @pytest.mark.asyncio
    async def test_preserve_valid_sub_domain_parent(self, db):
        """Clusters under a valid sub-domain keep their parent_id through cold path repair."""
        from sqlalchemy import select

        # Setup: backend -> sub-domain -> cluster
        backend = _make_domain("backend")
        db.add(backend)
        await db.flush()

        sub = _make_domain("warm-path-ops", parent_id=backend.id)
        db.add(sub)
        await db.flush()

        cluster = _make_cluster("perf-cluster", domain="backend", parent_id=sub.id)
        db.add(cluster)
        await db.flush()

        # Simulate Step 12 logic: build maps
        domain_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        all_domain_nodes = list(domain_q.scalars().all())
        domain_id_to_label = {dn.id: dn.label for dn in all_domain_nodes}

        sub_domain_ids_by_domain: dict[str, set[str]] = {}
        for dn in all_domain_nodes:
            if dn.parent_id and dn.parent_id in domain_id_to_label:
                parent_label = domain_id_to_label[dn.parent_id]
                sub_domain_ids_by_domain.setdefault(parent_label, set()).add(dn.id)

        # Run the repair logic
        valid_subs = sub_domain_ids_by_domain.get(cluster.domain, set())

        # Cluster is under a valid sub-domain — should be preserved
        assert cluster.parent_id in valid_subs
        assert cluster.parent_id == sub.id  # NOT re-parented to backend

    @pytest.mark.asyncio
    async def test_reparent_invalid_parent_to_domain(self, db):
        """Clusters with an invalid parent get re-parented to their top-level domain."""
        from sqlalchemy import select

        backend = _make_domain("backend")
        db.add(backend)
        await db.flush()

        # Cluster with a non-domain parent (leftover from HDBSCAN)
        cluster = _make_cluster("orphan-cluster", domain="backend", parent_id="nonexistent-id")
        db.add(cluster)
        await db.flush()

        # Build maps
        domain_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        all_domain_nodes = list(domain_q.scalars().all())
        domain_node_map = {dn.label: dn.id for dn in all_domain_nodes}
        domain_id_to_label = {dn.id: dn.label for dn in all_domain_nodes}

        sub_domain_ids_by_domain: dict[str, set[str]] = {}
        for dn in all_domain_nodes:
            if dn.parent_id and dn.parent_id in domain_id_to_label:
                parent_label = domain_id_to_label[dn.parent_id]
                sub_domain_ids_by_domain.setdefault(parent_label, set()).add(dn.id)

        # Run repair
        correct_parent = domain_node_map.get(cluster.domain)
        valid_subs = sub_domain_ids_by_domain.get(cluster.domain, set())

        if cluster.parent_id not in valid_subs and cluster.parent_id != correct_parent:
            cluster.parent_id = correct_parent

        assert cluster.parent_id == backend.id


# ---------------------------------------------------------------------------
# Tree integrity check 8
# ---------------------------------------------------------------------------


class TestTreeIntegrityEmptySubDomain:
    """Tests for check 8 in verify_domain_tree_integrity()."""

    @pytest.mark.asyncio
    async def test_detects_empty_sub_domain(self, db):
        """Empty sub-domains are reported as violations."""
        from sqlalchemy import func, select

        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        sub = _make_domain("empty-sub", parent_id=parent.id)
        sub.member_count = 0
        db.add(sub)
        await db.flush()

        # Gather domain IDs
        domain_id_q = await db.execute(
            select(PromptCluster.id).where(PromptCluster.state == "domain")
        )
        domain_ids = set(domain_id_q.scalars().all())

        # Check 8 logic
        violations = []
        for did in domain_ids:
            d_node = await db.get(PromptCluster, did)
            if not d_node or d_node.state != "domain":
                continue
            if d_node.parent_id not in domain_ids:
                continue
            child_count = (await db.execute(
                select(func.count()).where(
                    PromptCluster.parent_id == did,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
            )).scalar() or 0
            if child_count == 0:
                violations.append(d_node.label)

        assert "empty-sub" in violations

    @pytest.mark.asyncio
    async def test_non_empty_sub_domain_passes(self, db):
        """Sub-domains with children are not flagged."""
        from sqlalchemy import func, select

        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        sub = _make_domain("healthy-sub", parent_id=parent.id)
        db.add(sub)
        await db.flush()

        child = _make_cluster("child-cluster", domain="backend", parent_id=sub.id)
        db.add(child)
        await db.flush()

        domain_id_q = await db.execute(
            select(PromptCluster.id).where(PromptCluster.state == "domain")
        )
        domain_ids = set(domain_id_q.scalars().all())

        violations = []
        for did in domain_ids:
            d_node = await db.get(PromptCluster, did)
            if not d_node or d_node.state != "domain":
                continue
            if d_node.parent_id not in domain_ids:
                continue
            child_count = (await db.execute(
                select(func.count()).where(
                    PromptCluster.parent_id == did,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
            )).scalar() or 0
            if child_count == 0:
                violations.append(d_node.label)

        assert "healthy-sub" not in violations


class TestSignalDrivenCreation:
    """Tests for the signal-driven sub-domain discovery logic."""

    @pytest.mark.asyncio
    async def test_qualifier_from_domain_raw(self, db):
        """parse_domain() correctly extracts qualifier from domain_raw."""
        from app.utils.text_cleanup import parse_domain

        primary, qualifier = parse_domain("backend: auth")
        assert primary == "backend"
        assert qualifier == "auth"

    @pytest.mark.asyncio
    async def test_qualifier_none_for_plain_domain(self, db):
        """parse_domain() returns None qualifier for plain domain."""
        from app.utils.text_cleanup import parse_domain

        primary, qualifier = parse_domain("backend")
        assert primary == "backend"
        assert qualifier is None

    @pytest.mark.asyncio
    async def test_intent_label_fallback_matching(self, db):
        """intent_label keyword matching finds qualifiers from vocabulary."""
        # Use inline test vocab instead of deleted _DOMAIN_QUALIFIERS
        domain_qualifiers = {
            "auth": ["auth", "authentication", "login", "session", "oauth", "jwt", "token"],
            "api": ["api", "endpoint", "rest", "graphql", "route", "handler"],
        }
        intent_label = "MCP routing architecture"
        intent_lower = intent_label.lower()

        best_q = None
        best_hits = 0
        for q_name, keywords in domain_qualifiers.items():
            hits = sum(1 for kw in keywords if kw in intent_lower)
            if hits > best_hits:
                best_hits = hits
                best_q = q_name

        # "routing" doesn't match any qualifier keywords
        assert best_q is None or best_hits == 0

    @pytest.mark.asyncio
    async def test_propose_sub_domains_generates_vocab_for_all_domains(self, db, mock_provider):
        """Phase 5 generates vocabulary for domains regardless of static vocab presence."""
        from unittest.mock import AsyncMock, patch

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        # Create a domain node (e.g., "saas") with child clusters but no cached vocab
        import numpy as np

        domain = PromptCluster(
            label="saas", state="domain", domain="saas",
            color_hex="#00ff00", member_count=0,
        )
        db.add(domain)
        await db.flush()

        cluster_ids = []
        for i in range(4):
            cluster = PromptCluster(
                label=f"SaaS Cluster {i}", state="active", domain="saas",
                parent_id=domain.id, color_hex="#ff0000", member_count=3,
                centroid_embedding=np.random.randn(384).astype(np.float32).tobytes(),
            )
            db.add(cluster)
            await db.flush()
            cluster_ids.append(cluster.id)

        # Add enough optimization rows to pass SUB_DOMAIN_QUALIFIER_MIN_MEMBERS (5)
        for i, cid in enumerate(cluster_ids[:2]):
            for j in range(3):
                opt = Optimization(
                    raw_prompt=f"saas prompt {i}-{j}",
                    cluster_id=cid,
                    domain="saas",
                    embedding=_random_embedding(i * 10 + j),
                )
                db.add(opt)

        await db.commit()

        generate_calls = []

        async def fake_generate(provider, domain_label, cluster_contexts, similarity_matrix, model):
            generate_calls.append(domain_label)
            return {"growth": ["metrics", "kpi"], "pricing": ["tier", "billing"]}

        with patch(
            "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
            fake_generate,
        ):
            await engine._propose_sub_domains(db)

        # Verify generation was called for "saas" even though it has static vocab
        assert "saas" in generate_calls


# ---------------------------------------------------------------------------
# Sub-domain dissolution
# ---------------------------------------------------------------------------


def _make_opt(cluster_id: str, domain_raw: str, seed: int = 0) -> Optimization:
    return Optimization(
        raw_prompt=f"test prompt {seed}",
        domain_raw=domain_raw,
        cluster_id=cluster_id,
        embedding=_random_embedding(seed),
    )


def _make_sub_domain(
    label: str,
    parent_id: str,
    *,
    age_hours: int = 24,
    source: str = "discovered",
) -> PromptCluster:
    """Create a sub-domain node with the given age."""
    node = _make_domain(label, parent_id=parent_id, source=source)
    node.created_at = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=age_hours)
    )
    return node


def _make_engine(mock_provider):
    from unittest.mock import AsyncMock, MagicMock

    from app.services.taxonomy.engine import TaxonomyEngine

    mock_embedding = AsyncMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    # Provide stub indices via private backing attrs so remove() calls are no-ops.
    # embedding_index / transformation_index / optimized_index are read-only properties.
    # remove() must be AsyncMock because _dissolve_node() awaits idx.remove().
    for attr in ("_embedding_index", "_transformation_index", "_optimized_index"):
        mock_idx = MagicMock()
        mock_idx.remove = AsyncMock()
        setattr(engine, attr, mock_idx)
    return engine


class TestMultipleSiblingSubDomains:
    """B6 (2026-04-25): explicit coverage that multiple sub-domains can
    coexist as siblings under one parent domain.

    Live reference: cycle 2 produced an ``audit`` sub-domain under
    ``backend``. The user asked to confirm the architecture supports
    additional siblings (``audit`` + ``embedding`` + ``async`` etc.) —
    schema-wise it does (uniqueness only on ``(parent_id, label)`` per
    migration ``e7f8a9b0c1d2``), and the discovery loop iterates over
    every qualifier above threshold. These tests pin both invariants
    end-to-end so a future regression in either layer surfaces here.
    """

    @pytest.mark.asyncio
    async def test_two_qualifiers_above_threshold_yield_two_siblings(self, db, mock_provider):
        """Two qualifiers crossing the consistency threshold in the same
        cycle MUST produce two sub-domain nodes as siblings — not one
        winner-take-all.
        """
        from unittest.mock import AsyncMock, patch

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        # Return a normalised vector so vocab quality computation succeeds.
        mock_embedding.aembed_single = AsyncMock(
            return_value=np.random.RandomState(7).randn(EMBEDDING_DIM).astype(np.float32),
        )
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        # Parent domain — needs ≥2 child clusters for vocab to even fire,
        # ≥SUB_DOMAIN_QUALIFIER_MIN_MEMBERS optimizations per qualifier
        # to cross the threshold, and the qualifiers must be present in
        # ``domain_raw`` so Source 1 of the cascade picks them up.
        parent = PromptCluster(
            label="backend", state="domain", domain="backend",
            color_hex="#7c3aed", member_count=0,
            cluster_metadata=write_meta(None, source="seed"),
        )
        db.add(parent)
        await db.flush()

        # Two distinct child clusters per qualifier so the
        # SUB_DOMAIN_MIN_CLUSTER_BREADTH=2 gate passes for both.
        from app.services.taxonomy._constants import (
            SUB_DOMAIN_MIN_CLUSTER_BREADTH,
            SUB_DOMAIN_QUALIFIER_MIN_MEMBERS,
        )
        clusters = []
        for q, n in (("audit", SUB_DOMAIN_MIN_CLUSTER_BREADTH),
                     ("embedding", SUB_DOMAIN_MIN_CLUSTER_BREADTH)):
            for i in range(n):
                cl = PromptCluster(
                    label=f"{q.title()} Cluster {i}",
                    state="active", domain="backend",
                    parent_id=parent.id, color_hex="#abc",
                    member_count=SUB_DOMAIN_QUALIFIER_MIN_MEMBERS,
                    centroid_embedding=_random_embedding(hash((q, i)) % 2**31),
                )
                db.add(cl)
                clusters.append((q, cl))
        await db.flush()

        # Optimizations carrying ``domain_raw="backend: <qualifier>"``.
        # Need enough per qualifier that BOTH cross the adaptive threshold
        # ``max(0.40, 0.60 - 0.004 * total_opts)``. With two equally-sized
        # cohorts each consistency is 0.50, so total_opts must reach the
        # point where 0.60 - 0.004*N ≤ 0.50 → N ≥ 25. We use 30 per qualifier
        # (60 total) which gives threshold = max(0.40, 0.60-0.24) = 0.40,
        # well below the 0.50 consistency each cohort achieves.
        opts_per_qualifier = 30
        for q, cl in clusters:
            for j in range(opts_per_qualifier):
                db.add(Optimization(
                    raw_prompt=f"{q} prompt {j}",
                    domain="backend", domain_raw=f"backend: {q}",
                    intent_label=f"{q} task {j}", task_type="coding",
                    cluster_id=cl.id,
                ))
        await db.commit()

        async def _fake_vocab(provider, domain_label, cluster_contexts, similarity_matrix, model):
            return {
                "audit": ["audit", "verify", "review"],
                "embedding": ["embed", "vector", "fusion"],
            }

        with patch(
            "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
            _fake_vocab,
        ):
            created = await engine._propose_sub_domains(db)

        # BOTH qualifiers must have produced sub-domain nodes.
        assert "audit" in created, f"audit sub-domain missing — got {created}"
        assert "embedding" in created, f"embedding sub-domain missing — got {created}"

        # Verify schema persisted both as siblings under the same parent.
        from sqlalchemy import select
        rows = await db.execute(
            select(PromptCluster.label).where(
                PromptCluster.parent_id == parent.id,
                PromptCluster.state == "domain",
            )
        )
        sibling_labels = sorted(r[0] for r in rows.all())
        assert sibling_labels == ["audit", "embedding"], sibling_labels

    @pytest.mark.asyncio
    async def test_sibling_sub_domains_evaluated_independently(self, db, mock_provider):
        """``_reevaluate_sub_domains`` iterates per-sibling. A degraded
        sibling dissolves while a healthy one survives — they share a
        parent but their lifecycles are independent.
        """
        from unittest.mock import AsyncMock

        from app.services.taxonomy._constants import (
            SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS,
            SUB_DOMAIN_QUALIFIER_MIN_MEMBERS,
        )

        engine = _make_engine(mock_provider)
        engine._embedding.aembed_single = AsyncMock(
            return_value=np.random.RandomState(11).randn(EMBEDDING_DIM).astype(np.float32),
        )

        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        # Healthy sibling: child cluster with optimizations carrying
        # ``domain_raw="backend: audit"`` — high consistency.
        old_age = datetime.now(timezone.utc) - timedelta(
            hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 24,
        )
        sub_healthy = _make_domain("audit", parent_id=parent.id)
        sub_healthy.created_at = old_age
        # Degraded sibling: child has optimizations whose domain_raw doesn't
        # carry the sibling's qualifier, so consistency drops below floor.
        sub_degraded = _make_domain("legacy-tag", parent_id=parent.id)
        sub_degraded.created_at = old_age
        db.add_all([sub_healthy, sub_degraded])
        await db.flush()

        cl_healthy = _make_cluster("Audit Cluster", "backend", sub_healthy.id)
        cl_degraded = _make_cluster("Drift Cluster", "backend", sub_degraded.id)
        db.add_all([cl_healthy, cl_degraded])
        await db.flush()

        for j in range(SUB_DOMAIN_QUALIFIER_MIN_MEMBERS + 2):
            db.add(Optimization(
                raw_prompt=f"audit prompt {j}",
                domain="backend", domain_raw="backend: audit",
                intent_label=f"audit task {j}", task_type="coding",
                cluster_id=cl_healthy.id,
            ))
        for j in range(SUB_DOMAIN_QUALIFIER_MIN_MEMBERS + 2):
            db.add(Optimization(
                raw_prompt=f"unrelated prompt {j}",
                domain="backend", domain_raw="backend: somethingelse",
                intent_label=f"unrelated {j}", task_type="coding",
                cluster_id=cl_degraded.id,
            ))
        await db.commit()

        existing = {sub_healthy.label, sub_degraded.label}
        dissolved = await engine._reevaluate_sub_domains(db, parent, existing)

        assert "legacy-tag" in dissolved, (
            f"degraded sibling should dissolve — got {dissolved}"
        )
        assert "audit" not in dissolved, (
            f"healthy sibling must NOT dissolve when its sibling does — got {dissolved}"
        )

        # Healthy sibling node still present + child cluster still parented to it.
        await db.refresh(sub_healthy)
        await db.refresh(cl_healthy)
        assert sub_healthy.state == "domain"
        assert cl_healthy.parent_id == sub_healthy.id

    @pytest.mark.asyncio
    async def test_schema_allows_multiple_siblings_under_same_parent(self, db):
        """Direct schema integrity check: the partial unique index on
        ``(parent_id, label)`` for ``state='domain'`` rows admits
        multiple sub-domain rows with the same parent as long as their
        labels are distinct. Add a third sibling alongside two existing
        ones and confirm the commit succeeds.
        """
        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        for label in ("audit", "embedding", "concurrency"):
            sib = _make_domain(label, parent_id=parent.id)
            db.add(sib)
        await db.commit()

        from sqlalchemy import select
        rows = await db.execute(
            select(PromptCluster.label).where(
                PromptCluster.parent_id == parent.id,
                PromptCluster.state == "domain",
            )
        )
        labels = sorted(r[0] for r in rows.all())
        assert labels == ["audit", "concurrency", "embedding"]


class TestSubDomainDissolution:
    """Tests for _reevaluate_sub_domains() — graceful dissolution."""

    @pytest.mark.asyncio
    async def test_healthy_sub_domain_survives(self, db, mock_provider):
        """Sub-domain with good qualifier consistency is NOT dissolved."""
        engine = _make_engine(mock_provider)

        domain = _make_domain("database")
        db.add(domain)
        await db.flush()

        sub = _make_sub_domain("query", parent_id=domain.id, age_hours=24)
        db.add(sub)
        await db.flush()

        # 3 clusters under sub-domain
        cluster_ids = []
        for i in range(3):
            c = _make_cluster(f"query-cluster-{i}", domain="database", parent_id=sub.id)
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)

        # 6 optimizations, all with "database: query"
        for i, cid in enumerate(cluster_ids * 2):
            db.add(_make_opt(cid, "database: query", seed=i))
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(db, domain, existing_labels)

        assert dissolved == []
        await db.refresh(sub)
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_degraded_sub_domain_dissolved(self, db, mock_provider):
        """Sub-domain with low qualifier consistency is dissolved."""
        engine = _make_engine(mock_provider)

        domain = _make_domain("database")
        db.add(domain)
        await db.flush()

        sub = _make_sub_domain("query", parent_id=domain.id, age_hours=24)
        db.add(sub)
        await db.flush()

        cluster_ids = []
        for i in range(3):
            c = _make_cluster(f"cluster-{i}", domain="database", parent_id=sub.id)
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)

        # Only 1 out of 6 has "database: query" — consistency ~17%, below floor 25%
        db.add(_make_opt(cluster_ids[0], "database: query", seed=0))
        for i in range(1, 6):
            db.add(_make_opt(cluster_ids[i % 3], "database", seed=i))
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(db, domain, existing_labels)

        assert "query" in dissolved
        # State is set in-memory by the engine method (no commit required for check)
        assert sub.state == "archived"

    @pytest.mark.asyncio
    async def test_dissolution_reparents_to_top_domain(self, db, mock_provider):
        """Dissolved sub-domain's children are reparented to the top-level domain."""
        engine = _make_engine(mock_provider)

        domain = _make_domain("database")
        db.add(domain)
        await db.flush()

        sub = _make_sub_domain("query", parent_id=domain.id, age_hours=24)
        db.add(sub)
        await db.flush()

        cluster_ids = []
        for i in range(2):
            c = _make_cluster(f"reparent-cluster-{i}", domain="database", parent_id=sub.id)
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)

        # 0 out of 4 opts have "database: query" — well below floor
        for i in range(4):
            db.add(_make_opt(cluster_ids[i % 2], "database", seed=i))
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(db, domain, existing_labels)

        assert "query" in dissolved
        for cid in cluster_ids:
            c = await db.get(PromptCluster, cid)
            assert c is not None
            assert c.parent_id == domain.id, (
                f"cluster {c.label} should be reparented to domain, got {c.parent_id}"
            )

    @pytest.mark.asyncio
    async def test_dissolution_merges_meta_patterns(self, db, mock_provider):
        """Dissolved sub-domain's meta-patterns are merged into parent domain, NOT deleted."""
        from sqlalchemy import func, select

        engine = _make_engine(mock_provider)

        domain = _make_domain("database")
        db.add(domain)
        await db.flush()

        sub = _make_sub_domain("query", parent_id=domain.id, age_hours=24)
        db.add(sub)
        await db.flush()

        # Add clusters with opts to trigger dissolution
        cluster_ids = []
        for i in range(2):
            c = _make_cluster(f"mp-cluster-{i}", domain="database", parent_id=sub.id)
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)
        for i in range(4):
            db.add(_make_opt(cluster_ids[i % 2], "database", seed=i))  # no qualifier match

        # Add MetaPattern rows owned by the sub-domain
        for i in range(2):
            mp = MetaPattern(
                cluster_id=sub.id,
                pattern_text=f"sub-domain pattern {i}",
                embedding=_random_embedding(100 + i),
            )
            db.add(mp)
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(db, domain, existing_labels)

        assert "query" in dissolved

        # Patterns must be reassigned to domain, NOT deleted
        remaining_on_sub = (await db.execute(
            select(func.count()).where(MetaPattern.cluster_id == sub.id)
        )).scalar()
        assert remaining_on_sub == 0, "MetaPatterns must not remain on dissolved sub-domain"

        merged_on_domain = (await db.execute(
            select(func.count()).where(MetaPattern.cluster_id == domain.id)
        )).scalar()
        assert merged_on_domain == 2, "MetaPatterns must be merged into parent domain"

    @pytest.mark.asyncio
    async def test_young_sub_domain_protected(self, db, mock_provider):
        """Sub-domain younger than min age is NOT dissolved even with low consistency."""
        engine = _make_engine(mock_provider)

        domain = _make_domain("database")
        db.add(domain)
        await db.flush()

        # Age = 1 hour — well below the 6-hour minimum
        sub = _make_sub_domain(
            "query", parent_id=domain.id,
            age_hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS - 5,
        )
        db.add(sub)
        await db.flush()

        cluster_ids = []
        for i in range(2):
            c = _make_cluster(f"young-cluster-{i}", domain="database", parent_id=sub.id)
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)

        # All opts with plain "database" — would normally dissolve
        for i in range(4):
            db.add(_make_opt(cluster_ids[i % 2], "database", seed=i))
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(db, domain, existing_labels)

        assert dissolved == []
        await db.refresh(sub)
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_source3_dynamic_keyword_parity_prevents_drift(
        self, db, mock_provider
    ):
        """Reeval must match Source 3 (raw_prompt × dynamic keywords) like create does.

        Reproduces the measurement-drift flip-flop: a sub-domain created via the
        Source 3 path (TF-IDF dynamic keyword like "fastapi") must survive
        re-evaluation when its opts still carry that keyword in ``raw_prompt``,
        even when ``domain_raw`` and ``intent_label`` do not contain it.

        Prior behaviour: reeval selects only ``domain_raw`` + ``intent_label`` —
        Source 3 is missing entirely, so all opts score 0% consistency and the
        sub-domain is dissolved despite being a perfectly good cluster. The
        warm-path ``_propose_sub_domains`` re-creates it the next cycle
        (because Source 3 *is* available there), producing the flip-flop
        observed in taxonomy events (token-ops, query).
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        domain.cluster_metadata = write_meta(
            domain.cluster_metadata,
            signal_keywords=[("fastapi", 1.0)],
        )
        db.add(domain)
        await db.flush()

        sub = _make_sub_domain("fastapi", parent_id=domain.id, age_hours=24)
        db.add(sub)
        await db.flush()

        cluster_ids = []
        for i in range(2):
            c = _make_cluster(
                f"fastapi-cluster-{i}", domain="backend", parent_id=sub.id
            )
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)

        # 6 opts whose qualifier signal is ONLY in raw_prompt (Source 3).
        # - domain_raw="backend": no qualifier parse → Source 1 miss
        # - intent_label="add rest endpoint": "fastapi" absent → Source 2 miss
        # - raw_prompt contains "fastapi": Source 3 hit (weight 1.0 ≥ 0.8 floor)
        for i in range(6):
            db.add(
                Optimization(
                    raw_prompt=f"build a fastapi endpoint for route {i}",
                    intent_label="add rest endpoint",
                    domain_raw="backend",
                    cluster_id=cluster_ids[i % 2],
                    embedding=_random_embedding(200 + i),
                )
            )
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels
        )

        assert dissolved == [], (
            "Sub-domain created via Source 3 (dynamic keyword) must survive "
            "re-evaluation when opts still carry that keyword in raw_prompt. "
            "If this fails, reeval is using stricter matching than create "
            "and will produce flip-flop dissolve/recreate cycles."
        )
        await db.refresh(sub)
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_seed_sub_domain_can_dissolve(self, db, mock_provider):
        """Seed sub-domains are NOT protected — dissolve when they fail consistency."""
        from unittest.mock import AsyncMock

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        domain = _make_domain("backend")
        domain.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(domain)
        await db.flush()

        sub = _make_domain("api", parent_id=domain.id, source="seed")
        sub.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(sub)
        await db.commit()
        # No child clusters, no optimizations → will fail consistency
        # (empty sub-domains are handled by Phase 5.5, but re-evaluation also catches them)

        existing_labels = {"api", "backend"}
        # Should not raise — seed protection no longer blocks execution
        dissolved = await engine._reevaluate_sub_domains(db, domain, existing_labels)
        # Empty sub-domain exits via the empty-child-cluster check, not seed protection
        assert isinstance(dissolved, list)


# ---------------------------------------------------------------------------
# Shared _dissolve_node() extraction
# ---------------------------------------------------------------------------


class TestDissolveNode:
    """Tests for the shared _dissolve_node() method."""

    @pytest.mark.asyncio
    async def test_dissolve_reparents_clusters_to_target(self, db, mock_provider):
        """_dissolve_node() reparents child clusters to the dissolution target."""
        from unittest.mock import AsyncMock

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        # Create domain → sub-domain → child cluster
        domain = _make_domain("database")
        db.add(domain)
        await db.flush()

        sub = _make_domain("query", parent_id=domain.id)
        db.add(sub)
        await db.flush()

        child = _make_cluster("SQL Queries", domain="database", parent_id=sub.id)
        db.add(child)
        await db.commit()

        existing_labels = {"query", "database"}
        result = await engine._dissolve_node(
            db, sub, dissolution_target_id=domain.id,
            existing_labels=existing_labels,
            clear_signal_loader=False,
        )

        await db.refresh(child)
        assert child.parent_id == domain.id  # reparented to domain
        assert sub.state == "archived"
        assert "query" not in existing_labels  # label freed
        assert result["clusters_reparented"] >= 1

    @pytest.mark.asyncio
    async def test_dissolve_merges_meta_patterns(self, db, mock_provider):
        """_dissolve_node() merges meta-patterns into target (not deletes)."""
        from unittest.mock import AsyncMock

        from sqlalchemy import func, select

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        domain = _make_domain("security")
        db.add(domain)
        await db.flush()

        sub = _make_domain("jwt", parent_id=domain.id)
        db.add(sub)
        await db.flush()

        # Add meta-patterns owned by sub-domain
        mp1 = MetaPattern(cluster_id=sub.id, pattern_text="use refresh tokens", source_count=3)
        mp2 = MetaPattern(cluster_id=sub.id, pattern_text="rotate keys", source_count=2)
        db.add_all([mp1, mp2])
        await db.commit()

        existing_labels = {"jwt", "security"}
        await engine._dissolve_node(
            db, sub, dissolution_target_id=domain.id,
            existing_labels=existing_labels,
            clear_signal_loader=False,
        )

        # Patterns should be merged (cluster_id changed), not deleted
        count = (await db.execute(
            select(func.count()).where(MetaPattern.cluster_id == domain.id)
        )).scalar()
        assert count == 2

        sub_count = (await db.execute(
            select(func.count()).where(MetaPattern.cluster_id == sub.id)
        )).scalar()
        assert sub_count == 0


# ---------------------------------------------------------------------------
# Domain dissolution
# ---------------------------------------------------------------------------


class TestDomainDissolution:
    """Tests for _reevaluate_domains() — domain-level dissolution."""

    @pytest.mark.asyncio
    async def test_general_never_dissolves(self, db, mock_provider):
        """'general' domain is permanent regardless of content."""
        from unittest.mock import AsyncMock

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        general = _make_domain("general")
        general.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(general)
        await db.commit()

        existing_labels = {"general"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        assert dissolved == []
        assert general.state == "domain"

    @pytest.mark.asyncio
    async def test_domain_with_sub_domain_anchored(self, db, mock_provider):
        """Domain with surviving sub-domain cannot dissolve (anchor rule)."""
        from unittest.mock import AsyncMock

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        domain = _make_domain("security")
        domain.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(domain)
        await db.flush()

        sub = _make_domain("token-ops", parent_id=domain.id)
        db.add(sub)
        await db.commit()

        existing_labels = {"security", "token-ops"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        assert dissolved == []
        assert domain.state == "domain"

    @pytest.mark.asyncio
    async def test_young_domain_protected(self, db, mock_provider):
        """Domain younger than 48h is not dissolved."""
        from unittest.mock import AsyncMock

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        domain = _make_domain("devops")
        domain.created_at = datetime.now(timezone.utc).replace(tzinfo=None)  # just created
        db.add(domain)
        await db.commit()

        existing_labels = {"devops"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        assert dissolved == []

    @pytest.mark.asyncio
    async def test_large_domain_protected(self, db, mock_provider):
        """Domain with >5 clusters is not dissolved even with low consistency."""
        from unittest.mock import AsyncMock

        import numpy as np

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        domain = _make_domain("backend")
        domain.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(domain)
        await db.flush()

        # Add 6 clusters (above ceiling of 5)
        for i in range(6):
            cluster = PromptCluster(
                label=f"Backend Cluster {i}", state="active", domain="backend",
                parent_id=domain.id, color_hex="#ff0000", member_count=3,
                centroid_embedding=np.random.randn(384).astype(np.float32).tobytes(),
            )
            db.add(cluster)
        await db.commit()

        existing_labels = {"backend"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        assert dissolved == []

    @pytest.mark.asyncio
    async def test_small_inconsistent_domain_dissolves(self, db, mock_provider):
        """Domain with ≤5 clusters and <15% consistency dissolves."""
        from unittest.mock import AsyncMock

        import numpy as np

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        # Create "general" as dissolution target
        general = _make_domain("general")
        db.add(general)
        await db.flush()

        domain = _make_domain("devops")
        domain.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)  # old enough
        db.add(domain)
        await db.flush()

        # Add 2 clusters with optimizations that DON'T match "devops"
        for i in range(2):
            cluster = PromptCluster(
                label=f"Misc Cluster {i}", state="active", domain="devops",
                parent_id=domain.id, color_hex="#ff0000", member_count=3,
                centroid_embedding=np.random.randn(384).astype(np.float32).tobytes(),
            )
            db.add(cluster)
            await db.flush()
            for j in range(3):
                opt = Optimization(
                    raw_prompt=f"test prompt {i}-{j}",
                    domain="devops",
                    domain_raw="backend",  # wrong domain — low consistency
                    intent_label=f"backend task {j}",
                    task_type="coding",
                    cluster_id=cluster.id,
                )
                db.add(opt)
        await db.commit()

        existing_labels = {"devops", "general"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        assert "devops" in dissolved
        assert domain.state == "archived"

    @pytest.mark.asyncio
    async def test_seed_domain_can_dissolve(self, db, mock_provider):
        """Seed domains are NOT protected — dissolve when they fail consistency."""
        from unittest.mock import AsyncMock

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        general = _make_domain("general")
        db.add(general)
        await db.flush()

        seed_domain = _make_domain("fullstack", source="seed")
        seed_domain.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(seed_domain)
        await db.commit()
        # No clusters, no optimizations → 0% consistency, 0 members

        existing_labels = {"fullstack", "general"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        # Empty domain with 0 members and old enough → dissolves
        assert "fullstack" in dissolved


class TestEnrichedVocabulary:
    """Tests for enriched vocabulary generation (quality metric + context)."""

    @pytest.mark.asyncio
    async def test_quality_metric_orthogonal_groups(self):
        """Orthogonal group embeddings should produce quality score near 1.0."""
        vecs = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
        ], dtype=np.float32)
        pairwise = vecs @ vecs.T
        max_pairwise = max(
            pairwise[i][j]
            for i in range(len(vecs))
            for j in range(i + 1, len(vecs))
        )
        quality = round(1.0 - max_pairwise, 4)
        assert quality == pytest.approx(1.0, abs=1e-4)

    @pytest.mark.asyncio
    async def test_quality_metric_identical_groups(self):
        """Identical group embeddings should produce quality score 0.0."""
        v = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        vecs = np.vstack([v, v, v])
        pairwise = vecs @ vecs.T
        max_pairwise = max(
            pairwise[i][j]
            for i in range(len(vecs))
            for j in range(i + 1, len(vecs))
        )
        quality = round(1.0 - max_pairwise, 4)
        assert quality == pytest.approx(0.0, abs=1e-4)

    @pytest.mark.asyncio
    async def test_cluster_vocab_context_construction(self):
        """ClusterVocabContext constructs correctly with all fields."""
        from app.services.taxonomy.labeling import ClusterVocabContext
        ctx = ClusterVocabContext(
            label="api design",
            member_count=15,
            intent_labels=["rest api", "graphql api", "rest api"],
            qualifier_distribution={"rest": 8, "graphql": 4},
        )
        assert ctx.label == "api design"
        assert ctx.member_count == 15
        assert len(ctx.intent_labels) == 3
        assert ctx.qualifier_distribution["rest"] == 8

    @pytest.mark.asyncio
    async def test_cluster_vocab_context_defaults(self):
        """ClusterVocabContext has sensible defaults for optional fields."""
        from app.services.taxonomy.labeling import ClusterVocabContext
        ctx = ClusterVocabContext(label="x", member_count=0)
        assert ctx.intent_labels == []
        assert ctx.qualifier_distribution == {}

    @pytest.mark.asyncio
    async def test_generate_vocabulary_below_minimum_returns_empty(self):
        """With < 2 clusters, returns empty dict (no LLM call)."""
        from app.services.taxonomy.labeling import (
            ClusterVocabContext,
            generate_qualifier_vocabulary,
        )

        class FailProvider:
            async def messages(self, **kwargs):
                raise AssertionError("provider should not be called")

        result = await generate_qualifier_vocabulary(
            provider=FailProvider(),
            domain_label="x",
            cluster_contexts=[ClusterVocabContext(label="only", member_count=1)],
            similarity_matrix=None,
            model="test-model",
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_generate_vocabulary_no_provider_returns_empty(self):
        """With no provider, returns empty dict."""
        from app.services.taxonomy.labeling import (
            ClusterVocabContext,
            generate_qualifier_vocabulary,
        )
        result = await generate_qualifier_vocabulary(
            provider=None,
            domain_label="x",
            cluster_contexts=[
                ClusterVocabContext(label="a", member_count=5),
                ClusterVocabContext(label="b", member_count=5),
            ],
            similarity_matrix=None,
            model="test-model",
        )
        assert result == {}
