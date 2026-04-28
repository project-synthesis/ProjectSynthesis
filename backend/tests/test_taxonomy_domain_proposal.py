"""v0.4.11 P0a — Domain proposal cluster-count floor.

RED-phase tests for `DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS`. The bug is that
`engine._propose_domains()` creates a top-level domain from a single seed
cluster (per-cluster pass at engine.py:1685-1755) AND from a single
contributing cluster in the pooled pass (engine.py:1822-1828) when the
pooled member count is high enough. A single-cluster signal can therefore
promote a top-level domain — the live `fullstack` ghost was created this
way (3 members, 67% consistency, then merged out leaving an empty domain
node frozen by the 48h dissolution gate).

Both proposal paths must require evidence from ≥2 distinct contributing
clusters.

See `docs/specs/domain-proposal-hardening-2026-04-28.md` §P0a.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Optimization, PromptCluster
from app.services.embedding_service import EmbeddingService
from app.services.taxonomy.engine import TaxonomyEngine
from app.services.taxonomy.event_logger import (
    TaxonomyEventLogger,
    set_event_logger,
)

EMBEDDING_DIM = 384


# ---------------------------------------------------------------------------
# Fixtures (mirror backend/tests/taxonomy/conftest.py — this file lives one
# directory up so the taxonomy-package conftest does not apply).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def mock_embedding() -> EmbeddingService:
    """EmbeddingService mock returning deterministic unit vectors per text."""
    svc = MagicMock(spec=EmbeddingService)
    svc.dimension = EMBEDDING_DIM

    def _embed(text: str) -> np.ndarray:
        rng = np.random.RandomState(hash(text) % 2**31)
        vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-9)

    svc.embed_single.side_effect = _embed
    svc.aembed_single = AsyncMock(side_effect=_embed)
    svc.embed_texts.side_effect = lambda texts: [_embed(t) for t in texts]
    svc.aembed_texts = AsyncMock(side_effect=lambda texts: [_embed(t) for t in texts])
    svc.cosine_search = EmbeddingService.cosine_search
    return svc


@pytest.fixture(autouse=True)
def _setup_event_logger(tmp_path: Path) -> TaxonomyEventLogger:
    """Bind a fresh per-test logger so log_decision() doesn't raise."""
    logger = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
    set_event_logger(logger)
    return logger


async def _seed_general_domain(db: AsyncSession) -> PromptCluster:
    node = PromptCluster(
        label="general", state="domain", domain="general", persistence=1.0,
        color_hex="#7a7a9e", cluster_metadata={"source": "seed"},
    )
    db.add(node)
    await db.flush()
    return node


# ---------------------------------------------------------------------------
# Tests — TestDomainProposalMinSourceClusters (5 ACs)
# ---------------------------------------------------------------------------


