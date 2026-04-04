"""Unit tests for Phase 0.5 — candidate evaluation lifecycle.

Tests cover:
- High-coherence candidates → promoted to active
- Low-coherence candidates → rejected, members reassigned to active cluster
- Zero-member candidates → archived immediately
- Rejected members go to active clusters, NOT to other candidates
- All candidates from same parent rejected → split_fully_reversed event logged
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import pytest_asyncio

from app.models import Optimization, PromptCluster
from app.services.taxonomy.warm_phases import phase_evaluate_candidates
from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_centroid(embeddings: list[np.ndarray]) -> bytes:
    """Return L2-normalised mean of embeddings as bytes."""
    stacked = np.stack(embeddings, axis=0)
    centroid = np.mean(stacked, axis=0).astype(np.float32)
    norm = np.linalg.norm(centroid)
    if norm > 1e-9:
        centroid = centroid / norm
    return centroid.tobytes()


def _random_unit(seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    v = rng.randn(EMBEDDING_DIM).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


# ---------------------------------------------------------------------------
# TestCandidatePromotion
# ---------------------------------------------------------------------------


class TestCandidatePromotion:
    @pytest.mark.asyncio
    async def test_high_coherence_candidate_promoted(self, db):
        """A candidate with tight embeddings (coherence > 0.30) is promoted to active."""
        # Create tight cluster of embeddings
        rng = np.random.RandomState(42)
        tight_embs = make_cluster_distribution("coding prompt", 4, spread=0.02, rng=rng)

        candidate = PromptCluster(
            label="Tight Candidate",
            state="candidate",
            domain="general",
            centroid_embedding=_make_centroid(tight_embs),
            member_count=4,
            color_hex="#a855f7",
        )
        db.add(candidate)
        await db.flush()

        for i, emb in enumerate(tight_embs):
            opt = Optimization(
                raw_prompt=f"tight prompt {i}",
                cluster_id=candidate.id,
                embedding=emb.tobytes(),
            )
            db.add(opt)
        await db.commit()

        result = await phase_evaluate_candidates(db)

        assert result["promoted"] >= 1
        assert result["rejected"] == 0

        await db.refresh(candidate)
        assert candidate.state == "active"
        assert candidate.coherence is not None
        assert candidate.coherence >= 0.30

    @pytest.mark.asyncio
    async def test_low_coherence_candidate_rejected(self, db):
        """A candidate with scattered embeddings (coherence < 0.30) is rejected and archived."""
        # Create a stable target cluster for reassignment
        active_embs = make_cluster_distribution("stable coding", 3, spread=0.05)
        active_cluster = PromptCluster(
            label="Stable Active",
            state="active",
            domain="general",
            centroid_embedding=_make_centroid(active_embs),
            member_count=3,
            color_hex="#6366f1",
        )
        db.add(active_cluster)
        await db.flush()

        # Create maximally scattered embeddings (orthogonal directions)
        scattered_embs = [_random_unit(seed=i * 17 + 100) for i in range(4)]

        candidate = PromptCluster(
            label="Scattered Candidate",
            state="candidate",
            domain="general",
            centroid_embedding=_make_centroid(scattered_embs),
            member_count=4,
            color_hex="#a855f7",
        )
        db.add(candidate)
        await db.flush()

        for i, emb in enumerate(scattered_embs):
            opt = Optimization(
                raw_prompt=f"scattered prompt {i}",
                cluster_id=candidate.id,
                embedding=emb.tobytes(),
            )
            db.add(opt)
        await db.commit()

        result = await phase_evaluate_candidates(db)

        assert result["rejected"] >= 1
        assert result["promoted"] == 0

        await db.refresh(candidate)
        assert candidate.state == "archived"

    @pytest.mark.asyncio
    async def test_zero_member_candidate_archived(self, db):
        """A candidate with 0 members is archived immediately without coherence computation."""
        candidate = PromptCluster(
            label="Empty Candidate",
            state="candidate",
            domain="general",
            centroid_embedding=_random_unit(1).tobytes(),
            member_count=0,
            color_hex="#a855f7",
        )
        db.add(candidate)
        await db.commit()

        result = await phase_evaluate_candidates(db)

        assert result["rejected"] >= 1
        assert result["promoted"] == 0

        await db.refresh(candidate)
        assert candidate.state == "archived"
        assert candidate.archived_at is not None


# ---------------------------------------------------------------------------
# TestCandidateReassignment
# ---------------------------------------------------------------------------


class TestCandidateReassignment:
    @pytest.mark.asyncio
    async def test_rejected_members_go_to_active_not_candidate(self, db):
        """Members of a rejected candidate are reassigned to active clusters, not to other candidates."""
        # Create a sibling candidate — reassignment must NOT land here
        sibling_embs = make_cluster_distribution("sibling topic", 3, spread=0.05)
        sibling_candidate = PromptCluster(
            label="Sibling Candidate",
            state="candidate",
            domain="general",
            centroid_embedding=_make_centroid(sibling_embs),
            member_count=3,
            color_hex="#a855f7",
        )
        db.add(sibling_candidate)

        # Create a stable active cluster close to the scattered candidate centroid
        active_embs = make_cluster_distribution("general coding", 5, spread=0.05)
        active_cluster = PromptCluster(
            label="Active Target",
            state="active",
            domain="general",
            centroid_embedding=_make_centroid(active_embs),
            member_count=5,
            color_hex="#6366f1",
        )
        db.add(active_cluster)
        await db.flush()

        # Add sibling candidate members
        for i, emb in enumerate(sibling_embs):
            opt = Optimization(
                raw_prompt=f"sibling prompt {i}",
                cluster_id=sibling_candidate.id,
                embedding=emb.tobytes(),
            )
            db.add(opt)

        # Create a scattered candidate (will be rejected)
        scattered_embs = [_random_unit(seed=i * 31 + 200) for i in range(3)]
        bad_candidate = PromptCluster(
            label="Scattered Bad Candidate",
            state="candidate",
            domain="general",
            centroid_embedding=_make_centroid(scattered_embs),
            member_count=3,
            color_hex="#a855f7",
        )
        db.add(bad_candidate)
        await db.flush()

        bad_opt_ids = []
        for i, emb in enumerate(scattered_embs):
            opt = Optimization(
                raw_prompt=f"bad candidate prompt {i}",
                cluster_id=bad_candidate.id,
                embedding=emb.tobytes(),
            )
            db.add(opt)
            await db.flush()
            bad_opt_ids.append(opt.id)

        await db.commit()

        result = await phase_evaluate_candidates(db)

        # At least the bad candidate should have been rejected (sibling may or may not be promoted)
        assert result["rejected"] >= 1

        await db.refresh(bad_candidate)
        assert bad_candidate.state == "archived"

        # Verify reassigned optimizations are NOT in any candidate cluster
        from sqlalchemy import select
        for opt_id in bad_opt_ids:
            opt = await db.get(Optimization, opt_id)
            if opt is None:
                continue
            if opt.cluster_id is not None:
                target = await db.get(PromptCluster, opt.cluster_id)
                assert target is not None
                assert target.state != "candidate", (
                    f"Opt {opt_id} was reassigned to candidate {target.id} "
                    f"({target.label}), expected active/mature/template"
                )


# ---------------------------------------------------------------------------
# TestSplitFullyReversed
# ---------------------------------------------------------------------------


class TestSplitFullyReversed:
    @pytest.mark.asyncio
    async def test_all_candidates_rejected_logs_event(self, db):
        """When all candidates from the same parent are rejected, split_fully_reversed is logged."""
        # Create parent cluster
        parent_embs = make_cluster_distribution("parent topic", 5, spread=0.05)
        parent = PromptCluster(
            label="Parent Cluster",
            state="active",
            domain="general",
            centroid_embedding=_make_centroid(parent_embs),
            member_count=5,
            color_hex="#6366f1",
        )
        db.add(parent)
        await db.flush()

        # Create a stable target for reassignment
        fallback_embs = make_cluster_distribution("fallback stable", 4, spread=0.04)
        fallback = PromptCluster(
            label="Fallback Active",
            state="active",
            domain="general",
            centroid_embedding=_make_centroid(fallback_embs),
            member_count=4,
            color_hex="#6366f1",
        )
        db.add(fallback)
        await db.flush()

        # Create two child candidates with low coherence (scattered)
        child_ids = []
        for child_idx in range(2):
            scattered = [_random_unit(seed=child_idx * 50 + i * 13 + 300) for i in range(3)]
            child = PromptCluster(
                label=f"Bad Child {child_idx}",
                state="candidate",
                domain="general",
                parent_id=parent.id,
                centroid_embedding=_make_centroid(scattered),
                member_count=3,
                color_hex="#a855f7",
            )
            db.add(child)
            await db.flush()
            child_ids.append(child.id)

            for i, emb in enumerate(scattered):
                opt = Optimization(
                    raw_prompt=f"child {child_idx} prompt {i}",
                    cluster_id=child.id,
                    embedding=emb.tobytes(),
                )
                db.add(opt)

        await db.commit()

        # Patch get_event_logger to capture log_decision calls
        logged_decisions: list[dict] = []

        class _FakeLogger:
            def log_decision(self, path, op, decision, context=None):
                logged_decisions.append({
                    "path": path, "op": op, "decision": decision,
                    "context": context or {},
                })

        fake_logger = _FakeLogger()

        with patch(
            "app.services.taxonomy.warm_phases.get_event_logger",
            return_value=fake_logger,
        ):
            result = await phase_evaluate_candidates(db)

        assert result["rejected"] >= 2
        assert result["splits_fully_reversed"] >= 1

        # Confirm the split_fully_reversed event was logged
        srf_events = [
            d for d in logged_decisions
            if d["decision"] == "split_fully_reversed"
        ]
        assert len(srf_events) >= 1
        assert srf_events[0]["context"]["parent_id"] == parent.id
        assert srf_events[0]["context"]["rejected_count"] >= 2
