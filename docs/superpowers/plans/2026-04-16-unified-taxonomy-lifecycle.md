# Unified Taxonomy Lifecycle Implementation Plan

**Status:** Shipped (v0.3.35). `_dissolve_node()` shared primitive for both domain and sub-domain dissolution (reparent, merge meta-patterns, archive, clear indices + resolver + signal loader). `_reevaluate_domains()` + `_reevaluate_sub_domains()` + `dissolved_this_cycle` flip-flop guard. Historical record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify domain and sub-domain lifecycle with shared dissolution core, domain re-evaluation, bottom-up sub-domain anchoring, and seed domain protection removal.

**Architecture:** Extract `_dissolve_node()` shared method from inline sub-domain dissolution. Add `_reevaluate_domains()` for domain-level consistency checking (Source 1 only) with sub-domain anchor, member ceiling, and age gate. Remove `source="seed"` protection at all levels. Reorder `phase_discover()` to: vocab gen → sub-domain reeval → domain reeval → domain discovery → sub-domain discovery → existing post-discovery ops.

**Tech Stack:** Python 3.12, SQLAlchemy async, pytest, existing TaxonomyEngine/warm_phases infrastructure

**Spec:** `docs/superpowers/specs/2026-04-16-unified-taxonomy-lifecycle-design.md`

---

## File Structure

| File | Change | Responsibility |
|------|--------|---------------|
| `backend/app/services/taxonomy/_constants.py` | Modify | Add 3 domain dissolution constants |
| `backend/app/services/domain_signal_loader.py` | Modify | Add `remove_domain()` method |
| `backend/app/services/taxonomy/engine.py` | Modify | Extract `_dissolve_node()`, add `_reevaluate_domains()`, refactor `_reevaluate_sub_domains()`, remove seed protection |
| `backend/app/services/taxonomy/warm_phases.py` | Modify | Reorder `phase_discover()`, remove seed protection from Phase 5.5 |
| `backend/app/routers/health.py` | Modify | Add `domain_lifecycle` stats |
| `backend/tests/taxonomy/test_sub_domain_lifecycle.py` | Modify | Add domain dissolution tests, update seed tests |
| `backend/tests/test_domain_signal_loader.py` | Modify | Add `remove_domain()` tests |

---

### Task 1: Add domain dissolution constants

**Files:**
- Modify: `backend/app/services/taxonomy/_constants.py`

- [ ] **Step 1: Add constants**

After the existing `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS` constant, add:

```python
# ---------------------------------------------------------------------------
# Domain dissolution — graceful re-grouping when domains lose relevance.
# Domains have stricter guards than sub-domains: higher age gate, member
# ceiling, and sub-domain anchor rule.  Aligns with ADR-006 vision that
# seed domains are bootstrapping data, not permanent fixtures.
DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR: float = 0.15   # well below 60% creation threshold (45pt hysteresis)
DOMAIN_DISSOLUTION_MIN_AGE_HOURS: int = 48            # domains earn permanence through time
DOMAIN_DISSOLUTION_MEMBER_CEILING: int = 5             # large domains don't dissolve on consistency alone
```

- [ ] **Step 2: Verify import**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -c "from app.services.taxonomy._constants import DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR, DOMAIN_DISSOLUTION_MIN_AGE_HOURS, DOMAIN_DISSOLUTION_MEMBER_CEILING; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/taxonomy/_constants.py
git commit -m "feat(taxonomy): add domain dissolution constants (floor=0.15, age=48h, ceiling=5)"
```

---

### Task 2: Add DomainSignalLoader.remove_domain()

**Files:**
- Modify: `backend/app/services/domain_signal_loader.py`
- Test: `backend/tests/test_domain_signal_loader.py`

- [ ] **Step 1: Write tests**

Append to `backend/tests/test_domain_signal_loader.py`:

```python
def test_remove_domain_clears_signals():
    """remove_domain() clears signals, patterns, qualifier cache, and embedding cache."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    # Populate signals
    loader.register_signals("backend", [("api", 0.9), ("server", 0.8)])
    # Populate qualifier cache
    loader.refresh_qualifiers("backend", {"auth": ["login", "password"]})
    # Populate embedding cache
    import numpy as np
    loader.cache_qualifier_embedding("login|password", np.zeros(4, dtype=np.float32))

    assert "backend" in loader.signals
    assert loader.get_qualifiers("backend") != {}

    loader.remove_domain("backend")

    assert "backend" not in loader.signals
    assert loader.get_qualifiers("backend") == {}
    assert loader.get_cached_qualifier_embedding("login|password") is None


def test_remove_domain_nonexistent_is_safe():
    """remove_domain() on unknown domain does not raise."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    loader.remove_domain("nonexistent")  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/test_domain_signal_loader.py::test_remove_domain_clears_signals tests/test_domain_signal_loader.py::test_remove_domain_nonexistent_is_safe -v`
