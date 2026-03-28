# Unified Domain Taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all hardcoded domain constants with domain-as-PromptCluster-node architecture so the taxonomy engine discovers and evolves domains organically from user behavior.

**Architecture:** Domains become `PromptCluster` rows with `state="domain"`. A `DomainResolver` service replaces whitelist validation. A `DomainSignalLoader` replaces hardcoded heuristic keywords. The warm path gains domain discovery. Five stability guardrails protect domain nodes from evolutionary drift. All hardcoded constants (`VALID_DOMAINS`, `DOMAIN_COLORS`, `KNOWN_DOMAINS`, `_DOMAIN_SIGNALS`) are removed — no fallback maps.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, sentence-transformers, scikit-learn (TfidfVectorizer), SvelteKit 2 (Svelte 5 runes), Tailwind CSS 4

**Spec:** `docs/superpowers/specs/2026-03-28-unified-domain-taxonomy-design.md`
**ADR:** `docs/adr/ADR-004-unified-domain-taxonomy.md`

---

## File Map

### New files

| File | Responsibility |
|------|---------------|
| `backend/app/services/domain_resolver.py` | Cached domain label lookup, replaces `VALID_DOMAINS` whitelist |
| `backend/app/services/domain_signal_loader.py` | Loads heuristic keyword signals from domain node metadata, replaces `_DOMAIN_SIGNALS` |
| `backend/app/routers/domains.py` | `GET /api/domains`, `POST /api/domains/{id}/promote` |
| `backend/tests/test_domain_resolver.py` | Unit tests for DomainResolver |
| `backend/tests/test_domain_signal_loader.py` | Unit tests for DomainSignalLoader |
| `backend/tests/taxonomy/test_domain_discovery.py` | Warm path domain discovery tests |
| `backend/tests/taxonomy/test_domain_guardrails.py` | Guardrail assertion tests |
| `backend/tests/taxonomy/test_tree_integrity.py` | Tree integrity check + auto-repair tests |
| `backend/tests/test_domains_router.py` | API endpoint tests |
| `frontend/src/lib/stores/domains.svelte.ts` | Reactive domain store (API-fetched) |
| `frontend/src/lib/stores/domains.svelte.test.ts` | Domain store tests |
| `frontend/src/lib/api/domains.ts` | Domain API client functions |

### Modified files

| File | Change summary |
|------|---------------|
| `backend/app/models.py` | Add `metadata` JSON column to `PromptCluster`, add index |
| `backend/app/services/pipeline_constants.py` | Remove `VALID_DOMAINS`, `apply_domain_gate()`. Add discovery/risk constants |
| `backend/app/services/heuristic_analyzer.py` | Replace `_DOMAIN_SIGNALS` + `_classify_domain()` with `DomainSignalLoader` |
| `backend/app/services/taxonomy/engine.py` | Add `_propose_domains()`, `_monitor_general_health()`, signal refresh |
| `backend/app/services/taxonomy/lifecycle.py` | Add guardrail assertions, domain inherit on emerge/split |
| `backend/app/services/taxonomy/coloring.py` | Add `compute_max_distance_color()`, skip domain nodes in `assign_colors()` |
| `backend/app/services/taxonomy/quality.py` | Add `coherence_threshold()` with domain floor |
| `backend/app/services/taxonomy/snapshot.py` | Add domain discovery + integrity check to operations log |
| `backend/app/services/pipeline.py` | Replace `apply_domain_gate()` with `DomainResolver.resolve()` |
| `backend/app/services/sampling_pipeline.py` | Same replacement |
| `backend/app/routers/optimize.py` | Replace `VALID_DOMAINS` import with `DomainResolver` |
| `backend/app/routers/clusters.py` | Add domain validation on PATCH |
| `backend/app/routers/health.py` | Add `domain_count`, `domain_ceiling`, `general_domain` to response |
| `backend/app/tools/save_result.py` | Replace `VALID_DOMAINS` import with `DomainResolver` |
| `backend/app/tools/_shared.py` | Add `set_domain_resolver` / `get_domain_resolver` + signal loader accessors |
| `backend/app/main.py` | Initialize DomainResolver + DomainSignalLoader in lifespan |
| `backend/app/mcp_server.py` | Initialize DomainResolver + DomainSignalLoader in process init guard |
| `prompts/analyze.md` | Replace hardcoded domain list with `{{known_domains}}` |
| `prompts/manifest.json` | Add `known_domains` variable |
| `frontend/src/lib/utils/colors.ts` | Remove `DOMAIN_COLORS`, rewrite `taxonomyColor()` to use domain store |
| `frontend/src/lib/utils/colors.test.ts` | Update tests for store-based resolution |
| `frontend/src/lib/components/layout/Inspector.svelte` | Remove `KNOWN_DOMAINS`, use domain store |
| `frontend/src/lib/components/layout/StatusBar.svelte` | Add domain count + ceiling badge |
| `frontend/src/lib/components/taxonomy/TopologyData.ts` | Domain nodes render larger |

---

## Task 1: Data Model — Add `metadata` Column and Domain State

**Files:**
- Modify: `backend/app/models.py:97-140`
- Test: `backend/tests/taxonomy/test_models.py`

- [ ] **Step 1: Write the failing test**

In `backend/tests/taxonomy/test_models.py`, add:

```python
import pytest
from sqlalchemy import select

from app.models import PromptCluster


@pytest.mark.asyncio
async def test_prompt_cluster_metadata_column(db):
    """PromptCluster has a nullable JSON metadata column."""
    cluster = PromptCluster(
        label="test",
        state="domain",
        domain="test",
        metadata={"source": "seed", "signal_keywords": [["api", 0.8]]},
    )
    db.add(cluster)
    await db.commit()

    result = await db.execute(select(PromptCluster).where(PromptCluster.id == cluster.id))
    loaded = result.scalar_one()
    assert loaded.metadata["source"] == "seed"
    assert loaded.metadata["signal_keywords"] == [["api", 0.8]]


@pytest.mark.asyncio
async def test_prompt_cluster_domain_state(db):
    """PromptCluster accepts state='domain'."""
    node = PromptCluster(label="backend", state="domain", domain="backend", persistence=1.0)
    db.add(node)
    await db.commit()

    result = await db.execute(
        select(PromptCluster).where(PromptCluster.state == "domain")
    )
    loaded = result.scalar_one()
    assert loaded.label == "backend"
    assert loaded.persistence == 1.0


@pytest.mark.asyncio
async def test_prompt_cluster_metadata_null_by_default(db):
    """Non-domain clusters have metadata=None."""
    cluster = PromptCluster(label="test-cluster", state="active", domain="general")
    db.add(cluster)
    await db.commit()

    result = await db.execute(select(PromptCluster).where(PromptCluster.id == cluster.id))
    loaded = result.scalar_one()
    assert loaded.metadata is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_models.py::test_prompt_cluster_metadata_column -v`
Expected: FAIL — `PromptCluster` has no `metadata` column.

- [ ] **Step 3: Add metadata column to PromptCluster**

In `backend/app/models.py`, add after line 123 (`preferred_strategy`):

```python
    metadata = Column(JSON, nullable=True)
```

Add to `__table_args__` (inside the existing tuple, before the closing paren):

```python
        Index("ix_prompt_cluster_state_label", "state", "label"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/taxonomy/test_models.py::test_prompt_cluster_metadata_column tests/taxonomy/test_models.py::test_prompt_cluster_domain_state tests/taxonomy/test_models.py::test_prompt_cluster_metadata_null_by_default -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/taxonomy/test_models.py
git commit -m "feat(models): add metadata JSON column and state_label index to PromptCluster"
```

---

## Task 2: Pipeline Constants — Replace `VALID_DOMAINS` with Discovery Constants

**Files:**
- Modify: `backend/app/services/pipeline_constants.py`

- [ ] **Step 1: Remove `VALID_DOMAINS` and `apply_domain_gate()`, add new constants**

Replace the domain whitelist section (lines 13-19) and the `apply_domain_gate()` function (lines 82-94) in `backend/app/services/pipeline_constants.py`:

Remove:
```python
VALID_DOMAINS: set[str] = {
    "backend", "frontend", "database", "devops", "security", "fullstack", "general",
}
```

Remove:
```python
def apply_domain_gate(domain: str | None, confidence: float) -> str:
    """Override domain to ``general`` when confidence is below the domain gate."""
    effective = domain or "general"
    if confidence < DOMAIN_CONFIDENCE_GATE:
        logger.info(
            "Low confidence (%.2f) — overriding domain to 'general'",
            confidence,
        )
        effective = "general"
    return effective
```

Add in their place:

```python
# ---------------------------------------------------------------------------
# Domain confidence gate — retained from legacy system.
# DomainResolver uses this to override domain to "general" below threshold.
# ---------------------------------------------------------------------------
DOMAIN_CONFIDENCE_GATE = 0.6

# ---------------------------------------------------------------------------
# Domain discovery thresholds
# ---------------------------------------------------------------------------
DOMAIN_DISCOVERY_MIN_MEMBERS = 5
DOMAIN_DISCOVERY_MIN_COHERENCE = 0.6
DOMAIN_DISCOVERY_CONSISTENCY = 0.60  # 60% of members share the same domain_raw primary

# Domain quality
DOMAIN_COHERENCE_FLOOR = 0.3

# Domain proliferation ceiling (ADR-004 Risk 1)
DOMAIN_COUNT_CEILING = 30

# Signal staleness ratio (ADR-004 Risk 2) — refresh when member_count doubles
SIGNAL_REFRESH_MEMBER_RATIO = 2.0

# Domain archival suggestion thresholds (ADR-004 Risk 1 self-correction)
DOMAIN_ARCHIVAL_IDLE_DAYS = 90
DOMAIN_ARCHIVAL_MIN_USAGE = 3

# Color constraints — domain colors must avoid perceptual proximity to tier accents
TIER_ACCENTS = ["#00e5ff", "#22ff88", "#fbbf24"]  # internal, sampling, passthrough
```

