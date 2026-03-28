# Unified Domain Taxonomy — Design Specification

**Date:** 2026-03-28
**Status:** Approved
**ADR:** [ADR-004](../../adr/ADR-004-unified-domain-taxonomy.md)
**Supersedes:** Domain sections of [Evolutionary Taxonomy Engine Design](2026-03-20-evolutionary-taxonomy-engine-design.md)

---

## 1. Motivation

The domain system is the last hardcoded classification in Project Synthesis. Strategies are adaptive (filesystem-discovered), taxonomy clusters are organic (HDBSCAN-discovered), but domains remain a 7-item constant. This creates three problems:

1. **Non-coding prompts are invisible.** Marketing, legal, education, data science, creative writing — all collapse to `domain="general"` with identical gray coloring, no filtering, and no strategy affinity.
2. **The taxonomy engine's intelligence is wasted.** It already clusters prompts by semantic similarity and evolves through quality-gated lifecycle mutations. Domains don't participate.
3. **Scaling requires code changes.** Adding a domain requires editing 5 files across 2 codebases, rebuilding, and redeploying.

**Decision:** Domains become `PromptCluster` nodes with `state="domain"`. The taxonomy engine evolves them organically. All hardcoded domain constants are removed. See ADR-004 for full decision context and alternatives considered.

---

## 2. Data Model

### 2.1 PromptCluster state enum expansion

The `state` column (`String(20)`) gains one new value:

```
candidate → active → mature → template → archived
                                ↑
                             domain  (new)
```

`state="domain"` nodes are top-level navigational categories. They share the `PromptCluster` schema (centroid, metrics, lifecycle timestamps) but follow different lifecycle rules (see Section 4: Guardrails).

### 2.2 Domain node schema

Domain nodes use existing `PromptCluster` columns. No new columns are added:

| Column | Domain node usage |
|--------|-------------------|
| `id` | UUID, same as any cluster |
| `parent_id` | `NULL` for root domains. Non-null for sub-domains (future) |
| `label` | Domain name: `"backend"`, `"marketing"`, etc. Unique among domain nodes |
| `state` | `"domain"` |
| `domain` | Self-referencing: equals own `label` |
| `task_type` | `"general"` (domains span task types) |
| `centroid_embedding` | Mean embedding of member cluster centroids |
| `member_count` | Count of direct child clusters |
| `usage_count` | Aggregate of child cluster usage |
| `avg_score` | Weighted mean of child cluster scores |
| `coherence` | Intra-domain coherence (expected lower than cluster coherence) |
| `separation` | Inter-domain separation |
| `stability` | Change rate across warm path cycles |
| `persistence` | `1.0` for seed domains; computed for discovered domains |
| `color_hex` | Pinned color — not recomputed by cold path |
| `preferred_strategy` | Most effective strategy for this domain (from feedback data) |

### 2.3 Domain node metadata

Domain-specific configuration stored in a new `metadata` JSON column on `PromptCluster`:

```python
metadata = Column(JSON, nullable=True)
```

For domain nodes, this holds:

```json
{
  "source": "seed",
  "signal_keywords": [
    ["api", 0.8], ["endpoint", 0.9], ["server", 0.8],
    ["middleware", 0.9], ["fastapi", 1.0]
  ],
  "discovered_at": null,
  "proposed_by_snapshot": null
}
```

| Field | Purpose |
|-------|---------|
| `source` | `"seed"` (migration-created) or `"discovered"` (warm path-created) |
| `signal_keywords` | `[keyword, weight]` pairs for heuristic classifier. Seed domains carry current hardcoded values. Discovered domains get TF-IDF-extracted keywords. |
| `discovered_at` | ISO timestamp when warm path proposed this domain (null for seeds) |
| `proposed_by_snapshot` | `TaxonomySnapshot.id` that triggered discovery (audit trail) |

Non-domain nodes have `metadata=NULL` — no overhead.

### 2.4 Optimization.domain column

Remains `String`, stores the domain node's `label`. Resolution path:

```
Optimization.domain = "backend"
  → SELECT id FROM prompt_cluster WHERE state='domain' AND label='backend'
  → Returns the domain node for join/aggregation queries
```

This is a soft reference by label, not a FK. The taxonomy engine maintains label uniqueness among domain nodes.

### 2.5 Index additions

```python
Index("ix_prompt_cluster_state_label", "state", "label")  # domain lookup by label
Index("uq_prompt_cluster_domain_label", "label", unique=True,
      postgresql_where=text("state = 'domain'"),
      sqlite_where=text("state = 'domain'"))  # label uniqueness among domain nodes
```

---

## 3. Domain Lifecycle

### 3.1 Seed domains (migration)

Seven domain nodes created at migration time:

| Label | Color | Source keywords |
|-------|-------|----------------|
| `backend` | `#b44aff` | api, endpoint, server, middleware, fastapi, django, flask, authentication, route |
| `frontend` | `#ff4895` | react, svelte, component, css, ui, layout, responsive, tailwind, vue |
| `database` | `#36b5ff` | sql, migration, schema, query, postgresql, sqlite, orm, table |
| `devops` | `#6366f1` | docker, ci/cd, kubernetes, terraform, nginx, monitoring, deploy |
| `security` | `#ff2255` | auth, encryption, vulnerability, cors, jwt, oauth, sanitize, injection, xss, csrf |
| `fullstack` | `#d946ef` | (computed: backend + frontend signal co-occurrence) |
| `general` | `#7a7a9e` | (catch-all: no keywords, matched by fallback) |

Centroid embeddings computed by embedding the concatenated keyword list via `all-MiniLM-L6-v2`.

### 3.2 Domain discovery (warm path)

Added as a post-HDBSCAN step in the warm path:

```python
async def _propose_domains(self, db: AsyncSession) -> list[str]:
    """Discover new domains from coherent 'general' sub-populations."""

    # 1. Find coherent clusters under "general" domain
    general_node = await self._get_domain_node(db, "general")
    candidates = await db.execute(
        select(PromptCluster).where(
            PromptCluster.parent_id == general_node.id,
            PromptCluster.state.in_(["active", "mature"]),
            PromptCluster.member_count >= DOMAIN_DISCOVERY_MIN_MEMBERS,  # 5
            PromptCluster.coherence >= DOMAIN_DISCOVERY_MIN_COHERENCE,   # 0.6
        )
    )

    # 2. For each candidate, check domain_raw consistency
    for cluster in candidates:
        optimizations = await db.execute(
            select(Optimization.domain_raw).where(
                Optimization.cluster_id == cluster.id
            )
        )
        primaries = [parse_domain(o.domain_raw)[0] for o in optimizations]
        counter = Counter(primaries)
        top_primary, top_count = counter.most_common(1)[0]

        if (
            top_primary != "general"
            and top_count / len(primaries) >= DOMAIN_DISCOVERY_CONSISTENCY  # 0.60
            and not await self._domain_exists(db, top_primary)
        ):
            await self._create_domain_node(db, top_primary, cluster)

    # 3. Return list of newly created domain labels
```

### 3.3 Domain node creation

```python
async def _create_domain_node(
    self, db: AsyncSession, label: str, seed_cluster: PromptCluster
) -> PromptCluster:
    # Compute color via OKLab max-distance from existing domain colors
    existing_colors = await self._get_domain_colors(db)
    color_hex = compute_max_distance_color(existing_colors)

    # Extract TF-IDF keywords from member prompts
    keywords = await self._extract_domain_keywords(db, seed_cluster)

    domain_node = PromptCluster(
        label=label,
        state="domain",
        domain=label,
        task_type="general",
        color_hex=color_hex,
        persistence=1.0,
        centroid_embedding=seed_cluster.centroid_embedding,
        member_count=0,
        metadata={
            "source": "discovered",
            "signal_keywords": keywords,
            "discovered_at": utcnow().isoformat(),
            "proposed_by_snapshot": self._current_snapshot_id,
        },
    )
    db.add(domain_node)
    await db.flush()

    # Re-parent qualifying clusters
    await self._reparent_to_domain(db, domain_node, label)

    # Backfill Optimization.domain
    await self._backfill_optimization_domain(db, domain_node)

    # Emit event
    await event_bus.publish("domain_created", {
        "label": label,
        "color_hex": color_hex,
        "source": "discovered",
    })

    return domain_node
```

### 3.4 OKLab max-distance color assignment

```python
def compute_max_distance_color(existing_hex: list[str]) -> str:
    """Find the OKLab color maximally distant from all existing domain colors.

    Also avoids tier accent colors:
      internal=#00e5ff, sampling=#22ff88, passthrough=#fbbf24
    """
    existing_lab = [hex_to_oklab(h) for h in existing_hex + TIER_ACCENTS]

    best_color = None
    best_min_dist = 0.0

    # Sample candidates in OKLab space (L=0.7 for neon brightness, sweep a/b)
    for a in np.linspace(-0.15, 0.15, 60):
        for b in np.linspace(-0.15, 0.15, 60):
            candidate = OKLab(L=0.7, a=a, b=b)
            min_dist = min(oklab_distance(candidate, e) for e in existing_lab)
            if min_dist > best_min_dist:
                best_min_dist = min_dist
                best_color = candidate

    return oklab_to_hex(best_color)
```

