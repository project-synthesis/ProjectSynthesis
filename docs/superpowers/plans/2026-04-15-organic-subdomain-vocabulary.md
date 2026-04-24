# Organic Sub-Domain Vocabulary Implementation Plan

**Status:** Shipped (v0.3.32). Static `_DOMAIN_QUALIFIERS` removed; Haiku-generated vocabulary (`generated_qualifiers` in domain node metadata) is now primary, with TF-IDF `signal_keywords` as fallback. `DomainSignalLoader` caches + serves the organic vocabulary. Historical record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static `_DOMAIN_QUALIFIERS` dict with fully organic LLM-generated vocabulary so all domains discover sub-domains through the same pipeline.

**Architecture:** Delete the static vocabulary from `heuristic_analyzer.py`. Extend `DomainSignalLoader` with a qualifier cache populated from domain node metadata (`generated_qualifiers`). The warm path's Phase 5 generates vocabulary for ALL domains (no static gate), caches it on the domain node, and pushes it to the loader. The hot path's `_enrich_domain_qualifier()` reads from the loader instead of the static dict. Enrichment threshold drops from 2 to 1 keyword hit.

**Tech Stack:** Python 3.12, SQLAlchemy async, pytest, Haiku LLM via existing `generate_qualifier_vocabulary()`

**Spec:** `docs/superpowers/specs/2026-04-15-organic-subdomain-vocabulary-design.md`

---

## File Structure

| File | Change | Responsibility |
|------|--------|---------------|
| `backend/app/services/domain_signal_loader.py` | Modify | Add `_qualifier_cache`, `get_qualifiers()`, `refresh_qualifiers()`, update `load()` to populate from DB |
| `backend/app/services/heuristic_analyzer.py` | Modify | Delete `_DOMAIN_QUALIFIERS`, refactor `_enrich_domain_qualifier()` to use loader |
| `backend/app/services/taxonomy/_constants.py` | Modify | Change `SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS` from 2 to 1 |
| `backend/app/services/taxonomy/engine.py` | Modify | Remove static vocab gate in `_propose_sub_domains()` |
| `backend/app/routers/health.py` | Modify | Wire qualifier stats into health response |
| `backend/app/services/taxonomy/labeling.py` | Modify | Update docstring |
| `backend/tests/taxonomy/test_sub_domain_lifecycle.py` | Modify | Remove `_DOMAIN_QUALIFIERS` import |
| `backend/tests/test_domain_signal_loader.py` | Modify | Add qualifier cache tests |
| `backend/tests/test_heuristic_analyzer.py` | Modify | Update enrichment tests for organic vocab |

---

### Task 1: Extend DomainSignalLoader with qualifier cache

**Files:**
- Modify: `backend/app/services/domain_signal_loader.py`
- Test: `backend/tests/test_domain_signal_loader.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_domain_signal_loader.py`:

```python
def test_get_qualifiers_returns_empty_on_miss():
    """get_qualifiers returns empty dict when domain has no cached vocab."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    result = loader.get_qualifiers("saas")
    assert result == {}


def test_refresh_qualifiers_populates_cache():
    """refresh_qualifiers stores vocab and get_qualifiers returns it."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    vocab = {"growth": ["metrics", "kpi", "dashboard"], "pricing": ["tier", "billing"]}
    loader.refresh_qualifiers("saas", vocab)

    result = loader.get_qualifiers("saas")
    assert result == vocab
    assert loader.get_qualifiers("backend") == {}  # other domains unaffected


def test_qualifier_hit_miss_counters():
    """get_qualifiers increments hit/miss counters."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    loader.refresh_qualifiers("backend", {"auth": ["login"]})

    loader.get_qualifiers("backend")  # hit
    loader.get_qualifiers("backend")  # hit
    loader.get_qualifiers("saas")     # miss

    stats = loader.stats()
    assert stats["qualifier_cache_hits"] == 2
    assert stats["qualifier_cache_misses"] == 1
    assert stats["domains_with_vocab"] == 1
    assert stats["domains_without_vocab"] == 0  # only tracks cache lookups, not all domains


def test_stats_returns_qualifier_fields():
    """stats() includes all qualifier-related fields."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    stats = loader.stats()
    assert "qualifier_cache_hits" in stats
    assert "qualifier_cache_misses" in stats
    assert "domains_with_vocab" in stats
    assert "last_qualifier_refresh" in stats


@pytest.mark.asyncio
async def test_load_populates_qualifier_cache_from_metadata(db):
    """load() reads generated_qualifiers from domain node metadata into cache."""
    from app.services.domain_signal_loader import DomainSignalLoader

    # Create a domain node with generated_qualifiers in metadata
    import json
    from app.models import PromptCluster
    node = PromptCluster(
        label="saas",
        state="domain",
        domain="saas",
        color_hex="#00ff00",
        member_count=0,
        cluster_metadata={
            "signal_keywords": [["metrics", 0.9]],
            "generated_qualifiers": {
                "growth": ["metrics", "kpi", "dashboard"],
                "pricing": ["tier", "billing", "subscription"],
            },
        },
    )
    db.add(node)
    await db.commit()

    loader = DomainSignalLoader()
    await loader.load(db)

    # Qualifier cache should be populated from metadata
    qualifiers = loader.get_qualifiers("saas")
    assert "growth" in qualifiers
    assert "pricing" in qualifiers
    assert "metrics" in qualifiers["growth"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/test_domain_signal_loader.py::test_get_qualifiers_returns_empty_on_miss tests/test_domain_signal_loader.py::test_refresh_qualifiers_populates_cache tests/test_domain_signal_loader.py::test_qualifier_hit_miss_counters tests/test_domain_signal_loader.py::test_stats_returns_qualifier_fields tests/test_domain_signal_loader.py::test_load_populates_qualifier_cache_from_metadata -v`