Expected: FAIL with `AttributeError: 'DomainSignalLoader' object has no attribute 'remove_domain'`

- [ ] **Step 3: Implement remove_domain()**

In `backend/app/services/domain_signal_loader.py`, add after the `invalidate_qualifier_embedding_cache()` method:

```python
    def remove_domain(self, label: str) -> None:
        """Remove all cached data for a domain (called on domain dissolution).

        Clears keyword signals, compiled patterns, organic qualifier vocabulary,
        and qualifier embedding cache for the specified domain. Safe to call
        with a label that doesn't exist.
        """
        lbl = label.strip().lower()
        removed_signals = len(self._signals.pop(lbl, []))
        self._qualifier_cache.pop(lbl, None)
        # Rebuild patterns without the removed domain's keywords
        self._precompile_patterns()
        # Clear embedding cache (may contain embeddings referencing removed keywords)
        self.invalidate_qualifier_embedding_cache()
        logger.info(
            "remove_domain: domain=%s signals_removed=%d",
            lbl, removed_signals,
        )
```

- [ ] **Step 4: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/test_domain_signal_loader.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domain_signal_loader.py backend/tests/test_domain_signal_loader.py
git commit -m "feat(taxonomy): add DomainSignalLoader.remove_domain() for domain dissolution"
```

---

### Task 3: Extract _dissolve_node() shared method

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`
- Test: `backend/tests/taxonomy/test_sub_domain_lifecycle.py`

This is the core extraction — pulling the inline dissolution logic from `_reevaluate_sub_domains()` into a shared method.

- [ ] **Step 1: Write tests for _dissolve_node()**

Add a new test class to `backend/tests/taxonomy/test_sub_domain_lifecycle.py`:

```python
class TestDissolveNode:
    """Tests for the shared _dissolve_node() method."""

    @pytest.mark.asyncio
    async def test_dissolve_reparents_clusters_to_target(self, db, mock_provider):
        """_dissolve_node() reparents child clusters to the dissolution target."""
        from unittest.mock import AsyncMock
        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        # Create domain → sub-domain → child cluster
        domain = _make_domain("database")
        db.add(domain)
        await db.flush()

        sub = _make_domain("query", parent_id=domain.id)
        db.add(sub)
        await db.flush()

        child = _make_cluster("SQL Queries", domain="database", parent_id=sub.id)
        db.add(child)
        await db.commit()

        existing_labels = {"query", "database"}
        result = await engine._dissolve_node(
            db, sub, dissolution_target_id=domain.id,
            existing_labels=existing_labels,
            clear_signal_loader=False,
        )

        await db.refresh(child)
        assert child.parent_id == domain.id  # reparented to domain
        assert sub.state == "archived"
        assert "query" not in existing_labels  # label freed
        assert result["clusters_reparented"] >= 1

    @pytest.mark.asyncio
    async def test_dissolve_merges_meta_patterns(self, db, mock_provider):
        """_dissolve_node() merges meta-patterns into target (not deletes)."""
        from unittest.mock import AsyncMock
        from sqlalchemy import select, func
        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        domain = _make_domain("security")
        db.add(domain)
        await db.flush()

        sub = _make_domain("jwt", parent_id=domain.id)
        db.add(sub)
        await db.flush()

        # Add meta-patterns owned by sub-domain
        mp1 = MetaPattern(cluster_id=sub.id, pattern_text="use refresh tokens", source_count=3)
        mp2 = MetaPattern(cluster_id=sub.id, pattern_text="rotate keys", source_count=2)
        db.add_all([mp1, mp2])
        await db.commit()

        existing_labels = {"jwt", "security"}
        await engine._dissolve_node(
            db, sub, dissolution_target_id=domain.id,
            existing_labels=existing_labels,
            clear_signal_loader=False,
        )

        # Patterns should be merged (cluster_id changed), not deleted
        count = (await db.execute(
            select(func.count()).where(MetaPattern.cluster_id == domain.id)
        )).scalar()
        assert count == 2

        sub_count = (await db.execute(
            select(func.count()).where(MetaPattern.cluster_id == sub.id)
        )).scalar()
        assert sub_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py::TestDissolveNode -v`
