# Phase 2B: Global Pattern Tier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote high-value MetaPatterns to durable GlobalPatterns (cross-project, 500 cap), inject them with 1.3x relevance boost, validate with demotion/re-promotion hysteresis, enforce retention cap.

**Architecture:** Phase 4.5 in warm path (after Refresh, before Discover) runs every 10th cycle with 30-min wall-clock gate. Sibling discovery via embedding cosine finds MetaPatterns that are the "same technique" across clusters. Promotion when pattern spans 2+ projects, 5+ clusters, avg_score >= 6.0. Injection alongside MetaPatterns with 1.3x boost. Validation: demotion at < 5.0, re-promotion at >= 6.0 (1.0-point hysteresis).

**Tech Stack:** Python 3.12, SQLAlchemy async, numpy, pytest

**Spec:** `docs/specs/2026-04-08-phase2b-global-pattern-tier.md`

---

### Task 1: Constants + model migration + engine init

**Files:**
- Modify: `backend/app/services/taxonomy/_constants.py`
- Modify: `backend/app/models.py` (OptimizationPattern)
- Modify: `backend/app/services/taxonomy/engine.py` (__init__)
- Modify: `backend/app/main.py` (migration)
- Test: `backend/tests/taxonomy/test_global_pattern_promotion.py` (create)

- [ ] **Step 1: Write tests for constants and model**

```python
# backend/tests/taxonomy/test_global_pattern_promotion.py
"""Tests for GlobalPattern promotion (Phase 2B)."""

import pytest
from app.models import OptimizationPattern


def test_global_pattern_constants():
    """All Phase 2B constants exist."""
    from app.services.taxonomy._constants import (
        GLOBAL_PATTERN_CAP,
        GLOBAL_PATTERN_CYCLE_INTERVAL,
        GLOBAL_PATTERN_DEDUP_COSINE,
        GLOBAL_PATTERN_DEMOTION_SCORE,
        GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES,
        GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS,
        GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS,
        GLOBAL_PATTERN_PROMOTION_MIN_SCORE,
        GLOBAL_PATTERN_RELEVANCE_BOOST,
    )
    assert GLOBAL_PATTERN_RELEVANCE_BOOST == 1.3
    assert GLOBAL_PATTERN_CAP == 500
    assert GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS == 5
    assert GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS == 2
    assert GLOBAL_PATTERN_PROMOTION_MIN_SCORE == 6.0
    assert GLOBAL_PATTERN_DEMOTION_SCORE == 5.0
    assert GLOBAL_PATTERN_DEDUP_COSINE == 0.90
    assert GLOBAL_PATTERN_CYCLE_INTERVAL == 10
    assert GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES == 30


def test_optimization_pattern_has_global_pattern_id():
    """OptimizationPattern has global_pattern_id column."""
    assert hasattr(OptimizationPattern, "global_pattern_id")
```

- [ ] **Step 2: Implement constants**

Add to `_constants.py` after CROSS_PROJECT_THRESHOLD_BOOST:

```python
# ---------------------------------------------------------------------------
# Global Pattern Tier (ADR-005 Section 6)
# ---------------------------------------------------------------------------
GLOBAL_PATTERN_RELEVANCE_BOOST: float = 1.3
GLOBAL_PATTERN_CAP: int = 500
GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS: int = 5
GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS: int = 2
GLOBAL_PATTERN_PROMOTION_MIN_SCORE: float = 6.0
GLOBAL_PATTERN_DEMOTION_SCORE: float = 5.0
GLOBAL_PATTERN_DEDUP_COSINE: float = 0.90
GLOBAL_PATTERN_CYCLE_INTERVAL: int = 10
GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES: int = 30
```

- [ ] **Step 3: Add global_pattern_id to OptimizationPattern**

In `models.py`, add to OptimizationPattern class:

```python
    global_pattern_id = Column(String(36), ForeignKey("global_patterns.id"), nullable=True)
```

- [ ] **Step 4: Add migration and engine init**

In `main.py` lifespan, after Phase 2A migrations:

```python
            # ADR-005 Phase 2B: ensure global_pattern_id column on optimization_patterns
            try:
                async with async_session_factory() as _gpid_db:
                    from sqlalchemy import text as _text_gpid
                    await _gpid_db.execute(
                        _text_gpid("ALTER TABLE optimization_patterns ADD COLUMN global_pattern_id VARCHAR(36)")
                    )
                    await _gpid_db.commit()
            except Exception:
                pass
```