Expected: FAIL with `AttributeError: 'DomainSignalLoader' object has no attribute 'get_qualifiers'`

- [ ] **Step 3: Implement the qualifier cache**

In `backend/app/services/domain_signal_loader.py`, add to `__init__`:

```python
    def __init__(self) -> None:
        self._signals: dict[str, list[tuple[str, float]]] = {}
        self._patterns: dict[str, re.Pattern[str]] = {}
        # Organic qualifier vocabulary cache — populated by Phase 5 via
        # refresh_qualifiers() and by load() from domain node metadata.
        self._qualifier_cache: dict[str, dict[str, list[str]]] = {}
        self._qualifier_hits: int = 0
        self._qualifier_misses: int = 0
        self._last_qualifier_refresh: str | None = None
```

Add the `get_qualifiers` method after the `classify` method:

```python
    # ------------------------------------------------------------------
    # Qualifier vocabulary cache (organic sub-domain discovery)
    # ------------------------------------------------------------------

    def get_qualifiers(self, domain: str) -> dict[str, list[str]]:
        """Return the organic qualifier vocabulary for a domain.

        Returns an empty dict on cache miss (domain has no vocabulary yet).
        Never raises.
        """
        result = self._qualifier_cache.get(domain.strip().lower(), {})
        if result:
            self._qualifier_hits += 1
        else:
            self._qualifier_misses += 1
        return result

    def refresh_qualifiers(
        self, domain: str, qualifiers: dict[str, list[str]],
    ) -> None:
        """Push freshly generated qualifier vocabulary into the cache.

        Called by Phase 5 after Haiku generates vocabulary for a domain.
        Immediately available for subsequent hot-path enrichment calls.
        """
        from datetime import datetime, timezone

        if not qualifiers:
            return
        self._qualifier_cache[domain.strip().lower()] = qualifiers
        self._last_qualifier_refresh = datetime.now(timezone.utc).isoformat()
        logger.info(
            "refresh_qualifiers: domain=%s groups=%d (e.g. %s)",
            domain, len(qualifiers),
            ", ".join(list(qualifiers.keys())[:3]),
        )

    def stats(self) -> dict:
        """Return diagnostic stats for the health endpoint."""
        return {
            "qualifier_cache_hits": self._qualifier_hits,
            "qualifier_cache_misses": self._qualifier_misses,
            "domains_with_vocab": len(self._qualifier_cache),
            "domains_without_vocab": 0,  # not tracked globally
            "last_qualifier_refresh": self._last_qualifier_refresh,
        }
```

Update `load()` to also read `generated_qualifiers` from domain node metadata. After line 93 (`new_signals[cluster.label] = keywords`), add:

```python
                # Also load organic qualifier vocabulary from metadata
                gen_qual = self._extract_generated_qualifiers(cluster.cluster_metadata)
                if gen_qual:
                    new_qualifier_cache[cluster.label] = gen_qual
```

And before the `new_signals` assignment at the top of the try block, add:

```python
            new_qualifier_cache: dict[str, dict[str, list[str]]] = {}
```

And after `self._precompile_patterns()` (line 96), add:

```python
            self._qualifier_cache = new_qualifier_cache
            if new_qualifier_cache:
                logger.info(
                    "DomainSignalLoader loaded qualifier vocab for %d domains",
                    len(new_qualifier_cache),
                )
```

Add the extraction helper method after `_extract_keywords`:

```python
    def _extract_generated_qualifiers(
        self, metadata: Any,
    ) -> dict[str, list[str]]:
        """Extract ``generated_qualifiers`` from cluster_metadata.

        Returns an empty dict when the key is absent or the value is malformed.
        """
        if not isinstance(metadata, dict):
            return {}
        raw = metadata.get("generated_qualifiers")
        if not raw or not isinstance(raw, dict):
            return {}
        # Validate structure: {str: list[str]}
        result: dict[str, list[str]] = {}
        for key, val in raw.items():
            if isinstance(key, str) and isinstance(val, list):
                keywords = [v for v in val if isinstance(v, str)]
                if keywords:
                    result[key] = keywords
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/test_domain_signal_loader.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domain_signal_loader.py backend/tests/test_domain_signal_loader.py
git commit -m "feat(taxonomy): extend DomainSignalLoader with organic qualifier cache"
```

---

### Task 2: Lower SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS to 1

**Files:**
- Modify: `backend/app/services/taxonomy/_constants.py:114`

- [ ] **Step 1: Change the constant**

In `backend/app/services/taxonomy/_constants.py`, change line 114:

```python
SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS = 1       # minimum keyword hits to accept a qualifier
```

Update the comment at lines 108-109 to reflect the change:

```python
# Adaptive threshold: max(LOW, HIGH - SCALE_RATE * total_members).
# MIN_KEYWORD_HITS = 1 because the domain is already confirmed by
# classification — a single keyword hit is strong enough to select
# the specific qualifier within that domain.
```

- [ ] **Step 2: Verify import**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -c "from app.services.taxonomy._constants import SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS; print(SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS)"`
Expected: `1`

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/taxonomy/_constants.py
git commit -m "feat(taxonomy): lower qualifier enrichment threshold from 2 to 1 keyword hit"
```

---

### Task 3: Refactor _enrich_domain_qualifier() to use organic vocab

**Files:**
- Modify: `backend/app/services/heuristic_analyzer.py:275-363`
- Test: `backend/tests/test_heuristic_analyzer.py`

- [ ] **Step 1: Write the failing test**

Find the existing test file. Add a test that verifies organic vocab is used:

```python
def test_enrich_domain_qualifier_uses_organic_vocab():
    """_enrich_domain_qualifier reads from DomainSignalLoader, not static dict."""
    from unittest.mock import MagicMock, patch

    from app.services.heuristic_analyzer import _enrich_domain_qualifier

    # Create a mock loader with organic vocab
    mock_loader = MagicMock()
    mock_loader.get_qualifiers.return_value = {
        "growth": ["metrics", "kpi", "dashboard"],
        "pricing": ["tier", "billing"],
    }

    with patch("app.services.heuristic_analyzer.get_signal_loader", return_value=mock_loader):
        result = _enrich_domain_qualifier("saas", "analyze our saas metrics dashboard")

    assert result == "saas: growth"
    mock_loader.get_qualifiers.assert_called_once_with("saas")


def test_enrich_domain_qualifier_returns_plain_on_empty_cache():
    """When loader has no vocab for domain, return plain domain unchanged."""
    from unittest.mock import MagicMock, patch

    from app.services.heuristic_analyzer import _enrich_domain_qualifier

    mock_loader = MagicMock()
    mock_loader.get_qualifiers.return_value = {}

    with patch("app.services.heuristic_analyzer.get_signal_loader", return_value=mock_loader):
        result = _enrich_domain_qualifier("saas", "some saas prompt")

    assert result == "saas"


def test_enrich_domain_qualifier_single_keyword_hit_suffices():
    """With threshold=1, a single keyword hit enriches the domain."""
    from unittest.mock import MagicMock, patch

    from app.services.heuristic_analyzer import _enrich_domain_qualifier

    mock_loader = MagicMock()
    mock_loader.get_qualifiers.return_value = {
        "pricing": ["subscription", "billing", "tier"],
    }

    with patch("app.services.heuristic_analyzer.get_signal_loader", return_value=mock_loader):
        # Only one keyword hit: "subscription"
        result = _enrich_domain_qualifier("saas", "manage saas subscription lifecycle")

    assert result == "saas: pricing"


def test_enrich_domain_qualifier_no_loader_returns_plain():
    """When get_signal_loader() returns None, return plain domain."""
    from unittest.mock import patch

    from app.services.heuristic_analyzer import _enrich_domain_qualifier

    with patch("app.services.heuristic_analyzer.get_signal_loader", return_value=None):
        result = _enrich_domain_qualifier("saas", "saas metrics dashboard")

    assert result == "saas"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/test_heuristic_analyzer.py::test_enrich_domain_qualifier_uses_organic_vocab tests/test_heuristic_analyzer.py::test_enrich_domain_qualifier_returns_plain_on_empty_cache tests/test_heuristic_analyzer.py::test_enrich_domain_qualifier_single_keyword_hit_suffices tests/test_heuristic_analyzer.py::test_enrich_domain_qualifier_no_loader_returns_plain -v`
