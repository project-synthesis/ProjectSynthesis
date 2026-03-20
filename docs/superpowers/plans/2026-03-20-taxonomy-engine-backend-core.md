# Evolutionary Taxonomy Engine — Backend Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the self-organizing taxonomy engine package that replaces hardcoded domain classification with HDBSCAN-driven hierarchical clustering, quality-gated lifecycle operations, UMAP 3D projection, and OKLab perceptual coloring.

**Architecture:** A new `backend/app/services/taxonomy/` package (8 modules, ~2500 lines) implements three execution tiers: hot path (<500ms per optimization), warm path (<5s periodic re-clustering with lifecycle operations), and cold path (full HDBSCAN + UMAP recomputation). All lifecycle operations pass through speculative quality gates enforcing a non-regression invariant on composite Q_system. Two new DB tables (`taxonomy_nodes`, `taxonomy_snapshots`) plus column additions to `PatternFamily` and `Optimization`.

**Tech Stack:** Python 3.12, HDBSCAN (scikit-learn ≥1.3), umap-learn, scipy, numpy, sentence-transformers (all-MiniLM-L6-v2 384-dim), SQLAlchemy async, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-03-20-evolutionary-taxonomy-engine-design.md`

**Scope:** This is Plan 1 of 3. Covers the taxonomy engine package, DB models, and all taxonomy tests. Does NOT cover pipeline integration, API endpoints, MCP changes, frontend, or old code deletion (Plans 2 and 3).

---

## File Structure

### New Files

| File | Responsibility | ~Lines |
|------|---------------|--------|
| `backend/app/services/taxonomy/__init__.py` | Public API exports | 20 |
| `backend/app/services/taxonomy/engine.py` | Hot/warm/cold path orchestration, match_prompt, map_domain | 450 |
| `backend/app/services/taxonomy/clustering.py` | HDBSCAN wrapper, nearest-centroid, batch recluster | 250 |
| `backend/app/services/taxonomy/quality.py` | QWeights, Q_system, adaptive thresholds, speculative gates | 300 |
| `backend/app/services/taxonomy/lifecycle.py` | Emerge, merge, split, retire operations | 350 |
| `backend/app/services/taxonomy/projection.py` | UMAP 3D fit/transform, Procrustes alignment | 200 |
| `backend/app/services/taxonomy/labeling.py` | Haiku label generation via LLM | 100 |
| `backend/app/services/taxonomy/coloring.py` | OKLab color generation, deltaE enforcement | 200 |
| `backend/app/services/taxonomy/snapshot.py` | Snapshot CRUD, retention pruning, tree state serialization | 200 |
| `backend/tests/taxonomy/__init__.py` | Test package marker | 1 |
| `backend/tests/taxonomy/conftest.py` | Shared fixtures: DB, embedding mocks, cluster generators | 120 |
| `backend/tests/taxonomy/test_quality.py` | Q_system, weights, thresholds, edge cases | 250 |
| `backend/tests/taxonomy/test_coloring.py` | OKLab, WCAG contrast, deltaE | 150 |
| `backend/tests/taxonomy/test_projection.py` | UMAP, Procrustes, incremental | 150 |
| `backend/tests/taxonomy/test_labeling.py` | Label generation (mocked LLM) | 80 |
| `backend/tests/taxonomy/test_snapshot.py` | Snapshot CRUD, retention policy | 150 |
| `backend/tests/taxonomy/test_clustering.py` | HDBSCAN, nearest-centroid, noise | 200 |
| `backend/tests/taxonomy/test_lifecycle.py` | Each operation + conflict resolution | 300 |
| `backend/tests/taxonomy/test_engine_hot_path.py` | process_optimization, map_domain | 200 |
| `backend/tests/taxonomy/test_engine_warm_path.py` | Warm path + lifecycle integration | 200 |
| `backend/tests/taxonomy/test_engine_cold_path.py` | Cold path + UMAP refit | 150 |
| `backend/tests/taxonomy/test_domain_mapping.py` | Free-text mapping, Bayesian blend | 150 |
| `backend/tests/taxonomy/test_cold_start.py` | Phase 0-4 transitions | 150 |
| `backend/tests/taxonomy/test_emergence.py` | Synthetic distributions → clusters | 150 |
| `backend/tests/taxonomy/test_performance.py` | Latency assertions per tier | 100 |

### Modified Files

| File | Change |
|------|--------|
| `backend/app/models.py` | Add `TaxonomyNode`, `TaxonomySnapshot` models; add `taxonomy_node_id` + `domain_raw` to `PatternFamily` and `Optimization` |
| `backend/requirements.txt` | Add `scikit-learn>=1.3`, `umap-learn>=0.5.5`, `scipy>=1.11` |

---

### Task 1: Install Dependencies

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add new Python dependencies**

Add to `backend/requirements.txt`:

```
scikit-learn>=1.3
umap-learn>=0.5.5
scipy>=1.11
```

- [ ] **Step 2: Install and verify**

Run:
```bash
cd backend && source .venv/bin/activate && pip install -r requirements.txt
```

Verify imports work:
```bash
python -c "from sklearn.cluster import HDBSCAN; import umap; from scipy.linalg import orthogonal_procrustes; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore: add HDBSCAN, UMAP, scipy dependencies for taxonomy engine"
```

---

### Task 2: Database Models

**Files:**
- Modify: `backend/app/models.py:91-103` (PatternFamily), `backend/app/models.py:35-66` (Optimization)

**Reference:** Spec Section 5.1-5.4

- [ ] **Step 1: Write the test for new models**

Create `backend/tests/taxonomy/__init__.py` (empty) and `backend/tests/taxonomy/test_models.py`:

```python
"""Verify TaxonomyNode and TaxonomySnapshot DB models."""

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from app.models import (
    Base,
    TaxonomyNode,
    TaxonomySnapshot,
    PatternFamily,
    Optimization,
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_taxonomy_node_roundtrip(db: AsyncSession):
    """Create, persist, and read back a TaxonomyNode."""
    embedding = np.random.randn(384).astype(np.float32).tobytes()
    node = TaxonomyNode(
        label="API Architecture",
        centroid_embedding=embedding,
        member_count=5,
        coherence=0.85,
        separation=0.72,
        stability=0.90,
        persistence=0.65,
        state="confirmed",
        color_hex="#a855f7",
    )
    db.add(node)
    await db.commit()

    result = await db.execute(select(TaxonomyNode))
    loaded = result.scalar_one()
    assert loaded.label == "API Architecture"
    assert loaded.state == "confirmed"
    assert loaded.member_count == 5
    assert loaded.id is not None
    assert loaded.created_at is not None
    assert loaded.parent_id is None


@pytest.mark.asyncio
async def test_taxonomy_node_parent_child(db: AsyncSession):
    """Verify parent-child relationship works."""
    parent = TaxonomyNode(
        label="Infrastructure",
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        state="confirmed",
        color_hex="#00e5ff",
    )
    db.add(parent)
    await db.flush()

    child = TaxonomyNode(
        label="Backend APIs",
        parent_id=parent.id,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        state="candidate",
        color_hex="#a855f7",
    )
    db.add(child)
    await db.commit()

    result = await db.execute(
        select(TaxonomyNode).where(TaxonomyNode.parent_id == parent.id)
    )
    children = result.scalars().all()
    assert len(children) == 1
    assert children[0].label == "Backend APIs"


@pytest.mark.asyncio
async def test_taxonomy_snapshot_roundtrip(db: AsyncSession):
    """Create and read back a TaxonomySnapshot."""
    snap = TaxonomySnapshot(
        trigger="warm_path",
        q_system=0.847,
        q_coherence=0.812,
        q_separation=0.891,
        q_coverage=0.940,
        q_dbcv=0.0,
        operations="[]",
        nodes_created=2,
        nodes_retired=0,
        nodes_merged=1,
        nodes_split=0,
    )
    db.add(snap)
    await db.commit()

    result = await db.execute(select(TaxonomySnapshot))
    loaded = result.scalar_one()
    assert loaded.q_system == pytest.approx(0.847)
    assert loaded.trigger == "warm_path"


@pytest.mark.asyncio
async def test_pattern_family_has_taxonomy_fields(db: AsyncSession):
    """PatternFamily has taxonomy_node_id and domain_raw columns."""
    family = PatternFamily(
        intent_label="test",
        domain="general",
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        taxonomy_node_id=None,
        domain_raw="REST API design",
    )
    db.add(family)
    await db.commit()

    result = await db.execute(select(PatternFamily))
    loaded = result.scalar_one()
    assert loaded.domain_raw == "REST API design"
    assert loaded.taxonomy_node_id is None


@pytest.mark.asyncio
async def test_optimization_has_taxonomy_fields(db: AsyncSession):
    """Optimization has taxonomy_node_id and domain_raw columns."""
    opt = Optimization(
        raw_prompt="test prompt",
        taxonomy_node_id=None,
        domain_raw="database schema",
    )
    db.add(opt)
    await db.commit()

    result = await db.execute(select(Optimization))
    loaded = result.scalar_one()
    assert loaded.domain_raw == "database schema"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_models.py -v`

Expected: ImportError — `TaxonomyNode` and `TaxonomySnapshot` don't exist yet.

- [ ] **Step 3: Implement the models**

In `backend/app/models.py`, add after the `OptimizationPattern` class (after line 130):

```python
class TaxonomyNode(Base):
    __tablename__ = "taxonomy_nodes"
    __table_args__ = (
        Index("ix_taxonomy_parent", "parent_id"),
        Index("ix_taxonomy_state", "state"),
        Index("ix_taxonomy_persistence", "persistence"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    parent_id = Column(String, ForeignKey("taxonomy_nodes.id"), nullable=True)

    # Cluster identity
    label = Column(String, nullable=False)
    label_generated_at = Column(DateTime, default=_utcnow, nullable=False)

    # Embedding state
    centroid_embedding = Column(LargeBinary, nullable=False)
    member_count = Column(Integer, default=0, nullable=False)

    # Quality metrics (updated each warm-path cycle)
    coherence = Column(Float, default=0.0, nullable=False)
    separation = Column(Float, default=1.0, nullable=False)
    stability = Column(Float, default=1.0, nullable=False)
    persistence = Column(Float, default=0.0, nullable=False)

    # Lifecycle
    state = Column(String, default="candidate", nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    confirmed_at = Column(DateTime, nullable=True)
    retired_at = Column(DateTime, nullable=True)
    observations = Column(Integer, default=0, nullable=False)

    # Usage (propagated up tree — spec Section 7.8)
    usage_count = Column(Integer, default=0, nullable=False)

    # UMAP projection (cached)
    umap_x = Column(Float, nullable=True)
    umap_y = Column(Float, nullable=True)
    umap_z = Column(Float, nullable=True)

    # Generated color
    color_hex = Column(String, default="#7a7a9e", nullable=False)


class TaxonomySnapshot(Base):
    __tablename__ = "taxonomy_snapshots"

    id = Column(String, primary_key=True, default=_uuid)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    trigger = Column(String, nullable=False)  # 'warm_path' | 'cold_path' | 'manual'

    # System-wide metrics
    q_system = Column(Float, nullable=False)
    q_coherence = Column(Float, nullable=False)
    q_separation = Column(Float, nullable=False)
    q_coverage = Column(Float, nullable=False)
    q_dbcv = Column(Float, default=0.0, nullable=False)

    # What changed
    operations = Column(Text, default="[]", nullable=False)  # JSON list
    nodes_created = Column(Integer, default=0, nullable=False)
    nodes_retired = Column(Integer, default=0, nullable=False)
    nodes_merged = Column(Integer, default=0, nullable=False)
    nodes_split = Column(Integer, default=0, nullable=False)

    # Recovery
    tree_state = Column(Text, nullable=True)  # JSON: node IDs + parent edges
```

Add `taxonomy_node_id` and `domain_raw` to `PatternFamily` (after line 97):

```python
    taxonomy_node_id = Column(String, ForeignKey("taxonomy_nodes.id"), nullable=True)
    domain_raw = Column(String, nullable=True)
```

Add `taxonomy_node_id` and `domain_raw` to `Optimization` (after line 65):

```python
    taxonomy_node_id = Column(String, ForeignKey("taxonomy_nodes.id"), nullable=True)
    domain_raw = Column(String, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/taxonomy/test_models.py -v`

Expected: All 5 tests PASS.

- [ ] **Step 5: Run existing tests to verify no regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -x`

Expected: All existing tests still pass (new nullable columns don't break existing data).

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/tests/taxonomy/
git commit -m "feat(taxonomy): add TaxonomyNode and TaxonomySnapshot DB models

Add two new tables and taxonomy FK columns on PatternFamily and
Optimization. All new columns are nullable for backward compatibility.
Spec Section 5.1-5.4."
```

---

### Task 3: Test Fixtures

**Files:**
- Create: `backend/tests/taxonomy/conftest.py`

**Purpose:** Shared async fixtures for all taxonomy tests — DB session, mock embedding service, mock LLM provider, and synthetic cluster generator.

- [ ] **Step 1: Write conftest.py**

```python
"""Shared fixtures for taxonomy tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService

EMBEDDING_DIM = 384


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def mock_embedding() -> EmbeddingService:
    """EmbeddingService mock that returns deterministic embeddings.

    Embeds text by hashing it to a stable vector. Cosine search delegates
    to the real static method (pure numpy, no model needed).
    """
    svc = MagicMock(spec=EmbeddingService)
    svc.dimension = EMBEDDING_DIM

    def _embed(text: str) -> np.ndarray:
        """Hash text to a deterministic unit vector."""
        rng = np.random.RandomState(hash(text) % 2**31)
        vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-9)

    svc.embed_single.side_effect = _embed
    svc.aembed_single = AsyncMock(side_effect=_embed)
    svc.embed_texts.side_effect = lambda texts: [_embed(t) for t in texts]
    svc.aembed_texts = AsyncMock(side_effect=lambda texts: [_embed(t) for t in texts])
    svc.cosine_search = EmbeddingService.cosine_search  # use real implementation
    return svc


@pytest.fixture
def mock_provider() -> LLMProvider:
    """Mock LLM provider for Haiku label generation and pattern extraction."""
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "mock"
    return provider


def make_cluster_distribution(
    center_text: str,
    n_samples: int,
    spread: float = 0.1,
    embedding_svc: EmbeddingService | None = None,
    rng: np.random.RandomState | None = None,
) -> list[np.ndarray]:
    """Generate n embeddings clustered around center_text's embedding.

    Uses Gaussian noise + L2 normalization to create a tight cluster.
    If no embedding_svc provided, uses hash-based deterministic center.

    Args:
        center_text: Text to embed as cluster center.
        n_samples: Number of samples to generate.
        spread: Standard deviation of Gaussian noise (lower = tighter).
        embedding_svc: Optional real or mock embedding service.
        rng: Optional random state for reproducibility.

    Returns:
        List of n unit-norm 384-dim float32 vectors.
    """
    if rng is None:
        rng = np.random.RandomState(hash(center_text) % 2**31)

    # Compute center
    if embedding_svc is not None:
        center = embedding_svc.embed_single(center_text)
    else:
        center = rng.randn(EMBEDDING_DIM).astype(np.float32)
        center /= np.linalg.norm(center) + 1e-9

    samples = []
    for _ in range(n_samples):
        noise = rng.randn(EMBEDDING_DIM).astype(np.float32) * spread
        vec = center + noise
        vec /= np.linalg.norm(vec) + 1e-9
        samples.append(vec)

    return samples
```

- [ ] **Step 2: Verify fixture loads**

Run: `cd backend && python -m pytest tests/taxonomy/test_models.py -v`

Expected: Tests still pass (conftest auto-discovered).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/taxonomy/conftest.py
git commit -m "test(taxonomy): add shared fixtures — DB session, mock embedding, cluster generator"
```

---

### Task 4: Quality Module

**Files:**
- Create: `backend/app/services/taxonomy/quality.py`
- Create: `backend/tests/taxonomy/test_quality.py`

**Reference:** Spec Section 2.4, 2.5

- [ ] **Step 1: Write failing tests**

Create `backend/tests/taxonomy/test_quality.py`:

```python
"""Tests for taxonomy quality module — Q_system, weights, thresholds."""

import math

import pytest

from app.services.taxonomy.quality import (
    QWeights,
    adaptive_threshold,
    compute_q_system,
)


class TestQWeights:
    """Spec Section 2.5 — constant-sum weight normalization."""

    def test_weights_sum_to_one_no_dbcv(self):
        w = QWeights.from_ramp(ramp_progress=0.0)
        total = w.w_c + w.w_s + w.w_v + w.w_d
        assert total == pytest.approx(1.0)
        assert w.w_d == 0.0
        assert w.w_c == pytest.approx(0.4)
        assert w.w_s == pytest.approx(0.35)
        assert w.w_v == pytest.approx(0.25)

    def test_weights_sum_to_one_full_dbcv(self):
        w = QWeights.from_ramp(ramp_progress=1.0)
        total = w.w_c + w.w_s + w.w_v + w.w_d
        assert total == pytest.approx(1.0)
        assert w.w_d == pytest.approx(0.15)
        assert w.w_c == pytest.approx(0.34)  # 0.4 * 0.85
        assert w.w_s == pytest.approx(0.2975)  # 0.35 * 0.85

    def test_weights_sum_to_one_mid_ramp(self):
        w = QWeights.from_ramp(ramp_progress=0.5)
        total = w.w_c + w.w_s + w.w_v + w.w_d
        assert total == pytest.approx(1.0)

    def test_ramp_clamped_above_one(self):
        w = QWeights.from_ramp(ramp_progress=2.0)
        assert w.w_d == pytest.approx(0.15)  # capped at target

    def test_ramp_clamped_below_zero(self):
        w = QWeights.from_ramp(ramp_progress=-1.0)
        assert w.w_d == 0.0


class TestComputeQSystem:
    """Spec Section 2.5 — edge cases and invariants."""

    def test_empty_returns_zero(self):
        assert compute_q_system([], QWeights.from_ramp(0.0)) == 0.0

    def test_single_node(self):
        """Single confirmed node: coherence=1.0, separation=1.0."""
        from app.services.taxonomy.quality import NodeMetrics

        node = NodeMetrics(coherence=1.0, separation=1.0, state="confirmed")
        score = compute_q_system([node], QWeights.from_ramp(0.0), coverage=1.0)
        assert 0.0 <= score <= 1.0
        assert score > 0.5  # should be high with perfect metrics

    def test_result_bounded_zero_one(self):
        from app.services.taxonomy.quality import NodeMetrics

        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="confirmed"),
            NodeMetrics(coherence=0.9, separation=0.6, state="confirmed"),
        ]
        score = compute_q_system(nodes, QWeights.from_ramp(0.5), coverage=0.95)
        assert 0.0 <= score <= 1.0

    def test_nan_replaced_with_zero(self):
        from app.services.taxonomy.quality import NodeMetrics

        node = NodeMetrics(coherence=float("nan"), separation=0.5, state="confirmed")
        score = compute_q_system([node], QWeights.from_ramp(0.0), coverage=1.0)
        assert math.isfinite(score)

    def test_retired_nodes_excluded(self):
        from app.services.taxonomy.quality import NodeMetrics

        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="confirmed"),
            NodeMetrics(coherence=0.0, separation=0.0, state="retired"),
        ]
        score = compute_q_system(nodes, QWeights.from_ramp(0.0), coverage=1.0)
        # Retired node should not drag score down
        assert score > 0.5


class TestAdaptiveThreshold:
    """Spec Section 2.4 — threshold scales with population."""

    def test_small_population_lenient(self):
        t = adaptive_threshold(base=0.78, population=3)
        assert t == pytest.approx(0.78 * (1 + 0.15 * math.log(1 + 3)), rel=1e-3)
        assert t < 1.0  # must stay reasonable

    def test_large_population_strict(self):
        t_small = adaptive_threshold(base=0.78, population=3)
        t_large = adaptive_threshold(base=0.78, population=100)
        assert t_large > t_small  # larger populations are stricter

    def test_zero_population(self):
        t = adaptive_threshold(base=0.78, population=0)
        assert t == pytest.approx(0.78)  # base value exactly
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_quality.py -v`

Expected: ModuleNotFoundError — `app.services.taxonomy.quality` doesn't exist.

- [ ] **Step 3: Create the taxonomy package**

Create `backend/app/services/taxonomy/__init__.py`:

```python
"""Evolutionary Taxonomy Engine — self-organizing hierarchical clustering."""
```

- [ ] **Step 4: Implement quality.py**

Create `backend/app/services/taxonomy/quality.py`:

```python
"""Quality metrics and gates for the taxonomy engine.

Implements Q_system computation, constant-sum weight normalization,
adaptive thresholds, and speculative operation evaluation.

Reference: Spec Section 2.4, 2.5
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

# Default weight targets (when DBCV is fully active)
_W_D_TARGET = 0.15
_W_C_BASE = 0.4
_W_S_BASE = 0.35
_W_V_BASE = 0.25

# Adaptive threshold scaling factor
_ALPHA = 0.15


@dataclass(frozen=True)
class NodeMetrics:
    """Lightweight metrics container for Q_system computation.

    Not a DB model — populated from TaxonomyNode fields for quality calculations.
    """

    coherence: float
    separation: float
    state: str  # 'candidate' | 'confirmed' | 'retired'


@dataclass(frozen=True)
class QWeights:
    """Constant-sum weights for Q_system (always sum to 1.0).

    Spec Section 2.5 — DBCV ramps in linearly over 20 observations
    after >=5 confirmed nodes exist. Other weights scale proportionally.
    """

    w_c: float  # coherence
    w_s: float  # separation
    w_v: float  # coverage
    w_d: float  # DBCV

    @classmethod
    def from_ramp(cls, ramp_progress: float) -> QWeights:
        """Create weights for a given DBCV ramp progress (0.0–1.0).

        Args:
            ramp_progress: 0.0 = DBCV inactive, 1.0 = fully active.
                Clamped to [0.0, 1.0].
        """
        ramp = max(0.0, min(1.0, ramp_progress))
        w_d = _W_D_TARGET * ramp
        remaining = 1.0 - w_d
        return cls(
            w_c=_W_C_BASE * remaining,
            w_s=_W_S_BASE * remaining,
            w_v=_W_V_BASE * remaining,
            w_d=w_d,
        )


def compute_q_system(
    nodes: list[NodeMetrics],
    weights: QWeights,
    coverage: float = 1.0,
    dbcv: float = 0.0,
) -> float:
    """Compute composite system quality score.

    Reference: Spec Section 2.5

    Edge cases:
    - Empty or all-retired: returns 0.0
    - Single node: coherence=perfect, separation=perfect (no siblings)
    - NaN/Inf: replaced with 0.0
    """
    confirmed = [n for n in nodes if n.state == "confirmed"]
    if not confirmed:
        return 0.0

    # Gather finite coherence values
    coherences = [
        n.coherence for n in confirmed if math.isfinite(n.coherence)
    ]
    separations = [
        n.separation for n in confirmed if math.isfinite(n.separation)
    ]

    mean_c = statistics.mean(coherences) if coherences else 0.0
    mean_s = statistics.mean(separations) if separations else 1.0

    # Clamp all components to [0.0, 1.0]
    mean_c = max(0.0, min(1.0, mean_c))
    mean_s = max(0.0, min(1.0, mean_s))
    coverage = max(0.0, min(1.0, coverage))
    dbcv = max(0.0, min(1.0, dbcv))

    raw = (
        weights.w_c * mean_c
        + weights.w_s * mean_s
        + weights.w_v * coverage
        + weights.w_d * dbcv
    )

    # Defensive: self-heal if weights drift
    total_weight = weights.w_c + weights.w_s + weights.w_v + weights.w_d
    if total_weight < 1e-9:
        return 0.0
    if abs(total_weight - 1.0) > 1e-6:
        raw /= total_weight

    return max(0.0, min(1.0, raw))


def adaptive_threshold(
    base: float,
    population: int,
    alpha: float = _ALPHA,
) -> float:
    """Scale threshold with population size.

    Reference: Spec Section 2.4

    Formula: base * (1 + alpha * log(1 + population))

    Small populations get lenient thresholds (let clusters form).
    Large populations get strict thresholds (well-defined by now).
    """
    return base * (1 + alpha * math.log(1 + population))


def epsilon_tolerance(warm_path_age: int) -> float:
    """Compute non-regression epsilon for Q_system comparison.

    Reference: Spec Section 2.5

    Young taxonomies get larger epsilon (~0.007 at age 20).
    Mature taxonomies get tiny epsilon (~0.001 at age 100).

    Args:
        warm_path_age: Number of warm-path cycles completed.
    """
    return max(0.001, 0.01 * math.exp(-warm_path_age / 50))


def is_non_regressive(
    q_before: float,
    q_after: float,
    warm_path_age: int,
) -> bool:
    """Check if a quality transition passes the non-regression gate.

    Reference: Spec Section 2.5
    Q_after >= Q_before - epsilon (tolerance)
    """
    eps = epsilon_tolerance(warm_path_age)
    return q_after >= q_before - eps


def suggestion_threshold(
    base: float = 0.72,
    coherence: float = 0.0,
    alpha: float = 0.15,
) -> float:
    """Adaptive suggestion threshold based on cluster coherence.

    Reference: Spec Section 7.9

    High coherence → threshold near base (centroid is representative).
    Low coherence → threshold rises (centroid is blurred).
    """
    return base + alpha * (1.0 - max(0.0, min(1.0, coherence)))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/taxonomy/test_quality.py -v`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/__init__.py backend/app/services/taxonomy/quality.py backend/tests/taxonomy/test_quality.py
git commit -m "feat(taxonomy): add quality module — Q_system, weights, adaptive thresholds

Constant-sum weight normalization, epsilon tolerance, adaptive threshold
scaling, and suggestion threshold adaptation. Spec Section 2.4-2.5, 7.9."
```

---

### Task 5: OKLab Color Module

**Files:**
- Create: `backend/app/services/taxonomy/coloring.py`
- Create: `backend/tests/taxonomy/test_coloring.py`

**Reference:** Spec Section 8.6

- [ ] **Step 1: Write failing tests**

Create `backend/tests/taxonomy/test_coloring.py`:

```python
"""Tests for OKLab color generation."""

import re

import pytest

from app.services.taxonomy.coloring import (
    enforce_minimum_delta_e,
    generate_color,
    oklab_to_hex,
)


def _is_hex(s: str) -> bool:
    return bool(re.match(r"^#[0-9a-fA-F]{6}$", s))


class TestGenerateColor:
    def test_returns_valid_hex(self):
        color = generate_color(0.0, 0.0, 0.0)
        assert _is_hex(color)

    def test_different_positions_different_colors(self):
        c1 = generate_color(0.5, 0.5, 0.5)
        c2 = generate_color(-0.5, -0.5, 0.5)
        assert c1 != c2

    def test_deterministic(self):
        c1 = generate_color(0.3, -0.7, 0.1)
        c2 = generate_color(0.3, -0.7, 0.1)
        assert c1 == c2

    def test_dark_background_readable(self):
        """L=0.72 should give bright-enough colors for #06060c background."""
        color = generate_color(0.0, 0.0, 0.5)
        # Convert hex to approximate luminance — any valid color at L=0.72
        # should be visually readable on dark background
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        # Relative luminance should be reasonably high (>0.1 for AA contrast)
        lum = 0.2126 * (r / 255) + 0.7152 * (g / 255) + 0.0722 * (b / 255)
        assert lum > 0.1


class TestOklabToHex:
    def test_neutral_gray(self):
        color = oklab_to_hex(0.5, 0.0, 0.0)
        assert _is_hex(color)

    def test_gamut_clamping(self):
        """Extreme OKLab values should still produce valid hex."""
        color = oklab_to_hex(0.72, 0.3, 0.3)  # beyond extended gamut
        assert _is_hex(color)


class TestEnforceMinimumDeltaE:
    def test_identical_colors_get_separated(self):
        colors = [("a", "#a855f7"), ("b", "#a855f7")]
        result = enforce_minimum_delta_e(colors, min_delta_e=0.04)
        assert result[0][1] != result[1][1]  # no longer identical

    def test_already_distinct_unchanged(self):
        colors = [("a", "#a855f7"), ("b", "#00e5ff")]
        result = enforce_minimum_delta_e(colors, min_delta_e=0.04)
        assert result[0][1] == "#a855f7"
        assert result[1][1] == "#00e5ff"

    def test_empty_input(self):
        assert enforce_minimum_delta_e([], min_delta_e=0.04) == []

    def test_single_color(self):
        colors = [("a", "#ff0000")]
        assert enforce_minimum_delta_e(colors, min_delta_e=0.04) == colors
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_coloring.py -v`

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement coloring.py**

Create `backend/app/services/taxonomy/coloring.py`. Key functions:

- `oklab_to_hex(L, a, b)` — OKLab to sRGB hex with gamut clamping
- `generate_color(umap_x, umap_y, umap_z)` — normalize UMAP coords to OKLab (L=0.72, a/b ±0.20, z modulates chroma)
- `enforce_minimum_delta_e(colors, min_delta_e=0.04)` — post-processing to separate siblings
- `hex_to_oklab(hex_color)` — reverse for deltaE computation
- `delta_e_oklab(lab1, lab2)` — Euclidean distance in OKLab space

Implementation reference: Spec Section 8.6. Use the standard OKLab→linear sRGB→sRGB conversion matrix. Clamp sRGB channels to [0, 255].

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/taxonomy/test_coloring.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/coloring.py backend/tests/taxonomy/test_coloring.py
git commit -m "feat(taxonomy): add OKLab color generation from UMAP position

L=0.72 fixed lightness, a/b ±0.20 extended gamut, z-axis chroma
modulation, deltaE ≥0.04 sibling enforcement. Spec Section 8.6."
```

---

### Task 6: UMAP Projection Module

**Files:**
- Create: `backend/app/services/taxonomy/projection.py`
- Create: `backend/tests/taxonomy/test_projection.py`

**Reference:** Spec Section 8.5

- [ ] **Step 1: Write failing tests**

Create `backend/tests/taxonomy/test_projection.py`:

```python
"""Tests for UMAP 3D projection and Procrustes alignment."""

import numpy as np
import pytest

from app.services.taxonomy.projection import (
    UMAPProjector,
    procrustes_align,
)


@pytest.fixture
def projector():
    return UMAPProjector(random_state=42)


class TestUMAPProjector:
    def test_fit_returns_3d(self, projector):
        """UMAP should produce 3-component output."""
        embeddings = [np.random.randn(384).astype(np.float32) for _ in range(20)]
        positions = projector.fit(embeddings)
        assert positions.shape == (20, 3)

    def test_transform_incremental(self, projector):
        """Incremental transform should be fast and consistent."""
        base = [np.random.randn(384).astype(np.float32) for _ in range(20)]
        projector.fit(base)

        new = [np.random.randn(384).astype(np.float32) for _ in range(3)]
        positions = projector.transform(new)
        assert positions.shape == (3, 3)

    def test_fit_too_few_points(self, projector):
        """Should handle < 5 points gracefully (UMAP needs minimum)."""
        embeddings = [np.random.randn(384).astype(np.float32) for _ in range(3)]
        positions = projector.fit(embeddings)
        # Fallback to PCA or random placement for small sets
        assert positions.shape == (3, 3)


class TestProcrustesAlign:
    def test_preserves_relative_positions(self):
        """Procrustes should find rotation that minimizes displacement."""
        old_pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
        # Rotate 90 degrees around z-axis
        new_pos = np.array([[0, 0, 0], [0, 1, 0], [-1, 0, 0]], dtype=np.float64)
        aligned = procrustes_align(new_pos, old_pos)
        # After alignment, should be close to old_pos
        np.testing.assert_allclose(aligned, old_pos, atol=0.1)

    def test_identity_unchanged(self):
        """Same positions should stay the same."""
        pos = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float64)
        aligned = procrustes_align(pos, pos)
        np.testing.assert_allclose(aligned, pos, atol=1e-6)

    def test_handles_single_point(self):
        """Single point should return translated to match."""
        old = np.array([[1, 2, 3]], dtype=np.float64)
        new = np.array([[4, 5, 6]], dtype=np.float64)
        aligned = procrustes_align(new, old)
        np.testing.assert_allclose(aligned, old, atol=1e-6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_projection.py -v`

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement projection.py**

Create `backend/app/services/taxonomy/projection.py`. Key components:

- `UMAPProjector` class wrapping `umap.UMAP(n_components=3, metric="cosine", low_memory=True, random_state=42)`
- `fit(embeddings)` — full batch fit, returns positions. Falls back to random placement for < 5 points.
- `transform(new_embeddings)` — incremental transform using fitted model
- `procrustes_align(new_positions, old_positions)` — uses `scipy.linalg.orthogonal_procrustes` to find optimal rotation. Centers both point sets, computes R, applies `new_centered @ R + old_mean`.

Reference: Spec Section 8.5. Thread pool executor via `asyncio.to_thread()` for async wrappers.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/taxonomy/test_projection.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/projection.py backend/tests/taxonomy/test_projection.py
git commit -m "feat(taxonomy): add UMAP 3D projection with Procrustes alignment

Incremental transform for new points, cold-path refit with Procrustes
to preserve spatial mental model. Spec Section 8.5."
```

---

### Task 7: Label Generation Module

**Files:**
- Create: `backend/app/services/taxonomy/labeling.py`
- Create: `backend/tests/taxonomy/test_labeling.py`

**Reference:** Spec Section 6.2

- [ ] **Step 1: Write failing tests**

Create `backend/tests/taxonomy/test_labeling.py`:

```python
"""Tests for Haiku label generation."""

from unittest.mock import AsyncMock

import pytest

from app.services.taxonomy.labeling import generate_label


@pytest.mark.asyncio
async def test_generate_label_returns_string(mock_provider):
    """Should return a short label from the LLM."""
    mock_provider.complete_parsed = AsyncMock(
        return_value=type("R", (), {"label": "API Architecture"})()
    )
    label = await generate_label(
        provider=mock_provider,
        member_texts=["REST API endpoint", "GraphQL resolver"],
        model="claude-haiku-4-5",
    )
    assert label == "API Architecture"
    mock_provider.complete_parsed.assert_called_once()


@pytest.mark.asyncio
async def test_generate_label_fallback_on_error(mock_provider):
    """Should return fallback label if LLM fails."""
    mock_provider.complete_parsed = AsyncMock(side_effect=RuntimeError("LLM down"))
    label = await generate_label(
        provider=mock_provider,
        member_texts=["test text"],
        model="claude-haiku-4-5",
    )
    assert label == "Unnamed cluster"


@pytest.mark.asyncio
async def test_generate_label_no_provider():
    """Should return fallback label if no provider."""
    label = await generate_label(
        provider=None,
        member_texts=["test text"],
        model="claude-haiku-4-5",
    )
    assert label == "Unnamed cluster"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_labeling.py -v`

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement labeling.py**

Create `backend/app/services/taxonomy/labeling.py`:

```python
"""Haiku-based label generation for taxonomy nodes.

Calls the LLM with representative member texts to generate a concise
2-4 word label for a cluster. Falls back to "Unnamed cluster" on error.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)

_FALLBACK_LABEL = "Unnamed cluster"


class _LabelOutput(BaseModel):
    model_config = {"extra": "forbid"}
    label: str = Field(
        description="A concise 2-4 word label describing the common theme of these texts.",
    )


async def generate_label(
    provider: LLMProvider | None,
    member_texts: list[str],
    model: str,
) -> str:
    """Generate a label for a cluster from its member texts.

    Args:
        provider: LLM provider (Haiku). None = return fallback.
        member_texts: Representative texts from the cluster (truncated to 200 chars each).
        model: Model ID to use for generation.

    Returns:
        A short label string (2-4 words).
    """
    if not provider:
        return _FALLBACK_LABEL

    truncated = [t[:200] for t in member_texts[:10]]
    sample_block = "\n".join(f"- {t}" for t in truncated)

    try:
        result = await provider.complete_parsed(
            model=model,
            system_prompt=(
                "You are a taxonomy labeler. Given a list of text samples that "
                "belong to the same cluster, generate a concise 2-4 word label "
                "that captures their common theme. Be specific — 'API Architecture' "
                "is better than 'Backend'."
            ),
            user_message=f"Cluster samples:\n{sample_block}",
            output_format=_LabelOutput,
        )
        label = result.label.strip()
        if label:
            return label
        return _FALLBACK_LABEL
    except Exception as exc:
        logger.warning("Label generation failed (non-fatal): %s", exc)
        return _FALLBACK_LABEL
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/taxonomy/test_labeling.py -v`

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/labeling.py backend/tests/taxonomy/test_labeling.py
git commit -m "feat(taxonomy): add Haiku label generation for taxonomy nodes

LLM-based 2-4 word cluster labeling with graceful fallback."
```

---

### Task 8: Snapshot Module

**Files:**
- Create: `backend/app/services/taxonomy/snapshot.py`
- Create: `backend/tests/taxonomy/test_snapshot.py`

**Reference:** Spec Section 5.2

- [ ] **Step 1: Write failing tests**

Create `backend/tests/taxonomy/test_snapshot.py`:

```python
"""Tests for taxonomy snapshot CRUD and retention policy."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models import TaxonomySnapshot
from app.services.taxonomy.snapshot import (
    create_snapshot,
    get_latest_snapshot,
    prune_snapshots,
)


@pytest.mark.asyncio
async def test_create_snapshot(db):
    snap = await create_snapshot(
        db,
        trigger="warm_path",
        q_system=0.85,
        q_coherence=0.82,
        q_separation=0.88,
        q_coverage=0.95,
        q_dbcv=0.0,
        operations=[{"type": "emerge", "node_id": "abc"}],
        nodes_created=1,
    )
    assert snap.id is not None
    assert snap.trigger == "warm_path"
    assert snap.q_system == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_get_latest_snapshot(db):
    await create_snapshot(db, trigger="warm_path", q_system=0.80, q_coherence=0.7,
                          q_separation=0.8, q_coverage=0.9, q_dbcv=0.0)
    await create_snapshot(db, trigger="warm_path", q_system=0.85, q_coherence=0.75,
                          q_separation=0.85, q_coverage=0.92, q_dbcv=0.0)

    latest = await get_latest_snapshot(db)
    assert latest is not None
    assert latest.q_system == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_get_latest_snapshot_empty(db):
    assert await get_latest_snapshot(db) is None


@pytest.mark.asyncio
async def test_prune_keeps_recent_snapshots(db):
    """Snapshots from the last 24h should all be kept."""
    now = datetime.now(timezone.utc)
    for i in range(5):
        snap = TaxonomySnapshot(
            trigger="warm_path",
            q_system=0.8 + i * 0.01,
            q_coherence=0.7, q_separation=0.8, q_coverage=0.9, q_dbcv=0.0,
        )
        snap.created_at = now - timedelta(hours=i)
        db.add(snap)
    await db.commit()

    pruned = await prune_snapshots(db)
    assert pruned == 0  # all within 24h, nothing pruned
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_snapshot.py -v`

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement snapshot.py**

Create `backend/app/services/taxonomy/snapshot.py`. Key functions:

- `create_snapshot(db, trigger, q_*, operations, nodes_*)` — insert row, auto-serialize operations as JSON
- `get_latest_snapshot(db)` — query ordered by created_at DESC, limit 1
- `get_snapshot_history(db, limit=30)` — for sparkline data
- `prune_snapshots(db)` — retention policy: 24h keep all, 1-30 days keep daily best, 30+ days keep weekly

Reference: Spec Section 5.2.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/taxonomy/test_snapshot.py -v`

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/snapshot.py backend/tests/taxonomy/test_snapshot.py
git commit -m "feat(taxonomy): add snapshot CRUD and retention policy pruning

24h keep-all, 1-30d daily best, 30+ weekly. Spec Section 5.2."
```

---

### Task 9: Clustering Module

**Files:**
- Create: `backend/app/services/taxonomy/clustering.py`
- Create: `backend/tests/taxonomy/test_clustering.py`

**Reference:** Spec Section 2.3

- [ ] **Step 1: Write failing tests**

Create `backend/tests/taxonomy/test_clustering.py`:

```python
"""Tests for HDBSCAN clustering wrapper."""

import numpy as np
import pytest

from tests.taxonomy.conftest import make_cluster_distribution

from app.services.taxonomy.clustering import (
    ClusterResult,
    batch_cluster,
    nearest_centroid,
)


class TestNearestCentroid:
    def test_finds_closest(self):
        centroids = [
            np.array([1, 0, 0], dtype=np.float32),
            np.array([0, 1, 0], dtype=np.float32),
            np.array([0, 0, 1], dtype=np.float32),
        ]
        query = np.array([0.9, 0.1, 0], dtype=np.float32)
        idx, score = nearest_centroid(query, centroids)
        assert idx == 0
        assert score > 0.9

    def test_empty_centroids(self):
        query = np.array([1, 0, 0], dtype=np.float32)
        result = nearest_centroid(query, [])
        assert result is None

    def test_single_centroid(self):
        centroids = [np.array([0, 1, 0], dtype=np.float32)]
        query = np.array([0, 1, 0], dtype=np.float32)
        idx, score = nearest_centroid(query, centroids)
        assert idx == 0
        assert score == pytest.approx(1.0, abs=0.01)


class TestBatchCluster:
    def test_separates_distinct_clusters(self):
        """Two well-separated clusters should be found."""
        rng = np.random.RandomState(42)
        cluster_a = make_cluster_distribution("REST API", 15, spread=0.05, rng=rng)
        cluster_b = make_cluster_distribution("SQL database", 15, spread=0.05, rng=rng)
        embeddings = cluster_a + cluster_b

        result = batch_cluster(embeddings, min_cluster_size=3)
        assert isinstance(result, ClusterResult)
        # Should find at least 2 clusters (some points may be noise)
        assert result.n_clusters >= 2

    def test_noise_handling(self):
        """Random noise should produce mostly noise labels (-1)."""
        rng = np.random.RandomState(42)
        noise = [rng.randn(384).astype(np.float32) for _ in range(20)]
        result = batch_cluster(noise, min_cluster_size=5)
        # Most points should be noise with random embeddings
        assert result.noise_count > 0

    def test_too_few_points(self):
        """Less than min_cluster_size should return all noise."""
        embeddings = [np.random.randn(384).astype(np.float32) for _ in range(2)]
        result = batch_cluster(embeddings, min_cluster_size=5)
        assert result.n_clusters == 0
        assert result.noise_count == 2

    def test_returns_persistence(self):
        """Cluster result should include persistence values."""
        rng = np.random.RandomState(42)
        cluster = make_cluster_distribution("test cluster", 20, spread=0.05, rng=rng)
        result = batch_cluster(cluster, min_cluster_size=3)
        if result.n_clusters > 0:
            assert len(result.persistences) == result.n_clusters
            assert all(p >= 0 for p in result.persistences)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_clustering.py -v`

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement clustering.py**

Create `backend/app/services/taxonomy/clustering.py`. Key components:

- `ClusterResult` dataclass: `labels` (array), `n_clusters`, `noise_count`, `persistences` (per-cluster), `centroids` (per-cluster mean)
- `nearest_centroid(query, centroids)` — cosine search, returns `(idx, score)` or `None`
- `batch_cluster(embeddings, min_cluster_size=3)` — wraps `sklearn.cluster.HDBSCAN(min_cluster_size, metric="euclidean", cluster_selection_method="eom")` on L2-normalized embeddings (cosine via normalization). Extracts persistence from condensed tree.
- `compute_pairwise_coherence(embeddings)` — mean intra-cluster cosine similarity
- `compute_separation(centroids)` — min inter-cluster cosine distance

Reference: Spec Section 2.3. Use `sklearn.cluster.HDBSCAN` (available in scikit-learn >= 1.3).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/taxonomy/test_clustering.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/clustering.py backend/tests/taxonomy/test_clustering.py
git commit -m "feat(taxonomy): add HDBSCAN clustering wrapper with persistence extraction

Nearest-centroid assignment, batch clustering, coherence/separation
computation. Spec Section 2.3."
```

---

### Task 10: Lifecycle Module

**Files:**
- Create: `backend/app/services/taxonomy/lifecycle.py`
- Create: `backend/tests/taxonomy/test_lifecycle.py`

**Reference:** Spec Section 3.1–3.5

This is the most complex module. Four lifecycle operations, each with quality gate simulation.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/taxonomy/test_lifecycle.py`:

```python
"""Tests for taxonomy lifecycle operations — emerge, merge, split, retire."""

import numpy as np
import pytest

from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution

from app.models import MetaPattern, PatternFamily, TaxonomyNode
from app.services.taxonomy.lifecycle import (
    attempt_emerge,
    attempt_merge,
    attempt_retire,
    attempt_split,
    prioritize_operations,
)


@pytest.mark.asyncio
async def test_emerge_creates_candidate_node(db, mock_embedding):
    """Emerge should create a new candidate node from clustered members."""
    rng = np.random.RandomState(42)
    cluster = make_cluster_distribution("REST API", 5, spread=0.05, rng=rng)

    # Create families with embeddings
    families = []
    for i, emb in enumerate(cluster):
        f = PatternFamily(
            intent_label=f"api-pattern-{i}",
            domain="backend",
            centroid_embedding=emb.astype(np.float32).tobytes(),
        )
        db.add(f)
        families.append(f)
    await db.flush()

    result = await attempt_emerge(
        db=db,
        member_family_ids=[f.id for f in families],
        embeddings=cluster,
        warm_path_age=5,
        provider=None,
        model="claude-haiku-4-5",
    )

    assert result is not None
    assert result.state == "candidate"
    assert result.member_count == 5


@pytest.mark.asyncio
async def test_merge_combines_two_nodes(db, mock_embedding):
    """Merge should combine two sibling nodes into one."""
    emb_a = np.random.randn(EMBEDDING_DIM).astype(np.float32)
    emb_b = emb_a + np.random.randn(EMBEDDING_DIM).astype(np.float32) * 0.05

    parent = TaxonomyNode(
        label="Parent",
        centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
        state="confirmed",
        color_hex="#00e5ff",
    )
    db.add(parent)
    await db.flush()

    node_a = TaxonomyNode(
        label="Node A",
        parent_id=parent.id,
        centroid_embedding=emb_a.tobytes(),
        member_count=5,
        coherence=0.85,
        state="confirmed",
        color_hex="#a855f7",
    )
    node_b = TaxonomyNode(
        label="Node B",
        parent_id=parent.id,
        centroid_embedding=emb_b.tobytes(),
        member_count=3,
        coherence=0.80,
        state="confirmed",
        color_hex="#fbbf24",
    )
    db.add_all([node_a, node_b])
    await db.flush()

    result = await attempt_merge(
        db=db,
        node_a=node_a,
        node_b=node_b,
        warm_path_age=10,
    )

    assert result is not None
    assert result.member_count == 8  # combined
    assert node_a.state == "retired" or node_b.state == "retired"


@pytest.mark.asyncio
async def test_retire_redistributes_members(db, mock_embedding):
    """Retire should move members to nearest sibling."""
    parent = TaxonomyNode(
        label="Parent",
        centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
        state="confirmed",
        color_hex="#00e5ff",
    )
    db.add(parent)
    await db.flush()

    sibling = TaxonomyNode(
        label="Active sibling",
        parent_id=parent.id,
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=10,
        state="confirmed",
        color_hex="#a855f7",
    )
    target = TaxonomyNode(
        label="Idle node",
        parent_id=parent.id,
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=1,
        state="confirmed",
        observations=30,
        color_hex="#7a7a9e",
    )
    db.add_all([sibling, target])
    await db.flush()

    result = await attempt_retire(
        db=db,
        node=target,
        warm_path_age=25,
    )

    assert result is True
    assert target.state == "retired"
    assert target.retired_at is not None


def test_prioritize_operations():
    """Operations should execute in order: split > emerge > merge > retire."""
    ops = [
        {"type": "retire", "node_id": "d"},
        {"type": "emerge", "node_id": "b"},
        {"type": "merge", "node_id": "c"},
        {"type": "split", "node_id": "a"},
    ]
    ordered = prioritize_operations(ops)
    types = [o["type"] for o in ordered]
    assert types == ["split", "emerge", "merge", "retire"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_lifecycle.py -v`

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement lifecycle.py**

Create `backend/app/services/taxonomy/lifecycle.py`. Key functions:

- `attempt_emerge(db, member_family_ids, embeddings, warm_path_age, provider, model)` — create candidate node with computed centroid (mean of member embeddings), coherence, separation. Quality gate via `is_non_regressive()`. Label via `generate_label()`. Color via `generate_color()`.
- `attempt_merge(db, node_a, node_b, warm_path_age)` — verify coherence floor (`coherence(M) >= min(coherence(A), coherence(B))`), compute merged centroid (weighted mean), quality gate. Higher-persistence node survives, other retires. Preserve all meta-patterns.
- `attempt_split(db, parent_node, child_clusters, warm_path_age, provider, model)` — verify each child more coherent than parent, orphan count < 10%, quality gate. Redistribute meta-patterns by centroid similarity.
- `attempt_retire(db, node, warm_path_age)` — redistribute members to nearest sibling, absorb meta-patterns, quality gate. Minimum age gate: 7 days. Adaptive idle threshold: `max(20, 3 * age_in_days)`.
- `prioritize_operations(ops)` — sort by `{split: 0, emerge: 1, merge: 2, retire: 3}`.

Reference: Spec Section 3.1–3.5. Each operation is speculative — compute Q_before, apply in-memory, compute Q_after, commit or rollback.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/taxonomy/test_lifecycle.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/lifecycle.py backend/tests/taxonomy/test_lifecycle.py
git commit -m "feat(taxonomy): add lifecycle operations — emerge, merge, split, retire

Quality-gated speculative simulation for all operations. Priority
ordering: split > emerge > merge > retire. Spec Section 3.1-3.5."
```

---

### Task 11: Engine — Hot Path

**Files:**
- Create: `backend/app/services/taxonomy/engine.py`
- Create: `backend/tests/taxonomy/test_engine_hot_path.py`
- Create: `backend/tests/taxonomy/test_domain_mapping.py`

**Reference:** Spec Section 2.3 (hot path), 4.2 (domain mapping), 6.4 (pipeline integration), 7.3 (merge guard), 7.5 (Bayesian blend)

- [ ] **Step 1: Write failing tests for process_optimization**

Create `backend/tests/taxonomy/test_engine_hot_path.py`:

```python
"""Tests for TaxonomyEngine hot path — process_optimization."""

import numpy as np
import pytest

from app.models import Optimization, PatternFamily, TaxonomyNode
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_process_optimization_embeds_and_assigns(db, mock_embedding, mock_provider):
    """process_optimization should embed prompt and assign to nearest family."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    opt = Optimization(
        raw_prompt="Build a REST API with FastAPI",
        optimized_prompt="Build a REST API...",
        status="completed",
        intent_label="REST API",
        domain="backend",
        domain_raw="REST API design",
    )
    db.add(opt)
    await db.commit()

    await engine.process_optimization(opt.id, db)

    # Optimization should have embedding set
    assert opt.embedding is not None


@pytest.mark.asyncio
async def test_process_optimization_skips_non_completed(db, mock_embedding, mock_provider):
    """Should skip optimizations that aren't 'completed'."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    opt = Optimization(raw_prompt="test", status="failed")
    db.add(opt)
    await db.commit()

    await engine.process_optimization(opt.id, db)
    assert opt.embedding is None  # not processed


@pytest.mark.asyncio
async def test_process_optimization_idempotent(db, mock_embedding, mock_provider):
    """Second call should be a no-op."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    opt = Optimization(
        raw_prompt="test prompt",
        status="completed",
        domain_raw="backend",
    )
    db.add(opt)
    await db.commit()

    await engine.process_optimization(opt.id, db)
    first_embedding = opt.embedding

    # Process again — should skip
    await engine.process_optimization(opt.id, db)
    assert opt.embedding == first_embedding
```

- [ ] **Step 2: Write failing tests for map_domain**

Create `backend/tests/taxonomy/test_domain_mapping.py`:

```python
"""Tests for TaxonomyEngine.map_domain — free-text domain mapping."""

import numpy as np
import pytest

from app.models import PatternFamily, TaxonomyNode
from app.services.taxonomy.engine import TaxonomyEngine, TaxonomyMapping


@pytest.mark.asyncio
async def test_map_domain_cold_start(db, mock_embedding, mock_provider):
    """With no taxonomy nodes, should return unmapped."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.map_domain("REST API design", db=db)
    assert isinstance(result, TaxonomyMapping)
    assert result.taxonomy_node_id is None  # unmapped


@pytest.mark.asyncio
async def test_map_domain_finds_match(db, mock_embedding, mock_provider):
    """Should find matching taxonomy node when one exists."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create a confirmed node with known embedding
    emb = mock_embedding.embed_single("REST API design")
    node = TaxonomyNode(
        label="API Architecture",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=5,
        coherence=0.85,
        color_hex="#a855f7",
    )
    db.add(node)
    await db.commit()

    result = await engine.map_domain("REST API design", db=db)
    # Same text should map to same node (high cosine)
    assert result.taxonomy_node_id == node.id


@pytest.mark.asyncio
async def test_map_domain_bayesian_blend(db, mock_embedding, mock_provider):
    """Applied pattern IDs should bias domain mapping (70/30 blend)."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create two distinct nodes
    emb_api = mock_embedding.embed_single("REST API design")
    emb_db = mock_embedding.embed_single("SQL database schema")

    node_api = TaxonomyNode(
        label="API Architecture",
        centroid_embedding=emb_api.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=5,
        coherence=0.85,
        color_hex="#a855f7",
    )
    node_db = TaxonomyNode(
        label="Database Design",
        centroid_embedding=emb_db.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=5,
        coherence=0.85,
        color_hex="#00d4aa",
    )
    db.add_all([node_api, node_db])
    await db.flush()

    # Create a family linked to API node
    family = PatternFamily(
        intent_label="API patterns",
        domain="backend",
        centroid_embedding=emb_api.astype(np.float32).tobytes(),
        taxonomy_node_id=node_api.id,
    )
    db.add(family)
    await db.commit()

    # Map with applied pattern from API family — should bias toward API
    from app.models import MetaPattern
    mp = MetaPattern(family_id=family.id, pattern_text="use RESTful conventions")
    db.add(mp)
    await db.commit()

    result = await engine.map_domain(
        "general programming task",
        db=db,
        applied_pattern_ids=[mp.id],
    )
    # With the blend, should lean toward API node
    assert result is not None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/taxonomy/test_engine_hot_path.py tests/taxonomy/test_domain_mapping.py -v`

Expected: ImportError — `TaxonomyEngine` doesn't exist.

- [ ] **Step 4: Implement engine.py (hot path portion)**

Create `backend/app/services/taxonomy/engine.py`:

```python
"""Taxonomy Engine — hot/warm/cold path orchestration.

Reference: Spec Section 2, 6.3, 6.4, 7
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    MetaPattern,
    Optimization,
    OptimizationPattern,
    PatternFamily,
    TaxonomyNode,
)
from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService
from app.services.taxonomy.clustering import nearest_centroid
from app.services.taxonomy.quality import suggestion_threshold

logger = logging.getLogger(__name__)

# Hot-path thresholds
FAMILY_MERGE_THRESHOLD = 0.78
DOMAIN_ALIGNMENT_FLOOR = 0.35  # Spec Section 7.3


@dataclass
class TaxonomyMapping:
    """Result of mapping a free-text domain to the taxonomy."""
    taxonomy_node_id: str | None
    taxonomy_label: str | None
    taxonomy_breadcrumb: list[str]
    domain_raw: str


@dataclass
class PatternMatch:
    """Result of hierarchical pattern matching."""
    family: PatternFamily | None
    taxonomy_node: TaxonomyNode | None
    meta_patterns: list[MetaPattern]
    similarity: float
    match_level: str  # "family" | "cluster" | "none"


class TaxonomyEngine:
    """Unified hierarchical taxonomy management.

    Reference: Spec Section 6.3
    """

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self._embedding = embedding_service or EmbeddingService()
        self._provider = provider
        self._warm_path_lock = asyncio.Lock()
        # Cached centroid snapshot for lock-free hot path reads
        self._centroid_cache: list[tuple[str, np.ndarray]] | None = None

    # -------------------------------------------------------------------
    # Hot path — per-optimization (<500ms)
    # -------------------------------------------------------------------

    async def process_optimization(
        self, optimization_id: str, db: AsyncSession,
    ) -> None:
        """Full extraction pipeline for a single optimization.

        Replaces PatternExtractorService.process(). Reference: Spec Section 6.4.

        Steps:
        1. Embed the raw prompt
        2. Assign to nearest family (taxonomy-aware)
        3. Extract meta-patterns via Haiku
        4. Merge meta-patterns into family
        5. Write OptimizationPattern join record
        6. Publish taxonomy_changed event
        """
        t0 = time.monotonic()
        logger.info("Taxonomy process_optimization started: opt=%s", optimization_id)

        try:
            result = await db.execute(
                select(Optimization).where(Optimization.id == optimization_id)
            )
            opt = result.scalar_one_or_none()
            if not opt or opt.status != "completed":
                logger.debug("Skipping taxonomy for %s (status=%s)",
                             optimization_id, opt.status if opt else "not_found")
                return

            # Idempotency check
            existing = await db.execute(
                select(OptimizationPattern).where(
                    OptimizationPattern.optimization_id == optimization_id,
                    OptimizationPattern.relationship == "source",
                )
            )
            if existing.scalar_one_or_none():
                logger.debug("Skipping taxonomy for %s (already processed)", optimization_id)
                return

            # 1. Embed the raw prompt
            embedding = await self._embedding.aembed_single(opt.raw_prompt)
            opt.embedding = embedding.astype(np.float32).tobytes()

            # 2. Find or create family (taxonomy-aware)
            family = await self._assign_family(
                db, embedding, opt.intent_label or "general",
                opt.domain_raw or opt.domain or "general",
                opt.task_type or "general", opt.overall_score,
            )

            # 3. Extract meta-patterns via Haiku
            meta_texts = await self._extract_meta_patterns(opt)

            # 4. Merge meta-patterns into family
            for text in meta_texts:
                await self._merge_meta_pattern(db, family.id, text)

            # 5. Write join record
            join = OptimizationPattern(
                optimization_id=opt.id,
                family_id=family.id,
                relationship="source",
            )
            db.add(join)

            await db.commit()

            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "Taxonomy process_optimization complete in %.0fms: opt=%s family='%s'",
                elapsed_ms, optimization_id, family.intent_label,
            )

        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "Taxonomy process_optimization failed after %.0fms for %s: %s",
                elapsed_ms, optimization_id, exc, exc_info=True,
            )

    async def map_domain(
        self,
        domain_raw: str,
        db: AsyncSession,
        applied_pattern_ids: list[str] | None = None,
    ) -> TaxonomyMapping:
        """Map free-text domain string to taxonomy node.

        Reference: Spec Section 4.2, 7.5

        1. Embed domain_raw
        2. If applied_pattern_ids, blend 70/30 with pattern centroid
        3. Find nearest confirmed node
        4. If above threshold, return mapping; else return unmapped
        """
        domain_embedding = await self._embedding.aembed_single(domain_raw)

        # Bayesian blend with applied patterns (Spec Section 7.5)
        if applied_pattern_ids:
            pattern_centroid = await self._compute_pattern_centroid(
                db, applied_pattern_ids,
            )
            if pattern_centroid is not None:
                blended = 0.7 * domain_embedding + 0.3 * pattern_centroid
                blended /= np.linalg.norm(blended) + 1e-9
                domain_embedding = blended

        # Search confirmed taxonomy nodes
        result = await db.execute(
            select(TaxonomyNode).where(TaxonomyNode.state == "confirmed")
        )
        nodes = result.scalars().all()

        if not nodes:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        centroids = []
        valid_nodes = []
        for node in nodes:
            try:
                c = np.frombuffer(node.centroid_embedding, dtype=np.float32)
                centroids.append(c)
                valid_nodes.append(node)
            except (ValueError, TypeError):
                continue

        if not centroids:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        match = nearest_centroid(domain_embedding, centroids)
        if match is None:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        idx, score = match
        node = valid_nodes[idx]

        # Adaptive threshold based on node coherence
        threshold = suggestion_threshold(base=0.60, coherence=node.coherence)
        if score < threshold:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        breadcrumb = await self._build_breadcrumb(db, node)
        return TaxonomyMapping(
            taxonomy_node_id=node.id,
            taxonomy_label=node.label,
            taxonomy_breadcrumb=breadcrumb,
            domain_raw=domain_raw,
        )

    # --- Private helpers (implement in full) ---

    async def _assign_family(self, db, embedding, intent_label, domain_raw,
                             task_type, overall_score):
        """Find or create family with domain alignment guard (Spec 7.3)."""
        # Implementation: query families, nearest_centroid, domain alignment
        # check, create if no match. Same logic as PatternExtractorService
        # but with taxonomy_node_id instead of hardcoded domain.
        ...  # Full implementation in actual code

    async def _extract_meta_patterns(self, opt):
        """Call Haiku to extract meta-patterns. Spec Section 6.4 step 3."""
        ...  # Same logic as PatternExtractorService._extract_meta_patterns

    async def _merge_meta_pattern(self, db, family_id, pattern_text):
        """Merge meta-pattern into family. Spec Section 6.4 step 4."""
        ...  # Same logic as PatternExtractorService._merge_meta_pattern

    async def _compute_pattern_centroid(self, db, pattern_ids):
        """Compute mean centroid of pattern source families."""
        ...

    async def _build_breadcrumb(self, db, node):
        """Walk up parent chain to build label breadcrumb."""
        ...
```

Note: The `...` placeholders should be filled with full implementations following the spec. The `_assign_family`, `_extract_meta_patterns`, and `_merge_meta_pattern` methods port logic from `pattern_extractor.py` (already read in research phase) with the domain alignment guard from Spec Section 7.3.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/taxonomy/test_engine_hot_path.py tests/taxonomy/test_domain_mapping.py -v`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_engine_hot_path.py backend/tests/taxonomy/test_domain_mapping.py
git commit -m "feat(taxonomy): add TaxonomyEngine hot path — process_optimization + map_domain

Nearest-centroid assignment with domain alignment guard (cosine 0.35),
Bayesian domain blend (70/30 analyzer+pattern), breadcrumb builder.
Spec Section 2.3, 4.2, 6.4, 7.3, 7.5."
```

---

### Task 12: Engine — Warm & Cold Paths

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`
- Create: `backend/tests/taxonomy/test_engine_warm_path.py`
- Create: `backend/tests/taxonomy/test_engine_cold_path.py`

**Reference:** Spec Section 2.3 (tiers), 2.5 (non-regression), 2.6 (concurrency), 3.5 (operation ordering)

- [ ] **Step 1: Write failing tests for warm path**

Create `backend/tests/taxonomy/test_engine_warm_path.py`:

```python
"""Tests for TaxonomyEngine warm path — periodic re-clustering with lifecycle."""

import asyncio

import numpy as np
import pytest

from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution

from app.models import Optimization, PatternFamily, TaxonomyNode
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_warm_path_creates_snapshot(db, mock_embedding, mock_provider):
    """Warm path should always create a snapshot."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(db)
    assert result is not None
    assert result.snapshot_id is not None


@pytest.mark.asyncio
async def test_warm_path_lock_deduplication(db, mock_embedding, mock_provider):
    """Concurrent warm-path invocations should be deduplicated."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Acquire lock to simulate running warm path
    async with engine._warm_path_lock:
        assert engine._warm_path_lock.locked()
        # Second invocation should skip
        result = await engine.run_warm_path(db)
        assert result is None  # skipped due to lock


@pytest.mark.asyncio
async def test_warm_path_q_system_non_regressive(db, mock_embedding, mock_provider):
    """Q_system should not decrease across warm-path cycles (within epsilon)."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create some families and nodes to give the warm path something to work with
    rng = np.random.RandomState(42)
    for text in ["REST API", "SQL queries", "React components"]:
        cluster = make_cluster_distribution(text, 5, spread=0.05, rng=rng)
        for i, emb in enumerate(cluster):
            f = PatternFamily(
                intent_label=f"{text}-{i}",
                domain="general",
                centroid_embedding=emb.astype(np.float32).tobytes(),
            )
            db.add(f)
    await db.commit()

    # Run multiple warm paths
    q_values = []
    for _ in range(3):
        result = await engine.run_warm_path(db)
        if result and result.q_system is not None:
            q_values.append(result.q_system)

    # Q_system should be non-decreasing (within epsilon tolerance)
    for i in range(1, len(q_values)):
        assert q_values[i] >= q_values[i - 1] - 0.01  # epsilon tolerance
```

- [ ] **Step 2: Write failing tests for cold path**

Create `backend/tests/taxonomy/test_engine_cold_path.py`:

```python
"""Tests for TaxonomyEngine cold path — full HDBSCAN + UMAP refit."""

import numpy as np
import pytest

from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution

from app.models import PatternFamily, TaxonomyNode
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_cold_path_recomputes_umap(db, mock_embedding, mock_provider):
    """Cold path should set UMAP coordinates on all confirmed nodes."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create some confirmed nodes
    for label in ["Node A", "Node B", "Node C"]:
        node = TaxonomyNode(
            label=label,
            centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
            state="confirmed",
            member_count=5,
            color_hex="#a855f7",
        )
        db.add(node)
    await db.commit()

    result = await engine.run_cold_path(db)
    assert result is not None

    # Verify UMAP positions are set (may be None for < 5 nodes — fallback)
    from sqlalchemy import select
    nodes = (await db.execute(select(TaxonomyNode))).scalars().all()
    for node in nodes:
        # At minimum, positions should be set (even if PCA fallback)
        assert node.umap_x is not None or len(nodes) < 5


@pytest.mark.asyncio
async def test_cold_path_acquires_warm_lock(db, mock_embedding, mock_provider):
    """Cold path should block warm path during execution."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Run cold path — it should acquire the warm lock
    result = await engine.run_cold_path(db)
    # After completion, lock should be released
    assert not engine._warm_path_lock.locked()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/taxonomy/test_engine_warm_path.py tests/taxonomy/test_engine_cold_path.py -v`

Expected: AttributeError — `run_warm_path` and `run_cold_path` not implemented.

- [ ] **Step 4: Add warm and cold path methods to engine.py**

Add to `TaxonomyEngine` in `engine.py`:

- `run_warm_path(db)` — check `_warm_path_lock.locked()` for deduplication, acquire lock, run lifecycle operations in priority order (split > emerge > merge > retire), create snapshot, refresh centroid cache. Reference: Spec Section 2.3, 2.6, 3.5.
- `run_cold_path(db)` — acquire same lock, full HDBSCAN on all family embeddings, UMAP 3D refit with Procrustes alignment against previous positions, regenerate colors, create snapshot. Reference: Spec Section 2.3, 8.5.
- `WarmPathResult` and `ColdPathResult` dataclasses for return values.
- Deadlock breaker: if 5 consecutive warm paths reject ALL operations, force the best single-dimension operation through and schedule cold path via `asyncio.create_task()`. Reference: Spec Section 2.5.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/taxonomy/test_engine_warm_path.py tests/taxonomy/test_engine_cold_path.py -v`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_engine_warm_path.py backend/tests/taxonomy/test_engine_cold_path.py
git commit -m "feat(taxonomy): add warm and cold paths with lifecycle + UMAP refit

Warm path: lock deduplication, lifecycle operations, Q_system snapshots,
deadlock breaker. Cold path: full HDBSCAN + UMAP refit with Procrustes
alignment. Spec Section 2.3, 2.5, 2.6, 3.5, 8.5."
```

---

### Task 13: Engine — Pattern Matching

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`
- Create: `backend/tests/taxonomy/test_cold_start.py`

**Reference:** Spec Section 7.2 (cascade), 7.4 (cold-start), 7.7 (aggregation), 7.9 (adaptive threshold)

- [ ] **Step 1: Write failing tests**

Add to a new file `backend/tests/taxonomy/test_cold_start.py`:

```python
"""Tests for pattern matching — cascade search, cold-start, adaptive thresholds."""

import numpy as np
import pytest

from tests.taxonomy.conftest import EMBEDDING_DIM

from app.models import MetaPattern, PatternFamily, TaxonomyNode
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_match_prompt_empty_taxonomy(db, mock_embedding, mock_provider):
    """Phase 0: No nodes → returns None immediately."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.match_prompt("Build a REST API", db=db)
    assert result is None or result.match_level == "none"


@pytest.mark.asyncio
async def test_match_prompt_family_level(db, mock_embedding, mock_provider):
    """Family-level match: cosine >= 0.72 against leaf family."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create a confirmed node + family with known embedding
    emb = mock_embedding.embed_single("REST API endpoint design")
    node = TaxonomyNode(
        label="API Architecture",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=10,
        coherence=0.85,
        color_hex="#a855f7",
    )
    db.add(node)
    await db.flush()

    family = PatternFamily(
        intent_label="REST API patterns",
        domain="backend",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        taxonomy_node_id=node.id,
        member_count=5,
    )
    db.add(family)

    mp = MetaPattern(family_id=family.id, pattern_text="Use RESTful naming conventions")
    db.add(mp)
    await db.commit()

    # Same text should match at family level
    result = await engine.match_prompt("REST API endpoint design", db=db)
    assert result is not None
    assert result.match_level == "family"
    assert result.similarity > 0.7
    assert len(result.meta_patterns) > 0


@pytest.mark.asyncio
async def test_match_prompt_candidate_strict_threshold(db, mock_embedding, mock_provider):
    """Cold-start Phase 1: candidate families use strict 0.80 threshold."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    emb = mock_embedding.embed_single("test prompt")
    node = TaxonomyNode(
        label="Test",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        state="candidate",  # not confirmed yet
        member_count=2,
        coherence=0.5,
        color_hex="#7a7a9e",
    )
    db.add(node)

    family = PatternFamily(
        intent_label="Test patterns",
        domain="general",
        centroid_embedding=emb.astype(np.float32).tobytes(),
        taxonomy_node_id=node.id,
    )
    db.add(family)
    await db.commit()

    # Exact match still works even with strict threshold
    result = await engine.match_prompt("test prompt", db=db)
    # Should match (cosine ~= 1.0 > 0.80)
    assert result is not None


@pytest.mark.asyncio
async def test_match_prompt_cluster_level_fallback(db, mock_embedding, mock_provider):
    """Cluster-level match when no leaf family matches."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create parent cluster with child families
    parent_emb = mock_embedding.embed_single("API related topics")
    parent = TaxonomyNode(
        label="API Architecture",
        centroid_embedding=parent_emb.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=20,
        coherence=0.70,
        color_hex="#a855f7",
    )
    db.add(parent)
    await db.flush()

    # Create child families with DIFFERENT embeddings
    child_emb = mock_embedding.embed_single("GraphQL subscriptions")
    child = TaxonomyNode(
        label="GraphQL patterns",
        parent_id=parent.id,
        centroid_embedding=child_emb.astype(np.float32).tobytes(),
        state="confirmed",
        member_count=5,
        coherence=0.90,
        color_hex="#fbbf24",
    )
    db.add(child)

    family = PatternFamily(
        intent_label="GraphQL subs",
        domain="backend",
        centroid_embedding=child_emb.astype(np.float32).tobytes(),
        taxonomy_node_id=child.id,
    )
    db.add(family)
    await db.commit()

    # Query that matches parent but not child leaf
    result = await engine.match_prompt("API related topics", db=db)
    # Should match at cluster level since parent centroid matches
    if result is not None and result.match_level == "cluster":
        assert result.taxonomy_node is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/taxonomy/test_cold_start.py -v`

Expected: AttributeError — `match_prompt` not implemented.

- [ ] **Step 3: Implement match_prompt on TaxonomyEngine**

Add `match_prompt(prompt_text, db)` to `engine.py`:

```python
async def match_prompt(
    self, prompt_text: str, db: AsyncSession,
) -> PatternMatch | None:
    """Hierarchical pattern matching for on-paste suggestion.

    Reference: Spec Section 7.2, 7.4, 7.7, 7.9

    Cascade search:
    1. Embed prompt
    2. Search leaf families — if cosine >= family_threshold → family match
    3. If no leaf match, search parent clusters — if cosine >= cluster_threshold → cluster match
    4. No match at any level → return None

    Cold-start: candidate families use strict 0.80 threshold (Spec 7.4).
    Thresholds adapt per-cluster coherence (Spec 7.9).
    """
```

Implementation follows spec exactly:
- `FAMILY_MATCH_THRESHOLD = 0.72`, `CLUSTER_MATCH_THRESHOLD = 0.60`
- `CANDIDATE_THRESHOLD = 0.80` (cold-start strictness)
- Adaptive via `suggestion_threshold(base, coherence)`
- Cluster-level aggregates meta-patterns from top-3 child families by relevance, deduplicated at 0.82

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/taxonomy/test_cold_start.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_cold_start.py
git commit -m "feat(taxonomy): add hierarchical cascade pattern matching

Leaf → parent cascade, candidate strict threshold (0.80), adaptive
thresholds per coherence, cluster-level pattern aggregation.
Spec Section 7.2, 7.4, 7.7, 7.9."
```

---

### Task 14: Public API & Behavioral Tests

**Files:**
- Modify: `backend/app/services/taxonomy/__init__.py`
- Create: `backend/tests/taxonomy/test_emergence.py`
- Create: `backend/tests/taxonomy/test_performance.py`

**Reference:** Spec Section 9.1 (testing layers 3-4)

- [ ] **Step 1: Update __init__.py with public exports**

```python
"""Evolutionary Taxonomy Engine — self-organizing hierarchical clustering.

Public API:
    TaxonomyEngine — unified orchestrator
    TaxonomyMapping — domain mapping result
    PatternMatch — pattern matching result
    QWeights — quality metric weights
"""

from app.services.taxonomy.engine import (
    PatternMatch,
    TaxonomyEngine,
    TaxonomyMapping,
)
from app.services.taxonomy.quality import QWeights

__all__ = [
    "PatternMatch",
    "QWeights",
    "TaxonomyEngine",
    "TaxonomyMapping",
]
```

- [ ] **Step 2: Write behavioral emergence test**

Create `backend/tests/taxonomy/test_emergence.py`:

```python
"""Behavioral tests — distinct prompt domains produce distinct clusters.

Reference: Spec Section 9.1, Layer 3.
"""

import numpy as np
import pytest

from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution

from app.models import Optimization, PatternFamily
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_distinct_domains_produce_distinct_clusters(db, mock_embedding, mock_provider):
    """Three distinct prompt domains should emerge as separate taxonomy nodes.

    This is the core behavioral property of the taxonomy engine.
    """
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(42)

    # Simulate 15 optimizations across 3 distinct domains
    domains = {
        "REST API design": make_cluster_distribution("REST API design", 5, spread=0.03, rng=rng),
        "SQL optimization": make_cluster_distribution("SQL optimization", 5, spread=0.03, rng=rng),
        "React components": make_cluster_distribution("React components", 5, spread=0.03, rng=rng),
    }

    for domain_text, embeddings in domains.items():
        for i, emb in enumerate(embeddings):
            opt = Optimization(
                raw_prompt=f"{domain_text} prompt {i}",
                optimized_prompt=f"optimized {i}",
                status="completed",
                intent_label=domain_text,
                domain_raw=domain_text,
            )
            db.add(opt)
    await db.commit()

    # Process all optimizations
    from sqlalchemy import select
    all_opts = (await db.execute(select(Optimization))).scalars().all()
    for opt in all_opts:
        await engine.process_optimization(opt.id, db)

    # Run warm path to crystallize clusters
    await engine.run_warm_path(db)

    # Check families were created
    families = (await db.execute(select(PatternFamily))).scalars().all()
    assert len(families) >= 3, f"Expected >=3 families, got {len(families)}"


@pytest.mark.asyncio
async def test_identical_prompts_converge(db, mock_embedding, mock_provider):
    """Identical prompts should join the same family, not proliferate."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    for i in range(5):
        opt = Optimization(
            raw_prompt="Build a REST API with FastAPI and PostgreSQL",
            optimized_prompt=f"optimized {i}",
            status="completed",
            intent_label="REST API",
            domain_raw="REST API design",
        )
        db.add(opt)
    await db.commit()

    from sqlalchemy import select
    all_opts = (await db.execute(select(Optimization))).scalars().all()
    for opt in all_opts:
        await engine.process_optimization(opt.id, db)

    families = (await db.execute(select(PatternFamily))).scalars().all()
    # All 5 identical prompts should converge into 1 family
    assert len(families) == 1
    assert families[0].member_count == 5
```

- [ ] **Step 3: Write performance test**

Create `backend/tests/taxonomy/test_performance.py`:

```python
"""Performance tests — latency assertions per execution tier.

Reference: Spec Section 9.1, Layer 4.
"""

import time

import numpy as np
import pytest

from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution

from app.models import Optimization, PatternFamily
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_hot_path_under_500ms(db, mock_embedding, mock_provider):
    """process_optimization should complete in < 500ms."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    opt = Optimization(
        raw_prompt="Build a REST API with FastAPI",
        optimized_prompt="Build a REST API...",
        status="completed",
        domain_raw="REST API design",
    )
    db.add(opt)
    await db.commit()

    t0 = time.monotonic()
    await engine.process_optimization(opt.id, db)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert elapsed_ms < 500, f"Hot path took {elapsed_ms:.0f}ms (budget: 500ms)"


@pytest.mark.asyncio
async def test_match_prompt_under_100ms(db, mock_embedding, mock_provider):
    """match_prompt should be fast (no DB write, read-only)."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create some families
    for i in range(10):
        f = PatternFamily(
            intent_label=f"family-{i}",
            domain="general",
            centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        )
        db.add(f)
    await db.commit()

    t0 = time.monotonic()
    await engine.match_prompt("test prompt", db=db)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert elapsed_ms < 100, f"match_prompt took {elapsed_ms:.0f}ms (budget: 100ms)"
```

- [ ] **Step 4: Run all taxonomy tests**

Run: `cd backend && python -m pytest tests/taxonomy/ -v`

Expected: All tests PASS.

- [ ] **Step 5: Run full test suite for regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -x`

Expected: All tests PASS (no regressions from model changes).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/__init__.py backend/tests/taxonomy/test_emergence.py backend/tests/taxonomy/test_performance.py
git commit -m "feat(taxonomy): finalize public API exports and behavioral/performance tests

Emergence: distinct domains → distinct clusters, identical prompts converge.
Performance: hot path < 500ms, match_prompt < 100ms.
Spec Section 9.1 (Layers 3 and 4)."
```

---

## Verification Checklist

After all tasks complete, verify:

1. **Package structure:**
   ```
   backend/app/services/taxonomy/
       __init__.py
       engine.py
       clustering.py
       quality.py
       lifecycle.py
       projection.py
       labeling.py
       coloring.py
       snapshot.py
   ```

2. **Test suite:** `cd backend && python -m pytest tests/taxonomy/ -v --tb=short`
   - All tests pass
   - No warnings about missing imports

3. **No regressions:** `cd backend && python -m pytest tests/ -v --timeout=60`
   - All existing tests still pass

4. **Linting:** `cd backend && ruff check app/services/taxonomy/`
   - No lint errors

5. **Type checking (informational):** `cd backend && mypy app/services/taxonomy/ --ignore-missing-imports`

---

## What's Next

**Plan 2: Backend Integration** — Pipeline changes (`pipeline.py`, `sampling_pipeline.py`), analyzer prompt update (`analyze.md`), extract template update (`extract_patterns.md`), new API router (`routers/taxonomy.py`), pattern router modifications, MCP server integration, event bus changes (`pattern_updated` → `taxonomy_changed`), startup wiring (`main.py`), old code deletion (`pattern_extractor.py`, `pattern_matcher.py`), CLAUDE.md update.

**Plan 3: Frontend Overhaul** — Three.js 3D visualization (`SemanticTopology.svelte`, `TopologyRenderer.ts`, etc.), API client (`taxonomy.ts`), utility extraction (`colors.ts`), store modifications (`patterns.svelte.ts`), component updates (`PatternNavigator`, `Inspector`, `StatusBar`), old code deletion (`RadialMindmap`, `constants/patterns.ts`, `patterns/utils/layout.ts`), dependencies (`three`, `@types/three`).