In `engine.py` __init__, add:

```python
        self._last_global_pattern_check: float = 0.0  # monotonic, Phase 2B
```

- [ ] **Step 5: Run tests, commit**

```bash
pytest tests/taxonomy/test_global_pattern_promotion.py -v
pytest --tb=short -q
git add backend/app/services/taxonomy/_constants.py backend/app/models.py backend/app/main.py backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_global_pattern_promotion.py
git commit -m "feat(taxonomy): Phase 2B constants + model migration + engine init"
```

---

### Task 2: Sibling discovery + promotion logic

**Files:**
- Create: `backend/app/services/taxonomy/global_patterns.py`
- Test: `backend/tests/taxonomy/test_global_pattern_promotion.py` (extend)

- [ ] **Step 1: Write promotion test**

Add to `test_global_pattern_promotion.py`:

```python
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import GlobalPattern, MetaPattern, Optimization, PromptCluster


@pytest.mark.asyncio
async def test_promote_creates_global_pattern(db_session: AsyncSession):
    """Promotion creates GlobalPattern from cross-project MetaPattern siblings."""
    from app.services.taxonomy.global_patterns import run_global_pattern_phase

    # Create 2 projects with clusters that share similar patterns
    proj_a = PromptCluster(label="proj-a", state="project", domain="general", task_type="general", member_count=0)
    proj_b = PromptCluster(label="proj-b", state="project", domain="general", task_type="general", member_count=0)
    db_session.add_all([proj_a, proj_b])
    await db_session.flush()

    # Create 5 clusters across both projects, each with avg_score >= 6.0
    shared_emb = np.random.randn(384).astype(np.float32)
    shared_emb = shared_emb / np.linalg.norm(shared_emb)
    emb_bytes = shared_emb.tobytes()

    clusters = []
    for i in range(5):
        c = PromptCluster(
            label=f"cluster-{i}", state="active", domain="general",
            task_type="coding", member_count=10, avg_score=7.0,
            parent_id=proj_a.id if i < 3 else proj_b.id,
        )
        db_session.add(c)
        clusters.append(c)
    await db_session.flush()

    # Add MetaPatterns with same embedding (siblings) and high global_source_count
    for c in clusters:
        mp = MetaPattern(
            cluster_id=c.id,
            pattern_text="Use chain-of-thought reasoning",
            embedding=emb_bytes,
            global_source_count=5,
        )
        db_session.add(mp)

        # Add optimization with project_id
        opt = Optimization(
            raw_prompt="test", status="completed",
            cluster_id=c.id,
            project_id=proj_a.id if clusters.index(c) < 3 else proj_b.id,
        )
        db_session.add(opt)
    await db_session.flush()

    # Run promotion
    stats = await run_global_pattern_phase(db_session, warm_path_age=10)

    # Should have created at least one GlobalPattern
    gps = (await db_session.execute(
        select(GlobalPattern).where(GlobalPattern.state == "active")
    )).scalars().all()
    assert len(gps) >= 1
    assert gps[0].cross_project_count >= 2
    assert gps[0].global_source_count >= 5
```

- [ ] **Step 2: Implement global_patterns.py**

Create `backend/app/services/taxonomy/global_patterns.py` with the full sibling discovery + promotion + validation + retention logic from the spec (Sections 1-4). This is a substantial module (~200 lines). The spec provides complete pseudocode for each step.

Key functions:
- `run_global_pattern_phase(db, warm_path_age)` — orchestrator
- `_discover_promotion_candidates(db)` — sibling discovery
- `_validate_existing_patterns(db)` — demotion/re-promotion/retirement
- `_enforce_retention_cap(db)` — eviction

- [ ] **Step 3: Run tests, commit**

```bash
pytest tests/taxonomy/test_global_pattern_promotion.py -v
pytest --tb=short -q
git add backend/app/services/taxonomy/global_patterns.py backend/tests/taxonomy/test_global_pattern_promotion.py
git commit -m "feat(taxonomy): Phase 2B sibling discovery + promotion logic"
```

---

