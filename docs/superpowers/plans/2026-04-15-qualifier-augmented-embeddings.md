# Qualifier-Augmented Embeddings Implementation Plan

**Status:** Shipped (v0.3.32). `Optimization.qualifier_embedding` (384-dim) + `QualifierIndex` per-cluster centroid + `w_qualifier` as the fifth signal in `PhaseWeights` composite fusion. Blend weight `CLUSTERING_BLEND_W_QUALIFIER = 0.10` in HDBSCAN clustering. Historical record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fourth embedding signal from organic qualifier vocabulary, enabling qualifier-aware clustering that improves cross-project pattern discovery and sub-domain formation.

**Architecture:** Embed qualifier keywords as a standalone 384-dim vector, stored as `Optimization.qualifier_embedding`. New `QualifierIndex` (same pattern as `TransformationIndex`) tracks per-cluster mean qualifier vectors. `blend_embeddings()` gains a keyword-only `qualifier` param with weight 0.10 (raw adjusted from 0.65→0.55). `PhaseWeights`, `CompositeQuery`, and all fusion profiles extended to 5 signals. Qualifier embedding cache on `DomainSignalLoader` eliminates repeated MiniLM calls.

**Tech Stack:** Python 3.12, SQLAlchemy async, numpy, all-MiniLM-L6-v2 (384-dim), Alembic

**Spec:** `docs/superpowers/specs/2026-04-15-qualifier-augmented-embeddings-design.md`

---

## File Structure

| File | Change | Responsibility |
|------|--------|---------------|
| `backend/app/models.py` | Modify | Add `qualifier_embedding` column |
| `backend/app/services/taxonomy/qualifier_index.py` | Create | New `QualifierIndex` class (TransformationIndex pattern) |
| `backend/app/services/taxonomy/_constants.py` | Modify | Add `CLUSTERING_BLEND_W_QUALIFIER`, adjust `CLUSTERING_BLEND_W_RAW` |
| `backend/app/services/taxonomy/clustering.py` | Modify | Add keyword-only `qualifier` param to `blend_embeddings()` |
| `backend/app/services/taxonomy/fusion.py` | Modify | Extend `PhaseWeights` (5 fields), `CompositeQuery` (5 signals), all profiles/biases, `compute_score_correlated_target()` |
| `backend/app/services/domain_signal_loader.py` | Modify | Add qualifier embedding cache + counters |
| `backend/app/services/taxonomy/engine.py` | Modify | Generate qualifier embedding in `process_optimization()`, init `QualifierIndex` |
| `backend/app/services/taxonomy/warm_phases.py` | Modify | Phase 4 backfill for NULL qualifier_embeddings |
| `backend/app/services/taxonomy/cold_path.py` | Modify | Load qualifier_embedding for blended clustering |
| `backend/app/routers/health.py` | Modify | Wire new stats fields |
| `alembic/versions/` | Create | Migration for qualifier_embedding column |
| `backend/tests/taxonomy/test_qualifier_index.py` | Create | QualifierIndex tests |
| `backend/tests/taxonomy/test_blend_embeddings.py` | Modify | Update weight constants, add qualifier blend tests |
| `backend/tests/taxonomy/test_fusion.py` | Modify | Update all PhaseWeights to 5-arg, update profile tests |

---

### Task 1: Alembic migration + model column

**Files:**
- Modify: `backend/app/models.py`
- Create: `alembic/versions/<hash>_add_qualifier_embedding.py`

- [ ] **Step 1: Add the column to the model**

In `backend/app/models.py`, find the `transformation_embedding` column on the `Optimization` class. Add after it:

```python
    qualifier_embedding = Column(LargeBinary, nullable=True)  # 384-dim float32 qualifier signal
```

- [ ] **Step 2: Generate the Alembic migration**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && alembic revision --autogenerate -m "add qualifier_embedding column"`

Then open the generated migration file and verify the `upgrade()` adds the column and `downgrade()` drops it. Wrap in idempotency guard:

```python
def upgrade():
    with op.batch_alter_table("optimizations") as batch_op:
        batch_op.add_column(sa.Column("qualifier_embedding", sa.LargeBinary(), nullable=True))

def downgrade():
    with op.batch_alter_table("optimizations") as batch_op:
        batch_op.drop_column("qualifier_embedding")
