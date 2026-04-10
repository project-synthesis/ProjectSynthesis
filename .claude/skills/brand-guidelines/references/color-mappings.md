# Color Mappings Reference

## Table of Contents
- [Routing Tier Aesthetics](#routing-tier-aesthetics)
- [Domain Color Coding](#domain-color-coding)
- [Strategy Color Mapping](#strategy-color-mapping)
- [Task Type Color Mapping](#task-type-color-mapping)
- [Complexity Colors](#complexity-colors)
- [Score-to-Color Mapping](#score-to-color-mapping)
- [Data Visualization](#data-visualization)

---

## Routing Tier Aesthetics

Each routing tier has a dedicated accent color applied consistently across Navigator headings, toggles, badges, data values, and StatusBar phase indicators.

| Tier | Color | Variable | CSS classes |
|------|-------|----------|-------------|
| **Internal** (default) | Cyan `#00e5ff` | `--color-neon-cyan` | Default styling — no special class needed |
| **Sampling** | Green `#22ff88` | `--color-neon-green` | `.sub-heading--sampling`, `.sub-heading-note--sampling`, `.toggle-track--green`, `.status-phase-sampling` |
| **Passthrough** | Yellow `#fbbf24` | `--color-neon-yellow` | `.sub-heading--passthrough`, `.neon-yellow`, `.toggle-track--yellow`, `.status-phase-passthrough` |

**Rule**: Apply tier accent class conditionally via `routing.isSampling` / `routing.isPassthrough`. Internal uses default cyan (no special class).

**Badges**: `VIA MCP SAMPLING` (green border+text) or `PASSTHROUGH` (yellow border+text). Internal shows no badge.

**Force toggles**: Force IDE sampling → `.toggle-track--green` (always, identifies the toggle's tier). Force passthrough → `.toggle-track--yellow` (always).

**Source:** `frontend/src/lib/components/layout/Navigator.svelte` (CSS), `frontend/src/lib/stores/routing.svelte.ts` (logic)

---

## Domain Color Coding

Domains are dynamic — discovered organically by the taxonomy engine (ADR-004). Colors are **API-driven**, not hardcoded. The frontend fetches domain colors from `GET /api/domains` via the `domainStore`.

### Seed Domain Colors (migration-assigned)

| Domain | Color | Hex | Note |
|--------|-------|-----|------|
| backend | violet | `#b44aff` | |
| frontend | hot pink | `#ff4895` | |
| database | steel blue | `#36b5ff` | |
| data | warm taupe | `#b49982` | Perceptually distant from database's steel blue (ΔE=0.20) |
| devops | indigo | `#6366f1` | |
| security | red | `#ff2255` | |
| fullstack | magenta | `#d946ef` | |
| general | dim gray | `#7a7a9e` | Catch-all / incubator for undiscovered domains |

### Discovered Domain Colors (computed at creation)

When the taxonomy warm path discovers a new domain (e.g., "marketing"), its color is computed via **OKLab max-perceptual-distance** (`compute_max_distance_color()` in `taxonomy/coloring.py`). The algorithm:

1. Samples the OKLab color space at L=0.7 (neon brightness) across the (a, b) plane
2. For each candidate, computes minimum perceptual distance to ALL existing colors
3. Selects the candidate with the highest minimum distance

The avoidance list includes:
- All existing domain node colors (from DB)
- All 10 brand neon palette colors (`BRAND_RESERVED_COLORS` in `pipeline_constants.py`)

This guarantees discovered domains get unique, brand-safe colors that don't collide with any semantic signal in the design system.

### Domain State Badge

The `domain` lifecycle state uses warm platinum `#c0a060` — communicates "structural foundation" without colliding with any tier accent or neon palette color.

### Rules

1. **Domain colors are pinned at creation time.** The cold path's OKLab color assignment skips `state="domain"` nodes — domain colors never drift with UMAP repositioning.
2. **Domain colors must never overlap with brand reserved colors.** The 10 neon palette colors have fixed semantic assignments. Domain colors occupy a separate perceptual region.
3. **No hardcoded domain color maps in frontend.** All resolution goes through `domainStore.colorFor()` which fetches from the API. Adding a new domain requires zero frontend code changes.
4. **Fallback color is `#7a7a9e`** (general's gray / `--color-text-dim`) for unknown or unresolved domains.

**Source:** `frontend/src/lib/stores/domains.svelte.ts` → `colorFor()`, `backend/app/services/taxonomy/coloring.py` → `compute_max_distance_color()`

---

## Strategy Color Mapping

Each of the 10 optimization strategies has a unique neon color for visual identification across bars, badges, borders, and buttons.

| Strategy | Color Name | Hex | Raw RGBA |
|----------|-----------|-----|----------|
| Chain of Thought | neon-cyan | `#00e5ff` | `rgba(0, 229, 255, 0.35)` |
| CO-STAR | neon-purple | `#a855f7` | `rgba(168, 85, 247, 0.35)` |
| RISEN | neon-green | `#22ff88` | `rgba(34, 255, 136, 0.35)` |
| Role-Task-Format | neon-red | `#ff3366` | `rgba(255, 51, 102, 0.35)` |
| Few-Shot | neon-yellow | `#fbbf24` | `rgba(251, 191, 36, 0.35)` |
| Step by Step | neon-orange | `#ff8c00` | `rgba(255, 140, 0, 0.35)` |
| Structured Output | neon-blue | `#4d8eff` | `rgba(77, 142, 255, 0.35)` |
| Constraint Injection | neon-pink | `#ff6eb4` | `rgba(255, 110, 180, 0.35)` |
| Context Enrichment | neon-teal | `#00d4aa` | `rgba(0, 212, 170, 0.35)` |
| Persona Assignment | neon-indigo | `#7b61ff` | `rgba(123, 97, 255, 0.35)` |

**Source:** `frontend/src/lib/utils/strategies.ts` (`STRATEGY_COLOR_META`)

---

## Task Type Color Mapping

Each of the 14 classified task types has a neon color assignment. Primary types use full-opacity colors; secondary types use dimmed variants.

| Task Type | Color Name | CSS Color | Raw RGBA |
|-----------|-----------|-----------|----------|
| coding | neon-cyan | `#00e5ff` | `rgba(0, 229, 255, 0.35)` |
| analysis | neon-blue | `#4d8eff` | `rgba(77, 142, 255, 0.35)` |
| reasoning | neon-indigo | `#7b61ff` | `rgba(123, 97, 255, 0.35)` |
| math | neon-purple | `#a855f7` | `rgba(168, 85, 247, 0.35)` |
| writing | neon-green | `#22ff88` | `rgba(34, 255, 136, 0.35)` |
| creative | neon-pink | `#ff6eb4` | `rgba(255, 110, 180, 0.35)` |
| extraction | neon-teal | `#00d4aa` | `rgba(0, 212, 170, 0.35)` |
| classification | neon-orange | `#ff8c00` | `rgba(255, 140, 0, 0.35)` |
| formatting | neon-yellow | `#fbbf24` | `rgba(251, 191, 36, 0.35)` |
| medical | neon-red | `#ff3366` | `rgba(255, 51, 102, 0.35)` |
| legal | neon-red (dim) | `rgba(255, 51, 102, 0.7)` | `rgba(255, 51, 102, 0.25)` |
| education | neon-teal (dim) | `rgba(0, 212, 170, 0.7)` | `rgba(0, 212, 170, 0.25)` |
| general | neon-cyan (dim) | `rgba(0, 229, 255, 0.6)` | `rgba(0, 229, 255, 0.20)` |
| other | text-dim | `rgba(255, 255, 255, 0.4)` | `rgba(255, 255, 255, 0.10)` |

**Source:** `frontend/src/lib/utils/taskTypes.ts` (`TASK_TYPE_COLOR_META`)

---

## Complexity Colors

3-tier system with alias normalization (simple/low, moderate/medium, complex/high).

| Level | Aliases | Color Name | Hex | Raw RGBA |
|-------|---------|-----------|-----|----------|
| Low | simple, low | neon-green | `#22ff88` | `rgba(34, 255, 136, 0.35)` |
| Medium | moderate, medium | neon-yellow | `#fbbf24` | `rgba(251, 191, 36, 0.35)` |
| High | complex, high | neon-red | `#ff3366` | `rgba(255, 51, 102, 0.35)` |

**Source:** `frontend/src/lib/utils/complexity.ts`

---

## Score-to-Color Mapping

| Range | Color | Semantic |
|-------|-------|----------|
| 1–3 | neon-red | Poor — needs significant improvement |
| 4–6 | neon-yellow | Moderate — room for improvement |
| 7–8 | neon-cyan | Good — solid quality |
| 9–10 | neon-green | Excellent — high precision |

---

## Data Visualization

### Score Display Conventions

| Context | Format | Font | Color Logic |
|---------|--------|------|-------------|
| Score circle | Integer 1–10 inside 20px circle | Mono, 10px, 700 | Score-mapped (see table above) |
| Inline score | `8.2/10` | Mono, inherited size | Accent color of parent context |
| Score delta | `+2.1` or `-0.8` | Mono, inherited size | Positive = green, negative = red, zero = dim |
| Percentage | `42%` | Mono, inherited size | Contextual accent |
| Confidence | `0.87` (0–1 scale) | Mono, inherited size | Strategy accent color |

### Comparative Display

When showing before/after or side-by-side results:
- Original prompt: `text-secondary` (dimmed, the "before")
- Optimized prompt: `text-primary` (bright, the "after")
- Score improvements: green delta badges (`+N.N`) next to the optimized score
- Score regressions: red delta badges (`-N.N`) — never hidden, always visible
- Equal scores: dim text, no badge

### Taxonomy Visualization (3D Topology)

Domain and cluster nodes in the SemanticTopology (Three.js) follow chromatic encoding rules:

| Node type | Size | Color source | Opacity | Persistence |
|-----------|------|-------------|---------|-------------|
| **Domain** (`state="domain"`) | 2x base radius | Pinned `color_hex` from DB | 1.0 (always visible) | 1.0 (maximum) |
| **Active cluster** | 1x base radius | OKLab from UMAP position | 1.0 | Computed from HDBSCAN |
| **Candidate cluster** | 1x base radius | OKLab from UMAP position | 0.4 (dimmed) | Low |
| **Mature cluster** | 1.2x base radius | OKLab from UMAP position | 1.0 | High |
| **Template cluster** | 1.5x base radius | Overridden to neon-cyan `#00e5ff` | 1.0 | Very high |

**Node rendering rules:**
- Wireframe contour over dark fill (zero-effects directive: no glow, no emission bloom)
- LOD tiers: far (persistence ≥ 0.4), mid (≥ 0.2), near (≥ 0.0) — domain nodes always visible at all tiers
- Labels: billboard text, white on transparent, auto-hide at far LOD for non-domain nodes
- Raycasting: hover highlight via contour intensification (1px → 2px border), not color change

**Lifecycle state badge colors** (used in Inspector, ClusterNavigator filter tabs):

| State | Color | Hex | Note |
|-------|-------|-----|------|
| candidate | dim gray | `#7a7a9e` | Transient, not yet promoted |
| active | neon-blue | `#4d8eff` | Active cluster |
| mature | neon-purple | `#a855f7` | Stable, high-quality |
| template | neon-cyan | `#00e5ff` | Proven, reusable |
| domain | warm platinum | `#c0a060` | Structural foundation (distinct from all neon palette + tier accents) |
| archived | deep dark | `#2a2a3e` | Retired, dimmed |

**Source:** `frontend/src/lib/utils/colors.ts` → `stateColor()`, `frontend/src/lib/components/taxonomy/TopologyData.ts`

### Progress Indicators

| Type | Visual | Animation |
|------|--------|-----------|
| Pipeline stage progress | 4-node vertical timeline with gradient connectors | Nodes fill with stage color as they complete |
| Score bar | Horizontal fill bar, width = score/10 | 500ms `ease` fill animation |
| Batch progress | Fraction counter (`3/12`) in mono | Instant update, no animation on count |
| Loading skeleton | `shimmer` gradient sweep on bg-hover surface | 1500ms infinite |
