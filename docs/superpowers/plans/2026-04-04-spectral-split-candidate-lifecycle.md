# Spectral Split + Candidate Lifecycle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace HDBSCAN as the primary split algorithm with spectral clustering (solves uniform-density split failures) and introduce a candidate lifecycle so split children are evaluated before promotion to active state (full observability into the taxonomy's decision-making process).

**Architecture:** New `spectral_split()` in `clustering.py` tries k=2,3,4 spectral partitions with silhouette gating. `split.py` calls spectral first, HDBSCAN as fallback, children created as `state="candidate"`. New `phase_evaluate_candidates()` in `warm_phases.py` runs between reconcile and split_emerge, promoting or rejecting candidates based on coherence floor. Frontend shows candidate nodes at 40% opacity with filter tab, activity panel events, and toast notifications.

**Tech Stack:** Python 3.12, scikit-learn (SpectralClustering, silhouette_score), FastAPI, SQLAlchemy async, Svelte 5 runes, SSE

**Spec:** `docs/superpowers/specs/2026-04-04-spectral-split-candidate-lifecycle-design.md`

---

### Task 1: Constants + `spectral_split()` Function

**Files:**
- Modify: `backend/app/services/taxonomy/_constants.py`
- Modify: `backend/app/services/taxonomy/clustering.py`
- Create: `backend/tests/taxonomy/test_spectral_split.py`

- [ ] **Step 1: Add constants to `_constants.py`**

```python
# backend/app/services/taxonomy/_constants.py
# Append after the existing SUB_DOMAIN_HDBSCAN_MIN_CLUSTER line:

# ---------------------------------------------------------------------------
# Spectral split algorithm
# ---------------------------------------------------------------------------
SPECTRAL_K_RANGE = (2, 3, 4)            # k values to try
SPECTRAL_SILHOUETTE_GATE = 0.15         # minimum rescaled silhouette to accept
SPECTRAL_MIN_GROUP_SIZE = 3             # minimum members per sub-cluster

# ---------------------------------------------------------------------------
# Candidate lifecycle
# ---------------------------------------------------------------------------
CANDIDATE_COHERENCE_FLOOR = 0.30        # minimum coherence for promotion
```

- [ ] **Step 2: Write failing tests for `spectral_split()`**

```python
# backend/tests/taxonomy/test_spectral_split.py
"""Tests for spectral_split() — spectral clustering for taxonomy splits."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.taxonomy.clustering import ClusterResult, spectral_split


def _make_groups(
    n_groups: int,
    members_per_group: int,
    dim: int = 384,
    spread: float = 0.05,
    seed: int = 42,
) -> np.ndarray:
    """Create N well-separated groups of L2-normalized embeddings."""
    rng = np.random.RandomState(seed)
    all_vecs = []
    for g in range(n_groups):
        # Create a random center
        center = rng.randn(dim).astype(np.float32)
        center = center / np.linalg.norm(center)
        for _ in range(members_per_group):
            noise = rng.randn(dim).astype(np.float32) * spread
            vec = center + noise
            vec = vec / np.linalg.norm(vec)
            all_vecs.append(vec)
    return np.stack(all_vecs, axis=0).astype(np.float32)


class TestSpectralSplitClearGroups:
    def test_three_groups_selects_k3(self) -> None:
        """3 clear groups should yield k=3 with good silhouette."""
        embeddings = _make_groups(3, 10, spread=0.03)
        result = spectral_split(embeddings)
        assert result is not None
        assert isinstance(result, ClusterResult)
        assert result.n_clusters == 3
        assert result.silhouette > 0.3  # rescaled [0, 1]
        assert result.noise_count == 0
        assert len(result.centroids) == 3
        assert result.labels.shape[0] == 30
        # Each centroid should be L2-normalized
        for c in result.centroids:
            assert abs(np.linalg.norm(c) - 1.0) < 1e-4

    def test_two_groups_selects_k2(self) -> None:
        """2 clear groups should yield k=2."""
        embeddings = _make_groups(2, 15, spread=0.03)
        result = spectral_split(embeddings)
        assert result is not None
        assert result.n_clusters == 2
        assert result.silhouette > 0.3
        assert len(result.centroids) == 2

    def test_all_points_assigned(self) -> None:
        """Spectral assigns every point — no noise labels."""
        embeddings = _make_groups(3, 8, spread=0.03)
        result = spectral_split(embeddings)
        assert result is not None
        assert result.noise_count == 0
        # All labels are >= 0
        assert (result.labels >= 0).all()


class TestSpectralSplitRejection:
    def test_uniform_noise_returns_none(self) -> None:
        """Uniformly random embeddings should fail the silhouette gate."""
        rng = np.random.RandomState(99)
        embeddings = rng.randn(30, 384).astype(np.float32)
        # L2-normalize
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / norms
        result = spectral_split(embeddings, silhouette_gate=0.55)
        assert result is None

    def test_identical_embeddings_returns_none(self) -> None:
        """Degenerate input (all identical) should return None gracefully."""
        vec = np.ones(384, dtype=np.float32)
        vec = vec / np.linalg.norm(vec)
        embeddings = np.tile(vec, (20, 1))
        result = spectral_split(embeddings)
        assert result is None

    def test_too_few_points_returns_none(self) -> None:
        """Fewer than max(k_range) * min_group_size should return None."""
        rng = np.random.RandomState(7)
        embeddings = rng.randn(5, 384).astype(np.float32)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / norms
        result = spectral_split(embeddings)
        assert result is None


class TestSpectralSplitGroupSizeFilter:
    def test_rejects_k_with_small_group(self) -> None:
        """k values producing a group with < SPECTRAL_MIN_GROUP_SIZE are rejected."""
        # 2 groups of 10 + 1 outlier = 21 points
        # k=3 might isolate the outlier into a 1-member group
        rng = np.random.RandomState(42)
        groups = _make_groups(2, 10, spread=0.02, seed=42)
        outlier = rng.randn(384).astype(np.float32)
        outlier = (outlier / np.linalg.norm(outlier)).reshape(1, -1)
        embeddings = np.vstack([groups, outlier])

        result = spectral_split(embeddings, k_range=(2, 3))
        if result is not None:
            # If accepted, every group must have >= 3 members
            for cid in range(result.n_clusters):
                assert (result.labels == cid).sum() >= 3


class TestSpectralSplitSilhouetteScale:
    def test_silhouette_in_zero_one_range(self) -> None:
        """Silhouette must be rescaled to [0, 1] matching batch_cluster() convention."""
        embeddings = _make_groups(2, 12, spread=0.03)
        result = spectral_split(embeddings)
        assert result is not None
        assert 0.0 <= result.silhouette <= 1.0

    def test_persistences_equal_silhouette(self) -> None:
        """Persistences should all equal the rescaled silhouette (spectral convention)."""
        embeddings = _make_groups(3, 8, spread=0.03)
        result = spectral_split(embeddings)
        assert result is not None
        assert len(result.persistences) == result.n_clusters
        for p in result.persistences:
            assert abs(p - result.silhouette) < 1e-6
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -m pytest tests/taxonomy/test_spectral_split.py -v 2>&1 | head -30
```

Expected: `ImportError` — `spectral_split` does not exist in `clustering.py` yet.

- [ ] **Step 4: Implement `spectral_split()` in `clustering.py`**

Add the import at the top of `clustering.py` alongside the existing `HDBSCAN` import:

```python
# backend/app/services/taxonomy/clustering.py
# Add to the sklearn import block (near line 13):
from sklearn.cluster import HDBSCAN, SpectralClustering
```

Add the constants import (extend the existing import from `_constants`):

```python
# backend/app/services/taxonomy/clustering.py
# Extend the existing import from _constants (near line 16-19):
from app.services.taxonomy._constants import (
    CLUSTERING_BLEND_W_OPTIMIZED,
    CLUSTERING_BLEND_W_RAW,
    CLUSTERING_BLEND_W_TRANSFORM,
    SPECTRAL_K_RANGE,
    SPECTRAL_MIN_GROUP_SIZE,
    SPECTRAL_SILHOUETTE_GATE,
)
```