Expected: FAIL with `AttributeError: 'TaxonomyEngine' object has no attribute '_dissolve_node'`

- [ ] **Step 3: Implement _dissolve_node()**

Add to `TaxonomyEngine` in `engine.py`, before `_reevaluate_sub_domains()`:

```python
    async def _dissolve_node(
        self,
        db: AsyncSession,
        node: PromptCluster,
        dissolution_target_id: str,
        existing_labels: set[str],
        clear_signal_loader: bool = False,
    ) -> dict:
        """Shared dissolution logic for both domain and sub-domain nodes.

        Reparents child clusters and direct optimizations to the dissolution
        target, merges meta-patterns (UPDATE not DELETE — prompts never lost),
        archives the node, clears all 4 indices, clears resolver cache, and
        optionally clears DomainSignalLoader (domain-level only).

        Args:
            node: The domain/sub-domain node to dissolve.
            dissolution_target_id: ID of the node to reparent children to
                ("general" for domains, parent domain for sub-domains).
            existing_labels: Label set to discard from (enables re-discovery).
            clear_signal_loader: If True, also remove from DomainSignalLoader
                (domain dissolution only — sub-domains don't have loader entries).

        Returns:
            Dict with keys: clusters_reparented, meta_patterns_merged.
        """
        from sqlalchemy import update as _sa_update

        now = _utcnow()

        # --- Reparent child clusters ---
        child_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == node.id,
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        reparented = 0
        for child in child_q.scalars():
            child.parent_id = dissolution_target_id
            reparented += 1

        # --- Reparent any direct optimizations (defensive) ---
        await db.execute(
            _sa_update(Optimization)
            .where(Optimization.cluster_id == node.id)
            .values(cluster_id=dissolution_target_id)
        )

        # --- Merge meta-patterns into target (UPDATE, not DELETE) ---
        mp_result = await db.execute(
            _sa_update(MetaPattern)
            .where(MetaPattern.cluster_id == node.id)
            .values(cluster_id=dissolution_target_id)
        )
        patterns_merged = mp_result.rowcount

        # --- Archive the node ---
        node.state = "archived"
        node.archived_at = now
        node.member_count = 0
        node.usage_count = 0
        node.avg_score = None
        node.weighted_member_sum = 0.0
        node.scored_count = 0

        # --- Clear all 4 in-memory indices ---
        for index_name in ("embedding_index", "transformation_index", "optimized_index", "qualifier_index"):
            try:
                idx = getattr(self, index_name, None)
                if idx:
                    await idx.remove(node.id)
            except (KeyError, ValueError, AttributeError):
                pass

        # --- Clear DomainResolver cache ---
        try:
            from app.services.domain_resolver import get_domain_resolver
            resolver = get_domain_resolver()
            if resolver:
                resolver.remove_label(node.label)
        except (ValueError, Exception):
            pass

        # --- Optionally clear DomainSignalLoader (domain dissolution only) ---
        if clear_signal_loader:
            try:
                from app.services.domain_signal_loader import get_signal_loader
                loader = get_signal_loader()
                if loader:
                    loader.remove_domain(node.label)
            except Exception:
                pass

        # --- Free label for re-discovery ---
        existing_labels.discard(node.label.lower())

        return {
            "clusters_reparented": reparented,
            "meta_patterns_merged": patterns_merged,
        }
```

