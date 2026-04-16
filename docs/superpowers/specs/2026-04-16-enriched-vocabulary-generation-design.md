# Enriched Vocabulary Generation with Embedding-Aware Context

**Goal:** Give Haiku the embedding geometry, member intents, and existing domain_raw qualifiers as input context when generating qualifier vocabulary, so it produces naturally discriminative keyword groups aligned with actual cluster boundaries — in a single LLM call.

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
    label: str                       # cluster label (existing)
    member_count: int                # optimization count (existing)
    intent_labels: list[str]         # member intent_labels, deduplicated, up to 10
    domain_raw_qualifiers: list[str] # existing domain_raw qualifiers with counts
```

Plus a pairwise centroid cosine similarity matrix: `list[list[float]]` (NxN where N = number of clusters).

### Enriched Haiku Prompt

The system prompt instructs Haiku to:
- Group clusters with cosine > 0.7 into the same qualifier group (they're semantically overlapping)
- Separate clusters with cosine < 0.3 into different qualifier groups (they're genuinely distinct)
- Use the intent_labels to find discriminating keywords (words that appear in one cluster group but not others)
- Respect existing domain_raw qualifier distributions where strong consensus exists

The user message contains the enriched per-cluster context and the similarity matrix in a readable format.

### Post-Generation Quality Metric

After Haiku returns vocabulary, embed each qualifier group's keywords (via qualifier embedding cache) and compute pairwise cosine between groups. This produces a quality score:

- `vocab_quality = 1.0 - max_pairwise_cosine` (range 0.0-1.0, higher = more discriminative)
- Quality ≥ 0.3: Good — groups are discriminative
- Quality 0.1-0.3: Acceptable — log warning with overlapping pair names
- Quality < 0.1: Poor — log WARNING with full details

**No retry, no rejection.** The metric is observability only. The vocabulary is accepted regardless of quality score. Consistently poor scores signal cluster structure issues, not vocabulary generation bugs.

---

## Data Collection

**One additional query during the vocab generation pass** to collect intent_labels and domain_raw values for all optimizations under the domain's child clusters:

```sql
SELECT cluster_id, intent_label, domain_raw 
FROM optimizations 
WHERE cluster_id IN (:child_ids)
```

This is the SAME data the Phase 5 qualifier scan queries later. Collect once at the start of the vocab generation pass and pass downstream to avoid a redundant DB round-trip.

**Centroid similarity matrix:** Computed from cluster centroids already available in the vocab generation pass. Pure numpy: `centroids @ centroids.T`. No additional DB query.

**Intent label budget:** Up to 10 deduplicated intent_labels per cluster. For clusters with >10 members, sample the 10 most recent (they reflect the current content better than old ones). Total additional context: ~200-300 tokens for a typical domain with 6 clusters.

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

**Backward compatibility:** The caller in `_propose_sub_domains()` (engine.py) already queries cluster info. It now additionally queries intent_labels and domain_raw values, computes the centroid matrix, and passes the enriched context. The function signature change is internal — no external API impact.

**Graceful degradation:** If the enriched query fails (DB error), fall back to constructing `ClusterVocabContext` with only `label` and `member_count` (current behavior). If the similarity matrix computation fails, pass `None` (Haiku generates without geometric context). The enriched path is additive — failure degrades to current behavior, never blocks.

---

## Observability

### Events

| Path | Op | Decision | Context |
|------|----|----------|---------|
| `warm` | `discover` | `vocab_generated_enriched` | `{domain, groups, quality_score, max_pairwise_cosine, intent_labels_provided: int, matrix_size: int, generation_ms}` |
| `warm` | `discover` | `vocab_quality_assessed` | `{domain, quality_score, max_pairwise_cosine, overlapping_pair: [name1, name2] \| null}` |
| `warm` | `discover` | `vocab_enrichment_fallback` | `{domain, reason: "query_failed"\|"matrix_failed", error}` |

### Health Endpoint

Extend existing `qualifier_vocab` stats with:
```python
"avg_vocab_quality": float | None,  # mean quality score across domains with vocabulary
```

### Error Handling

| Component | Failure Mode | Handling |
|-----------|-------------|----------|
| Intent/domain_raw query | DB error | WARNING log, `vocab_enrichment_fallback` event, fall back to label-only context |
| Centroid matrix computation | numpy error | WARNING log, pass `similarity_matrix=None` to Haiku |
| Quality metric computation | Embedding/numpy error | WARNING log, skip metric, accept vocabulary as-is |
| Haiku call | LLM timeout/error | Existing behavior: use cached vocab, set `_maintenance_pending` |

**Principle:** Enrichment is additive. Every failure degrades to current behavior. Never blocks vocabulary generation. Never crashes Phase 5.

---

## Changes

| File | Change |
|------|--------|
| `backend/app/services/taxonomy/labeling.py` | Update `generate_qualifier_vocabulary()` signature and Haiku prompt with enriched context. Add `ClusterVocabContext` dataclass. |
| `backend/app/services/taxonomy/engine.py` | In vocab generation pass: query intent_labels + domain_raw, compute centroid matrix, construct `ClusterVocabContext` list, pass to enriched `generate_qualifier_vocabulary()`. Add quality metric computation after generation. |
| `backend/app/services/domain_signal_loader.py` | Extend `stats()` with `avg_vocab_quality` field |
| `backend/app/routers/health.py` | Wire `avg_vocab_quality` into health response |

---

## Testing Strategy

### Unit Tests
- `generate_qualifier_vocabulary()` with enriched context — verify Haiku receives cluster intents and similarity matrix in prompt
- `generate_qualifier_vocabulary()` with `similarity_matrix=None` — verify fallback to label-only prompt
- Quality metric computation — verify correct score from known embeddings (orthogonal groups → quality 1.0, identical groups → quality 0.0)
- `ClusterVocabContext` construction — verify intent deduplication and 10-item cap

### Integration Tests
- Full Phase 5 vocab generation with enriched context — verify quality metric logged in events
- Enriched query failure → graceful fallback to label-only generation
- Matrix computation failure → generation succeeds without geometric context
- Quality metric with real Haiku output — verify score is between 0.0 and 1.0

### Regression Tests
- Existing vocabulary generation tests pass with new signature (backward compat via degradation)
- Phase 5 cycle time stays within acceptable bounds (enriched query + matrix add ~50ms)