### 3.5 Keyword extraction for discovered domains

```python
async def _extract_domain_keywords(
    self, db: AsyncSession, cluster: PromptCluster, top_k: int = 15
) -> list[list[str | float]]:
    """Extract top TF-IDF keywords from cluster member prompts."""
    optimizations = await db.execute(
        select(Optimization.raw_prompt).where(
            Optimization.cluster_id == cluster.id
        )
    )
    texts = [o.raw_prompt for o in optimizations if o.raw_prompt]

    if not texts:
        return []

    vectorizer = TfidfVectorizer(
        max_features=top_k,
        stop_words="english",
        ngram_range=(1, 2),
    )
    tfidf = vectorizer.fit_transform(texts)
    feature_names = vectorizer.get_feature_names_out()
    scores = tfidf.mean(axis=0).A1

    ranked = sorted(zip(feature_names, scores), key=lambda x: x[1], reverse=True)
    return [[kw, round(float(score), 2)] for kw, score in ranked[:top_k]]
```

### 3.6 Re-parenting and backfill

When a new domain is created, two operations execute:

**Re-parent clusters:**
```sql
UPDATE prompt_cluster
SET parent_id = :new_domain_id, domain = :new_label
WHERE parent_id = :general_domain_id
  AND domain = 'general'
  AND id IN (
    SELECT cluster_id FROM optimizations
    WHERE domain_raw LIKE :label || '%'
    GROUP BY cluster_id
    HAVING COUNT(*) * 1.0 / (SELECT COUNT(*) FROM optimizations WHERE cluster_id = prompt_cluster.id) >= 0.6
  )
```

**Backfill optimizations:**
```sql
UPDATE optimizations
SET domain = :new_label
WHERE cluster_id IN (SELECT id FROM prompt_cluster WHERE parent_id = :new_domain_id)
  AND domain = 'general'
```

---

## 4. Stability Guardrails

### 4.1 Color pinning

**Location:** `taxonomy/coloring.py`

The cold path's `assign_colors()` function skips domain nodes:

```python
async def assign_colors(nodes: list[PromptCluster]) -> None:
    for node in nodes:
        if node.state == "domain":
            continue  # Domain colors are pinned at creation time
        node.color_hex = oklab_from_umap(node.umap_x, node.umap_y, node.umap_z)
```

### 4.2 Retire exemption

**Location:** `taxonomy/lifecycle.py`

```python
async def retire(db: AsyncSession, node: PromptCluster, ...) -> bool:
    if node.state == "domain":
        logger.info("Skipping retire for domain node: %s", node.label)
        return False
    # ... existing retire logic
```

### 4.3 Separate coherence floor

**Location:** `taxonomy/quality.py`

```python
DOMAIN_COHERENCE_FLOOR = 0.3
CLUSTER_COHERENCE_FLOOR = 0.6

def coherence_threshold(node: PromptCluster) -> float:
    return DOMAIN_COHERENCE_FLOOR if node.state == "domain" else CLUSTER_COHERENCE_FLOOR
```

### 4.4 Merge approval gate

**Location:** `taxonomy/lifecycle.py`

```python
async def merge(db: AsyncSession, survivor: PromptCluster, loser: PromptCluster, ...) -> bool:
    if survivor.state == "domain" or loser.state == "domain":
        await event_bus.publish("domain_merge_proposed", {
            "survivor": survivor.label,
            "loser": loser.label,
            "similarity": cosine_similarity,
        })
        logger.info("Domain merge proposed (requires approval): %s ← %s", survivor.label, loser.label)
        return False
    # ... existing merge logic
```

### 4.5 Split creates clusters

**Location:** `taxonomy/lifecycle.py`

```python
async def split(db: AsyncSession, parent: PromptCluster, ...) -> list[PromptCluster]:
    # Children always start as candidates, even when parent is a domain
    children = []
    for centroid, member_ids in sub_clusters:
        child = PromptCluster(
            parent_id=parent.id,
            state="candidate",  # Never "domain"
            domain=parent.label if parent.state == "domain" else parent.domain,
            # ...
        )
        children.append(child)
    return children
```

---

## 5. Removals — Code Deleted

No fallback maps, no legacy constants. Clean removal.

### 5.1 Backend removals

| File | What | Replacement |
|------|------|-------------|
| `pipeline_constants.py` | `VALID_DOMAINS` set | Domain nodes: `SELECT label FROM prompt_cluster WHERE state='domain'` |
| `pipeline_constants.py` | `apply_domain_gate()` | Taxonomy engine embedding-based assignment with "general" domain fallback |
| `heuristic_analyzer.py` | `_DOMAIN_SIGNALS` dict (hardcoded) | `DomainSignalLoader` reads from domain node metadata |
| `heuristic_analyzer.py` | `_classify_domain()` fullstack promotion hack | Fullstack is a seed domain node; classification via signal matching like any other domain |
| `optimize.py:586-591` | `VALID_DOMAINS` validation | Domain label validation against `state="domain"` nodes |
| `save_result.py:195-208` | `VALID_DOMAINS` validation | Same domain label validation |

### 5.2 Frontend removals

| File | What | Replacement |
|------|------|-------------|
| `colors.ts:22-30` | `DOMAIN_COLORS` map | `domainStore.colors` (API-fetched, cached) |
| `Inspector.svelte:9` | `KNOWN_DOMAINS` array | `domainStore.labels` (API-fetched) |

### 5.3 Prompt removals

| File | What | Replacement |
|------|------|-------------|
| `analyze.md:17` | Hardcoded domain list | `{{known_domains}}` template variable |

---

## 6. Service Integration Map

Every service that touches domain data requires modification. This section maps each integration point with the exact change, data flow direction, and error contract.

### 6.1 Pipeline Services — Domain Resolution Path

#### `pipeline.py` — Internal pipeline (lines 303–340, 704–734, 787–788)

**Current flow:** `analysis.domain` → `apply_domain_gate()` → `effective_domain` (string from `VALID_DOMAINS`) → DB persistence + event broadcast.

**New flow:**
```python
# Phase 1.5: Domain resolution (replaces apply_domain_gate)
domain_raw = analysis.domain or "general"
domain_node = await domain_resolver.resolve(db, domain_raw, confidence)
effective_domain = domain_node.label

# Phase 2: Taxonomy mapping (unchanged — already uses domain_raw)
mapping = await taxonomy_engine.map_domain(
    domain_raw=domain_raw, db=db, applied_pattern_ids=applied_pattern_ids,
)

# Persistence: both fields stored
db_opt = Optimization(
    domain=effective_domain,       # Resolved domain node label
    domain_raw=domain_raw,         # Analyzer's original output
    cluster_id=mapping.cluster_id, # Mapped cluster
)
```

**Error contract:** If `domain_resolver.resolve()` fails (DB error, no domain nodes), fall back to `"general"` with WARNING log. Pipeline must never fail due to domain resolution.

#### `sampling_pipeline.py` — Sampling path (lines 632–660, 965–966)

**Identical changes** to `pipeline.py`. Uses `getattr(analysis, "domain", None)` for safe attribute access. Same `domain_resolver.resolve()` call, same fallback.

#### `pipeline_constants.py` — Constant replacements (lines 17–19, 82–94)

**Removed:**
- `VALID_DOMAINS` set
- `apply_domain_gate()` function
- `DOMAIN_CONFIDENCE_GATE` constant

**Added:**
```python
# Domain discovery thresholds
DOMAIN_DISCOVERY_MIN_MEMBERS = 5
DOMAIN_DISCOVERY_MIN_COHERENCE = 0.6
DOMAIN_DISCOVERY_CONSISTENCY = 0.60
DOMAIN_COHERENCE_FLOOR = 0.3
TIER_ACCENTS = ["#00e5ff", "#22ff88", "#fbbf24"]
```

**Impact:** All importers of `VALID_DOMAINS` and `apply_domain_gate` must be updated. Exact list: `pipeline.py`, `sampling_pipeline.py`, `optimize.py`, `save_result.py`.

### 6.2 Heuristic Classification — Signal Loading

#### `heuristic_analyzer.py` — Domain classification (lines 97–177)

**Removed:**
- `_DOMAIN_SIGNALS` module-level dict (5 hardcoded keyword dictionaries)
- `_classify_domain()` function with hardcoded fullstack promotion
- `_precompile_keyword_patterns()` for domain signals

