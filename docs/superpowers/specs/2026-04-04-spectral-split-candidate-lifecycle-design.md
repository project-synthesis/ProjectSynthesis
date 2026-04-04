# Spectral Split + Candidate Lifecycle

Replace HDBSCAN cluster splits with spectral clustering and introduce candidate-state visibility for split children.

## Problem

Large low-coherence clusters (e.g., 39 members, 0.41 coherence) cannot be split because HDBSCAN requires density variation to find sub-groups. In uniform-density embedding spaces — common for broad semantic categories like "SaaS growth strategy" — HDBSCAN returns 0 clusters and the split exhausts after 3 attempts. The K-Means fallback only fires when member count ≥ 50, missing the 25-49 range entirely.

Additionally, split children are created as `state="active"` immediately with zero visibility into the taxonomy's decision-making process. Users cannot see what was proposed, what was accepted, or why.

## Solution

Two changes:

1. **Spectral clustering** replaces HDBSCAN as the primary split algorithm. Spectral finds sub-communities by analyzing the similarity graph structure, not density — directly solving the uniform-density problem.

2. **Candidate lifecycle** for split children. Children start as `state="candidate"`, are visible in the topology and sidebar, and are promoted or rejected by the warm path on the next cycle. Full observability at every decision point.

## Scope

- Backend: `clustering.py` (new `spectral_split`), `split.py` (swap primary algorithm), `warm_phases.py` (candidate evaluation phase), `_constants.py` (new thresholds)
- Frontend: `TopologyRenderer` (candidate node opacity), `ActivityPanel` (candidate events + filter chip), sidebar filter tabs (candidate tab with count badge), `Inspector` (CANDIDATE badge), toast notifications, SSE handling
- Tests: unit tests for spectral algorithm, candidate lifecycle, member reassignment on rejection

Does NOT include: agglomerative cold-path rewrite (separate project), human-in-the-loop candidate intervention, manual promote/reject controls.

---

## 1. Spectral Split Algorithm

### New function: `spectral_split()` in `clustering.py`

```
spectral_split(
    embeddings: np.ndarray,    # (N, D) L2-normalized blended embeddings
    k_range: tuple = (2, 3, 4),
    silhouette_gate: float = 0.15,
) -> ClusterResult | None
```

**Steps:**
1. Compute cosine similarity matrix: `S = embeddings @ embeddings.T`
2. Clip negative values: `S = np.clip(S, 0, None)` (spectral requires non-negative affinity)
3. For each k in k_range:
   - Run `SpectralClustering(n_clusters=k, affinity='precomputed', random_state=42).fit_predict(S)`
   - Compute silhouette score on the labels using cosine metric
   - Reject if any cluster has fewer than 3 members (prevent degenerate micro-clusters)
4. Select k with best silhouette score
5. If best silhouette < silhouette_gate → return None (genuinely unsplittable)
6. Compute L2-normalized centroid per cluster (mean of members, re-normalized)
7. Return `ClusterResult(labels, n_clusters, noise_count=0, centroids, persistences=[silhouette]*n, silhouette=best_silhouette)`

**Why no noise:** Spectral assigns every point to a cluster. For splits, this is better than HDBSCAN's noise label — we don't want orphaned members during a split. The existing noise reassignment code in `split.py` still runs but will be a no-op (noise_count=0).

### Integration in `split.py`

Replace the current flow:

```python
# Current:
split_result = batch_cluster(child_blended, min_cluster_size=8)
# ... K-Means fallback if <2 clusters AND ≥50 members

# New:
split_result = spectral_split(child_blended)
if split_result is None:
    # Secondary fallback: HDBSCAN may find density-based structure spectral missed
    split_result = batch_cluster(child_blended, min_cluster_size=8)
```

Remove the K-Means fallback block (lines 113-143 of current split.py). Spectral subsumes it — it can find 2, 3, or 4 groups natively.

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `SPECTRAL_K_RANGE` | `(2, 3, 4)` | k values to try |
| `SPECTRAL_SILHOUETTE_GATE` | `0.15` | Minimum silhouette to accept a split |
| `SPECTRAL_MIN_GROUP_SIZE` | `3` | Minimum members per sub-cluster |

---

## 2. Candidate Lifecycle