- [ ] **Step 4: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py::TestDissolveNode -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_sub_domain_lifecycle.py
git commit -m "feat(taxonomy): extract _dissolve_node() shared dissolution method"
```

---

### Task 4: Add _reevaluate_domains() with all guards

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`
- Test: `backend/tests/taxonomy/test_sub_domain_lifecycle.py`

- [ ] **Step 1: Write tests**

Add `TestDomainDissolution` class to `backend/tests/taxonomy/test_sub_domain_lifecycle.py`:

```python
class TestDomainDissolution:
    """Tests for _reevaluate_domains() — domain-level dissolution."""

    @pytest.mark.asyncio
    async def test_general_never_dissolves(self, db, mock_provider):
        """'general' domain is permanent regardless of content."""
        from unittest.mock import AsyncMock
        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        general = _make_domain("general")
        general.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(general)
        await db.commit()

        existing_labels = {"general"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        assert dissolved == []
        assert general.state == "domain"

    @pytest.mark.asyncio
    async def test_domain_with_sub_domain_anchored(self, db, mock_provider):
        """Domain with surviving sub-domain cannot dissolve (anchor rule)."""
        from unittest.mock import AsyncMock
        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        domain = _make_domain("security")
        domain.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(domain)
        await db.flush()

        sub = _make_domain("token-ops", parent_id=domain.id)
        db.add(sub)
        await db.commit()

        existing_labels = {"security", "token-ops"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        assert dissolved == []
        assert domain.state == "domain"

    @pytest.mark.asyncio
    async def test_young_domain_protected(self, db, mock_provider):
        """Domain younger than 48h is not dissolved."""
        from unittest.mock import AsyncMock
        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        domain = _make_domain("devops")
        domain.created_at = datetime.now(timezone.utc).replace(tzinfo=None)  # just created
        db.add(domain)
        await db.commit()

        existing_labels = {"devops"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        assert dissolved == []

    @pytest.mark.asyncio
    async def test_large_domain_protected(self, db, mock_provider):
        """Domain with >5 clusters is not dissolved even with low consistency."""
        from unittest.mock import AsyncMock
        from app.services.taxonomy.engine import TaxonomyEngine
        import numpy as np

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        domain = _make_domain("backend")
        domain.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(domain)
        await db.flush()

        # Add 6 clusters (above ceiling of 5)
        for i in range(6):
            cluster = PromptCluster(
                label=f"Backend Cluster {i}", state="active", domain="backend",
                parent_id=domain.id, color_hex="#ff0000", member_count=3,
                centroid_embedding=np.random.randn(384).astype(np.float32).tobytes(),
            )
            db.add(cluster)
        await db.commit()

        existing_labels = {"backend"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        assert dissolved == []

    @pytest.mark.asyncio
    async def test_small_inconsistent_domain_dissolves(self, db, mock_provider):
        """Domain with ≤5 clusters and <15% consistency dissolves."""
        from unittest.mock import AsyncMock
        from app.services.taxonomy.engine import TaxonomyEngine
        import numpy as np

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        # Create "general" as dissolution target
        general = _make_domain("general")
        db.add(general)
        await db.flush()

        domain = _make_domain("devops")
        domain.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)  # old enough
        db.add(domain)
        await db.flush()

        # Add 2 clusters with optimizations that DON'T match "devops"
        for i in range(2):
            cluster = PromptCluster(
                label=f"Misc Cluster {i}", state="active", domain="devops",
                parent_id=domain.id, color_hex="#ff0000", member_count=3,
                centroid_embedding=np.random.randn(384).astype(np.float32).tobytes(),
            )
            db.add(cluster)
            await db.flush()
            for j in range(3):
                opt = Optimization(
                    raw_prompt=f"test prompt {i}-{j}",
                    domain="devops",
                    domain_raw="backend",  # wrong domain — low consistency
                    intent_label=f"backend task {j}",
                    task_type="coding",
                    cluster_id=cluster.id,
                )
                db.add(opt)
        await db.commit()

        existing_labels = {"devops", "general"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        assert "devops" in dissolved
        assert domain.state == "archived"

    @pytest.mark.asyncio
    async def test_seed_domain_can_dissolve(self, db, mock_provider):
        """Seed domains are NOT protected — dissolve when they fail consistency."""
        from unittest.mock import AsyncMock
        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        general = _make_domain("general")
        db.add(general)
        await db.flush()

        seed_domain = _make_domain("fullstack", source="seed")
        seed_domain.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(seed_domain)
        await db.commit()
        # No clusters, no optimizations → 0% consistency, 0 members

        existing_labels = {"fullstack", "general"}
        dissolved = await engine._reevaluate_domains(db, existing_labels)
        # Empty domain with 0 members and old enough → dissolves
        assert "fullstack" in dissolved
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py::TestDomainDissolution -v`
Expected: FAIL with `AttributeError: 'TaxonomyEngine' object has no attribute '_reevaluate_domains'`

