# Phase 2A: Multi-Project Isolation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable project-scoped taxonomy isolation — project creation on repo link, hot-path project-scoped search with cross-project fallback, per-project Q metrics, topology UI project filter.

**Architecture:** Session-based project resolution via `Optimization.repo_full_name → LinkedRepo → project_node_id`. Two-tier cluster assignment: in-project first, cross-project fallback with +0.15 boosted threshold. Per-project Q gates in speculative phases. Topology UI with project dropdown and "N of M" multi-project badges.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, aiosqlite, SvelteKit 2, Svelte 5, pytest

**Spec:** `docs/specs/2026-04-08-phase2a-multi-project-isolation.md`

---

### Task 1: Model changes + migration + constant

**Files:**
- Modify: `backend/app/models.py:270` (LinkedRepo class)
- Modify: `backend/app/services/taxonomy/_constants.py`
- Modify: `backend/app/main.py` (lifespan migration)
- Test: `backend/tests/test_project_creation.py` (create)

- [ ] **Step 1: Write the test**

```python
# backend/tests/test_project_creation.py
"""Tests for ADR-005 Phase 2A project creation."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LinkedRepo, PromptCluster


@pytest.mark.asyncio
async def test_linked_repo_has_project_node_id(db_session: AsyncSession):
    """LinkedRepo model has project_node_id column."""
    assert hasattr(LinkedRepo, "project_node_id")


@pytest.mark.asyncio
async def test_cross_project_threshold_boost_constant():
    """CROSS_PROJECT_THRESHOLD_BOOST constant exists."""
    from app.services.taxonomy._constants import CROSS_PROJECT_THRESHOLD_BOOST
    assert CROSS_PROJECT_THRESHOLD_BOOST == 0.15
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && source .venv/bin/activate
pytest tests/test_project_creation.py -v
```
Expected: FAIL — `project_node_id` not on LinkedRepo, constant not defined

- [ ] **Step 3: Add project_node_id to LinkedRepo**

In `backend/app/models.py`, add to the LinkedRepo class (after `linked_at` column):

```python
    project_node_id = Column(String(36), ForeignKey("prompt_cluster.id"), nullable=True)
```

- [ ] **Step 4: Add CROSS_PROJECT_THRESHOLD_BOOST constant**

In `backend/app/services/taxonomy/_constants.py`, add after EXCLUDED_STRUCTURAL_STATES:

```python
# ---------------------------------------------------------------------------
# Cross-project assignment (ADR-005 Section 2)
# ---------------------------------------------------------------------------
# Boost applied to the adaptive merge threshold when searching across projects.
# A prompt in Project B must be this much MORE similar to join a cluster in
# Project A than it would need to be within its own project.
CROSS_PROJECT_THRESHOLD_BOOST: float = 0.15
```

- [ ] **Step 5: Add migration to lifespan**

In `backend/app/main.py`, after the existing ADR-005 migrations (after the `_run_adr005_migration` call block):

```python
            # ADR-005 Phase 2A: ensure project_node_id column on linked_repos
            try:
                async with async_session_factory() as _pnid_db:
                    from sqlalchemy import text as _text_pnid
                    await _pnid_db.execute(
                        _text_pnid("ALTER TABLE linked_repos ADD COLUMN project_node_id VARCHAR(36)")
                    )
                    await _pnid_db.commit()
                    logger.info("Added project_node_id column to linked_repos")
            except Exception:
                pass  # Column already exists

            # ADR-005 Phase 2A: backfill project_node_id on existing LinkedRepo rows
            try:
                async with async_session_factory() as _pnid_bf_db:
                    from app.models import LinkedRepo as _LR
                    from sqlalchemy import select as _sel_pnid

                    # Find Legacy project node
                    legacy = (await _pnid_bf_db.execute(
                        _sel_pnid(PromptCluster).where(PromptCluster.state == "project").limit(1)
                    )).scalar_one_or_none()

                    if legacy:
                        # Backfill any LinkedRepo rows missing project_node_id
                        unlinked = (await _pnid_bf_db.execute(
                            _sel_pnid(_LR).where(_LR.project_node_id.is_(None))
                        )).scalars().all()
                        for lr in unlinked:
                            lr.project_node_id = legacy.id
                        if unlinked:
                            await _pnid_bf_db.commit()
                            logger.info("Backfilled project_node_id on %d LinkedRepo rows", len(unlinked))
            except Exception as pnid_exc:
                logger.warning("LinkedRepo project_node_id backfill failed (non-fatal): %s", pnid_exc)
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_project_creation.py -v
pytest --tb=short -q
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/app/services/taxonomy/_constants.py backend/app/main.py backend/tests/test_project_creation.py
git commit -m "feat(taxonomy): Phase 2A model changes — project_node_id on LinkedRepo + CROSS_PROJECT_THRESHOLD_BOOST"
```

