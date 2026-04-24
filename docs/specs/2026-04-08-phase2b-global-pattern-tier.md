# Phase 2B Design Spec: Global Pattern Tier

**Date:** 2026-04-08
**ADR:** [ADR-005](../adr/ADR-005-taxonomy-scaling-architecture.md) (Phase 2, item 4 — Section 6)
**Depends on:** Phase 2A (multi-project isolation) — project nodes exist, project_id populated on Optimization, cross-project clusters possible
**Status:** Shipped (v0.4.0, B8 ). `GlobalPattern` model + `global_patterns.py` service (promotion / validation / retire lifecycle) + 500-row cap with LRU eviction + dual-gate promotion (`GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS=5` + `MIN_PROJECTS=2`) + 1.3x injection boost + every-10-cycle validation all live. `GlobalPattern.state` hysteresis (demote <5.0, re-promote ≥6.0 with 1.0-pt gap). `OptimizationPattern.global_pattern_id` FK for provenance.

## Problem

MetaPatterns are cluster-scoped. When a cluster is archived, its patterns are lost. Cross-cluster injection (`global_source_count >= 3`) partially addresses this, but patterns are still tied to living clusters. A technique that proves valuable across 5+ clusters in 2+ projects deserves permanent storage that survives cluster lifecycle.

## Design Overview

```
MetaPattern (cluster-scoped, ephemeral)
    |
    v Promotion (Phase 4.5, every 10th warm cycle)
    |
GlobalPattern (cross-project, durable, 500 cap)
    |
    v Injection (alongside MetaPattern cross-cluster injection)
    |
Optimization prompt (1.3x relevance boost for global patterns)
    |
    v Validation (same Phase 4.5)
    |
Demotion / Re-promotion / Retirement (hysteresis: 5.0 <-> 6.0)
```

## 1. Promotion Pipeline

### Trigger

New warm path **Phase 4.5** — runs after Phase 4 (Refresh) and before Phase 5 (Discover). Gated:
- Every 10th warm cycle (`engine._warm_path_age % GLOBAL_PATTERN_CYCLE_INTERVAL == 0`)
- Minimum 30 minutes since last Phase 4.5 run (wall-clock gate via `engine._last_global_pattern_check`)
- Both conditions must be true.

**Note:** The ADR says "warm path Phase 4" for promotion. This spec places it at Phase 4.5 because Phase 4 (Refresh) already exists and handles pattern extraction. Phase 4.5 runs immediately after, using the freshly-extracted patterns.

### Cross-cluster sibling discovery algorithm

The `global_source_count` on a MetaPattern records how many clusters independently discovered a similar pattern, but does NOT record which clusters or projects. To find cross-project candidates, we need to discover MetaPattern siblings across clusters:

**Step 1: Find high-impact MetaPatterns**
```python
candidates = await db.execute(
    select(MetaPattern).where(
        MetaPattern.global_source_count >= GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS,
        MetaPattern.embedding.isnot(None),
    )
)
```

**Step 2: For each candidate, find siblings via embedding similarity**
```python
for candidate in candidates:
    candidate_emb = np.frombuffer(candidate.embedding, dtype=np.float32)

    # Find all MetaPatterns with cosine >= 0.90 to this candidate
    # (these are the "same technique" discovered independently)
    all_patterns = await db.execute(
        select(MetaPattern).where(
            MetaPattern.id != candidate.id,
            MetaPattern.embedding.isnot(None),
        )
    )
    siblings = []
    for mp in all_patterns.scalars():
        mp_emb = np.frombuffer(mp.embedding, dtype=np.float32)
        sim = cosine_similarity(candidate_emb, mp_emb)
        if sim >= GLOBAL_PATTERN_DEDUP_COSINE:  # 0.90
            siblings.append(mp)

    # Collect distinct cluster_ids and project_ids across all siblings + candidate
    all_cluster_ids = {candidate.cluster_id} | {s.cluster_id for s in siblings}
    all_project_ids = set()
    for cid in all_cluster_ids:
        # Get project_id from any optimization in this cluster
        pid = (await db.execute(
            select(Optimization.project_id)
            .where(Optimization.cluster_id == cid, Optimization.project_id.isnot(None))
            .limit(1)
        )).scalar()
        if pid:
            all_project_ids.add(pid)
```

