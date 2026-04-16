# Enriched Vocabulary Generation with Embedding-Aware Context

**Goal:** Give Haiku the embedding geometry, member intents, and existing domain_raw qualifier distribution as input context when generating qualifier vocabulary, so it produces naturally discriminative keyword groups aligned with actual cluster boundaries — in a single LLM call.

**Problem:** Haiku currently generates qualifier vocabulary from cluster labels alone (3-5 word summaries). It has no visibility into:
- How close or far apart clusters are in embedding space
- What the actual prompt content looks like (intent_labels of members)
- What qualifiers the system has already assigned (domain_raw distribution)

This produces vocabulary where qualifier groups may overlap in embedding space (keywords for "deployment-orchestra" embed similarly to "pipeline-testing"), fail to discriminate between clusters that ARE semantically different, or miss specializations visible in the data but not in the labels.

**Solution:** Enrich the `generate_qualifier_vocabulary()` input with three additional signals: pairwise centroid cosine similarity matrix, per-cluster member intent_labels, and per-cluster domain_raw qualifier distribution. Haiku sees the actual topology and generates vocabulary that respects embedding geometry. A post-generation quality metric (no retry, observability only) validates discriminative power.

---

## Architecture

### Enriched Input Context

Replace the current `cluster_labels: list[tuple[str, int]]` parameter with a rich context structure:

```python
@dataclass
class ClusterVocabContext:
    label: str                          # cluster label (existing)
    member_count: int                   # optimization count (existing)
    intent_labels: list[str]            # member intent_labels, deduplicated, up to 10 most common
    qualifier_distribution: dict[str, int]  # domain_raw qualifier → count (e.g., {"auth": 12, "api": 3})
```

Plus a pairwise centroid cosine similarity matrix: `list[list[float]] | None` (NxN where N = number of clusters with non-NULL centroids). `None` when centroids unavailable.

### Enriched Haiku Prompt