---

### Task 2: Project creation service function

**Files:**
- Create: `backend/app/services/project_service.py`
- Test: `backend/tests/test_project_creation.py` (extend)

- [ ] **Step 1: Write the test**

Add to `backend/tests/test_project_creation.py`:

```python
@pytest.mark.asyncio
async def test_ensure_project_first_repo_renames_legacy(db_session: AsyncSession):
    """First linked repo renames Legacy project node."""
    from app.services.project_service import ensure_project_for_repo

    # Create a Legacy project node (mimics Phase 1 migration)
    legacy = PromptCluster(
        label="Legacy", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(legacy)
    await db_session.flush()

    project_id = await ensure_project_for_repo(db_session, "user/backend-api")

    assert project_id == legacy.id
    await db_session.refresh(legacy)
    assert legacy.label == "user/backend-api"


@pytest.mark.asyncio
async def test_ensure_project_second_repo_creates_new(db_session: AsyncSession):
    """Second linked repo creates a new project node."""
    from app.services.project_service import ensure_project_for_repo

    # First repo — rename Legacy
    legacy = PromptCluster(
        label="Legacy", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(legacy)
    await db_session.flush()
    await ensure_project_for_repo(db_session, "user/backend-api")

    # Second repo — new project
    project_id = await ensure_project_for_repo(db_session, "user/marketing-site")
    assert project_id != legacy.id

    new_project = await db_session.get(PromptCluster, project_id)
    assert new_project.state == "project"
    assert new_project.label == "user/marketing-site"


@pytest.mark.asyncio
async def test_ensure_project_idempotent(db_session: AsyncSession):
    """Calling twice for same repo returns same project ID."""
    from app.services.project_service import ensure_project_for_repo

    legacy = PromptCluster(
        label="Legacy", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(legacy)

    lr = LinkedRepo(
        session_id="sess-1", full_name="user/backend-api",
        branch="main", language="Python",
    )
    db_session.add(lr)
    await db_session.flush()

    pid1 = await ensure_project_for_repo(db_session, "user/backend-api")
    pid2 = await ensure_project_for_repo(db_session, "user/backend-api")
    assert pid1 == pid2


@pytest.mark.asyncio
async def test_ensure_project_relink_finds_existing(db_session: AsyncSession):
    """Re-linking a previously unlinked repo reattaches to existing project."""
    from app.services.project_service import ensure_project_for_repo

    # Create project for first repo
    legacy = PromptCluster(
        label="Legacy", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(legacy)
    await db_session.flush()

    pid1 = await ensure_project_for_repo(db_session, "user/backend-api")
    await db_session.flush()

    # Simulate unlink + re-link of a second repo
    second = PromptCluster(
        label="user/marketing-site", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(second)
    await db_session.flush()

    # Re-link should find the existing project by label
    pid2 = await ensure_project_for_repo(db_session, "user/marketing-site")
    assert pid2 == second.id
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_project_creation.py -v
```
Expected: FAIL — `project_service` module not found

- [ ] **Step 3: Implement the service**

Create `backend/app/services/project_service.py`:

```python
"""Project node management for ADR-005 multi-project isolation.

Handles project creation, re-linking, and resolution from repo name.
Called from routers/github_repos.py (link endpoint) and engine.py
(process_optimization project resolution).
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LinkedRepo, PromptCluster

logger = logging.getLogger(__name__)


async def ensure_project_for_repo(
    db: AsyncSession,
    repo_full_name: str,
) -> str:
    """Find or create a project node for the given repo.

    Logic:
    1. If LinkedRepo already has project_node_id set, return it.
    2. If only Legacy project exists (label="Legacy"), rename it to repo name.
    3. If a project node matching this repo label exists, reattach (re-link).
    4. Otherwise, create a new project node.

    Returns the project node ID (PromptCluster.id with state="project").
    """
    # Check if LinkedRepo already points to a project
    lr = (await db.execute(
        select(LinkedRepo).where(LinkedRepo.full_name == repo_full_name).limit(1)
    )).scalar_one_or_none()

    if lr and lr.project_node_id:
        return lr.project_node_id

    # Check for existing project node matching this repo label (re-link case)
    existing = (await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "project",
            PromptCluster.label == repo_full_name,
        ).limit(1)
    )).scalar_one_or_none()

    if existing:
        if lr:
            lr.project_node_id = existing.id
        return existing.id

    # Check if Legacy project exists and hasn't been renamed
    all_projects = (await db.execute(
        select(PromptCluster).where(PromptCluster.state == "project")
    )).scalars().all()

    legacy = None
    for p in all_projects:
        if p.label == "Legacy":
            legacy = p
            break

    if legacy and len(all_projects) == 1:
        # First repo: rename Legacy
        legacy.label = repo_full_name
        if lr:
            lr.project_node_id = legacy.id
        logger.info(
            "Phase 2A: renamed Legacy project to '%s' (%s)",
            repo_full_name, legacy.id[:8],
        )
        return legacy.id

    # Subsequent repos: create new project node
    new_project = PromptCluster(
        label=repo_full_name,
        state="project",
        domain="general",
        task_type="general",
        member_count=0,
    )
    db.add(new_project)
    await db.flush()

    if lr:
        lr.project_node_id = new_project.id

    logger.info(
        "Phase 2A: created project node '%s' (%s)",
        repo_full_name, new_project.id[:8],
    )
    return new_project.id


async def resolve_project_id(
    db: AsyncSession,
    repo_full_name: str | None,
    legacy_project_id: str | None = None,
) -> str | None:
    """Resolve project_id from repo_full_name.

    Args:
        db: Active database session.
        repo_full_name: From Optimization.repo_full_name.
        legacy_project_id: Cached Legacy project ID (avoids query).

    Returns:
        Project node ID, or legacy_project_id as fallback.
    """
    if not repo_full_name:
        return legacy_project_id

    lr = (await db.execute(
        select(LinkedRepo.project_node_id)
        .where(LinkedRepo.full_name == repo_full_name)
        .limit(1)
    )).scalar_one_or_none()

    if lr:
        return lr

    return legacy_project_id
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_project_creation.py -v
pytest --tb=short -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/project_service.py backend/tests/test_project_creation.py
git commit -m "feat(taxonomy): Phase 2A project creation service — ensure_project_for_repo + resolve_project_id"
```

---

### Task 3: Wire project creation into repo link endpoint

**Files:**
- Modify: `backend/app/routers/github_repos.py:67` (link endpoint)

- [ ] **Step 1: Add project creation call to link endpoint**

In `backend/app/routers/github_repos.py`, in the `link_repo` function, after the `LinkedRepo` is created and flushed (around line 114):

```python
        # ADR-005 Phase 2A: ensure project node exists for this repo
        from app.services.project_service import ensure_project_for_repo
        project_node_id = await ensure_project_for_repo(db, body.full_name)
        new_link.project_node_id = project_node_id
```

Also emit SSE event after commit:

```python
        # Emit project creation event
        try:
            from app.services.event_bus import event_bus
            event_bus.publish("taxonomy_changed", {
                "trigger": "project_created",
                "project_id": project_node_id,
                "repo": body.full_name,
            })
        except Exception:
            pass
```

- [ ] **Step 2: Update unlink endpoint**

In the unlink endpoint, clear `project_node_id` but preserve the project node:

```python
        # ADR-005: clear project reference but preserve project node and data
        link.project_node_id = None
```

- [ ] **Step 3: Run tests**

```bash
pytest --tb=short -q
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/github_repos.py
git commit -m "feat(routers): wire project creation into repo link/unlink endpoints (Phase 2A)"
```

---

