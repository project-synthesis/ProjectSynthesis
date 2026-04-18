/**
 * Transform API taxonomy tree data into scene-graph-ready structures.
 *
 * Pure functions — no Three.js dependency (just typed arrays and interfaces).
 * The renderer consumes SceneData to build Three.js objects.
 */
import type { ClusterNode, SimilarityEdge, InjectionEdge } from '$lib/api/clusters';
import type { LODTier } from './TopologyRenderer';
import { taxonomyColor } from '$lib/utils/colors';
import { parsePrimaryDomain } from '$lib/utils/formatting';
import type { ReadinessTier } from './readiness-tier';
import { composeReadinessTier } from './readiness-tier';
import { readinessStore } from '$lib/stores/readiness.svelte';

/**
 * Exhaustive set of valid `ReadinessTier` enum values. Used as a runtime
 * guard in `buildSceneData()` to drop unknown tier strings produced by
 * schema drift (e.g. a future backend adds a new stability tier the
 * frontend enum doesn't know yet). `Set` gives O(1) membership checking;
 * the cast is safe because the array is exhaustive over the literal
 * union (compile error if a member is removed). Re-declared here instead
 * of exported from `readiness-tier.ts` to keep that module a pure type +
 * palette utility, matching how `parsePrimaryDomain` and
 * `stateSizeMultiplier` keep their validators local.
 */
const _KNOWN_READINESS_TIERS: ReadonlySet<ReadinessTier> = new Set<ReadinessTier>([
  'healthy', 'warming', 'guarded', 'critical', 'ready',
]);

export interface SceneNode {
  id: string;
  position: [number, number, number];
  color: string;
  size: number;
  opacity: number;
  persistence: number;
  state: string;
  label: string;
  visible: boolean;
  parentId?: string;
  coherence: number;        // [0, 1] → wireframe brightness
  avgScore: number | null;  // [1, 10] → color saturation
  domain: string;           // primary domain (e.g. "backend", "general")
  memberCount: number;      // member_count from API
  isSubDomain: boolean;     // true for domain nodes whose parent is also a domain
  template_count: number;   // number of proven templates forked from this cluster
  readinessTier?: ReadinessTier; // composite readiness tier, only set on domain nodes with a matching report
}

/** Opacity by lifecycle state and active filter.
 *  - null filter ("all" tab): candidates at 40%, everything else 100%
 *  - filter with matches: matching nodes 100%, structural (domain/project) 50%, rest 25% (ghosted)
 *  - filter with NO matches: same as "all" — dimming everything to highlight
 *    nothing is pointless UX. The navigator list already says "0 clusters".
 *
 * The `hasMatches` param indicates whether any visible (non-archived) node
 * in the current scene matches the filter. Computed once in buildSceneData
 * and passed through to avoid per-node recomputation.
 */
function stateOpacity(state: string, stateFilter: string | null, hasMatches: boolean): number {
  if (stateFilter === null || !hasMatches) {
    // "all" tab or empty filter — candidates translucent, everything else full.
    return state === 'candidate' ? 0.4 : 1.0;
  }
  // Filtered tab with matches — matching nodes glow, structural nodes semi-visible, rest ghosted.
  if (state === stateFilter) return 1.0;
  if (state === 'domain' || state === 'project') return 0.5;
  return 0.25;
}

/** Size multiplier by lifecycle state.
 * Structural nodes (domain, project) aggregate children's members for base
 * size, so the multiplier is moderate — enough to be visually dominant
 * without overwhelming the graph at scale.
 *
 * Note: the final size is clamped to MAX_NODE_SIZE after applying
 * this multiplier, so structural nodes can't blow past the visual budget. */
function stateSizeMultiplier(state: string, isSubDomain: boolean = false): number {
  if (state === 'project') return 1.3;
  if (state === 'domain') return isSubDomain ? 1.0 : 1.3;
  if (state === 'mature') return 1.15;
  return 1.0;
}

/** Hard ceiling on final node size (radius in scene units).
 * Without this, domain nodes with many children can exceed the base
 * 3.0 clamp via the state multiplier, creating spheres that dwarf
 * everything else (cubic volume scaling makes even small radius
 * differences visually extreme). */
const MAX_NODE_SIZE = 3.0;

/** Color by lifecycle state.
 * All nodes (including templates) inherit their domain color via `taxonomyColor()`.
 * Template identification is carried by the halo ring decoration in SemanticTopology,
 * the cyan TEMPLATE badge in the inspector, and the `template_count` field on SceneNode.
 */
function stateNodeColor(_state: string, oklabColor: string | null): string {
  return taxonomyColor(oklabColor); // existing logic handles hex/domain/null
}

