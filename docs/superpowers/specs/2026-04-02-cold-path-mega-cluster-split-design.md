# Cold Path Mega-Cluster Split + Split Cooldown Fix

**Date**: 2026-04-02
**Status**: Approved
**Scope**: Two-pass cold path architecture + split cooldown escape hatch

---

## Context

The cold path recluster runs HDBSCAN on PromptCluster centroids (34 points in 384D). At this scale, HDBSCAN finds 0 clusters (100% noise). Every centroid matches back to its original node — recluster is a no-op. Meanwhile, the warm path split_failures cooldown (3-strike lockout) has no escape hatch: the only reset path is a cold path refit match, which never fires because the cold path is a no-op. Mega-clusters (24 members, coherence 0.41, 5 task types) are permanently stuck.

---

## Fix 1: Cold Path Mega-Cluster Split Pass

### Architecture

The cold path becomes two passes:

1. **Pass 1 (existing)**: Centroid-level HDBSCAN — topology reorganization. Steps 1-24 unchanged.
2. **Pass 2 (new)**: Mega-cluster split — member-level HDBSCAN on qualifying clusters.

Pass 2 runs after Pass 1 commits, operating on reconciled state.

### Detection Criteria

A cluster qualifies for mega-cluster split when ALL are true:
- `member_count >= 2 * SPLIT_MIN_MEMBERS` (currently 12)
- `coherence < SPLIT_COHERENCE_FLOOR` (currently 0.5)
- `state` not in `("domain", "archived")`

Uses static floor (not dynamic) — the cold path is an explicit user action, so aggressive intervention is appropriate.

### Per-Cluster Split Logic

For each qualifying cluster:
1. Load `Optimization.id`, `.embedding`, `.optimized_embedding`, `.transformation_embedding` where `cluster_id = node.id`
2. Compute blended embeddings via `blend_embeddings(raw, optimized, transformation)`
3. Run `batch_cluster(blended, min_cluster_size=3)`
4. If HDBSCAN finds < 2 clusters and `len(members) >= 2 * SPLIT_MIN_MEMBERS`: K-means bisection fallback (`n_clusters=2`, reject if any group < 3)
5. If 2+ sub-clusters found:
   - Create new PromptCluster nodes (label via Haiku, compute centroid/coherence/avg_score from members)
   - Reassign `Optimization.cluster_id` to new children
   - Reassign noise points to nearest child by cosine similarity
   - Archive parent node (state="archived", remove from all 3 indices)
   - Upsert children into EmbeddingIndex
6. Reset `split_failures = 0` on all processed clusters regardless of split success (cold path tried — give warm path a fresh start)

### Shared `split_cluster()` Function

Extract the leaf split logic from `warm_phases.py` (lines 522-802) into a standalone function in a new module `backend/app/services/taxonomy/split.py`.

**Signature:**
```python
@dataclass
class SplitResult:
    success: bool
    children_created: int
    noise_reassigned: int
    children: list[PromptCluster]

async def split_cluster(
    node: PromptCluster,
    engine: TaxonomyEngine,
    db: AsyncSession,
    opt_rows: list[tuple[str, bytes, bytes | None, bytes | None]],
) -> SplitResult:
```

**Parameters:**
- `node`: The cluster to split
- `engine`: TaxonomyEngine (for provider, embedding index, labeling)
- `db`: Active async session
- `opt_rows`: Pre-fetched (opt_id, embedding_bytes, optimized_bytes, transformation_bytes) tuples

**Behavior:**
1. Build blended + raw embedding lists from opt_rows
2. HDBSCAN with min_cluster_size=3
3. K-means bisection fallback if HDBSCAN < 2 clusters and enough members
4. If 2+ sub-clusters: create children, reassign optimizations, reassign noise, archive parent
5. Return SplitResult

This function is called by:
- `warm_phases.py:phase_split_emerge` (replacing inline code)
- `cold_path.py` (new mega-cluster pass)

### Cold Path Integration

In `cold_path.py`, after the existing Step 24 commit section (around line 760), add:

```python
# ------------------------------------------------------------------
# Step 25: Mega-cluster split pass
# ------------------------------------------------------------------
mega_q = await db.execute(
    select(PromptCluster).where(
        PromptCluster.state.notin_(["domain", "archived"]),
        PromptCluster.member_count >= 2 * SPLIT_MIN_MEMBERS,
        PromptCluster.coherence < SPLIT_COHERENCE_FLOOR,
    )
)
mega_clusters = list(mega_q.scalars().all())

for mc in mega_clusters:
    # Load member embeddings
    opt_q = await db.execute(...)
    opt_rows = [...]
    
    result = await split_cluster(mc, engine, db, opt_rows)
    
    # Always reset split_failures (cold path tried)
    mc.cluster_metadata = write_meta(mc.cluster_metadata, split_failures=0)
    
    if result.success:
        nodes_created += result.children_created
        # ... log, update indices

await db.commit()  # Commit mega-cluster changes
```

---

## Fix 2: Split Cooldown Growth-Based Reset

### Problem

Once `split_failures >= 3`, the cluster is permanently skipped by warm path splits. The only reset is a cold path match — which never fires.

### Design

Store `split_attempt_member_count` in cluster_metadata when a split is attempted. Before the `split_failures >= 3` check in `phase_split_emerge`, check if the cluster has grown significantly:

```python
split_attempt_mc = node_meta.get("split_attempt_member_count", 0)
if split_failures >= 3 and split_attempt_mc > 0:
    growth = member_count / max(split_attempt_mc, 1)
    if growth >= 1.25:  # 25% growth
        split_failures = 0
        node.cluster_metadata = write_meta(
            node.cluster_metadata, split_failures=0
        )
        logger.info("Split cooldown reset: '%s' grew from %d to %d members",
                    node.label, split_attempt_mc, member_count)
```

When a split is attempted (regardless of success), store the current member_count:
```python
node.cluster_metadata = write_meta(
    node.cluster_metadata,
    split_attempt_member_count=member_count,
)
```

---

## Files Modified

| File | Change |
|------|--------|
| `backend/app/services/taxonomy/split.py` | **New file** — extracted `split_cluster()` + `SplitResult` |
| `backend/app/services/taxonomy/warm_phases.py` | Replace inline leaf split with `split_cluster()` call; add growth-based cooldown reset |
| `backend/app/services/taxonomy/cold_path.py` | Add Step 25 mega-cluster split pass after existing commit |
| `backend/app/services/taxonomy/cluster_meta.py` | Add `split_attempt_member_count` to defaults |
| `backend/app/services/taxonomy/_constants.py` | Add `MEGA_CLUSTER_MEMBER_FLOOR = 2 * SPLIT_MIN_MEMBERS` (12) |

## Testing

- `test_split.py`: Unit tests for `split_cluster()` — HDBSCAN finds sub-clusters, k-means fallback, noise reassignment, parent archived
- `test_cold_path.py`: Integration test verifying mega-cluster pass detects and splits qualifying clusters
- `test_warm_phases.py`: Test growth-based cooldown reset (split_failures resets when member_count grows 25%+)
- Full suite: `cd backend && pytest --cov=app -v`

## Verification

1. Backend tests pass
2. Restart services, trigger recluster
3. Verify "Saas Customer Lifecycle" (24 members, coherence 0.41) is split into 2-3 sub-clusters
4. Verify split_failures reset on processed clusters
5. Verify Q_system remains stable or improves after split