### Task 4: Project resolution in process_optimization + engine caches

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py:263` (process_optimization)
- Test: `backend/tests/taxonomy/test_project_scoped_search.py` (create)

- [ ] **Step 1: Add engine caches to __init__**

In `backend/app/services/taxonomy/engine.py`, add to `__init__()` after `self._scheduler`:

```python
        # ADR-005 Phase 2A: project resolution caches
        self._cluster_project_cache: dict[str, str] = {}  # cluster_id -> project_id
        self._legacy_project_id: str | None = None  # cached Legacy project node ID
```

- [ ] **Step 2: Add repo_full_name parameter to process_optimization**

Change the signature at line 263:

```python
    async def process_optimization(
        self,
        optimization_id: str,
        db: AsyncSession,
        repo_full_name: str | None = None,  # ADR-005 Phase 2A
    ) -> None:
```

Add project resolution before the cluster assignment (after loading the optimization):

```python
        # ADR-005 Phase 2A: resolve project_id from repo
        from app.services.project_service import resolve_project_id
        if self._legacy_project_id is None:
            from app.models import PromptCluster as _PC
            legacy_q = await db.execute(
                select(_PC).where(_PC.state == "project").limit(1)
            )
            legacy = legacy_q.scalar_one_or_none()
            if legacy:
                self._legacy_project_id = legacy.id

        project_id = await resolve_project_id(db, repo_full_name, self._legacy_project_id)
        if project_id:
            opt.project_id = project_id
```

After cluster assignment, update the cache:

```python
            # ADR-005: update cluster->project cache
            if project_id:
                self._cluster_project_cache[cluster.id] = project_id
```

And pass project_id to the embedding index upsert:

```python
            await self._embedding_index.upsert(cluster.id, centroid, project_id=project_id)
```

- [ ] **Step 3: Write test**

Create `backend/tests/taxonomy/test_project_scoped_search.py`:

```python
"""Tests for Phase 2A project-scoped cluster assignment."""

import pytest
from unittest.mock import MagicMock
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LinkedRepo, Optimization, PromptCluster


@pytest.mark.asyncio
async def test_process_optimization_sets_project_id(db_session: AsyncSession):
    """process_optimization resolves and sets project_id from repo_full_name."""
    from app.services.taxonomy.engine import TaxonomyEngine

    # Create project + domain + cluster infrastructure
    project = PromptCluster(
        label="user/test-repo", state="project",
        domain="general", task_type="general", member_count=0,
    )
    db_session.add(project)
    await db_session.flush()

    lr = LinkedRepo(
        session_id="test-session", full_name="user/test-repo",
        branch="main", language="Python",
        project_node_id=project.id,
    )
    db_session.add(lr)
    await db_session.flush()

    domain = PromptCluster(
        label="general", state="domain", domain="general",
        task_type="general", member_count=0, parent_id=project.id,
    )
    db_session.add(domain)
    await db_session.flush()

    opt = Optimization(
        raw_prompt="Write a REST API endpoint",
        status="completed",
        repo_full_name="user/test-repo",
    )
    db_session.add(opt)
    await db_session.flush()

    # The optimization should get project_id set
    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # process_optimization will try to embed + assign cluster
    # We just verify it sets project_id correctly (may fail on embedding, that's OK)
    try:
        await engine.process_optimization(opt.id, db_session, repo_full_name="user/test-repo")
    except Exception:
        pass  # Embedding service is mocked, may fail

    await db_session.refresh(opt)
    assert opt.project_id == project.id
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/taxonomy/test_project_scoped_search.py -v
pytest --tb=short -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_project_scoped_search.py
git commit -m "feat(taxonomy): Phase 2A project resolution in process_optimization + caches"
```

---

### Task 5: Two-tier project-scoped cluster assignment

**Files:**
- Modify: `backend/app/services/taxonomy/family_ops.py:281` (assign_cluster)
- Test: `backend/tests/taxonomy/test_project_scoped_search.py` (extend)

- [ ] **Step 1: Add project_id parameter and helper functions**

In `backend/app/services/taxonomy/family_ops.py`, add `project_id` to `assign_cluster` signature:

```python
async def assign_cluster(
    db: AsyncSession,
    embedding: np.ndarray,
    label: str,
    domain: str,
    task_type: str,
    overall_score: float | None,
    embedding_index: EmbeddingIndex | None = None,
    project_id: str | None = None,  # ADR-005 Phase 2A
) -> PromptCluster:
```

Add helper functions before `assign_cluster`:

```python
async def _get_project_domain_ids(
    db: AsyncSession, project_id: str,
) -> set[str]:
    """Get domain node IDs for a project."""
    result = await db.execute(
        select(PromptCluster.id).where(
            PromptCluster.parent_id == project_id,
            PromptCluster.state == "domain",
        )
    )
    return {row[0] for row in result.all()}