```

- [ ] **Step 3: Run migration**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && alembic upgrade head`

- [ ] **Step 4: Verify**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -c "from app.models import Optimization; print(hasattr(Optimization, 'qualifier_embedding'))"`
Expected: `True`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py alembic/versions/
git commit -m "feat(schema): add qualifier_embedding column to Optimization"
```

---

### Task 2: Create QualifierIndex

**Files:**
- Create: `backend/app/services/taxonomy/qualifier_index.py`
- Create: `backend/tests/taxonomy/test_qualifier_index.py`

- [ ] **Step 1: Write tests**

Create `backend/tests/taxonomy/test_qualifier_index.py` following the exact same test patterns as `backend/tests/taxonomy/test_transformation_index.py`. Key tests:

```python
import numpy as np
import pytest

from app.services.taxonomy.qualifier_index import QualifierIndex

DIM = 4  # small for tests


@pytest.mark.asyncio
async def test_upsert_and_get():
    idx = QualifierIndex(dim=DIM)
    vec = np.array([1, 0, 0, 0], dtype=np.float32)
    await idx.upsert("c-1", vec)
    result = idx.get_vector("c-1")
    assert result is not None
    assert np.allclose(result, vec / np.linalg.norm(vec))


@pytest.mark.asyncio
async def test_search():
    idx = QualifierIndex(dim=DIM)
    await idx.upsert("c-1", np.array([1, 0, 0, 0], dtype=np.float32))
    await idx.upsert("c-2", np.array([0, 1, 0, 0], dtype=np.float32))
    results = idx.search(np.array([1, 0, 0, 0], dtype=np.float32), k=1)
    assert results[0][0] == "c-1"


@pytest.mark.asyncio
async def test_remove():
    idx = QualifierIndex(dim=DIM)
    await idx.upsert("c-1", np.array([1, 0, 0, 0], dtype=np.float32))
    await idx.remove("c-1")
    assert idx.get_vector("c-1") is None


@pytest.mark.asyncio
async def test_rebuild():
    idx = QualifierIndex(dim=DIM)
    vecs = {
        "c-1": np.array([1, 0, 0, 0], dtype=np.float32),
        "c-2": np.array([0, 1, 0, 0], dtype=np.float32),
    }
    await idx.rebuild(vecs)
    assert idx.size == 2


@pytest.mark.asyncio
async def test_snapshot_restore():
    idx = QualifierIndex(dim=DIM)
    await idx.upsert("c-1", np.array([1, 0, 0, 0], dtype=np.float32))
    snap = await idx.snapshot()
    await idx.remove("c-1")
    assert idx.size == 0
    await idx.restore(snap)
    assert idx.size == 1
```

- [ ] **Step 2: Create QualifierIndex**

Create `backend/app/services/taxonomy/qualifier_index.py` — this is an exact copy of `transformation_index.py` with class/variable names changed:
- `TransformationIndex` → `QualifierIndex`
- `TransformationSnapshot` → `QualifierSnapshot`
- All log messages: `"TransformationIndex"` → `"QualifierIndex"`

The file is ~249 lines. Copy `transformation_index.py` and do a global find-replace.