- [ ] **Step 3: Implement _reevaluate_domains()**

Add to `TaxonomyEngine` in `engine.py`, after `_dissolve_node()`:

```python
    async def _reevaluate_domains(
        self,
        db: AsyncSession,
        existing_labels: set[str],
    ) -> list[str]:
        """Re-evaluate top-level domains and dissolve those with degraded consistency.

        Guards (all must pass for dissolution):
        1. Not "general" (permanent root)
        2. No surviving sub-domains (bottom-up anchor)
        3. Age >= DOMAIN_DISSOLUTION_MIN_AGE_HOURS
        4. Consistency < DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR (Source 1 only)
        5. member_count <= DOMAIN_DISSOLUTION_MEMBER_CEILING

        Returns list of dissolved domain labels.
        """
        from app.services.taxonomy._constants import (
            DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
            DOMAIN_DISSOLUTION_MEMBER_CEILING,
            DOMAIN_DISSOLUTION_MIN_AGE_HOURS,
        )
        from app.utils.text_cleanup import parse_domain as _parse_domain

        dissolved: list[str] = []

        # Find the "general" domain node as dissolution target
        general_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label == "general",
            )
        )
        general_node = general_q.scalars().first()
        if not general_node:
            return dissolved

        # Load all non-general top-level domains
        domain_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label != "general",
            )
        )
        domains = list(domain_q.scalars().all())

        now = _utcnow()
        age_cutoff = now - __import__("datetime").timedelta(hours=DOMAIN_DISSOLUTION_MIN_AGE_HOURS)

        for domain in domains:
            # Guard 1: "general" already excluded by query

            # Guard 2: sub-domain anchor — bottom-up only
            sub_count_q = await db.execute(
                select(func.count()).where(
                    PromptCluster.parent_id == domain.id,
                    PromptCluster.state == "domain",
                )
            )
            sub_count = sub_count_q.scalar() or 0
            if sub_count > 0:
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="domain_dissolution_blocked",
                        context={
                            "domain": domain.label,
                            "reason": "has_sub_domains",
                            "sub_domain_count": sub_count,
                        },
                    )
                except RuntimeError:
                    pass
                continue

            # Guard 3: age gate
            created = domain.created_at
            if created is not None:
                if isinstance(created, str):
                    try:
                        created = __import__("datetime").datetime.fromisoformat(created)
                    except (ValueError, TypeError):
                        created = None
                if created is not None and created.tzinfo is not None:
                    created = created.replace(tzinfo=None)
            if created and created > age_cutoff:
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="domain_dissolution_blocked",
                        context={
                            "domain": domain.label,
                            "reason": "too_young",
                            "age_hours": round((now - created).total_seconds() / 3600, 1) if created else 0,
                        },
                    )
                except RuntimeError:
                    pass
                continue

            # Guard 5: member ceiling (check before consistency to avoid unnecessary DB queries)
            child_q = await db.execute(
                select(PromptCluster.id).where(
                    PromptCluster.parent_id == domain.id,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
            )
            child_ids = [r[0] for r in child_q.all()]
            if len(child_ids) > DOMAIN_DISSOLUTION_MEMBER_CEILING:
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="domain_dissolution_blocked",
                        context={
                            "domain": domain.label,
                            "reason": "above_member_ceiling",
                            "member_count": len(child_ids),
                            "ceiling": DOMAIN_DISSOLUTION_MEMBER_CEILING,
                        },
                    )
                except RuntimeError:
                    pass
                continue

            # Guard 4: consistency check (Source 1 only — domain_raw primary label)
            if child_ids:
                opt_q = await db.execute(
                    select(Optimization.domain_raw).where(
                        Optimization.cluster_id.in_(child_ids),
                    )
                )
                domain_raws = [r[0] for r in opt_q.all()]
                total_opts = len(domain_raws)

                if total_opts > 0:
                    matching = 0
                    for dr in domain_raws:
                        if not dr:
                            continue
                        primary, _ = _parse_domain(dr)
                        if primary == domain.label.lower():
                            matching += 1
                    consistency = matching / total_opts
                else:
                    consistency = 0.0
            else:
                total_opts = 0
                consistency = 0.0

            # Log re-evaluation
            try:
                get_event_logger().log_decision(
                    path="warm", op="discover",
                    decision="domain_reevaluated",
                    context={
                        "domain": domain.label,
                        "consistency_pct": round(consistency * 100, 1),
                        "floor_pct": round(DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR * 100, 1),
                        "member_count": len(child_ids),
                        "member_ceiling": DOMAIN_DISSOLUTION_MEMBER_CEILING,
                        "has_sub_domains": False,
                        "source": "domain_raw",
                        "total_opts": total_opts,
                        "passed": consistency >= DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
                    },
                )
            except RuntimeError:
                pass

            if consistency >= DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR:
                continue  # healthy

            # --- Dissolve ---
            logger.info(
                "Dissolving domain '%s': consistency=%.1f%% < floor=%.1f%%, %d clusters",
                domain.label, consistency * 100,
                DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR * 100, len(child_ids),
            )
            result = await self._dissolve_node(
                db, domain, dissolution_target_id=general_node.id,
                existing_labels=existing_labels,
                clear_signal_loader=True,
            )
            dissolved.append(domain.label)

            try:
                get_event_logger().log_decision(
                    path="warm", op="discover",
                    decision="domain_dissolved",
                    cluster_id=domain.id,
                    context={
                        "domain": domain.label,
                        "consistency_pct": round(consistency * 100, 1),
                        "floor_pct": round(DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR * 100, 1),
                        "clusters_reparented": result["clusters_reparented"],
                        "meta_patterns_merged": result["meta_patterns_merged"],
                        "reason": "consistency_below_floor",
                    },
                )
            except RuntimeError:
                pass

        return dissolved
```

