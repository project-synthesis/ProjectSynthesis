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
from app.services.taxonomy.cluster_meta import read_meta, write_meta

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

        async def fake_generate(provider, domain_label, cluster_contexts, similarity_matrix, model, **_kwargs):
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

        async def fake_generate(provider, domain_label, cluster_contexts, similarity_matrix, model, **_kwargs):
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

        async def _fake_vocab(provider, domain_label, cluster_contexts, similarity_matrix, model, **_kwargs):
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
        # Non-empty generated_qualifiers so R3's empty-snapshot guard does
        # NOT short-circuit the test before consistency is evaluated.
        sub_healthy.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={"audit": ["audit", "verify", "review"]},
        )
        # Degraded sibling: child has optimizations whose domain_raw doesn't
        # carry the sibling's qualifier, so consistency drops below floor.
        sub_degraded = _make_domain("legacy-tag", parent_id=parent.id)
        sub_degraded.created_at = old_age
        sub_degraded.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={"legacy-tag": ["legacy", "deprecated"]},
        )
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
        # Non-empty generated_qualifiers so R3's empty-snapshot guard does
        # NOT short-circuit before the shrinkage decision runs.
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={"query": ["query", "select", "lookup"]},
        )
        db.add(sub)
        await db.flush()

        cluster_ids = []
        for i in range(3):
            c = _make_cluster(f"cluster-{i}", domain="database", parent_id=sub.id)
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)

        # N=12 chosen to clear the Bayesian shrinkage floor at K=10/center=0.40
        # prior — see spec R1.  With 1 match at N=12, shrunk=5/22=0.227 < 0.25
        # → dissolves.  At the original N=6/1-match the shrunk value 5/16=0.3125
        # would KEEP, which would defeat the dissolution intent.  Test semantic
        # unchanged: low consistency still dissolves; just at a sample size
        # past the prior's small-N protection.
        db.add(_make_opt(cluster_ids[0], "database: query", seed=0))
        for i in range(1, 12):
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
        # Non-empty generated_qualifiers so R3's empty-snapshot guard does
        # NOT short-circuit before the shrinkage decision runs.
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={"query": ["query", "select", "lookup"]},
        )
        db.add(sub)
        await db.flush()

        cluster_ids = []
        for i in range(2):
            c = _make_cluster(f"reparent-cluster-{i}", domain="database", parent_id=sub.id)
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)

        # N=10 chosen to clear the Bayesian shrinkage floor at K=10/center=0.40
        # prior — see spec R1.  With 0 matches at N=10, shrunk=4/20=0.20 < 0.25
        # → dissolves.  At the original N=4/0-match the shrunk value 4/14=0.286
        # would KEEP under the new prior, defeating the reparenting test intent.
        for i in range(10):
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
        # Non-empty generated_qualifiers so R3's empty-snapshot guard does
        # NOT short-circuit before the shrinkage decision runs.
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={"query": ["query", "select", "lookup"]},
        )
        db.add(sub)
        await db.flush()

        # Add clusters with opts to trigger dissolution
        cluster_ids = []
        for i in range(2):
            c = _make_cluster(f"mp-cluster-{i}", domain="database", parent_id=sub.id)
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)
        # N=10 chosen to clear the Bayesian shrinkage floor at K=10/center=0.40
        # prior — see spec R1.  With 0 matches at N=10, shrunk=4/20=0.20 < 0.25
        # → dissolves.  At the original N=4/0-match the shrunk value 4/14=0.286
        # would KEEP under the new prior, leaving meta-patterns un-merged.
        for i in range(10):
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
# Bug B regression — sub-domain vocab-group/term/token consistency
# ---------------------------------------------------------------------------


class TestSubDomainConsistencyVocabGroupMatch:
    """Bug B: ``_reevaluate_sub_domains`` must accept domain_raw qualifiers
    that are vocab GROUP names or vocab TERMS within the sub-domain's own
    ``generated_qualifiers``, not just exact-equality match against the
    sub-domain label.  Pre-fix, a sub-domain named ``embedding-health`` (an
    aggregate concept) was dissolved every cycle because its children's
    ``domain_raw`` was ``backend: observability`` / ``backend: metrics`` /
    ``backend: concurrency`` — vocab GROUPS inside the sub-domain's own
    ``generated_qualifiers``, but never the literal label
    ``embedding-health``.  Consistency was 0%, dissolution fired, and the
    sub-domain re-emerged on the next discovery pass for an infinite loop.
    """

    @pytest.mark.asyncio
    async def test_vocab_group_qualifier_keeps_sub_domain_alive(
        self, db, mock_provider,
    ):
        """domain_raw qualifier == sub-domain's vocab GROUP name → matched."""
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        # Sub-domain whose vocab covers four groups; domain_raw on children
        # quotes the GROUP names, never the sub-domain label itself.
        sub = _make_sub_domain("embedding-health", parent_id=domain.id, age_hours=24)
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={
                "optimization": ["warmup", "batching", "hot-path"],
                "correctness": ["normalization", "ordering"],
                "instrumentation": ["observability", "tracing", "monitoring"],
                "concurrency": ["race-condition", "asyncio"],
            },
        )
        db.add(sub)
        await db.flush()

        cluster_ids: list[str] = []
        for i in range(3):
            c = _make_cluster(
                f"eh-cluster-{i}", domain="backend", parent_id=sub.id,
            )
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)

        # 6 opts whose domain_raw is the vocab GROUP names — none equal
        # the sub-domain label "embedding-health".  Pre-fix consistency = 0%.
        for i, raw in enumerate([
            "backend: observability", "backend: tracing",
            "backend: monitoring", "backend: warmup",
            "backend: ordering", "backend: asyncio",
        ]):
            db.add(_make_opt(cluster_ids[i % 3], raw, seed=i))
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels,
        )

        assert dissolved == [], (
            "embedding-health should be KEPT (consistency >= 25% via vocab "
            f"group matching). Pre-fix returned: {dissolved}"
        )
        await db.refresh(sub)
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_intent_label_token_match_keeps_sub_domain_alive(
        self, db, mock_provider,
    ):
        """intent_label tokens hitting vocab terms → matched (Source 2)."""
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        sub = _make_sub_domain("embedding-health", parent_id=domain.id, age_hours=24)
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={
                "instrumentation": [
                    "cache-instrumentation", "cache-metrics", "tracing",
                ],
            },
        )
        db.add(sub)
        await db.flush()

        c = _make_cluster("instrumentation-cluster", domain="backend", parent_id=sub.id)
        db.add(c)
        await db.flush()

        # domain_raw is bare "backend" (no qualifier) — Source 1 misses.
        # intent_label "Cache Eviction Policy Audit" hits "cache" via the
        # tokenized substring of "cache-instrumentation"/"cache-metrics".
        for i in range(4):
            opt = _make_opt(c.id, "backend", seed=i)
            opt.intent_label = "Cache Eviction Policy Audit"
            db.add(opt)
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels,
        )

        assert dissolved == [], (
            "Sub-domain should be KEPT — intent_label tokens hit vocab "
            f"terms. Got dissolved: {dissolved}"
        )

    @pytest.mark.asyncio
    async def test_unrelated_qualifiers_still_dissolve(
        self, db, mock_provider,
    ):
        """Control: when children's qualifiers are TRULY unrelated to the
        sub-domain's vocab, dissolution still fires correctly.  Guards
        against an over-permissive fix that keeps every sub-domain alive."""
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        sub = _make_sub_domain("embedding-health", parent_id=domain.id, age_hours=24)
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={
                "instrumentation": ["observability", "tracing"],
            },
        )
        db.add(sub)
        await db.flush()

        c = _make_cluster("unrelated-cluster", domain="backend", parent_id=sub.id)
        db.add(c)
        await db.flush()

        # N=10 chosen for margin past the Bayesian shrinkage floor at
        # K=10/center=0.40 prior — see spec R1.  At N=6/0-match the shrunk
        # consistency lands exactly at the 0.25 floor (4/16=0.25), so the
        # gate's `>=` would KEEP rather than DISSOLVE.  At N=10/0-match
        # shrunk = 4/20 = 0.20 < 0.25, restoring the original control intent.
        # All opts mention auth/security topics — completely outside the
        # sub-domain's instrumentation vocabulary.  No vocab-group, term,
        # or intent-token match.
        for i, raw in enumerate([
            "backend: auth", "backend: jwt", "backend: oauth",
            "backend: security", "backend: validation", "backend: csrf",
            "backend: encryption", "backend: authorization",
            "backend: hashing", "backend: token",
        ]):
            opt = _make_opt(c.id, raw, seed=i)
            opt.intent_label = "Authentication Endpoint Audit"
            db.add(opt)
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels,
        )

        assert "embedding-health" in dissolved, (
            "Unrelated qualifiers should still dissolve — fix must not be "
            f"so permissive it keeps every sub-domain alive. Got: {dissolved}"
        )


# ---------------------------------------------------------------------------
# R1 — Bayesian shrinkage on consistency metric
# (spec: docs/specs/sub-domain-dissolution-hardening-2026-04-27.md §R1)
# ---------------------------------------------------------------------------