**Replaced with:**
```python
class HeuristicAnalyzer:
    def __init__(self, signal_loader: DomainSignalLoader):
        self._signal_loader = signal_loader

    def _classify_domain(self, scored: dict[str, float]) -> str:
        return self._signal_loader.classify(scored)

    def _score_domains(self, words: set[str]) -> dict[str, float]:
        return self._signal_loader.score(words)
```

**Initialization:** `DomainSignalLoader` loaded at app startup via `lifespan()`, stored on `app.state`. Injected into `HeuristicAnalyzer` via constructor. Hot-reloaded on `domain_created` events.

**Error contract:** If signal loader has no loaded signals (empty DB, startup race), `classify()` returns `"general"`. Never raises.

#### `context_enrichment.py` — Domain value property (line 50–52)

**Unchanged.** `domain_value` property returns `self.analysis.domain` from heuristic analysis. The heuristic analyzer now uses `DomainSignalLoader` internally, so `analysis.domain` can contain any discovered domain label (not just the 7 seed values).

**Downstream consumers of `enrichment.domain_value`:**
- `optimize.py:230–231` (passthrough pending optimization)
- `optimize.py:448` (prepare passthrough response)
- `tools/optimize.py:104–105` (MCP passthrough)
- `tools/prepare.py:111–112` (MCP prepare)

All store the value in `domain` and `domain_raw` — no validation change needed since domain is now resolved by `domain_resolver`, not by whitelist.

### 6.3 Validation Layer — Domain Resolution Service

#### New: `domain_resolver.py` (`backend/app/services/domain_resolver.py`)

Replaces all `VALID_DOMAINS` whitelist checks with domain node lookup.

```python
class DomainResolver:
    """Resolves free-form domain strings to domain node labels.

    Cached in-memory with event-bus invalidation. All callers get
    the same resolver instance via app.state.domain_resolver.
    """

    _cache: dict[str, str] = {}          # primary → domain label
    _domain_labels: set[str] = set()     # known domain node labels

    async def load(self, db: AsyncSession) -> None:
        """Load all domain node labels into cache."""
        result = await db.execute(
            select(PromptCluster.label).where(PromptCluster.state == "domain")
        )
        self._domain_labels = {row[0] for row in result}
        self._cache.clear()
        logger.info("DomainResolver loaded %d domain labels", len(self._domain_labels))

    async def resolve(
        self, db: AsyncSession, domain_raw: str | None, confidence: float
    ) -> str:
        """Resolve a free-form domain string to a known domain label.

        Returns the domain label if the primary matches a domain node.
        Falls back to 'general' if:
        - domain_raw is None/empty
        - confidence < DOMAIN_CONFIDENCE_GATE (0.6)
        - primary doesn't match any domain node
        """
        if not domain_raw or not domain_raw.strip():
            return "general"

        if confidence < DOMAIN_CONFIDENCE_GATE:
            logger.debug(
                "Domain confidence gate: %.2f < %.2f, defaulting to 'general'",
                confidence, DOMAIN_CONFIDENCE_GATE,
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

    def invalidate(self) -> None:
        """Clear cache. Called on domain_created events."""
        self._cache.clear()
        self._domain_labels.clear()
```

**Used by:**
- `pipeline.py` — replaces `apply_domain_gate()` + `VALID_DOMAINS` check
- `sampling_pipeline.py` — same replacement
- `optimize.py:586–591` — passthrough save validation
- `save_result.py:195–208` — MCP save validation

### 6.4 Router Layer — Validation & Response Changes

#### `routers/optimize.py` — Passthrough save (lines 585–591)

**Before:**
```python
from app.services.pipeline_constants import VALID_DOMAINS
domain_primary, _ = parse_domain(body.domain)
validated_domain = domain_primary if domain_primary in VALID_DOMAINS else "general"
```

**After:**
```python
domain_resolver: DomainResolver = request.app.state.domain_resolver
validated_domain = await domain_resolver.resolve(db, body.domain, confidence=1.0)
```

**SSE event emission (lines 630–631):** No change — already emits `domain` and `domain_raw`.

#### `routers/optimize.py` — Optimization detail (line 351)

**No change.** `domain=opt.domain` already returns the stored label.

#### `routers/history.py` — History list (line 123)

**No change.** `domain=opt.domain` serialization unchanged.

#### `routers/clusters.py` — Domain update (lines 251–254)

**Before:** Accepts any string, no validation.

**After:**
```python
if body.domain is not None:
    domain_resolver: DomainResolver = request.app.state.domain_resolver
    if body.domain not in domain_resolver._domain_labels:
        raise HTTPException(422, f"Unknown domain: '{body.domain}'. Use GET /api/domains for valid options.")
    old_domain = cluster.domain
    cluster.domain = body.domain
    logger.info("Cluster domain changed: id=%s '%s' -> '%s'", cluster_id, old_domain, body.domain)
```

#### `routers/clusters.py` — Domain filter (tree, list, stats)

**No change.** Filter queries use `PromptCluster.domain == filter_value` — works for any domain label.

#### `routers/health.py` — Domain count

**Added field:**
```python
domain_count = await db.scalar(
    select(func.count()).where(PromptCluster.state == "domain")
)
```

### 6.5 MCP Tool Layer

#### `tools/save_result.py` — Domain validation (lines 195–208)

**Before:**
```python
from app.services.pipeline_constants import VALID_DOMAINS
domain_primary, _ = parse_domain(domain)
validated_domain = domain_primary if domain_primary in VALID_DOMAINS else "general"
```

**After:**
```python
domain_resolver = get_domain_resolver()  # from tools/_shared.py
validated_domain = await domain_resolver.resolve(db, domain, confidence=1.0)
```

#### `tools/optimize.py` — Domain in output (lines 207–208, 232)

**No change.** Output already includes `domain` from pipeline result.

#### `tools/analyze.py` — Domain from analysis (lines 162, 242)

**No change.** Domain flows from analyzer output, stored as-is in `domain_raw`. The `domain` field gets the resolved value from pipeline.

#### `tools/prepare.py` — Domain from enrichment (lines 111–112)

**No change.** Domain flows from `enrichment.domain_value`.

#### `tools/match.py` — Domain in match

**No change.** Match queries taxonomy engine which already uses embedding-based search.

#### `tools/health.py` — Domain count

**Added:** `domain_count` field in health output, mirroring router change.

#### `tools/_shared.py` — New accessor

**Added:**
```python
_domain_resolver: DomainResolver | None = None

def set_domain_resolver(resolver: DomainResolver) -> None:
    global _domain_resolver
    _domain_resolver = resolver

def get_domain_resolver() -> DomainResolver:
    if _domain_resolver is None:
        raise RuntimeError("DomainResolver not initialized")
    return _domain_resolver
```

### 6.6 Taxonomy Engine — Domain-Aware Operations

#### `engine.py` — Hot path (lines 205–217)

**Before:** `domain=opt.domain_raw or opt.domain or "general"` passed to `assign_cluster()`.

**After:** Same flow. The hot path passes the raw domain to `assign_cluster()` for cross-domain merge prevention. Domain node resolution happens upstream in the pipeline. No change needed in engine.

#### `engine.py` — Warm path: domain discovery addition

**New step** added after HDBSCAN clustering and lifecycle mutations:

```python
async def run_warm_path(self, db: AsyncSession) -> WarmPathResult:
    # ... existing HDBSCAN + lifecycle operations ...

    # Domain discovery: propose new domains from coherent "general" sub-populations
    new_domains = await self._propose_domains(db)
    if new_domains:
        # Reload signal loader for immediate heuristic availability
        signal_loader = get_signal_loader()
        await signal_loader.load(db)
        # Reload domain resolver cache
        domain_resolver = get_domain_resolver()
        await domain_resolver.load(db)
        logger.info("Warm path discovered %d new domains: %s", len(new_domains), new_domains)

    # ... existing quality gate + snapshot ...
```

#### `engine.py` — Cold path color assignment

**Modified:** `assign_colors()` call passes domain state awareness (see Guardrail 4.1).

#### `family_ops.py` — Cross-domain merge prevention (lines 182–194)

**No change.** `parse_domain()` extracts primary, string equality check prevents cross-domain merges. Works for any domain label, not just the seed 7.

#### `family_ops.py` — New cluster creation (lines 245–267)

**No change.** Stores `domain=domain` parameter as-is. Domain is now any valid label from the analyzer or the resolved domain.

#### `lifecycle.py` — Emerge (lines 61–141)

**Before:** Domain defaults to `"general"` (column default).

**After:** Emerge inherits domain from majority of member clusters:
```python
# Determine domain from member cluster majority
member_domains = [c.domain for c in member_clusters if c.domain]
domain_counts = Counter(member_domains)
inherited_domain = domain_counts.most_common(1)[0][0] if domain_counts else "general"

node = PromptCluster(
    label=label,
    domain=inherited_domain,
    # ...
)
```