async def _resolve_or_create_domain(
    db: AsyncSession,
    project_id: str | None,
    domain_label: str,
) -> PromptCluster | None:
    """Find or create a domain node under the project for new cluster parenting."""
    if not project_id:
        return None

    # Look for existing domain under this project
    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.parent_id == project_id,
            PromptCluster.state == "domain",
            PromptCluster.label == domain_label,
        ).limit(1)
    )
    domain = result.scalar_one_or_none()
    if domain:
        return domain

    # Look for "general" domain under this project
    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.parent_id == project_id,
            PromptCluster.state == "domain",
            PromptCluster.label == "general",
        ).limit(1)
    )
    general = result.scalar_one_or_none()
    if general:
        return general

    # Auto-bootstrap: create general domain for this project
    new_domain = PromptCluster(
        label="general",
        state="domain",
        domain="general",
        task_type="general",
        member_count=0,
        parent_id=project_id,
    )
    db.add(new_domain)
    await db.flush()
    logger.info(
        "Phase 2A: auto-created 'general' domain under project %s",
        project_id[:8],
    )
    return new_domain
```

- [ ] **Step 2: Add project filtering to the candidate loading**

Inside `assign_cluster`, before the similarity loop, add project-scoped candidate filtering:

```python
    # ADR-005 Phase 2A: scope candidates to project
    if project_id:
        project_domain_ids = await _get_project_domain_ids(db, project_id)
        if project_domain_ids:
            # Tier 1: load only in-project candidates
            candidates_q = await db.execute(
                select(PromptCluster).where(
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                    PromptCluster.parent_id.in_(project_domain_ids),
                )
            )
            project_candidates = list(candidates_q.scalars().all())
        else:
            project_candidates = []
    else:
        project_candidates = None  # None = use all candidates (current behavior)
```

Then modify the existing candidate loop to use `project_candidates` when available, and add the cross-project fallback after.

This is the most complex modification — it must integrate with the existing multi-signal penalty logic without breaking it. The plan provides the structure; the implementer must read the existing `assign_cluster()` code carefully and wrap the candidate loop.

- [ ] **Step 3: Add cross-project fallback**

After the in-project search finds no match:

```python
    # ADR-005 Phase 2A: Tier 2 — cross-project fallback
    from app.services.taxonomy._constants import CROSS_PROJECT_THRESHOLD_BOOST

    if project_id and best_match is None and project_candidates is not None:
        # Re-run with ALL candidates and boosted threshold
        all_candidates_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        all_candidates = list(all_candidates_q.scalars().all())

        # Same similarity + penalty logic as above, but with boosted threshold
        for candidate in all_candidates:
            if candidate.id in {c.id for c in (project_candidates or [])}:
                continue  # already evaluated

            # ... cosine + penalty computation (same as existing) ...
            boosted_threshold = adaptive_merge_threshold(candidate) + CROSS_PROJECT_THRESHOLD_BOOST
            if score >= boosted_threshold:
                best_match = candidate
                best_score = score
                break  # Take first cross-project match above boosted threshold
```

- [ ] **Step 4: Ensure new clusters parent to project domain**

At the cluster creation point (where no match found), use `_resolve_or_create_domain`:

```python
    # ADR-005 Phase 2A: parent new cluster to project's domain
    if project_id and not parent_domain:
        parent_domain = await _resolve_or_create_domain(db, project_id, domain)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/taxonomy/test_project_scoped_search.py -v