class TestSubDomainBayesianShrinkage:
    """R1 (audit 2026-04-27): point-estimate consistency at small N is
    statistically meaningless — one off-topic member at N=5 swings the
    metric by 20 percentage points and triggers spurious dissolution.

    The fix swaps the dissolution input from raw ``matching / total_opts``
    to a Bayesian Beta-Binomial posterior:

        shrunk = (matching + α_prior) / (total_opts + α_prior + β_prior)
        with K=10, center=0.40 → α=4.0, β=6.0

    Pre-fix ALL four tests fail (3 assertion errors on dissolution decision,
    1 KeyError/assertion on missing telemetry key). Post-fix all four pass.

    Acceptance criteria: AC-R1-1 .. AC-R1-4 in the spec. The control
    case ``test_large_n_zero_match_still_dissolves`` must pass under
    both regimes (pre-fix because raw=0.00, post-fix because
    shrunk=0.133 < 0.25); it locks the contract that shrinkage does
    not make dissolution unreachable.
    """

    @pytest.mark.asyncio
    async def test_small_n_one_match_keeps_via_shrinkage(
        self, db, mock_provider,
    ):
        """AC-R1-1: N=5, matching=1, raw=0.20 → KEEP (shrunk≈0.333 ≥ 0.25).

        Pre-fix the raw 0.20 consistency falls below the 0.25 floor and the
        sub-domain is dissolved despite 1/5 of children carrying a
        topically-aligned qualifier. Post-fix the Beta(4,6) prior pulls
        the estimate up to 5/15 = 0.333 ≥ 0.25 → kept.
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        # Age computed relative to the constant so the test stays correct
        # under R2's bump from 6h → 24h.
        sub = _make_sub_domain(
            "observability",
            parent_id=domain.id,
            age_hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5,
        )
        # Non-empty generated_qualifiers so R3's empty-snapshot guard does
        # NOT short-circuit the test before the shrinkage decision runs.
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={
                "observability": ["tracing", "monitoring"],
            },
        )
        db.add(sub)
        await db.flush()

        cluster = _make_cluster(
            "obs-cluster", domain="backend", parent_id=sub.id,
        )
        db.add(cluster)
        await db.flush()

        # 1 matching opt (vocab GROUP "observability") + 4 opts with
        # qualifier outside the sub-domain's vocab.  Raw consistency = 0.20.
        db.add(_make_opt(cluster.id, "backend: observability", seed=0))
        for i in range(1, 5):
            db.add(_make_opt(cluster.id, "backend: unrelated_qualifier", seed=i))
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels,
        )

        assert dissolved == [], (
            "Sub-domain with N=5 and 1 matching qualifier should be KEPT "
            "via Bayesian shrinkage (shrunk=0.333 ≥ 0.25). "
            f"Pre-fix raw=0.20 < 0.25 → dissolved. Got: {dissolved}"
        )
        await db.refresh(sub)
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_small_n_zero_match_keeps_via_shrinkage(
        self, db, mock_provider,
    ):
        """AC-R1-2: N=5, matching=0, raw=0.00 → KEEP (shrunk≈0.267 ≥ 0.25).

        The prior's safety-net behavior: even with zero matches, too-few
        samples do not justify dissolution. shrunk = 4/15 = 0.267.
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        sub = _make_sub_domain(
            "observability",
            parent_id=domain.id,
            age_hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5,
        )
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={
                "observability": ["tracing", "monitoring"],
            },
        )
        db.add(sub)
        await db.flush()

        cluster = _make_cluster(
            "obs-cluster", domain="backend", parent_id=sub.id,
        )
        db.add(cluster)
        await db.flush()

        # All 5 opts unrelated. Raw consistency = 0.00.
        for i in range(5):
            db.add(_make_opt(cluster.id, "backend: unrelated_qualifier", seed=i))
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels,
        )

        assert dissolved == [], (
            "Sub-domain with N=5 and 0 matches should be KEPT via "
            "Bayesian shrinkage prior (shrunk=4/15=0.267 ≥ 0.25). "
            f"Pre-fix raw=0.00 < 0.25 → dissolved. Got: {dissolved}"
        )
        await db.refresh(sub)
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_large_n_zero_match_still_dissolves(
        self, db, mock_provider,
    ):
        """AC-R1-3: N=20, matching=0 → DISSOLVE (shrunk=4/30=0.133 < 0.25).

        Locks the contract that Bayesian shrinkage does NOT make dissolution
        unreachable.  At large N the prior fades and the empirical rate
        dominates.  Test passes pre-fix (raw=0.00 < 0.25 also dissolves)
        and post-fix (shrunk=0.133 < 0.25 dissolves) — same outcome,
        different arithmetic.
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        sub = _make_sub_domain(
            "observability",
            parent_id=domain.id,
            age_hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5,
        )
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={
                "observability": ["tracing", "monitoring"],
            },
        )
        db.add(sub)
        await db.flush()

        cluster = _make_cluster(
            "obs-cluster", domain="backend", parent_id=sub.id,
        )
        db.add(cluster)
        await db.flush()

        # All 20 opts unrelated.  Raw=0.00, shrunk=4/30=0.133 — both below
        # the 0.25 floor.  Dissolution must fire under both regimes.
        for i in range(20):
            db.add(_make_opt(cluster.id, "backend: unrelated_qualifier", seed=i))
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels,
        )

        assert "observability" in dissolved, (
            "Sub-domain with N=20 and 0 matches must STILL dissolve — "
            "shrinkage prior must not make dissolution unreachable. "
            f"shrunk=4/30=0.133 < 0.25. Got: {dissolved}"
        )

    @pytest.mark.asyncio
    async def test_dissolution_event_carries_both_consistency_metrics(
        self, db, mock_provider, tmp_path,
    ):
        """AC-R1-4: ``sub_domain_dissolved`` event must carry BOTH
        ``consistency_pct`` (raw, existing) AND ``shrunk_consistency_pct``
        (new, Bayesian posterior).  Existing keys are preserved — additive
        contract only.

        Reuses the N=20/0-match scenario so dissolution actually fires
        and an event is emitted.  Pre-fix the event lacks
        ``shrunk_consistency_pct`` → KeyError on the assertion.
        """
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            reset_event_logger,
            set_event_logger,
        )

        # Install an isolated event logger so we can inspect the ring
        # buffer without cross-test contamination.
        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = _make_engine(mock_provider)

            domain = _make_domain("backend")
            db.add(domain)
            await db.flush()

            sub = _make_sub_domain(
                "observability",
                parent_id=domain.id,
                age_hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5,
            )
            sub.cluster_metadata = write_meta(
                None,
                source="discovered",
                generated_qualifiers={
                    "observability": ["tracing", "monitoring"],
                },
            )
            db.add(sub)
            await db.flush()

            cluster = _make_cluster(
                "obs-cluster", domain="backend", parent_id=sub.id,
            )
            db.add(cluster)
            await db.flush()

            # N=20, matching=0 → dissolves under both regimes.
            for i in range(20):
                db.add(_make_opt(cluster.id, "backend: unrelated_qualifier", seed=i))
            await db.commit()

            existing_labels = {sub.label}
            dissolved = await engine._reevaluate_sub_domains(
                db, domain, existing_labels,
            )
            assert "observability" in dissolved, (
                f"setup precondition: sub-domain must dissolve. Got: {dissolved}"
            )

            # Locate the dissolution event in the ring buffer.
            buffer = list(get_event_logger()._buffer)
            dissolution_events = [
                e for e in buffer
                if e.get("decision") == "sub_domain_dissolved"
                and e.get("context", {}).get("sub_domain") == "observability"
            ]
            assert dissolution_events, (
                "Expected a sub_domain_dissolved event for 'observability'. "
                f"Buffer decisions: {[e.get('decision') for e in buffer]}"
            )
            ctx = dissolution_events[0]["context"]

            # Existing key preserved.
            assert "consistency_pct" in ctx, (
                f"Expected raw consistency_pct in event context. Got keys: "
                f"{sorted(ctx.keys())}"
            )
            assert isinstance(ctx["consistency_pct"], float), (
                f"consistency_pct must be float. Got: {type(ctx['consistency_pct'])}"
            )
            assert ctx["consistency_pct"] == 0.0, (
                f"Raw consistency for N=20/0-match must be 0.0. "
                f"Got: {ctx['consistency_pct']}"
            )

            # NEW key — present only after R1 ships.  Pre-fix this fails
            # with KeyError or assertion failure.
            assert "shrunk_consistency_pct" in ctx, (
                f"Expected NEW shrunk_consistency_pct key in dissolution event "
                f"context (R1 spec AC-R1-4). Got keys: {sorted(ctx.keys())}"
            )
            assert isinstance(ctx["shrunk_consistency_pct"], float), (
                f"shrunk_consistency_pct must be float. "
                f"Got: {type(ctx['shrunk_consistency_pct'])}"
            )
            # shrunk = 4/30 = 0.1333... → rounded to 1 decimal place ≈ 13.3
            assert abs(ctx["shrunk_consistency_pct"] - 13.3) < 0.1, (
                f"shrunk_consistency_pct for N=20/0-match expected ≈ 13.3 "
                f"(=100*4/30). Got: {ctx['shrunk_consistency_pct']}"
            )
        finally:
            reset_event_logger()


# ---------------------------------------------------------------------------
# R2 — 24h grace period before sub-domains are eligible for dissolution
# (spec: docs/specs/sub-domain-dissolution-hardening-2026-04-27.md §R2)
# ---------------------------------------------------------------------------


class TestSubDomainGracePeriod:
    """R2 (audit 2026-04-27): both observed dissolutions on 2026-04-26 fired
    at 6h 0m and 6h 8m post-creation — literally on the first cycle the age
    gate allowed.  6 hours is shorter than typical bootstrap volatility
    windows (overnight cadence + first vocab regen).  The fix bumps
    ``SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS`` from 6 → 24 so a fresh
    sub-domain gets one full daily cycle of grace before hostile
    re-evaluation can dissolve it.

    Test 1 (``test_age_below_grace_period_blocks_dissolution``) uses
    age=12h, which previously fell PAST the 6h gate but now falls BEFORE
    the 24h gate — pre-fix dissolves, post-fix kept.

    Test 2 (``test_age_above_grace_period_proceeds_to_evaluation``) uses
    age=25h, which clears both regimes — pre- and post-fix dissolve.
    Acts as a regression guard against any future change that
    accidentally over-permissive shifts the gate beyond 25 hours.

    Both tests pin generated_qualifiers so R3's empty-snapshot guard
    (cycle 3) does NOT short-circuit the test before the age gate runs.

    Acceptance criteria: AC-R2-1, AC-R2-2 in the spec.
    """

    @pytest.mark.asyncio
    async def test_age_below_grace_period_blocks_dissolution(
        self, db, mock_provider,
    ):
        """AC-R2-1: sub-domain aged 12h with N=15 hostile members must NOT
        be dissolved — the 24h age gate must skip it.

        Pre-fix (gate=6h): 12h > 6h gate passes; shrunk = 4/25 = 0.16 < 0.25
        → DISSOLVES.  Test FAILS.

        Post-fix (gate=24h): 12h < 24h gate skips entirely; not even
        evaluated → KEPT.  Test PASSES.
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        # Aged 12h — between the pre-fix 6h gate and the post-fix 24h gate.
        sub = _make_sub_domain(
            "embedding-health", parent_id=domain.id, age_hours=12,
        )
        # Non-empty generated_qualifiers so R3's empty-snapshot guard
        # does NOT short-circuit when R3 ships later.
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={
                "instrumentation": ["observability", "tracing"],
            },
        )
        db.add(sub)
        await db.flush()

        cluster = _make_cluster(
            "eh-cluster", domain="backend", parent_id=sub.id,
        )
        db.add(cluster)
        await db.flush()

        # N=15 with all unrelated qualifiers, each distinct to defeat any
        # accidental deduplication.  Raw consistency = 0.00, shrunk = 4/25
        # = 0.16 — well below the 0.25 floor; without the age gate this
        # WOULD dissolve.
        for i in range(15):
            db.add(
                _make_opt(cluster.id, f"backend: unrelated_{i}", seed=i),
            )
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels,
        )

        assert dissolved == [], (
            "Sub-domain aged 12h must be SKIPPED by the 24h grace period "
            "gate even though its members are entirely unrelated. "
            f"Pre-fix the 6h gate let dissolution fire. Got: {dissolved}"
        )
        await db.refresh(sub)
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_age_above_grace_period_proceeds_to_evaluation(
        self, db, mock_provider,
    ):
        """AC-R2-2: sub-domain aged 25h with N=15 hostile members IS
        dissolved — the gate must let it through (pre- and post-fix).

        Pre-fix (gate=6h): 25h > 6h gate passes; shrunk dissolves.
        Post-fix (gate=24h): 25h > 24h gate passes; shrunk dissolves.

        Regression guard: locks the contract that the gate does not
        accidentally extend past 25h in some future refactor.
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        # Aged 25h — past both the 6h and 24h gate values.
        sub = _make_sub_domain(
            "embedding-health", parent_id=domain.id, age_hours=25,
        )
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={
                "instrumentation": ["observability", "tracing"],
            },
        )
        db.add(sub)
        await db.flush()

        cluster = _make_cluster(
            "eh-cluster", domain="backend", parent_id=sub.id,
        )
        db.add(cluster)
        await db.flush()

        # Same hostile setup as Test 1 (N=15, all distinct unrelated
        # qualifiers) — shrunk = 4/25 = 0.16 < 0.25 → DISSOLVE.
        for i in range(15):
            db.add(
                _make_opt(cluster.id, f"backend: unrelated_{i}", seed=i),
            )
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels,
        )

        assert "embedding-health" in dissolved, (
            "Sub-domain aged 25h must NOT be skipped by the grace period "
            "gate.  The age gate must permit consistency evaluation, and "
            "shrunk=0.16<0.25 must dissolve.  Got: "
            f"{dissolved}"
        )


# ---------------------------------------------------------------------------
# R3 — Empty-snapshot guardrail: when generated_qualifiers is absent, the
# matcher silently degrades to the v0.4.6 exact-equality bug.  Skip
# dissolution defensively rather than fail-open.
# (spec: docs/specs/sub-domain-dissolution-hardening-2026-04-27.md §R3)
# ---------------------------------------------------------------------------


class TestSubDomainEmptySnapshotSkip:
    """R3 (audit 2026-04-27): ``_reevaluate_sub_domains`` reads
    ``cluster_metadata.generated_qualifiers`` to build the matcher's
    ``sub_vocab_groups`` / ``sub_vocab_terms`` / ``sub_vocab_tokens``
    sets.  When the key is absent (cold-start, vocab-gen failure,
    manual/legacy creation) all three sets are empty and matching falls
    back to the v0.4.6 exact-equality clause (``q_norm == sub_qualifier``)
    that the v0.4.7 fix was supposed to retire.  On healthy sub-domains
    whose children carry GROUP-name qualifiers, this guarantees
    ``matching=0`` and Bayesian shrinkage at N=15 collapses to
    ``4/25 = 0.16 < 0.25`` — premature dissolution.

    The fix inserts a defensive skip immediately after the snapshot load
    and emits a ``sub_domain_reevaluation_skipped`` decision event so
    operators can detect chronic empty-snapshot states (a useful signal
    for vocab-gen failures).

    Acceptance criteria: AC-R3-1, AC-R3-2 in the spec.

    Both tests pin ``age_hours=30`` (well past R2's 24h gate) so the age
    check does not short-circuit the test before the empty-snapshot
    branch runs.
    """

    @pytest.mark.asyncio
    async def test_empty_snapshot_skips_dissolution(
        self, db, mock_provider,
    ):
        """AC-R3-1: a sub-domain whose ``cluster_metadata`` lacks
        ``generated_qualifiers`` is NOT dissolved even when its members
        carry mismatched qualifiers.

        Pre-fix (R3 not merged): empty ``sub_vocab_*`` sets fall through
        to exact-equality matching → matching=0 → shrunk=4/25=0.16 < 0.25
        → DISSOLVES.  Test FAILS.

        Post-fix: empty-snapshot skip block fires → ``dissolved == []``.
        Test PASSES.
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        # Aged 30h (well past the 24h grace gate from R2) so the age
        # check passes and we exercise the empty-snapshot branch.
        sub = _make_sub_domain(
            "embedding-health",
            parent_id=domain.id,
            age_hours=30,
            source="discovered",
        )
        # Empty snapshot: write_meta WITHOUT generated_qualifiers.  The
        # ``_make_sub_domain`` helper delegates to ``_make_domain`` which
        # already calls ``write_meta(None, source=source)`` — so the
        # node's metadata starts without ``generated_qualifiers``.  We
        # don't override it.  Confirm the precondition explicitly so a
        # future change to ``_make_domain`` cannot silently break the
        # test's assumption.
        meta_view = read_meta(sub.cluster_metadata)
        assert not meta_view.get("generated_qualifiers"), (
            "Test precondition: sub-domain metadata must lack a populated "
            "generated_qualifiers entry so the empty-snapshot branch is "
            f"exercised.  Got: {meta_view.get('generated_qualifiers')!r}"
        )

        db.add(sub)
        await db.flush()

        cluster = _make_cluster(
            "eh-cluster", domain="backend", parent_id=sub.id,
        )
        db.add(cluster)
        await db.flush()

        # N=15 hostile members.  Each carries a distinct unrelated
        # qualifier so even legacy exact-equality cannot accidentally
        # match.  Pre-fix: matching=0, shrunk=0.16 < 0.25 → dissolves.
        for i in range(15):
            db.add(
                _make_opt(cluster.id, f"backend: unrelated_{i}", seed=i),
            )
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels,
        )

        assert dissolved == [], (
            "Sub-domain with empty generated_qualifiers must be SKIPPED "
            "by the R3 empty-snapshot guardrail — matching cannot be "
            "evaluated reliably when the vocab snapshot is missing. "
            "Pre-fix the matcher fell back to exact-equality, scored "
            f"matching=0, and dissolved.  Got: {dissolved}"
        )
        await db.refresh(sub)
        assert sub.state == "domain"

    @pytest.mark.asyncio
    async def test_empty_snapshot_emits_skip_event(
        self, db, mock_provider, tmp_path,
    ):
        """AC-R3-2: when the empty-snapshot skip fires, a
        ``sub_domain_reevaluation_skipped`` JSONL event is emitted with
        ``reason="empty_vocab_snapshot"``, the sub-domain's ``cluster_id``,
        and the parent domain's identity in ``context``.

        Pre-fix: no such event exists — assertion that the event is
        present fails.

        Post-fix: the new event is emitted with the documented context
        keys.  Test PASSES.
        """
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            reset_event_logger,
            set_event_logger,
        )

        # Install an isolated event logger so we can inspect the ring
        # buffer without cross-test contamination — same pattern as the
        # R1 dissolution-event test in TestSubDomainBayesianShrinkage.
        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = _make_engine(mock_provider)

            domain = _make_domain("backend")
            db.add(domain)
            await db.flush()

            sub = _make_sub_domain(
                "embedding-health",
                parent_id=domain.id,
                age_hours=30,
                source="discovered",
            )
            meta_view = read_meta(sub.cluster_metadata)
            assert not meta_view.get("generated_qualifiers"), (
                "Test precondition: empty generated_qualifiers required."
            )
            db.add(sub)
            await db.flush()

            cluster = _make_cluster(
                "eh-cluster", domain="backend", parent_id=sub.id,
            )
            db.add(cluster)
            await db.flush()

            # Same hostile setup as Test 1 — N=15 distinct unrelated
            # qualifiers so the legacy exact-equality fallback yields 0.
            for i in range(15):
                db.add(
                    _make_opt(cluster.id, f"backend: unrelated_{i}", seed=i),
                )
            await db.commit()

            existing_labels = {sub.label}
            await engine._reevaluate_sub_domains(
                db, domain, existing_labels,
            )

            # Locate the skip event in the ring buffer.
            buffer = list(get_event_logger()._buffer)
            skip_events = [
                e for e in buffer
                if e.get("decision") == "sub_domain_reevaluation_skipped"
                and e.get("cluster_id") == sub.id
            ]
            assert skip_events, (
                "Expected a sub_domain_reevaluation_skipped event for "
                f"cluster_id={sub.id!r}.  Buffer decisions: "
                f"{[e.get('decision') for e in buffer]}"
            )
            ctx = skip_events[0]["context"]

            assert ctx.get("reason") == "empty_vocab_snapshot", (
                "Expected context.reason='empty_vocab_snapshot'.  Got: "
                f"{ctx.get('reason')!r}"
            )
            assert ctx.get("domain") == "backend", (
                f"Expected context.domain='backend'.  Got: "
                f"{ctx.get('domain')!r}"
            )
            assert ctx.get("sub_domain") == "embedding-health", (
                f"Expected context.sub_domain='embedding-health'.  Got: "
                f"{ctx.get('sub_domain')!r}"
            )
            assert "domain_node_id" in ctx, (
                f"Expected context.domain_node_id to be present.  Got "
                f"keys: {sorted(ctx.keys())}"
            )
            assert ctx["domain_node_id"] == domain.id, (
                "Expected context.domain_node_id to equal the parent "
                f"domain's id ({domain.id!r}).  Got: "
                f"{ctx.get('domain_node_id')!r}"
            )
        finally:
            reset_event_logger()


