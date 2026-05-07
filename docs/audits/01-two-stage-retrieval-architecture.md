# Audit Report #1: Two-Stage Retrieval Architecture

> **Tier:** 🔴 Architectural Shift  
> **Effort:** 4-8 weeks  
> **Impact:** Transformative — directly addresses the article's core finding  
> **Risk if ignored:** Up to 40% retrieval degradation under compositional edge cases

---

## Article Finding

> "Fine-tuning embedding models for compositional sensitivity triggers a 40% drop in retrieval generalization. The solution is a **two-stage architecture**: broad dense retrieval for recall, followed by a precision verification layer (cross-encoder or token-level model) that can detect subtle semantic differences."

The article identifies that single-stage retrieval architectures — where a single embedding model handles both recall AND precision — are fundamentally fragile in agentic pipelines. The representational space has a fixed capacity: optimizing for one capability (compositional sensitivity) necessarily degrades another (generalization).

---

## Current Architecture — Single-Stage Everywhere

Project Synthesis uses **single-stage cosine similarity retrieval** in every retrieval pathway:

### 1. Pattern Injection (`pattern_injection.py`)

```python
# Line 427-428: Single threshold search
matches = embedding_index.search(
    search_embedding, k=5, threshold=0.45,
    project_filter=_project_filter,
)
```

**Risk:** A pattern about "implement rate limiting" and a pattern about "remove rate limiting" will have similar embeddings (both contain "rate limiting" as dominant tokens). MiniLM-L6 cannot reliably distinguish the intent inversion.

### 2. Codebase Retrieval (`repo_index_query.py`)

```python
# Line 404: Full corpus cosine search
ranked = self._es.cosine_search(query_vec, corpus_vecs, top_k=len(rows))
```

**Risk:** A prompt asking "audit the authentication middleware" retrieves the auth middleware file, but also retrieves test files, mocks, and unrelated middleware that share vocabulary. No verification distinguishes "file that implements auth" from "file that tests auth" from "file that mocks auth".

### 3. Repo Relevance Gate (`repo_relevance.py`)

```python
# Line 279-282: Single cosine between prompt and repo synthesis
cosine = float(
    np.dot(prompt_vec, synth_vec)
    / (np.linalg.norm(prompt_vec) * np.linalg.norm(synth_vec) + 1e-9)
)
```

**Risk:** The B0 gate compares a short prompt (~50 tokens) against a long synthesis (~3K chars). This structural asymmetry makes cosine similarity unreliable for short prompts — the article specifically warns about this: "distance metrics are poor proxies for semantic equivalence at the compositional level."

### 4. Taxonomy Matching (`taxonomy/matching.py`)

Hierarchical cascade using threshold-only filtering:
- Level 1: leaf families at threshold 0.55
- Level 2: parent clusters at threshold 0.45
- Cross-cluster: global patterns with relevance floor

**Risk:** The cascade broadens search on miss but never narrows it. A high-similarity match is trusted absolutely — there's no verification that the matched cluster's patterns are semantically appropriate (not just lexically similar).

### 5. Composite Fusion (`taxonomy/fusion.py`)

5-signal weighted blend producing a single query vector:

```
topic (0.55) + transformation (0.15) + output (0.10) + pattern (0.10) + qualifier (0.10)
```

**Risk:** The fusion produces a richer query embedding, but the SEARCH still uses single-stage cosine. The richness of the query vector doesn't compensate for the poverty of the similarity metric.

### 6. Few-Shot Example Retrieval (`pattern_injection.py`)

```python
# Lines 777-790: Dual retrieval paths (input + output similarity)
input_sim = float(np.dot(prompt_embedding, emb) / (prompt_norm * emb_norm))
output_sim = float(np.dot(prompt_embedding, opt_emb) / (prompt_norm * opt_norm))
```

**Risk:** The dual-retrieval path is the most sophisticated retrieval in the system, but both paths use the same MiniLM cosine similarity — if the model can't distinguish "implement X" from "remove X" in one path, it can't distinguish it in either.

---

## Proposed Two-Stage Architecture

### Stage 1: Broad Recall (Current System, Tuned)

Keep the existing cosine similarity search but **widen thresholds** to maximize recall:

```python
# Current: threshold=0.45, k=5
# Proposed: threshold=0.30, k=15 (3x candidate pool)
matches = embedding_index.search(
    search_embedding, k=15, threshold=0.30,
    project_filter=_project_filter,
)
```

### Stage 2: Precision Verification (New)

Add a lightweight verification layer that re-ranks the Stage 1 candidates using a **different representation**:

#### Option A: Cross-Encoder Reranker (Recommended)