### Task 3: GlobalPattern injection in pattern_injection.py

**Files:**
- Modify: `backend/app/services/pattern_injection.py:41` (InjectedPattern)
- Modify: `backend/app/services/pattern_injection.py:103` (format_injected_patterns)
- Modify: `backend/app/services/pattern_injection.py:131` (auto_inject_patterns)
- Test: `backend/tests/taxonomy/test_global_pattern_injection.py` (create)

- [ ] **Step 1: Write injection test**

```python
# backend/tests/taxonomy/test_global_pattern_injection.py
"""Tests for GlobalPattern injection (Phase 2B)."""

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GlobalPattern
from app.services.pattern_injection import InjectedPattern, format_injected_patterns


def test_injected_pattern_has_source_fields():
    """InjectedPattern has source and source_id fields."""
    ip = InjectedPattern(text="test", relevance=0.8)
    assert ip.source == "cluster"
    assert ip.source_id == ""


def test_format_separates_global_from_cluster():
    """format_injected_patterns creates separate sections for global patterns."""
    patterns = [
        InjectedPattern(text="Cluster technique", relevance=0.8, source="cluster"),
        InjectedPattern(text="Global technique", relevance=0.9, source="global"),
    ]
    result = format_injected_patterns(patterns)
    assert "Relevant Techniques" in result
    assert "Proven Cross-Project Techniques" in result
    assert result.index("Relevant Techniques") < result.index("Proven Cross-Project Techniques")
```

- [ ] **Step 2: Update InjectedPattern dataclass**

At `pattern_injection.py:41`:

```python
@dataclass
class InjectedPattern:
    text: str
    relevance: float
    cluster_id: str = ""
    cluster_label: str = ""
    source: str = "cluster"    # "cluster" | "global"
    source_id: str = ""        # MetaPattern.id or GlobalPattern.id
```

- [ ] **Step 3: Update format_injected_patterns**

At `pattern_injection.py:103`, modify to separate global from cluster patterns in the output.

- [ ] **Step 4: Add GlobalPattern query to auto_inject_patterns**

At `pattern_injection.py:131`, after the cross-cluster MetaPattern injection section, add:

```python
    # ADR-005 Phase 2B: inject GlobalPatterns with 1.3x boost
    try:
        from app.models import GlobalPattern
        from app.services.taxonomy._constants import (
            GLOBAL_PATTERN_RELEVANCE_BOOST,
        )
        from app.services.pipeline_constants import CROSS_CLUSTER_RELEVANCE_FLOOR

        gp_q = await db.execute(
            select(GlobalPattern).where(
                GlobalPattern.state == "active",
                GlobalPattern.embedding.isnot(None),
            )
        )
        for gp in gp_q.scalars():
            try:
                gp_emb = np.frombuffer(gp.embedding, dtype=np.float32)
                sim = float(np.dot(prompt_embedding, gp_emb) / (
                    np.linalg.norm(prompt_embedding) * np.linalg.norm(gp_emb) + 1e-9
                ))
                relevance = sim * GLOBAL_PATTERN_RELEVANCE_BOOST
                if relevance >= CROSS_CLUSTER_RELEVANCE_FLOOR:
                    auto_injected.append(InjectedPattern(
                        text=gp.pattern_text,
                        relevance=relevance,
                        source="global",
                        source_id=gp.id,
                    ))
            except (ValueError, TypeError):
                continue
    except Exception as gp_exc:
        logger.warning("GlobalPattern injection failed (non-fatal): %s", gp_exc)
```

- [ ] **Step 5: Run tests, commit**

```bash
pytest tests/taxonomy/test_global_pattern_injection.py -v
pytest --tb=short -q
git add backend/app/services/pattern_injection.py backend/tests/taxonomy/test_global_pattern_injection.py
git commit -m "feat(taxonomy): Phase 2B GlobalPattern injection with 1.3x boost"
```

---

### Task 4: Validation — demotion, re-promotion, retirement

**Files:**
- Modify: `backend/app/services/taxonomy/global_patterns.py` (extend)
- Test: `backend/tests/taxonomy/test_global_pattern_validation.py` (create)

- [ ] **Step 1: Write validation tests**

Tests covering: demotion when avg_cluster_score < 5.0, re-promotion when >= 6.0, retirement when all sources archived + 30 days, hysteresis gap prevents oscillation.

