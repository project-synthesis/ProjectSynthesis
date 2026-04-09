"""Tests for Phase 2A project-scoped cluster assignment."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine

EMBEDDING_DIM = 384


def _unit_vec(seed: int) -> np.ndarray:
    """Deterministic unit vector from a seed."""
    rng = np.random.RandomState(seed)
    v = rng.randn(EMBEDDING_DIM).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


class TestEngineProjectCaches:
    """TaxonomyEngine has _cluster_project_cache and _legacy_project_id."""

    def test_caches_initialised(self):
        mock_embedding = MagicMock()
        mock_provider = MagicMock()
        engine = TaxonomyEngine(
            embedding_service=mock_embedding, provider=mock_provider
        )
        assert hasattr(engine, "_cluster_project_cache")
        assert hasattr(engine, "_legacy_project_id")
        assert engine._cluster_project_cache == {}
        assert engine._legacy_project_id is None

    def test_process_optimization_accepts_repo_full_name(self):
        """Signature accepts repo_full_name keyword argument."""
        import inspect

        sig = inspect.signature(TaxonomyEngine.process_optimization)
        params = list(sig.parameters.keys())
        assert "repo_full_name" in params
        # Default should be None
        assert sig.parameters["repo_full_name"].default is None


class TestResolveOrCreateDomain:
    """Tests for _resolve_or_create_domain helper."""

    @pytest.mark.asyncio
    async def test_bootstraps_general_domain(self, db_session: AsyncSession):
        """_resolve_or_create_domain creates general domain if none exists."""
        from app.services.taxonomy.family_ops import _resolve_or_create_domain

        project = PromptCluster(
            label="test-project",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(project)
        await db_session.flush()

        domain = await _resolve_or_create_domain(db_session, project.id, "backend")
        assert domain is not None
        assert domain.label == "general"
        assert domain.state == "domain"
        assert domain.parent_id == project.id

    @pytest.mark.asyncio
    async def test_returns_matching_domain(self, db_session: AsyncSession):
        """Returns existing domain matching the requested label."""
        from app.services.taxonomy.family_ops import _resolve_or_create_domain

        project = PromptCluster(
            label="test-project",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(project)
        await db_session.flush()

        existing = PromptCluster(
            label="backend",
            state="domain",
            domain="backend",
            task_type="general",
            member_count=0,
            parent_id=project.id,
        )
        db_session.add(existing)
        await db_session.flush()

        domain = await _resolve_or_create_domain(db_session, project.id, "backend")
        assert domain is not None
        assert domain.id == existing.id
        assert domain.label == "backend"

    @pytest.mark.asyncio
    async def test_returns_none_without_project_id(self, db_session: AsyncSession):
        """Returns None when project_id is None."""
        from app.services.taxonomy.family_ops import _resolve_or_create_domain

        result = await _resolve_or_create_domain(db_session, None, "backend")
        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_to_general(self, db_session: AsyncSession):
        """Falls back to 'general' domain when specific label not found."""
        from app.services.taxonomy.family_ops import _resolve_or_create_domain

        project = PromptCluster(
            label="test-project",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(project)
        await db_session.flush()

        general = PromptCluster(
            label="general",
            state="domain",
            domain="general",
            task_type="general",
            member_count=0,
            parent_id=project.id,
        )
        db_session.add(general)
        await db_session.flush()

        # Request "backend" but only "general" exists → falls back
        domain = await _resolve_or_create_domain(db_session, project.id, "backend")
        assert domain is not None
        assert domain.id == general.id
        assert domain.label == "general"


class TestGetProjectDomainIds:
    """Tests for _get_project_domain_ids helper."""

    @pytest.mark.asyncio
    async def test_returns_domain_ids(self, db_session: AsyncSession):
        """Returns domain node IDs for a project."""
        from app.services.taxonomy.family_ops import _get_project_domain_ids

        project = PromptCluster(
            label="test-project",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(project)
        await db_session.flush()

        domain = PromptCluster(
            label="backend",
            state="domain",
            domain="backend",
            task_type="general",
            member_count=0,
            parent_id=project.id,
        )
        db_session.add(domain)
        await db_session.flush()

        ids = await _get_project_domain_ids(db_session, project.id)
        assert domain.id in ids

    @pytest.mark.asyncio
    async def test_empty_for_no_domains(self, db_session: AsyncSession):
        """Returns empty set when project has no domain nodes."""
        from app.services.taxonomy.family_ops import _get_project_domain_ids

        project = PromptCluster(
            label="test-project",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(project)
        await db_session.flush()

        ids = await _get_project_domain_ids(db_session, project.id)
        assert ids == set()


class TestAssignClusterProjectScoped:
    """Tests for assign_cluster with project_id parameter."""

    @pytest.mark.asyncio
    async def test_signature_accepts_project_id(self):
        """assign_cluster accepts project_id keyword argument."""
        import inspect

        from app.services.taxonomy.family_ops import assign_cluster

        sig = inspect.signature(assign_cluster)
        assert "project_id" in sig.parameters
        assert sig.parameters["project_id"].default is None

    @pytest.mark.asyncio
    async def test_project_scoped_creates_under_project_domain(
        self, db_session: AsyncSession
    ):
        """New cluster created with project_id is parented to project domain."""
        from app.services.taxonomy.family_ops import assign_cluster

        # Set up project with a domain
        project = PromptCluster(
            label="my-project",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(project)
        await db_session.flush()

        domain = PromptCluster(
            label="general",
            state="domain",
            domain="general",
            task_type="general",
            member_count=0,
            parent_id=project.id,
        )
        db_session.add(domain)
        await db_session.flush()

        emb = _unit_vec(42)
        cluster = await assign_cluster(
            db=db_session,
            embedding=emb,
            label="test prompt",
            domain="general",
            task_type="coding",
            overall_score=7.0,
            project_id=project.id,
        )

        assert cluster is not None
        assert cluster.parent_id == domain.id
        assert cluster.label == "test prompt"

    @pytest.mark.asyncio
    async def test_project_scoped_bootstraps_domain(
        self, db_session: AsyncSession
    ):
        """When project has no domains, assign_cluster auto-creates general."""
        from app.services.taxonomy.family_ops import assign_cluster

        project = PromptCluster(
            label="empty-project",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(project)
        await db_session.flush()

        emb = _unit_vec(99)
        cluster = await assign_cluster(
            db=db_session,
            embedding=emb,
            label="first prompt",
            domain="backend",
            task_type="coding",
            overall_score=6.0,
            project_id=project.id,
        )

        assert cluster is not None
        # Should have a parent (the auto-created general domain)
        assert cluster.parent_id is not None

    @pytest.mark.asyncio
    async def test_without_project_id_uses_global(
        self, db_session: AsyncSession
    ):
        """Without project_id, falls back to global domain lookup."""
        from app.services.taxonomy.family_ops import assign_cluster

        # Global domain node (not under any project)
        global_domain = PromptCluster(
            label="general",
            state="domain",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(global_domain)
        await db_session.flush()

        emb = _unit_vec(7)
        cluster = await assign_cluster(
            db=db_session,
            embedding=emb,
            label="global prompt",
            domain="general",
            task_type="coding",
            overall_score=5.0,
        )

        assert cluster is not None
        assert cluster.parent_id == global_domain.id