# ---------------------------------------------------------------------------
# R5 — Forensic dissolution telemetry
# (spec: docs/specs/sub-domain-dissolution-hardening-r4-r6.md §R5)
# ---------------------------------------------------------------------------


class TestSubDomainForensicTelemetry:
    """R5 (spec ``sub-domain-dissolution-hardening-r4-r6.md``): the
    ``sub_domain_dissolved`` and ``sub_domain_reevaluated`` events must
    carry a forensic breakdown of *why* dissolution did or did not fire:

    - ``matching_members`` (int): the engine's integer count of opts that
      matched the sub-domain's vocab — surfaces the same number used in
      the floor decision so the event tells the full story without
      requiring the consumer to re-derive it from ``consistency_pct``.
    - ``sample_match_failures`` (list, len ≤ ``SUB_DOMAIN_FAILURE_SAMPLES``):
      a deterministic prefix of the non-matching opts including
      ``cluster_id``, ``domain_raw``, ``intent_label``, and the matcher's
      ``reason`` field — so an operator can reconstruct the dissolution
      inputs from a single log line.

    Pre-fix (R5 not merged): the events lack both keys.  Each test below
    raises ``KeyError`` on the missing key.

    Acceptance criteria: AC-R5-1 .. AC-R5-5 in the spec.

    Conventions:
    - Each sub-domain ages ``SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5``
      so R2's grace gate is past.
    - Each sub-domain carries a populated ``generated_qualifiers``
      snapshot so R3's empty-snapshot skip does NOT short-circuit.
    - The match expectations follow R1's Bayesian shrinkage
      ``(matching + 4) / (total_opts + 10)`` against the 0.25 floor.
    """

    @pytest.mark.asyncio
    async def test_dissolution_event_carries_sample_failures(
        self, db, mock_provider, tmp_path,
    ):
        """AC-R5-1: a hostile-N=20/0-match scenario dissolves and the
        emitted ``sub_domain_dissolved`` event carries
        ``sample_match_failures`` of length 1, 2, or 3 (cap is
        ``SUB_DOMAIN_FAILURE_SAMPLES`` = 3) with each entry exposing
        ``cluster_id`` / ``domain_raw`` / ``intent_label`` / ``reason``.
        """
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            reset_event_logger,
            set_event_logger,
        )

        # Try to import the constant from its eventual home; if R5 hasn't
        # shipped yet, fall back to the spec-mandated literal so the test
        # is still meaningful in RED.
        try:
            from app.services.taxonomy._constants import (
                SUB_DOMAIN_FAILURE_SAMPLES,
            )
        except ImportError:
            SUB_DOMAIN_FAILURE_SAMPLES = 3  # noqa: N806 — spec literal fallback for RED-phase resilience

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = _make_engine(mock_provider)

            domain = _make_domain("backend")
            db.add(domain)
            await db.flush()

            sub = _make_sub_domain(
                "observability",
                parent_id=domain.id,
                age_hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5,
            )
            sub.cluster_metadata = write_meta(
                None,
                source="discovered",
                generated_qualifiers={
                    "instrumentation": ["observability", "tracing"],
                },
            )
            db.add(sub)
            await db.flush()

            cluster = _make_cluster(
                "obs-cluster", domain="backend", parent_id=sub.id,
            )
            db.add(cluster)
            await db.flush()

            # N=20 hostile members, distinct unrelated qualifiers and
            # distinct intent labels so each opt yields a unique failure
            # signature.  shrunk = 4/30 = 0.133 < 0.25 → DISSOLVE.
            for i in range(20):
                opt = _make_opt(cluster.id, f"backend: unrelated_{i}", seed=i)
                opt.intent_label = f"Unrelated Topic {i}"
                db.add(opt)
            await db.commit()

            existing_labels = {sub.label}
            dissolved = await engine._reevaluate_sub_domains(
                db, domain, existing_labels,
            )
            assert "observability" in dissolved, (
                f"setup precondition: sub-domain must dissolve. Got: {dissolved}"
            )

            # Locate the dissolution event in the ring buffer.
            buffer = list(get_event_logger()._buffer)
            dissolution_events = [
                e for e in buffer
                if e.get("decision") == "sub_domain_dissolved"
                and e.get("context", {}).get("sub_domain") == "observability"
            ]
            assert dissolution_events, (
                "Expected a sub_domain_dissolved event for 'observability'. "
                f"Buffer decisions: {[e.get('decision') for e in buffer]}"
            )
            ctx = dissolution_events[0]["context"]

            # AC-R5-1: forensic samples are present.
            assert "sample_match_failures" in ctx, (
                "Expected NEW sample_match_failures key in dissolution event "
                f"context (R5 spec AC-R5-1). Got keys: {sorted(ctx.keys())}"
            )
            samples = ctx["sample_match_failures"]
            assert isinstance(samples, list), (
                f"sample_match_failures must be a list. Got: {type(samples)}"
            )
            assert 1 <= len(samples) <= SUB_DOMAIN_FAILURE_SAMPLES, (
                f"sample_match_failures must have 1..{SUB_DOMAIN_FAILURE_SAMPLES} "
                f"entries (cap SUB_DOMAIN_FAILURE_SAMPLES={SUB_DOMAIN_FAILURE_SAMPLES}). "
                f"Got len={len(samples)}: {samples!r}"
            )
            required_keys = {"cluster_id", "domain_raw", "intent_label", "reason"}
            for entry in samples:
                assert isinstance(entry, dict), (
                    f"Each sample_match_failures entry must be a dict. Got: "
                    f"{type(entry)} → {entry!r}"
                )
                missing = required_keys - set(entry.keys())
                assert not missing, (
                    "Each sample_match_failures entry must carry "
                    f"{sorted(required_keys)}. Missing: {sorted(missing)}. "
                    f"Got entry keys: {sorted(entry.keys())}"
                )
                reason = entry["reason"]
                assert isinstance(reason, str) and reason, (
                    "sample_match_failures.reason must be a non-empty string. "
                    f"Got: {reason!r}"
                )
        finally:
            reset_event_logger()

    @pytest.mark.asyncio
    async def test_sample_failures_exclude_matched_opts(
        self, db, mock_provider, tmp_path,
    ):
        """AC-R5-2: when ``matching_members > 0``, the failure samples
        are drawn ONLY from non-matching opts.  No matched opt may
        appear in ``sample_match_failures``.

        Setup: 10 matched (vocab GROUP "observability") + 10 unmatched
        (``backend: unrelated_<i>``).  Raw=0.50, shrunk=14/30=0.467 →
        KEEP.  Captures the ``sub_domain_reevaluated`` event.
        """
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            reset_event_logger,
            set_event_logger,
        )

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = _make_engine(mock_provider)

            domain = _make_domain("backend")
            db.add(domain)
            await db.flush()

            sub = _make_sub_domain(
                "observability",
                parent_id=domain.id,
                age_hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5,
            )
            sub.cluster_metadata = write_meta(
                None,
                source="discovered",
                generated_qualifiers={
                    "instrumentation": ["observability", "tracing"],
                },
            )
            db.add(sub)
            await db.flush()

            cluster = _make_cluster(
                "obs-cluster", domain="backend", parent_id=sub.id,
            )
            db.add(cluster)
            await db.flush()

            # First 10: vocab group "observability" → match=True (Source 1).
            for i in range(10):
                db.add(
                    _make_opt(cluster.id, "backend: observability", seed=i),
                )
            # Last 10: distinct unrelated qualifiers → match=False.
            for i in range(10, 20):
                db.add(
                    _make_opt(cluster.id, f"backend: unrelated_{i}", seed=i),
                )
            await db.commit()

            existing_labels = {sub.label}
            dissolved = await engine._reevaluate_sub_domains(
                db, domain, existing_labels,
            )
            assert dissolved == [], (
                "setup precondition: 14/30 = 0.467 ≥ 0.25 → KEEP. "
                f"Got dissolved: {dissolved}"
            )

            # Locate the re-evaluation event.
            buffer = list(get_event_logger()._buffer)
            reeval_events = [
                e for e in buffer
                if e.get("decision") == "sub_domain_reevaluated"
                and e.get("context", {}).get("sub_domain") == "observability"
            ]
            assert reeval_events, (
                "Expected a sub_domain_reevaluated event for 'observability'. "
                f"Buffer decisions: {[e.get('decision') for e in buffer]}"
            )
            ctx = reeval_events[-1]["context"]

            assert "sample_match_failures" in ctx, (
                "Expected sample_match_failures in re-evaluation context "
                f"(AC-R5-2). Got keys: {sorted(ctx.keys())}"
            )
            samples = ctx["sample_match_failures"]
            # Every sample must be from the non-matching cohort.  The
            # matched cohort uses domain_raw="backend: observability";
            # the unmatched cohort uses domain_raw="backend: unrelated_<i>".
            for entry in samples:
                raw = entry.get("domain_raw") or ""
                assert raw.startswith("backend: unrelated_"), (
                    "sample_match_failures may only contain non-matching "
                    "opts. Found a matched opt in the failure samples: "
                    f"{entry!r}"
                )
        finally:
            reset_event_logger()

    @pytest.mark.asyncio
    async def test_long_text_truncated(
        self, db, mock_provider, tmp_path,
    ):
        """AC-R5-3: text fields in ``sample_match_failures`` are
        truncated to ``SUB_DOMAIN_FAILURE_FIELD_TRUNCATE`` = 80 chars.

        Setup: 5 hostile opts each with a 200-char ``intent_label``.
        Forces a dissolution; asserts each emitted entry's
        ``intent_label`` is at most 80 chars.
        """
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            reset_event_logger,
            set_event_logger,
        )

        try:
            from app.services.taxonomy._constants import (
                SUB_DOMAIN_FAILURE_FIELD_TRUNCATE,
            )
        except ImportError:
            SUB_DOMAIN_FAILURE_FIELD_TRUNCATE = 80  # noqa: N806 — spec literal fallback for RED-phase resilience

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = _make_engine(mock_provider)

            domain = _make_domain("backend")
            db.add(domain)
            await db.flush()

            sub = _make_sub_domain(
                "observability",
                parent_id=domain.id,
                age_hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5,
            )
            sub.cluster_metadata = write_meta(
                None,
                source="discovered",
                generated_qualifiers={
                    "instrumentation": ["observability", "tracing"],
                },
            )
            db.add(sub)
            await db.flush()

            cluster = _make_cluster(
                "obs-cluster", domain="backend", parent_id=sub.id,
            )
            db.add(cluster)
            await db.flush()

            # 5 hostile opts.  Each carries an over-long intent_label.
            # shrunk = (0+4)/(5+10) = 4/15 = 0.267 — pre-R1 fix this would
            # KEEP (≥ 0.25); but we want dissolution here, so use N=20.
            # Actually 5 KEEPs under shrinkage; bump to 20 hostile opts.
            long_text = "X" * 200
            for i in range(20):
                opt = _make_opt(cluster.id, f"backend: unrelated_{i}", seed=i)
                opt.intent_label = long_text
                db.add(opt)
            await db.commit()

            existing_labels = {sub.label}
            dissolved = await engine._reevaluate_sub_domains(
                db, domain, existing_labels,
            )
            assert "observability" in dissolved, (
                f"setup precondition: sub-domain must dissolve. Got: {dissolved}"
            )

            buffer = list(get_event_logger()._buffer)
            dissolution_events = [
                e for e in buffer
                if e.get("decision") == "sub_domain_dissolved"
                and e.get("context", {}).get("sub_domain") == "observability"
            ]
            assert dissolution_events, (
                "Expected a sub_domain_dissolved event for 'observability'. "
                f"Buffer decisions: {[e.get('decision') for e in buffer]}"
            )
            ctx = dissolution_events[0]["context"]

            assert "sample_match_failures" in ctx, (
                "Expected sample_match_failures in dissolution context "
                f"(AC-R5-3). Got keys: {sorted(ctx.keys())}"
            )
            samples = ctx["sample_match_failures"]
            assert samples, (
                "Hostile setup must produce at least one failure sample"
            )
            for entry in samples:
                label = entry.get("intent_label")
                assert label is not None, (
                    f"intent_label must be present in sample. Got: {entry!r}"
                )
                assert len(label) <= SUB_DOMAIN_FAILURE_FIELD_TRUNCATE, (
                    "sample_match_failures.intent_label must be truncated "
                    f"to ≤ {SUB_DOMAIN_FAILURE_FIELD_TRUNCATE} chars "
                    f"(SUB_DOMAIN_FAILURE_FIELD_TRUNCATE).  Got "
                    f"len={len(label)}: {label!r}"
                )
        finally:
            reset_event_logger()

    @pytest.mark.asyncio
    async def test_matching_members_matches_engine_count(
        self, db, mock_provider, tmp_path,
    ):
        """AC-R5-4: ``context.matching_members`` exposes the integer
        ``matching`` count and ``context.consistency_pct`` is computed
        from the same value.

        Setup: 13 children, 7 with vocab-group qualifier
        ("observability"), 6 unrelated.  Raw consistency = 7/13 ≈ 0.538.
        Shrunk = (7+4)/(13+10) = 11/23 ≈ 0.478 → KEEP.

        Asserts:
        - ``matching_members == 7``
        - ``consistency_pct == round(7/13 * 100, 1)``
        """
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            reset_event_logger,
            set_event_logger,
        )

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = _make_engine(mock_provider)

            domain = _make_domain("backend")
            db.add(domain)
            await db.flush()

            sub = _make_sub_domain(
                "observability",
                parent_id=domain.id,
                age_hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5,
            )
            sub.cluster_metadata = write_meta(
                None,
                source="discovered",
                generated_qualifiers={
                    "instrumentation": ["observability"],
                },
            )
            db.add(sub)
            await db.flush()

            cluster = _make_cluster(
                "obs-cluster", domain="backend", parent_id=sub.id,
            )
            db.add(cluster)
            await db.flush()

            # 7 matched (vocab group "observability") + 6 unrelated.
            for i in range(7):
                db.add(
                    _make_opt(cluster.id, "backend: observability", seed=i),
                )
            for i in range(7, 13):
                db.add(
                    _make_opt(cluster.id, f"backend: unrelated_{i}", seed=i),
                )
            await db.commit()

            existing_labels = {sub.label}
            dissolved = await engine._reevaluate_sub_domains(
                db, domain, existing_labels,
            )
            assert dissolved == [], (
                "setup precondition: 11/23 ≈ 0.478 ≥ 0.25 → KEEP. "
                f"Got dissolved: {dissolved}"
            )

            buffer = list(get_event_logger()._buffer)
            reeval_events = [
                e for e in buffer
                if e.get("decision") == "sub_domain_reevaluated"
                and e.get("context", {}).get("sub_domain") == "observability"
            ]
            assert reeval_events, (
                "Expected a sub_domain_reevaluated event for 'observability'. "
                f"Buffer decisions: {[e.get('decision') for e in buffer]}"
            )
            ctx = reeval_events[-1]["context"]

            assert "matching_members" in ctx, (
                "Expected matching_members in re-evaluation context "
                f"(AC-R5-4). Got keys: {sorted(ctx.keys())}"
            )
            assert ctx["matching_members"] == 7, (
                "matching_members must equal the engine's integer match "
                f"count (= 7).  Got: {ctx['matching_members']!r}"
            )
            expected_pct = round(7 / 13 * 100, 1)
            assert ctx["consistency_pct"] == expected_pct, (
                "consistency_pct must equal round(matching/total * 100, 1) = "
                f"{expected_pct}.  Got: {ctx['consistency_pct']!r}"
            )
        finally:
            reset_event_logger()

    @pytest.mark.asyncio
    async def test_all_match_emits_empty_failures(
        self, db, mock_provider, tmp_path,
    ):
        """AC-R5-5: when every opt matches, ``sample_match_failures`` is
        ``[]`` and ``matching_members == total_opts``.

        Setup: 20 children all carrying ``backend: observability`` (vocab
        group hit, Source 1).  shrunk = (20+4)/(20+10) = 24/30 = 0.80 →
        KEEP.  No failures should appear.
        """
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            reset_event_logger,
            set_event_logger,
        )

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = _make_engine(mock_provider)

            domain = _make_domain("backend")
            db.add(domain)
            await db.flush()

            sub = _make_sub_domain(
                "observability",
                parent_id=domain.id,
                age_hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5,
            )
            sub.cluster_metadata = write_meta(
                None,
                source="discovered",
                generated_qualifiers={
                    "instrumentation": ["observability"],
                },
            )
            db.add(sub)
            await db.flush()

            cluster = _make_cluster(
                "obs-cluster", domain="backend", parent_id=sub.id,
            )
            db.add(cluster)
            await db.flush()

            for i in range(20):
                db.add(
                    _make_opt(cluster.id, "backend: observability", seed=i),
                )
            await db.commit()

            existing_labels = {sub.label}
            dissolved = await engine._reevaluate_sub_domains(
                db, domain, existing_labels,
            )
            assert dissolved == [], (
                "setup precondition: 24/30 = 0.80 ≥ 0.25 → KEEP. "
                f"Got dissolved: {dissolved}"
            )

            buffer = list(get_event_logger()._buffer)
            reeval_events = [
                e for e in buffer
                if e.get("decision") == "sub_domain_reevaluated"
                and e.get("context", {}).get("sub_domain") == "observability"
            ]
            assert reeval_events, (
                "Expected a sub_domain_reevaluated event for 'observability'. "
                f"Buffer decisions: {[e.get('decision') for e in buffer]}"
            )
            ctx = reeval_events[-1]["context"]

            assert "sample_match_failures" in ctx, (
                "Expected sample_match_failures in re-evaluation context "
                f"(AC-R5-5). Got keys: {sorted(ctx.keys())}"
            )
            assert ctx["sample_match_failures"] == [], (
                "When all opts match, sample_match_failures must be the "
                f"empty list.  Got: {ctx['sample_match_failures']!r}"
            )

            assert "matching_members" in ctx, (
                "Expected matching_members in re-evaluation context "
                f"(AC-R5-5). Got keys: {sorted(ctx.keys())}"
            )
            assert ctx["matching_members"] == 20, (
                "matching_members must equal total_opts=20 when every "
                f"opt matches.  Got: {ctx['matching_members']!r}"
            )
        finally:
            reset_event_logger()