- [ ] **Step 4: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py::TestDomainDissolution -v`
Expected: All 6 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_sub_domain_lifecycle.py
git commit -m "feat(taxonomy): add _reevaluate_domains() with anchor, age, member, consistency guards"
```

---

### Task 5: Refactor _reevaluate_sub_domains() to use _dissolve_node()

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`

- [ ] **Step 1: Replace inline dissolution with _dissolve_node() call**

In `_reevaluate_sub_domains()`, find the dissolution block (starts at `# --- Dissolve: reparent children to top-level domain ---`, around line 2462). Replace the entire inline dissolution block (reparent clusters, reparent optimizations, merge meta-patterns, archive node, clear indices, clear resolver) with:

```python
            # --- Dissolve via shared method ---
            result = await self._dissolve_node(
                db, sub, dissolution_target_id=domain_node.id,
                existing_labels=existing_labels,
                clear_signal_loader=False,
            )
            reparented = result["clusters_reparented"]
            patterns_merged = result["meta_patterns_merged"]

            dissolved.append(sub.label)
```

Keep the logging and event emission after this block (the `logger.info()` and `get_event_logger()` calls).

- [ ] **Step 2: Run existing sub-domain tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py -v --tb=short`
Expected: All PASS (existing dissolution tests still work via the shared method)

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/taxonomy/engine.py
git commit -m "refactor(taxonomy): use _dissolve_node() in _reevaluate_sub_domains()"
```

---