pytest --tb=short -q
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/family_ops.py backend/tests/taxonomy/test_project_scoped_search.py
git commit -m "feat(taxonomy): Phase 2A two-tier project-scoped cluster assignment in assign_cluster"
```

---

### Task 6: Per-project Q metrics in warm path

**Files:**
- Modify: `backend/app/services/taxonomy/warm_path.py:84` (_load_active_nodes)
- Modify: `backend/app/services/taxonomy/warm_path.py:107` (_run_speculative_phase)
- Test: `backend/tests/taxonomy/test_per_project_q.py` (create)

- [ ] **Step 1: Write the test**

```python
# backend/tests/taxonomy/test_per_project_q.py
"""Tests for Phase 2A per-project Q metrics."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptCluster


@pytest.mark.asyncio
async def test_load_active_nodes_with_project_filter(db_session: AsyncSession):
    """_load_active_nodes with project_id returns only project's clusters."""
    from app.services.taxonomy.warm_path import _load_active_nodes

    # Project A with one cluster
    proj_a = PromptCluster(
        label="proj-a", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(proj_a)
    await db_session.flush()

    domain_a = PromptCluster(
        label="general", state="domain", domain="general",
        task_type="general", member_count=0, parent_id=proj_a.id,
    )
    db_session.add(domain_a)
    await db_session.flush()

    cluster_a = PromptCluster(
        label="cluster-a", state="active", domain="general",
        task_type="coding", member_count=5, parent_id=domain_a.id,
    )
    db_session.add(cluster_a)

    # Project B with one cluster
    proj_b = PromptCluster(
        label="proj-b", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(proj_b)
    await db_session.flush()

    domain_b = PromptCluster(
        label="general", state="domain", domain="general",
        task_type="general", member_count=0, parent_id=proj_b.id,
    )
    db_session.add(domain_b)
    await db_session.flush()

    cluster_b = PromptCluster(
        label="cluster-b", state="active", domain="general",
        task_type="coding", member_count=3, parent_id=domain_b.id,
    )
    db_session.add(cluster_b)
    await db_session.flush()

    # Without filter: both clusters
    all_nodes = await _load_active_nodes(db_session)
    assert len(all_nodes) >= 2

    # With project_id filter: only project A's cluster
    proj_a_nodes = await _load_active_nodes(db_session, project_id=proj_a.id)
    cluster_ids = {n.id for n in proj_a_nodes}
    assert cluster_a.id in cluster_ids
    assert cluster_b.id not in cluster_ids
```

- [ ] **Step 2: Add project_id parameter to _load_active_nodes**

```python
async def _load_active_nodes(
    db: AsyncSession,
    exclude_candidates: bool = False,
    project_id: str | None = None,  # ADR-005 Phase 2A
) -> list[PromptCluster]:
    excluded = list(EXCLUDED_STRUCTURAL_STATES)
    if exclude_candidates:
        excluded.append("candidate")

    if project_id:
        # Load only clusters under this project's domain subtree
        domain_ids_q = await db.execute(
            select(PromptCluster.id).where(
                PromptCluster.parent_id == project_id,
                PromptCluster.state == "domain",
            )
        )
        domain_ids = {row[0] for row in domain_ids_q.all()}

        if not domain_ids:
            return []

        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(excluded),
                PromptCluster.parent_id.in_(domain_ids),
            )
        )
    else:
        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(excluded)
            )
        )
    return list(result.scalars().all())
```

- [ ] **Step 3: Add per-project Q scoping in _run_speculative_phase**

In `_run_speculative_phase()`, before Q computation:

```python
        # ADR-005 Phase 2A: scope Q to project when all dirty clusters are from one project
        _project_scope = None
        if dirty_ids:
            _dirty_projects = set()
            for cid in dirty_ids:
                pid = engine._cluster_project_cache.get(cid)
                if pid:
                    _dirty_projects.add(pid)
            if len(_dirty_projects) == 1:
                _project_scope = _dirty_projects.pop()

        nodes_before = await _load_active_nodes(
            db, exclude_candidates=True, project_id=_project_scope,
        )