#### `lifecycle.py` — Split (lines 234–324)

**Before:** Children default to `"general"`.

**After:** Children inherit from parent (already in guardrail 4.5):
```python
child = PromptCluster(
    domain=parent.label if parent.state == "domain" else parent.domain,
    state="candidate",
    # ...
)
```

#### `lifecycle.py` — Merge (lines 144–231)

**Before:** Survivor retains its domain, no check.

**After:** Domain merge guard added (guardrail 4.4). For non-domain nodes, survivor retains domain (no change).

#### `lifecycle.py` — Retire (lines 327–401)

**Before:** No domain check.

**After:** Domain retire exemption (guardrail 4.2).

#### `matching.py` — map_domain (lines 363–472)

**No change.** Already embedding-based. Works for any domain string.

#### `quality.py` — Coherence threshold

**Added:** `coherence_threshold(node)` function returns `DOMAIN_COHERENCE_FLOOR` for domain nodes (guardrail 4.3). All warm path quality gate calls use this function instead of hardcoded threshold.

#### `coloring.py` — Color assignment

**Added:** `compute_max_distance_color()` function. Cold path `assign_colors()` skips domain nodes (guardrail 4.1).

#### `snapshot.py` — Audit trail

**Extended:** Snapshot `operations` JSON log includes domain discovery events:
```json
{
    "type": "domain_discovered",
    "label": "marketing",
    "source_cluster_id": "uuid",
    "member_count": 12,
    "consistency": 0.75,
    "color_hex": "#xx"
}
```

#### `embedding_index.py` — No change

Indexes cluster embeddings by ID. Domain-agnostic.

#### `sparkline.py` — No change

Q_system visualization. Domain-agnostic.

### 6.7 Supporting Services — Indirect Integration

#### `optimization_service.py` — Sort/filter (line 30)

**No change.** `"domain"` is already in `VALID_SORT_COLUMNS`. Sorting/filtering works for any domain label.

#### `feedback_service.py` — Feedback loop

**No change.** Feedback tracks `(task_type, strategy)` affinity, not domain. Domain influence is indirect through cluster membership.

**Future enhancement (not in this spec):** domain-aware strategy affinity for richer adaptation.

#### `adaptation_tracker.py` — Strategy affinity

**No change.** Per `(task_type, strategy)` tracking is domain-agnostic.

#### `heuristic_scorer.py` — Scoring

**No change.** Heuristic scoring is domain-agnostic (clarity, specificity, structure, faithfulness, conciseness).

#### `score_blender.py` — Blending

**No change.** Blends LLM + heuristic scores without domain weighting.

#### `prompt_lifecycle.py` — Cluster curation

**No change.** Curation operates on cluster state, coherence, score. Domain is a cluster attribute that persists through curation.

#### `pattern_injection.py` — Meta-pattern injection

**No change.** `auto_inject_patterns()` uses embedding index search, domain-agnostic.

#### `passthrough.py` — Passthrough assembly (lines 43–92)

**No change.** `analysis_summary` parameter contains domain from heuristic analysis. Injected into `passthrough.md` template. Works for any domain label.

#### `prompt_loader.py` — Template rendering

**New variable:** `{{known_domains}}` added to the render context for `analyze.md`. Value: comma-separated list of domain node labels from `DomainResolver._domain_labels`.

```python
# In pipeline.py, before calling analyzer:
domain_list = ", ".join(sorted(domain_resolver._domain_labels))
# Injected into analyze.md template via PromptLoader.render()
```

#### `workspace_intelligence.py` — No change

Workspace analysis is domain-agnostic.

#### `context_resolver.py` — No change

Per-source character caps and untrusted-context wrapping are domain-agnostic.

#### `event_bus.py` — Event types

**Added event types:** `domain_created`, `domain_merge_proposed`. Registered in event type constants. MCP server's cross-process HTTP notification handles these like existing events.

#### `event_notification.py` — Cross-process

**No change.** `notify_event_bus()` forwards any event type. New domain events flow through the same HTTP POST path.

#### `mcp_session_file.py` — No change

Session file tracks MCP connection state, not domain state.

#### `mcp_proxy.py` — No change

REST→MCP sampling proxy is domain-agnostic.

### 6.8 App Lifecycle — Startup & Initialization

#### `main.py` — FastAPI lifespan

**Added initialization:**
```python
async def lifespan(app: FastAPI):
    # ... existing startup ...

    # Domain services
    domain_resolver = DomainResolver()
    async with get_session() as db:
        await domain_resolver.load(db)
    app.state.domain_resolver = domain_resolver

    signal_loader = DomainSignalLoader()
    async with get_session() as db:
        await signal_loader.load(db)
    app.state.signal_loader = signal_loader

    # Subscribe to domain events for cache invalidation
    event_bus.subscribe("domain_created", _on_domain_created)
    event_bus.subscribe("taxonomy_changed", _on_taxonomy_changed)

    # ... existing startup ...

async def _on_domain_created(event: dict) -> None:
    """Invalidate caches when a new domain is discovered."""
    resolver: DomainResolver = app.state.domain_resolver
    loader: DomainSignalLoader = app.state.signal_loader
    async with get_session() as db:
        await resolver.load(db)
        await loader.load(db)
    logger.info("Domain caches reloaded after domain_created: %s", event.get("label"))
```

#### `mcp_server.py` — MCP lifespan

**Added:** Same `DomainResolver` and `DomainSignalLoader` initialization in the `_process_initialized` guard block. Stored via `tools/_shared.py` accessors (`set_domain_resolver`, `set_signal_loader`).

---

## 7. Logging Strategy

Structured logging for every domain operation. All domain log entries include `domain=` and `trace_id=` (where available) for grep-based debugging.

### 7.1 Log levels by operation

| Operation | Level | Message pattern | When |
|-----------|-------|----------------|------|
| Domain resolved | `DEBUG` | `"Domain resolved: raw='%s' → label='%s' trace_id=%s"` | Every pipeline run |
| Domain confidence gate | `DEBUG` | `"Domain confidence gate: %.2f < %.2f, defaulting to 'general'"` | Confidence below threshold |
| Domain mapped to cluster | `INFO` | `"Domain mapped: '%s' → node '%s' (%s) trace_id=%s"` | Successful taxonomy mapping |
| Domain mapping failed | `WARNING` | `"Domain mapping failed: '%s' — no matching cluster, trace_id=%s"` | No cluster found above threshold |
| Cross-domain merge prevented | `INFO` | `"Cross-domain merge prevented: cluster '%s' domain=%s != incoming domain=%s (cosine=%.3f)"` | `assign_cluster()` domain check |
| Domain discovery proposed | `INFO` | `"Domain proposed: '%s' (members=%d, coherence=%.3f, consistency=%.1f%%)"` | Warm path discovery |
| Domain node created | `INFO` | `"Domain created: label='%s', color='%s', source='discovered', snapshot=%s"` | Successful domain creation |
| Domain discovery skipped | `DEBUG` | `"Domain discovery skipped for cluster %s: %s"` | Threshold not met (with reason) |
| Domain re-parenting | `INFO` | `"Re-parented %d clusters from 'general' to '%s'"` | Post-creation re-parenting |
| Domain backfill | `INFO` | `"Backfilled %d optimizations from 'general' to '%s'"` | Post-creation optimization update |
| Domain merge proposed | `INFO` | `"Domain merge proposed (requires approval): %s ← %s (cosine=%.3f)"` | Warm path detects merge candidate |
| Domain retire skipped | `INFO` | `"Skipping retire for domain node: %s"` | Guardrail prevents retire |
| Signal loader reload | `INFO` | `"DomainSignalLoader loaded %d domains with %d total keywords"` | Startup and hot-reload |
| Signal loader empty | `WARNING` | `"DomainSignalLoader: no domain signals loaded — classifier will default to 'general'"` | Empty DB or startup race |
| Domain resolver reload | `INFO` | `"DomainResolver loaded %d domain labels"` | Startup and hot-reload |
| Domain color assigned | `DEBUG` | `"Domain color computed: '%s' → %s (min_distance=%.4f)"` | OKLab max-distance calculation |
| Domain keyword extraction | `DEBUG` | `"Extracted %d TF-IDF keywords for domain '%s' from %d prompts"` | Discovery keyword extraction |
| Domain validation error | `WARNING` | `"Unknown domain '%s' in cluster update, rejecting"` | PATCH endpoint validation |
| Domain promotion | `INFO` | `"Cluster %s promoted to domain: label='%s'"` | Manual promotion via API |

### 7.2 Trace logger integration

Domain discovery events are recorded in `trace_logger.py` JSONL traces:

```json
{
    "phase": "domain_discovery",
    "timestamp": "2026-03-28T14:30:00Z",
    "domain_label": "marketing",
    "source_cluster_id": "uuid",
    "member_count": 12,
    "coherence": 0.78,
    "consistency": 0.75,
    "color_hex": "#ab45cd",
    "keywords_extracted": 15,
    "clusters_reparented": 3,
    "optimizations_backfilled": 28,
    "snapshot_id": "uuid"
}
```

---

## 8. Error Handling

### 8.1 Error taxonomy

Every domain-related failure is classified into one of three categories:

| Category | Behavior | Example |
|----------|----------|---------|
| **Recoverable** | Log WARNING, use fallback, continue | Unknown domain → "general" |
| **Degraded** | Log ERROR, skip operation, continue pipeline | Domain discovery DB error → skip discovery, pipeline continues |
| **Fatal** | Log CRITICAL, raise, fail startup | No domain nodes in DB after migration → startup fails |

### 8.2 Error handling by component

#### DomainResolver

```python
async def resolve(self, db: AsyncSession, domain_raw: str | None, confidence: float) -> str:
    try:
        primary, _ = parse_domain(domain_raw)
        if primary in self._domain_labels:
            return primary
        return "general"
    except Exception:
        logger.warning("DomainResolver.resolve() failed for '%s', defaulting to 'general'", domain_raw, exc_info=True)
        return "general"  # RECOVERABLE: never block pipeline
```

#### DomainSignalLoader

```python
async def load(self, db: AsyncSession) -> None:
    try:
        domains = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        # ... load signals ...
    except Exception:
        logger.error("DomainSignalLoader.load() failed — classifier will use empty signals", exc_info=True)
        self._signals = {}  # DEGRADED: classifier defaults to "general"
        # Do NOT raise — app can function without domain signals
```

#### Domain discovery (warm path)

```python
async def _propose_domains(self, db: AsyncSession) -> list[str]:
    created = []
    try:
        general_node = await self._get_domain_node(db, "general")
        if general_node is None:
            logger.error("Domain discovery: 'general' domain node not found — skipping")
            return []  # DEGRADED: skip discovery

        candidates = await self._find_discovery_candidates(db, general_node.id)
        for cluster in candidates:
            try:
                label = await self._evaluate_candidate(db, cluster)
                if label:
                    await self._create_domain_node(db, label, cluster)
                    created.append(label)
            except Exception:
                logger.error(
                    "Domain discovery failed for cluster %s — skipping",
                    cluster.id, exc_info=True,
                )
                # DEGRADED: skip this candidate, continue with others
                continue

    except Exception:
        logger.error("Domain discovery failed entirely — skipping", exc_info=True)
        # DEGRADED: warm path continues without domain discovery

    return created
```

#### OKLab color computation

```python
def compute_max_distance_color(existing_hex: list[str]) -> str:
    try:
        # ... OKLab computation ...
        return oklab_to_hex(best_color)
    except Exception:
        logger.warning("OKLab color computation failed, using fallback gray", exc_info=True)
        return "#7a7a9e"  # RECOVERABLE: general's gray as safe default
```

#### TF-IDF keyword extraction

```python
async def _extract_domain_keywords(self, db: AsyncSession, cluster: PromptCluster, top_k: int = 15) -> list:
    try:
        # ... TF-IDF extraction ...
        return ranked
    except Exception:
        logger.warning(
            "Keyword extraction failed for cluster %s — domain will have no heuristic signals",
            cluster.id, exc_info=True,
        )
        return []  # RECOVERABLE: domain created without signals, embedding-based classification still works
```

#### Re-parenting and backfill

```python
async def _reparent_to_domain(self, db: AsyncSession, domain_node: PromptCluster, label: str) -> int:
    try:
        count = await self._execute_reparent(db, domain_node, label)
        logger.info("Re-parented %d clusters from 'general' to '%s'", count, label)
        return count
    except Exception:
        logger.error(
            "Re-parenting failed for domain '%s' — clusters remain under 'general'",
            label, exc_info=True,
        )
        # DEGRADED: domain node exists but children not yet moved
        # Next warm path cycle will detect and retry
        return 0
```

#### Migration

```python
def upgrade():
    # Check idempotency
    existing = op.get_bind().execute(
        text("SELECT COUNT(*) FROM prompt_cluster WHERE state = 'domain'")
    ).scalar()
    if existing >= 7:
        logger.info("Migration: domain nodes already exist (%d), skipping seed", existing)
        return

    # ... insert seed domains ...

    # Validate post-migration
    count = op.get_bind().execute(
        text("SELECT COUNT(*) FROM prompt_cluster WHERE state = 'domain'")
    ).scalar()
    if count < 7:
        raise RuntimeError(f"Migration validation failed: expected 7 domain nodes, found {count}")
        # FATAL: migration must succeed for app to function
```

#### App startup validation

```python
async def _validate_domain_nodes(db: AsyncSession) -> None:
    """Verify domain nodes exist after migration. Called in lifespan."""
    count = await db.scalar(
        select(func.count()).where(PromptCluster.state == "domain")
    )
    if count == 0:
        logger.critical("No domain nodes found — run migration first")
        raise RuntimeError("Domain nodes missing. Run: alembic upgrade head")
    logger.info("Domain validation passed: %d domain nodes", count)
```

### 8.3 Error propagation rules

1. **Domain resolution never raises.** Any error in `DomainResolver.resolve()` returns `"general"`. The pipeline must never fail because a domain couldn't be classified.

2. **Domain discovery errors are isolated.** Each candidate is processed independently. One failed candidate doesn't block others. Total discovery failure doesn't block the warm path.

3. **Signal loader errors degrade gracefully.** If signals can't be loaded, the heuristic classifier returns `"general"` for all domains. The LLM analyzer and embedding-based taxonomy still function.

4. **Re-parenting errors are self-healing.** If re-parenting fails after domain creation, the domain node exists but has no children. The next warm path cycle detects orphaned clusters under "general" that should be under the new domain and retries.

5. **Color computation errors use safe default.** If OKLab computation fails, the domain gets `"general"`'s gray (`#7a7a9e`). Functional but visually suboptimal — fixed on next cold path or manual recolor.

6. **Frontend handles missing domains gracefully.** If `/api/domains` returns empty (API error, loading race), `domainStore.colorFor()` returns fallback gray. Inspector picker shows empty state with retry button. Topology renders nodes with their `color_hex` directly (API-supplied, not store-dependent).

---

## 8B. Strategic Risk Detection & Self-Correction

Section 8 covers operational failures (DB errors, computation failures, startup races). This section covers the five strategic risks identified in [ADR-004](../../adr/ADR-004-unified-domain-taxonomy.md) — systemic problems that develop over time and require active monitoring, alerting, and automated or guided correction.

### Risk 1: Domain Proliferation

**What:** Too many domains created, cluttering navigation and diluting domain identity.

**Detection — health endpoint metric + warm path guard:**

```python
# In engine.py — warm path, before _propose_domains()
DOMAIN_COUNT_CEILING = 30  # pipeline_constants.py

async def _check_domain_ceiling(self, db: AsyncSession) -> bool:
    """Gate domain discovery when ceiling is reached."""
    count = await db.scalar(
        select(func.count()).where(PromptCluster.state == "domain")
    )
    if count >= DOMAIN_COUNT_CEILING:
        logger.warning(
            "Domain ceiling reached (%d/%d) — skipping domain discovery. "
            "Consider archiving low-usage domains or raising the ceiling.",
            count, DOMAIN_COUNT_CEILING,
        )
        return False
    return True
```

**Logging:**
```
WARNING  "Domain ceiling reached (30/30) — skipping domain discovery"
INFO     "Domain count: %d/%d (%.0f%% of ceiling)" — logged every warm path cycle
```

**Health endpoint exposure:**
```python
# In GET /api/health response
"domain_count": 18,
"domain_ceiling": 30,
"domain_utilization": 0.6,  # count / ceiling
```

**Frontend alerting:** When `domain_utilization >= 0.8`, StatusBar shows amber domain count badge. Toast on first breach: "Domain ceiling approaching — consider archiving unused domains."

**Self-correction — usage-based archival suggestions:**

```python
async def _suggest_domain_archival(self, db: AsyncSession) -> list[str]:
    """Identify low-activity domains for potential archival.

    A domain is suggested for archival when:
    - member_count == 0 (no child clusters)
    - OR last_used_at is >90 days ago AND usage_count < 3
    - AND source == "discovered" (seed domains are never suggested)
    """
    stale = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            or_(
                PromptCluster.member_count == 0,
                and_(
                    PromptCluster.last_used_at < utcnow() - timedelta(days=90),
                    PromptCluster.usage_count < 3,
                ),
            ),
        )
    )
    suggestions = []
    for domain in stale.scalars():
        meta = domain.metadata or {}
        if meta.get("source") == "seed":
            continue  # Never suggest archiving seed domains
        suggestions.append(domain.label)
        logger.info(
            "Domain archival suggested: '%s' (members=%d, usage=%d, last_used=%s)",
            domain.label, domain.member_count, domain.usage_count, domain.last_used_at,
        )
    return suggestions
```

