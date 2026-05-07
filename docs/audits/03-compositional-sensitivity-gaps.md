# Audit Report #3: Compositional Sensitivity Gaps

> **Tier:** 🟠 Significant Effort | **Effort:** 2-3 weeks | **Impact:** High

## Article Finding

> "Models fine-tuned for compositional sensitivity can detect negation flips, binding errors, and spatial inversions — but at the cost of 40% generalization loss. Systems that DON'T fine-tune are blind to these failures."

PromptForge_v2 uses an unmodified `all-MiniLM-L6-v2` — no compositional fine-tuning. This means the system is **completely blind** to compositional failures but retains full generalization. The article recommends a verification layer to catch these.

## Failure Taxonomy

### 1. Negation Flips (Highest Risk)

**Where it matters:** Pattern injection, taxonomy matching

```
User prompt: "Write an API endpoint that does NOT require authentication"
Matched pattern: "Ensure all API endpoints require authentication"
Cosine similarity: ~0.82 (high match — "API endpoint authentication" dominates)
Result: The optimizer applies the OPPOSITE intent
```

**Codebase locations exposed:**
- `pattern_injection.py:427` — threshold 0.45, no negation check
- `pattern_injection.py:549-551` — cross-cluster cosine, no negation check
- `taxonomy/matching.py` — threshold cascade, no negation check

### 2. Binding/Role Errors

**Where it matters:** Codebase retrieval, few-shot examples

```
User prompt: "The server validates client certificates"
Retrieved code: validate_server_certificate(client_cert)  # server cert validation
Cosine similarity: ~0.87 (same tokens, swapped roles)
Result: Wrong code injected into optimizer context
```

**Codebase locations exposed:**
- `repo_index_query.py:404` — full corpus search, no role verification
- `pattern_injection.py:777-790` — few-shot dual retrieval, no binding check

### 3. Scope/Qualifier Ambiguity

**Where it matters:** Domain detection, enrichment profile selection

```
User prompt: "Add logging to the production database migrations"
Domain detection: "database" (correct domain, wrong scope)
Retrieved patterns: Development migration patterns (wrong context)
```

**Codebase locations exposed:**
- `domain_detector.py` — keyword-based, no scope awareness
- `context_enrichment.py` — enrichment profile selected by domain, no scope refinement

### 4. Temporal/Conditional Inversions

```
User prompt: "Before deploying, run the test suite"
Matched pattern: "After deploying, verify the test suite passed"
```

Embedding models generally can't distinguish "before X do Y" from "after X do Y".

## Current Defenses Assessment

| Defense | Status | Gap |
|---------|--------|-----|
| B0 Relevance Gate | ✅ Exists | 🟡 Catches unrelated repos, not compositional inversions |
| Domain Vocabulary Overlap | ✅ Exists | 🟡 Token-level, not semantic |
| Identifier Match Boost | ✅ Exists | 🟢 Structural signal, not affected |
| Cross-Domain Filter | ✅ Exists | 🟡 Domain-level, not intent-level |
| Source-Type Weighting | ✅ Exists | 🟢 File classification, not affected |
| Negation Detection | ❌ Missing | 🔴 Critical gap |
| Binding Verification | ❌ Missing | 🔴 Critical gap |
| Scope Disambiguation | ❌ Missing | 🟠 Significant gap |

## Proposed Defenses

### Defense 1: Negation-Aware Similarity (Quick Win)

Add to `pattern_injection.py` between search and injection:

```python
NEGATION_TOKENS = frozenset({
    "not", "no", "never", "without", "remove", "delete",
    "disable", "prevent", "avoid", "exclude", "skip",
    "don't", "doesn't", "won't", "shouldn't", "can't",
})

def detect_intent_inversion(query: str, candidate: str) -> bool:
    """Check if query and candidate have mismatched negation."""
    q_tokens = set(query.lower().split())
    c_tokens = set(candidate.lower().split())
    q_neg = q_tokens & NEGATION_TOKENS
    c_neg = c_tokens & NEGATION_TOKENS
    # Asymmetric negation = likely intent inversion
    if bool(q_neg) != bool(c_neg):
        # Verify they share a common action verb
        shared = q_tokens & c_tokens - NEGATION_TOKENS
        action_verbs = {"implement", "add", "create", "use", "apply", "enable",
                       "require", "include", "configure", "set", "build"}
        if shared & action_verbs:
            return True
    return False
```

**Integration point:** After `embedding_index.search()` in `auto_inject_patterns()`, filter candidates.

### Defense 2: Binding-Aware Scoring (Medium Effort)

For few-shot retrieval, add subject-object consistency check:

```python
def check_binding_consistency(query: str, candidate: str) -> float:
    """Score binding consistency (0.0=inverted, 1.0=consistent)."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    q_doc = nlp(query[:200])
    c_doc = nlp(candidate[:200])
    
    q_subj_obj = [(t.text.lower(), t.dep_) for t in q_doc if t.dep_ in ("nsubj", "dobj")]
    c_subj_obj = [(t.text.lower(), t.dep_) for t in c_doc if t.dep_ in ("nsubj", "dobj")]
    
    # Check if subject/object roles are swapped
    for q_text, q_dep in q_subj_obj:
        for c_text, c_dep in c_subj_obj:
            if q_text == c_text and q_dep != c_dep:
                return 0.3  # Penalize role swap
    return 1.0
```

**Note:** Requires `spacy` dependency. Can be optional, gated by preference.

### Defense 3: Compositional Test Suite

Create `tests/test_compositional_sensitivity.py`:

```python
COMPOSITIONAL_TEST_CASES = [
    # (query, positive_match, negative_match, description)
    ("implement rate limiting", "add rate limiting to API", "remove rate limiting from API", "negation"),
    ("admin can delete user posts", "admin post deletion", "user deleting admin posts", "binding"),
    ("before deploying run tests", "pre-deployment testing", "post-deployment verification", "temporal"),
    ("add auth to public endpoints", "public API authentication", "remove auth from internal endpoints", "scope"),
]

@pytest.mark.parametrize("query,positive,negative,desc", COMPOSITIONAL_TEST_CASES)
async def test_compositional_discrimination(query, positive, negative, desc):
    es = EmbeddingService()
    q_vec = es.embed_single(query)
    p_vec = es.embed_single(positive)
    n_vec = es.embed_single(negative)
    
    pos_sim = float(np.dot(q_vec, p_vec) / (np.linalg.norm(q_vec) * np.linalg.norm(p_vec)))
    neg_sim = float(np.dot(q_vec, n_vec) / (np.linalg.norm(q_vec) * np.linalg.norm(n_vec)))
    
    # Positive match should score higher than negative
    assert pos_sim > neg_sim, (
        f"Compositional failure ({desc}): "
        f"positive_sim={pos_sim:.3f} <= negative_sim={neg_sim:.3f}"
    )
```

## Implementation Priority

1. **Week 1:** Compositional test suite — understand current failure rate
2. **Week 1-2:** Negation-aware filtering in `pattern_injection.py`
3. **Week 2-3:** Extend negation check to `repo_index_query.py` and `taxonomy/matching.py`
4. **Later:** Binding-aware scoring (needs spaCy, evaluate ROI after negation fix)
