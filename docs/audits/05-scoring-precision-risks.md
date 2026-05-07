# Audit Report #5: Scoring Pipeline Precision Risks

> **Tier:** 🟠 Significant Effort | **Effort:** 2 weeks | **Impact:** High

## Article Finding

The article warns that embedding-based metrics quietly degrade under compositional failure. PromptForge's `heuristic_scorer.py` uses the same MiniLM model for faithfulness scoring, and `score_blender.py` amplifies imprecision through z-score normalization.

## Risk 1: Faithfulness Heuristic Uses Vulnerable Embeddings

**File:** `heuristic_scorer.py:349-399`

```python
def heuristic_faithfulness(original: str, optimized: str) -> float:
    svc = EmbeddingService()
    orig_vec = svc.embed_single(original)
    opt_vec = svc.embed_single(optimized)
    similarity = float(np.dot(orig_vec, opt_vec) / (...))
    # Asymmetrical projection based on length ratio
    projection = similarity * (math.log(max(l1, l2)) / math.log(l1))
```

**Problem:** If MiniLM can't distinguish "implement X" from "remove X", the faithfulness metric will score an intent-inverted optimization as "faithful" because the embeddings are nearly identical.

**Impact:** A prompt about "add authentication" that gets optimized to "remove authentication" would receive a high faithfulness score. The asymmetric projection metric (log-length correction) does not fix this — it only corrects for expansion, not for compositional inversion.

### Proposed Fix

Add a compositional consistency check before the embedding comparison:

```python
def heuristic_faithfulness(original: str, optimized: str) -> float:
    # NEW: Check for compositional inversion before embedding comparison
    if detect_intent_inversion(original, optimized):
        logger.warning("faithfulness: intent inversion detected")
        return 2.0  # Strong penalty
    
    # Existing embedding comparison...
    svc = EmbeddingService()
    # ... (unchanged)
```

## Risk 2: Z-Score Amplification of Precision Errors

**File:** `score_blender.py:94-124`

```python
def _normalize_llm_score(raw: float, mean: float, stddev: float) -> float:
    z = (raw - mean) / stddev
    if z < 0:
        z = max(-ZSCORE_CAP, z)
    normalized = ZSCORE_CENTER + z * ZSCORE_SPREAD
    return _clamp(normalized)
```

**Problem:** Z-score normalization amplifies small differences. If the historical mean is 8.58 and stddev is 0.4 (as documented in comments), a score difference of 0.4 points (1 stddev) maps to a 1.5-point spread on the normalized scale.

**Impact on compositional errors:** If a compositional failure causes the LLM scorer to give a 7.5 instead of 8.5 (a "slight miss" in absolute terms), z-score normalization amplifies this to a 2.5-point penalty — turning a subtle precision error into a visible score drop.

**The feedback loop:** These amplified scores feed back into adaptation weights (`record_pattern_usefulness` at threshold 7.5/6.5), incorrectly penalizing patterns that were retrieved correctly but scored poorly due to compositional noise in the evaluation.

### Proposed Mitigations

1. **Widen ZSCORE_MIN_STDDEV** from 0.5 to 0.8 to skip normalization on narrow distributions
2. **Add score stability check** — compare pre/post normalization deltas and flag >2.0 shifts
3. **Gate usefulness recording** on scoring mode confidence:

```python
async def record_pattern_usefulness(db, *, optimization_id, overall_score):
    # NEW: Skip usefulness recording when scoring confidence is low
    if scoring_confidence < 0.6:
        logger.info("Skipping pattern usefulness — low scoring confidence")
        return 0
    # ... existing logic
```

## Risk 3: Divergence Flag Blind Spots

**File:** `score_blender.py:217-223`

```python
if abs(llm_raw - heur_raw) > 2.5:
    divergence_flags.append(dim)
```

**Problem:** The 2.5-point divergence threshold catches gross disagreements but misses compositional failures where both LLM and heuristic agree on a wrong answer. Example:

- Heuristic faithfulness: 8.5 (embedding says "similar" — wrong due to compositional blindness)
- LLM faithfulness: 8.0 (LLM also fooled by surface similarity)
- Divergence: 0.5 → no flag
- Reality: faithfulness should be 3.0 (intent was inverted)

### Proposed Fix

Add a `compositional_risk` flag independent of divergence:

```python
# After divergence detection
if dim == "faithfulness" and llm_raw > 7.0 and heur_raw > 7.0:
    # Both agree it's faithful — but is it compositionally correct?
    if detect_intent_inversion(original_prompt, optimized_prompt):
        divergence_flags.append("compositional_inversion")
```

## Summary of Changes

| Change | File | Effort | Impact |
|--------|------|--------|--------|
| Intent inversion check in faithfulness | `heuristic_scorer.py` | 2 days | High |
| Widen ZSCORE_MIN_STDDEV | `score_blender.py` | 1 hour | Medium |
| Score stability logging | `score_blender.py` | 1 day | Medium |
| Gate usefulness recording on confidence | `pattern_injection.py` | 1 day | Medium |
| Compositional risk flag | `score_blender.py` | 1 day | Medium |