/**
 * Walk up the `state="domain"` parent chain to the TOP-LEVEL domain node and
 * return its label (for `taxonomyColor()` lookup). Returns `null` if the node
 * is not a domain-chain node or the chain is malformed.
 *
 * Used to make sub-domain nodes (and their descendant clusters) inherit the
 * parent domain's canonical brand color instead of their own backend-generated
 * OKLab variant. Without this, `security > token-ops` would render in a dark
 * red variant (`#d20033`) instead of the canonical security red (`#ff2255`),
 * breaking parity with `ClusterNavigator`'s per-domain color dots.
 *
 * The walk is bounded by `domainNodesById` — it stops as soon as the parent
 * is not a domain node, which is also the correct termination for clusters
 * parented to a sub-domain (they already carry `node.domain="<top-level>"`,
 * but if future backend changes allow sub-domain-scoped domain labels, this
 * walk keeps the frontend robust).
 */
function rootDomainLabel(
  node: { id: string; parent_id?: string | null; label?: string | null },
  domainNodesById: Map<string, { id: string; parent_id?: string | null; label?: string | null }>,
): string | null {
  let current = node;
  const seen = new Set<string>();
  // Cap at 8 hops to short-circuit any pathological cycle (defensive —
  // backend invariants forbid cycles, but a malformed tree shouldn't hang).
  for (let i = 0; i < 8; i++) {
    if (seen.has(current.id)) return null;
    seen.add(current.id);
    const parentId = current.parent_id ?? null;
    if (!parentId) return current.label ?? null;
    const parent = domainNodesById.get(parentId);
    if (!parent) return current.label ?? null;
    current = parent;
  }
  return current.label ?? null;
}

export interface SceneEdge {
  from: string;
  to: string;
  type: 'hierarchical' | 'similarity' | 'injection';
  distance?: number; // euclidean distance between endpoints (hierarchical only)
}

export interface SceneData {
  nodes: SceneNode[];
  edges: SceneEdge[];
}

// LOD persistence thresholds: nodes below these are hidden.
// Default persistence is 0.5 (from hot-path cluster creation), so FAR
// threshold must be <= 0.5 to show new clusters before cold-path runs.
const LOD_THRESHOLDS: Record<LODTier, number> = {
  far: 0.4,
  mid: 0.2,
  near: 0.0,
};

/**
 * Convert flat taxonomy node list into scene-ready nodes and edges.
 * Backend `get_tree` returns a flat list — we build edges from `parent_id`.
 *
 * Domain nodes (including sub-domains) are additionally decorated with an
 * optional `readinessTier` sourced from `readinessStore`. The store read is a
 * synchronous derived-map lookup (no IO, no side effects) so the function
 * remains a pure transform of `(flatNodes, edges, filter, readinessSnapshot)`
 * at call time. When the store is unloaded or lacks a matching report, the
 * field stays undefined and the consumer (`SemanticTopology`) simply omits
 * the contour ring — decoration is purely additive.
 *
 * **Hidden reactive dependency (not visible from signature):** this function
 * reads `readinessStore.byDomain(id)` internally. The read itself is pure
 * given a fixed store snapshot, but callers that need the rendered scene to
 * *react* to readiness changes MUST touch `readinessStore.reports` (or
 * another tracked rune on the store) inside the same `$effect` that invokes
 * `buildSceneData`. Without an explicit read, Svelte's reactivity tracker
 * cannot see the dependency and tier decoration will stop updating on SSE
 * `taxonomy_changed` / `domain_created` events. See `SemanticTopology.svelte`
 * for the canonical caller pattern: an `$effect` that reads
 * `readinessStore.reports` alongside `flatNodes` before calling this
 * function. Intentionally not surfaced as a parameter — threading the
 * snapshot through would be a breaking refactor, and the store is already
 * a singleton-scoped reactive source that every caller shares.
 */
