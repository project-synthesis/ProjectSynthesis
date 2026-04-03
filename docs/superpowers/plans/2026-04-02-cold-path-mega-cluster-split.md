# Cold Path Mega-Cluster Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the cold path recluster meaningful by adding a mega-cluster split pass that operates on member-level embeddings, and fix the split cooldown escape hatch so stuck clusters can eventually be split by the warm path.

**Architecture:** Extract shared `split_cluster()` function from warm_phases.py into split.py. Add a second pass to the cold path that detects mega-clusters (high member count + low coherence) and splits them using member-level HDBSCAN. Add growth-based cooldown reset to warm path so clusters that gain new members get fresh split attempts.

**Tech Stack:** Python, numpy, sklearn HDBSCAN/KMeans, SQLAlchemy async, pytest

**Spec:** `docs/superpowers/specs/2026-04-02-cold-path-mega-cluster-split-design.md`

---

### Task 1: Add Constants and Metadata Key

**Files:**
- Modify: `backend/app/services/taxonomy/_constants.py`
- Modify: `backend/app/services/taxonomy/cluster_meta.py`

- [ ] **Step 1: Add MEGA_CLUSTER_MEMBER_FLOOR constant**

In `backend/app/services/taxonomy/_constants.py`, after line 17 (`SPLIT_MIN_MEMBERS = 6`), add:

```python
MEGA_CLUSTER_MEMBER_FLOOR = 2 * SPLIT_MIN_MEMBERS  # 12 — cold path mega-cluster split threshold
```

- [ ] **Step 2: Add split_attempt_member_count to ClusterMeta**

In `backend/app/services/taxonomy/cluster_meta.py`, add to the `ClusterMeta` TypedDict (after `split_failures` line 38):

```python
    split_attempt_member_count: int   # member_count when last split was attempted (growth-based cooldown reset)
```

Add to `_DEFAULTS` dict (after `"split_failures": 0,` line 62):

```python
    "split_attempt_member_count": 0,
```

Add to `_COERCE` dict (after `"split_failures": int,` line 74):

```python
    "split_attempt_member_count": int,
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `cd backend && source .venv/bin/activate && pytest tests/taxonomy/ -v --tb=short -q 2>&1 | tail -5`

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/taxonomy/_constants.py backend/app/services/taxonomy/cluster_meta.py
git commit -m "feat: add MEGA_CLUSTER_MEMBER_FLOOR constant and split_attempt_member_count metadata"
```

---

### Task 2: Create split.py with split_cluster()

**Files:**
- Create: `backend/app/services/taxonomy/split.py`
- Create: `backend/tests/taxonomy/test_split.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/taxonomy/test_split.py`:

```python
"""Tests for the shared split_cluster() function."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.taxonomy.split import SplitResult, split_cluster


def _rand_emb(dim: int = 384, seed: int = 0) -> bytes:
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tobytes()


def test_split_result_dataclass():
    """SplitResult has required fields."""
    r = SplitResult(success=True, children_created=3, noise_reassigned=2, children=[])
    assert r.success is True
    assert r.children_created == 3
    assert r.noise_reassigned == 2


def test_split_result_failure():
    """SplitResult for failed split."""
    r = SplitResult(success=False, children_created=0, noise_reassigned=0, children=[])
    assert r.success is False


def test_split_cluster_is_async():
    """split_cluster must be a coroutine function."""
    import inspect
    assert inspect.iscoroutinefunction(split_cluster)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/taxonomy/test_split.py -v`

Expected: FAIL — `split.py` doesn't exist yet.

- [ ] **Step 3: Create split.py with SplitResult and split_cluster()**

Create `backend/app/services/taxonomy/split.py`:

```python
"""Shared cluster split logic — member-level HDBSCAN + k-means fallback.

Extracted from warm_phases.py to be reusable by both warm-path leaf splits
and cold-path mega-cluster splits. The function receives pre-fetched
Optimization embedding rows and handles:
  1. Blended embedding construction
  2. HDBSCAN clustering (min_cluster_size=3)
  3. K-means bisection fallback when HDBSCAN finds < 2 clusters
  4. Child node creation with Haiku labeling
  5. Optimization reassignment to children
  6. Noise point reassignment to nearest child
  7. Parent node archival

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Optimization, PromptCluster
from app.services.taxonomy._constants import SPLIT_MIN_MEMBERS, _utcnow
from app.services.taxonomy.clustering import (
    batch_cluster,
    blend_embeddings,
    compute_pairwise_coherence,
    cosine_similarity,
    l2_normalize_1d,
)
from app.services.taxonomy.family_ops import merge_score_into_cluster

if TYPE_CHECKING:
    from app.services.taxonomy.engine import TaxonomyEngine

logger = logging.getLogger(__name__)


@dataclass
class SplitResult:
    """Outcome of a split_cluster() attempt."""

    success: bool
    children_created: int
    noise_reassigned: int
    children: list[PromptCluster] = field(default_factory=list)


async def split_cluster(
    node: PromptCluster,
    engine: TaxonomyEngine,
    db: AsyncSession,
    opt_rows: list[tuple[str, bytes, bytes | None, bytes | None]],
) -> SplitResult:
    """Split a cluster into sub-clusters using member-level HDBSCAN.

    Args:
        node: The PromptCluster to split.
        engine: TaxonomyEngine instance (for provider, indices).
        db: Active async session (caller manages commit/rollback).
        opt_rows: Pre-fetched (opt_id, embedding_bytes, optimized_bytes,
                  transformation_bytes) tuples for this cluster's members.

    Returns:
        SplitResult indicating success and what was created.
    """
    # Build blended + raw embeddings
    child_embs: list[np.ndarray] = []       # raw (for centroid storage)
    child_blended: list[np.ndarray] = []    # blended (for HDBSCAN)
    child_opt_ids: list[str] = []
    for opt_id, emb_bytes, opt_bytes, trans_bytes in opt_rows:
        try:
            raw = np.frombuffer(emb_bytes, dtype=np.float32).copy()
            opt_emb = (
                np.frombuffer(opt_bytes, dtype=np.float32).copy()
                if opt_bytes else None
            )
            trans_emb = (
                np.frombuffer(trans_bytes, dtype=np.float32).copy()
                if trans_bytes else None
            )
            child_embs.append(raw)
            child_blended.append(blend_embeddings(
                raw=raw, optimized=opt_emb, transformation=trans_emb,
            ))
            child_opt_ids.append(opt_id)
        except (ValueError, TypeError):
            continue

    if len(child_blended) < SPLIT_MIN_MEMBERS:
        return SplitResult(success=False, children_created=0, noise_reassigned=0)

    # HDBSCAN
    split_result = batch_cluster(child_blended, min_cluster_size=3)

    # K-means bisection fallback
    if (
        split_result.n_clusters < 2
        and len(child_blended) >= 2 * SPLIT_MIN_MEMBERS
    ):
        try:
            from sklearn.cluster import KMeans

            emb_stack = np.stack(child_blended, axis=0)
            km = KMeans(n_clusters=2, n_init=10, random_state=42)
            km_labels = km.fit_predict(emb_stack)
            sizes = [int((km_labels == c).sum()) for c in range(2)]
            if all(s >= 3 for s in sizes):
                centroids = [
                    l2_normalize_1d(km.cluster_centers_[c].astype(np.float32))
                    for c in range(2)
                ]
                split_result = type(split_result)(
                    labels=km_labels,
                    centroids=centroids,
                    n_clusters=2,
                    persistences=[0.5, 0.5],
                    noise_count=0,
                )
                logger.info(
                    "HDBSCAN failed, k-means bisection succeeded: %s members",
                    sizes,
                )
        except Exception as km_exc:
            logger.debug("k-means fallback failed: %s", km_exc)

    if split_result.n_clusters < 2:
        return SplitResult(success=False, children_created=0, noise_reassigned=0)

    # Create child clusters
    from app.services.taxonomy.coloring import generate_color
    from app.services.taxonomy.labeling import generate_label

    parent_domain = node.domain or "general"
    parent_id_for_children = node.parent_id or node.id
    new_children: list[PromptCluster] = []

    for cid in range(split_result.n_clusters):
        mask = split_result.labels == cid
        group_opt_ids = [
            child_opt_ids[i] for i in range(len(child_opt_ids)) if mask[i]
        ]
        group_embs = [
            child_embs[i] for i in range(len(child_embs)) if mask[i]
        ]
        if not group_embs:
            continue

        centroid = l2_normalize_1d(
            np.mean(np.stack(group_embs), axis=0).astype(np.float32)
        )
        child_coherence = compute_pairwise_coherence(group_embs)

        # Generate label from member intent labels
        opt_labels_q = await db.execute(
            select(Optimization.intent_label)
            .where(Optimization.id.in_(group_opt_ids))
            .limit(10)
        )
        member_texts = [r[0] for r in opt_labels_q.all() if r[0]]
        label = await generate_label(
            provider=engine._provider,
            member_texts=member_texts,
            model=settings.MODEL_HAIKU,
        )

        # Compute avg_score from members
        score_q = await db.execute(
            select(
                func.avg(Optimization.overall_score),
                func.count(Optimization.overall_score),
            ).where(
                Optimization.id.in_(group_opt_ids),
                Optimization.overall_score.isnot(None),
            )
        )
        score_row = score_q.one()
        child_avg_score = round(score_row[0], 2) if score_row[0] is not None else None
        child_scored_count = score_row[1] or 0

        child_node = PromptCluster(
            label=label,
            centroid_embedding=centroid.astype(np.float32).tobytes(),
            member_count=len(group_opt_ids),
            scored_count=child_scored_count,
            avg_score=child_avg_score,
            coherence=child_coherence,
            state="active",
            domain=parent_domain,
            parent_id=parent_id_for_children,
            color_hex=generate_color(0.0, 0.0, 0.0),
        )
        db.add(child_node)
        await db.flush()

        # Reassign optimizations
        await db.execute(
            sa_update(Optimization)
            .where(Optimization.id.in_(group_opt_ids))
            .values(cluster_id=child_node.id)
        )
        new_children.append(child_node)
        logger.info(
            "  Split child '%s' (%d members, coherence=%.3f)",
            label, len(group_opt_ids), child_coherence,
        )

    if len(new_children) < 2:
        return SplitResult(success=False, children_created=0, noise_reassigned=0)

    # Archive parent
    node.state = "archived"
    node.archived_at = _utcnow()
    node.member_count = 0
    node.scored_count = 0
    node.usage_count = 0
    node.avg_score = None
    await engine._embedding_index.remove(node.id)
    await engine._transformation_index.remove(node.id)
    await engine._optimized_index.remove(node.id)

    # Upsert children into embedding index
    for child in new_children:
        c_emb = np.frombuffer(child.centroid_embedding, dtype=np.float32)
        await engine._embedding_index.upsert(child.id, c_emb)

    # Reassign noise to nearest child
    noise_reassigned = 0
    noise_ids = [
        child_opt_ids[i]
        for i in range(len(child_opt_ids))
        if split_result.labels[i] == -1
    ]
    if noise_ids:
        noise_emb_lookup: dict[str, bytes] = {}
        for opt_id, emb_bytes, *_ in opt_rows:
            if opt_id in set(noise_ids):
                noise_emb_lookup[opt_id] = emb_bytes

        noise_score_q = await db.execute(
            select(Optimization.id, Optimization.overall_score)
            .where(Optimization.id.in_(noise_ids))
        )
        noise_score_lookup = {r[0]: r[1] for r in noise_score_q.all()}

        for nid in noise_ids:
            n_bytes = noise_emb_lookup.get(nid)
            if not n_bytes:
                continue
            n_emb = np.frombuffer(n_bytes, dtype=np.float32)
            best_c, best_s = None, -1.0
            for ch in new_children:
                c_emb = np.frombuffer(ch.centroid_embedding, dtype=np.float32)
                s = cosine_similarity(n_emb, c_emb)
                if s > best_s:
                    best_s, best_c = s, ch
            if best_c:
                await db.execute(
                    sa_update(Optimization)
                    .where(Optimization.id == nid)
                    .values(cluster_id=best_c.id)
                )
                best_c.member_count = (best_c.member_count or 0) + 1
                merge_score_into_cluster(best_c, noise_score_lookup.get(nid))
                noise_reassigned += 1

    await db.flush()
    logger.info(
        "Split '%s' -> %d sub-clusters (%d noise reassigned)",
        node.label, len(new_children), noise_reassigned,
    )

    return SplitResult(
        success=True,
        children_created=len(new_children),
        noise_reassigned=noise_reassigned,
        children=new_children,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/taxonomy/test_split.py -v`