The system prompt instructs Haiku to:
- Group clusters with cosine > 0.7 into the same qualifier group (they're semantically overlapping)
- Separate clusters with cosine < 0.3 into different qualifier groups (they're genuinely distinct)
- Use the intent_labels to find discriminating keywords (words that appear in one cluster group but not others)
- Respect existing domain_raw qualifier distributions where strong consensus exists (high-count qualifiers indicate established classifications)

The user message contains the enriched per-cluster context and the similarity matrix in a readable format.

### Post-Generation Quality Metric

After Haiku returns vocabulary, embed each qualifier group's keywords via `engine._embedding.aembed_single()` (the quality metric runs in engine.py which has access to the embedding service) and compute pairwise cosine between groups. This produces a quality score:

- `vocab_quality = 1.0 - max_pairwise_cosine` (range 0.0-1.0, higher = more discriminative)
- Quality ≥ 0.3: Good — groups are discriminative
- Quality 0.1-0.3: Acceptable — log warning with overlapping pair names
- Quality < 0.1: Poor — log WARNING with full details

**No retry, no rejection.** The metric is observability only. The vocabulary is accepted regardless of quality score. Consistently poor scores signal cluster structure issues, not vocabulary generation bugs.

---

## Data Collection

### Optimization data (intent_labels + domain_raw)

**One additional query during the vocab generation pass** to collect intent_labels and domain_raw values:

```sql
SELECT cluster_id, intent_label, domain_raw 
FROM optimizations 
WHERE cluster_id IN (:child_ids)
```

**NULL handling:** Both `intent_label` and `domain_raw` are nullable. Filter NULLs before processing. Clusters where all members have NULL intent_labels get an empty `intent_labels` list (Haiku receives only the cluster label for that cluster — graceful degradation).

**Intent label selection:** Query ALL intent_labels for the cluster, deduplicate (case-insensitive), then take the 10 most COMMON (by frequency, not recency). Frequency-based selection represents cluster content better than recency — a burst of similar recent prompts shouldn't overshadow the cluster's established topics.

**Scope:** The query uses `child_ids` from the vocab generation pass — direct children of the domain node only. This differs from the qualifier scan's expanded scope (which includes sub-domain descendants). The two passes collect data INDEPENDENTLY — no shared-collection optimization. The vocab pass needs enrichment for current direct children; the qualifier scan needs full descendant coverage.

### Centroid similarity matrix

**Requires expanding the existing cluster info query** to also fetch `PromptCluster.centroid_embedding`. The current vocab pass query (engine.py) selects only `label` and `member_count`. Add `centroid_embedding` to the SELECT.

**NULL centroid handling:** Clusters with NULL `centroid_embedding` (candidates, newly created) are excluded from the similarity matrix. The matrix is computed only from clusters with valid centroids. For domains where all clusters lack centroids, pass `similarity_matrix=None` (Haiku generates without geometric context).

**Computation:** L2-normalize each centroid, stack into matrix, compute `centroids @ centroids.T`. Pure numpy, ~1ms for typical domain sizes (3-10 clusters).

### Token budget

Realistic estimate for a domain with 6 clusters:
- Cluster labels + member counts: ~50 tokens (existing)
- 6 clusters × 10 intents × ~5 tokens each: ~300 tokens
- Similarity matrix (6×6 rendered as "C1↔C2: 0.85"): ~100 tokens
- Per-cluster qualifier distribution: ~60 tokens
- Formatting overhead: ~50 tokens
- **Total additional: ~510 tokens** (well within Haiku's capacity)

For large domains (10+ clusters), total could reach ~1000 additional tokens. Still within Haiku's practical input budget.

---

## Function Signature Change

```python
# Current
async def generate_qualifier_vocabulary(
    provider: LLMProvider | None,
    domain_label: str,
    cluster_labels: list[tuple[str, int]],
    model: str,
) -> dict[str, list[str]]

# New
async def generate_qualifier_vocabulary(
    provider: LLMProvider | None,
    domain_label: str,
    cluster_contexts: list[ClusterVocabContext],
    similarity_matrix: list[list[float]] | None,
    model: str,
) -> dict[str, list[str]]
```

**Backward compatibility:** The caller in the vocab generation pass (engine.py) already queries cluster info. It now additionally queries intent_labels and domain_raw values, expands the cluster info query to include centroids, computes the matrix, and passes the enriched context. The function signature change is internal — no external API impact. Two test mocks in `test_sub_domain_lifecycle.py` (lines ~460 and ~737) need updated signatures.

**Graceful degradation:** If the enriched query fails (DB error), fall back to constructing `ClusterVocabContext` with only `label` and `member_count` (current behavior). If the similarity matrix computation fails, pass `None` (Haiku generates without geometric context). The enriched path is additive — failure degrades to current behavior, never blocks.

---

## Observability

### Events

| Path | Op | Decision | Context |
|------|----|----------|---------|
| `warm` | `discover` | `vocab_generated_enriched` | `{domain, groups, quality_score, max_pairwise_cosine, clusters_with_intents: int, clusters_with_centroids: int, matrix_coverage_pct: float, generation_ms, quality_ms}` |
| `warm` | `discover` | `vocab_quality_assessed` | `{domain, quality_score, max_pairwise_cosine, overlapping_pair: [name1, name2] \| null}` |
| `warm` | `discover` | `vocab_enrichment_fallback` | `{domain, reason: "query_failed"\|"matrix_failed"\|"no_centroids", error}` |

### Health Endpoint

Extend existing `qualifier_vocab` stats with:
```python
"avg_vocab_quality": float | None,  # mean quality score across domains with vocabulary
```

### Error Handling

| Component | Failure Mode | Handling |
|-----------|-------------|----------|
| Intent/domain_raw query | DB error | WARNING log, `vocab_enrichment_fallback` event, fall back to label-only context |
| Centroid query | NULL centroids | Exclude from matrix. All NULL → `similarity_matrix=None`, `vocab_enrichment_fallback` event |
| Centroid matrix computation | numpy error | WARNING log, pass `similarity_matrix=None` |
| Quality metric computation | Embedding/numpy error | WARNING log, skip metric, accept vocabulary as-is |
| Haiku call | LLM timeout/error | Existing behavior: use cached vocab, set `_maintenance_pending` |

**Principle:** Enrichment is additive. Every failure degrades to current behavior. Never blocks vocabulary generation. Never crashes Phase 5.

---

## Changes

| File | Change |
|------|--------|
| `backend/app/services/taxonomy/labeling.py` | Update `generate_qualifier_vocabulary()` signature and Haiku prompt with enriched context. Add `ClusterVocabContext` dataclass. |
| `backend/app/services/taxonomy/engine.py` | In vocab generation pass: expand cluster query to include `centroid_embedding`, query intent_labels + domain_raw, compute centroid matrix, construct `ClusterVocabContext` list, pass to enriched `generate_qualifier_vocabulary()`. Add quality metric computation after generation (uses `self._embedding` for qualifier group embedding). |
| `backend/app/services/domain_signal_loader.py` | Extend `stats()` with `avg_vocab_quality` field |
| `backend/app/routers/health.py` | Wire `avg_vocab_quality` into health response |
| `backend/tests/taxonomy/test_sub_domain_lifecycle.py` | Update 2 test mocks for new `generate_qualifier_vocabulary()` signature |

---

## Testing Strategy

### Unit Tests
- `generate_qualifier_vocabulary()` with enriched context — verify Haiku receives cluster intents and similarity matrix in prompt
- `generate_qualifier_vocabulary()` with `similarity_matrix=None` — verify fallback to label-only prompt
- Quality metric computation — verify correct score from known embeddings (orthogonal groups → quality 1.0, identical groups → quality 0.0)
- `ClusterVocabContext` construction — verify intent deduplication (frequency-based, not recency), 10-item cap, NULL filtering
- `qualifier_distribution` as `dict[str, int]` — verify counts computed correctly from domain_raw values

### Integration Tests
- Full Phase 5 vocab generation with enriched context — verify quality metric logged in events
- Enriched query failure → graceful fallback to label-only generation
- Matrix computation failure → generation succeeds without geometric context
- Quality metric with real Haiku output — verify score is between 0.0 and 1.0
- 2-cluster minimum case — verify Haiku receives single similarity value and produces usable vocabulary
- All-NULL intents for a cluster — verify empty `intent_labels` list, Haiku receives label-only for that cluster
- Mixed NULL/non-NULL centroids — verify matrix computed from non-NULL only, NULL clusters excluded

### Regression Tests
- Existing vocabulary generation tests pass with new signature (2 test mocks updated)
- Phase 5 cycle time stays within acceptable bounds (enriched query + matrix add ~50-100ms)