### Split children created as candidates

In `split.py`, change child creation from `state="active"` to `state="candidate"`.

Merge protection (60-min window) still applies — prevents warm-path merges from absorbing candidates before evaluation.

### Candidate evaluation (warm path Phase 0.5)

New function `phase_evaluate_candidates()` in `warm_phases.py`, runs after Phase 0 (reconcile) and before Phase 1 (split/emerge).

**For each cluster with `state="candidate"`:**
1. Recompute coherence from member embeddings (Phase 0 already did this)
2. Check coherence ≥ `CANDIDATE_COHERENCE_FLOOR` (0.30 — same as domain coherence floor)
3. **Promote:** Set `state="active"`. Log `candidate_promoted` event.
4. **Reject:** Reassign members to nearest active clusters via `assign_cluster()`. Archive the candidate. Log `candidate_rejected` event with reassignment details.

**If ALL candidates from a single split are rejected:**
- Parent stays archived (it was already incoherent, un-archiving won't help)
- Members scatter organically via nearest-active assignment
- Log `split_fully_reversed` event

### Candidate evaluation is NOT Q-gated

Candidate promotion/rejection is a per-cluster coherence check, not a speculative transaction. It commits directly — no rollback needed. The Q-gate already validated the split before creating candidates.

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `CANDIDATE_COHERENCE_FLOOR` | `0.30` | Minimum coherence for promotion |

---

## 3. Observability

### New events (op="candidate")

| Decision | When | Context Keys |
|----------|------|-------------|
| `candidate_created` | Split produces a candidate child | `parent_id`, `parent_label`, `parent_member_count`, `child_label`, `child_member_count`, `child_coherence`, `split_algorithm`, `k_selected`, `silhouette_score` |
| `candidate_promoted` | Warm path promotes to active | `cluster_label`, `member_count`, `coherence`, `reason`, `coherence_floor`, `time_as_candidate_ms` |
| `candidate_rejected` | Warm path rejects candidate | `cluster_label`, `member_count`, `coherence`, `reason`, `coherence_floor`, `members_reassigned_to` (list of {cluster_id, cluster_label, count}) |
| `split_fully_reversed` | All candidates from one split rejected | `parent_id`, `parent_label`, `candidates_rejected`, `total_members_reassigned` |

### New event (op="split")

| Decision | When | Context Keys |
|----------|------|-------------|
| `spectral_evaluation` | After running spectral on all k values | `cluster_id`, `cluster_label`, `member_count`, `input_coherence`, `silhouettes_by_k`, `best_k`, `best_silhouette`, `gate_threshold`, `accepted`, `fallback_to_hdbscan` |

### Enhanced existing events

| Event | Addition |
|-------|----------|
| `split/split_complete` | `algorithm: "spectral"/"hdbscan"`, `k_tried`, `silhouettes`, `k_selected`, `children_state: "candidate"` |
| `split/no_sub_structure` | `spectral_silhouettes`, `hdbscan_clusters`, `reason` |

### Event chain for a complete split lifecycle

```
split/spectral_evaluation     → algorithm trace (silhouettes, k selection)
split/split_complete          → split accepted, N candidates created
candidate/candidate_created   → per-child detail (one per child)
...warm cycle...
candidate/candidate_promoted  → per-child promotion (or rejection)
```

---

## 4. Frontend

### Sidebar filter tabs

Add `candidate` to the existing state filter row in ClusterNavigator:

```
all | active | candidate | mature | template | archived
```

When candidates > 0, the `candidate` tab shows a count badge.

### Topology visualization

Candidate nodes render at **40% opacity** with their domain color. Same size scaling by member count. Same parent-child positioning. Clickable — opens Inspector. No billboard label until promoted (reduces visual noise for transient state).

### Inspector

State badge shows `CANDIDATE` with dimmed styling (`--color-text-dim`). All fields populated (members, scores, coherence, patterns). "Promote to Template" button hidden while candidate.

### Toast notifications

| Event | Toast |
|-------|-------|
| Split producing candidates | `"Split: 3 candidates from Saas Growth Strategy"` (one toast per split) |
| Candidate promoted | `"Promoted: SaaS Billing Tasks → active"` (per child) |
| Candidate rejected | `"Rejected: SaaS Misc (coh 0.18) — 4 members reassigned"` (per child) |

### Activity panel

- New `candidate` op in filter chip row
- `decisionColor`: `candidate_created` → cyan, `candidate_promoted` → green, `candidate_rejected` → amber, `split_fully_reversed` → amber
- `keyMetric` handlers for all four candidate events

### SSE

All candidate events flow through existing `taxonomy_activity` SSE type. No new SSE event types needed. `taxonomy_changed` published after promotion/rejection batch to trigger topology re-render.

---

## 5. Error Handling

| Failure | Scope | Recovery | Event |
|---------|-------|----------|-------|
| SpectralClustering raises (degenerate matrix) | Per-split | Catch, log warning, fall through to HDBSCAN | `spectral_evaluation` with `accepted: false, error: "..."` |
| Both spectral and HDBSCAN fail | Per-split | `no_sub_structure`, 3-strike cooldown | `split/no_sub_structure` with both algorithms' details |
| All candidates from split rejected | Per-split | Members scatter via `assign_cluster()`, parent stays archived | `split_fully_reversed` |
| Candidate has 0 members at evaluation | Per-candidate | Archive immediately | `candidate_rejected` with `reason: "zero_members"` |
| Candidate coherence is None | Per-candidate | Treat as below floor, reject | `candidate_rejected` with `reason: "coherence_unavailable"` |
| `assign_cluster()` fails during rejection reassignment | Per-member | Log warning, skip that member (orphaned, hot path picks up later) | Warning in `candidate_rejected` context |

---

## 6. Testing

### Unit tests

- `spectral_split()` with 3 clear groups (k=3 selected, silhouette > 0.3)
- `spectral_split()` with 2 clear groups (k=2 selected)
- `spectral_split()` with uniform noise (returns None, all silhouettes < gate)
- `spectral_split()` with degenerate input (identical embeddings, returns None gracefully)
- `spectral_split()` rejects k where any group has < 3 members
- Candidate promotion: coherence 0.45 → promoted to active
- Candidate rejection: coherence 0.18 → rejected, members reassigned
- Candidate rejection: all candidates rejected → `split_fully_reversed` event
- Candidate with 0 members → archived immediately

### Integration tests

- End-to-end: create 30-member low-coherence cluster → warm path triggers split → spectral produces 3 candidates → next warm cycle promotes all 3
- End-to-end: split produces 3 candidates, 1 rejected → 2 promoted, rejected members reassigned
- Existing split tests updated: expect `state="candidate"` instead of `state="active"`
- Q-gate rejection: split candidates created, Q-gate rejects → rollback, candidates never committed, split_failures incremented

---

## 7. Files Modified

| File | Change |
|------|--------|
| `backend/app/services/taxonomy/clustering.py` | Add `spectral_split()` function |
| `backend/app/services/taxonomy/split.py` | Swap HDBSCAN for spectral primary, remove K-Means fallback, children as candidates |
| `backend/app/services/taxonomy/warm_phases.py` | Add `phase_evaluate_candidates()` after Phase 0 |
| `backend/app/services/taxonomy/warm_path.py` | Call candidate evaluation in warm path flow |
| `backend/app/services/taxonomy/_constants.py` | Add spectral + candidate constants |
| `backend/app/services/taxonomy/engine.py` | Sub-domain candidates consistent treatment |
| `frontend/src/lib/components/taxonomy/ActivityPanel.svelte` | candidate op chip, keyMetric, decisionColor |
| `frontend/src/lib/components/taxonomy/TopologyRenderer.ts` | Candidate node opacity (40%) |
| `frontend/src/lib/components/layout/ClusterNavigator.svelte` | Candidate filter tab with count badge |
| `frontend/src/lib/utils/colors.ts` | `stateColor("candidate")` mapping |
| `frontend/src/routes/app/+page.svelte` | Toast notifications for candidate events |
| `backend/tests/taxonomy/test_spectral_split.py` | New: spectral algorithm unit tests |
| `backend/tests/taxonomy/test_candidate_lifecycle.py` | New: candidate promotion/rejection tests |
| `backend/tests/taxonomy/test_engine_warm_path.py` | Update existing split tests for candidate state |