export function buildSceneData(flatNodes: ClusterNode[], similarityEdges?: SimilarityEdge[], injectionEdges?: InjectionEdge[], stateFilter?: string | null): SceneData {
  const nodes: SceneNode[] = [];
  const edges: SceneEdge[] = [];

  // Filter out archived nodes — they belong in the ClusterNavigator's "archived"
  // tab but NOT in the 3D topology visualization.  The tree endpoint returns all
  // states (including archived) for the navigator; topology filters here.
  const visibleNodes = flatNodes.filter(n => n.state !== 'archived');

  // Check if any visible node matches the current state filter.
  // When no matches exist (empty tab), stateOpacity falls back to "all" mode
  // so the graph stays readable instead of ghosting everything.
  const effectiveFilter = stateFilter ?? null;
  const hasFilterMatches = effectiveFilter === null
    || visibleNodes.some(n => n.state === effectiveFilter);

  // Pre-compute aggregate member count per domain node (sum of children's members).
  // Domain nodes' own member_count is child-cluster count, not optimization count,
  // so a domain with 1 child cluster of 25 members would render tiny without this.
  const domainChildMembers = new Map<string, number>();
  for (const node of visibleNodes) {
    if (node.state === 'domain') {
      const childSum = visibleNodes
        .filter(n => n.parent_id === node.id && n.state !== 'domain')
        .reduce((sum, n) => sum + n.member_count + n.usage_count * 0.5, 0);
      domainChildMembers.set(node.id, childSum);
    }
  }

  // Pre-compute aggregate member count per project node (sum of domain children).
  // Project nodes size by their descendant optimizations, not their raw member_count
  // (which tracks optimization count, not structural children).
  const projectChildMembers = new Map<string, number>();
  for (const node of visibleNodes) {
    if (node.state === 'project') {
      const childDomains = visibleNodes.filter(n => n.parent_id === node.id && n.state === 'domain');
      const projectSum = childDomains.reduce(
        (sum, d) => sum + (domainChildMembers.get(d.id) ?? d.member_count ?? 0), 0
      );
      projectChildMembers.set(node.id, projectSum);
    }
  }

  // Detect sub-domain nodes: state="domain" with parent_id pointing to another domain node
  const domainNodesById = new Map(visibleNodes.filter(n => n.state === 'domain').map(n => [n.id, n]));
  const domainIds = new Set(domainNodesById.keys());
  const subDomainIds = new Set(
    visibleNodes
      .filter(n => n.state === 'domain' && n.parent_id != null && domainIds.has(n.parent_id))
      .map(n => n.id)
  );

  for (const node of visibleNodes) {
    // Position: UMAP coords scaled to scene units, or hash-based fallback.
    // UMAP outputs ~[-1, 1]; multiply by 10 for comfortable spacing.
    const UMAP_SCALE = 10;
    const x = node.umap_x != null ? node.umap_x * UMAP_SCALE : hashFloat(node.id, 0) * 20 - 10;
    const y = node.umap_y != null ? node.umap_y * UMAP_SCALE : hashFloat(node.id, 1) * 20 - 10;
    const z = node.umap_z != null ? node.umap_z * UMAP_SCALE : hashFloat(node.id, 2) * 20 - 10;

    // Size: structural nodes aggregate descendants; clusters use their own.
    const memberInput = node.state === 'project'
      ? projectChildMembers.get(node.id) ?? 1
      : node.state === 'domain'
        ? domainChildMembers.get(node.id) ?? 1
        : node.member_count + node.usage_count * 0.5;
    const raw = Math.log2(Math.max(1, memberInput));
    let size = Math.max(0.6, Math.min(3.0, raw * 0.5));

    // GENERAL domain: vary size by score to reduce uniform gray blob appearance
    const primaryDomain = parsePrimaryDomain(node.domain);
    if (primaryDomain === 'general' && node.state !== 'domain' && node.avg_score != null) {
      const scoreBonus = (node.avg_score - 5) * 0.1; // +/-0.5 range centered on score 5
      size = Math.max(0.6, Math.min(3.0, size + scoreBonus));
    }

    // Final size: apply state multiplier then clamp to prevent domain
    // nodes from overwhelming the scene (volume scales as r³).
    const isSubDomain = subDomainIds.has(node.id);
    const finalSize = Math.min(MAX_NODE_SIZE, size * stateSizeMultiplier(node.state, isSubDomain));
    const nodeOpacity = stateOpacity(node.state, effectiveFilter, hasFilterMatches);

    // Domain-chain color inheritance: any node parented directly to a domain
    // node walks up to the TOP-LEVEL domain's label so brand colors match the
    // navbar. Covers two cases:
    //   (1) sub-domain nodes (state=domain parented to a top-level domain),
    //   (2) clusters parented to a sub-domain (should render in the top-level
    //       brand color, not a sub-domain OKLab variant).
    // Nodes whose parent is not a domain (or whose parent is missing) fall back
    // to their own `node.domain` as before — the previous behavior for regular
    // clusters under a top-level domain is unchanged by construction because
    // `rootDomainLabel` returns the top-level domain's label either way.
    const parentIsDomain = node.parent_id != null && domainNodesById.has(node.parent_id);
    const colorKey = parentIsDomain
      ? (rootDomainLabel(node, domainNodesById) ?? node.domain ?? node.color_hex)
      : (node.domain ?? node.color_hex);

    const sceneNode: SceneNode = {
      id: node.id,
      position: [x, y, z],
      color: stateNodeColor(node.state, colorKey),
      size: finalSize,
      opacity: nodeOpacity,
      persistence: node.persistence ?? 0.5,
      state: node.state,
      label: nodeOpacity < 0.5 ? '' : (node.label ?? ''),
      visible: true,
      parentId: node.parent_id ?? undefined,
      coherence: node.coherence ?? 0.5,
      avgScore: node.avg_score ?? null,
      domain: parsePrimaryDomain(node.domain),
      memberCount: node.member_count,
      isSubDomain: subDomainIds.has(node.id),
      template_count: node.template_count ?? 0,
    };

    // Decorate domain nodes (top-level and sub-domain) with composite
    // readiness tier when a matching report exists. `readinessStore.byDomain`
    // is a derived O(1) map lookup — no IO — so the surrounding function
    // stays pure relative to `(flatNodes, readinessSnapshot)`. When the store
    // is unloaded (empty `reports`) or the domain has no report yet, the
    // field stays undefined and the ring is omitted by `SemanticTopology`.
    if (node.state === 'domain') {
      const report = readinessStore.byDomain(node.id);
      if (report) {
        // Schema-drift guard: if composeReadinessTier passes through an
        // unknown stability tier (backend adds a new enum value the
        // frontend hasn't adopted), drop it — the ring renderer looks up
        // color by key and would silently paint an invalid `undefined`.
        const composed = composeReadinessTier(report);
        if (_KNOWN_READINESS_TIERS.has(composed)) {
          sceneNode.readinessTier = composed;
        }
      }
    }

    nodes.push(sceneNode);

    // Hierarchical edges from parent_id
    if (node.parent_id) {
      edges.push({ from: node.parent_id, to: node.id, type: 'hierarchical' });
    }
  }

  // Compute distances for hierarchical edges (proximity suppression in renderer)
  const positionById = new Map(nodes.map(n => [n.id, n.position]));
  for (const edge of edges) {
    if (edge.type !== 'hierarchical') continue;
    const fp = positionById.get(edge.from);
    const tp = positionById.get(edge.to);
    if (fp && tp) {
      const dx = fp[0] - tp[0], dy = fp[1] - tp[1], dz = fp[2] - tp[2];
      edge.distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
    }
  }

  // Optional edge layers — build node lookup once for both
  const nodeIdSet = (similarityEdges || injectionEdges)
    ? new Set(nodes.map(n => n.id))
    : null;

  // Similarity edges from embedding index pairwise similarities
  if (similarityEdges && nodeIdSet) {
    for (const edge of similarityEdges) {
      if (nodeIdSet.has(edge.from_id) && nodeIdSet.has(edge.to_id)) {
        edges.push({ from: edge.from_id, to: edge.to_id, type: 'similarity' });
      }
    }
  }

  // Injection provenance edges (directed: source cluster → target cluster)
  if (injectionEdges && nodeIdSet) {
    for (const edge of injectionEdges) {
      if (nodeIdSet.has(edge.source_id) && nodeIdSet.has(edge.target_id)) {
        edges.push({ from: edge.source_id, to: edge.target_id, type: 'injection' });
      }
    }
  }

  return { nodes, edges };
}