- [ ] **Step 3: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_qualifier_index.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/taxonomy/qualifier_index.py backend/tests/taxonomy/test_qualifier_index.py
git commit -m "feat(taxonomy): add QualifierIndex for per-cluster qualifier embeddings"
```

---

### Task 3: Blend weights + blend_embeddings() qualifier parameter

**Files:**
- Modify: `backend/app/services/taxonomy/_constants.py`
- Modify: `backend/app/services/taxonomy/clustering.py`
- Modify: `backend/tests/taxonomy/test_blend_embeddings.py`

- [ ] **Step 1: Update constants**

In `_constants.py`, change:
```python
CLUSTERING_BLEND_W_RAW = 0.55           # Topic signal (reduced from 0.65 to make room for qualifier)
```
And add after `CLUSTERING_BLEND_W_TRANSFORM`:
```python
CLUSTERING_BLEND_W_QUALIFIER = 0.10     # Domain specialization signal from organic vocabulary
```

- [ ] **Step 2: Add qualifier parameter to blend_embeddings()**

In `clustering.py`, change the `blend_embeddings` function signature and body. The `qualifier` parameter must be keyword-only (after `*`) to avoid breaking existing positional callers:

```python
def blend_embeddings(
    raw: np.ndarray,
    optimized: np.ndarray | None = None,
    transformation: np.ndarray | None = None,
    w_raw: float = CLUSTERING_BLEND_W_RAW,
    w_optimized: float = CLUSTERING_BLEND_W_OPTIMIZED,
    w_transform: float = CLUSTERING_BLEND_W_TRANSFORM,
    *,
    qualifier: np.ndarray | None = None,
    w_qualifier: float = CLUSTERING_BLEND_W_QUALIFIER,
) -> np.ndarray:
```

Add to the imports at the top of `clustering.py`:
```python
from app.services.taxonomy._constants import (
    CLUSTERING_BLEND_W_QUALIFIER,
    ...existing imports...
)
```

In the function body, after the `transformation` block, add:
```python
    if qualifier is not None:
        signals.append(qualifier.astype(np.float32).ravel())
        weights.append(w_qualifier)
```

Update the docstring to mention the qualifier signal.

- [ ] **Step 3: Update tests**

In `test_blend_embeddings.py`:
- Add `CLUSTERING_BLEND_W_QUALIFIER` to the import
- Update `test_weights_sum_to_one`: add `CLUSTERING_BLEND_W_QUALIFIER` to the sum
- Update `test_raw_dominates`: assertions still pass (0.55 > 0.20, 0.55 > 0.15)
- Add `test_all_weights_positive`: add `assert CLUSTERING_BLEND_W_QUALIFIER > 0`
- Add new test:

```python
def test_blend_with_qualifier_signal():
    """Qualifier signal blends into the output when provided."""
    raw = np.array([1, 0, 0, 0], dtype=np.float32)
    qualifier = np.array([0, 1, 0, 0], dtype=np.float32)
    blended = blend_embeddings(raw, qualifier=qualifier)
    # Blended should shift toward qualifier direction
    assert blended[1] > 0  # qualifier pulled it toward [0,1,0,0]
    assert blended[0] > blended[1]  # but raw still dominates (0.55 > 0.10)


def test_blend_without_qualifier_matches_original():
    """Without qualifier, blend is identical to 3-signal blend."""
    raw = np.array([1, 0, 0, 0], dtype=np.float32)
    opt = np.array([0, 1, 0, 0], dtype=np.float32)
    result_no_q = blend_embeddings(raw, optimized=opt)
    result_with_none = blend_embeddings(raw, optimized=opt, qualifier=None)
    np.testing.assert_array_almost_equal(result_no_q, result_with_none)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_blend_embeddings.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/_constants.py backend/app/services/taxonomy/clustering.py backend/tests/taxonomy/test_blend_embeddings.py
git commit -m "feat(taxonomy): add qualifier signal to blend_embeddings() with 0.10 weight"
```

---

### Task 4: Extend PhaseWeights and fusion profiles to 5 signals

**Files:**
- Modify: `backend/app/services/taxonomy/fusion.py`
- Modify: `backend/tests/taxonomy/test_fusion.py`

This is the largest task — every 4-signal structure in fusion.py becomes 5-signal.

- [ ] **Step 1: Update PhaseWeights dataclass**

In `fusion.py`, change the `PhaseWeights` dataclass:

```python
@dataclass
class PhaseWeights:
    """Five-signal weight profile for composite query fusion."""

    w_topic: float
    w_transform: float
    w_output: float
    w_pattern: float
    w_qualifier: float

    @property
    def total(self) -> float:
        return self.w_topic + self.w_transform + self.w_output + self.w_pattern + self.w_qualifier
