# Audit Report #2: Embedding Model Risk Assessment

> **Tier:** 🔴 Architectural Shift | **Effort:** 2-4 weeks | **Impact:** Critical

## Article Finding

> "The representational space has a fixed capacity. When fine-tuning pushes the model toward compositional sensitivity, it necessarily pulls capacity from generalization."

## Current Model: `all-MiniLM-L6-v2` (384d)

**File:** `embedding_service.py` — singleton used by every retrieval path.

| Property | Value | Concern |
|----------|-------|---------|
| Architecture | MiniLM-L6 (6 layers) | Shallow — limited compositional capacity |
| Dimensions | 384 | Small representational space |
| Training | Distilled from mpnet-base | Distillation loses compositional nuance |
| Max sequence | 256 tokens | Truncation risk on long prompts |

### Why 384d Is Maximally Exposed

Compositional sensitivity and generalization compete for the same dimensions. 384d has less room for both than 768d or 1024d. Distillation from mpnet-base loses compositional nuances first.

### Single Model, Multiple Contexts

| Context | Compositional Risk |
|---------|-------------------|
| Pattern injection | 🔴 High (patterns are short, precise) |
| Codebase retrieval | 🟠 Medium (code has structural signals) |
| B0 relevance gate | 🟡 Low (asymmetric comparison) |
| Taxonomy matching | 🔴 High (centroid averaging loses precision) |
| Few-shot retrieval | 🔴 High (intent matters critically) |
| Faithfulness scoring | 🟠 Medium (expansion asymmetry) |

### Failure Mode Examples

- **Negation:** "Implement rate limiting" vs "Remove rate limiting" → cosine ~0.85
- **Binding:** "Admin deletes user posts" vs "User deletes admin posts" → cosine ~0.92
- **Scope:** "Add auth to public API" vs "Remove auth from internal API" → cosine ~0.78

### Cascade Risk

```
Bad embeddings → bad patterns → bad optimizations → bad scores
  → bad feedback → bad adaptation weights → worse embeddings (loop)
```

## Embedding Usage Inventory (11+ calls per pipeline run)

| Service | Calls | Purpose |
|---------|-------|---------|
| `pattern_injection.py` | 1+N | Prompt embed + cross-cluster scoring |
| `repo_index_query.py` | 1 | Codebase search |
| `repo_relevance.py` | 2 | B0 gate (prompt + synthesis) |
| `heuristic_scorer.py` | 2 | Faithfulness (original + optimized) |
| `taxonomy/fusion.py` | 5 | 5 fusion signals |

## Alternative Models

### Drop-in (same 384d)
- `bge-small-en-v1.5` — instruction-tuned, better compositional
- `gte-small` — Alibaba generalist, comparable speed

### Upgraded (768d, needs re-index)
- `bge-base-en-v1.5` — best cost/quality
- `nomic-embed-text-v1.5` — Matryoshka dimensions

## Recommendations

1. **Immediate:** Add compositional test suite to baseline current model
2. **Short-term:** Evaluate `bge-small-en-v1.5` as drop-in replacement
3. **Medium-term:** Dual-model architecture (fast for recall, precise for verification)
4. **Long-term:** Matryoshka embeddings for variable-precision retrieval

## Migration Notes

Model change requires re-embedding all stored vectors (5-15 min CPU). Same-dimension swaps (384d→384d) are transparent post re-embed.