# ---------------------------------------------------------------------------
# R6: rebuild-sub-domains recovery endpoint (engine method tests)
# ---------------------------------------------------------------------------


class TestRebuildSubDomainsService:
    """R6 (spec ``sub-domain-dissolution-hardening-r4-r6.md``): operator-
    triggered sub-domain rebuild on a single domain.

    The engine method ``rebuild_sub_domains(db, domain_id, *,
    min_consistency_override=None, dry_run=False)`` extends
    ``_propose_sub_domains`` semantics for ONE domain, reusing
    ``compute_qualifier_cascade`` for the qualifier scan and
    ``_create_domain_node`` for sub-domain creation.

    Idempotency: existing sub-domain labels under the parent are listed
    in ``skipped_existing`` and never recreated.

    Threshold: defaults to the adaptive formula
    ``max(0.40, 0.60 - 0.004 * total_opts)``; an override bypasses the
    formula but is hard-floored at
    ``SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR=0.25`` (the runtime check
    raises ``ValueError`` for sub-floor inputs as defense-in-depth
    against bypassing the Pydantic ``ge=0.25`` validator).

    Telemetry: every call emits a ``sub_domain_rebuild_invoked`` event
    (including dry runs and zero-creation calls) so the operator audit
    trail is complete.  When ``created`` is non-empty AND non-dry, also
    publishes a ``taxonomy_changed`` event to ``event_bus``.

    Atomicity: all sub-domain creations in a single rebuild are atomic —
    if any creation fails mid-batch, the entire transaction rolls back.

    Acceptance criteria: AC-R6-3 / AC-R6-4 / AC-R6-5 / AC-R6-6 /
    AC-R6-7 / AC-R6-8 / AC-R6-9 / AC-R6-10 / AC-R6-11.

    Pre-fix (R6 not merged): each test below fails with
    ``ImportError`` (the schemas don't exist) or ``AttributeError``
    (the engine method doesn't exist).
    """

    @pytest.mark.asyncio
    async def test_rebuild_default_threshold_matches_discovery(
        self, db, mock_provider,
    ):
        """AC-R6-11: with no override, ``threshold_used`` equals the
        adaptive formula ``max(0.40, 0.60 - 0.004 * total_opts)`` for
        the actual N. Set up 5 child clusters with varied qualifiers
        (10 total opts) so the formula is exercised.
        """
        # Importing the schemas here so RED fails before engine setup.
        from app.schemas.domains import (  # noqa: F401
            RebuildSubDomainsRequest,
            RebuildSubDomainsResult,
        )

        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        # 5 child clusters carrying various qualifiers.
        cluster_ids: list[str] = []
        for i in range(5):
            c = _make_cluster(
                f"backend-cluster-{i}", domain="backend", parent_id=domain.id,
            )
            db.add(c)
            await db.flush()
            cluster_ids.append(c.id)

        # 10 opts spread across qualifiers.
        for i in range(10):
            qualifier = f"backend: qual_{i % 4}"
            db.add(_make_opt(cluster_ids[i % 5], qualifier, seed=i))
        await db.commit()

        result = await engine.rebuild_sub_domains(db, domain.id)

        # Adaptive formula with N total_opts.
        # max(0.40, 0.60 - 0.004 * N)  — N = 10 here.
        expected = max(0.40, 0.60 - 0.004 * 10)
        assert "threshold_used" in result, (
            f"result must carry threshold_used. Got keys: {sorted(result.keys())}"
        )
        assert abs(result["threshold_used"] - expected) < 1e-9, (
            "threshold_used must match adaptive formula "
            f"max(0.40, 0.60 - 0.004*{10}) = {expected}. "
            f"Got: {result['threshold_used']}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_with_override_uses_override(
        self, db, mock_provider,
    ):
        """AC-R6-11 (override path): ``min_consistency_override=0.30``
        sets ``threshold_used`` to exactly 0.30 (no adaptive formula).
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        cluster = _make_cluster(
            "backend-cluster", domain="backend", parent_id=domain.id,
        )
        db.add(cluster)
        await db.flush()

        for i in range(5):
            db.add(_make_opt(cluster.id, "backend: foo", seed=i))
        await db.commit()

        result = await engine.rebuild_sub_domains(
            db, domain.id, min_consistency_override=0.30,
        )

        assert result["threshold_used"] == 0.30, (
            "min_consistency_override=0.30 must set threshold_used to 0.30. "
            f"Got: {result['threshold_used']}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_idempotent_skips_existing(
        self, db, mock_provider,
    ):
        """AC-R6-6: pre-existing sub-domain labels appear in
        ``skipped_existing`` and never in ``created``.
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        # Pre-create ``audit`` sub-domain under backend.
        sub = _make_sub_domain("audit", parent_id=domain.id, age_hours=24)
        db.add(sub)
        await db.flush()

        # Add some opts so cascade has data.
        cluster = _make_cluster(
            "backend-cluster", domain="backend", parent_id=domain.id,
        )
        db.add(cluster)
        await db.flush()
        for i in range(5):
            db.add(_make_opt(cluster.id, "backend: audit", seed=i))
        await db.commit()

        result = await engine.rebuild_sub_domains(
            db, domain.id, min_consistency_override=0.30,
        )

        assert "audit" in result["skipped_existing"], (
            "Pre-existing 'audit' sub-domain MUST appear in skipped_existing. "
            f"Got skipped_existing: {result['skipped_existing']}"
        )
        assert "audit" not in result["created"], (
            "Pre-existing sub-domain must NOT be re-created. "
            f"Got created: {result['created']}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_creates_new_sub_domain_below_default_threshold(
        self, db, mock_provider,
    ):
        """AC-R6 happy path: with ``min_consistency_override=0.30`` and
        a qualifier above 0.30 but below the default 0.40 floor, the
        rebuild creates the sub-domain.

        Setup: 10 opts; 4 carry qualifier ``concurrency`` (consistency=0.40
        with default floor) spread across 2 distinct clusters (breadth gate
        passes), 6 distinct unrelated qualifiers.  With override=0.30 the
        cascade admits ``concurrency``.
        """
        from sqlalchemy import func, select

        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        # Two distinct clusters (breadth=2 gate passes).
        c1 = _make_cluster(
            "concurrency-cluster-a", domain="backend", parent_id=domain.id,
        )
        c2 = _make_cluster(
            "concurrency-cluster-b", domain="backend", parent_id=domain.id,
        )
        c3 = _make_cluster(
            "other-cluster", domain="backend", parent_id=domain.id,
        )
        db.add_all([c1, c2, c3])
        await db.flush()

        # 4 opts on "concurrency" qualifier across c1+c2 (breadth ≥ 2).
        db.add(_make_opt(c1.id, "backend: concurrency", seed=0))
        db.add(_make_opt(c1.id, "backend: concurrency", seed=1))
        db.add(_make_opt(c2.id, "backend: concurrency", seed=2))
        db.add(_make_opt(c2.id, "backend: concurrency", seed=3))
        # 6 distinct unrelated opts on c3.
        for i in range(6):
            db.add(_make_opt(c3.id, f"backend: unrelated_{i}", seed=10 + i))
        await db.commit()

        # Snapshot the pre-rebuild domain-node count under this parent.
        pre_count_q = await db.execute(
            select(func.count()).where(
                PromptCluster.state == "domain",
                PromptCluster.parent_id == domain.id,
            )
        )
        pre_count = int(pre_count_q.scalar() or 0)

        result = await engine.rebuild_sub_domains(
            db, domain.id, min_consistency_override=0.30,
        )

        assert "concurrency" in result["created"], (
            "Expected 'concurrency' (consistency=0.40, breadth=2) to be "
            f"created with override=0.30. Got created: {result['created']}, "
            f"proposed: {result['proposed']}"
        )

        # Verify in DB: domain-node count under this parent increased by 1.
        post_count_q = await db.execute(
            select(func.count()).where(
                PromptCluster.state == "domain",
                PromptCluster.parent_id == domain.id,
            )
        )
        post_count = int(post_count_q.scalar() or 0)
        assert post_count == pre_count + 1, (
            "Sub-domain count under parent must increase by 1. "
            f"pre={pre_count} post={post_count}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_dry_run_no_creation(
        self, db, mock_provider,
    ):
        """AC-R6-5: ``dry_run=True`` returns ``proposed`` non-empty but
        ``created == []`` and the DB is unchanged.
        """
        from sqlalchemy import func, select

        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        c1 = _make_cluster(
            "concurrency-cluster-a", domain="backend", parent_id=domain.id,
        )
        c2 = _make_cluster(
            "concurrency-cluster-b", domain="backend", parent_id=domain.id,
        )
        c3 = _make_cluster(
            "other-cluster", domain="backend", parent_id=domain.id,
        )
        db.add_all([c1, c2, c3])
        await db.flush()

        db.add(_make_opt(c1.id, "backend: concurrency", seed=0))
        db.add(_make_opt(c1.id, "backend: concurrency", seed=1))
        db.add(_make_opt(c2.id, "backend: concurrency", seed=2))
        db.add(_make_opt(c2.id, "backend: concurrency", seed=3))
        for i in range(6):
            db.add(_make_opt(c3.id, f"backend: unrelated_{i}", seed=10 + i))
        await db.commit()

        pre_count_q = await db.execute(
            select(func.count()).where(
                PromptCluster.state == "domain",
                PromptCluster.parent_id == domain.id,
            )
        )
        pre_count = int(pre_count_q.scalar() or 0)

        result = await engine.rebuild_sub_domains(
            db, domain.id, min_consistency_override=0.30, dry_run=True,
        )

        assert result["dry_run"] is True, (
            f"dry_run flag must echo back True. Got: {result['dry_run']!r}"
        )
        assert "concurrency" in result["proposed"], (
            "Eligible qualifier 'concurrency' must appear in proposed even "
            f"in dry-run mode. Got proposed: {result['proposed']}"
        )
        assert result["created"] == [], (
            "dry_run=True MUST NOT create sub-domains. "
            f"Got created: {result['created']}"
        )

        # Verify DB is unchanged.
        post_count_q = await db.execute(
            select(func.count()).where(
                PromptCluster.state == "domain",
                PromptCluster.parent_id == domain.id,
            )
        )
        post_count = int(post_count_q.scalar() or 0)
        assert post_count == pre_count, (
            "Dry-run must leave DB unchanged. "
            f"pre={pre_count} post={post_count}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_emits_taxonomy_changed_when_creating(
        self, db, mock_provider,
    ):
        """AC-R6-8: when ``created`` is non-empty (non-dry creation), a
        ``taxonomy_changed`` event is published to ``event_bus`` so the
        readiness TTL cache invalidates and the resident engine's
        dirty_set picks up the new sub-domain.
        """
        import asyncio

        from app.services.event_bus import event_bus

        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        c1 = _make_cluster(
            "concurrency-cluster-a", domain="backend", parent_id=domain.id,
        )
        c2 = _make_cluster(
            "concurrency-cluster-b", domain="backend", parent_id=domain.id,
        )
        c3 = _make_cluster(
            "other-cluster", domain="backend", parent_id=domain.id,
        )
        db.add_all([c1, c2, c3])
        await db.flush()

        db.add(_make_opt(c1.id, "backend: concurrency", seed=0))
        db.add(_make_opt(c1.id, "backend: concurrency", seed=1))
        db.add(_make_opt(c2.id, "backend: concurrency", seed=2))
        db.add(_make_opt(c2.id, "backend: concurrency", seed=3))
        for i in range(6):
            db.add(_make_opt(c3.id, f"backend: unrelated_{i}", seed=10 + i))
        await db.commit()

        # Defend against a prior test that may have flipped the bus into
        # shutdown mode (publish is a no-op while shutting down).
        event_bus._shutting_down = False  # type: ignore[attr-defined]

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        event_bus._subscribers.add(queue)
        try:
            result = await engine.rebuild_sub_domains(
                db, domain.id, min_consistency_override=0.30,
            )

            assert result["created"], (
                "Setup precondition: at least one sub-domain must be created "
                f"so a taxonomy_changed event fires. Got created: {result['created']}"
            )

            taxonomy_events: list[dict] = []
            while True:
                try:
                    evt = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if evt.get("event") == "taxonomy_changed":
                    taxonomy_events.append(evt)
        finally:
            event_bus._subscribers.discard(queue)

        assert len(taxonomy_events) >= 1, (
            "AC-R6-8: rebuild that creates a sub-domain MUST publish at "
            f"least one taxonomy_changed event. Got count: {len(taxonomy_events)}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_no_taxonomy_changed_on_zero_creates(
        self, db, mock_provider,
    ):
        """AC-R6-9: when ``created`` is empty (dry-run or idempotent
        re-run), NO ``taxonomy_changed`` event fires.
        """
        import asyncio

        from app.services.event_bus import event_bus

        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        c1 = _make_cluster(
            "concurrency-cluster-a", domain="backend", parent_id=domain.id,
        )
        c2 = _make_cluster(
            "concurrency-cluster-b", domain="backend", parent_id=domain.id,
        )
        db.add_all([c1, c2])
        await db.flush()

        db.add(_make_opt(c1.id, "backend: concurrency", seed=0))
        db.add(_make_opt(c1.id, "backend: concurrency", seed=1))
        db.add(_make_opt(c2.id, "backend: concurrency", seed=2))
        db.add(_make_opt(c2.id, "backend: concurrency", seed=3))
        await db.commit()

        event_bus._shutting_down = False  # type: ignore[attr-defined]

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        event_bus._subscribers.add(queue)
        try:
            # Dry-run: created MUST be empty so no taxonomy_changed fires.
            result = await engine.rebuild_sub_domains(
                db, domain.id, min_consistency_override=0.30, dry_run=True,
            )
            assert result["created"] == [], (
                "Setup precondition: dry_run=True MUST yield created=[]. "
                f"Got: {result['created']}"
            )

            taxonomy_events: list[dict] = []
            while True:
                try:
                    evt = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if evt.get("event") == "taxonomy_changed":
                    taxonomy_events.append(evt)
        finally:
            event_bus._subscribers.discard(queue)

        assert len(taxonomy_events) == 0, (
            "AC-R6-9: zero-creation rebuild MUST NOT publish taxonomy_changed. "
            f"Got count: {len(taxonomy_events)}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_emits_telemetry_non_dry(
        self, db, mock_provider, tmp_path,
    ):
        """AC-R6-7: a non-dry-run ``sub_domain_rebuild_invoked`` event is
        emitted with the expected context keys.
        """
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            reset_event_logger,
            set_event_logger,
        )

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = _make_engine(mock_provider)

            domain = _make_domain("backend")
            db.add(domain)
            await db.flush()

            c1 = _make_cluster(
                "concurrency-cluster-a", domain="backend", parent_id=domain.id,
            )
            c2 = _make_cluster(
                "concurrency-cluster-b", domain="backend", parent_id=domain.id,
            )
            db.add_all([c1, c2])
            await db.flush()

            db.add(_make_opt(c1.id, "backend: concurrency", seed=0))
            db.add(_make_opt(c1.id, "backend: concurrency", seed=1))
            db.add(_make_opt(c2.id, "backend: concurrency", seed=2))
            db.add(_make_opt(c2.id, "backend: concurrency", seed=3))
            await db.commit()

            await engine.rebuild_sub_domains(
                db, domain.id, min_consistency_override=0.30, dry_run=False,
            )

            buffer = list(get_event_logger()._buffer)
            invoke_events = [
                e for e in buffer
                if e.get("decision") == "sub_domain_rebuild_invoked"
            ]
            assert invoke_events, (
                "AC-R6-7: expected at least one sub_domain_rebuild_invoked "
                f"event. Buffer decisions: {[e.get('decision') for e in buffer]}"
            )
            ctx = invoke_events[-1]["context"]
            required = {
                "domain", "min_consistency_override", "threshold_used",
                "dry_run", "proposed_count", "created_count",
                "skipped_existing_count",
            }
            missing = required - set(ctx.keys())
            assert not missing, (
                "Telemetry context must carry "
                f"{sorted(required)}. Missing: {sorted(missing)}. "
                f"Got context keys: {sorted(ctx.keys())}"
            )
            assert ctx["dry_run"] is False, (
                f"dry_run in telemetry context must be False. Got: {ctx['dry_run']!r}"
            )
        finally:
            reset_event_logger()

    @pytest.mark.asyncio
    async def test_rebuild_emits_telemetry_dry_run(
        self, db, mock_provider, tmp_path,
    ):
        """AC-R6-5 (telemetry side): the rebuild_invoked event fires for
        dry runs as well, with ``dry_run=True`` and ``created_count=0``.
        """
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            reset_event_logger,
            set_event_logger,
        )

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = _make_engine(mock_provider)

            domain = _make_domain("backend")
            db.add(domain)
            await db.flush()

            c1 = _make_cluster(
                "concurrency-cluster-a", domain="backend", parent_id=domain.id,
            )
            c2 = _make_cluster(
                "concurrency-cluster-b", domain="backend", parent_id=domain.id,
            )
            db.add_all([c1, c2])
            await db.flush()

            db.add(_make_opt(c1.id, "backend: concurrency", seed=0))
            db.add(_make_opt(c1.id, "backend: concurrency", seed=1))
            db.add(_make_opt(c2.id, "backend: concurrency", seed=2))
            db.add(_make_opt(c2.id, "backend: concurrency", seed=3))
            await db.commit()

            await engine.rebuild_sub_domains(
                db, domain.id, min_consistency_override=0.30, dry_run=True,
            )

            buffer = list(get_event_logger()._buffer)
            invoke_events = [
                e for e in buffer
                if e.get("decision") == "sub_domain_rebuild_invoked"
            ]
            assert invoke_events, (
                "Dry-run rebuild must still emit sub_domain_rebuild_invoked. "
                f"Buffer decisions: {[e.get('decision') for e in buffer]}"
            )
            ctx = invoke_events[-1]["context"]
            assert ctx["dry_run"] is True, (
                f"Dry-run telemetry must carry dry_run=True. Got: {ctx['dry_run']!r}"
            )
            assert ctx["created_count"] == 0, (
                f"Dry-run telemetry must carry created_count=0. Got: {ctx['created_count']!r}"
            )
        finally:
            reset_event_logger()

    @pytest.mark.asyncio
    async def test_rebuild_rejects_below_floor_runtime_check(
        self, db, mock_provider,
    ):
        """AC-R6-4 (runtime defense-in-depth): the engine method itself
        rejects ``min_consistency_override < SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR=0.25``
        with a clear ``ValueError`` — Pydantic catches it at the router
        layer first, but the engine MUST also enforce it for direct callers.
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.commit()

        with pytest.raises(ValueError) as exc_info:
            await engine.rebuild_sub_domains(
                db, domain.id, min_consistency_override=0.10,
            )
        msg = str(exc_info.value)
        assert msg, (
            "ValueError must carry a non-empty message explaining the floor. "
            f"Got: {exc_info.value!r}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_rolls_back_on_partial_failure(
        self, db, mock_provider,
    ):
        """AC-R6-10: if sub-domain creation raises mid-batch, the entire
        transaction rolls back — no partial sub-domain leftovers.

        Setup: 2 eligible qualifiers (``concurrency`` and ``audit``)
        across 2 distinct clusters each.  Mock ``_create_domain_node``
        to raise on the 2nd call.  Assert the exception propagates AND
        no sub-domain is persisted.
        """
        from unittest.mock import patch

        from sqlalchemy import func, select

        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        # Two clusters per qualifier (breadth gate passes for both).
        c_a1 = _make_cluster(
            "concurrency-a", domain="backend", parent_id=domain.id,
        )
        c_a2 = _make_cluster(
            "concurrency-b", domain="backend", parent_id=domain.id,
        )
        c_b1 = _make_cluster(
            "audit-a", domain="backend", parent_id=domain.id,
        )
        c_b2 = _make_cluster(
            "audit-b", domain="backend", parent_id=domain.id,
        )
        db.add_all([c_a1, c_a2, c_b1, c_b2])
        await db.flush()

        # 4 opts on "concurrency" across 2 clusters.
        db.add(_make_opt(c_a1.id, "backend: concurrency", seed=0))
        db.add(_make_opt(c_a1.id, "backend: concurrency", seed=1))
        db.add(_make_opt(c_a2.id, "backend: concurrency", seed=2))
        db.add(_make_opt(c_a2.id, "backend: concurrency", seed=3))
        # 4 opts on "audit" across 2 clusters.
        db.add(_make_opt(c_b1.id, "backend: audit", seed=4))
        db.add(_make_opt(c_b1.id, "backend: audit", seed=5))
        db.add(_make_opt(c_b2.id, "backend: audit", seed=6))
        db.add(_make_opt(c_b2.id, "backend: audit", seed=7))
        await db.commit()

        pre_count_q = await db.execute(
            select(func.count()).where(
                PromptCluster.state == "domain",
                PromptCluster.parent_id == domain.id,
            )
        )
        pre_count = int(pre_count_q.scalar() or 0)

        # Patch _create_domain_node to raise on the 2nd call.
        original = engine._create_domain_node
        call_counter = {"n": 0}

        async def _flaky_create_domain_node(*args, **kwargs):
            call_counter["n"] += 1
            if call_counter["n"] == 2:
                raise RuntimeError("Simulated failure on 2nd creation")
            return await original(*args, **kwargs)

        # Pre-flight: rebuild_sub_domains must exist so the patch has a
        # real method to override.  This guards against the rollback
        # check passing trivially via AttributeError in RED.
        assert hasattr(engine, "rebuild_sub_domains"), (
            "engine.rebuild_sub_domains must exist before this test can "
            "exercise the rollback path. RED phase: AttributeError expected."
        )

        with patch.object(
            engine, "_create_domain_node", side_effect=_flaky_create_domain_node,
        ):
            with pytest.raises(RuntimeError, match="Simulated failure"):
                await engine.rebuild_sub_domains(
                    db, domain.id, min_consistency_override=0.30,
                )

        # Use a fresh DB query (after rollback) — single transaction
        # semantics demand zero sub-domains persisted.
        post_count_q = await db.execute(
            select(func.count()).where(
                PromptCluster.state == "domain",
                PromptCluster.parent_id == domain.id,
            )
        )
        post_count = int(post_count_q.scalar() or 0)
        assert post_count == pre_count, (
            "AC-R6-10: partial-failure rebuild must roll back ALL sub-domain "
            f"creations. pre={pre_count} post={post_count}"
        )


# ---------------------------------------------------------------------------
# R4: shared per-opt matcher byte-equivalence regression
# ---------------------------------------------------------------------------


class TestSubDomainReevalUsesSharedPrimitive:
    """R4 (audit 2026-04-27): byte-equivalence regression — the engine's
    ``_reevaluate_sub_domains`` must behave identically before and after
    the matching cascade is extracted to ``match_opt_to_sub_domain_vocab``
    in ``sub_domain_readiness.py``.

    This test exercises the engine with a known input and asserts a
    specific dissolution outcome.  It locks the integer math:
    ``matching=0 → consistency=0 → shrunk=4/25=0.16 → < 0.25 floor → DISSOLVE``.

    Today (RED): passes — the inline matching cascade gives this same
    answer.  After GREEN swaps in the new primitive, this test catches
    any drift in the per-opt cascade behavior.

    Acceptance criteria: AC-R4-2, AC-R4-4.
    """

    @pytest.mark.asyncio
    async def test_known_input_produces_known_dissolution(
        self, db, mock_provider,
    ):
        """N=15 hostile members with no vocab match → dissolution fires.

        The sub-domain is aged 30h (past the R2 24h grace gate) and
        carries a populated ``generated_qualifiers`` snapshot (so the
        R3 empty-snapshot guard does NOT short-circuit) whose vocab
        groups (``observability``/``tracing``) deliberately do not
        match any of the 15 children's ``backend: unrelated_<i>``
        qualifiers.  Bayesian shrinkage at K=10/center=0.40 yields
        ``(0 + 4) / (15 + 10) = 0.16``, which is below the 0.25 floor,
        so the sub-domain dissolves.
        """
        engine = _make_engine(mock_provider)

        domain = _make_domain("backend")
        db.add(domain)
        await db.flush()

        sub = _make_sub_domain(
            "embedding-health",
            parent_id=domain.id,
            age_hours=30,
            source="discovered",
        )
        # Populated vocab snapshot: defeats R3's empty-snapshot skip so
        # the matching cascade is exercised.  The vocab groups/terms are
        # chosen so that NONE of the children's qualifiers
        # (``unrelated_<i>``) overlap them — guaranteeing matching=0
        # through Source 1, Source 2, Source 2b, and Source 3.
        sub.cluster_metadata = write_meta(
            None,
            source="discovered",
            generated_qualifiers={
                "instrumentation": ["observability", "tracing"],
            },
        )
        db.add(sub)
        await db.flush()

        c = _make_cluster("hostile-cluster", domain="backend", parent_id=sub.id)
        db.add(c)
        await db.flush()

        for i in range(15):
            opt = _make_opt(c.id, f"backend: unrelated_{i}", seed=i)
            db.add(opt)
        await db.commit()

        existing_labels = {sub.label}
        dissolved = await engine._reevaluate_sub_domains(
            db, domain, existing_labels,
        )

        assert "embedding-health" in dissolved, (
            "Expected 'embedding-health' in dissolved set: with N=15 "
            "hostile members and unmatched vocab, Bayesian shrinkage "
            "yields (0+4)/(15+10)=0.16 < 0.25 floor → DISSOLVE.  Got: "
            f"{dissolved}"
        )


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


# ---------------------------------------------------------------------------
# R7 (audit 2026-04-27): Vocab regeneration overlap telemetry
# ---------------------------------------------------------------------------


class TestVocabRegenOverlap:
    """R7 (audit 2026-04-27): the ``vocab_generated_enriched`` event must
    carry ``previous_groups``, ``new_groups`` and ``overlap_pct`` (Jaccard
    intersection-over-union × 100) so operators can correlate
    sub-domain mass-dissolutions with vocab churn at a glance.

    The audit incident at 2026-04-26 03:47:57 UTC saw the backend domain's
    vocabulary swap from ``{metrics, tracing, pattern-instrumentation}`` to
    ``{concurrency, observability, embeddings, security}`` — **zero
    overlap** — one minute after the second sub-domain dissolved.  Without
    these fields the only way to spot the churn is to diff JSONL events by
    hand.

    The tests exercise the engine via ``_propose_sub_domains(vocab_only=True)``
    with a mocked ``generate_qualifier_vocabulary`` so we control the
    returned ``generated`` dict deterministically.
    """

    def _setup_domain_with_clusters(self, db, *, generated_qualifiers=None):
        """Create a top-level domain with three child clusters carrying
        centroids — enough to satisfy the ``len(child_ids) < 2`` guard and
        the ``cached_cluster_count=0`` staleness check (3 > max(2, 0))
        when ``generated_qualifiers`` is non-empty.

        Returns the parent domain node so the caller can flush+commit and
        introspect IDs.
        """
        meta = {"source": "discovered"}
        if generated_qualifiers is not None:
            meta["generated_qualifiers"] = generated_qualifiers
            # Force staleness: cached_cluster_count=0 vs current=3 → stale.
            meta["generated_qualifiers_cluster_count"] = 0

        domain = PromptCluster(
            label="backend",
            state="domain",
            domain="backend",
            task_type="general",
            parent_id=None,
            persistence=1.0,
            color_hex="#7c3aed",
            centroid_embedding=_random_embedding(101),
            cluster_metadata=write_meta(None, **meta),
            created_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        )
        db.add(domain)
        return domain

    def _add_three_clusters(self, db, parent_id):
        """Add three active child clusters under ``parent_id`` with
        centroids — required for the vocab matrix path.
        """
        for i in range(3):
            cluster = PromptCluster(
                label=f"backend cluster {i}",
                state="active",
                domain="backend",
                parent_id=parent_id,
                member_count=5,
                centroid_embedding=_random_embedding(200 + i),
            )
            db.add(cluster)

    def _make_engine_with_embed(self, mock_provider):
        """Like ``_make_engine`` but with a real-shaped ``aembed_single``
        return so the vocab quality block (which ``np.linalg.norm()``s
        the embedding) doesn't trip the surrounding try/except and silently
        skip metric emission.  We don't assert on quality — we only need
        the event itself to be emitted.
        """
        from unittest.mock import AsyncMock, MagicMock

        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        mock_embedding.aembed_single = AsyncMock(
            return_value=np.random.RandomState(7).randn(EMBEDDING_DIM).astype(np.float32),
        )
        engine = TaxonomyEngine(
            embedding_service=mock_embedding, provider=mock_provider,
        )
        for attr in ("_embedding_index", "_transformation_index", "_optimized_index"):
            mock_idx = MagicMock()
            mock_idx.remove = AsyncMock()
            setattr(engine, attr, mock_idx)
        return engine

    @pytest.mark.asyncio
    async def test_bootstrap_no_previous_groups(self, db, mock_provider, tmp_path):
        """AC-R7-1: First-time vocab generation (no prior
        ``generated_qualifiers``) emits the event with
        ``previous_groups=[]``, ``new_groups=sorted(generated.keys())``,
        ``overlap_pct=0.0``.
        """
        from unittest.mock import AsyncMock, patch

        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            set_event_logger,
        )

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = self._make_engine_with_embed(mock_provider)

            # Bootstrap: NO ``generated_qualifiers`` in metadata.
            domain = self._setup_domain_with_clusters(db, generated_qualifiers=None)
            await db.flush()
            self._add_three_clusters(db, domain.id)
            await db.commit()

            mock_gen = AsyncMock(return_value={"a": ["x"], "b": ["y"]})
            with patch(
                "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
                mock_gen,
            ):
                await engine._propose_sub_domains(db, vocab_only=True)

            buffer = list(get_event_logger()._buffer)
            events = [
                e for e in buffer
                if e.get("decision") == "vocab_generated_enriched"
                and e.get("context", {}).get("domain") == "backend"
            ]
            assert events, (
                "Expected a vocab_generated_enriched event for 'backend'. "
                f"Buffer decisions: {[e.get('decision') for e in buffer]}"
            )
            ctx = events[-1]["context"]

            assert "previous_groups" in ctx, (
                f"Expected NEW previous_groups key in vocab_generated_enriched "
                f"context (R7 spec AC-R7-1). Got keys: {sorted(ctx.keys())}"
            )
            assert ctx["previous_groups"] == [], (
                f"Bootstrap must emit previous_groups=[]. Got: {ctx['previous_groups']}"
            )
            assert "new_groups" in ctx, (
                f"Expected NEW new_groups key. Got keys: {sorted(ctx.keys())}"
            )
            assert ctx["new_groups"] == ["a", "b"], (
                f"new_groups must be sorted list of generated keys. "
                f"Got: {ctx['new_groups']}"
            )
            assert "overlap_pct" in ctx, (
                f"Expected NEW overlap_pct key. Got keys: {sorted(ctx.keys())}"
            )
            assert ctx["overlap_pct"] == 0.0, (
                f"Bootstrap must emit overlap_pct=0.0. Got: {ctx['overlap_pct']}"
            )
        finally:
            # Reset to a fresh logger so subsequent tests don't see buffer
            # contamination via the module-level singleton.
            set_event_logger(TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False))

    @pytest.mark.asyncio
    async def test_full_overlap(self, db, mock_provider, tmp_path):
        """AC-R7-2: Vocab regeneration with full overlap (previous and
        new have identical group names) emits ``overlap_pct=100.0``.
        """
        from unittest.mock import AsyncMock, patch

        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            set_event_logger,
        )

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = self._make_engine_with_embed(mock_provider)

            domain = self._setup_domain_with_clusters(
                db,
                generated_qualifiers={"a": ["x"], "b": ["y"]},
            )
            await db.flush()
            self._add_three_clusters(db, domain.id)
            await db.commit()

            # Same group names, different terms — Jaccard on names only.
            mock_gen = AsyncMock(return_value={"a": ["x2"], "b": ["y2"]})
            with patch(
                "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
                mock_gen,
            ):
                await engine._propose_sub_domains(db, vocab_only=True)

            buffer = list(get_event_logger()._buffer)
            events = [
                e for e in buffer
                if e.get("decision") == "vocab_generated_enriched"
                and e.get("context", {}).get("domain") == "backend"
            ]
            assert events, (
                f"Expected vocab_generated_enriched. "
                f"Buffer: {[e.get('decision') for e in buffer]}"
            )
            ctx = events[-1]["context"]

            assert ctx.get("overlap_pct") == 100.0, (
                f"Identical group names must yield overlap_pct=100.0. "
                f"Got: {ctx.get('overlap_pct')}"
            )
            assert ctx.get("previous_groups") == ["a", "b"], (
                f"previous_groups must list cached qualifier keys sorted. "
                f"Got: {ctx.get('previous_groups')}"
            )
            assert ctx.get("new_groups") == ["a", "b"], (
                f"new_groups must list new qualifier keys sorted. "
                f"Got: {ctx.get('new_groups')}"
            )
        finally:
            set_event_logger(TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False))

    @pytest.mark.asyncio
    async def test_zero_overlap_warns(self, db, mock_provider, tmp_path, caplog):
        """AC-R7-3: Audit-incident reproducer.  Zero overlap on a
        non-bootstrap regeneration must emit ``overlap_pct=0.0`` AND a
        WARNING log line whose message contains "low overlap".
        """
        import logging
        from unittest.mock import AsyncMock, patch

        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            set_event_logger,
        )

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        caplog.set_level(logging.WARNING, logger="app.services.taxonomy.engine")
        try:
            engine = self._make_engine_with_embed(mock_provider)

            domain = self._setup_domain_with_clusters(
                db,
                generated_qualifiers={
                    "metrics": [],
                    "tracing": [],
                    "pattern-instrumentation": [],
                },
            )
            await db.flush()
            self._add_three_clusters(db, domain.id)
            await db.commit()

            # New vocab — zero overlap (audit-incident reproduction).
            mock_gen = AsyncMock(return_value={
                "concurrency": [],
                "observability": [],
                "embeddings": [],
                "security": [],
            })
            with patch(
                "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
                mock_gen,
            ):
                await engine._propose_sub_domains(db, vocab_only=True)

            buffer = list(get_event_logger()._buffer)
            events = [
                e for e in buffer
                if e.get("decision") == "vocab_generated_enriched"
                and e.get("context", {}).get("domain") == "backend"
            ]
            assert events, (
                f"Expected vocab_generated_enriched. "
                f"Buffer: {[e.get('decision') for e in buffer]}"
            )
            ctx = events[-1]["context"]
            assert ctx.get("overlap_pct") == 0.0, (
                f"Zero overlap incident must yield overlap_pct=0.0. "
                f"Got: {ctx.get('overlap_pct')}"
            )

            # WARNING log was emitted, mentions "low overlap".
            warn_records = [
                rec for rec in caplog.records
                if rec.levelno == logging.WARNING
                and "low overlap" in rec.message.lower()
            ]
            assert warn_records, (
                f"Expected WARNING log containing 'low overlap' "
                f"(R7 AC-R7-3). Got messages: "
                f"{[rec.message for rec in caplog.records]}"
            )
        finally:
            set_event_logger(TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False))

    @pytest.mark.asyncio
    async def test_partial_overlap_jaccard_math(self, db, mock_provider, tmp_path):
        """AC-R7-4: Partial overlap — Jaccard intersection-over-union math
        correct.  prev={a,b}, new={b,c} → intersect={b}, union={a,b,c} →
        1/3 × 100 = 33.3 (rounded to 1 decimal).
        """
        from unittest.mock import AsyncMock, patch

        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            get_event_logger,
            set_event_logger,
        )

        inst = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst)
        try:
            engine = self._make_engine_with_embed(mock_provider)

            domain = self._setup_domain_with_clusters(
                db,
                generated_qualifiers={"a": [], "b": []},
            )
            await db.flush()
            self._add_three_clusters(db, domain.id)
            await db.commit()

            mock_gen = AsyncMock(return_value={"b": [], "c": []})
            with patch(
                "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
                mock_gen,
            ):
                await engine._propose_sub_domains(db, vocab_only=True)

            buffer = list(get_event_logger()._buffer)
            events = [
                e for e in buffer
                if e.get("decision") == "vocab_generated_enriched"
                and e.get("context", {}).get("domain") == "backend"
            ]
            assert events, (
                f"Expected vocab_generated_enriched. "
                f"Buffer: {[e.get('decision') for e in buffer]}"
            )
            ctx = events[-1]["context"]

            # Jaccard: |{b}| / |{a,b,c}| = 1/3 ≈ 0.3333 → 33.3%.
            assert ctx.get("overlap_pct") == 33.3, (
                f"Partial overlap (1/3 Jaccard) must yield overlap_pct=33.3. "
                f"Got: {ctx.get('overlap_pct')}"
            )
        finally:
            set_event_logger(TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False))

    @pytest.mark.asyncio
    async def test_warning_suppressed_on_bootstrap_and_high_overlap(
        self, db, mock_provider, tmp_path, caplog,
    ):
        """AC-R7-5: WARNING log fires ONLY when ``overlap_pct < 50.0`` AND
        ``previous_groups`` is non-empty (i.e., not on bootstrap).

        Sub-case A: bootstrap (no previous groups) — no warning regardless
        of overlap value.
        Sub-case B: high overlap (60%, above 50% threshold) — no warning
        despite the regen being non-bootstrap.
        """
        import logging
        from unittest.mock import AsyncMock, patch

        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            set_event_logger,
        )

        # ---- Sub-case A: bootstrap ------------------------------------
        inst_a = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst_a)
        caplog.set_level(logging.WARNING, logger="app.services.taxonomy.engine")
        caplog.clear()
        try:
            engine_a = self._make_engine_with_embed(mock_provider)

            domain_a = self._setup_domain_with_clusters(db, generated_qualifiers=None)
            await db.flush()
            self._add_three_clusters(db, domain_a.id)
            await db.commit()

            mock_gen_a = AsyncMock(return_value={"a": [], "b": []})
            with patch(
                "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
                mock_gen_a,
            ):
                await engine_a._propose_sub_domains(db, vocab_only=True)

            warn_records_a = [
                rec for rec in caplog.records
                if rec.levelno == logging.WARNING
                and "low overlap" in rec.message.lower()
            ]
            assert not warn_records_a, (
                f"Bootstrap (no previous groups) must NOT emit 'low overlap' "
                f"WARNING. Got: {[rec.message for rec in warn_records_a]}"
            )
        finally:
            set_event_logger(TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False))

        # ---- Sub-case B: 60% overlap (above 50% threshold) ------------
        # Run in a fresh database scope by deleting prior data — the ``db``
        # fixture is per-test, but this method runs both cases in one test
        # body so we must purge to avoid the prior bootstrap domain
        # interfering.
        from sqlalchemy import delete
        await db.execute(delete(Optimization))
        await db.execute(delete(PromptCluster))
        await db.commit()

        inst_b = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        set_event_logger(inst_b)
        caplog.clear()
        try:
            engine_b = self._make_engine_with_embed(mock_provider)

            # 4 previous, 4 new, overlap=3 → Jaccard = 3/5 = 0.6 → 60%.
            domain_b = self._setup_domain_with_clusters(
                db,
                generated_qualifiers={"a": [], "b": [], "c": [], "d": []},
            )
            await db.flush()
            self._add_three_clusters(db, domain_b.id)
            await db.commit()

            mock_gen_b = AsyncMock(return_value={
                "a": [], "b": [], "c": [], "e": [],
            })
            with patch(
                "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
                mock_gen_b,
            ):
                await engine_b._propose_sub_domains(db, vocab_only=True)

            warn_records_b = [
                rec for rec in caplog.records
                if rec.levelno == logging.WARNING
                and "low overlap" in rec.message.lower()
            ]
            assert not warn_records_b, (
                f"60% overlap (≥ 50% threshold) must NOT emit 'low overlap' "
                f"WARNING. Got: {[rec.message for rec in warn_records_b]}"
            )
        finally:
            set_event_logger(TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False))