```python
from sentence_transformers import CrossEncoder

class RetrievalVerifier:
    """Two-stage precision verification for retrieval results."""
    
    _reranker: CrossEncoder | None = None
    
    @classmethod
    def _load_reranker(cls) -> CrossEncoder:
        if cls._reranker is None:
            # cross-encoder/ms-marco-MiniLM-L-6-v2: 22M params, ~5ms/pair
            cls._reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        return cls._reranker
    
    @classmethod
    def rerank(
        cls,
        query: str,
        candidates: list[tuple[str, float]],  # (text, stage1_score)
        top_k: int = 5,
    ) -> list[tuple[str, float, float]]:  # (text, stage1_score, rerank_score)
        """Rerank candidates using cross-encoder attention."""
        reranker = cls._load_reranker()
        pairs = [(query, text) for text, _ in candidates]
        rerank_scores = reranker.predict(pairs)
        
        combined = [
            (text, s1, float(rs))
            for (text, s1), rs in zip(candidates, rerank_scores)
        ]
        combined.sort(key=lambda x: x[2], reverse=True)
        return combined[:top_k]
```

**Cost:** ~5ms per candidate pair × 15 candidates = ~75ms added latency.

#### Option B: Lightweight Heuristic Verification

For zero-dependency environments, a heuristic verifier that catches the most dangerous failure modes:

```python
class HeuristicVerifier:
    """Rule-based precision check for retrieval results."""
    
    NEGATION_PATTERNS = [
        (r"\bnot\b", r"\b(?:remove|delete|disable|prevent)\b"),
        (r"\bwithout\b", r"\b(?:with|using|including)\b"),
        (r"\bavoid\b", r"\b(?:use|implement|apply)\b"),
    ]
    
    @classmethod
    def check_negation_flip(cls, query: str, candidate: str) -> bool:
        """Detect if query and candidate have inverted intent."""
        q_lower = query.lower()
        c_lower = candidate.lower()
        for neg_pattern, pos_pattern in cls.NEGATION_PATTERNS:
            q_neg = bool(re.search(neg_pattern, q_lower))
            c_neg = bool(re.search(neg_pattern, c_lower))
            if q_neg != c_neg:
                # One has negation, the other doesn't
                q_pos = bool(re.search(pos_pattern, q_lower))
                c_pos = bool(re.search(pos_pattern, c_lower))
                if q_pos or c_pos:
                    return True  # Likely intent inversion
        return False
    
    @classmethod
    def verify(cls, query: str, candidates: list[tuple[str, float]]) -> list[tuple[str, float, str]]:
        """Verify candidates, returning (text, score, status)."""
        results = []
        for text, score in candidates:
            if cls.check_negation_flip(query, text):
                results.append((text, score * 0.3, "negation_flip"))
            else:
                results.append((text, score, "verified"))
        return results
```

**Cost:** <1ms total, zero dependencies.

---

## Integration Points

### Pattern Injection (highest priority)

```python
# In auto_inject_patterns(), after Stage 1 search:
if matches:
    # Stage 2: Verify intent alignment
    candidates = [
        (pattern_text, similarity)
        for cluster_id, similarity in matches
        for pattern_text in cluster_patterns[cluster_id]
    ]
    verified = RetrievalVerifier.rerank(raw_prompt, candidates, top_k=5)
    # Only inject verified patterns
```

### Codebase Retrieval

```python
# In query_curated_context(), after diversity selection:
if selected:
    candidates = [
        (rows[idx].content or rows[idx].outline, score)
        for idx, score in selected[:15]  # rerank top 15
    ]
    verified = RetrievalVerifier.rerank(query, candidates, top_k=10)
```

### Taxonomy Matching

```python
# In matching.py, after threshold cascade:
# Rerank matched patterns against the original query
verified_patterns = RetrievalVerifier.rerank(
    query_text, matched_patterns, top_k=3
)
```

---

## Migration Path

### Phase 1: Instrumentation (1 week)
- Add retrieval quality metrics (see Report #8)
- Baseline current precision/recall against test prompts
- Identify compositional failure cases in production data

### Phase 2: Heuristic Verifier (1-2 weeks)
- Deploy `HeuristicVerifier` for negation flip detection
- Zero dependencies, zero latency impact
- Covers the most dangerous single failure mode

### Phase 3: Cross-Encoder Reranker (2-4 weeks)
- Add `sentence-transformers` cross-encoder as optional dependency
- Gate by preference (`pipeline.enable_reranking`)
- Deploy to pattern injection first (highest impact)

### Phase 4: Full Two-Stage (4-8 weeks)
- Extend reranker to all retrieval paths
- Evaluate embedding model alternatives (see Report #2)
- Build compositional test suite (see Report #3)

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Latency increase | Medium | Cross-encoder adds ~75ms; gate by preference |
| Model loading memory | Low | MiniLM cross-encoder is 22M params (~90MB) |
| False negatives | Medium | Heuristic verifier may miss subtle inversions |
| Backward compatibility | None | Additive change, existing behavior preserved |

---

## Key Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Pattern injection precision | Unknown (no measurement) | ≥90% intent-aligned |
| Negation flip rate | Unknown | <1% of injections |
| Retrieval rerank divergence | N/A | Track % where reranker changes top-3 |
| Cross-encoder latency | N/A | <100ms p95 |