**Step 3: Check promotion criteria**
```python
    if len(all_project_ids) < GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS:
        continue  # not cross-project enough
    if len(all_cluster_ids) < GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS:
        continue  # not enough independent sources

    # Check avg_score across source clusters (per-cluster gate, not mean)
    avg_scores = []
    for cid in all_cluster_ids:
        cluster = await db.get(PromptCluster, cid)
        if cluster and cluster.state not in EXCLUDED_STRUCTURAL_STATES:
            if (cluster.avg_score or 0) >= GLOBAL_PATTERN_PROMOTION_MIN_SCORE:
                avg_scores.append(cluster.avg_score)

    if len(avg_scores) < GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS:
        continue  # not enough qualifying clusters

    avg_cluster_score = sum(avg_scores) / len(avg_scores)
```

**Clarification on the 6.0 threshold:** This is a per-cluster gate. Each source cluster must individually have `avg_score >= 6.0`. The `avg_cluster_score` stored on the GlobalPattern is the mean of the qualifying clusters. This is the stricter interpretation (prevents one high-scoring cluster from carrying several low-scoring ones).

**Step 4: Dedup against existing GlobalPatterns**
```python
    # Check for existing GlobalPattern with cosine >= 0.90
    existing_gps = await db.execute(
        select(GlobalPattern).where(
            GlobalPattern.state.in_(["active", "demoted"]),
            GlobalPattern.embedding.isnot(None),
        )
    )
    dedup_match = None
    for gp in existing_gps.scalars():
        gp_emb = np.frombuffer(gp.embedding, dtype=np.float32)
        if cosine_similarity(candidate_emb, gp_emb) >= GLOBAL_PATTERN_DEDUP_COSINE:
            dedup_match = gp
            break

    if dedup_match:
        # Update existing — union source lists, refresh metadata
        dedup_match.source_cluster_ids = list(
            set(dedup_match.source_cluster_ids) | all_cluster_ids
        )
        dedup_match.source_project_ids = list(
            set(dedup_match.source_project_ids) | all_project_ids
        )
        dedup_match.cross_project_count = len(set(dedup_match.source_project_ids))
        dedup_match.global_source_count = len(set(dedup_match.source_cluster_ids))
        dedup_match.avg_cluster_score = avg_cluster_score
        dedup_match.last_validated_at = utcnow()
        # Preserve promoted_at (original promotion timestamp)
    else:
        # Create new GlobalPattern
        gp = GlobalPattern(
            pattern_text=candidate.pattern_text,
            embedding=candidate.embedding,  # already bytes
            source_cluster_ids=list(all_cluster_ids),
            source_project_ids=list(all_project_ids),
            cross_project_count=len(all_project_ids),
            global_source_count=len(all_cluster_ids),
            avg_cluster_score=avg_cluster_score,
            state="active",
        )
        db.add(gp)
```

**Performance note:** The sibling discovery loop is O(candidates * all_patterns). For 500 MetaPatterns with `global_source_count >= 5`, this is ~250K cosine comparisons — fast with numpy vectorization. For larger taxonomies, batch the cosine computation using the MetaPattern embedding matrix.

### Phase 4.5 ignores the dirty set

Phase 4.5 is a global scan — it evaluates all MetaPatterns regardless of dirty_ids. This is acceptable because it runs infrequently (every 10th cycle, 30-min gate).

## 2. Injection

### Where

In `auto_inject_patterns()` (pattern_injection.py), after the existing cross-cluster MetaPattern injection section.

### Query

```python
global_patterns = await db.execute(
    select(GlobalPattern).where(
        GlobalPattern.state == "active",
        GlobalPattern.embedding.isnot(None),
    )
)
```