**Event:** `domain_archival_suggested` with `{labels: [...]}` payload. Frontend shows actionable toast with archive buttons per domain.

### Risk 2: Stale Learned Signals

**What:** TF-IDF keywords extracted at domain creation become outdated as the domain's membership evolves.

**Detection — signal staleness tracking:**

```python
# In domain node metadata
{
    "signal_keywords": [...],
    "signal_generated_at": "2026-03-28T14:30:00Z",
    "signal_member_count_at_generation": 12,  # snapshot of member_count when signals were last computed
}
```

**Warm path staleness check:**

```python
SIGNAL_REFRESH_MEMBER_RATIO = 2.0  # Refresh when member_count doubles since last generation

async def _check_signal_staleness(self, db: AsyncSession) -> list[PromptCluster]:
    """Identify domains whose signals need regeneration."""
    stale = []
    domains = await db.execute(
        select(PromptCluster).where(PromptCluster.state == "domain")
    )
    for domain in domains.scalars():
        meta = domain.metadata or {}
        if meta.get("source") == "seed":
            continue  # Seed signals are curated, not auto-refreshed

        gen_count = meta.get("signal_member_count_at_generation", 0)
        if gen_count == 0:
            continue  # No signals to refresh

        if domain.member_count >= gen_count * SIGNAL_REFRESH_MEMBER_RATIO:
            stale.append(domain)
            logger.info(
                "Signal staleness detected: domain '%s' generated at %d members, now has %d",
                domain.label, gen_count, domain.member_count,
            )
    return stale
```

**Self-correction — automatic signal regeneration:**

```python
async def _refresh_domain_signals(self, db: AsyncSession, domain: PromptCluster) -> None:
    """Regenerate TF-IDF keywords for a domain with stale signals."""
    keywords = await self._extract_domain_keywords(db, domain)
    meta = dict(domain.metadata or {})
    meta["signal_keywords"] = keywords
    meta["signal_generated_at"] = utcnow().isoformat()
    meta["signal_member_count_at_generation"] = domain.member_count
    domain.metadata = meta

    logger.info(
        "Signals refreshed for domain '%s': %d keywords from %d members",
        domain.label, len(keywords), domain.member_count,
    )
```

Called in warm path after domain discovery, before signal loader reload. Ensures heuristic classifier always has current signals.

**Logging:**
```
INFO   "Signal staleness detected: domain '%s' generated at %d members, now has %d"
INFO   "Signals refreshed for domain '%s': %d keywords from %d members"
DEBUG  "Signal staleness check: %d domains checked, %d stale"
```

### Risk 3: Guardrail Bypass

**What:** Future code changes accidentally skip `state="domain"` checks in lifecycle operations, breaking domain stability (color drift, unintended retirement, auto-merge).

**Detection — runtime assertions in lifecycle operations:**

```python
# In lifecycle.py — added to every lifecycle mutation function

def _assert_domain_guardrails(operation: str, node: PromptCluster) -> None:
    """Runtime assertion that domain guardrails are enforced.

    Called at the START of every lifecycle mutation. Raises AssertionError
    in debug mode, logs CRITICAL in production. This is a safety net —
    the caller should have already checked, but this catches regressions.
    """
    if node.state != "domain":
        return

    violations = {
        "retire": "Domain nodes cannot be retired — use manual archival",
        "merge": "Domain nodes cannot be auto-merged — requires approval event",
        "color_assign": "Domain colors are pinned — cold path must skip",
    }
    if operation in violations:
        msg = f"GUARDRAIL VIOLATION: {operation} attempted on domain node '{node.label}'. {violations[operation]}"
        logger.critical(msg)
        raise GuardrailViolationError(msg)
```

```python
class GuardrailViolationError(RuntimeError):
    """Raised when a lifecycle operation violates domain stability guardrails.

    This exception should never occur in production — it indicates a code
    regression that bypassed the guardrail checks.
    """
    pass
```

**Placement in lifecycle.py:**

```python
async def attempt_retire(db, node, warm_path_age) -> bool:
    _assert_domain_guardrails("retire", node)  # Line 1 of function body
    # ... existing logic ...

async def attempt_merge(db, node_a, node_b, warm_path_age) -> PromptCluster | None:
    _assert_domain_guardrails("merge", node_a)  # Both nodes checked
    _assert_domain_guardrails("merge", node_b)
    # ... existing logic ...
```

**Placement in coloring.py:**

```python
async def assign_colors(nodes: list[PromptCluster]) -> None:
    for node in nodes:
        _assert_domain_guardrails("color_assign", node)  # Catches if skip was removed
        if node.state == "domain":
            continue
        # ... existing logic ...
```

**Test coverage requirement:**

```python
# In test_lifecycle.py — one test per guardrail
def test_retire_domain_raises_guardrail_violation():
    domain_node = make_cluster(state="domain", label="backend")
    with pytest.raises(GuardrailViolationError, match="retire"):
        await attempt_retire(db, domain_node, warm_path_age=1)

def test_merge_domain_raises_guardrail_violation():
    domain_a = make_cluster(state="domain", label="backend")
    domain_b = make_cluster(state="domain", label="frontend")
    with pytest.raises(GuardrailViolationError, match="merge"):
        await attempt_merge(db, domain_a, domain_b, warm_path_age=1)

def test_cold_path_skips_domain_colors():
    domain_node = make_cluster(state="domain", label="backend", color_hex="#b44aff")
    await assign_colors([domain_node])
    assert domain_node.color_hex == "#b44aff"  # Unchanged
```

**Logging:**
```
CRITICAL "GUARDRAIL VIOLATION: retire attempted on domain node 'backend'"
```

This is a circuit-breaker — the warm path catches `GuardrailViolationError` and halts the current lifecycle cycle (not the entire warm path), logging the violation for investigation. The domain node is unmodified.

### Risk 4: "General" Never Shrinks

**What:** Discovery thresholds are too conservative, so "general" accumulates diverse prompts without ever spawning new domains.

**Detection — warm path monitoring metrics:**

```python
async def _monitor_general_health(self, db: AsyncSession) -> None:
    """Log diagnostic metrics for the 'general' domain after each warm path."""
    general = await self._get_domain_node(db, "general")
    if not general:
        return

    # Count child clusters
    child_count = await db.scalar(
        select(func.count()).where(
            PromptCluster.parent_id == general.id,
            PromptCluster.state.in_(["active", "mature"]),
        )
    )

    # Count children that ALMOST meet discovery thresholds
    near_threshold = await db.scalar(
        select(func.count()).where(
            PromptCluster.parent_id == general.id,
            PromptCluster.state.in_(["active", "mature"]),
            PromptCluster.member_count >= DOMAIN_DISCOVERY_MIN_MEMBERS - 2,  # within 2 of threshold
            PromptCluster.coherence >= DOMAIN_DISCOVERY_MIN_COHERENCE - 0.1,  # within 0.1
        )
    )

    # Count total optimizations under general
    opt_count = await db.scalar(
        select(func.count()).where(Optimization.domain == "general")
    )

    logger.info(
        "General domain health: %d child clusters, %d near discovery threshold, "
        "%d total optimizations, member_count=%d",
        child_count, near_threshold, opt_count, general.member_count,
    )

    # Alert if general is accumulating without discovery
    if opt_count > 50 and child_count > 5 and near_threshold == 0:
        logger.warning(
            "General domain stagnation: %d optimizations across %d clusters "
            "but none near discovery threshold. Consider lowering "
            "DOMAIN_DISCOVERY_MIN_MEMBERS (current=%d) or "
            "DOMAIN_DISCOVERY_MIN_COHERENCE (current=%.2f).",
            opt_count, child_count,
            DOMAIN_DISCOVERY_MIN_MEMBERS, DOMAIN_DISCOVERY_MIN_COHERENCE,
        )
```

**Health endpoint exposure:**
```python
# In GET /api/health response
"general_domain": {
    "child_clusters": 12,
    "near_threshold": 3,
    "total_optimizations": 87,
},
```

**Logging:**
```
INFO     "General domain health: %d child clusters, %d near discovery threshold, %d total optimizations"
WARNING  "General domain stagnation: %d optimizations across %d clusters but none near discovery threshold"
```

**Self-correction:** The warning message includes the current threshold values and a suggestion to lower them. This is a human-in-the-loop correction — the thresholds are configurable constants in `pipeline_constants.py` and can be adjusted without code changes (environment variable override or config file).

### Risk 5: Migration Data Corruption

**What:** The migration creates domain nodes, re-parents clusters, and backfills optimizations. A failure partway through can leave the tree in an inconsistent state.