Also remove the now-unused `DOMAIN_CONFIDENCE_GATE = 0.6` line that was on line 31 (it's now in the new block above).

- [ ] **Step 2: Fix any imports that reference removed symbols**

Run: `cd backend && grep -rn "VALID_DOMAINS\|apply_domain_gate" app/ --include="*.py"`

For each hit, add a `# TODO: replace in Task N` comment (these will be fixed in later tasks). Do NOT fix them yet — the tests for those files will drive the changes.

- [ ] **Step 3: Run existing pipeline_constants tests**

Run: `cd backend && python -m pytest tests/ -k "pipeline_constants" -v`
Expected: PASS (no tests directly test `VALID_DOMAINS` — it's tested indirectly through routers)

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/pipeline_constants.py
git commit -m "feat(constants): replace VALID_DOMAINS with domain discovery constants"
```

---

## Task 3: DomainResolver Service

**Files:**
- Create: `backend/app/services/domain_resolver.py`
- Create: `backend/tests/test_domain_resolver.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_domain_resolver.py`:

```python
"""Tests for DomainResolver — cached domain label lookup."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PromptCluster
from app.services.domain_resolver import DomainResolver


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


async def _seed_domains(db: AsyncSession) -> None:
    """Insert seed domain nodes."""
    for label in ("backend", "frontend", "general"):
        db.add(PromptCluster(label=label, state="domain", domain=label, persistence=1.0))
    await db.commit()


@pytest.mark.asyncio
async def test_resolve_known_domain(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, "backend", confidence=0.8)
    assert result == "backend"


@pytest.mark.asyncio
async def test_resolve_unknown_domain_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, "marketing", confidence=0.8)
    assert result == "general"


@pytest.mark.asyncio
async def test_resolve_with_qualifier(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, "backend: auth middleware", confidence=0.8)
    assert result == "backend"


@pytest.mark.asyncio
async def test_resolve_low_confidence_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, "backend", confidence=0.3)
    assert result == "general"


@pytest.mark.asyncio
async def test_resolve_none_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, None, confidence=0.9)
    assert result == "general"


@pytest.mark.asyncio
async def test_resolve_empty_string_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, "  ", confidence=0.9)
    assert result == "general"


@pytest.mark.asyncio
async def test_cache_invalidation(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)

    # "marketing" resolves to "general"
    assert await resolver.resolve(db, "marketing", confidence=0.8) == "general"

    # Add "marketing" domain node
    db.add(PromptCluster(label="marketing", state="domain", domain="marketing", persistence=1.0))
    await db.commit()

    # Before reload, cache still returns "general"
    assert await resolver.resolve(db, "marketing", confidence=0.8) == "general"

    # After reload, resolves correctly
    await resolver.load(db)
    assert await resolver.resolve(db, "marketing", confidence=0.8) == "marketing"


@pytest.mark.asyncio
async def test_domain_labels_property(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert resolver.domain_labels == {"backend", "frontend", "general"}


@pytest.mark.asyncio
async def test_resolve_exception_returns_general(db):
    """Resolve must never raise — returns 'general' on any error."""
    resolver = DomainResolver()
    # Not loaded — empty domain_labels
    result = await resolver.resolve(db, "backend", confidence=0.9)
    assert result == "general"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_domain_resolver.py -v`
Expected: FAIL — `domain_resolver` module does not exist.

- [ ] **Step 3: Implement DomainResolver**

Create `backend/app/services/domain_resolver.py`:

```python
"""DomainResolver — cached domain label lookup.

Replaces the ``VALID_DOMAINS`` constant with a live query against
``PromptCluster`` nodes where ``state='domain'``.  Cached in memory
with event-bus invalidation.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptCluster
from app.services.pipeline_constants import DOMAIN_CONFIDENCE_GATE
from app.utils.text_cleanup import parse_domain

logger = logging.getLogger(__name__)


class DomainResolver:
    """Resolve free-form domain strings to known domain node labels.

    Initialised at app startup via :meth:`load`, stored on
    ``app.state.domain_resolver``.  Cache invalidated on
    ``domain_created`` / ``taxonomy_changed`` events.
    """

    def __init__(self) -> None:
        self._domain_labels: set[str] = set()
        self._cache: dict[str, str] = {}

    @property
    def domain_labels(self) -> set[str]:
        """Currently known domain labels (read-only snapshot)."""
        return set(self._domain_labels)

    async def load(self, db: AsyncSession) -> None:
        """Load all domain node labels into cache."""
        result = await db.execute(
            select(PromptCluster.label).where(PromptCluster.state == "domain")
        )
        self._domain_labels = {row[0] for row in result}
        self._cache.clear()
        logger.info("DomainResolver loaded %d domain labels", len(self._domain_labels))

    async def resolve(
        self,
        db: AsyncSession,
        domain_raw: str | None,
        confidence: float,
    ) -> str:
        """Resolve a free-form domain string to a known domain label.

        Returns ``"general"`` if:
        - *domain_raw* is ``None`` or blank
        - *confidence* is below ``DOMAIN_CONFIDENCE_GATE``
        - The extracted primary does not match any domain node

        This method **never raises** — any exception returns ``"general"``.
        """
        try:
            if not domain_raw or not domain_raw.strip():
                return "general"

            if confidence < DOMAIN_CONFIDENCE_GATE:
                logger.debug(
                    "Domain confidence gate: %.2f < %.2f, defaulting to 'general'",
                    confidence,
                    DOMAIN_CONFIDENCE_GATE,
                )
                return "general"

            primary, _ = parse_domain(domain_raw)

            # Cache hit
            if primary in self._cache:
                return self._cache[primary]

            # Check against domain nodes
            if primary in self._domain_labels:
                self._cache[primary] = primary
                return primary

            # Unknown primary — default to "general"
            self._cache[primary] = "general"
            return "general"

        except Exception:
            logger.warning(
                "DomainResolver.resolve() failed for '%s', defaulting to 'general'",
                domain_raw,
                exc_info=True,
            )
            return "general"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_domain_resolver.py -v`
Expected: 9 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domain_resolver.py backend/tests/test_domain_resolver.py
git commit -m "feat: add DomainResolver service — replaces VALID_DOMAINS whitelist"
```

---

## Task 4: DomainSignalLoader Service

**Files:**
- Create: `backend/app/services/domain_signal_loader.py`
- Create: `backend/tests/test_domain_signal_loader.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_domain_signal_loader.py`:

```python
"""Tests for DomainSignalLoader — dynamic heuristic keyword signals."""

from __future__ import annotations

import re

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PromptCluster
from app.services.domain_signal_loader import DomainSignalLoader


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


async def _seed_domain(db: AsyncSession, label: str, keywords: list) -> None:
    db.add(PromptCluster(
        label=label,
        state="domain",
        domain=label,
        persistence=1.0,
        metadata={"source": "seed", "signal_keywords": keywords},
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_load_signals_from_domain_metadata(db):
    await _seed_domain(db, "backend", [["api", 0.8], ["endpoint", 0.9]])
    loader = DomainSignalLoader()
    await loader.load(db)
    assert "backend" in loader.signals
    assert ("api", 0.8) in loader.signals["backend"]


@pytest.mark.asyncio
async def test_classify_returns_matching_domain(db):
    await _seed_domain(db, "backend", [["api", 0.8], ["endpoint", 0.9]])
    await _seed_domain(db, "frontend", [["react", 1.0], ["component", 0.8]])
    loader = DomainSignalLoader()
    await loader.load(db)
    scored = {"backend": 2.5, "frontend": 0.3}
    assert loader.classify(scored) == "backend"


@pytest.mark.asyncio
async def test_classify_returns_general_when_no_scores(db):
    loader = DomainSignalLoader()
    await loader.load(db)
    assert loader.classify({}) == "general"


@pytest.mark.asyncio
async def test_classify_returns_general_when_below_threshold(db):
    await _seed_domain(db, "backend", [["api", 0.8]])
    loader = DomainSignalLoader()
    await loader.load(db)
    scored = {"backend": 0.5}  # Below 1.0 threshold
    assert loader.classify(scored) == "general"


@pytest.mark.asyncio
async def test_classify_cross_cutting_domain(db):
    await _seed_domain(db, "backend", [["api", 0.8]])
    await _seed_domain(db, "security", [["auth", 0.7], ["jwt", 0.9]])
    loader = DomainSignalLoader()
    await loader.load(db)
    scored = {"backend": 2.0, "security": 1.5}
    result = loader.classify(scored)
    assert result == "backend: security"


@pytest.mark.asyncio
async def test_score_words(db):
    await _seed_domain(db, "backend", [["api", 0.8], ["endpoint", 0.9]])
    loader = DomainSignalLoader()
    await loader.load(db)
    words = {"api", "endpoint", "the", "a"}
    scored = loader.score(words)
    assert scored["backend"] == pytest.approx(1.7)  # 0.8 + 0.9


@pytest.mark.asyncio
async def test_empty_signals_classify_general(db):
    """No domain nodes → classifier returns 'general' for everything."""
    loader = DomainSignalLoader()
    await loader.load(db)
    assert loader.classify({"backend": 5.0}) == "general"


@pytest.mark.asyncio
async def test_patterns_precompiled(db):
    await _seed_domain(db, "backend", [["api", 0.8]])
    loader = DomainSignalLoader()
    await loader.load(db)
    assert "api" in loader.patterns
    assert isinstance(loader.patterns["api"], re.Pattern)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_domain_signal_loader.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement DomainSignalLoader**

Create `backend/app/services/domain_signal_loader.py`:

```python
"""DomainSignalLoader — dynamic heuristic keyword signals.

Replaces the hardcoded ``_DOMAIN_SIGNALS`` dict in
``heuristic_analyzer.py``.  Loads keyword signals from domain node
metadata at startup and hot-reloads on ``domain_created`` events.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptCluster

logger = logging.getLogger(__name__)


class DomainSignalLoader:
    """Load and serve domain classification keyword signals.

    Signals are ``(keyword, weight)`` pairs stored in each domain
    node's ``metadata.signal_keywords`` JSON field.
    """

    def __init__(self) -> None:
        self._signals: dict[str, list[tuple[str, float]]] = {}
        self._patterns: dict[str, re.Pattern[str]] = {}

    @property
    def signals(self) -> dict[str, list[tuple[str, float]]]:
        """Current signals by domain label (read-only)."""
        return dict(self._signals)

    @property
    def patterns(self) -> dict[str, re.Pattern[str]]:
        """Pre-compiled keyword patterns (read-only)."""
        return dict(self._patterns)

    async def load(self, db: AsyncSession) -> None:
        """Load signals from all domain nodes with metadata."""
        try:
            result = await db.execute(
                select(PromptCluster).where(PromptCluster.state == "domain")
            )
            self._signals = {}
            total_keywords = 0
            for domain in result.scalars():
                meta = domain.metadata or {}
                keywords = meta.get("signal_keywords")
                if keywords:
                    self._signals[domain.label] = [
                        (str(kw), float(weight)) for kw, weight in keywords
                    ]
                    total_keywords += len(keywords)
            self._precompile_patterns()
            logger.info(
                "DomainSignalLoader loaded %d domains with %d total keywords",
                len(self._signals),
                total_keywords,
            )
        except Exception:
            logger.error(
                "DomainSignalLoader.load() failed — classifier will use empty signals",
                exc_info=True,
            )
            self._signals = {}

    def _precompile_patterns(self) -> None:
        """Pre-compile word-boundary regex for all single-word keywords."""
        self._patterns = {}
        for keywords in self._signals.values():
            for keyword, _weight in keywords:
                kw = keyword.lower()
                if " " not in kw and kw not in self._patterns:
                    self._patterns[kw] = re.compile(r"\b" + re.escape(kw) + r"\b")

    def score(self, words: set[str]) -> dict[str, float]:
        """Score a set of words against all domain signals.

        Returns ``{domain_label: total_score}`` for domains with any match.
        """
        scored: dict[str, float] = {}
        for domain_label, keywords in self._signals.items():
            total = 0.0
            for keyword, weight in keywords:
                if keyword.lower() in words:
                    total += weight
            if total > 0:
                scored[domain_label] = total
        return scored

    def classify(self, scored: dict[str, float]) -> str:
        """Classify domain from keyword scores.

        Returns ``"primary: qualifier"`` when a secondary domain scores
        above the minimum threshold (1.0).  Returns ``"general"`` when
        no domain scores above 1.0 or when no signals are loaded.
        """
        if not scored or not self._signals:
            return "general"

        sorted_domains = sorted(scored.items(), key=lambda x: x[1], reverse=True)
        if not sorted_domains or sorted_domains[0][1] < 1.0:
            return "general"

        primary = sorted_domains[0][0]

        # Check for cross-cutting secondary domain
        if len(sorted_domains) >= 2:
            secondary = sorted_domains[1][0]
            secondary_score = sorted_domains[1][1]
            if secondary_score >= 1.0 and secondary != primary:
                return f"{primary}: {secondary}"

        return primary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_domain_signal_loader.py -v`
Expected: 9 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domain_signal_loader.py backend/tests/test_domain_signal_loader.py
git commit -m "feat: add DomainSignalLoader — dynamic heuristic keyword signals from domain metadata"
```

---

## Task 5: Shared Accessors + App Initialization

**Files:**
- Modify: `backend/app/tools/_shared.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add accessors to `_shared.py`**

In `backend/app/tools/_shared.py`, add after the existing `_context_service` block:

```python
_domain_resolver = None  # DomainResolver | None
_signal_loader = None    # DomainSignalLoader | None


def set_domain_resolver(resolver) -> None:
    """Set the module-level domain resolver (called by lifespan)."""
    global _domain_resolver
    _domain_resolver = resolver


def get_domain_resolver():
    """Return domain resolver or raise if not initialized."""
    if _domain_resolver is None:
        raise ValueError("DomainResolver not initialized")
    return _domain_resolver


def set_signal_loader(loader) -> None:
    """Set the module-level signal loader (called by lifespan)."""
    global _signal_loader
    _signal_loader = loader


def get_signal_loader():
    """Return signal loader (may be None if init failed)."""
    return _signal_loader
```

Update `__all__` to include the four new functions.

- [ ] **Step 2: Add initialization to `main.py` lifespan**

In `backend/app/main.py`, add after the taxonomy engine initialization block (after the `set_engine(engine)` line, around line 99), before the backfill block:

```python
            # Initialize domain services
            from app.services.domain_resolver import DomainResolver
            from app.services.domain_signal_loader import DomainSignalLoader

            domain_resolver = DomainResolver()
            signal_loader = DomainSignalLoader()
            async with async_session_factory() as _init_db:
                await domain_resolver.load(_init_db)
                await signal_loader.load(_init_db)
            app.state.domain_resolver = domain_resolver
            app.state.signal_loader = signal_loader
            logger.info("Domain services initialized")

            # Subscribe to domain events for cache invalidation
            async def _on_domain_changed(event: dict) -> None:
                try:
                    async with async_session_factory() as _reload_db:
                        await domain_resolver.load(_reload_db)
                        await signal_loader.load(_reload_db)
                    logger.info(
                        "Domain caches reloaded: %s",
                        event.get("label", "taxonomy_changed"),
                    )
                except Exception:
                    logger.error("Domain cache reload failed", exc_info=True)

            event_bus.subscribe("domain_created", _on_domain_changed)
            event_bus.subscribe("taxonomy_changed", _on_domain_changed)
```

- [ ] **Step 3: Run existing startup tests**

Run: `cd backend && python -m pytest tests/test_main.py -v`
Expected: PASS (startup tests should still work — domain nodes don't exist yet in test DB, `DomainResolver.load()` returns empty set gracefully)

- [ ] **Step 4: Commit**

```bash
git add backend/app/tools/_shared.py backend/app/main.py
git commit -m "feat: wire DomainResolver and DomainSignalLoader into app startup"
```

---

## Task 6: Guardrails — Lifecycle Assertions

**Files:**
- Modify: `backend/app/services/taxonomy/lifecycle.py`
- Create: `backend/tests/taxonomy/test_domain_guardrails.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/taxonomy/test_domain_guardrails.py`:

```python
"""Tests for domain stability guardrails in lifecycle operations."""

from __future__ import annotations

import pytest

from app.models import PromptCluster
from app.services.taxonomy.lifecycle import (
    GuardrailViolationError,
    _assert_domain_guardrails,
)


def _make_domain_node(label: str = "backend") -> PromptCluster:
    return PromptCluster(label=label, state="domain", domain=label, persistence=1.0)


def _make_cluster(label: str = "test") -> PromptCluster:
    return PromptCluster(label=label, state="active", domain="backend")


def test_retire_domain_raises():
    with pytest.raises(GuardrailViolationError, match="retire"):
        _assert_domain_guardrails("retire", _make_domain_node())


def test_merge_domain_raises():
    with pytest.raises(GuardrailViolationError, match="merge"):
        _assert_domain_guardrails("merge", _make_domain_node())


def test_color_assign_domain_raises():
    with pytest.raises(GuardrailViolationError, match="color_assign"):
        _assert_domain_guardrails("color_assign", _make_domain_node())


def test_non_domain_cluster_passes():
    # Should not raise for any operation
    for op in ("retire", "merge", "color_assign", "split", "emerge"):
        _assert_domain_guardrails(op, _make_cluster())


def test_unknown_operation_on_domain_passes():
    # Operations not in the violations map pass through
    _assert_domain_guardrails("split", _make_domain_node())
    _assert_domain_guardrails("emerge", _make_domain_node())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/taxonomy/test_domain_guardrails.py -v`
Expected: FAIL — `GuardrailViolationError` and `_assert_domain_guardrails` do not exist.

- [ ] **Step 3: Implement guardrails in lifecycle.py**

In `backend/app/services/taxonomy/lifecycle.py`, add near the top (after imports):

```python
class GuardrailViolationError(RuntimeError):
    """Raised when a lifecycle operation violates domain stability guardrails.

    This exception should never occur in production — it indicates a code
    regression that bypassed the guardrail checks.
    """
    pass


_GUARDRAIL_VIOLATIONS: dict[str, str] = {
    "retire": "Domain nodes cannot be retired — use manual archival",
    "merge": "Domain nodes cannot be auto-merged — requires approval event",
    "color_assign": "Domain colors are pinned — cold path must skip",
}


def _assert_domain_guardrails(operation: str, node: PromptCluster) -> None:
    """Runtime assertion that domain guardrails are enforced.

    Called at the START of every lifecycle mutation.  Raises
    :class:`GuardrailViolationError` if the operation would violate
    domain stability.
    """
    if node.state != "domain":
        return

    if operation in _GUARDRAIL_VIOLATIONS:
        msg = (
            f"GUARDRAIL VIOLATION: {operation} attempted on domain node "
            f"'{node.label}'. {_GUARDRAIL_VIOLATIONS[operation]}"
        )
        logger.critical(msg)
        raise GuardrailViolationError(msg)
```

Then add guardrail assertions at the start of each lifecycle function:

In `attempt_retire()` — add as the first line of the function body:
```python
    _assert_domain_guardrails("retire", node)
```

In `attempt_merge()` — add as the first two lines:
```python
    _assert_domain_guardrails("merge", node_a)
    _assert_domain_guardrails("merge", node_b)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/taxonomy/test_domain_guardrails.py -v`
Expected: 5 PASS

- [ ] **Step 5: Run existing lifecycle tests to verify no regression**

Run: `cd backend && python -m pytest tests/taxonomy/test_lifecycle.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/lifecycle.py backend/tests/taxonomy/test_domain_guardrails.py
git commit -m "feat(taxonomy): add domain stability guardrails to lifecycle operations"
```

---

## Task 7: Taxonomy Engine — Emerge/Split Domain Inheritance

**Files:**
- Modify: `backend/app/services/taxonomy/lifecycle.py`

- [ ] **Step 1: Write tests for domain inheritance**

Add to `backend/tests/taxonomy/test_domain_guardrails.py`:

```python
@pytest.mark.asyncio
async def test_split_children_inherit_domain_from_parent(db):
    """When splitting a domain node, children inherit its label as their domain."""
    from app.services.taxonomy.lifecycle import attempt_split

    parent = PromptCluster(
        label="backend", state="domain", domain="backend", persistence=1.0,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        member_count=10,
    )
    db.add(parent)
    await db.flush()

    # Create child clusters to simulate split material
    child_embeddings = [
        np.random.randn(384).astype(np.float32) for _ in range(6)
    ]
    child_ids = []
    for i, emb in enumerate(child_embeddings):
        c = PromptCluster(
            label=f"child-{i}", state="active", domain="backend",
            parent_id=parent.id,
            centroid_embedding=(emb / np.linalg.norm(emb)).tobytes(),
        )
        db.add(c)
        await db.flush()
        child_ids.append(c.id)

    sub_clusters = [
        (child_ids[:3], child_embeddings[:3]),
        (child_ids[3:], child_embeddings[3:]),
    ]

    children = await attempt_split(
        db, parent, sub_clusters, warm_path_age=1, provider=None, model="test",
    )
    assert len(children) == 2
    for child in children:
        assert child.state == "candidate"
        assert child.domain == "backend"
```

Add necessary imports at top of test file:

```python
import numpy as np
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base
```

And add the db fixture if not already present (same pattern as other test files).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_domain_guardrails.py::test_split_children_inherit_domain_from_parent -v`
Expected: FAIL — children get `domain="general"` (column default)

- [ ] **Step 3: Modify `attempt_split()` to inherit domain**

In `backend/app/services/taxonomy/lifecycle.py`, in `attempt_split()`, where children are created (around line 288-295), change the domain assignment:

Find the line where `PromptCluster` is created for each child and set:
```python
domain=parent_node.label if parent_node.state == "domain" else parent_node.domain,
```

- [ ] **Step 4: Modify `attempt_emerge()` to inherit domain from member majority**

In `attempt_emerge()`, before creating the new node, add domain inference:

```python
    # Inherit domain from member majority
    member_domains = []
    for cid in member_cluster_ids:
        member = await db.get(PromptCluster, cid)
        if member and member.domain:
            member_domains.append(member.domain)
    from collections import Counter
    domain_counts = Counter(member_domains)
    inherited_domain = domain_counts.most_common(1)[0][0] if domain_counts else "general"
```

Then set `domain=inherited_domain` on the new node creation.

- [ ] **Step 5: Run tests to verify pass**

Run: `cd backend && python -m pytest tests/taxonomy/test_domain_guardrails.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/lifecycle.py backend/tests/taxonomy/test_domain_guardrails.py
git commit -m "feat(taxonomy): domain inheritance in emerge/split lifecycle operations"
```

---

## Task 8: Coloring — Domain Color Pinning + Max-Distance

**Files:**
- Modify: `backend/app/services/taxonomy/coloring.py`
- Create test additions in `backend/tests/taxonomy/test_coloring.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/taxonomy/test_coloring.py`:

```python
def test_compute_max_distance_color_returns_valid_hex():
    from app.services.taxonomy.coloring import compute_max_distance_color
    existing = ["#b44aff", "#ff4895", "#36b5ff"]
    result = compute_max_distance_color(existing)
    assert result.startswith("#")
    assert len(result) == 7


def test_compute_max_distance_color_avoids_existing():
    from app.services.taxonomy.coloring import compute_max_distance_color
    existing = ["#b44aff"]
    result = compute_max_distance_color(existing)
    assert result != "#b44aff"


def test_compute_max_distance_color_empty_existing():
    from app.services.taxonomy.coloring import compute_max_distance_color
    result = compute_max_distance_color([])
    assert result.startswith("#")
    assert len(result) == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/taxonomy/test_coloring.py::test_compute_max_distance_color_returns_valid_hex -v`
Expected: FAIL — function does not exist.

- [ ] **Step 3: Implement `compute_max_distance_color()` in coloring.py**

Add to `backend/app/services/taxonomy/coloring.py`:

```python
def compute_max_distance_color(existing_hex: list[str]) -> str:
    """Find the OKLab color maximally distant from all existing domain colors.

    Also avoids tier accent colors to prevent perceptual confusion.
    Returns a hex color string.
    """
    from app.services.pipeline_constants import TIER_ACCENTS

    all_hex = [h for h in existing_hex + TIER_ACCENTS if h and h.startswith("#")]
    if not all_hex:
        return "#a855f7"  # Default purple if no existing colors

    existing_lab = [_hex_to_oklab(h) for h in all_hex]

    best_color = None
    best_min_dist = 0.0

    # Sample candidates in OKLab space (L=0.7 for neon brightness)
    for a_val in np.linspace(-0.15, 0.15, 50):
        for b_val in np.linspace(-0.15, 0.15, 50):
            candidate = (0.7, float(a_val), float(b_val))
            min_dist = min(
                _oklab_distance(candidate, e) for e in existing_lab
            )
            if min_dist > best_min_dist:
                best_min_dist = min_dist
                best_color = candidate

    if best_color is None:
        return "#7a7a9e"

    return _oklab_to_hex(best_color)


def _hex_to_oklab(hex_color: str) -> tuple[float, float, float]:
    """Convert hex to OKLab (L, a, b)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))

    # sRGB → linear
    r = r / 12.92 if r <= 0.04045 else ((r + 0.055) / 1.055) ** 2.4
    g = g / 12.92 if g <= 0.04045 else ((g + 0.055) / 1.055) ** 2.4
    b = b / 12.92 if b <= 0.04045 else ((b + 0.055) / 1.055) ** 2.4

    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b

    l_ = l_ ** (1/3) if l_ > 0 else 0
    m_ = m_ ** (1/3) if m_ > 0 else 0
    s_ = s_ ** (1/3) if s_ > 0 else 0

    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_val = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return (L, a, b_val)