Demoted patterns are excluded entirely (not injected at all, not just de-boosted). This matches the injection query filtering by `state == "active"`.

### Relevance scoring

Same cosine-similarity scoring as MetaPattern injection, but with 1.3x multiplier:

```python
for gp in global_patterns.scalars():
    gp_emb = np.frombuffer(gp.embedding, dtype=np.float32)
    sim = cosine_similarity(prompt_embedding, gp_emb)
    relevance = sim * GLOBAL_PATTERN_RELEVANCE_BOOST
    if relevance >= CROSS_CLUSTER_RELEVANCE_FLOOR:
        injected.append(InjectedPattern(
            text=gp.pattern_text,
            relevance=relevance,
            source="global",
            source_id=gp.id,
        ))
```

### InjectedPattern dataclass change

Add two new fields to `InjectedPattern` in `pattern_injection.py`:

```python
@dataclass
class InjectedPattern:
    text: str
    relevance: float
    cluster_id: str = ""       # existing
    cluster_label: str = ""    # existing
    source: str = "cluster"    # NEW: "cluster" | "global"
    source_id: str = ""        # NEW: MetaPattern.id or GlobalPattern.id
```

### Formatting

`format_injected_patterns()` checks the `source` field to separate output:

```python
cluster_patterns = [p for p in patterns if p.source == "cluster"]
global_patterns = [p for p in patterns if p.source == "global"]

text = ""
if cluster_patterns:
    text += "## Relevant Techniques\n"
    for p in cluster_patterns:
        text += f"- {p.text} (relevance: {p.relevance:.2f})\n"

if global_patterns:
    text += "\n## Proven Cross-Project Techniques\n"
    for p in global_patterns:
        text += f"- {p.text} (relevance: {p.relevance:.2f})\n"
```

### Provenance

Global pattern injection provenance uses `OptimizationPattern` with:
- `relationship = "global_injected"` (new relationship type)
- `cluster_id` = the GlobalPattern's first source cluster ID (NOT NULL — satisfies the existing NOT NULL constraint)
- `global_pattern_id` = the GlobalPattern's ID (new nullable FK)

This avoids changing `cluster_id` to nullable. The `cluster_id` value is approximate (first source cluster) but sufficient for provenance tracking. The `global_pattern_id` FK is the precise reference.

## 3. Validation

### When

Same Phase 4.5 trigger as promotion. Validation runs after promotion within the same phase.

### Logic

For each GlobalPattern with `state IN ('active', 'demoted')`:

1. **Recompute avg_cluster_score:** Query source clusters by `source_cluster_ids`. Compute mean of `avg_score` for clusters that still exist and are not archived. Update `avg_cluster_score`.

2. **Demotion check:** If `avg_cluster_score < 5.0` and `state == 'active'`:
   - Set `state = 'demoted'`. Pattern excluded from injection.
   - Log `global_pattern/demoted` event.

3. **Re-promotion check:** If `avg_cluster_score >= 6.0` and `state == 'demoted'`:
   - Set `state = 'active'`. Pattern returns to injection pool.
   - Log `global_pattern/re_promoted` event.
   - The 1.0-point hysteresis gap (5.0 demotion, 6.0 re-promotion) prevents oscillation.

4. **Retirement check:** If ALL source clusters are archived AND `last_validated_at` < now - 30 days:
   - Set `state = 'retired'`.
   - Log `global_pattern/retired` event.
   - Retired patterns are never injected or validated again. Kept for audit trail.

5. Update `last_validated_at = utcnow()` for all validated patterns.

## 4. Retention Policy

### Cap

Maximum 500 GlobalPatterns with `state IN ('active', 'demoted')`. Retired patterns excluded from the cap.

### Eviction

When cap is exceeded after promotion:

1. Evict `demoted` patterns first, ordered by `last_validated_at ASC` (least recently validated).
2. If still over cap, evict `active` patterns by `last_validated_at ASC`.
3. Eviction sets `state = 'retired'` (not DELETE — audit trail preserved).