**Detection — post-migration integrity check:**

```python
async def verify_domain_tree_integrity(db: AsyncSession) -> list[str]:
    """Post-migration (and periodic warm-path) integrity check.

    Returns a list of violation descriptions. Empty list = healthy.
    """
    violations = []

    # 1. Every domain node must have state="domain" and a unique label
    domain_labels = await db.execute(
        select(PromptCluster.label, func.count()).where(
            PromptCluster.state == "domain"
        ).group_by(PromptCluster.label)
    )
    for label, count in domain_labels:
        if count > 1:
            violations.append(f"Duplicate domain label: '{label}' appears {count} times")

    # 2. Every non-domain cluster with parent_id must point to an existing node
    orphans = await db.execute(text("""
        SELECT c.id, c.label, c.parent_id
        FROM prompt_cluster c
        LEFT JOIN prompt_cluster p ON c.parent_id = p.id
        WHERE c.parent_id IS NOT NULL AND p.id IS NULL
    """))
    for row in orphans:
        violations.append(f"Orphan cluster: '{row.label}' (id={row.id}) references missing parent {row.parent_id}")

    # 3. Every non-domain cluster's domain field must match a domain node label
    mismatched = await db.execute(text("""
        SELECT c.id, c.label, c.domain
        FROM prompt_cluster c
        WHERE c.state != 'domain'
          AND c.domain NOT IN (SELECT label FROM prompt_cluster WHERE state = 'domain')
    """))
    for row in mismatched:
        violations.append(f"Domain mismatch: cluster '{row.label}' has domain='{row.domain}' which is not a domain node")

    # 4. No circular parent references
    # (lightweight check: no node is its own parent)
    self_refs = await db.execute(text("""
        SELECT id, label FROM prompt_cluster WHERE parent_id = id
    """))
    for row in self_refs:
        violations.append(f"Self-referencing parent: '{row.label}' (id={row.id})")

    # 5. Domain nodes must have persistence=1.0
    weak_domains = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            or_(PromptCluster.persistence < 1.0, PromptCluster.persistence.is_(None)),
        )
    )
    for d in weak_domains.scalars():
        violations.append(f"Domain node '{d.label}' has persistence={d.persistence} (expected 1.0)")

    if violations:
        for v in violations:
            logger.error("Tree integrity violation: %s", v)
    else:
        logger.info("Domain tree integrity check passed")

    return violations
```

**When it runs:**
1. **Post-migration** — called at the end of the Alembic `upgrade()`. If violations found, migration raises `RuntimeError` and rolls back.
2. **App startup** — called in `lifespan()` after `DomainResolver.load()`. Violations logged at ERROR level but do not block startup (self-healing may fix them).
3. **Warm path** — called after domain discovery and re-parenting. Violations logged and included in `TaxonomySnapshot.operations` for audit trail.

**Self-correction — warm path auto-repair:**

```python
async def _repair_tree_violations(self, db: AsyncSession, violations: list[str]) -> int:
    """Attempt to repair detected tree integrity violations.

    Returns count of repairs made. Logs each repair.
    """
    repaired = 0

    # Repair orphaned clusters: re-parent under "general"
    general = await self._get_domain_node(db, "general")
    if general:
        orphan_result = await db.execute(text("""
            UPDATE prompt_cluster
            SET parent_id = :general_id, domain = 'general'
            WHERE parent_id IS NOT NULL
              AND parent_id NOT IN (SELECT id FROM prompt_cluster)
              AND state != 'domain'
        """), {"general_id": general.id})
        if orphan_result.rowcount > 0:
            logger.info("Auto-repaired %d orphaned clusters → 'general'", orphan_result.rowcount)
            repaired += orphan_result.rowcount

    # Repair domain mismatch: set domain to parent's domain (or "general")
    mismatch_result = await db.execute(text("""
        UPDATE prompt_cluster c
        SET domain = COALESCE(
            (SELECT p.domain FROM prompt_cluster p WHERE p.id = c.parent_id),
            'general'
        )
        WHERE c.state != 'domain'
          AND c.domain NOT IN (SELECT label FROM prompt_cluster WHERE state = 'domain')
    """))
    if mismatch_result.rowcount > 0:
        logger.info("Auto-repaired %d domain mismatches", mismatch_result.rowcount)
        repaired += mismatch_result.rowcount

    # Repair weak domain persistence
    weak_result = await db.execute(
        update(PromptCluster)
        .where(PromptCluster.state == "domain", PromptCluster.persistence < 1.0)
        .values(persistence=1.0)
    )
    if weak_result.rowcount > 0:
        logger.info("Auto-repaired %d domain nodes with weak persistence", weak_result.rowcount)
        repaired += weak_result.rowcount

    return repaired
```

**Logging:**
```
ERROR    "Tree integrity violation: %s"  (one per violation)
INFO     "Domain tree integrity check passed"
INFO     "Auto-repaired %d orphaned clusters → 'general'"
INFO     "Auto-repaired %d domain mismatches"
INFO     "Auto-repaired %d domain nodes with weak persistence"
WARNING  "Tree integrity: %d violations detected, %d auto-repaired, %d remaining"
```

**Snapshot audit trail:** Integrity check results and repairs are recorded in `TaxonomySnapshot.operations`:
```json
{
    "type": "integrity_check",
    "violations_found": 3,
    "violations_repaired": 2,
    "violations_remaining": ["Duplicate domain label: 'marketing' appears 2 times"],
    "timestamp": "2026-03-28T15:00:00Z"
}
```

---

## 9. New Services

### 9.1 DomainSignalLoader (`backend/app/services/domain_signal_loader.py`)

Replaces `_DOMAIN_SIGNALS` in `heuristic_analyzer.py`.

```python
class DomainSignalLoader:
    """Loads domain classification signals from domain node metadata."""

    _signals: dict[str, list[tuple[str, float]]] = {}
    _patterns: dict[str, re.Pattern] = {}

    async def load(self, db: AsyncSession) -> None:
        """Load signals from all active domain nodes."""
        domains = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        self._signals = {}
        for domain in domains.scalars():
            if domain.metadata and domain.metadata.get("signal_keywords"):
                self._signals[domain.label] = [
                    (kw, weight) for kw, weight in domain.metadata["signal_keywords"]
                ]
        self._precompile_patterns()

    def classify(self, scored: dict[str, float]) -> str:
        """Classify domain from keyword scores. Same algorithm as current _classify_domain()."""
        # ... identical logic, but uses self._signals instead of module-level dict
```

Loaded at startup. Hot-reloaded on `domain_created` and `taxonomy_changed` events via event bus subscription.

### 9.2 DomainResolver (`backend/app/services/domain_resolver.py`)

Full specification in Section 6.3 above.

### 9.3 Domain color service (extension of `taxonomy/coloring.py`)

`compute_max_distance_color()` added to `coloring.py`. Reuses existing OKLab conversion utilities.

---

## 10. API Changes

### 10.1 New endpoint: `GET /api/domains`

```python
@router.get("/api/domains")
async def list_domains(db: AsyncSession = Depends(get_db)) -> list[DomainInfo]:
    """List all active domain nodes with colors and metadata."""
    domains = await db.execute(
        select(PromptCluster)
        .where(PromptCluster.state == "domain")
        .order_by(PromptCluster.label)
    )
    return [
        DomainInfo(
            id=d.id,
            label=d.label,
            color_hex=d.color_hex,
            member_count=d.member_count,
            avg_score=d.avg_score,
            source=d.metadata.get("source", "seed") if d.metadata else "seed",
        )
        for d in domains.scalars()
    ]
```

### 10.2 New schema: `DomainInfo`

```python
class DomainInfo(BaseModel):
    id: str
    label: str
    color_hex: str
    member_count: int = 0
    avg_score: float | None = None
    source: str = "seed"  # seed | discovered
```

### 10.3 New endpoint: `POST /api/domains/{id}/promote`

Promotes a mature cluster to domain status. Validates:
- Source cluster must be `state="mature"` or `state="active"` with `member_count >= 5`
- No existing domain node with the same label
- Assigns OKLab max-distance color

### 10.4 Modified: `PATCH /api/clusters/{id}`

Accepts `state="domain"` only with explicit validation:
- Caller must confirm intent (e.g., `"confirm_domain_promotion": true` in body)
- Target cluster must meet minimum thresholds (member_count, coherence)

### 10.5 Modified: `GET /api/health`

Adds `domain_count: int` to health response.

### 10.6 Modified: `GET /api/clusters/tree`

Domain nodes appear as root-level entries with their child clusters nested. No schema change — `ClusterNode` already has `parent_id` and `state`.

---

## 11. Frontend Architecture

### 11.1 Domain store (`frontend/src/lib/stores/domains.svelte.ts`)

New reactive store that is the single source of truth for domain data:

```typescript
interface DomainEntry {
  id: string;
  label: string;
  color_hex: string;
  member_count: number;
  avg_score: number | null;
  source: 'seed' | 'discovered';
}

// State
let domains = $state<DomainEntry[]>([]);
let loaded = $state(false);

// Derived
let colors = $derived(
  Object.fromEntries(domains.map(d => [d.label, d.color_hex]))
);
let labels = $derived(domains.map(d => d.label));

// Actions
async function fetchDomains(): Promise<void> { ... }
function colorFor(domain: string): string { ... }
```

Initialized in app startup. Refreshed on `domain_created` and `taxonomy_changed` SSE events.

### 11.2 Color resolution (`colors.ts`)

`taxonomyColor()` rewritten to resolve from domain store:

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

No hardcoded `DOMAIN_COLORS` map.

### 11.3 Inspector domain picker

```svelte
{#each domainStore.labels as d (d)}
  <button
    class="domain-option"
    class:domain-option--active={d === parsePrimaryDomain(family.domain)}
    style="background: {domainStore.colorFor(d)};"
    onclick={() => selectDomain(d)}
    disabled={domainSaving}
  >{d}</button>
{/each}
```

No hardcoded `KNOWN_DOMAINS` array.

### 11.4 Topology rendering

Domain nodes render as larger spheres (2x cluster radius) with `persistence=1.0` ensuring they're always visible regardless of LOD tier. Their pinned `color_hex` is used directly.

---

## 12. Migration Plan

### 12.1 Alembic migration: `add_domain_nodes`

**Step 1:** Add `metadata` column to `prompt_cluster`:
```python
op.add_column('prompt_cluster', sa.Column('metadata', sa.JSON, nullable=True))
```

**Step 2:** Add index:
```python
op.create_index('ix_prompt_cluster_state_label', 'prompt_cluster', ['state', 'label'])
```

**Step 3:** Insert 7 seed domain nodes with pre-computed centroid embeddings, colors, and keyword metadata.

**Step 4:** Re-parent existing clusters under matching domain nodes:
```sql
UPDATE prompt_cluster
SET parent_id = (SELECT id FROM prompt_cluster AS d WHERE d.state = 'domain' AND d.label = prompt_cluster.domain)
WHERE state != 'domain' AND parent_id IS NULL
```

**Step 5:** Backfill `Optimization.domain` for `domain_raw` values resolvable to new domains:
```python
# For each domain node, find optimizations with matching domain_raw primary
for domain in seed_domains:
    await db.execute(
        update(Optimization)
        .where(
            Optimization.domain == "general",
            func.substr(Optimization.domain_raw, 1, func.instr(Optimization.domain_raw, ':') - 1) == domain.label
        )
        .values(domain=domain.label)
    )
```

### 12.2 Migration safety

- **Idempotent:** Checks for existing domain nodes before inserting. Safe to re-run.
- **Reversible:** Downgrade drops domain nodes, nullifies re-parented `parent_id`, restores `domain="general"` on backfilled rows.
- **Tested:** Integration test creates sample data, runs migration, validates tree structure and domain assignments.

---

## 13. Configuration Constants

All in `pipeline_constants.py` (replacing `VALID_DOMAINS`):

```python
# Domain discovery thresholds
DOMAIN_DISCOVERY_MIN_MEMBERS = 5
DOMAIN_DISCOVERY_MIN_COHERENCE = 0.6
DOMAIN_DISCOVERY_CONSISTENCY = 0.60  # 60% of members share the same domain_raw primary

# Domain quality
DOMAIN_COHERENCE_FLOOR = 0.3
DOMAIN_CONFIDENCE_GATE = 0.6  # Retained from current system — override domain to "general" below this

# Domain proliferation ceiling (Risk 1)
DOMAIN_COUNT_CEILING = 30

# Signal staleness ratio (Risk 2) — refresh when member_count doubles since last generation
SIGNAL_REFRESH_MEMBER_RATIO = 2.0

# Domain archival suggestion thresholds (Risk 1 self-correction)
DOMAIN_ARCHIVAL_IDLE_DAYS = 90
DOMAIN_ARCHIVAL_MIN_USAGE = 3

# Color constraints
TIER_ACCENTS = ["#00e5ff", "#22ff88", "#fbbf24"]  # internal, sampling, passthrough — avoid proximity
```

---

## 14. Event Bus Integration

New SSE event types:

| Event | Payload | Trigger |
|-------|---------|---------|
| `domain_created` | `{label, color_hex, source}` | Warm path creates a new domain node |
| `domain_merge_proposed` | `{survivor, loser, similarity}` | Warm path detects two domains should merge |
| `domain_archival_suggested` | `{labels: [...]}` | Warm path identifies low-usage discovered domains |
| `domain_signals_refreshed` | `{label, keyword_count}` | Warm path regenerates stale TF-IDF signals |
| `domain_ceiling_reached` | `{count, ceiling}` | Domain count hits `DOMAIN_COUNT_CEILING` |

Frontend handlers:
- `domain_created` → refresh domain store, show toast ("New domain discovered: {label}"), invalidate topology
- `domain_merge_proposed` → show actionable toast with approve/reject buttons
- `domain_archival_suggested` → show toast with archive buttons per domain
- `domain_signals_refreshed` → silent refresh of domain store (no toast — internal maintenance)
- `domain_ceiling_reached` → amber badge on StatusBar domain count, toast on first occurrence

---

## 15. Testing Strategy

### Unit tests

**Core services:**
- `DomainResolver`: resolve known domain, resolve unknown → "general", confidence gate, cache invalidation, empty DB
- `DomainSignalLoader`: load from domain metadata, classify with dynamic signals, hot-reload on event, empty signals → "general"
- `compute_max_distance_color()`: produces valid hex, avoids tier accents, maximizes distance, handles empty input
- `_propose_domains()`: discovers domains when thresholds met, skips when not, handles edge cases (empty clusters, conflicting primaries)

**Guardrails (Section 4):**
- `test_retire_domain_raises_guardrail_violation`: retire on `state="domain"` raises `GuardrailViolationError`
- `test_merge_two_domains_raises_guardrail_violation`: merge with either node as domain raises
- `test_cold_path_skips_domain_colors`: domain `color_hex` unchanged after `assign_colors()`
- `test_split_domain_creates_candidate_children`: split children have `state="candidate"`, inherit parent domain label
- `test_domain_coherence_floor_applied`: quality threshold uses 0.3 for domain nodes, 0.6 for clusters

**Risk detection (Section 8B):**
- `test_domain_ceiling_blocks_discovery`: with 30 domain nodes, `_propose_domains()` returns empty and logs warning
- `test_signal_staleness_detected`: domain with `signal_member_count_at_generation=5` and `member_count=10` flagged as stale
- `test_signal_refresh_updates_metadata`: `_refresh_domain_signals()` updates keywords, timestamp, and member count snapshot
- `test_general_stagnation_warning`: 50+ optimizations under general, 5+ clusters, 0 near threshold → warning logged
- `test_tree_integrity_detects_orphans`: cluster with non-existent `parent_id` → violation reported
- `test_tree_integrity_detects_domain_mismatch`: cluster with `domain="marketing"` but no "marketing" domain node → violation
- `test_tree_integrity_detects_duplicate_domain_labels`: two domain nodes with same label → violation
- `test_tree_integrity_detects_weak_persistence`: domain node with `persistence=0.5` → violation
- `test_auto_repair_orphans`: orphaned clusters re-parented to "general"
- `test_auto_repair_domain_mismatch`: mismatched domains corrected to parent's domain
- `test_archival_suggestion_skips_seeds`: seed domains never suggested for archival regardless of usage

### Integration tests
- Full warm path cycle with domain discovery: seed data → HDBSCAN → domain proposal → re-parent → backfill → signal extraction → event emission → resolver/loader reload
- Domain ceiling enforcement: create 30 domains, verify discovery blocked, verify event emitted
- Signal lifecycle: create domain → accumulate members → trigger staleness → verify refresh → verify classifier uses new signals
- Migration: create sample clusters + optimizations, run migration, validate tree structure via `verify_domain_tree_integrity()`
- API: `GET /api/domains` returns seed domains, `POST /api/domains/{id}/promote` validates preconditions, `PATCH /api/clusters/{id}` rejects unknown domains

### Frontend tests
- Domain store: fetches from API, caches, refreshes on `domain_created` SSE, refreshes on `taxonomy_changed` SSE
- `taxonomyColor()`: resolves from store, handles unknown domains (returns fallback), handles hex passthrough
- Inspector picker: renders dynamic domain list from store, handles empty state during load, shows retry button on API failure
- StatusBar: shows domain count, amber badge when `domain_utilization >= 0.8`
- Toast notifications: `domain_created` shows discovery toast, `domain_archival_suggested` shows actionable archive buttons, `domain_ceiling_reached` shows warning