def _oklab_to_hex(lab: tuple[float, float, float]) -> str:
    """Convert OKLab (L, a, b) to hex."""
    L, a, b = lab

    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b

    l_ = l_ ** 3
    m_ = m_ ** 3
    s_ = s_ ** 3

    r = +4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    b_val = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_

    # linear → sRGB
    def to_srgb(c: float) -> int:
        c = max(0.0, min(1.0, c))
        c = c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1/2.4)) - 0.055
        return max(0, min(255, int(round(c * 255))))

    return f"#{to_srgb(r):02x}{to_srgb(g):02x}{to_srgb(b_val):02x}"


def _oklab_distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """Euclidean distance in OKLab space."""
    return ((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2) ** 0.5
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/taxonomy/test_coloring.py -v`
Expected: All PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/coloring.py backend/tests/taxonomy/test_coloring.py
git commit -m "feat(taxonomy): add OKLab max-distance color computation for domain nodes"
```

---

## Task 9: Quality — Domain Coherence Floor

**Files:**
- Modify: `backend/app/services/taxonomy/quality.py`

- [ ] **Step 1: Write failing test**

Add to `backend/tests/taxonomy/test_quality.py`:

```python
def test_coherence_threshold_domain_node():
    from app.models import PromptCluster
    from app.services.taxonomy.quality import coherence_threshold

    domain = PromptCluster(label="backend", state="domain")
    assert coherence_threshold(domain) == pytest.approx(0.3)


def test_coherence_threshold_regular_cluster():
    from app.models import PromptCluster
    from app.services.taxonomy.quality import coherence_threshold

    cluster = PromptCluster(label="test", state="active")
    assert coherence_threshold(cluster) == pytest.approx(0.6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/taxonomy/test_quality.py::test_coherence_threshold_domain_node -v`
Expected: FAIL — function does not exist.

- [ ] **Step 3: Implement `coherence_threshold()`**

Add to `backend/app/services/taxonomy/quality.py`:

```python
from app.services.pipeline_constants import DOMAIN_COHERENCE_FLOOR

CLUSTER_COHERENCE_FLOOR = 0.6


def coherence_threshold(node: PromptCluster) -> float:
    """Return the coherence floor for a node based on its state.

    Domain nodes use a lower threshold because they span multiple
    sub-topics — lower coherence is expected and correct.
    """
    return DOMAIN_COHERENCE_FLOOR if node.state == "domain" else CLUSTER_COHERENCE_FLOOR
```

- [ ] **Step 4: Run all quality tests**

Run: `cd backend && python -m pytest tests/taxonomy/test_quality.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/quality.py backend/tests/taxonomy/test_quality.py
git commit -m "feat(taxonomy): add domain-aware coherence threshold"
```

---

## Task 10: Pipeline Integration — Replace `apply_domain_gate` with `DomainResolver`

**Files:**
- Modify: `backend/app/services/pipeline.py`
- Modify: `backend/app/services/sampling_pipeline.py`
- Modify: `backend/app/routers/optimize.py`
- Modify: `backend/app/tools/save_result.py`

- [ ] **Step 1: Update `pipeline.py`**

In `backend/app/services/pipeline.py`:

Remove from imports:
```python
from app.services.pipeline_constants import apply_domain_gate
```

Replace the domain gate call (around line 305):

Before:
```python
effective_domain = apply_domain_gate(analysis.domain, confidence)
```

After:
```python
# Resolve domain via domain nodes (replaces hardcoded VALID_DOMAINS whitelist)
from app.services.domain_resolver import DomainResolver
_resolver: DomainResolver | None = getattr(app_state, "domain_resolver", None) if app_state else None
if _resolver:
    effective_domain = await _resolver.resolve(db, analysis.domain or "general", confidence)
else:
    effective_domain = "general"  # Startup race — resolver not yet initialized
```

Note: The exact integration depends on how `app_state` is accessed in `pipeline.py`. Check the file and use the existing pattern for accessing app state.

- [ ] **Step 2: Update `sampling_pipeline.py`**

Same pattern as pipeline.py — replace `apply_domain_gate()` call with `DomainResolver.resolve()`. Use the `get_domain_resolver()` accessor from `_shared.py`:

```python
from app.tools._shared import get_domain_resolver

try:
    resolver = get_domain_resolver()
    effective_domain = await resolver.resolve(db, getattr(analysis, "domain", None) or "general", confidence)
except ValueError:
    effective_domain = "general"
```

- [ ] **Step 3: Update `routers/optimize.py`**

Remove:
```python
from app.services.pipeline_constants import VALID_DOMAINS
```

Replace the passthrough save validation (lines 585-591):

Before:
```python
domain_primary, _ = parse_domain(body.domain)
validated_domain = (
    domain_primary if domain_primary in VALID_DOMAINS
    else (opt.domain if opt.domain in VALID_DOMAINS else "general")
)
```

After:
```python
domain_resolver: DomainResolver = request.app.state.domain_resolver
validated_domain = await domain_resolver.resolve(db, body.domain, confidence=1.0)
```

Add import: `from app.services.domain_resolver import DomainResolver`

- [ ] **Step 4: Update `tools/save_result.py`**

Remove:
```python
from app.services.pipeline_constants import VALID_DOMAINS
```

Replace the domain validation (lines 195-208):

Before:
```python
domain_primary, _ = parse_domain(domain)
# ... VALID_DOMAINS check ...
validated_domain = domain_primary if domain_primary in VALID_DOMAINS else "general"
```

After:
```python
from app.tools._shared import get_domain_resolver
resolver = get_domain_resolver()
validated_domain = await resolver.resolve(db, domain, confidence=1.0)
```

- [ ] **Step 5: Run pipeline tests**

Run: `cd backend && python -m pytest tests/test_pipeline.py tests/test_sampling_pipeline.py tests/test_passthrough.py -v`
Expected: PASS (tests may need domain node seeds — check and fix if needed)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/pipeline.py backend/app/services/sampling_pipeline.py \
    backend/app/routers/optimize.py backend/app/tools/save_result.py
git commit -m "feat: replace VALID_DOMAINS whitelist with DomainResolver across all pipelines"
```

---

## Task 11: Heuristic Analyzer — Replace Hardcoded Signals

**Files:**
- Modify: `backend/app/services/heuristic_analyzer.py`

- [ ] **Step 1: Refactor `heuristic_analyzer.py`**

Remove the `_DOMAIN_SIGNALS` dict (lines 97-124) and the `_classify_domain()` function (lines 147-176).

Replace `_precompile_keyword_patterns()` to only compile task type patterns (remove domain signal compilation).

Add `DomainSignalLoader` injection:

```python
# Module-level reference, set during app startup
_signal_loader: DomainSignalLoader | None = None


def set_signal_loader(loader: DomainSignalLoader) -> None:
    """Set the module-level signal loader (called at startup)."""
    global _signal_loader
    _signal_loader = loader


def get_signal_loader() -> DomainSignalLoader | None:
    """Return the signal loader (may be None before init)."""
    return _signal_loader
```

Update `_score_domains()` to delegate to the signal loader:

```python
def _score_domains(words: set[str]) -> dict[str, float]:
    if _signal_loader is None:
        return {}
    return _signal_loader.score(words)
```

Update domain classification to delegate:

```python
def _classify_domain(scored: dict[str, float]) -> str:
    if _signal_loader is None:
        return "general"
    return _signal_loader.classify(scored)
```

- [ ] **Step 2: Wire signal loader in `main.py`**

After the signal loader is created in the lifespan, add:

```python
from app.services.heuristic_analyzer import set_signal_loader as set_analyzer_signal_loader
set_analyzer_signal_loader(signal_loader)
```

- [ ] **Step 3: Run heuristic analyzer tests**

Run: `cd backend && python -m pytest tests/ -k "heuristic" -v`
Expected: PASS — tests should work with signal loader providing same signals as before (once domain nodes are seeded)

Note: Tests that rely on hardcoded domain classification may need fixture updates to seed domain nodes with keyword metadata. Check test failures and add domain node seeds to test fixtures as needed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/heuristic_analyzer.py backend/app/main.py
git commit -m "feat: replace hardcoded _DOMAIN_SIGNALS with DomainSignalLoader in heuristic analyzer"
```

---

## Task 12: Analyzer Prompt — Dynamic Domain List

**Files:**
- Modify: `prompts/analyze.md`
- Modify: `prompts/manifest.json`

- [ ] **Step 1: Update analyze.md**

In `prompts/analyze.md`, line 17, replace:

Before:
```markdown
3. **Domain** — Use the format "primary: qualifier" where the primary domain is one of: backend, frontend, database, devops, security, fullstack, general.
```

After:
```markdown
3. **Domain** — Use the format "primary: qualifier" where the primary domain is one of: {{known_domains}}. The qualifier adds specificity about the cross-cutting concern or sub-area (e.g., "backend: auth middleware", "frontend: accessibility"). If no qualifier applies, use just the primary domain. Use "general" only for prompts that don't fit any specific domain.
```

- [ ] **Step 2: Update manifest.json**

Add `"known_domains"` to the `analyze.md` entry in `prompts/manifest.json`.

- [ ] **Step 3: Wire `known_domains` variable in pipeline**

In the analyzer call site (both `pipeline.py` and `sampling_pipeline.py`), when rendering the analyze prompt, add the `known_domains` variable:

```python
from app.services.domain_resolver import DomainResolver
resolver: DomainResolver = app.state.domain_resolver  # or get_domain_resolver()
known_domains = ", ".join(sorted(resolver.domain_labels))

# Pass to template render
variables = {
    # ... existing variables ...
    "known_domains": known_domains,
}
```

- [ ] **Step 4: Run template validation**

Run: `cd backend && python -c "from app.services.prompt_loader import PromptLoader; PromptLoader('prompts').validate_all()"`
Expected: No errors (template has `{{known_domains}}` and manifest declares it)

- [ ] **Step 5: Commit**

```bash
git add prompts/analyze.md prompts/manifest.json backend/app/services/pipeline.py backend/app/services/sampling_pipeline.py
git commit -m "feat: inject dynamic domain list into analyzer prompt template"
```

---

## Task 13: API — Domain Endpoints

**Files:**
- Create: `backend/app/routers/domains.py`
- Create: `backend/app/schemas/domains.py`
- Create: `backend/tests/test_domains_router.py`
- Modify: `backend/app/main.py` (register router)

- [ ] **Step 1: Create schema**

Create `backend/app/schemas/domains.py`:

```python
"""Pydantic models for the domains API."""

from pydantic import BaseModel


class DomainInfo(BaseModel):
    """Domain node summary for GET /api/domains."""

    id: str
    label: str
    color_hex: str
    member_count: int = 0
    avg_score: float | None = None
    source: str = "seed"  # seed | discovered
```

- [ ] **Step 2: Create router**

Create `backend/app/routers/domains.py`:

```python
"""Domain management endpoints.

GET /api/domains — list all active domain nodes.
POST /api/domains/{id}/promote — promote a cluster to domain status.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import PromptCluster
from app.schemas.domains import DomainInfo
from app.services.taxonomy.coloring import compute_max_distance_color

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/domains", tags=["domains"])


@router.get("", response_model=list[DomainInfo])
async def list_domains(db: AsyncSession = Depends(get_db)) -> list[DomainInfo]:
    """List all active domain nodes with colors and metadata."""
    result = await db.execute(
        select(PromptCluster)
        .where(PromptCluster.state == "domain")
        .order_by(PromptCluster.label)
    )
    return [
        DomainInfo(
            id=d.id,
            label=d.label,
            color_hex=d.color_hex or "#7a7a9e",
            member_count=d.member_count,
            avg_score=d.avg_score,
            source=(d.metadata or {}).get("source", "seed"),
        )
        for d in result.scalars()
    ]


@router.post("/{domain_id}/promote", response_model=DomainInfo)
async def promote_to_domain(
    domain_id: str,
    db: AsyncSession = Depends(get_db),
) -> DomainInfo:
    """Promote a mature cluster to domain status."""
    cluster = await db.get(PromptCluster, domain_id)
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    if cluster.state == "domain":
        raise HTTPException(422, "Already a domain node")

    if cluster.state not in ("active", "mature"):
        raise HTTPException(422, f"Cannot promote cluster with state='{cluster.state}' — must be 'active' or 'mature'")

    if cluster.member_count < 5:
        raise HTTPException(422, f"Cluster has {cluster.member_count} members — minimum 5 required for domain promotion")

    # Check label uniqueness among domains
    existing = await db.execute(
        select(func.count()).where(
            PromptCluster.state == "domain",
            PromptCluster.label == cluster.label,
        )
    )
    if existing.scalar() > 0:
        raise HTTPException(409, f"Domain with label '{cluster.label}' already exists")

    # Compute color
    colors_result = await db.execute(
        select(PromptCluster.color_hex).where(
            PromptCluster.state == "domain",
            PromptCluster.color_hex.isnot(None),
        )
    )
    existing_colors = [row[0] for row in colors_result if row[0]]
    color_hex = compute_max_distance_color(existing_colors)

    # Promote
    cluster.state = "domain"
    cluster.domain = cluster.label
    cluster.color_hex = color_hex
    cluster.persistence = 1.0
    cluster.metadata = {
        "source": "manual",
        "signal_keywords": [],
        "discovered_at": None,
        "proposed_by_snapshot": None,
    }
    await db.commit()

    logger.info("Cluster %s promoted to domain: label='%s'", domain_id, cluster.label)

    return DomainInfo(
        id=cluster.id,
        label=cluster.label,
        color_hex=color_hex,
        member_count=cluster.member_count,
        avg_score=cluster.avg_score,
        source="manual",
    )
```

- [ ] **Step 3: Register router in main.py**

In `backend/app/main.py`, add with the other router imports:

```python
from app.routers.domains import router as domains_router
app.include_router(domains_router)
```

- [ ] **Step 4: Write tests**

Create `backend/tests/test_domains_router.py`:

```python
"""Tests for GET /api/domains and POST /api/domains/{id}/promote."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models import PromptCluster


@pytest.mark.asyncio
async def test_list_domains_empty(db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/domains")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_domains_returns_seed_domains(db):
    # Seed a domain node
    db.add(PromptCluster(
        label="backend", state="domain", domain="backend",
        color_hex="#b44aff", persistence=1.0,
        metadata={"source": "seed"},
    ))
    await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/domains")
    assert resp.status_code == 200
    domains = resp.json()
    assert len(domains) == 1
    assert domains[0]["label"] == "backend"
    assert domains[0]["color_hex"] == "#b44aff"
    assert domains[0]["source"] == "seed"
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_domains_router.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/domains.py backend/app/schemas/domains.py \
    backend/tests/test_domains_router.py backend/app/main.py
git commit -m "feat: add GET /api/domains and POST /api/domains/{id}/promote endpoints"
```

---

## Task 14: Cluster Router — Domain Validation on PATCH

**Files:**
- Modify: `backend/app/routers/clusters.py`

- [ ] **Step 1: Add domain validation**

In `backend/app/routers/clusters.py`, in the `update_cluster` function, replace the domain update block:

Before:
```python
if body.domain is not None:
    old_domain = cluster.domain
    cluster.domain = body.domain
```

After:
```python
if body.domain is not None:
    resolver = request.app.state.domain_resolver
    if body.domain not in resolver.domain_labels:
        raise HTTPException(
            422,
            f"Unknown domain: '{body.domain}'. Use GET /api/domains for valid options.",
        )
    old_domain = cluster.domain
    cluster.domain = body.domain
    logger.info("Cluster domain changed: id=%s '%s' -> '%s'", cluster_id, old_domain, body.domain)
```

Add `request: Request` to the function parameters and `from fastapi import Request` to imports.

- [ ] **Step 2: Run cluster router tests**

Run: `cd backend && python -m pytest tests/test_clusters_router.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/clusters.py
git commit -m "feat: validate domain against domain nodes on cluster PATCH"
```

---

## Task 15: Frontend — Domain Store + Color Resolution

**Files:**
- Create: `frontend/src/lib/api/domains.ts`
- Create: `frontend/src/lib/stores/domains.svelte.ts`
- Modify: `frontend/src/lib/utils/colors.ts`
- Modify: `frontend/src/lib/components/layout/Inspector.svelte`

- [ ] **Step 1: Create domain API client**

Create `frontend/src/lib/api/domains.ts`:

```typescript
import { apiClient } from './client';

export interface DomainEntry {
  id: string;
  label: string;
  color_hex: string;
  member_count: number;
  avg_score: number | null;
  source: 'seed' | 'discovered' | 'manual';
}

export async function fetchDomains(): Promise<DomainEntry[]> {
  const resp = await apiClient.get('/api/domains');
  return resp.data;
}
```

- [ ] **Step 2: Create domain store**

Create `frontend/src/lib/stores/domains.svelte.ts`:

```typescript
/**
 * Reactive domain store — single source of truth for domain data.
 * Replaces hardcoded DOMAIN_COLORS and KNOWN_DOMAINS.
 */

import { fetchDomains, type DomainEntry } from '$lib/api/domains';

const FALLBACK_COLOR = '#7a7a9e';

let domains = $state<DomainEntry[]>([]);
let loaded = $state(false);
let loading = $state(false);

let colors = $derived(
  Object.fromEntries(domains.map((d) => [d.label, d.color_hex]))
);

let labels = $derived(domains.map((d) => d.label));

async function load(): Promise<void> {
  if (loading) return;
  loading = true;
  try {
    domains = await fetchDomains();
    loaded = true;
  } catch (e) {
    console.error('Failed to fetch domains:', e);
  } finally {
    loading = false;
  }
}

function colorFor(domain: string): string {
  if (!domain) return FALLBACK_COLOR;
  const primary = domain.includes(':') ? domain.split(':')[0].trim() : domain.trim();
  const exact = colors[primary];
  if (exact) return exact;

  // Keyword fallback for free-form strings
  const lower = primary.toLowerCase();
  for (const [label, hex] of Object.entries(colors)) {
    if (label !== 'general' && lower.includes(label)) return hex;
  }
  return FALLBACK_COLOR;
}

function invalidate(): void {
  loaded = false;
  load();
}

export const domainStore = {
  get domains() { return domains; },
  get loaded() { return loaded; },
  get loading() { return loading; },
  get colors() { return colors; },
  get labels() { return labels; },
  load,
  colorFor,
  invalidate,
};
```

- [ ] **Step 3: Rewrite `colors.ts`**

In `frontend/src/lib/utils/colors.ts`, replace the `DOMAIN_COLORS` map and `taxonomyColor()`:

Remove:
```typescript
const DOMAIN_COLORS: Record<string, string> = { ... };
```

Replace `taxonomyColor()`:
```typescript
import { domainStore } from '$lib/stores/domains.svelte';

const FALLBACK_COLOR = '#7a7a9e';

export function taxonomyColor(color: string | null | undefined): string {
  if (!color) return FALLBACK_COLOR;
  if (color.startsWith('#')) return color;
  const primary = color.includes(':') ? color.split(':')[0].trim() : color;
  return domainStore.colorFor(primary);
}
```

Keep `scoreColor()`, `qHealthColor()`, `stateColor()`, and `DIMENSION_COLORS` unchanged.

- [ ] **Step 4: Update Inspector.svelte**

In `frontend/src/lib/components/layout/Inspector.svelte`, remove:
```typescript
const KNOWN_DOMAINS = ['backend', 'frontend', 'database', 'security', 'devops', 'fullstack', 'general'];
```

Replace with:
```typescript
import { domainStore } from '$lib/stores/domains.svelte';
```

Replace the domain picker `{#each}` block:
```svelte
{#each domainStore.labels as d (d)}
  <button
    class="domain-option"
    class:domain-option--active={d === parsePrimaryDomain(family.domain)}
    style="background: {domainStore.colorFor(d)};"
    onclick={() => selectDomain(d)}
    disabled={domainSaving}
    role="option"
    aria-selected={d === parsePrimaryDomain(family.domain)}
  >{d}</button>
{/each}
```

- [ ] **Step 5: Initialize domain store in app startup**

In the app's root layout or initialization, call `domainStore.load()`. Add SSE event subscription for `domain_created` and `taxonomy_changed` to call `domainStore.invalidate()`.

- [ ] **Step 6: Update color tests**

In `frontend/src/lib/utils/colors.test.ts`, update tests to mock the domain store or test `taxonomyColor()` with the store initialized.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api/domains.ts frontend/src/lib/stores/domains.svelte.ts \
    frontend/src/lib/utils/colors.ts frontend/src/lib/utils/colors.test.ts \
    frontend/src/lib/components/layout/Inspector.svelte
git commit -m "feat(frontend): replace hardcoded DOMAIN_COLORS and KNOWN_DOMAINS with API-driven domain store"
```

---

## Task 16: Domain Discovery — Warm Path Integration

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`
- Create: `backend/tests/taxonomy/test_domain_discovery.py`

- [ ] **Step 1: Write domain discovery tests**

Create `backend/tests/taxonomy/test_domain_discovery.py`. This is the most complex test file — it validates the full warm path domain proposal flow. Tests should cover:

- `test_propose_domains_creates_new_domain_node`: seed 8+ optimizations with `domain_raw="marketing: email"` under "general", run `_propose_domains()`, verify "marketing" domain node created
- `test_propose_domains_skips_below_threshold`: seed 3 optimizations (below `DOMAIN_DISCOVERY_MIN_MEMBERS=5`), verify no domain created
- `test_propose_domains_skips_inconsistent_primaries`: seed 8 optimizations with mixed `domain_raw` values, verify no domain created
- `test_propose_domains_skips_existing_domain`: seed optimizations with `domain_raw="backend"` (already a domain), verify no duplicate created
- `test_domain_ceiling_blocks_discovery`: create 30 domain nodes, verify `_propose_domains()` returns empty
- `test_reparent_to_domain`: after domain creation, verify clusters re-parented
- `test_backfill_optimization_domain`: after domain creation, verify `Optimization.domain` updated

- [ ] **Step 2: Implement `_propose_domains()` in engine.py**

Add to `backend/app/services/taxonomy/engine.py`:

```python
async def _propose_domains(self, db: AsyncSession) -> list[str]:
    """Discover new domains from coherent 'general' sub-populations."""
    from collections import Counter
    from app.services.pipeline_constants import (
        DOMAIN_COUNT_CEILING,
        DOMAIN_DISCOVERY_CONSISTENCY,
        DOMAIN_DISCOVERY_MIN_COHERENCE,
        DOMAIN_DISCOVERY_MIN_MEMBERS,
    )
    from app.utils.text_cleanup import parse_domain

    # Check ceiling
    domain_count = await db.scalar(
        select(func.count()).where(PromptCluster.state == "domain")
    )
    if domain_count >= DOMAIN_COUNT_CEILING:
        logger.warning(
            "Domain ceiling reached (%d/%d) — skipping domain discovery",
            domain_count, DOMAIN_COUNT_CEILING,
        )
        await event_bus.publish("domain_ceiling_reached", {
            "count": domain_count, "ceiling": DOMAIN_COUNT_CEILING,
        })
        return []

    # Find "general" domain node
    general_result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.label == "general",
        )
    )
    general = general_result.scalar_one_or_none()
    if not general:
        logger.error("Domain discovery: 'general' domain node not found — skipping")
        return []

    # Find candidate clusters under "general"
    candidates_result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.parent_id == general.id,
            PromptCluster.state.in_(["active", "mature"]),
            PromptCluster.member_count >= DOMAIN_DISCOVERY_MIN_MEMBERS,
            PromptCluster.coherence >= DOMAIN_DISCOVERY_MIN_COHERENCE,
        )
    )

    created = []
    for cluster in candidates_result.scalars():
        try:
            # Check domain_raw consistency
            opts_result = await db.execute(
                select(Optimization.domain_raw).where(
                    Optimization.cluster_id == cluster.id
                )
            )
            raw_domains = [row[0] for row in opts_result if row[0]]
            if not raw_domains:
                continue

            primaries = [parse_domain(d)[0] for d in raw_domains]
            counter = Counter(primaries)
            top_primary, top_count = counter.most_common(1)[0]

            if (
                top_primary != "general"
                and top_count / len(primaries) >= DOMAIN_DISCOVERY_CONSISTENCY
            ):
                # Check if domain already exists
                exists = await db.scalar(
                    select(func.count()).where(
                        PromptCluster.state == "domain",
                        PromptCluster.label == top_primary,
                    )
                )
                if exists > 0:
                    continue

                # Create domain node
                await self._create_domain_node(db, top_primary, cluster)
                created.append(top_primary)
                logger.info(
                    "Domain proposed: '%s' (members=%d, coherence=%.3f, consistency=%.1f%%)",
                    top_primary, cluster.member_count, cluster.coherence or 0,
                    (top_count / len(primaries)) * 100,
                )

        except Exception:
            logger.error(
                "Domain discovery failed for cluster %s — skipping",
                cluster.id, exc_info=True,
            )
            continue

    return created
```

Also implement `_create_domain_node()`, `_reparent_to_domain()`, and `_backfill_optimization_domain()` as private methods on the engine. Follow the spec (Section 3.3, 3.5, 3.6) for exact logic.

- [ ] **Step 3: Wire into warm path**

In `run_warm_path()`, after the existing lifecycle operations and before the quality gate, add:

```python
# Domain discovery
new_domains = await self._propose_domains(db)
if new_domains:
    logger.info("Warm path discovered %d new domains: %s", len(new_domains), new_domains)
```

- [ ] **Step 4: Run discovery tests**

Run: `cd backend && python -m pytest tests/taxonomy/test_domain_discovery.py -v`
Expected: All PASS

- [ ] **Step 5: Run full taxonomy test suite**

Run: `cd backend && python -m pytest tests/taxonomy/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_domain_discovery.py
git commit -m "feat(taxonomy): add warm-path domain discovery with ceiling guard and re-parenting"
```

---

## Task 17: Tree Integrity — Check and Auto-Repair

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py`
- Create: `backend/tests/taxonomy/test_tree_integrity.py`

- [ ] **Step 1: Write integrity check tests**

Create `backend/tests/taxonomy/test_tree_integrity.py` with tests from the spec Section 8B Risk 5:

- `test_integrity_detects_orphans`
- `test_integrity_detects_domain_mismatch`
- `test_integrity_detects_duplicate_domain_labels`
- `test_integrity_detects_weak_persistence`
- `test_integrity_passes_clean_tree`
- `test_auto_repair_orphans`
- `test_auto_repair_domain_mismatch`

- [ ] **Step 2: Implement `verify_domain_tree_integrity()` and `_repair_tree_violations()`**

Add to `backend/app/services/taxonomy/engine.py` as methods on `TaxonomyEngine`. Follow the spec Section 8B Risk 5 exactly for the 5 integrity checks and 3 auto-repair operations.

- [ ] **Step 3: Wire into warm path**

After domain discovery in the warm path:

```python
# Tree integrity check
violations = await self.verify_domain_tree_integrity(db)
if violations:
    repaired = await self._repair_tree_violations(db, violations)
    logger.warning(
        "Tree integrity: %d violations detected, %d auto-repaired, %d remaining",
        len(violations), repaired, len(violations) - repaired,
    )
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/taxonomy/test_tree_integrity.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_tree_integrity.py
git commit -m "feat(taxonomy): add tree integrity verification and auto-repair"
```

---

## Task 18: Health Endpoint — Domain Metrics

**Files:**
- Modify: `backend/app/routers/health.py`
- Modify: `backend/app/tools/health.py`

- [ ] **Step 1: Add domain metrics to health router**

In `backend/app/routers/health.py`, add to the health response:

```python
from app.services.pipeline_constants import DOMAIN_COUNT_CEILING

domain_count = await db.scalar(
    select(func.count()).where(PromptCluster.state == "domain")
)

# Include in response
"domain_count": domain_count,
"domain_ceiling": DOMAIN_COUNT_CEILING,
```

- [ ] **Step 2: Add domain metrics to MCP health tool**

Mirror the same addition in `backend/app/tools/health.py`.

- [ ] **Step 3: Run health tests**

Run: `cd backend && python -m pytest tests/ -k "health" -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/health.py backend/app/tools/health.py
git commit -m "feat: add domain_count and domain_ceiling to health endpoint"
```

---

## Task 19: Frontend — StatusBar + Topology Domain Rendering

**Files:**
- Modify: `frontend/src/lib/components/layout/StatusBar.svelte`
- Modify: `frontend/src/lib/components/taxonomy/TopologyData.ts`

- [ ] **Step 1: StatusBar domain count**

In `StatusBar.svelte`, add domain count display near the existing cluster count. Show amber color when utilization >= 80%:

```svelte
{#if domainStore.loaded}
  <span class="status-item" style="color: {domainCount >= domainCeiling * 0.8 ? 'var(--color-neon-yellow)' : 'var(--color-text-dim)'}">
    {domainCount} domains
  </span>
{/if}
```

- [ ] **Step 2: TopologyData domain node sizing**

In `TopologyData.ts`, when converting API nodes to scene nodes, domain nodes get 2x radius:

```typescript
const sizeMult = node.state === 'domain' ? 2.0 : (stateMultiplier[node.state] ?? 1.0);
```

- [ ] **Step 3: Run frontend tests**

Run: `cd frontend && npm run test`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/layout/StatusBar.svelte \
    frontend/src/lib/components/taxonomy/TopologyData.ts
git commit -m "feat(frontend): domain count in StatusBar, 2x domain node size in topology"
```

---

## Task 20: MCP Server Initialization

**Files:**
- Modify: `backend/app/mcp_server.py`

- [ ] **Step 1: Add domain services to MCP process init**

In `backend/app/mcp_server.py`, inside the `_process_initialized` guard block (where taxonomy engine and context service are initialized), add:

```python
from app.services.domain_resolver import DomainResolver
from app.services.domain_signal_loader import DomainSignalLoader
from app.tools._shared import set_domain_resolver, set_signal_loader

domain_resolver = DomainResolver()
signal_loader = DomainSignalLoader()
async with async_session_factory() as _init_db:
    await domain_resolver.load(_init_db)
    await signal_loader.load(_init_db)
set_domain_resolver(domain_resolver)
set_signal_loader(signal_loader)

# Subscribe to domain events
event_bus.subscribe("domain_created", lambda e: asyncio.create_task(_reload_domain_caches()))
event_bus.subscribe("taxonomy_changed", lambda e: asyncio.create_task(_reload_domain_caches()))
```

Add the reload helper:

```python
async def _reload_domain_caches() -> None:
    try:
        from app.tools._shared import get_domain_resolver, get_signal_loader
        async with async_session_factory() as db:
            resolver = get_domain_resolver()
            await resolver.load(db)
            loader = get_signal_loader()
            if loader:
                await loader.load(db)
    except Exception:
        logger.error("MCP domain cache reload failed", exc_info=True)
```

- [ ] **Step 2: Run MCP tests**

Run: `cd backend && python -m pytest tests/ -k "mcp" -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/mcp_server.py
git commit -m "feat: wire domain services into MCP server process initialization"
```

---

## Task 21: Final Integration Test + Cleanup

**Files:**
- Run: full test suite
- Modify: any remaining broken imports

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && python -m pytest --cov=app -v 2>&1 | tail -50`

Fix any remaining import errors from removed `VALID_DOMAINS` or `apply_domain_gate` references. Common fixes:
- Test files that import `VALID_DOMAINS` directly — replace with `DomainResolver` fixture
- Test files that call `apply_domain_gate()` — replace with `DomainResolver.resolve()`

- [ ] **Step 2: Run frontend test suite**

Run: `cd frontend && npm run test`

Fix any color test failures from removed `DOMAIN_COLORS`.

- [ ] **Step 3: Run type checks**

Run: `cd frontend && npx svelte-check`
Run: `cd backend && python -m mypy app/ --ignore-missing-imports`

- [ ] **Step 4: Update CLAUDE.md**

Add to the root `CLAUDE.md` under "Key architectural decisions":

```markdown
- **Unified domain taxonomy**: Domains are `PromptCluster` nodes with `state="domain"`. No hardcoded domain constants. `DomainResolver` (cached label lookup) replaces `VALID_DOMAINS`. `DomainSignalLoader` (metadata-driven keywords) replaces `_DOMAIN_SIGNALS`. Warm path discovers new domains from coherent "general" sub-populations. Five stability guardrails prevent evolutionary drift. See ADR-004.
```

- [ ] **Step 5: Update CHANGELOG.md**

Add under `## Unreleased`:

```markdown
### Added
- Unified domain taxonomy — domains are now first-class taxonomy nodes discovered organically from user behavior (ADR-004)
- `GET /api/domains` endpoint for dynamic domain palette
- `POST /api/domains/{id}/promote` for manual cluster-to-domain promotion
- Warm-path domain discovery with configurable thresholds
- Domain stability guardrails (color pinning, retire exemption, merge approval, coherence floor, split isolation)
- Tree integrity verification and auto-repair
- Domain ceiling guard and archival suggestions

### Removed
- `VALID_DOMAINS` constant — replaced by domain nodes in database
- `DOMAIN_COLORS` frontend constant — replaced by API-driven domain store
- `KNOWN_DOMAINS` Inspector constant — replaced by dynamic domain picker
- `_DOMAIN_SIGNALS` heuristic analyzer constant — replaced by `DomainSignalLoader`
- `apply_domain_gate()` — replaced by `DomainResolver.resolve()`
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: unified domain taxonomy — complete integration and cleanup"
```

---

## Deferred: Migration Script

The Alembic migration (spec Section 12) should be written AFTER all code changes are integrated and tests pass. It creates seed domain nodes, re-parents existing clusters, and backfills optimizations. This is a data migration that depends on the new `metadata` column and domain node logic being in place.

Create: `backend/alembic/versions/xxxx_add_domain_nodes.py`

Steps:
1. Add `metadata` column
2. Add `ix_prompt_cluster_state_label` index
3. Insert 7 seed domain nodes with pre-computed embeddings, colors, and keyword metadata
4. Re-parent existing clusters under matching domain nodes
5. Backfill `Optimization.domain` for resolvable `domain_raw` values
6. Run `verify_domain_tree_integrity()` post-migration

This should be its own commit: `feat: add Alembic migration for domain node seeding and re-parenting`