## 5. New Constants

```python
# _constants.py additions
GLOBAL_PATTERN_RELEVANCE_BOOST: float = 1.3
GLOBAL_PATTERN_CAP: int = 500
GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS: int = 5   # distinct source clusters
GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS: int = 2   # distinct source projects
GLOBAL_PATTERN_PROMOTION_MIN_SCORE: float = 6.0  # per-cluster avg_score gate
GLOBAL_PATTERN_DEMOTION_SCORE: float = 5.0       # mean score demotion threshold
GLOBAL_PATTERN_DEDUP_COSINE: float = 0.90        # sibling/dedup similarity threshold
GLOBAL_PATTERN_CYCLE_INTERVAL: int = 10           # every Nth warm cycle
GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES: int = 30   # minimum minutes between Phase 4.5 runs
```

## 6. Model Changes

### OptimizationPattern

Add optional FK for global pattern provenance:

```python
global_pattern_id = Column(String(36), ForeignKey("global_patterns.id"), nullable=True)
```

**Migration:** ALTER TABLE `optimization_patterns` ADD COLUMN `global_pattern_id VARCHAR(36)`. Wrapped in try/except in lifespan (same pattern as existing Phase 1 migrations).

### InjectedPattern dataclass

Add `source: str = "cluster"` and `source_id: str = ""` fields.

### TaxonomyEngine

Add to `__init__()`:

```python
self._last_global_pattern_check: float = 0.0  # monotonic timestamp
```

## 7. Observability

### Event logger

4 new decision events via `TaxonomyEventLogger.log_decision()`:

| Event | Path | Op | Context |
|-------|------|----|---------|
| `global_pattern/promoted` | warm | global_pattern | pattern_text, source_clusters, source_projects, avg_score |
| `global_pattern/demoted` | warm | global_pattern | pattern_id, old_score, new_score |
| `global_pattern/re_promoted` | warm | global_pattern | pattern_id, score |
| `global_pattern/retired` | warm | global_pattern | pattern_id, reason (all_archived / evicted) |

### Health endpoint

Add to `GET /api/health` response:

```json
{
  "global_patterns": {
    "active": 42,
    "demoted": 3,
    "retired": 15,
    "total": 60
  }
}
```

## 8. Validation

### Seed targets
- 1K+ optimizations across 3 projects, 200+ clusters.
- Ensure enough cross-project MetaPattern overlap to trigger promotion (seed similar prompt types in 2+ projects).

### Assertions
- GlobalPatterns created from MetaPattern sibling groups spanning 2+ projects with 5+ source clusters.
- Each source cluster individually has avg_score >= 6.0.
- Deduplication: cosine >= 0.90 updates existing rather than creating duplicate.
- Injection: global patterns appear in optimization prompts with 1.3x boost.
- Formatting: "Proven Cross-Project Techniques" section appears after cluster patterns.
- Provenance: OptimizationPattern records with relationship="global_injected" and cluster_id set (NOT NULL).
- Demotion: pattern with avg_cluster_score < 5.0 loses active status and stops being injected.
- Re-promotion: demoted pattern with avg_cluster_score >= 6.0 regains active status.
- Retirement: all sources archived + 30 days -> retired.
- Cap: promotion beyond 500 evicts demoted LRU, then active LRU.

### Test files
- `tests/taxonomy/test_global_pattern_promotion.py` — sibling discovery, promotion criteria, dedup, embedding
- `tests/taxonomy/test_global_pattern_injection.py` — injection alongside MetaPattern, 1.3x boost, formatting
- `tests/taxonomy/test_global_pattern_validation.py` — demotion, re-promotion, retirement, hysteresis
- `tests/taxonomy/test_global_pattern_retention.py` — cap enforcement, eviction ordering
- `tests/taxonomy/test_global_pattern_provenance.py` — OptimizationPattern records, cluster_id NOT NULL
