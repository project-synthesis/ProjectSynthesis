# Scoring System Decompression

**Date:** 2026-04-03
**Status:** Design
**Scope:** Full recalibration of the 5-dimension scoring system + delta-augmented improvement scoring

## Problem

All 73 optimization scores fall between 5.70 and 7.82 (std=0.44) on a 1-10 scale. This compression cascades into the taxonomy engine: centroid weighting provides only 1.37x differentiation, score-correlated adaptation can't find signal, and few-shot retrieval filters are ineffective.

**Root causes (6 layers):**
1. LLM rubric uses 5 bands that cluster output at midpoints (7-8 for good prompts)
2. Conciseness always drops ~3 points (optimization makes prompts longer — penalized as verbosity)
3. Faithfulness penalizes added scope even when it serves the user's intent
4. Z-score normalization re-centers already-compressed distributions, amplifying compression
5. Heuristic baselines are conservative (5.0-7.5), creating a gravity well at mid-scale
6. Unweighted arithmetic mean ensures dimension compression propagates to overall score

## Solution

### 1. Rewrite LLM Scoring Rubric (`prompts/scoring.md`)

**Current:** 5 bands per dimension (1-2, 3-4, 5-6, 7-8, 9-10) with calibration examples only at band boundaries.

**New:** Explicit calibration anchors at 1, 3, 5, 7, 9 for each dimension with concrete prompt examples. Anti-compression directive. Full-range usage instruction.

Key reframes:
- **Conciseness** → task-relative. A 600-word structured system design prompt scores 8 if every sentence earns its place. Only penalize filler, repetition, and over-specification — not length itself.
- **Faithfulness** → intent preservation. Adding relevant constraints that serve the user's goal is NOT scope creep. Score high if the optimized prompt would produce what the user actually wanted.
- **Anti-clustering**: "Use the FULL 1-10 scale. A 3 is common for vague prompts. A 9 requires exceptional precision. If most of your scores are between 6 and 8, you are not using the scale correctly."

### 2. Disable Z-Score Normalization

Temporarily disable z-score normalization in `score_blender.py` until the new rubric produces a well-distributed baseline (~50+ scores). The normalization was designed to correct LLM clustering, but with a compressed historical distribution it amplifies the problem.

**How:** Set `ZSCORE_MIN_SAMPLES = 999999` (effectively disabled). Re-enable after baseline is established with recalibrated `ZSCORE_CENTER` and `ZSCORE_SPREAD`.

### 3. Weighted Dimension Overall Score

Replace `overall = mean(5 dims)` with weighted mean:

| Dimension | Weight | Rationale |
|---|---|---|
| clarity | 0.25 | Most important — LLM must understand what to do |
| specificity | 0.25 | Constraints and details drive output quality |
| structure | 0.20 | Organization matters but is somewhat formulaic |
| faithfulness | 0.20 | Intent preservation is critical |
| conciseness | 0.10 | Task-relative; no longer penalizes necessary detail |

Formula: `overall = sum(dim * weight for dim, weight in zip(dims, weights))`

### 4. Recalibrate Heuristic Scorer (`heuristic_scorer.py`)

Current heuristic baselines pull scores toward 5.0-7.5. Recalibrate:

- **Structure**: Raise baseline from 3.0 to 4.0. Well-structured prompts with headers + lists should reach 8.5-9.0.
- **Clarity**: Adjust Flesch-based scoring to reward direct language. Current ceiling is ~7.5; raise to 9.0 for unambiguous prompts.
- **Specificity**: Increase per-constraint bonus. Prompts with 5+ explicit constraints should reach 8.5+.
- **Conciseness**: Reframe as information density (useful content / total content). Long prompts with high density score well.
- **Faithfulness**: Keep as-is (embedding similarity is already variable).

### 5. Improvement Score (New Column)

New `improvement_score` on `Optimization` model. Computed from existing `score_deltas` JSON:

```python
improvement_score = (
    deltas["clarity"] * 0.25 +
    deltas["specificity"] * 0.25 +
    deltas["structure"] * 0.20 +
    deltas["faithfulness"] * 0.20 +
    deltas["conciseness"] * 0.10
)
# Clamped to [0.0, 10.0]
```

Backfilled for all existing optimizations from stored `score_deltas`.

**Used by:**
- Centroid weighting: blend `overall_score` and `improvement_score`
- Adaptation learning: `improvement_score` provides wider variance for z-score weighting
- Few-shot retrieval: filter by both quality AND improvement value
- History API: exposed to frontend for display

### 6. Centroid Weight Formula

Current: `max(0.1, overall_score / 10.0)` → 1.37x range with compressed scores.

New: `max(0.2, (overall_score / 10.0) ** 1.5)` → 4.25x range with decompressed scores.

| Score | Current Weight | New Weight |
|---|---|---|
| 3.0 | 0.30 | 0.20 (floor) |
| 5.0 | 0.50 | 0.35 |
| 7.0 | 0.70 | 0.59 |
| 9.0 | 0.90 | 0.85 |

### 7. Migration Strategy

Historical scores are on a compressed scale. Options:
- **Don't re-score**: Accept that historical scores are compressed. New scores will be on the decompressed scale. The taxonomy will naturally transition as new optimizations accumulate.
- **Backfill improvement_score**: Compute from stored `score_deltas` for all existing optimizations.

## Files Modified

| File | Change |
|---|---|
| `prompts/scoring.md` | Full rubric rewrite with calibration anchors, task-relative conciseness, anti-compression directive |
| `backend/app/services/score_blender.py` | Weighted overall, disable z-score (ZSCORE_MIN_SAMPLES=999999), new DIMENSION_WEIGHTS constant |
| `backend/app/services/heuristic_scorer.py` | Recalibrate baselines for structure, clarity, specificity, conciseness |
| `backend/app/models.py` | Add `improvement_score` column to Optimization |
| `backend/app/services/pipeline.py` | Compute and store improvement_score after scoring phase |
| `backend/app/services/taxonomy/family_ops.py` | New centroid weight formula |
| `backend/app/services/taxonomy/fusion.py` | Use improvement_score in adaptation learning |
| `alembic/versions/` | Migration for improvement_score column |

## Verification

1. Run 5+ optimizations on the new rubric and check score distribution
2. Verify scores span at least 3.0-9.0 range (not clustered 6-8)
3. Check conciseness dimension: structured prompts should score 7+ not 5
4. Check improvement_score: should have wider variance than overall_score
5. Check centroid weights: high-scoring optimizations should shift centroids more
6. Check Activity panel for any scoring-related events
7. Run `pytest` for backend tests