```

Update `enforce_floor()`:
- Change `n = 4` → `n = 5`
- Add `max(self.w_qualifier, 0.0)` to `raw` list
- Change the all-pinned fallback from `PhaseWeights(0.25, 0.25, 0.25, 0.25)` to `PhaseWeights(0.20, 0.20, 0.20, 0.20, 0.20)`
- Add `w_qualifier=result[4]` to the return constructor

Update `for_phase()`:
- Index 5-tuples: add `w_qualifier=profile[4]`

Update `from_dict()`:
- Add `w_qualifier=float(d.get("w_qualifier", 0.0))` — default 0.0 for backward compat

Update `to_dict()`:
- Add `"w_qualifier": round(self.w_qualifier, 4)`

- [ ] **Step 2: Update profiles and biases**

Change `_DEFAULT_PROFILES` type and values (5-tuples):
```python
_DEFAULT_PROFILES: dict[str, tuple[float, float, float, float, float]] = {
    "analysis":          (0.55, 0.15, 0.10, 0.15, 0.05),
    "optimization":      (0.18, 0.30, 0.22, 0.20, 0.10),
    "pattern_injection": (0.22, 0.22, 0.18, 0.28, 0.10),
    "scoring":           (0.13, 0.18, 0.42, 0.20, 0.07),
}
```

Add `w_qualifier` to each `_TASK_TYPE_WEIGHT_BIAS` entry:
```python
_TASK_TYPE_WEIGHT_BIAS: dict[str, dict[str, float]] = {
    "coding":   {"w_topic": -0.10, "w_transform": +0.15, "w_output": -0.05, "w_pattern": 0.00, "w_qualifier": +0.15},
    "writing":  {"w_topic": -0.05, "w_transform": -0.05, "w_output": +0.15, "w_pattern": -0.05, "w_qualifier": +0.05},
    "analysis": {"w_topic": +0.10, "w_transform": -0.05, "w_output": -0.10, "w_pattern": +0.05, "w_qualifier": +0.12},
    "creative": {"w_topic": -0.10, "w_transform": +0.05, "w_output": +0.10, "w_pattern": -0.05, "w_qualifier": +0.03},
    "data":     {"w_topic": +0.05, "w_transform": +0.10, "w_output": -0.10, "w_pattern": -0.05, "w_qualifier": +0.10},
    "system":   {"w_topic": +0.05, "w_transform": -0.05, "w_output": -0.05, "w_pattern": +0.05, "w_qualifier": +0.10},
    "general":  {"w_topic": 0.00,  "w_transform": 0.00,  "w_output": 0.00,  "w_pattern": 0.00,  "w_qualifier": +0.05},
}
```

- [ ] **Step 3: Update resolve_contextual_weights()**

Add `w_qualifier` to both the biased construction and the learned blending:

```python
        biased = PhaseWeights(
            w_topic=base.w_topic + bias.get("w_topic", 0.0),
            w_transform=base.w_transform + bias.get("w_transform", 0.0),
            w_output=base.w_output + bias.get("w_output", 0.0),
            w_pattern=base.w_pattern + bias.get("w_pattern", 0.0),
            w_qualifier=base.w_qualifier + bias.get("w_qualifier", 0.0),
        ).enforce_floor()
```

And in the learned blending block:
```python
            biased = PhaseWeights(
                w_topic=biased.w_topic + alpha * (learned.w_topic - biased.w_topic),
                w_transform=biased.w_transform + alpha * (learned.w_transform - biased.w_transform),
                w_output=biased.w_output + alpha * (learned.w_output - biased.w_output),
                w_pattern=biased.w_pattern + alpha * (learned.w_pattern - biased.w_pattern),
                w_qualifier=biased.w_qualifier + alpha * (learned.w_qualifier - biased.w_qualifier),
            ).enforce_floor()
```

- [ ] **Step 4: Update CompositeQuery**

```python
@dataclass
class CompositeQuery:
    """Five-signal composite embedding query."""

    topic: np.ndarray
    transformation: np.ndarray
    output: np.ndarray
    pattern: np.ndarray
    qualifier: np.ndarray

    def fuse(self, weights: PhaseWeights) -> np.ndarray:
        from app.services.taxonomy.clustering import weighted_blend
        return weighted_blend(
            signals=[self.topic, self.transformation, self.output, self.pattern, self.qualifier],
            weights=[weights.w_topic, weights.w_transform, weights.w_output, weights.w_pattern, weights.w_qualifier],
        )
```

- [ ] **Step 5: Update compute_score_correlated_target()**

Add the 5th accumulator. After line 444 (`w_pattern = 0.0`), add:
```python
        w_qualifier = 0.0