- [ ] **Step 2: Implement validation in global_patterns.py**

`_validate_existing_patterns(db)` — iterates active/demoted GlobalPatterns, recomputes avg_cluster_score, applies demotion/re-promotion/retirement rules per spec Section 3.

- [ ] **Step 3: Run tests, commit**

```bash
pytest tests/taxonomy/test_global_pattern_validation.py -v
git add backend/app/services/taxonomy/global_patterns.py backend/tests/taxonomy/test_global_pattern_validation.py
git commit -m "feat(taxonomy): Phase 2B GlobalPattern validation — demotion/re-promotion/retirement"
```

---

### Task 5: Retention cap enforcement

**Files:**
- Modify: `backend/app/services/taxonomy/global_patterns.py` (extend)
- Test: `backend/tests/taxonomy/test_global_pattern_retention.py` (create)

- [ ] **Step 1: Write retention tests**

Tests covering: cap at 500, evict demoted LRU first, then active LRU, eviction sets state="retired" (not DELETE).

- [ ] **Step 2: Implement retention in global_patterns.py**

`_enforce_retention_cap(db)` — per spec Section 4.

- [ ] **Step 3: Run tests, commit**

```bash
pytest tests/taxonomy/test_global_pattern_retention.py -v
git add backend/app/services/taxonomy/global_patterns.py backend/tests/taxonomy/test_global_pattern_retention.py
git commit -m "feat(taxonomy): Phase 2B GlobalPattern retention cap (500, LRU eviction)"
```

---

### Task 6: Wire Phase 4.5 into warm path + observability

**Files:**
- Modify: `backend/app/services/taxonomy/warm_path.py` (Phase 4.5 orchestration)
- Modify: `backend/app/routers/health.py` (global_patterns stats)

- [ ] **Step 1: Add Phase 4.5 to execute_warm_path**

After Phase 4 (Refresh) and before Phase 5 (Discover):

```python
    # ------------------------------------------------------------------
    # Phase 4.5: Global Pattern Promotion + Validation (ADR-005 Phase 2B)
    # Runs every Nth cycle with wall-clock gate. Full scan (ignores dirty_ids).
    # ------------------------------------------------------------------
    import time as _gp_time
    from app.services.taxonomy._constants import (
        GLOBAL_PATTERN_CYCLE_INTERVAL,
        GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES,
    )

    _gp_age_gate = (engine._warm_path_age % GLOBAL_PATTERN_CYCLE_INTERVAL == 0)
    _gp_wall_gate = (
        _gp_time.monotonic() - engine._last_global_pattern_check
        >= GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES * 60
    )

    if _gp_age_gate and _gp_wall_gate:
        try:
            async with session_factory() as db:
                from app.services.taxonomy.global_patterns import run_global_pattern_phase
                gp_stats = await run_global_pattern_phase(db, engine._warm_path_age)
                await db.commit()
                engine._last_global_pattern_check = _gp_time.monotonic()
                if gp_stats.get("promoted", 0) or gp_stats.get("demoted", 0) or gp_stats.get("retired", 0):
                    logger.info(
                        "Phase 4.5 (global patterns): promoted=%d demoted=%d retired=%d evicted=%d",
                        gp_stats.get("promoted", 0),
                        gp_stats.get("demoted", 0),
                        gp_stats.get("retired", 0),
                        gp_stats.get("evicted", 0),
                    )
        except Exception as gp_exc:
            logger.warning("Phase 4.5 (global patterns) failed (non-fatal): %s", gp_exc)
```

- [ ] **Step 2: Add health endpoint stats**

In `health.py`, add global_patterns counts.

- [ ] **Step 3: Run full test suite, commit**

```bash
pytest --tb=short -q
git add backend/app/services/taxonomy/warm_path.py backend/app/routers/health.py
git commit -m "feat(taxonomy): Phase 2B wire Phase 4.5 into warm path + health endpoint"
```

---

### Task 7: E2E validation

- [ ] **Step 1: Restart, run full tests, verify health endpoint**

```bash
./init.sh restart
cd backend && source .venv/bin/activate
pytest --tb=short -q
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool | grep -A5 global
```

- [ ] **Step 2: Commit if fixes needed**