```

And the same for Q_after:

```python
        nodes_after = await _load_active_nodes(
            db, exclude_candidates=True, project_id=_project_scope,
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/taxonomy/test_per_project_q.py -v
pytest --tb=short -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/warm_path.py backend/tests/taxonomy/test_per_project_q.py
git commit -m "feat(taxonomy): Phase 2A per-project Q metrics in warm path"
```

---

### Task 7: Cold path rebuild with project_ids + topology endpoints

**Files:**
- Modify: `backend/app/services/taxonomy/cold_path.py` (rebuild)
- Modify: `backend/app/routers/clusters.py:84` (tree endpoint)
- Modify: `backend/app/routers/clusters.py` (cluster detail)
- Modify: `backend/app/routers/health.py` (project_count)
- Test: `backend/tests/test_topology_project_filter.py` (create)

- [ ] **Step 1: Add _resolve_cluster_project_ids helper to cold_path.py**

```python
async def _resolve_cluster_project_ids(
    db: AsyncSession,
) -> dict[str, str | None]:
    """Build cluster_id -> dominant project_id mapping from Optimization rows."""
    from app.models import Optimization

    # Group by cluster_id, project_id, count — take max count per cluster
    rows = (await db.execute(
        select(
            Optimization.cluster_id,
            Optimization.project_id,
            func.count().label("ct"),
        ).where(
            Optimization.cluster_id.isnot(None),
            Optimization.project_id.isnot(None),
        ).group_by(
            Optimization.cluster_id,
            Optimization.project_id,
        ).order_by(func.count().desc())
    )).all()

    result: dict[str, str | None] = {}
    for cluster_id, project_id, _ct in rows:
        if cluster_id not in result:  # first row per cluster = highest count
            result[cluster_id] = project_id
    return result
```

- [ ] **Step 2: Wire into cold_path rebuild**

In `execute_cold_path()`, at the embedding index rebuild call:

```python
    # ADR-005 Phase 2A: populate project_ids on embedding index
    project_ids = await _resolve_cluster_project_ids(db)
    await engine._embedding_index.rebuild(index_centroids, project_ids=project_ids)
```

- [ ] **Step 3: Add project_id query param to tree endpoint**

In `backend/app/routers/clusters.py`, modify `get_cluster_tree`:

```python
@router.get("/api/clusters/tree")
async def get_cluster_tree(
    request: Request,
    min_persistence: float = Query(0.0, ge=0.0, le=1.0),
    project_id: str | None = Query(None),  # ADR-005 Phase 2A
    db: AsyncSession = Depends(get_db),
) -> ClusterTreeResponse:
```

Inside the function, if `project_id` is set, filter the tree to only include the project subtree.

- [ ] **Step 4: Add member_counts_by_project to cluster detail**

In the cluster detail endpoint, add:

```python
    # ADR-005 Phase 2A: per-project member breakdown
    project_counts_q = await db.execute(
        select(Optimization.project_id, func.count())
        .where(Optimization.cluster_id == cluster_id)
        .group_by(Optimization.project_id)
    )
    member_counts_by_project = {
        pid or "legacy": count for pid, count in project_counts_q.all()
    }
```

- [ ] **Step 5: Add project_count to health endpoint**

In `backend/app/routers/health.py`:

```python
    project_count = (await db.scalar(
        select(func.count()).where(PromptCluster.state == "project")
    )) or 0
```

- [ ] **Step 6: Run tests**

```bash
pytest --tb=short -q
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/taxonomy/cold_path.py backend/app/routers/clusters.py backend/app/routers/health.py
git commit -m "feat(taxonomy): Phase 2A cold path project_ids + topology endpoints"
```

---

### Task 8: E2E validation — restart, seed, verify

- [ ] **Step 1: Restart server**

```bash
./init.sh restart
```

- [ ] **Step 2: Verify migration**

```bash
cd backend && source .venv/bin/activate
python3 -c "
import asyncio, sys
sys.path.insert(0, '.')
from app.database import async_session_factory
from sqlalchemy import select, func, text
from app.models import PromptCluster, LinkedRepo

async def verify():
    async with async_session_factory() as db:
        # project_node_id column exists
        try:
            r = await db.execute(text('SELECT project_node_id FROM linked_repos LIMIT 1'))
            print('project_node_id column: exists')
        except Exception as e:
            print(f'project_node_id column: MISSING ({e})')

        # Project count
        pc = (await db.scalar(select(func.count()).where(PromptCluster.state == 'project'))) or 0
        print(f'Project nodes: {pc}')
asyncio.run(verify())
" 2>/dev/null
```

- [ ] **Step 3: Run full test suite**

```bash
pytest --tb=short -q
```

- [ ] **Step 4: Verify health endpoint**

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool | grep project
```

- [ ] **Step 5: Commit if fixes needed**

```bash
git add -A && git commit -m "fix: Phase 2A E2E validation adjustments"
```