```

In the accumulation loop, after `w_pattern += pw.w_pattern * contribution`, add:
```python
            # Skip qualifier dimension for old profiles (w_qualifier=0.0)
            # to avoid treating "no data" as "zero weight is optimal"
            if pw.w_qualifier > 0.0:
                w_qualifier += pw.w_qualifier * contribution
```

In the target construction, change to:
```python
        # Qualifier dimension: if no profiles had qualifier data,
        # use the phase default rather than learned zero
        q_weight = w_qualifier / phase_contribution if w_qualifier > 0 else PhaseWeights.for_phase(phase).w_qualifier

        target = PhaseWeights(
            w_topic=w_topic / phase_contribution,
            w_transform=w_transform / phase_contribution,
            w_output=w_output / phase_contribution,
            w_pattern=w_pattern / phase_contribution,
            w_qualifier=q_weight,
        )
```

- [ ] **Step 6: Update tests**

In `test_fusion.py`, update ALL `PhaseWeights(...)` constructions from 4-arg to 5-arg. There are 18 occurrences. For each, add a 5th argument (e.g., `0.10` or `0.0`). Update profile assertions for the new 5-tuple values. Add:

```python
def test_phase_weights_from_dict_backward_compat():
    """Old 4-key dicts default w_qualifier to 0.0."""
    pw = PhaseWeights.from_dict({"w_topic": 0.5, "w_transform": 0.2, "w_output": 0.2, "w_pattern": 0.1})
    assert pw.w_qualifier == 0.0

def test_phase_weights_5_field_roundtrip():
    """5-field PhaseWeights round-trips through dict."""
    pw = PhaseWeights(0.3, 0.2, 0.2, 0.2, 0.1)
    d = pw.to_dict()
    pw2 = PhaseWeights.from_dict(d)
    assert abs(pw2.w_qualifier - 0.1) < 1e-3
```

- [ ] **Step 7: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_fusion.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/taxonomy/fusion.py backend/tests/taxonomy/test_fusion.py
git commit -m "feat(taxonomy): extend PhaseWeights, CompositeQuery, and fusion profiles to 5 signals"
```

---

### Task 5: Qualifier embedding cache on DomainSignalLoader

**Files:**
- Modify: `backend/app/services/domain_signal_loader.py`
- Modify: `backend/tests/test_domain_signal_loader.py`

- [ ] **Step 1: Write tests**

Add to `backend/tests/test_domain_signal_loader.py`:

```python
def test_qualifier_embedding_cache_hit():
    """Cached qualifier embeddings are returned without re-embedding."""
    from app.services.domain_signal_loader import DomainSignalLoader
    import numpy as np

    loader = DomainSignalLoader()
    vec = np.random.randn(384).astype(np.float32)
    loader.cache_qualifier_embedding("growth|metrics|kpi", vec)

    result = loader.get_cached_qualifier_embedding("growth|metrics|kpi")
    assert result is not None
    np.testing.assert_array_equal(result, vec)


def test_qualifier_embedding_cache_miss():
    """Missing keys return None."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    assert loader.get_cached_qualifier_embedding("unknown") is None


def test_qualifier_embedding_cache_invalidation():
    """refresh_qualifiers() clears the embedding cache for that domain."""
    from app.services.domain_signal_loader import DomainSignalLoader
    import numpy as np

    loader = DomainSignalLoader()
    loader.refresh_qualifiers("saas", {"growth": ["metrics", "kpi"]})
    vec = np.random.randn(384).astype(np.float32)
    loader.cache_qualifier_embedding("growth|metrics|kpi", vec)

    # Refresh invalidates cache
    loader.refresh_qualifiers("saas", {"growth": ["metrics", "kpi", "new"]})
    assert loader.get_cached_qualifier_embedding("growth|metrics|kpi") is None
```

- [ ] **Step 2: Implement cache**

In `domain_signal_loader.py`, add to `__init__`:
```python
        self._qualifier_embedding_cache: dict[str, np.ndarray] = {}
        self._qualifier_embeddings_generated: int = 0
        self._qualifier_embeddings_skipped: int = 0
```

