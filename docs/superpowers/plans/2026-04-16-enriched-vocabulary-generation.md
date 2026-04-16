# Enriched Vocabulary Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich Haiku's vocabulary generation input with cluster centroid similarity matrix, member intent_labels, and domain_raw qualifier distribution so it produces naturally discriminative keyword groups in a single LLM call.

**Architecture:** Add `ClusterVocabContext` dataclass to `labeling.py`. Expand the vocab generation pass in `engine.py` to query `centroid_embedding`, `intent_label`, and `domain_raw`, compute pairwise centroid cosine matrix, and pass enriched context to `generate_qualifier_vocabulary()`. Post-generation quality metric computed in `engine.py` using the embedding service. Observability via taxonomy events and health endpoint.

**Tech Stack:** Python 3.12, SQLAlchemy async, numpy, Haiku LLM, existing taxonomy infrastructure

**Spec:** `docs/superpowers/specs/2026-04-16-enriched-vocabulary-generation-design.md`

---

## File Structure

| File | Change | Responsibility |
|------|--------|---------------|
| `backend/app/services/taxonomy/labeling.py` | Modify | Add `ClusterVocabContext`, update `generate_qualifier_vocabulary()` signature and prompt |
| `backend/app/services/taxonomy/engine.py` | Modify | Enrich vocab pass with centroid query + intent/domain_raw collection + matrix computation + quality metric |
| `backend/app/services/domain_signal_loader.py` | Modify | Add `avg_vocab_quality` to `stats()` |
| `backend/app/routers/health.py` | Modify | Wire `avg_vocab_quality` |
| `backend/tests/taxonomy/test_sub_domain_lifecycle.py` | Modify | Update 2 test mocks for new signature |

---

### Task 1: Add ClusterVocabContext and update generate_qualifier_vocabulary()

**Files:**
- Modify: `backend/app/services/taxonomy/labeling.py`

- [ ] **Step 1: Add ClusterVocabContext dataclass**

In `labeling.py`, before the `_QualifierGroup` class (around line 138), add:

```python
from dataclasses import dataclass, field


@dataclass
class ClusterVocabContext:
    """Enriched per-cluster context for vocabulary generation."""

    label: str
    member_count: int
    intent_labels: list[str] = field(default_factory=list)
    qualifier_distribution: dict[str, int] = field(default_factory=dict)
```

- [ ] **Step 2: Update function signature**

Change `generate_qualifier_vocabulary()` from:

```python
async def generate_qualifier_vocabulary(
    provider: LLMProvider | None,
    domain_label: str,
    cluster_labels: list[tuple[str, int]],
    model: str,
) -> dict[str, list[str]]:
```

To:

```python
async def generate_qualifier_vocabulary(
    provider: LLMProvider | None,
    domain_label: str,
    cluster_contexts: list[ClusterVocabContext],
    similarity_matrix: list[list[float]] | None,
    model: str,
) -> dict[str, list[str]]:
```

Update the minimum check from `len(cluster_labels) < 2` to `len(cluster_contexts) < 2`.

- [ ] **Step 3: Build enriched prompt content**

Replace the current `cluster_block` construction (lines 180-182):

```python
    cluster_block = "\n".join(
        f"- {label} ({count} members)" for label, count in cluster_labels
    )
```

With:

```python
    lines = []
    for i, ctx in enumerate(cluster_contexts):
        parts = [f"- C{i+1}: \"{ctx.label}\" ({ctx.member_count} members)"]
        if ctx.intent_labels:
            parts.append(f"  Intents: {', '.join(ctx.intent_labels[:10])}")
        if ctx.qualifier_distribution:
            dist = ', '.join(f'{q}({c})' for q, c in sorted(ctx.qualifier_distribution.items(), key=lambda x: -x[1])[:5])
            parts.append(f"  Existing qualifiers: {dist}")
        lines.append('\n'.join(parts))
    cluster_block = '\n'.join(lines)

    # Add similarity matrix if available
    matrix_block = ""
    if similarity_matrix and len(similarity_matrix) >= 2:
        matrix_lines = ["Cluster similarity (cosine):"]
        for i in range(len(similarity_matrix)):
            for j in range(i + 1, len(similarity_matrix)):
                sim = similarity_matrix[i][j]
                hint = " (very similar)" if sim > 0.7 else " (distinct)" if sim < 0.3 else ""
                matrix_lines.append(f"  C{i+1}↔C{j+1}: {sim:.2f}{hint}")
        matrix_block = '\n'.join(matrix_lines)
```