Expected: FAIL — current code reads from static dict, not loader

- [ ] **Step 3: Delete _DOMAIN_QUALIFIERS and refactor _enrich_domain_qualifier()**

In `backend/app/services/heuristic_analyzer.py`:

**Delete** the entire `_DOMAIN_QUALIFIERS` dict and its comment header (lines 275-322).

**Replace** `_enrich_domain_qualifier()` (lines 325-363) with:

```python
def _enrich_domain_qualifier(domain: str, prompt_lower: str) -> str:
    """Enrich a plain domain label with a sub-qualifier from organic vocabulary.

    Reads qualifier vocabulary from ``DomainSignalLoader.get_qualifiers()``,
    which is populated organically by Haiku from cluster labels during the
    warm path's Phase 5 discovery.

    If *domain* already contains a qualifier (has ``:``) or the loader has
    no vocabulary for this domain, returns the original string unchanged.

    Returns:
        Enriched domain string (e.g., ``"saas: growth"``) or original.
    """
    if ":" in domain:
        return domain

    primary = domain.strip().lower()

    try:
        from app.services.domain_signal_loader import get_signal_loader

        loader = get_signal_loader()
        if not loader:
            return domain
        qualifiers = loader.get_qualifiers(primary)
    except Exception:
        return domain

    if not qualifiers:
        return domain

    best_qualifier: str | None = None
    best_hits = 0

    for qualifier_name, keywords in qualifiers.items():
        hits = sum(1 for kw in keywords if kw in prompt_lower)
        if hits >= 1 and hits > best_hits:
            best_hits = hits
            best_qualifier = qualifier_name

    if best_qualifier:
        logger.debug(
            "qualifier_enrichment: domain=%s qualifier=%s hits=%d",
            primary, best_qualifier, best_hits,
        )
        return f"{primary}: {best_qualifier}"
    return domain
```

Note: the threshold is now hardcoded to `1` in the `if hits >= 1` check, matching the constant change from Task 2. We no longer import `SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS` here since the constant is also used by Phase 5's Source 2 intent_label matching (engine.py uses its own `>= 1` check at line 1955).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/test_heuristic_analyzer.py -v`
Expected: All PASS (including existing tests — function signature unchanged)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/heuristic_analyzer.py backend/tests/test_heuristic_analyzer.py
git commit -m "feat(taxonomy): replace static _DOMAIN_QUALIFIERS with organic DomainSignalLoader vocab"
```

---

### Task 4: Remove static vocab gate in _propose_sub_domains()

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py:1767-1887`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/taxonomy/test_sub_domain_lifecycle.py` (after updating the import — see Step 3):

```python
@pytest.mark.asyncio
async def test_propose_sub_domains_generates_vocab_for_all_domains(self, db, mock_provider):
    """Phase 5 generates vocabulary for domains regardless of static vocab presence."""
    from unittest.mock import AsyncMock, patch

    from app.services.taxonomy.engine import TaxonomyEngine

    mock_embedding = AsyncMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create a domain node (e.g., "saas") with child clusters but no cached vocab
    from app.models import PromptCluster
    import numpy as np

    domain = PromptCluster(
        label="saas", state="domain", domain="saas",
        color_hex="#00ff00", member_count=0,
    )
    db.add(domain)
    await db.flush()

    for i in range(4):
        cluster = PromptCluster(
            label=f"SaaS Cluster {i}", state="active", domain="saas",
            parent_id=domain.id, color_hex="#ff0000", member_count=3,
            centroid_embedding=np.random.randn(384).astype(np.float32).tobytes(),
        )
        db.add(cluster)
    await db.commit()

    generate_calls = []

    async def fake_generate(provider, domain_label, cluster_labels, model):
        generate_calls.append(domain_label)
        return {"growth": ["metrics", "kpi"], "pricing": ["tier", "billing"]}

    with patch(
        "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
        fake_generate,
    ):
        created = await engine._propose_sub_domains(db)

    # Verify generation was called for "saas" even though it has static vocab
    assert "saas" in generate_calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py::TestSubDomainDiscovery::test_propose_sub_domains_generates_vocab_for_all_domains -v`
Expected: FAIL — the static vocab gate skips generation for "saas"

- [ ] **Step 3: Remove the static vocab gate and import**

In `backend/app/services/taxonomy/engine.py`:

**Line 1767:** Remove the import of `_DOMAIN_QUALIFIERS`:
```python
        # Delete this line:
        # from app.services.heuristic_analyzer import _DOMAIN_QUALIFIERS