Add methods:
```python
    def cache_qualifier_embedding(self, key: str, embedding: np.ndarray) -> None:
        """Cache a qualifier embedding keyed by sorted keyword string."""
        self._qualifier_embedding_cache[key] = embedding

    def get_cached_qualifier_embedding(self, key: str) -> np.ndarray | None:
        """Look up a cached qualifier embedding. Returns None on miss."""
        return self._qualifier_embedding_cache.get(key)

    def invalidate_qualifier_embedding_cache(self) -> None:
        """Clear all cached qualifier embeddings (called on vocab refresh)."""
        self._qualifier_embedding_cache.clear()
```

In `refresh_qualifiers()`, add `self.invalidate_qualifier_embedding_cache()` after the cache update.

Update `stats()` to include:
```python
            "qualifier_embeddings_generated": self._qualifier_embeddings_generated,
            "qualifier_embeddings_skipped": self._qualifier_embeddings_skipped,
            "qualifier_embedding_cache_size": len(self._qualifier_embedding_cache),
```

- [ ] **Step 3: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/test_domain_signal_loader.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/domain_signal_loader.py backend/tests/test_domain_signal_loader.py
git commit -m "feat(taxonomy): add qualifier embedding cache to DomainSignalLoader"
```

---

### Task 6: Generate qualifier embedding in process_optimization()

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`

- [ ] **Step 1: Initialize QualifierIndex in TaxonomyEngine.__init__()**

Find the `TransformationIndex` initialization in `__init__` and add after it:
```python
        from app.services.taxonomy.qualifier_index import QualifierIndex
        self._qualifier_index = QualifierIndex(dim=384)
```

Add a property:
```python
    @property
    def qualifier_index(self) -> QualifierIndex:
        return self._qualifier_index
```

- [ ] **Step 2: Generate qualifier embedding in process_optimization()**

After the transformation embedding block (around line 558), add:

```python
            # 1d. Compute qualifier embedding from organic vocabulary
            qualifier_emb = None
            try:
                _, domain_qualifier = parse_domain(opt.domain_raw or "")
                if domain_qualifier:
                    from app.services.domain_signal_loader import get_signal_loader
                    loader = get_signal_loader()
                    if loader:
                        qualifiers = loader.get_qualifiers(domain_primary)
                        keywords = qualifiers.get(domain_qualifier)
                        if keywords:
                            # Build cache key from sorted keywords
                            cache_key = "|".join(sorted(keywords))
                            cached = loader.get_cached_qualifier_embedding(cache_key)
                            if cached is not None:
                                qualifier_emb = cached
                            else:
                                qualifier_text = " ".join(keywords)
                                qualifier_emb = await self._embedding.aembed_single(qualifier_text)
                                loader.cache_qualifier_embedding(cache_key, qualifier_emb)
                            opt.qualifier_embedding = qualifier_emb.astype(np.float32).tobytes()
                            loader._qualifier_embeddings_generated += 1
                        else:
                            loader._qualifier_embeddings_skipped += 1
                    else:
                        pass  # No loader — cold start
                else:
                    pass  # No qualifier in domain_raw
            except Exception as qe:
                logger.warning("Qualifier embedding failed (non-fatal): %s", qe)
```

- [ ] **Step 3: Update QualifierIndex after cluster assignment**

Find the TransformationIndex update block (around line 678-698). Add a similar block after it for QualifierIndex:

```python
            # 3d. Update QualifierIndex
            if qualifier_emb is not None:
                try:
                    await self._qualifier_index.upsert(cluster.id, qualifier_emb)
                except Exception as qi_exc:
                    logger.warning("QualifierIndex upsert failed: %s", qi_exc)
```

- [ ] **Step 4: Save QualifierIndex alongside other indices**

Find where `TransformationIndex` is saved to disk (search for `transformation_index.pkl`). Add similar save for qualifier:

```python
        _qi_path = DATA_DIR / "qualifier_index.pkl"
        await engine.qualifier_index.save_cache(_qi_path)
```

- [ ] **Step 5: Load QualifierIndex on startup**

Find where `TransformationIndex` cache is loaded on startup. Add similar load:

```python
        _qi_path = DATA_DIR / "qualifier_index.pkl"
        await engine.qualifier_index.load_cache(_qi_path)
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/engine.py
git commit -m "feat(taxonomy): generate qualifier embedding in hot path with cache"
```

---

### Task 7: Wire qualifier into warm/cold path blending