/**
 * Update visibility flags based on LOD tier.
 * Mutates nodes in place for performance.
 */
export function assignLodVisibility(nodes: SceneNode[], tier: LODTier): void {
  const threshold = LOD_THRESHOLDS[tier];
  for (const node of nodes) {
    node.visible = node.persistence >= threshold;
  }
}

/** Build a lookup map of node ID → SceneNode for beam targeting. */
export function buildNodeMap(nodes: SceneNode[]): Map<string, SceneNode> {
  const map = new Map<string, SceneNode>();
  for (const node of nodes) {
    map.set(node.id, node);
  }
  return map;
}

/**
 * Compute per-edge opacity for hierarchical edges based on parent child count.
 * Dense domains (many children) get lighter edges; sparse domains keep full opacity.
 * Formula: base * min(1, CAP / childCount)
 * With base=0.55, CAP=6: 3 children → 0.55, 12 children → 0.275, 20 children → 0.165.
 * Base is higher than a standalone 0.4 because the depth shader applies an
 * additional proportional reduction (up to 60% at far distance).
 */
const DENSITY_OPACITY_BASE = 1.0;
const DENSITY_OPACITY_CAP = 10;

export function computeHierarchicalOpacity(childCount: number): number {
  if (childCount <= 0) return DENSITY_OPACITY_BASE;
  return DENSITY_OPACITY_BASE * Math.min(1.0, DENSITY_OPACITY_CAP / childCount);
}

/** Deterministic float from string hash (0..1). */
function hashFloat(str: string, seed: number): number {
  let h = seed * 2654435761;
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  }
  return ((h >>> 0) % 10000) / 10000;
}
