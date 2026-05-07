# Audit Report #6: Context Enrichment Cascade Risks

> **Tier:** 🟡 Medium Effort | **Effort:** 1-2 weeks | **Impact:** Medium-High

## Article Relevance

The article warns about cascade failures in agentic pipelines: "When precision drops at the retrieval stage, every downstream component inherits that noise." PromptForge's context enrichment has 6 sources that cascade without cross-validation.

## Current Enrichment Sources

From `context_enrichment.py`, the enrichment profile assembles context from:

| Source | Profile | Validated? |
|--------|---------|-----------|
| 1. Codebase context (curated retrieval) | `code_aware` | ❌ No |
| 2. Pattern injection (taxonomy patterns) | All profiles | ❌ No |
| 3. Few-shot examples (historical) | All profiles | ❌ No |
| 4. Strategy intelligence (adaptation data) | All profiles | ❌ No |
| 5. Divergence alerts | All profiles | ✅ Rule-based |
| 6. Domain qualifier enrichment | All profiles | ✅ Keyword-based |

### The Cascade Problem

```
Source 1 (codebase retrieval) produces noisy results
  → Source 2 (patterns) selected based on same embedding → correlated noise
    → Source 3 (few-shot) ranked by same embedding → triple-correlated noise
      → All injected into optimizer prompt
        → Optimizer produces output biased by noise
          → Scorer uses same embeddings to evaluate
            → Score feeds back into adaptation weights
```

**Key insight:** Sources 1-3 all use the same `EmbeddingService`. A single embedding model failure mode creates correlated errors across all three sources. This is the article's exact scenario — but applied to the enrichment cascade, not just retrieval.

## Specific Cascade Risks

### Risk A: Pattern-Codebase Conflict

Pattern injection finds "use rate limiting pattern X" while codebase retrieval shows code that uses pattern Y. The optimizer receives contradictory signals.

**Current behavior:** Both are injected without conflict detection.

**Fix:** Cross-reference pattern claims against codebase evidence:

```python
def detect_enrichment_conflicts(
    patterns: list[InjectedPattern],
    codebase_context: CuratedCodebaseContext,
) -> list[str]:
    """Detect conflicts between injected patterns and codebase evidence."""
    conflicts = []
    for pattern in patterns:
        # Check if pattern claims something that contradicts codebase
        pattern_lower = pattern.pattern_text.lower()
        if codebase_context and codebase_context.context_text:
            code_lower = codebase_context.context_text.lower()
            # Simple: negation mismatch on shared verbs
            for verb in ["use", "implement", "add", "require"]:
                if verb in pattern_lower and f"not {verb}" in code_lower:
                    conflicts.append(
                        f"Pattern suggests '{verb}' but codebase shows 'not {verb}'"
                    )
    return conflicts
```

### Risk B: Few-Shot Contamination

Few-shot examples are selected by embedding similarity (same model as patterns). If the model has a compositional blind spot, the few-shot example may demonstrate the OPPOSITE of what the user wants.

**Current behavior:** `retrieve_few_shot_examples()` uses dual retrieval (input + output similarity) with MMR diversity, but no intent verification.

**Fix:** Add intent consistency check:

```python
# In retrieve_few_shot_examples(), after MMR selection:
for example in selected_examples:
    if detect_intent_inversion(raw_prompt, example.raw_prompt):
        logger.warning(
            "few_shot_intent_inversion: removing example with inverted intent"
        )
        selected_examples.remove(example)
```

### Risk C: Enrichment Profile Mismatch

The enrichment profile (`code_aware` / `knowledge_work` / `cold_start`) is selected based on available data, not on whether the data is relevant. A linked repo may have stale context that doesn't match the prompt.

**Current defense:** B0 relevance gate catches completely unrelated repos (cosine < 0.15).

**Gap:** Repos that are related but in a different context (e.g., same codebase but prompt is about deployment while retrieval returns development code) pass B0 but produce noise.

## Proposed: Enrichment Coherence Check

Add a lightweight coherence assessment after all sources are assembled but before injection:

```python
@dataclass
class EnrichmentCoherence:
    overall: float           # 0.0-1.0
    source_conflicts: int    # Number of cross-source conflicts
    redundancy_rate: float   # Fraction of duplicate information
    noise_estimate: float    # Estimated noise ratio
    action: str              # "inject_all" | "filter_noisy" | "reduce"

class CoherenceChecker:
    def assess(
        self,
        query: str,
        patterns: list[InjectedPattern],
        codebase: CuratedCodebaseContext | None,
        few_shots: list[FewShotExample],
    ) -> EnrichmentCoherence:
        """Cross-source coherence assessment."""
        conflicts = detect_enrichment_conflicts(patterns, codebase)
        
        # Estimate redundancy (how much do sources repeat each other)
        all_texts = (
            [p.pattern_text for p in patterns] +
            [f.optimized_prompt for f in few_shots] +
            ([codebase.context_text[:500]] if codebase else [])
        )
        # Simple: token overlap between sources
        if len(all_texts) >= 2:
            token_sets = [set(t.lower().split()) for t in all_texts]
            pairwise_overlaps = []
            for i in range(len(token_sets)):
                for j in range(i+1, len(token_sets)):
                    if token_sets[i] and token_sets[j]:
                        overlap = len(token_sets[i] & token_sets[j]) / len(token_sets[i] | token_sets[j])
                        pairwise_overlaps.append(overlap)
            redundancy = sum(pairwise_overlaps) / len(pairwise_overlaps) if pairwise_overlaps else 0
        else:
            redundancy = 0
        
        overall = max(0, 1.0 - len(conflicts) * 0.2 - redundancy * 0.3)
        
        if overall > 0.7:
            action = "inject_all"
        elif overall > 0.4:
            action = "filter_noisy"
        else:
            action = "reduce"
        
        return EnrichmentCoherence(
            overall=round(overall, 3),
            source_conflicts=len(conflicts),
            redundancy_rate=round(redundancy, 3),
            noise_estimate=round(1.0 - overall, 3),
            action=action,
        )
```

## Integration Point

In `context_enrichment.py`, after all layers resolve:

```python
# After enrichment assembly
coherence = CoherenceChecker().assess(
    raw_prompt, injected_patterns, codebase_context, few_shot_examples,
)

if coherence.action == "filter_noisy":
    # Remove lowest-confidence patterns
    injected_patterns = [p for p in injected_patterns if p.similarity > 0.5]
elif coherence.action == "reduce":
    # Keep only top-2 patterns and skip few-shots
    injected_patterns = sorted(injected_patterns, key=lambda p: -p.similarity)[:2]
    few_shot_examples = []
```

## Telemetry

| Metric | Purpose |
|--------|---------|
| `enrichment_coherence_score` | Track quality trend |
| `enrichment_conflict_count` | Detect systematic conflicts |
| `enrichment_action_distribution` | How often filtering/reducing fires |
| `enrichment_redundancy_rate` | Detect over-retrieval |