Expected: All 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/split.py backend/tests/taxonomy/test_split.py
git commit -m "feat: extract split_cluster() into shared split.py module"
```

---

### Task 3: Wire warm_phases.py to use split_cluster()

**Files:**
- Modify: `backend/app/services/taxonomy/warm_phases.py` (replace inline leaf split ~lines 522-802)

- [ ] **Step 1: Replace inline leaf split with split_cluster() call**

In `backend/app/services/taxonomy/warm_phases.py`, find the leaf split path starting at line 521. Replace the block from line 522 (`if len(node_families) < SPLIT_MIN_MEMBERS and member_count >= SPLIT_MIN_MEMBERS:`) through the end of the leaf split (approximately line 802, ending with the `operations_log.append` and `logger.info("Leaf split complete:...)`).

Replace the entire block with:

```python
        # --- Leaf split path ---
        if len(node_families) < SPLIT_MIN_MEMBERS and member_count >= SPLIT_MIN_MEMBERS:
            opt_rows = _cached_opt_rows
            if len(opt_rows) >= SPLIT_MIN_MEMBERS:
                from app.services.taxonomy.split import split_cluster

                result = await split_cluster(node, engine, db, opt_rows)

                if not result.success:
                    # Track failed attempt for cooldown
                    node.cluster_metadata = write_meta(
                        node.cluster_metadata,
                        split_failures=split_failures + 1,
                        split_attempt_member_count=member_count,
                    )
                    logger.info(
                        "Leaf split failed for '%s' (attempt %d/3)",
                        node.label, split_failures + 1,
                    )
                else:
                    # Reset failure counter on success
                    node.cluster_metadata = write_meta(
                        node.cluster_metadata,
                        split_failures=0,
                        split_attempt_member_count=0,
                    )
                    embedding_index_mutations += len(result.children) + 1

                    # Protect split children and parent from merge in same cycle
                    split_protected_ids.add(node.id)
                    for ch in result.children:
                        split_protected_ids.add(ch.id)

                    ops_accepted += result.children_created
                    operations_log.append({
                        "type": "leaf_split",
                        "parent_id": node.id,
                        "children": [c.id for c in result.children],
                    })
                    logger.info(
                        "Leaf split complete: '%s' -> %d sub-clusters",
                        node.label, result.children_created,
                    )
```

- [ ] **Step 2: Add growth-based cooldown reset**

In the same function, BEFORE the `split_failures >= 3` check (line ~502), add the growth-based reset. The code currently reads:

```python
        # Cooldown: skip if this cluster already failed to split 3+ times
        node_meta = read_meta(node.cluster_metadata)
        split_failures = node_meta["split_failures"]
        if split_failures >= 3:
            continue
```

Replace with:

```python
        # Cooldown: skip if this cluster already failed to split 3+ times
        # Growth-based reset: if member_count grew 25%+ since last attempt,
        # new data may create sub-structure that wasn't there before.
        node_meta = read_meta(node.cluster_metadata)
        split_failures = node_meta["split_failures"]
        if split_failures >= 3:
            split_attempt_mc = node_meta.get("split_attempt_member_count", 0)
            if split_attempt_mc > 0 and member_count >= split_attempt_mc * 1.25:
                split_failures = 0
                node.cluster_metadata = write_meta(
                    node.cluster_metadata, split_failures=0,
                )
                logger.info(
                    "Split cooldown reset: '%s' grew from %d to %d members",
                    node.label, split_attempt_mc, member_count,
                )
            else:
                continue
```

- [ ] **Step 3: Run warm path tests**

Run: `cd backend && pytest tests/taxonomy/test_warm_phases.py tests/taxonomy/test_engine_warm_path.py tests/taxonomy/test_warm_path.py -v --tb=short -q 2>&1 | tail -10`

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/taxonomy/warm_phases.py
git commit -m "refactor: warm path uses shared split_cluster(), adds growth-based cooldown reset"
```

---

### Task 4: Add Mega-Cluster Split Pass to Cold Path

**Files:**
- Modify: `backend/app/services/taxonomy/cold_path.py` (add Step 25 after line ~773)

- [ ] **Step 1: Write failing test**

Add to `backend/tests/taxonomy/test_cold_path.py`:

```python
def test_cold_path_has_mega_cluster_split_pass():
    """Verify cold_path.py implements mega-cluster split pass."""
    import inspect
    from app.services.taxonomy import cold_path

    source = inspect.getsource(cold_path)
    assert "MEGA_CLUSTER_MEMBER_FLOOR" in source, (
        "Cold path must reference MEGA_CLUSTER_MEMBER_FLOOR for mega-cluster detection"
    )
    assert "split_cluster" in source, (
        "Cold path must call split_cluster() for mega-cluster splits"
    )
    assert "SPLIT_COHERENCE_FLOOR" in source, (
        "Cold path must use SPLIT_COHERENCE_FLOOR for coherence check"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/taxonomy/test_cold_path.py::test_cold_path_has_mega_cluster_split_pass -v`

Expected: FAIL.

- [ ] **Step 3: Add mega-cluster split pass to cold_path.py**

First, update the imports at the top of `backend/app/services/taxonomy/cold_path.py`. Add to the `_constants` import (line ~38):

```python
from app.services.taxonomy._constants import (
    CLUSTERING_BLEND_W_OPTIMIZED,
    CLUSTERING_BLEND_W_TRANSFORM,
    MEGA_CLUSTER_MEMBER_FLOOR,
    SPLIT_COHERENCE_FLOOR,
)
```

Then, in `cold_path.py`, after the snapshot creation (line ~772) and before the `engine._cold_path_needed = False` line (~774), add the mega-cluster split pass:

```python
    # ------------------------------------------------------------------
    # Step 25: Mega-cluster split pass
    # Identify clusters with high member count + low coherence and split
    # them using member-level HDBSCAN. This is the second pass of the
    # two-pass cold path: Pass 1 handled topology (centroid-level),
    # Pass 2 handles mega-clusters (member-level).
    # ------------------------------------------------------------------
    mega_split_created = 0
    try:
        mega_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(["domain", "archived"]),
                PromptCluster.member_count >= MEGA_CLUSTER_MEMBER_FLOOR,
                PromptCluster.coherence < SPLIT_COHERENCE_FLOOR,
            )
        )
        mega_clusters = list(mega_q.scalars().all())

        if mega_clusters:
            from app.services.taxonomy.split import split_cluster

            logger.info(
                "Mega-cluster split pass: %d candidates detected",
                len(mega_clusters),
            )

            for mc in mega_clusters:
                # Load member embeddings
                mc_opt_q = await db.execute(
                    select(
                        Optimization.id,
                        Optimization.embedding,
                        Optimization.optimized_embedding,
                        Optimization.transformation_embedding,
                    ).where(
                        Optimization.cluster_id == mc.id,
                        Optimization.embedding.isnot(None),
                    )
                )
                mc_opt_rows = [
                    (r[0], r[1], r[2], r[3]) for r in mc_opt_q.all()
                ]

                if len(mc_opt_rows) < MEGA_CLUSTER_MEMBER_FLOOR:
                    continue

                mc_result = await split_cluster(mc, engine, db, mc_opt_rows)

                # Always reset split_failures — cold path tried
                mc.cluster_metadata = write_meta(
                    mc.cluster_metadata,
                    split_failures=0,
                    split_attempt_member_count=0,
                )

                if mc_result.success:
                    mega_split_created += mc_result.children_created
                    logger.info(
                        "Mega-cluster split: '%s' -> %d sub-clusters (%d noise)",
                        mc.label,
                        mc_result.children_created,
                        mc_result.noise_reassigned,
                    )
                else:
                    logger.info(
                        "Mega-cluster split failed for '%s' (HDBSCAN found no sub-structure)",
                        mc.label,
                    )

            if mega_split_created > 0:
                await db.commit()
                engine._invalidate_stats_cache()
                nodes_created += mega_split_created
                logger.info(
                    "Mega-cluster split pass complete: %d new clusters created",
                    mega_split_created,
                )
    except Exception as mega_exc:
        logger.warning(
            "Mega-cluster split pass failed (non-fatal): %s", mega_exc,
            exc_info=True,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/taxonomy/test_cold_path.py -v --tb=short -q 2>&1 | tail -10`

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/cold_path.py backend/tests/taxonomy/test_cold_path.py
git commit -m "feat: cold path mega-cluster split pass (two-pass architecture)"
```

---

### Task 5: Full Test Suite + Lint + Push

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && pytest --tb=short -q 2>&1 | tail -10`

Expected: All tests pass.

- [ ] **Step 2: Run ruff lint**

Run: `cd backend && ruff check app/ tests/ --fix`

Expected: Clean or auto-fixed.

- [ ] **Step 3: Final commit if lint changes**

```bash
git add -A && git commit -m "fix: lint cleanup for cold path mega-cluster split"
```

(Skip if no changes.)

- [ ] **Step 4: Push**

```bash
git push origin main
```