```

**Lines 1828-1829:** Replace the static vocab lookup with empty dict:
```python
            # Old:
            # domain_qualifiers = _DOMAIN_QUALIFIERS.get(domain_node.label, {})
            # New — always start with empty, organic generation fills it:
            domain_qualifiers: dict[str, list[str]] = {}
```

**Line 1835:** Remove the `if not domain_qualifiers:` guard — the entire vocabulary generation block should now run unconditionally. Change the indentation of lines 1836-1886 back one level (they were inside the `if not domain_qualifiers:` block). The block starts with:
```python
                cached_vocab = meta.get("generated_qualifiers")
```
and ends with:
```python
                elif cached_vocab and isinstance(cached_vocab, dict):
                    domain_qualifiers = cached_vocab
```

After de-indenting, this code should flow directly after `domain_qualifiers: dict[str, list[str]] = {}` — no guard.

**After the vocabulary generation block**, add a call to push vocab to the DomainSignalLoader cache:

```python
            # Push vocab to DomainSignalLoader for hot-path enrichment
            if domain_qualifiers:
                try:
                    from app.services.domain_signal_loader import get_signal_loader
                    loader = get_signal_loader()
                    if loader:
                        loader.refresh_qualifiers(domain_node.label, domain_qualifiers)
                        try:
                            get_event_logger().log_decision(
                                path="warm", op="discover",
                                decision="vocab_cache_propagated",
                                context={
                                    "domain": domain_node.label,
                                    "qualifier_count": len(domain_qualifiers),
                                },
                            )
                        except RuntimeError:
                            pass
                except Exception as cache_exc:
                    logger.warning(
                        "Failed to propagate vocab to DomainSignalLoader for '%s': %s",
                        domain_node.label, cache_exc,
                    )
```

**Also add observability** to the vocabulary generation/refresh. After the existing `sub_domain_vocab_generated` event (around line 1873), add timing. Wrap the `generate_qualifier_vocabulary` call with `time.monotonic()`:

```python
            import time as _vocab_time
            _vocab_start = _vocab_time.monotonic()
```

And after successful generation, compute duration:

```python
                        _vocab_ms = round((_vocab_time.monotonic() - _vocab_start) * 1000, 1)
```

Include `generation_ms` in the event context.

- [ ] **Step 4: Update test_sub_domain_lifecycle.py import**

In `backend/tests/taxonomy/test_sub_domain_lifecycle.py`, find line 548:
```python
        from app.services.heuristic_analyzer import _DOMAIN_QUALIFIERS
```

Replace the test `test_intent_label_fallback_matching` (lines 545-563) with a version that uses inline test fixtures instead of the deleted static dict:

```python
    @pytest.mark.asyncio
    async def test_intent_label_fallback_matching(self, db):
        """intent_label keyword matching finds qualifiers from vocabulary."""
        # Use inline test vocab instead of deleted _DOMAIN_QUALIFIERS
        domain_qualifiers = {
            "auth": ["auth", "authentication", "login", "session", "oauth", "jwt", "token"],
            "api": ["api", "endpoint", "rest", "graphql", "route", "handler"],
        }
        intent_label = "MCP routing architecture"
        intent_lower = intent_label.lower()

        best_q = None
        best_hits = 0
        for q_name, keywords in domain_qualifiers.items():
            hits = sum(1 for kw in keywords if kw in intent_lower)
            if hits > best_hits:
                best_hits = hits
                best_q = q_name

        # "routing" doesn't match any qualifier keywords
        assert best_q is None or best_hits == 0