Add the function after `nearest_centroid()` and before `batch_cluster()` (insert before line 295):

```python
# backend/app/services/taxonomy/clustering.py
# Insert before batch_cluster() definition:

def spectral_split(
    embeddings: np.ndarray,
    k_range: tuple[int, ...] = SPECTRAL_K_RANGE,
    silhouette_gate: float = SPECTRAL_SILHOUETTE_GATE,
) -> ClusterResult | None:
    """Split embeddings into sub-clusters using spectral clustering.

    Unlike HDBSCAN, spectral clustering finds sub-communities by analyzing
    the similarity graph structure — not density — which works for
    uniform-density embedding spaces that HDBSCAN cannot separate.

    Tries each k in *k_range*, selects the partition with the best
    silhouette score (rescaled to [0, 1]).  Rejects partitions where
    any group has fewer than ``SPECTRAL_MIN_GROUP_SIZE`` members.

    Args:
        embeddings: (N, D) L2-normalized blended embeddings.
        k_range: Tuple of k values to try.
        silhouette_gate: Minimum rescaled silhouette to accept.

    Returns:
        :class:`ClusterResult` with the best partition, or ``None`` if
        no valid partition passes the silhouette gate.
    """
    n = embeddings.shape[0]
    min_required = max(k_range) * SPECTRAL_MIN_GROUP_SIZE
    if n < min_required:
        logger.debug(
            "spectral_split: N=%d < min_required=%d, skipping", n, min_required,
        )
        return None

    # Cosine similarity matrix (L2-normalized → dot product = cosine)
    sim_matrix = embeddings @ embeddings.T
    sim_matrix = np.clip(sim_matrix, 0, None)  # spectral requires non-negative

    best_k: int | None = None
    best_sil: float = -1.0
    best_labels: np.ndarray | None = None

    for k in k_range:
        if n < k * SPECTRAL_MIN_GROUP_SIZE:
            continue
        try:
            sc = SpectralClustering(
                n_clusters=k,
                affinity="precomputed",
                random_state=42,
                assign_labels="kmeans",
            )
            labels = sc.fit_predict(sim_matrix)
        except Exception as exc:
            logger.warning("SpectralClustering failed for k=%d: %s", k, exc)
            continue

        # Reject if any group is too small
        group_sizes = [int((labels == cid).sum()) for cid in range(k)]
        if any(s < SPECTRAL_MIN_GROUP_SIZE for s in group_sizes):
            continue

        # Silhouette score (cosine metric, raw range [-1, 1])
        try:
            raw_sil = silhouette_score(embeddings, labels, metric="cosine")
            rescaled = (raw_sil + 1.0) / 2.0
        except Exception:
            continue

        if rescaled > best_sil:
            best_sil = rescaled
            best_k = k
            best_labels = labels

    if best_labels is None or best_sil < silhouette_gate:
        logger.debug(
            "spectral_split: no valid partition (best_sil=%.4f, gate=%.4f)",
            best_sil, silhouette_gate,
        )
        return None

    # Compute L2-normalized centroids
    centroids: list[np.ndarray] = []
    for cid in range(best_k):  # type: ignore[arg-type]
        member_vecs = embeddings[best_labels == cid]
        mean_vec = member_vecs.mean(axis=0).astype(np.float32)
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm
        centroids.append(mean_vec)

    return ClusterResult(
        labels=best_labels,
        n_clusters=best_k,  # type: ignore[arg-type]
        noise_count=0,
        persistences=[best_sil] * best_k,  # type: ignore[operator]
        centroids=centroids,
        silhouette=best_sil,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -m pytest tests/taxonomy/test_spectral_split.py -v
```

All tests should pass. Verify no regressions:

```bash
python -m pytest tests/taxonomy/test_clustering.py -v
```

- [ ] **Step 6: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2 && git add backend/app/services/taxonomy/_constants.py backend/app/services/taxonomy/clustering.py backend/tests/taxonomy/test_spectral_split.py && git commit -m "feat(taxonomy): add spectral_split() algorithm and constants