**Files:**
- Modify: `backend/app/services/taxonomy/warm_phases.py`
- Modify: `backend/app/services/taxonomy/cold_path.py`

- [ ] **Step 1: Update warm path blending**

Find all calls to `blend_embeddings()` in `warm_phases.py`. For each, load the `qualifier_embedding` from the Optimization row and pass as keyword argument:

```python
qualifier_vec = np.frombuffer(opt.qualifier_embedding, dtype=np.float32) if opt.qualifier_embedding else None
blended = blend_embeddings(raw, optimized, transformation, qualifier=qualifier_vec)
```

- [ ] **Step 2: Update cold path blending**

Same pattern in `cold_path.py` — wherever `blend_embeddings()` is called, load `qualifier_embedding` and pass it.

- [ ] **Step 3: Add Phase 4 backfill**

In `warm_phases.py`, in the `phase_refresh()` function, after the existing pattern extraction and label refresh, add a qualifier backfill section:

```python
    # Qualifier embedding backfill (capped at 50 per cycle)
    QUALIFIER_BACKFILL_CAP = 50
    backfilled = 0
    # ... query for optimizations with NULL qualifier_embedding where domain_raw has qualifier
    # ... for each (up to cap): parse domain_raw, look up vocab, embed, store
```

The backfill should also handle `qualifier_stale` clusters by regenerating ALL qualifier embeddings for those clusters (not just NULLs).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/taxonomy/warm_phases.py backend/app/services/taxonomy/cold_path.py
git commit -m "feat(taxonomy): wire qualifier embedding into warm/cold path blending + Phase 4 backfill"
```

---

### Task 8: Wire qualifier into CompositeQuery builder + health endpoint

**Files:**
- Modify: `backend/app/services/taxonomy/fusion.py` (`build_composite_query`)
- Modify: `backend/app/routers/health.py`

- [ ] **Step 1: Add qualifier signal to build_composite_query()**

In `fusion.py`, find `build_composite_query()`. After the pattern signal construction (the 4th signal), add qualifier as 5th:

```python
    # Signal 5: Qualifier — domain specialization from organic vocabulary
    qualifier_signal = np.zeros(dim, dtype=np.float32)
    try:
        from app.services.domain_signal_loader import get_signal_loader
        from app.utils.text_cleanup import parse_domain
        loader = get_signal_loader()
        if loader:
            _, qualifier_name = parse_domain(domain_raw or "")
            if qualifier_name:
                primary, _ = parse_domain(domain_raw or "")
                qualifiers = loader.get_qualifiers(primary)
                keywords = qualifiers.get(qualifier_name)
                if keywords:
                    cache_key = "|".join(sorted(keywords))
                    cached = loader.get_cached_qualifier_embedding(cache_key)
                    if cached is not None:
                        qualifier_signal = cached
                    else:
                        qualifier_signal = await embedding_service.aembed_single(" ".join(keywords))
                        loader.cache_qualifier_embedding(cache_key, qualifier_signal)
    except Exception:
        pass  # Best-effort
```

Update the `CompositeQuery` construction to include `qualifier=qualifier_signal`.

Note: `build_composite_query()` needs access to `domain_raw` — check if it's already a parameter or needs to be added.

- [ ] **Step 2: Wire health endpoint**

In `health.py`, the `qualifier_vocab` stats block already calls `_loader.stats()`. The new fields (`qualifier_embeddings_generated`, `qualifier_embeddings_skipped`, `qualifier_embedding_cache_size`) are already added to `stats()` in Task 5. No additional wiring needed — verify the fields appear.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/taxonomy/fusion.py backend/app/routers/health.py
git commit -m "feat(taxonomy): wire qualifier signal into CompositeQuery builder"
```

---

### Task 9: Full verification

- [ ] **Step 1: Run all taxonomy tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/ -v --tb=short`

- [ ] **Step 2: Run full backend test suite**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest --tb=short -q`
Expected: 2201+ tests pass

- [ ] **Step 3: Lint**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && ruff check app/services/taxonomy/ app/services/domain_signal_loader.py app/models.py app/routers/health.py`

- [ ] **Step 4: Commit any fixes**

```bash
git add -u
git commit -m "style: fix lint in qualifier-augmented embeddings"
```