```

- [ ] **Step 5: Run tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_lifecycle.py -v --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_sub_domain_lifecycle.py
git commit -m "feat(taxonomy): remove static vocab gate — all domains use organic vocabulary"
```

---

### Task 5: Wire qualifier stats into health endpoint

**Files:**
- Modify: `backend/app/routers/health.py`

- [ ] **Step 1: Add the field to HealthResponse**

In `backend/app/routers/health.py`, find the `HealthResponse` model. After the `classification_agreement` field (around line 97), add:

```python
    qualifier_vocab: dict | None = Field(
        default=None, description="Organic qualifier vocabulary cache stats.",
    )
```

- [ ] **Step 2: Add the stats collection**

In the health endpoint function, after the `recovery_metrics` block (around line 402), add:

```python
    # Organic qualifier vocabulary stats
    qualifier_vocab_stats: dict | None = None
    try:
        from app.services.domain_signal_loader import get_signal_loader
        _loader = get_signal_loader()
        if _loader:
            qualifier_vocab_stats = _loader.stats()
    except Exception:
        pass
```

And in the `HealthResponse()` constructor call, add:

```python
        qualifier_vocab=qualifier_vocab_stats,
```

- [ ] **Step 3: Verify endpoint**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && python -c "from app.routers.health import HealthResponse; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/health.py
git commit -m "feat(health): add organic qualifier vocabulary stats to health endpoint"
```

---

### Task 6: Update docstrings and documentation references

**Files:**
- Modify: `backend/app/services/taxonomy/labeling.py:164-165`
- Modify: Root `CLAUDE.md`
- Modify: `backend/CLAUDE.md`

- [ ] **Step 1: Update labeling.py docstring**

In `backend/app/services/taxonomy/labeling.py`, find line 164-165:
```python
    qualifier names to keyword lists, in the same format as the static
    ``_DOMAIN_QUALIFIERS`` entries.
```

Replace with:
```python
    qualifier names to keyword lists (e.g., ``{"growth": ["metrics", "kpi", ...]}``)
    stored in domain node ``cluster_metadata["generated_qualifiers"]``.
```

- [ ] **Step 2: Update root CLAUDE.md**

In the root `CLAUDE.md`, find the line mentioning `_DOMAIN_QUALIFIERS` in the Classification section. Replace `_DOMAIN_QUALIFIERS` vocabulary reference with:

```
Qualifier enrichment: `_enrich_domain_qualifier()` appends sub-qualifiers to `domain_raw` (e.g. "backend: auth") using organic vocabulary from `DomainSignalLoader` (generated by Haiku from cluster labels, cached on domain node metadata)
```

- [ ] **Step 3: Update backend/CLAUDE.md**

In `backend/CLAUDE.md`, find the heuristic_analyzer description mentioning `_DOMAIN_QUALIFIERS`. Replace with the same organic vocabulary reference.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/taxonomy/labeling.py CLAUDE.md backend/CLAUDE.md
git commit -m "docs: update references from static _DOMAIN_QUALIFIERS to organic vocabulary"
```

---

### Task 7: Run full test suite and lint

**Files:**
- None (verification only)

- [ ] **Step 1: Run warm path tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/taxonomy/ -v --tb=short`
Expected: All PASS

- [ ] **Step 2: Run domain signal loader tests**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest tests/test_domain_signal_loader.py tests/test_heuristic_analyzer.py -v`
Expected: All PASS

- [ ] **Step 3: Run full backend suite**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && pytest --tb=short -q`
Expected: All PASS (2184+ tests)

- [ ] **Step 4: Lint changed files**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend && source .venv/bin/activate && ruff check app/services/domain_signal_loader.py app/services/heuristic_analyzer.py app/services/taxonomy/engine.py app/services/taxonomy/_constants.py app/routers/health.py`
Expected: All checks passed

- [ ] **Step 5: Verify no remaining references to _DOMAIN_QUALIFIERS**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2 && grep -r "_DOMAIN_QUALIFIERS" backend/app/ backend/tests/ --include="*.py"`
Expected: No output (all references removed)

- [ ] **Step 6: Final lint fix commit if needed**

```bash
git add -u
git commit -m "style: fix lint in organic vocabulary refactor"
```