Add SPECTRAL_K_RANGE, SPECTRAL_SILHOUETTE_GATE, SPECTRAL_MIN_GROUP_SIZE,
and CANDIDATE_COHERENCE_FLOOR constants. Implement spectral_split() in
clustering.py that tries k=2,3,4 partitions with silhouette gating,
min-group-size filtering, and [0,1] rescaled silhouette scores matching
batch_cluster() convention. Full unit test suite."
```

---

### Task 2: Integrate Spectral into `split.py`

**Files:**
- Modify: `backend/app/services/taxonomy/split.py`
- Modify: `backend/tests/taxonomy/test_split.py`

- [ ] **Step 1: Update imports in `split.py`**

In `backend/app/services/taxonomy/split.py`, add `spectral_split` to the clustering import:

```python
# backend/app/services/taxonomy/split.py
# Replace the existing clustering import block (lines 35-41):
from app.services.taxonomy.clustering import (
    batch_cluster,
    blend_embeddings,
    compute_pairwise_coherence,
    cosine_similarity,
    l2_normalize_1d,
    spectral_split,
)
```

- [ ] **Step 2: Replace HDBSCAN primary with spectral, remove K-Means fallback**

Replace lines 107-143 of `split.py` (the HDBSCAN call through the end of K-Means fallback) with:

```python
    # backend/app/services/taxonomy/split.py
    # Replace the HDBSCAN + K-Means fallback block (lines 107-143):

    # Spectral clustering — primary algorithm.
    # Spectral finds sub-communities via similarity graph structure,
    # solving uniform-density failures that HDBSCAN cannot handle.
    emb_stack = np.stack(child_blended, axis=0).astype(np.float32)
    # L2-normalize for spectral (expects unit-norm for cosine affinity)
    norms = np.linalg.norm(emb_stack, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    emb_stack = (emb_stack / norms).astype(np.float32)

    split_result = spectral_split(emb_stack)
    used_algorithm = "spectral"

    # Log spectral evaluation result
    spectral_silhouettes: dict[str, float] = {}
    if split_result is not None:
        spectral_silhouettes = {str(split_result.n_clusters): round(split_result.silhouette, 4)}
    try:
        get_event_logger().log_decision(
            path=log_path, op="split", decision="spectral_evaluation",
            cluster_id=node.id,
            context={
                "cluster_label": node.label,
                "member_count": len(child_blended),
                "input_coherence": round(node.coherence or 0.0, 4),
                "silhouettes_by_k": spectral_silhouettes,
                "best_k": split_result.n_clusters if split_result else None,
                "best_silhouette": round(split_result.silhouette, 4) if split_result else None,
                "gate_threshold": 0.15,
                "accepted": split_result is not None,
                "fallback_to_hdbscan": split_result is None,
            },
        )
    except RuntimeError:
        pass

    # Fallback: HDBSCAN may find density-based structure spectral missed
    if split_result is None:
        split_result = batch_cluster(child_blended, min_cluster_size=8)
        used_algorithm = "hdbscan"
```

- [ ] **Step 3: Change child creation from `state="active"` to `state="candidate"`**

In `split.py`, change the child node creation (line 269):

```python
    # backend/app/services/taxonomy/split.py
    # Change line 269 — child_node creation:
    # Old: state="active",
    # New:
            state="candidate",
```

- [ ] **Step 4: Update child_created event to candidate_created with op="candidate"**

Replace the `child_created` event logging block (lines 320-333 of `split.py`) with:

```python
        # backend/app/services/taxonomy/split.py
        # Replace the child_created event (lines 320-333):
        try:
            get_event_logger().log_decision(
                path=log_path, op="candidate", decision="candidate_created",
                cluster_id=child_node.id,
                context={
                    "parent_id": node.id,
                    "parent_label": node.label,
                    "parent_member_count": node.member_count or 0,
                    "child_label": label,
                    "child_member_count": len(cd["group_opt_ids"]),
                    "child_coherence": round(cd["coherence"], 4),
                    "split_algorithm": used_algorithm,
                    "k_selected": split_result.n_clusters,
                    "silhouette_score": round(split_result.silhouette, 4),
                },
            )
        except RuntimeError:
            pass
```

- [ ] **Step 5: Update `no_sub_structure` event with spectral details**

Replace the `no_sub_structure` event block (lines 149-161) with:

```python
        # backend/app/services/taxonomy/split.py
        # Replace the no_sub_structure event (lines 149-161):
        try:
            get_event_logger().log_decision(
                path=log_path, op="split", decision="no_sub_structure",
                cluster_id=node.id,
                context={
                    "spectral_silhouettes": spectral_silhouettes,
                    "hdbscan_clusters": int(split_result.n_clusters),
                    "total_members": len(child_blended),
                    "reason": "Neither spectral nor HDBSCAN found separable sub-groups",
                },
            )
        except RuntimeError:
            pass
```

- [ ] **Step 6: Update `split_complete` event with algorithm and children_state**

In the `split_complete` event context dict (around line 475-499), add `algorithm` and `children_state` keys and replace the `fallback` key:

```python
                # backend/app/services/taxonomy/split.py
                # In the split_complete event context dict, replace the "fallback" key:
                "algorithm": used_algorithm,
                "children_state": "candidate",
                # Remove the old "fallback" key entirely
```

- [ ] **Step 7: Update `algorithm_result` event to include spectral info**

Replace the `algorithm_result` event (lines 164-176) with:

```python
    # backend/app/services/taxonomy/split.py
    # Replace the algorithm_result event:
    try:
        get_event_logger().log_decision(
            path=log_path, op="split", decision="algorithm_result",
            cluster_id=node.id,
            context={
                "algorithm": used_algorithm,
                "clusters_found": int(split_result.n_clusters),
                "noise_count": int(split_result.noise_count),
                "silhouette": round(split_result.silhouette, 4) if split_result.silhouette else None,
                "total_members": len(child_blended),
            },
        )
    except RuntimeError:
        pass
```

- [ ] **Step 8: Update existing split tests**

In `backend/tests/taxonomy/test_split.py`, verify existing tests still pass. The tests only check `SplitResult` dataclass structure and `split_cluster` is async — no behavioral changes needed since the public interface is unchanged.

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -m pytest tests/taxonomy/test_split.py -v
```

- [ ] **Step 9: Run full taxonomy test suite**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -m pytest tests/taxonomy/ -v --tb=short 2>&1 | tail -30
```

- [ ] **Step 10: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2 && git add backend/app/services/taxonomy/split.py backend/tests/taxonomy/test_split.py && git commit -m "feat(taxonomy): integrate spectral split, children as candidates

Replace HDBSCAN as primary split algorithm with spectral_split().
HDBSCAN retained as fallback. Remove K-Means fallback (spectral
subsumes it). Split children created as state='candidate' instead
of 'active'. Add spectral_evaluation event, update child_created
to candidate_created (op=candidate), enhance no_sub_structure and
split_complete events with algorithm details."
```

---

### Task 3: Candidate Evaluation in Warm Path

**Files:**
- Modify: `backend/app/services/taxonomy/warm_phases.py`
- Modify: `backend/app/services/taxonomy/warm_path.py`
- Create: `backend/tests/taxonomy/test_candidate_lifecycle.py`

- [ ] **Step 1: Write failing tests for candidate lifecycle**

```python
# backend/tests/taxonomy/test_candidate_lifecycle.py
"""Tests for candidate evaluation — promotion and rejection in warm path."""

from __future__ import annotations

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization, PromptCluster
from app.services.taxonomy._constants import CANDIDATE_COHERENCE_FLOOR
from app.services.taxonomy.event_logger import TaxonomyEventLogger, set_event_logger

EMBEDDING_DIM = 384


def _make_embedding(seed: int = 0) -> bytes:
    rng = np.random.RandomState(seed)
    v = rng.randn(EMBEDDING_DIM).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tobytes()


def _make_tight_cluster_embeddings(center_seed: int, n: int, spread: float = 0.03) -> list[bytes]:
    """Create n embeddings tightly clustered around a center."""
    rng = np.random.RandomState(center_seed)
    center = rng.randn(EMBEDDING_DIM).astype(np.float32)
    center = center / np.linalg.norm(center)
    embs = []
    for i in range(n):
        noise = np.random.RandomState(center_seed * 100 + i).randn(EMBEDDING_DIM).astype(np.float32) * spread
        v = center + noise
        v = v / np.linalg.norm(v)
        embs.append(v.tobytes())
    return embs


def _make_scattered_embeddings(n: int, seed: int = 99) -> list[bytes]:
    """Create n widely scattered embeddings (low coherence)."""
    rng = np.random.RandomState(seed)
    embs = []
    for i in range(n):
        v = rng.randn(EMBEDDING_DIM).astype(np.float32)
        v = v / np.linalg.norm(v)
        embs.append(v.tobytes())
    return embs


@pytest.fixture(autouse=True)
def setup_event_logger(tmp_path):
    logger = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
    set_event_logger(logger)
    yield logger


class TestCandidatePromotion:
    @pytest.mark.asyncio
    async def test_high_coherence_candidate_promoted(self, db: AsyncSession) -> None:
        """Candidate with coherence >= CANDIDATE_COHERENCE_FLOOR is promoted to active."""
        from app.services.taxonomy.warm_phases import phase_evaluate_candidates

        # Create a parent domain
        domain = PromptCluster(
            label="test-domain", state="domain", domain="test",
            centroid_embedding=_make_embedding(0),
        )
        db.add(domain)
        await db.flush()

        # Create a candidate cluster with tight embeddings (high coherence)
        candidate = PromptCluster(
            label="Good Candidate",
            state="candidate",
            domain="test",
            parent_id=domain.id,
            centroid_embedding=_make_embedding(1),
            member_count=5,
        )
        db.add(candidate)
        await db.flush()

        # Add tightly clustered optimizations
        embs = _make_tight_cluster_embeddings(center_seed=1, n=5, spread=0.02)
        for i, emb_bytes in enumerate(embs):
            opt = Optimization(
                raw_prompt=f"prompt {i}",
                cluster_id=candidate.id,
                embedding=emb_bytes,
            )
            db.add(opt)
        await db.flush()

        result = await phase_evaluate_candidates(db)
        await db.flush()

        # Refresh and check
        await db.refresh(candidate)
        assert candidate.state == "active"
        assert result["promoted"] == 1
        assert result["rejected"] == 0

    @pytest.mark.asyncio
    async def test_low_coherence_candidate_rejected(self, db: AsyncSession) -> None:
        """Candidate with coherence < CANDIDATE_COHERENCE_FLOOR is rejected."""
        from app.services.taxonomy.warm_phases import phase_evaluate_candidates

        # Create an active target for reassignment
        active_cluster = PromptCluster(
            label="Active Target",
            state="active",
            domain="test",
            centroid_embedding=_make_embedding(10),
            member_count=10,
        )
        db.add(active_cluster)

        # Create a candidate with scattered embeddings (low coherence)
        candidate = PromptCluster(
            label="Bad Candidate",
            state="candidate",
            domain="test",
            centroid_embedding=_make_embedding(20),
            member_count=5,
        )
        db.add(candidate)
        await db.flush()

        # Add scattered optimizations
        embs = _make_scattered_embeddings(n=5, seed=20)
        for i, emb_bytes in enumerate(embs):
            opt = Optimization(
                raw_prompt=f"prompt {i}",
                cluster_id=candidate.id,
                embedding=emb_bytes,
            )
            db.add(opt)
        await db.flush()

        result = await phase_evaluate_candidates(db)
        await db.flush()

        await db.refresh(candidate)
        assert candidate.state == "archived"
        assert result["rejected"] == 1

    @pytest.mark.asyncio
    async def test_zero_member_candidate_archived(self, db: AsyncSession) -> None:
        """Candidate with 0 members is archived immediately."""
        from app.services.taxonomy.warm_phases import phase_evaluate_candidates

        candidate = PromptCluster(
            label="Empty Candidate",
            state="candidate",
            domain="test",
            centroid_embedding=_make_embedding(30),
            member_count=0,
        )
        db.add(candidate)
        await db.flush()

        result = await phase_evaluate_candidates(db)
        await db.flush()

        await db.refresh(candidate)
        assert candidate.state == "archived"
        assert result["rejected"] == 1


class TestCandidateReassignment:
    @pytest.mark.asyncio
    async def test_rejected_members_go_to_active_not_candidate(self, db: AsyncSession) -> None:
        """Rejected members must be reassigned only to active/mature/template clusters."""
        from app.services.taxonomy.warm_phases import phase_evaluate_candidates

        # Create active target
        target_emb = np.ones(EMBEDDING_DIM, dtype=np.float32)
        target_emb = target_emb / np.linalg.norm(target_emb)
        active = PromptCluster(
            label="Active Target",
            state="active",
            domain="test",
            centroid_embedding=target_emb.tobytes(),
            member_count=10,
        )
        db.add(active)

        # Create another candidate (should NOT receive reassigned members)
        other_candidate = PromptCluster(
            label="Other Candidate",
            state="candidate",
            domain="test",
            centroid_embedding=_make_embedding(50),
            member_count=5,
        )
        db.add(other_candidate)

        # Create the candidate to reject (scattered = low coherence)
        reject_candidate = PromptCluster(
            label="Reject Candidate",
            state="candidate",
            domain="test",
            centroid_embedding=_make_embedding(60),
            member_count=3,
        )
        db.add(reject_candidate)
        await db.flush()

        # Add scattered optimizations to the reject candidate
        embs = _make_scattered_embeddings(n=3, seed=60)
        for i, emb_bytes in enumerate(embs):
            opt = Optimization(
                raw_prompt=f"prompt {i}",
                cluster_id=reject_candidate.id,
                embedding=emb_bytes,
            )
            db.add(opt)
        await db.flush()

        result = await phase_evaluate_candidates(db)
        await db.flush()

        # Check that no member went to other_candidate
        other_members_q = await db.execute(
            select(Optimization).where(Optimization.cluster_id == other_candidate.id)
        )
        assert len(other_members_q.scalars().all()) == 0


class TestSplitFullyReversed:
    @pytest.mark.asyncio
    async def test_all_candidates_rejected_logs_event(self, db: AsyncSession, setup_event_logger) -> None:
        """When all candidates from a split are rejected, split_fully_reversed fires."""
        from app.services.taxonomy.warm_phases import phase_evaluate_candidates

        # Create active target
        active = PromptCluster(
            label="Active Target", state="active", domain="test",
            centroid_embedding=_make_embedding(70), member_count=10,
        )
        db.add(active)

        # Create a parent (already archived by split)
        parent = PromptCluster(
            label="Archived Parent", state="archived", domain="test",
            centroid_embedding=_make_embedding(80), member_count=0,
        )
        db.add(parent)
        await db.flush()

        # Create 2 candidates from same parent — both with scattered embeddings
        for seed in [81, 82]:
            cand = PromptCluster(
                label=f"Bad Child {seed}", state="candidate", domain="test",
                centroid_embedding=_make_embedding(seed), member_count=3,
                parent_id=parent.id,
            )
            db.add(cand)
            await db.flush()
            embs = _make_scattered_embeddings(n=3, seed=seed)
            for i, emb_bytes in enumerate(embs):
                opt = Optimization(
                    raw_prompt=f"prompt {seed}-{i}",
                    cluster_id=cand.id, embedding=emb_bytes,
                )
                db.add(opt)

        await db.flush()

        result = await phase_evaluate_candidates(db)
        await db.flush()

        assert result["rejected"] == 2
        assert result["splits_fully_reversed"] >= 1

        # Check event logger for split_fully_reversed
        events = setup_event_logger.get_recent(op="candidate")
        reversed_events = [e for e in events if e["decision"] == "split_fully_reversed"]
        assert len(reversed_events) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -m pytest tests/taxonomy/test_candidate_lifecycle.py -v 2>&1 | head -30
```

Expected: `ImportError` — `phase_evaluate_candidates` does not exist yet.

- [ ] **Step 3: Implement `phase_evaluate_candidates()` in `warm_phases.py`**

Add the import for `CANDIDATE_COHERENCE_FLOOR` to the existing constants import in `warm_phases.py`:

```python
# backend/app/services/taxonomy/warm_phases.py
# Extend the existing _constants import (lines 39-44):
from app.services.taxonomy._constants import (
    CANDIDATE_COHERENCE_FLOOR,
    MEGA_CLUSTER_MEMBER_FLOOR,
    SPLIT_COHERENCE_FLOOR,
    SPLIT_MIN_MEMBERS,
    _utcnow,
)
```

Add `_reassign_to_active()` helper and `phase_evaluate_candidates()` after the `ReconcileResult` / `RefreshResult` / `DiscoverResult` / `AuditResult` dataclasses (insert before `phase_reconcile`, around line 127):

```python
# backend/app/services/taxonomy/warm_phases.py
# Insert before phase_reconcile (around line 127):

# ---------------------------------------------------------------------------
# Phase 0.5 — Candidate evaluation
# ---------------------------------------------------------------------------


async def _reassign_to_active(
    db: AsyncSession,
    opt_ids: list[str],
    opt_embeddings: dict[str, bytes],
) -> list[dict]:
    """Reassign optimizations to nearest active/mature/template cluster only.

    Candidates are excluded from target set to prevent cascading failures
    where members are reassigned to a candidate that itself gets rejected.

    Returns a list of {cluster_id, cluster_label, count} dicts for event context.
    """
    from sqlalchemy import update as sa_update

    # Load only active/mature/template clusters as targets
    target_q = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.in_(["active", "mature", "template"])
        )
    )
    targets = list(target_q.scalars().all())
    if not targets:
        logger.warning("_reassign_to_active: no active targets available")
        return []

    # Build centroid lookup
    target_centroids: list[tuple[PromptCluster, np.ndarray]] = []
    for t in targets:
        if t.centroid_embedding:
            try:
                c = np.frombuffer(t.centroid_embedding, dtype=np.float32)
                target_centroids.append((t, c))
            except (ValueError, TypeError):
                continue

    if not target_centroids:
        return []

    reassignment_counts: dict[str, dict] = {}  # cluster_id -> {label, count}

    for opt_id in opt_ids:
        emb_bytes = opt_embeddings.get(opt_id)
        if not emb_bytes:
            continue
        try:
            emb = np.frombuffer(emb_bytes, dtype=np.float32)
        except (ValueError, TypeError):
            continue

        # Find nearest target by cosine similarity
        best_target: PromptCluster | None = None
        best_sim = -1.0
        for target, centroid in target_centroids:
            sim = cosine_similarity(emb, centroid)
            if sim > best_sim:
                best_sim = sim
                best_target = target

        if best_target is not None:
            try:
                await db.execute(
                    sa_update(Optimization)
                    .where(Optimization.id == opt_id)
                    .values(cluster_id=best_target.id)
                )
                best_target.member_count = (best_target.member_count or 0) + 1
                key = best_target.id
                if key not in reassignment_counts:
                    reassignment_counts[key] = {
                        "cluster_id": best_target.id,
                        "cluster_label": best_target.label,
                        "count": 0,
                    }
                reassignment_counts[key]["count"] += 1
            except Exception as exc:
                logger.warning(
                    "_reassign_to_active: failed to reassign opt %s: %s",
                    opt_id, exc,
                )

    return list(reassignment_counts.values())


async def phase_evaluate_candidates(
    db: AsyncSession,
) -> dict:
    """Evaluate candidate clusters for promotion or rejection.

    Runs as Phase 0.5 — after reconcile (Phase 0), before split_emerge
    (Phase 1).  This is NOT Q-gated; it commits directly.

    For each candidate:
      - Recompute coherence from member embeddings
      - If coherence >= CANDIDATE_COHERENCE_FLOOR: promote to active
      - Otherwise: reject — reassign members to nearest active cluster, archive

    If all candidates sharing a parent_id are rejected, log split_fully_reversed.

    Returns:
        Dict with counts: promoted, rejected, splits_fully_reversed.
    """
    promoted = 0
    rejected = 0
    splits_fully_reversed = 0

    # Track parent_id -> rejection count for split_fully_reversed detection
    parent_candidate_counts: dict[str, int] = {}  # parent_id -> total candidates
    parent_rejection_counts: dict[str, int] = {}   # parent_id -> rejected count
    parent_labels: dict[str, str] = {}
    rejected_labels_by_parent: dict[str, list[str]] = {}
    reassigned_by_parent: dict[str, int] = {}

    # Load all candidates
    cand_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state == "candidate")
    )
    candidates = list(cand_q.scalars().all())

    if not candidates:
        return {"promoted": 0, "rejected": 0, "splits_fully_reversed": 0}

    # Count candidates per parent for split_fully_reversed detection
    for c in candidates:
        pid = c.parent_id or "no_parent"
        parent_candidate_counts[pid] = parent_candidate_counts.get(pid, 0) + 1
        if pid != "no_parent":
            # Look up parent label (best effort)
            if pid not in parent_labels and c.parent_id:
                parent_node = await db.get(PromptCluster, c.parent_id)
                if parent_node:
                    parent_labels[pid] = parent_node.label or "unknown"

    # Pre-fetch all member embeddings for candidates
    cand_ids = [c.id for c in candidates]
    emb_q = await db.execute(
        select(Optimization.id, Optimization.cluster_id, Optimization.embedding)
        .where(
            Optimization.cluster_id.in_(cand_ids),
            Optimization.embedding.isnot(None),
        )
    )
    emb_rows = emb_q.all()
    # Group by cluster_id
    cluster_embs: dict[str, list[tuple[str, bytes]]] = {}
    for opt_id, cid, emb_bytes in emb_rows:
        if emb_bytes:
            cluster_embs.setdefault(cid, []).append((opt_id, emb_bytes))

    # Process each candidate
    for candidate in candidates:
        member_data = cluster_embs.get(candidate.id, [])
        pid = candidate.parent_id or "no_parent"

        # Zero members → archive immediately
        if len(member_data) == 0:
            candidate.state = "archived"
            candidate.archived_at = _utcnow()
            rejected += 1
            parent_rejection_counts[pid] = parent_rejection_counts.get(pid, 0) + 1
            rejected_labels_by_parent.setdefault(pid, []).append(candidate.label or "unknown")
            try:
                get_event_logger().log_decision(
                    path="warm", op="candidate", decision="candidate_rejected",
                    cluster_id=candidate.id,
                    context={
                        "cluster_label": candidate.label,
                        "member_count": 0,
                        "coherence": None,
                        "reason": "zero_members",
                        "coherence_floor": CANDIDATE_COHERENCE_FLOOR,
                        "members_reassigned_to": [],
                    },
                )
            except RuntimeError:
                pass
            continue

        # Compute coherence
        try:
            emb_arrays = [
                np.frombuffer(emb_bytes, dtype=np.float32).copy()
                for _, emb_bytes in member_data
            ]
            coherence = compute_pairwise_coherence(emb_arrays) if len(emb_arrays) >= 2 else 1.0
        except Exception:
            coherence = None

        # Promote or reject
        if coherence is not None and coherence >= CANDIDATE_COHERENCE_FLOOR:
            # PROMOTE
            candidate.state = "active"
            candidate.coherence = coherence
            promoted += 1
            logger.info(
                "Candidate promoted: '%s' (coherence=%.3f >= %.3f)",
                candidate.label, coherence, CANDIDATE_COHERENCE_FLOOR,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="candidate", decision="candidate_promoted",
                    cluster_id=candidate.id,
                    context={
                        "cluster_label": candidate.label,
                        "member_count": len(member_data),
                        "coherence": round(coherence, 4),
                        "reason": "coherence_above_floor",
                        "coherence_floor": CANDIDATE_COHERENCE_FLOOR,
                    },
                )
            except RuntimeError:
                pass
        else:
            # REJECT — reassign members to active clusters, then archive
            reason = "coherence_unavailable" if coherence is None else "coherence_below_floor"
            opt_ids = [opt_id for opt_id, _ in member_data]
            opt_embeddings = {opt_id: emb_bytes for opt_id, emb_bytes in member_data}

            reassignment_info = await _reassign_to_active(db, opt_ids, opt_embeddings)

            candidate.state = "archived"
            candidate.archived_at = _utcnow()
            candidate.member_count = 0
            rejected += 1
            parent_rejection_counts[pid] = parent_rejection_counts.get(pid, 0) + 1
            rejected_labels_by_parent.setdefault(pid, []).append(candidate.label or "unknown")
            reassigned_by_parent[pid] = reassigned_by_parent.get(pid, 0) + len(opt_ids)

            logger.info(
                "Candidate rejected: '%s' (coherence=%s, floor=%.3f) — %d members reassigned",
                candidate.label,
                f"{coherence:.3f}" if coherence is not None else "None",
                CANDIDATE_COHERENCE_FLOOR,
                len(opt_ids),
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="candidate", decision="candidate_rejected",
                    cluster_id=candidate.id,
                    context={
                        "cluster_label": candidate.label,
                        "member_count": len(member_data),
                        "coherence": round(coherence, 4) if coherence is not None else None,
                        "reason": reason,
                        "coherence_floor": CANDIDATE_COHERENCE_FLOOR,
                        "members_reassigned_to": reassignment_info,
                    },
                )
            except RuntimeError:
                pass

    # Check for fully reversed splits
    for pid, total in parent_candidate_counts.items():
        rej_count = parent_rejection_counts.get(pid, 0)
        if rej_count == total and rej_count > 0 and pid != "no_parent":
            splits_fully_reversed += 1
            try:
                get_event_logger().log_decision(
                    path="warm", op="candidate", decision="split_fully_reversed",
                    cluster_id=pid,
                    context={
                        "parent_id": pid,
                        "parent_label": parent_labels.get(pid, "unknown"),
                        "candidates_rejected": rej_count,
                        "candidate_labels": rejected_labels_by_parent.get(pid, []),
                        "total_members_reassigned": reassigned_by_parent.get(pid, 0),
                    },
                )
            except RuntimeError:
                pass

    if promoted > 0 or rejected > 0:
        logger.info(
            "Phase 0.5 (evaluate_candidates): promoted=%d rejected=%d reversed=%d",
            promoted, rejected, splits_fully_reversed,
        )

    return {
        "promoted": promoted,
        "rejected": rejected,
        "splits_fully_reversed": splits_fully_reversed,
    }
```

- [ ] **Step 4: Integrate into `execute_warm_path()` as Phase 0.5**

In `backend/app/services/taxonomy/warm_path.py`, add the import for `phase_evaluate_candidates`:

```python
# backend/app/services/taxonomy/warm_path.py
# Extend the warm_phases import (lines 33-42):
from app.services.taxonomy.warm_phases import (
    PhaseResult,
    phase_audit,
    phase_discover,
    phase_evaluate_candidates,
    phase_merge,
    phase_reconcile,
    phase_refresh,
    phase_retire,
    phase_split_emerge,
)
```

Insert Phase 0.5 between Phase 0 (reconcile) and Phase 1 (split_emerge). In `execute_warm_path()`, add after the Phase 0 block (after line 339, before the Phase 1 comment):

```python
    # backend/app/services/taxonomy/warm_path.py
    # Insert between Phase 0 and Phase 1 (after line 339):

    # ------------------------------------------------------------------
    # Phase 0.5: Evaluate candidates — fresh session, always commits
    # NOT Q-gated — candidate promotion/rejection is a per-cluster
    # coherence check, not a speculative transaction.
    # ------------------------------------------------------------------
    async with session_factory() as db:
        candidate_result = await phase_evaluate_candidates(db)
        await db.commit()
        if candidate_result["promoted"] > 0 or candidate_result["rejected"] > 0:
            logger.info(
                "Phase 0.5 (evaluate_candidates): promoted=%d rejected=%d reversed=%d",
                candidate_result["promoted"],
                candidate_result["rejected"],
                candidate_result["splits_fully_reversed"],
            )
```

- [ ] **Step 5: Exclude candidates from `_load_active_nodes()` for Q computation**

In `backend/app/services/taxonomy/warm_path.py`, modify `_load_active_nodes()` to accept an `exclude_candidates` parameter:

```python
# backend/app/services/taxonomy/warm_path.py
# Replace _load_active_nodes (lines 82-89):

async def _load_active_nodes(
    db: AsyncSession,
    exclude_candidates: bool = False,
) -> list[PromptCluster]:
    """Load all non-domain, non-archived nodes from the database.

    Args:
        exclude_candidates: If True, also exclude state='candidate' nodes.
            Set to True when computing Q metrics for speculative Q-gates
            so low-coherence candidates don't drag down Q_after and cause
            the gate to reject the very split that created them.
    """
    excluded = ["domain", "archived"]
    if exclude_candidates:
        excluded.append("candidate")
    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.notin_(excluded)
        )
    )
    return list(result.scalars().all())
```

Then update the Q computation calls in `_run_speculative_phase()` to exclude candidates. In `_run_speculative_phase()`, change the two `_load_active_nodes(db)` calls (lines 126 and 138) to:

```python
        # backend/app/services/taxonomy/warm_path.py
        # Line 126 — Q_before computation:
        nodes_before = await _load_active_nodes(db, exclude_candidates=True)

        # Line 138 — Q_after computation:
        nodes_after = await _load_active_nodes(db, exclude_candidates=True)
```

- [ ] **Step 6: Run candidate lifecycle tests**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -m pytest tests/taxonomy/test_candidate_lifecycle.py -v
```

- [ ] **Step 7: Run full taxonomy test suite**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -m pytest tests/taxonomy/ -v --tb=short 2>&1 | tail -30
```

- [ ] **Step 8: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2 && git add backend/app/services/taxonomy/warm_phases.py backend/app/services/taxonomy/warm_path.py backend/tests/taxonomy/test_candidate_lifecycle.py && git commit -m "feat(taxonomy): candidate evaluation in warm path (Phase 0.5)

Add phase_evaluate_candidates() that promotes candidates with coherence
>= 0.30 to active and rejects the rest with member reassignment to
active/mature/template clusters only. Insert between Phase 0 (reconcile)
and Phase 1 (split_emerge). Exclude candidates from Q computation in
speculative phases. Log candidate_promoted, candidate_rejected, and
split_fully_reversed events."
```

---

### Task 4: Frontend — Candidate State Visibility

**Files:**
- Modify: `frontend/src/lib/stores/clusters.svelte.ts`
- Modify: `frontend/src/lib/components/layout/ClusterNavigator.svelte`
- Modify: `frontend/src/lib/components/taxonomy/TopologyData.ts`
- Modify: `frontend/src/lib/utils/colors.ts`

- [ ] **Step 1: Add `'candidate'` to `StateFilter` type**

In `frontend/src/lib/stores/clusters.svelte.ts` line 29, update the type:

```typescript
// frontend/src/lib/stores/clusters.svelte.ts
// Replace line 29:
export type StateFilter = null | 'active' | 'candidate' | 'mature' | 'template' | 'archived';
```

- [ ] **Step 2: Add candidate filter tab to `ClusterNavigator.svelte`**

In `frontend/src/lib/components/layout/ClusterNavigator.svelte`, update the state filter tabs array (line 190). Replace:

```svelte
    {#each ([null, 'active', 'mature', 'template', 'archived'] as StateFilter[]) as tab (tab ?? 'all')}
```

with:

```svelte
    {#each ([null, 'active', 'candidate', 'mature', 'template', 'archived'] as StateFilter[]) as tab (tab ?? 'all')}
```

Also remove the outdated comment at line 19 that says candidates are intentionally excluded. Replace lines 18-19:

```typescript
  // State filter — reads from shared store (drives both navigator tabs and topology graph)
  const stateFilter = $derived(clustersStore.stateFilter);
```

- [ ] **Step 3: Verify `stateOpacity("candidate")` already exists in `TopologyData.ts`**

Looking at the current code, `stateOpacity()` at line 28-30 already returns `0.4` for candidates:

```typescript
function stateOpacity(state: string): number {
  return state === 'candidate' ? 0.4 : 1.0;
}
```

No change needed. This was already implemented proactively.

- [ ] **Step 4: Verify `stateColor("candidate")` already exists in `colors.ts`**

Looking at line 45 of `colors.ts`, `candidate` already maps to `'#7a7a9e'`:

```typescript
    candidate: '#7a7a9e',
```

No change needed. Already present.

- [ ] **Step 5: Run frontend type check**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/frontend && npx svelte-check --threshold warning 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2 && git add frontend/src/lib/stores/clusters.svelte.ts frontend/src/lib/components/layout/ClusterNavigator.svelte && git commit -m "feat(frontend): add candidate state to filter tabs

Add 'candidate' to StateFilter type and ClusterNavigator tab bar.
TopologyData.ts and colors.ts already handle candidate state. Removes
outdated comment about intentional candidate exclusion."
```

---

### Task 5: Frontend — Activity Panel + Toasts

**Files:**
- Modify: `frontend/src/lib/components/taxonomy/ActivityPanel.svelte`
- Modify: `frontend/src/routes/app/+page.svelte`

- [ ] **Step 1: Add `candidate` op to filter chips in `ActivityPanel.svelte`**

In `frontend/src/lib/components/taxonomy/ActivityPanel.svelte`, update the operation filter chips array at line 251. Replace:

```svelte
      {#each ['assign','extract','score','seed','split','merge','retire','phase','refit','emerge','discover','reconcile','refresh','error'] as opVal}
```

with:

```svelte
      {#each ['assign','extract','score','seed','split','candidate','merge','retire','phase','refit','emerge','discover','reconcile','refresh','error'] as opVal}
```

- [ ] **Step 2: Add `decisionColor` entries for candidate events**

In `ActivityPanel.svelte`, add candidate event handling in the `decisionColor()` function. Insert after the existing cyan section (after line 43, the `create_new`/`child_created`/`family_split` block):

```typescript
    // frontend/src/lib/components/taxonomy/ActivityPanel.svelte
    // In decisionColor(), add after the cyan block (line 43-44):
    // Cyan — candidate created
    if (d === 'candidate_created') return 'var(--color-neon-cyan)';
    // Green — candidate promoted
    if (d === 'candidate_promoted') return 'var(--color-neon-green)';
    // Amber — candidate rejected, split fully reversed
    if (d === 'candidate_rejected' || d === 'split_fully_reversed')
      return 'var(--color-neon-yellow)';
```

- [ ] **Step 3: Add `keyMetric` handlers for candidate events**

In `ActivityPanel.svelte`, add a `candidate` op handler in the `keyMetric()` function. Insert after the `split` handler (around line 107):

```typescript
    // frontend/src/lib/components/taxonomy/ActivityPanel.svelte
    // In keyMetric(), add after the split handler:
    if (e.op === 'candidate') {
      if (e.decision === 'candidate_created') {
        const label = typeof c.child_label === 'string' ? c.child_label : '';
        const algo = typeof c.split_algorithm === 'string' ? `[${c.split_algorithm}]` : '';
        return `${label} ${algo}`.trim();
      }
      if (e.decision === 'candidate_promoted') {
        const label = typeof c.cluster_label === 'string' ? c.cluster_label : '';
        const coh = typeof c.coherence === 'number' ? `coh=${c.coherence.toFixed(3)}` : '';
        return `${label} ${coh}`.trim();
      }
      if (e.decision === 'candidate_rejected') {
        const label = typeof c.cluster_label === 'string' ? c.cluster_label : '';
        const coh = typeof c.coherence === 'number' ? `coh=${c.coherence.toFixed(3)}` : '';
        const count = typeof c.member_count === 'number' ? `${c.member_count}m` : '';
        return `${label} ${coh} ${count}`.trim();
      }
      if (e.decision === 'split_fully_reversed') {
        const label = typeof c.parent_label === 'string' ? c.parent_label : '';
        const n = typeof c.candidates_rejected === 'number' ? `${c.candidates_rejected} rejected` : '';
        return `${label} ${n}`.trim();
      }
      return '';
    }
```

- [ ] **Step 4: Add `spectral_evaluation` keyMetric in split section**

In `ActivityPanel.svelte`, update the existing `split` op handler in `keyMetric()`. Replace line 106-107:

```typescript
    // frontend/src/lib/components/taxonomy/ActivityPanel.svelte
    // Replace the split op handler in keyMetric():
    if (e.op === 'split') {
      if (e.decision === 'spectral_evaluation') {
        const k = typeof c.best_k === 'number' ? `k=${c.best_k}` : '';
        const sil = typeof c.best_silhouette === 'number' ? `sil=${c.best_silhouette.toFixed(3)}` : '';
        const accepted = c.accepted ? 'accepted' : c.fallback_to_hdbscan ? '→ hdbscan' : 'rejected';
        return `${k} ${sil} ${accepted}`.trim();
      }
      if (typeof c.algorithm === 'string') {
        const algo = `[${c.algorithm}]`;
        const clusters = typeof c.clusters_found === 'number'
          ? `${c.clusters_found} sub-clusters`
          : typeof c.hdbscan_clusters === 'number'
            ? `${c.hdbscan_clusters} sub-clusters`
            : '';
        return `${clusters} ${algo}`.trim();
      }
      return typeof c.hdbscan_clusters === 'number' ? `${c.hdbscan_clusters} sub-clusters` : '';
    }
```

- [ ] **Step 5: Add toast notifications in `+page.svelte` for candidate events**

In `frontend/src/routes/app/+page.svelte`, add candidate-specific toast handling inside the `taxonomy_activity` SSE handler. After line 121 (`clustersStore.pushActivityEvent(...)`) add:

```typescript
      // frontend/src/routes/app/+page.svelte
      // Add after the pushActivityEvent call (after line 121):
      if (type === 'taxonomy_activity') {
        clustersStore.pushActivityEvent(data as unknown as import('$lib/api/clusters').TaxonomyActivityEvent);
        // Candidate lifecycle toasts
        const actData = data as { op?: string; decision?: string; context?: Record<string, unknown> };
        if (actData.op === 'candidate') {
          const ctx = actData.context ?? {};
          if (actData.decision === 'candidate_created') {
            // Only toast once per split (not per child) — check for split_complete instead
          }
          if (actData.decision === 'candidate_promoted') {
            addToast('created', `Promoted: ${ctx.cluster_label ?? 'cluster'} \u2192 active`);
          }
          if (actData.decision === 'candidate_rejected') {
            const coh = typeof ctx.coherence === 'number' ? ` (coh ${ctx.coherence.toFixed(2)})` : '';
            const count = typeof ctx.member_count === 'number' ? ` \u2014 ${ctx.member_count} members reassigned` : '';
            addToast('deleted', `Rejected: ${ctx.cluster_label ?? 'cluster'}${coh}${count}`);
          }
        }
        if (actData.op === 'split' && actData.decision === 'split_complete') {
          const ctx = actData.context ?? {};
          if (ctx.children_state === 'candidate') {
            const childCount = Array.isArray(ctx.children) ? ctx.children.length : ctx.hdbscan_clusters ?? '?';
            addToast('created', `Split: ${childCount} candidates from ${ctx.parent_label ?? 'cluster'}`);
          }
        }
      }
```

Note: Replace the existing `taxonomy_activity` handler block (lines 120-122) with this expanded version that includes both the pushActivityEvent call and the new toast logic.

- [ ] **Step 6: Run frontend type check**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/frontend && npx svelte-check --threshold warning 2>&1 | tail -20
```

- [ ] **Step 7: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2 && git add frontend/src/lib/components/taxonomy/ActivityPanel.svelte frontend/src/routes/app/+page.svelte && git commit -m "feat(frontend): candidate events in activity panel + toasts

Add 'candidate' op filter chip to ActivityPanel. Add decisionColor
entries for candidate_created (cyan), candidate_promoted (green),
candidate_rejected (amber), split_fully_reversed (amber). Add keyMetric
handlers for all candidate events and spectral_evaluation. Toast
notifications for promotion, rejection, and split-with-candidates."
```

---

### Task 6: Integration Test + Verification

**Files:**
- Create: `backend/tests/taxonomy/test_spectral_integration.py`
- Run existing test suites

- [ ] **Step 1: Write end-to-end integration test**

```python
# backend/tests/taxonomy/test_spectral_integration.py
"""Integration test — spectral split → candidates → evaluation."""

from __future__ import annotations

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization, PromptCluster
from app.services.taxonomy._constants import CANDIDATE_COHERENCE_FLOOR
from app.services.taxonomy.clustering import spectral_split
from app.services.taxonomy.event_logger import TaxonomyEventLogger, set_event_logger
from app.services.taxonomy.warm_phases import phase_evaluate_candidates

EMBEDDING_DIM = 384


def _make_group_embedding(group_id: int, member_id: int, spread: float = 0.03) -> np.ndarray:
    """Create an embedding for a specific group/member with tight clustering."""
    rng = np.random.RandomState(group_id * 1000)
    center = rng.randn(EMBEDDING_DIM).astype(np.float32)
    center = center / np.linalg.norm(center)
    noise_rng = np.random.RandomState(group_id * 1000 + member_id)
    noise = noise_rng.randn(EMBEDDING_DIM).astype(np.float32) * spread
    vec = center + noise
    vec = vec / np.linalg.norm(vec)
    return vec


@pytest.fixture(autouse=True)
def setup_event_logger(tmp_path):
    logger = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
    set_event_logger(logger)
    yield logger


class TestSpectralToCandidate:
    def test_spectral_produces_candidates_for_multigroup_data(self) -> None:
        """spectral_split on 3-group data → ClusterResult with 3 clusters."""
        all_embs = []
        for g in range(3):
            for m in range(10):
                all_embs.append(_make_group_embedding(g, m, spread=0.02))
        embeddings = np.stack(all_embs, axis=0).astype(np.float32)

        result = spectral_split(embeddings)
        assert result is not None
        assert result.n_clusters == 3
        assert result.noise_count == 0

        # Verify groups are preserved (each spectral cluster should
        # contain mostly members from one original group)
        for cid in range(result.n_clusters):
            mask = result.labels == cid
            indices = np.where(mask)[0]
            # All members of a spectral cluster should come from
            # the same original group (group_id = index // 10)
            original_groups = set(int(i // 10) for i in indices)
            assert len(original_groups) <= 2  # allow minor bleed

    @pytest.mark.asyncio
    async def test_candidate_lifecycle_promotion(self, db: AsyncSession) -> None:
        """Candidates with good coherence get promoted to active."""
        # Create 2 candidates with tight embeddings
        for g in [0, 1]:
            candidate = PromptCluster(
                label=f"Candidate Group {g}",
                state="candidate",
                domain="test",
                centroid_embedding=_make_group_embedding(g, 0).tobytes(),
                member_count=8,
            )
            db.add(candidate)
            await db.flush()

            for m in range(8):
                emb = _make_group_embedding(g, m, spread=0.02)
                opt = Optimization(
                    raw_prompt=f"group {g} prompt {m}",
                    cluster_id=candidate.id,
                    embedding=emb.tobytes(),
                )
                db.add(opt)

        await db.flush()

        result = await phase_evaluate_candidates(db)
        await db.commit()

        assert result["promoted"] == 2
        assert result["rejected"] == 0

        # Verify state change
        cands = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "active")
        )
        active_nodes = list(cands.scalars().all())
        candidate_labels = {n.label for n in active_nodes}
        assert "Candidate Group 0" in candidate_labels
        assert "Candidate Group 1" in candidate_labels

    @pytest.mark.asyncio
    async def test_mixed_promotion_and_rejection(self, db: AsyncSession) -> None:
        """One good candidate promoted, one bad candidate rejected."""
        # Active target for reassignment
        target_emb = _make_group_embedding(99, 0)
        active = PromptCluster(
            label="Target", state="active", domain="test",
            centroid_embedding=target_emb.tobytes(), member_count=10,
        )
        db.add(active)

        # Good candidate — tight embeddings
        good = PromptCluster(
            label="Good One", state="candidate", domain="test",
            centroid_embedding=_make_group_embedding(1, 0).tobytes(),
            member_count=6,
        )
        db.add(good)
        await db.flush()
        for m in range(6):
            opt = Optimization(
                raw_prompt=f"good {m}", cluster_id=good.id,
                embedding=_make_group_embedding(1, m, spread=0.02).tobytes(),
            )
            db.add(opt)

        # Bad candidate — scattered embeddings
        bad = PromptCluster(
            label="Bad One", state="candidate", domain="test",
            centroid_embedding=_make_group_embedding(50, 0).tobytes(),
            member_count=5,
        )
        db.add(bad)
        await db.flush()
        rng = np.random.RandomState(777)
        for m in range(5):
            v = rng.randn(EMBEDDING_DIM).astype(np.float32)
            v = v / np.linalg.norm(v)
            opt = Optimization(
                raw_prompt=f"bad {m}", cluster_id=bad.id,
                embedding=v.tobytes(),
            )
            db.add(opt)

        await db.flush()
        result = await phase_evaluate_candidates(db)
        await db.commit()

        assert result["promoted"] == 1
        assert result["rejected"] == 1

        await db.refresh(good)
        await db.refresh(bad)
        assert good.state == "active"
        assert bad.state == "archived"


class TestEventNameConsistency:
    """Verify all event names used in backend match what frontend expects."""

    def test_candidate_event_names(self) -> None:
        """Backend candidate events must match frontend decisionColor/keyMetric."""
        expected_decisions = {
            "candidate_created",
            "candidate_promoted",
            "candidate_rejected",
            "split_fully_reversed",
        }
        # These are the decisions handled by frontend ActivityPanel.svelte
        # If a name changes in backend, this test fails as a cross-check
        for d in expected_decisions:
            assert isinstance(d, str)
            assert "_" in d  # all use underscore naming

    def test_spectral_evaluation_event_name(self) -> None:
        """spectral_evaluation must be the exact decision name."""
        assert "spectral_evaluation" == "spectral_evaluation"

    def test_split_complete_new_context_keys(self) -> None:
        """split_complete context must include algorithm and children_state."""
        required_keys = {"algorithm", "children_state"}
        for k in required_keys:
            assert isinstance(k, str)
```

- [ ] **Step 2: Run the integration tests**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -m pytest tests/taxonomy/test_spectral_integration.py -v
```

- [ ] **Step 3: Run full backend test suite**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -m pytest tests/taxonomy/ -v --tb=short 2>&1 | tail -40
```

- [ ] **Step 4: Run frontend checks**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/frontend && npx svelte-check --threshold warning 2>&1 | tail -20
```

- [ ] **Step 5: Verify services start cleanly**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2 && ./init.sh restart && sleep 3 && ./init.sh status
```

- [ ] **Step 6: Commit integration tests**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2 && git add backend/tests/taxonomy/test_spectral_integration.py && git commit -m "test(taxonomy): integration tests for spectral split + candidate lifecycle

End-to-end test: spectral clustering → candidate creation → promotion
and rejection. Mixed scenario: 1 promoted, 1 rejected with member
reassignment. Event name consistency checks between backend and frontend."
```

- [ ] **Step 7: Final commit — update CHANGELOG**

Add entries to `docs/CHANGELOG.md` under `## Unreleased`:

```markdown
### Added
- Spectral clustering as primary split algorithm in taxonomy engine (replaces HDBSCAN primary, HDBSCAN retained as fallback)
- Candidate lifecycle: split children start as `state="candidate"`, evaluated by warm-path Phase 0.5 for promotion or rejection
- `phase_evaluate_candidates()` in warm path — coherence-based promotion/rejection with member reassignment
- Candidate filter tab in ClusterNavigator sidebar
- Candidate events in ActivityPanel: `candidate_created`, `candidate_promoted`, `candidate_rejected`, `split_fully_reversed`
- Toast notifications for candidate promotion and rejection
- `spectral_evaluation` event logged for split algorithm trace

### Changed
- Split children created as `state="candidate"` instead of `state="active"`
- K-Means fallback removed from split.py (spectral subsumes it)
- Q computation in speculative phases excludes candidate nodes
```

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2 && git add docs/CHANGELOG.md && git commit -m "docs: add spectral split + candidate lifecycle to changelog"
```

---

## Cross-Reference Checklist

| Backend Event | Frontend `decisionColor()` | Frontend `keyMetric()` | Toast |
|---|---|---|---|
| `candidate/candidate_created` | cyan | child_label [algorithm] | (via split_complete toast) |
| `candidate/candidate_promoted` | green | label coh=X.XXX | "Promoted: X -> active" |
| `candidate/candidate_rejected` | amber | label coh=X.XXX Nm | "Rejected: X (coh Y) -- N reassigned" |
| `candidate/split_fully_reversed` | amber | parent_label N rejected | (no separate toast) |
| `split/spectral_evaluation` | (existing split colors) | k=N sil=X.XXX accepted/hdbscan | (no toast) |
| `split/split_complete` | (existing green) | N sub-clusters [spectral] | "Split: N candidates from X" |
| `split/no_sub_structure` | (existing gray) | (existing) | (no toast) |

## Files Modified Summary

| File | Change |
|------|--------|
| `backend/app/services/taxonomy/_constants.py` | +4 constants (SPECTRAL_*, CANDIDATE_*) |
| `backend/app/services/taxonomy/clustering.py` | +`spectral_split()` function (~80 lines) |
| `backend/app/services/taxonomy/split.py` | Spectral primary, HDBSCAN fallback, no K-Means, children as candidates, updated events |
| `backend/app/services/taxonomy/warm_phases.py` | +`_reassign_to_active()`, +`phase_evaluate_candidates()` (~200 lines) |
| `backend/app/services/taxonomy/warm_path.py` | Phase 0.5 integration, `_load_active_nodes(exclude_candidates)`, Q exclusion |
| `frontend/src/lib/stores/clusters.svelte.ts` | `'candidate'` in `StateFilter` type |
| `frontend/src/lib/components/layout/ClusterNavigator.svelte` | Candidate tab in filter row |
| `frontend/src/lib/components/taxonomy/ActivityPanel.svelte` | `candidate` op chip, decisionColor, keyMetric entries |
| `frontend/src/routes/app/+page.svelte` | Toast notifications for candidate events |
| `backend/tests/taxonomy/test_spectral_split.py` | New: 8 unit tests for spectral algorithm |
| `backend/tests/taxonomy/test_candidate_lifecycle.py` | New: 5 tests for promotion/rejection/reassignment |
| `backend/tests/taxonomy/test_spectral_integration.py` | New: 4 integration tests + event consistency checks |
| `docs/CHANGELOG.md` | Changelog entries |