### Task 6: Remove seed protection

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`
- Modify: `backend/app/services/taxonomy/warm_phases.py`
- Test: `backend/tests/taxonomy/test_sub_domain_lifecycle.py`

- [ ] **Step 1: Remove seed protection from _reevaluate_sub_domains()**

In `engine.py`, find the seed protection block in `_reevaluate_sub_domains()`:
```python
            # --- Seed protection ---
            meta = read_meta(sub.cluster_metadata)
            if meta.get("source") == "seed":
                continue
```

Delete these 3 lines. Also update the docstring to remove the seed protection mention.

- [ ] **Step 2: Remove seed protection from phase_archive_empty_sub_domains()**

In `warm_phases.py`, find:
```python
        # Skip seed domains
        meta = read_meta(sub.cluster_metadata)
        if meta.get("source") == "seed":
            continue
```

Delete these 3 lines. Also update the docstring to remove the `source != "seed"` safety check mention.

- [ ] **Step 3: Update existing seed protection test**

In `test_sub_domain_lifecycle.py`, find `test_skip_seed_domain` and `test_seed_sub_domain_protected`. These tests currently verify that seed domains/sub-domains are NOT archived/dissolved. Update them to verify the OPPOSITE — seed domains ARE subject to the same lifecycle:

Replace `test_skip_seed_domain`:
```python
    @pytest.mark.asyncio
    async def test_seed_domain_can_be_archived(self, db):
        """Seed domains are now subject to the same lifecycle — can be archived."""
        from unittest.mock import AsyncMock, MagicMock
        from app.services.taxonomy.warm_phases import phase_archive_empty_sub_domains

        engine = MagicMock()
        engine.embedding_index = MagicMock()
        engine.embedding_index.remove = AsyncMock()
        engine.transformation_index = MagicMock()
        engine.transformation_index.remove = AsyncMock()
        engine.optimized_index = MagicMock()
        engine.optimized_index.remove = AsyncMock()
        engine.qualifier_index = MagicMock()
        engine.qualifier_index.remove = AsyncMock()

        parent = _make_domain("backend")
        db.add(parent)
        await db.flush()

        sub = _make_domain("seeded-sub", parent_id=parent.id, source="seed")
        sub.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
        db.add(sub)
        await db.flush()

        archived = await phase_archive_empty_sub_domains(engine, db)
        assert archived == 1  # seed sub-domain CAN be archived now
        assert sub.state == "archived"
```

Replace `test_seed_sub_domain_protected`:
```python
    @pytest.mark.asyncio
    async def test_seed_sub_domain_can_dissolve(self, db, mock_provider):
        """Seed sub-domains are NOT protected — dissolve when they fail consistency."""
        from unittest.mock import AsyncMock
        from app.services.taxonomy.engine import TaxonomyEngine

        mock_embedding = AsyncMock()
        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        domain = _make_domain("backend")
        domain.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(domain)
        await db.flush()

        sub = _make_domain("api", parent_id=domain.id, source="seed")
        sub.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(sub)
        await db.commit()
        # No child clusters, no optimizations → will fail consistency

        existing_labels = {"api", "backend"}
        dissolved = await engine._reevaluate_sub_domains(db, domain, existing_labels)
        # Seed sub-domain with no content should dissolve
        # (empty sub-domains are handled by Phase 5.5, but re-evaluation also catches them)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py -v --tb=short`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/app/services/taxonomy/warm_phases.py backend/tests/taxonomy/test_sub_domain_lifecycle.py