- [ ] **Step 4: Update the Haiku system prompt and user message**

Replace the system_prompt and user_message in the `call_provider_with_retry` call:

```python
        system_prompt=(
            "You are a taxonomy analyst. Given clusters within a domain with their "
            "member intents, existing qualifier signals, and pairwise embedding similarity, "
            "identify 3-6 thematic specializations. For each, produce a short name "
            "(1-2 lowercase words) and 5-10 lowercase keywords that signal that "
            "specialization in a user's prompt. Keywords should DISCRIMINATE between "
            "groups — choose words that appear in one specialization but not others. "
            "Use the similarity matrix to guide grouping: clusters with cosine > 0.7 "
            "should typically belong to the same group. "
            "Do not include the domain name itself as a keyword."
        ),
        user_message=(
            f"Domain: {domain_label}\n\n"
            f"Clusters:\n{cluster_block}\n\n"
            + (f"{matrix_block}\n\n" if matrix_block else "")
            + "Generate qualifier groups for this domain's specializations."
        ),
```

- [ ] **Step 5: Update docstring**

Update the docstring to reflect the new parameters and enriched context.

- [ ] **Step 6: Run existing tests (they'll fail due to signature change)**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py -v --tb=short 2>&1 | tail -5`
Expected: 2 failures from test mocks with old signature

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/taxonomy/labeling.py
git commit -m "feat(taxonomy): enrich generate_qualifier_vocabulary() with centroid + intent context"
```

---

### Task 2: Update test mocks for new signature

**Files:**
- Modify: `backend/tests/taxonomy/test_sub_domain_lifecycle.py`

- [ ] **Step 1: Update both test mocks**

Find the two `fake_generate` functions (around lines 455 and 732). Both currently have:

```python
async def fake_generate(provider, domain_label, cluster_labels, model):
```

Change both to:

```python
async def fake_generate(provider, domain_label, cluster_contexts, similarity_matrix, model):
```

The body stays the same — they just return hardcoded vocab dicts.

- [ ] **Step 2: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py -v --tb=short`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/taxonomy/test_sub_domain_lifecycle.py
git commit -m "fix(tests): update generate_qualifier_vocabulary mock signatures"
```

---

### Task 3: Enrich the vocab generation pass in engine.py

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`

This is the core change — enriching the caller to collect intent_labels, domain_raw, centroids, and compute the similarity matrix.

- [ ] **Step 1: Expand cluster info query to include centroid_embedding**

Find the cluster_info query in the vocab generation pass (around line 1869-1874). Change:

```python
                cluster_info_q = await db.execute(
                    select(PromptCluster.label, PromptCluster.member_count).where(
                        PromptCluster.id.in_(child_ids),
                    )
                )
                cluster_info = [(r[0], r[1] or 0) for r in cluster_info_q.all()]
```

To:

```python
                cluster_info_q = await db.execute(
                    select(
                        PromptCluster.id,
                        PromptCluster.label,
                        PromptCluster.member_count,
                        PromptCluster.centroid_embedding,
                    ).where(
                        PromptCluster.id.in_(child_ids),
                    )
                )
                cluster_rows = cluster_info_q.all()
```

- [ ] **Step 2: Query intent_labels and domain_raw for enrichment**

After the cluster query, add:

```python
                # Collect intent_labels and domain_raw for enrichment
                from collections import Counter as _Counter
                opt_enrichment_q = await db.execute(
                    select(
                        Optimization.cluster_id,
                        Optimization.intent_label,
                        Optimization.domain_raw,
                    ).where(
                        Optimization.cluster_id.in_(child_ids),
                    )
                )
                opt_rows = opt_enrichment_q.all()

                # Group by cluster_id
                intents_by_cluster: dict[str, _Counter[str]] = {}
                qualifiers_by_cluster: dict[str, _Counter[str]] = {}
                for cid, intent, domain_raw in opt_rows:
                    if intent:
                        intents_by_cluster.setdefault(cid, _Counter())[intent.lower()] += 1
                    if domain_raw and ':' in domain_raw:
                        _, q = parse_domain(domain_raw)
                        if q:
                            qualifiers_by_cluster.setdefault(cid, _Counter())[q] += 1
```

- [ ] **Step 3: Compute centroid similarity matrix**

After the enrichment query, add:

```python
                # Compute centroid similarity matrix
                import numpy as np

                centroid_vecs = []
                centroid_indices = []  # which cluster_rows index has a valid centroid
                for i, (cid, label, mc, centroid_bytes) in enumerate(cluster_rows):
                    if centroid_bytes:
                        vec = np.frombuffer(centroid_bytes, dtype=np.float32)
                        norm = np.linalg.norm(vec)
                        if norm > 1e-9:
                            centroid_vecs.append(vec / norm)
                            centroid_indices.append(i)

                similarity_matrix: list[list[float]] | None = None
                if len(centroid_vecs) >= 2:
                    mat = np.vstack(centroid_vecs)
                    sim = (mat @ mat.T).tolist()
                    # Map back to full cluster_rows indices (sparse matrix for clusters with centroids)
                    n = len(cluster_rows)
                    similarity_matrix = [[0.0] * n for _ in range(n)]
                    for si, ri in enumerate(centroid_indices):
                        for sj, rj in enumerate(centroid_indices):
                            similarity_matrix[ri][rj] = sim[si][sj]
```

- [ ] **Step 4: Build ClusterVocabContext list**

After the matrix computation, add:

```python
                from app.services.taxonomy.labeling import ClusterVocabContext

                cluster_contexts = []
                for i, (cid, label, mc, _centroid) in enumerate(cluster_rows):
                    # Top 10 most common intents (deduplicated by frequency)
                    intent_counter = intents_by_cluster.get(cid, _Counter())
                    top_intents = [intent for intent, _ in intent_counter.most_common(10)]

                    # Qualifier distribution
                    qual_counter = qualifiers_by_cluster.get(cid, _Counter())
                    qual_dist = dict(qual_counter.most_common(5))

                    cluster_contexts.append(ClusterVocabContext(
                        label=label,
                        member_count=mc or 0,
                        intent_labels=top_intents,
                        qualifier_distribution=qual_dist,
                    ))
```

- [ ] **Step 5: Update the generate_qualifier_vocabulary call**

Change:

```python
                    generated = await generate_qualifier_vocabulary(
                        provider=self._provider,
                        domain_label=domain_node.label,
                        cluster_labels=cluster_info,
                        model=settings.MODEL_HAIKU,
                    )
```

To:

```python
                    generated = await generate_qualifier_vocabulary(
                        provider=self._provider,
                        domain_label=domain_node.label,
                        cluster_contexts=cluster_contexts,
                        similarity_matrix=similarity_matrix,
                        model=settings.MODEL_HAIKU,
                    )
```

- [ ] **Step 6: Add graceful degradation on enrichment failure**

Wrap the enrichment (steps 2-4) in a try/except that falls back to label-only context:

```python
                try:
                    # ... enrichment code from steps 2-4 ...
                except Exception as enrich_exc:
                    logger.warning("Vocab enrichment failed for '%s' (falling back to labels): %s", domain_node.label, enrich_exc)
                    cluster_contexts = [
                        ClusterVocabContext(label=r[1], member_count=r[2] or 0)
                        for r in cluster_rows
                    ]
                    similarity_matrix = None
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="discover",
                            decision="vocab_enrichment_fallback",
                            context={"domain": domain_node.label, "reason": "query_failed", "error": str(enrich_exc)[:200]},
                        )
                    except RuntimeError:
                        pass
```

- [ ] **Step 7: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py -v --tb=short`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/taxonomy/engine.py
git commit -m "feat(taxonomy): enrich vocab pass with centroids, intents, and qualifier distribution"
```

---

### Task 4: Add post-generation quality metric

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`

- [ ] **Step 1: Add quality metric computation after vocabulary generation**

In the vocab generation pass, after `generated = await generate_qualifier_vocabulary(...)` succeeds (inside the `if generated:` block), add:

```python
                    # Compute vocabulary quality metric
                    _quality_score: float | None = None
                    _max_pairwise: float | None = None
                    _overlapping_pair: list[str] | None = None
                    try:
                        import time as _qm_time
                        _qm_start = _qm_time.monotonic()

                        group_embeddings = {}
                        for gname, gkws in generated.items():
                            emb = await self._embedding.aembed_single(" ".join(gkws))
                            emb_norm = np.linalg.norm(emb)
                            if emb_norm > 1e-9:
                                group_embeddings[gname] = emb / emb_norm

                        if len(group_embeddings) >= 2:
                            names = list(group_embeddings.keys())
                            vecs = np.vstack([group_embeddings[n] for n in names])
                            pairwise = vecs @ vecs.T
                            _max_pairwise = -1.0
                            for i in range(len(names)):
                                for j in range(i + 1, len(names)):
                                    if pairwise[i][j] > _max_pairwise:
                                        _max_pairwise = float(pairwise[i][j])
                                        _overlapping_pair = [names[i], names[j]]
                            _quality_score = round(1.0 - _max_pairwise, 4)

                        _qm_ms = round((_qm_time.monotonic() - _qm_start) * 1000, 1)

                        if _quality_score is not None:
                            try:
                                get_event_logger().log_decision(
                                    path="warm", op="discover",
                                    decision="vocab_quality_assessed",
                                    context={
                                        "domain": domain_node.label,
                                        "quality_score": _quality_score,
                                        "max_pairwise_cosine": _max_pairwise,
                                        "overlapping_pair": _overlapping_pair if _max_pairwise and _max_pairwise > 0.7 else None,
                                        "quality_ms": _qm_ms,
                                    },
                                )
                            except RuntimeError:
                                pass

                            if _quality_score < 0.1:
                                logger.warning(
                                    "Vocab quality poor for '%s': score=%.2f (max_pairwise=%.2f between %s)",
                                    domain_node.label, _quality_score, _max_pairwise,
                                    _overlapping_pair,
                                )
                    except Exception as qm_exc:
                        logger.warning("Vocab quality metric failed for '%s': %s", domain_node.label, qm_exc)
```

- [ ] **Step 2: Store quality score for health endpoint**

Add a quality score accumulator to `TaxonomyEngine.__init__()`:

```python
        self._vocab_quality_scores: list[float] = []
```

After the quality metric computation, store the score:

```python
                    if _quality_score is not None:
                        self._vocab_quality_scores.append(_quality_score)
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/taxonomy/engine.py
git commit -m "feat(taxonomy): add post-generation vocabulary quality metric"
```

---

### Task 5: Wire health endpoint + observability

**Files:**
- Modify: `backend/app/services/domain_signal_loader.py`
- Modify: `backend/app/routers/health.py`

- [ ] **Step 1: Add avg_vocab_quality to DomainSignalLoader stats**

In `domain_signal_loader.py`, update the `stats()` method to include:

```python
            "avg_vocab_quality": None,  # populated from engine._vocab_quality_scores
```

Note: The actual quality scores live on the engine, not the signal loader. The health endpoint should read from the engine directly. Update the health endpoint instead.

- [ ] **Step 2: Wire into health endpoint**

In `health.py`, in the domain_lifecycle_stats collection block, add:

```python
        if _engine and hasattr(_engine, '_vocab_quality_scores') and _engine._vocab_quality_scores:
            domain_lifecycle_stats = domain_lifecycle_stats or {}
            domain_lifecycle_stats["avg_vocab_quality"] = round(
                sum(_engine._vocab_quality_scores) / len(_engine._vocab_quality_scores), 4
            )
```

- [ ] **Step 3: Verify**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -c "from app.routers.health import HealthResponse; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/domain_signal_loader.py backend/app/routers/health.py backend/app/services/taxonomy/engine.py
git commit -m "feat(health): wire avg_vocab_quality into health endpoint"
```

---

### Task 6: Full verification

- [ ] **Step 1: Run all taxonomy tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/ -v --tb=short`
Expected: All PASS

- [ ] **Step 2: Run full backend test suite**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest --tb=short -q`
Expected: 2223+ tests pass

- [ ] **Step 3: Lint**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && ruff check app/services/taxonomy/labeling.py app/services/taxonomy/engine.py app/services/domain_signal_loader.py app/routers/health.py`

- [ ] **Step 4: Commit lint fixes if any**

```bash
git add -u
git commit -m "style: fix lint in enriched vocabulary generation"
```