class TestDomainProposalMinSourceClusters:
    """v0.4.11 P0a — both proposal paths require ≥2 contributing clusters."""

    @pytest.mark.asyncio
    async def test_single_cluster_rejected(
        self, db: AsyncSession, mock_embedding: EmbeddingService,
    ) -> None:
        """AC-P0a-1: a single cluster with 3 members + 100% consistency
        on a NOVEL domain_raw must NOT create a top-level domain.

        Pre-fix expectation: FAIL (per-cluster pass currently creates
        the domain from this one cluster — exactly the fullstack ghost
        pathology).
        """
        general = await _seed_general_domain(db)

        cluster = PromptCluster(
            label="single-source", state="active", domain="general",
            parent_id=general.id, member_count=3, coherence=0.75,
            centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
        )
        db.add(cluster)
        await db.flush()

        for i in range(3):
            db.add(Optimization(
                raw_prompt=f"testdomain prompt {i}",
                domain="general", domain_raw="testdomain",
                cluster_id=cluster.id, status="completed",
            ))
        await db.commit()

        engine = TaxonomyEngine(
            embedding_service=mock_embedding,
            provider_resolver=lambda: None,
        )
        created = await engine._propose_domains(db)

        assert "testdomain" not in created, (
            "single-cluster signal must be rejected by the cluster-count floor"
        )

        # Belt-and-suspenders: no testdomain row in the DB either
        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label == "testdomain",
            )
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_two_clusters_promote(
        self, db: AsyncSession, mock_embedding: EmbeddingService,
    ) -> None:
        """AC-P0a-2: TWO distinct clusters, each with 3 members and
        consistent domain_raw, ARE sufficient to promote.

        Pre-fix expectation: PASS (per-cluster pass already creates from
        either single cluster, so two definitely succeed). Forward-
        compatible regression guard for the GREEN-phase aggregation.
        """
        general = await _seed_general_domain(db)

        for ci in range(2):
            cluster = PromptCluster(
                label=f"source-{ci}", state="active", domain="general",
                parent_id=general.id, member_count=3, coherence=0.75,
                centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
            )
            db.add(cluster)
            await db.flush()
            for i in range(3):
                db.add(Optimization(
                    raw_prompt=f"testdomain2 cluster{ci} prompt {i}",
                    domain="general", domain_raw="testdomain2",
                    cluster_id=cluster.id, status="completed",
                ))
        await db.commit()

        engine = TaxonomyEngine(
            embedding_service=mock_embedding,
            provider_resolver=lambda: None,
        )
        created = await engine._propose_domains(db)

        assert "testdomain2" in created, (
            "two contributing clusters must satisfy the cluster-count floor"
        )

        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label == "testdomain2",
            )
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_pooled_single_cluster_rejected(
        self, db: AsyncSession, mock_embedding: EmbeddingService,
    ) -> None:
        """AC-P0a-3: pooled-pass scenario — 1 cluster contributing 5
        opts that all share the same domain_raw must NOT promote even
        though the pooled member count crosses the existing member-floor
        gate.

        Setup forces the per-cluster pass to skip (cluster.member_count=1
        is below the per-cluster member gate) so the pooled pass is the
        only path that can promote.

        Pre-fix expectation: FAIL (pooled pass checks pooled member
        count but NOT pooled cluster count — a single contributing
        cluster with enough internal members currently promotes).
        """
        general = await _seed_general_domain(db)

        # member_count=1 fails the per-cluster SQL gate (>=3, or >=2 in
        # bootstrap mode) so the per-cluster pass cannot promote.  The
        # pooled pass queries ALL active clusters under general without
        # a member filter and pools by domain_raw primary — finding 5
        # pooled members from a single contributing cluster.
        cluster = PromptCluster(
            label="pooled-source", state="active", domain="general",
            parent_id=general.id, member_count=1, coherence=0.5,
            centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
        )
        db.add(cluster)
        await db.flush()

        for i in range(5):
            db.add(Optimization(
                raw_prompt=f"pooledtestdomain prompt {i}",
                domain="general", domain_raw="pooledtestdomain",
                cluster_id=cluster.id, status="completed",
            ))
        await db.commit()

        engine = TaxonomyEngine(
            embedding_service=mock_embedding,
            provider_resolver=lambda: None,
        )
        created = await engine._propose_domains(db)

        assert "pooledtestdomain" not in created, (
            "pooled pass with a single contributing cluster must be "
            "rejected by the cluster-count floor"
        )

        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label == "pooledtestdomain",
            )
        )
        assert result.scalar_one_or_none() is None

    def test_invariant_asserts_at_import(self) -> None:
        """AC-P0a-4: module-level invariant — `DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS`
        must exist as a constant >= 1 in `app.services.taxonomy._constants`.

        Pre-fix expectation: FAIL (constant doesn't exist; ImportError).
        Mirrors the R8-style fail-fast pattern in
        `_validate_threshold_invariants()`.
        """
        from app.services.taxonomy._constants import (  # noqa: PLC0415
            DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS,
        )

        assert isinstance(DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS, int)
        assert DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS >= 1, (
            "DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS must be >= 1; otherwise "
            "the proposal gate would never reject."
        )
        # Spec default
        assert DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS == 2, (
            "Spec sets the default to 2 — single-cluster signals must be "
            "insufficient evidence for top-level domain promotion."
        )

    @pytest.mark.asyncio
    async def test_rejection_event_emitted(
        self,
        db: AsyncSession,
        mock_embedding: EmbeddingService,
        _setup_event_logger: TaxonomyEventLogger,
    ) -> None:
        """AC-P0a-5: when the cluster-count floor rejects a primary,
        a `proposal_rejected_min_source_clusters` event must be emitted
        for forensic visibility.

        Pre-fix expectation: FAIL (no such rejection logic, no event).
        """
        general = await _seed_general_domain(db)

        cluster = PromptCluster(
            label="single-source", state="active", domain="general",
            parent_id=general.id, member_count=3, coherence=0.75,
            centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
        )
        db.add(cluster)
        await db.flush()
        for i in range(3):
            db.add(Optimization(
                raw_prompt=f"ghostdomain prompt {i}",
                domain="general", domain_raw="ghostdomain",
                cluster_id=cluster.id, status="completed",
            ))
        await db.commit()

        engine = TaxonomyEngine(
            embedding_service=mock_embedding,
            provider_resolver=lambda: None,
        )
        await engine._propose_domains(db)

        recent = _setup_event_logger.get_recent(limit=100)
        rejection_events = [
            ev for ev in recent
            if ev.get("decision") == "proposal_rejected_min_source_clusters"
        ]
        assert rejection_events, (
            "expected a `proposal_rejected_min_source_clusters` event in the "
            f"ring buffer; got decisions={[ev.get('decision') for ev in recent]}"
        )

        # Forensic context must identify the rejected primary.
        ctx = rejection_events[0].get("context") or {}
        assert ctx.get("domain_label") == "ghostdomain", (
            "rejection event must carry the rejected primary label in context"
        )