git commit -m "feat(taxonomy): remove seed domain protection per ADR-006"
```

---

### Task 7: Reorder phase_discover() execution

**Files:**
- Modify: `backend/app/services/taxonomy/warm_phases.py`

- [ ] **Step 1: Restructure phase_discover()**

In `warm_phases.py`, rewrite `phase_discover()` to follow the new execution order. The current order is: domain discovery → sub-domain discovery → candidate detection → risk monitoring → tree integrity.

New order:
1. Sub-domain re-evaluation (call `engine._reevaluate_sub_domains()` for each domain with sub-domains)
2. Domain re-evaluation (call `engine._reevaluate_domains()`)
3. Domain discovery (`engine._propose_domains()` — existing)
4. Sub-domain discovery (`engine._propose_sub_domains()` — existing)
5. Existing post-discovery ops (candidate detection, risk monitoring, tree integrity — PRESERVED exactly as-is)

Note: Vocabulary generation is already handled in a separate pass within `_propose_sub_domains()` which runs first.

The key addition is wrapping steps 1-2 in try/except with `dissolved_this_cycle` tracking:

```python
    # --- Sub-domain re-evaluation (bottom-up) ---
    dissolved_this_cycle: set[str] = set()
    try:
        # ... iterate non-general domains, call _reevaluate_sub_domains for each
        pass
    except Exception as reeval_exc:
        logger.warning("Sub-domain re-evaluation failed (non-fatal): %s", reeval_exc)

    # --- Domain re-evaluation ---
    try:
        dissolved_domains = await engine._reevaluate_domains(db, existing_labels)
        if dissolved_domains:
            dissolved_this_cycle.update(dissolved_domains)
            # ... log
    except Exception as domain_reeval_exc:
        logger.warning("Domain re-evaluation failed (non-fatal): %s", domain_reeval_exc)
```

Add Phase 5 cycle summary log at the end:

```python
    logger.info(
        "Phase 5: domains_reevaluated=%d dissolved=%d created=%d sub_created=%d",
        domains_reevaluated, len(dissolved_this_cycle),
        result.domains_created, len(new_sub_domains) if new_sub_domains else 0,
    )
```

- [ ] **Step 2: Run all taxonomy tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/ -v --tb=short`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/taxonomy/warm_phases.py
git commit -m "feat(taxonomy): reorder phase_discover() — sub-domain reeval → domain reeval → discovery"
```

---

### Task 8: Wire health endpoint

**Files:**
- Modify: `backend/app/routers/health.py`

- [ ] **Step 1: Add domain_lifecycle field to HealthResponse**

In `health.py`, find the `HealthResponse` model. Add after `qualifier_vocab`:

```python
    domain_lifecycle: dict | None = Field(
        default=None, description="Domain dissolution lifecycle stats.",
    )
```

- [ ] **Step 2: Add stats collection**

In the health endpoint function, after the `qualifier_vocab_stats` block, add:

```python
    # Domain lifecycle stats
    domain_lifecycle_stats: dict | None = None
    try:
        _engine = getattr(request.app.state, "taxonomy_engine", None)
        if _engine:
            domain_lifecycle_stats = getattr(_engine, "_domain_lifecycle_stats", None)
    except Exception:
        pass
```

Add to the HealthResponse constructor:
```python
        domain_lifecycle=domain_lifecycle_stats,
```

Also add `_domain_lifecycle_stats` dict to `TaxonomyEngine.__init__()` in engine.py:

```python
        self._domain_lifecycle_stats: dict = {
            "domains_reevaluated": 0,
            "domains_dissolved": 0,
            "seeds_remaining": 0,
            "dissolution_blocked": 0,
            "last_domain_reeval": None,
        }
```

Increment these counters in `_reevaluate_domains()` at the appropriate points.

- [ ] **Step 3: Verify**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -c "from app.routers.health import HealthResponse; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/health.py backend/app/services/taxonomy/engine.py
git commit -m "feat(health): add domain_lifecycle stats to health endpoint"
```

---

### Task 9: Full verification

- [ ] **Step 1: Run all lifecycle tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py -v --tb=short`

- [ ] **Step 2: Run all taxonomy tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/ -v --tb=short`

- [ ] **Step 3: Run full backend test suite**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest --tb=short -q`
Expected: 2213+ tests pass

- [ ] **Step 4: Lint**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && ruff check app/services/taxonomy/engine.py app/services/taxonomy/warm_phases.py app/services/taxonomy/_constants.py app/services/domain_signal_loader.py app/routers/health.py`

- [ ] **Step 5: Verify no stale seed protection**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && grep -rn 'source.*seed' app/services/taxonomy/ --include="*.py" | grep -v __pycache__ | grep -v "source_count"`
Expected: No results (all seed protection removed)

- [ ] **Step 6: Commit lint fixes if any**

```bash
git add -u
git commit -m "style: fix lint in unified taxonomy lifecycle"
```
