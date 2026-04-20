"""ADR-005 C1c — dissolution routes minority-project members to same-project targets.

Verifies that ``_reassign_to_active()`` prefers same-project targets over
higher-cosine cross-project targets, preventing the "silent leak into
Legacy" regression described in the ADR-005 plan matrix:

    "Mixed-cluster dissolution | C1c routes minority-project members to
     same-project targets first, preventing silent leak."

Also verifies the explicit ``preferred_project_id`` kwarg (spec-exact
uniform preference) overrides per-opt inference when set.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.models import Optimization, PromptCluster
from app.services.taxonomy.event_logger import TaxonomyEventLogger, set_event_logger
from app.services.taxonomy.warm_phases import _reassign_to_active

EMBEDDING_DIM = 384


def _unit(seed: int) -> np.ndarray:
    rng = np.random.RandomState(seed)
    v = rng.randn(EMBEDDING_DIM).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def _blend(a: np.ndarray, b: np.ndarray, alpha: float) -> np.ndarray:
    """Unit-normalised blend — used to control cosine similarity between
    embeddings for deterministic routing assertions."""
    v = alpha * a + (1.0 - alpha) * b
    return v / (np.linalg.norm(v) + 1e-9)


@pytest.fixture(autouse=True)
def setup_event_logger(tmp_path):
    logger = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
    set_event_logger(logger)
    yield logger


class TestDissolveSameProjectPref:
    @pytest.mark.asyncio
    async def test_minority_member_routes_to_own_project(self, db) -> None:
        """Per-opt default: minority-project member lands in its own project's
        target even when a cross-project target is marginally closer."""
        # Two projects, each with an active target cluster in the same domain.
        # Both targets live near a common semantic center so cosine similarity
        # between the minority member and each target is comparable; the
        # cross-project one is deliberately *closer* in raw cosine so the
        # same-project preference must be what tips the selection.
        center = _unit(1)
        near_center = _blend(center, _unit(2), 0.95)  # very close to center
        offset = _blend(center, _unit(3), 0.80)       # further from center

        project_a_target = PromptCluster(
            label="proj-a target",
            state="active",
            domain="coding",
            centroid_embedding=offset.tobytes(),
            member_count=10,
            coherence=0.8,
            dominant_project_id="project-a",
        )
        project_b_target = PromptCluster(
            label="proj-b target",
            state="active",
            domain="coding",
            centroid_embedding=near_center.tobytes(),
            member_count=10,
            coherence=0.8,
            dominant_project_id="project-b",
        )
        db.add_all([project_a_target, project_b_target])
        await db.flush()

        # Minority member (project-a opt) with embedding near the center.
        # Raw cosine favours project-b-target (near_center), but same-project
        # preference should steer the opt to project-a-target — provided the
        # margin stays within CROSS_PROJECT_REASSIGN_MARGIN (0.10).
        member_emb = center
        minority_opt = Optimization(
            raw_prompt="minority member prompt",
            domain="coding",
            project_id="project-a",
            embedding=member_emb.tobytes(),
        )
        db.add(minority_opt)
        await db.flush()

        result = await _reassign_to_active(
            db=db,
            opt_ids=[minority_opt.id],
            opt_embeddings=[member_emb],
        )
        await db.flush()
        await db.refresh(minority_opt)

        assert minority_opt.cluster_id == project_a_target.id, (
            "Minority project-a member should land in project-a target, "
            "not project-b target (silent leak)"
        )
        assert result, "expected a reassignment record"
        assert result[0]["cluster_id"] == project_a_target.id

    @pytest.mark.asyncio
    async def test_cross_project_allowed_when_margin_exceeds_threshold(
        self, db,
    ) -> None:
        """When cross-project similarity beats same-project by >0.10 cosine,
        same-project preference yields to the cross-project winner."""
        # Same-project target is deliberately far from the member embedding
        # so the margin exceeds CROSS_PROJECT_REASSIGN_MARGIN (0.10).
        center = _unit(10)
        other = _unit(11)

        project_a_far = PromptCluster(
            label="proj-a far target",
            state="active",
            domain="coding",
            centroid_embedding=other.tobytes(),  # orthogonal-ish
            member_count=10,
            coherence=0.8,
            dominant_project_id="project-a",
        )
        project_b_close = PromptCluster(
            label="proj-b close target",
            state="active",
            domain="coding",
            centroid_embedding=center.tobytes(),  # exact match
            member_count=10,
            coherence=0.8,
            dominant_project_id="project-b",
        )
        db.add_all([project_a_far, project_b_close])
        await db.flush()

        minority_opt = Optimization(
            raw_prompt="mismatched project prompt",
            domain="coding",
            project_id="project-a",
            embedding=center.tobytes(),
        )
        db.add(minority_opt)
        await db.flush()

        await _reassign_to_active(
            db=db,
            opt_ids=[minority_opt.id],
            opt_embeddings=[center],
        )
        await db.flush()
        await db.refresh(minority_opt)

        assert minority_opt.cluster_id == project_b_close.id, (
            "Same-project preference must yield when cross-project cosine "
            "wins by more than CROSS_PROJECT_REASSIGN_MARGIN"
        )

    @pytest.mark.asyncio
    async def test_explicit_preferred_project_id_overrides_per_opt(self, db) -> None:
        """Explicit ``preferred_project_id`` pins every opt to the given
        project, overriding per-opt ``project_id`` inference."""
        center = _unit(20)
        near_center = _blend(center, _unit(21), 0.95)
        offset = _blend(center, _unit(22), 0.80)

        project_a_target = PromptCluster(
            label="proj-a target (preferred)",
            state="active",
            domain="coding",
            centroid_embedding=offset.tobytes(),
            member_count=10,
            coherence=0.8,
            dominant_project_id="project-a",
        )
        project_b_target = PromptCluster(
            label="proj-b target (closer)",
            state="active",
            domain="coding",
            centroid_embedding=near_center.tobytes(),
            member_count=10,
            coherence=0.8,
            dominant_project_id="project-b",
        )
        db.add_all([project_a_target, project_b_target])
        await db.flush()

        # Opt's own project is project-b, but caller explicitly overrides.
        proj_b_opt = Optimization(
            raw_prompt="opt tagged project-b",
            domain="coding",
            project_id="project-b",
            embedding=center.tobytes(),
        )
        db.add(proj_b_opt)
        await db.flush()

        await _reassign_to_active(
            db=db,
            opt_ids=[proj_b_opt.id],
            opt_embeddings=[center],
            preferred_project_id="project-a",
        )
        await db.flush()
        await db.refresh(proj_b_opt)

        assert proj_b_opt.cluster_id == project_a_target.id, (
            "Explicit preferred_project_id must override per-opt inference"
        )

    @pytest.mark.asyncio
    async def test_mixed_cluster_members_preserve_own_attribution(
        self, db,
    ) -> None:
        """Mixed-cluster dissolution batch: project-a member → project-a
        target, project-b member → project-b target. No silent leakage."""
        center = _unit(30)
        near_center = _blend(center, _unit(31), 0.95)

        target_a = PromptCluster(
            label="target-a",
            state="active",
            domain="coding",
            centroid_embedding=center.tobytes(),
            member_count=10,
            coherence=0.8,
            dominant_project_id="project-a",
        )
        target_b = PromptCluster(
            label="target-b",
            state="active",
            domain="coding",
            centroid_embedding=near_center.tobytes(),
            member_count=10,
            coherence=0.8,
            dominant_project_id="project-b",
        )
        db.add_all([target_a, target_b])
        await db.flush()

        # Create 2 members per project — simulates mixed cluster dissolution.
        member_emb = _blend(center, near_center, 0.5)
        opts: list[Optimization] = []
        for i in range(4):
            project = "project-a" if i < 2 else "project-b"
            opt = Optimization(
                raw_prompt=f"mixed member {i}",
                domain="coding",
                project_id=project,
                embedding=member_emb.tobytes(),
            )
            db.add(opt)
            opts.append(opt)
        await db.flush()

        await _reassign_to_active(
            db=db,
            opt_ids=[o.id for o in opts],
            opt_embeddings=[member_emb] * len(opts),
        )
        await db.flush()

        # Each opt should land in its own project's target.
        for opt in opts:
            await db.refresh(opt)

        assert opts[0].cluster_id == target_a.id
        assert opts[1].cluster_id == target_a.id
        assert opts[2].cluster_id == target_b.id
        assert opts[3].cluster_id == target_b.id
