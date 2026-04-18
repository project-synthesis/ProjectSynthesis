import { describe, it, expect, beforeEach } from 'vitest';
import { buildSceneData, assignLodVisibility, computeHierarchicalOpacity, type SceneNode } from './TopologyData';
import type { ClusterNode } from '$lib/api/clusters';
import { domainStore } from '$lib/stores/domains.svelte';
import { readinessStore } from '$lib/stores/readiness.svelte';
import type { DomainReadinessReport } from '$lib/api/readiness';

function makeNode(overrides: Partial<ClusterNode> = {}): ClusterNode {
  return {
    id: 'node-1',
    parent_id: null,
    label: 'Test',
    state: 'active',
    domain: 'backend',
    task_type: 'coding',
    persistence: 0.8,
    coherence: 0.9,
    separation: 0.85,
    stability: 0.7,
    member_count: 10,
    usage_count: 5,
    avg_score: null,
    color_hex: '#b44aff',
    umap_x: 1.0,
    umap_y: 2.0,
    umap_z: 3.0,
    preferred_strategy: null,
    output_coherence: null, blend_w_raw: null, blend_w_optimized: null,
    blend_w_transform: null, split_failures: 0, meta_pattern_count: 0, template_count: 0,
    created_at: null,
    children: [],
    ...overrides,
  };
}

describe('buildSceneData', () => {
  beforeEach(() => {
    domainStore._reset();
    // Populate domain store for tests that rely on domain color resolution
    domainStore.domains = [
      { id: 'd1', label: 'backend', color_hex: '#b44aff', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'd2', label: 'frontend', color_hex: '#ff4895', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'd3', label: 'general', color_hex: '#7a7a9e', member_count: 0, avg_score: null, source: 'seed' },
    ];
  });

  it('creates SceneNode for each tree node', () => {
    const tree = [makeNode({ id: 'a' }), makeNode({ id: 'b' })];
    const result = buildSceneData(tree);
    expect(result.nodes).toHaveLength(2);
    expect(result.nodes[0].id).toBe('a');
  });

  it('handles flat list with parent_id references', () => {
    // Backend get_tree returns flat list — children linked via parent_id
    const flat = [
      makeNode({ id: 'parent' }),
      makeNode({ id: 'child', parent_id: 'parent' }),
    ];
    const result = buildSceneData(flat);
    expect(result.nodes).toHaveLength(2);
  });

  it('creates hierarchical edges from parent_id', () => {
    const flat = [
      makeNode({ id: 'parent', domain: 'backend' }),
      makeNode({ id: 'child', parent_id: 'parent', domain: 'frontend' }),
    ];
    const result = buildSceneData(flat);
    // 1 hierarchical edge only (different domains → no similarity edge)
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0]).toMatchObject({ from: 'parent', to: 'child', type: 'hierarchical' });
  });

  it('produces no similarity or injection edges when not passed', () => {
    const flat = [
      makeNode({ id: 'a', domain: 'backend' }),
      makeNode({ id: 'b', domain: 'backend' }),
      makeNode({ id: 'c', domain: 'frontend' }),
    ];
    const result = buildSceneData(flat);
    // Without explicit similarity/injection edge arrays, only hierarchical edges appear
    expect(result.edges).toHaveLength(0);
    expect(result.edges.filter(e => e.type === 'similarity')).toHaveLength(0);
    expect(result.edges.filter(e => e.type === 'injection')).toHaveLength(0);
  });

  it('adds similarity edges when passed and both nodes exist in scene', () => {
    const flat = [
      makeNode({ id: 'a', domain: 'backend' }),
      makeNode({ id: 'b', domain: 'frontend' }),
    ];
    const simEdges = [{ from_id: 'a', to_id: 'b', similarity: 0.75 }];
    const result = buildSceneData(flat, simEdges);
    const sim = result.edges.filter(e => e.type === 'similarity');
    expect(sim).toHaveLength(1);
    expect(sim[0]).toEqual({ from: 'a', to: 'b', type: 'similarity' });
  });

  it('filters similarity edges where a node is missing from scene', () => {
    const flat = [makeNode({ id: 'a' })];
    const simEdges = [{ from_id: 'a', to_id: 'missing', similarity: 0.8 }];
    const result = buildSceneData(flat, simEdges);
    expect(result.edges.filter(e => e.type === 'similarity')).toHaveLength(0);
  });

  it('adds injection edges when passed and both nodes exist in scene', () => {
    const flat = [
      makeNode({ id: 'src' }),
      makeNode({ id: 'tgt' }),
    ];
    const injEdges = [{ source_id: 'src', target_id: 'tgt', weight: 3 }];
    const result = buildSceneData(flat, undefined, injEdges);
    const inj = result.edges.filter(e => e.type === 'injection');
    expect(inj).toHaveLength(1);
    expect(inj[0]).toEqual({ from: 'src', to: 'tgt', type: 'injection' });
  });

  it('filters injection edges where a node is missing from scene', () => {
    const flat = [makeNode({ id: 'src' })];
    const injEdges = [{ source_id: 'src', target_id: 'missing', weight: 1 }];
    const result = buildSceneData(flat, undefined, injEdges);
    expect(result.edges.filter(e => e.type === 'injection')).toHaveLength(0);
  });

  it('defaults persistence to 0.5 when node has null persistence', () => {
    const tree = [makeNode({ persistence: null as unknown as number })];
    const result = buildSceneData(tree);
    expect(result.nodes[0].persistence).toBe(0.5);
  });

  it('uses fallback position when UMAP coords are null', () => {
    const tree = [makeNode({ umap_x: null, umap_y: null, umap_z: null })];
    const result = buildSceneData(tree);
    // Should have deterministic fallback, not NaN
    expect(Number.isFinite(result.nodes[0].position[0])).toBe(true);
  });

  it('converts null label to empty string', () => {
    const tree = [makeNode({ label: null as unknown as string })];
    const result = buildSceneData(tree);
    expect(result.nodes[0].label).toBe('');
  });

  it('uses domain color as fallback when color_hex is null', () => {
    const tree = [makeNode({ color_hex: null, domain: 'backend' })];
    const result = buildSceneData(tree);
    expect(result.nodes[0].color).toBe('#b44aff'); // backend domain color
  });

  it('uses general fallback for unknown domain with null color_hex', () => {
    const tree = [makeNode({ color_hex: null, domain: 'unknown-domain' })];
    const result = buildSceneData(tree);
    expect(result.nodes[0].color).toBe('#7a7a9e'); // FALLBACK_COLOR
  });

  it('sub-domain node inherits parent domain canonical color, not its own OKLab variant', () => {
    // Regression: a sub-domain like `security > token-ops` used to resolve to
    // its own backend-generated OKLab variant (e.g. #d20033 dark red) instead
    // of the parent's canonical brand color (#ff2255 bright red). Pattern
    // graph color parity with ClusterNavigator requires parent inheritance.
    domainStore.domains = [
      { id: 'sec', label: 'security', color_hex: '#ff2255', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'sub', label: 'token-ops', color_hex: '#d20033', member_count: 0, avg_score: null, source: 'seed' },
    ];
    const tree = [
      makeNode({ id: 'sec', state: 'domain', domain: 'security', label: 'security' }),
      makeNode({ id: 'sub', state: 'domain', domain: 'token-ops', label: 'token-ops', parent_id: 'sec' }),
    ];
    const { nodes } = buildSceneData(tree);
    const sub = nodes.find((n) => n.id === 'sub')!;
    expect(sub.isSubDomain).toBe(true);
    expect(sub.color).toBe('#ff2255');
  });

  it('top-level domain node keeps its own canonical color', () => {
    // Guard that the parent-inheritance rule doesn't accidentally rewrite
    // top-level domain colors (they have no domain parent to walk to).
    domainStore.domains = [
      { id: 'sec', label: 'security', color_hex: '#ff2255', member_count: 0, avg_score: null, source: 'seed' },
    ];
    const tree = [makeNode({ id: 'sec', state: 'domain', domain: 'security', label: 'security' })];
    const { nodes } = buildSceneData(tree);
    expect(nodes[0].color).toBe('#ff2255');
  });

  it('cluster parented to a sub-domain inherits the TOP-LEVEL domain color', () => {
    // A cluster under `security > token-ops` must render in security-red,
    // not token-ops variant red. We walk all the way to the root domain.
    domainStore.domains = [
      { id: 'sec', label: 'security', color_hex: '#ff2255', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'sub', label: 'token-ops', color_hex: '#d20033', member_count: 0, avg_score: null, source: 'seed' },
    ];
    const tree = [
      makeNode({ id: 'sec', state: 'domain', domain: 'security', label: 'security' }),
      makeNode({ id: 'sub', state: 'domain', domain: 'token-ops', label: 'token-ops', parent_id: 'sec' }),
      makeNode({ id: 'c1', state: 'active', domain: 'security', label: 'c1', parent_id: 'sub', color_hex: null }),
    ];
    const { nodes } = buildSceneData(tree);
    const cluster = nodes.find((n) => n.id === 'c1')!;
    expect(cluster.color).toBe('#ff2255');
  });

  it('handles empty input array', () => {
    const result = buildSceneData([]);
    expect(result.nodes).toHaveLength(0);
    expect(result.edges).toHaveLength(0);
  });
});

describe('assignLodVisibility', () => {
  it('hides low-persistence nodes at far LOD', () => {
    const nodes: SceneNode[] = [
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, opacity: 1, persistence: 0.9, state: 'active', label: 'A', visible: true, coherence: 0.5, avgScore: null, domain: 'general', memberCount: 0, isSubDomain: false, template_count: 0 },
      { id: 'b', position: [1,1,1], color: '#fff', size: 1, opacity: 1, persistence: 0.2, state: 'active', label: 'B', visible: true, coherence: 0.5, avgScore: null, domain: 'general', memberCount: 0, isSubDomain: false, template_count: 0 },
    ];
    assignLodVisibility(nodes, 'far');
    expect(nodes[0].visible).toBe(true);
    expect(nodes[1].visible).toBe(false);
  });

  it('shows all nodes at near LOD', () => {
    const nodes: SceneNode[] = [
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, opacity: 1, persistence: 0.1, state: 'active', label: 'A', visible: false, coherence: 0.5, avgScore: null, domain: 'general', memberCount: 0, isSubDomain: false, template_count: 0 },
    ];
    assignLodVisibility(nodes, 'near');
    expect(nodes[0].visible).toBe(true);
  });

  it('shows nodes with threshold-level persistence at mid LOD', () => {
    const nodes: SceneNode[] = [
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, opacity: 1, persistence: 0.3, state: 'active', label: 'A', visible: false, coherence: 0.5, avgScore: null, domain: 'general', memberCount: 0, isSubDomain: false, template_count: 0 },
      { id: 'b', position: [1,1,1], color: '#fff', size: 1, opacity: 1, persistence: 0.1, state: 'active', label: 'B', visible: false, coherence: 0.5, avgScore: null, domain: 'general', memberCount: 0, isSubDomain: false, template_count: 0 },
    ];
    assignLodVisibility(nodes, 'mid');
    expect(nodes[0].visible).toBe(true);   // 0.3 >= 0.2
    expect(nodes[1].visible).toBe(false);  // 0.1 < 0.2
  });
});

describe('buildSceneData — state-based visual encoding', () => {
  it('surfaces template_count through SceneNode', () => {
    const cluster = makeNode({ id: 'c1', state: 'mature', template_count: 3 });
    const { nodes } = buildSceneData([cluster], undefined, undefined, null);
    const node = nodes.find(n => n.id === 'c1');
    expect(node?.template_count).toBe(3);
  });

  it('template_count defaults to 0 when absent from input node', () => {
    // makeNode does not set template_count — it should default to 0, not undefined
    const cluster = makeNode({ id: 'c1', state: 'active' });
    const { nodes } = buildSceneData([cluster]);
    expect(nodes[0].template_count).toBe(0);
  });

  it('mature state nodes get size multiplied by 1.15', () => {
    const baseNode = makeNode({ id: 'base', state: 'active', member_count: 4, usage_count: 2 });
    const matureNode = makeNode({ id: 'mat', state: 'mature', member_count: 4, usage_count: 2 });

    const { nodes } = buildSceneData([baseNode, matureNode]);
    const base = nodes.find(n => n.id === 'base')!;
    const mature = nodes.find(n => n.id === 'mat')!;

    expect(mature.size).toBeCloseTo(base.size * 1.15, 5);
  });

  it('candidate state nodes get opacity 0.4', () => {
    const { nodes } = buildSceneData([makeNode({ state: 'candidate' })]);
    expect(nodes[0].opacity).toBe(0.4);
  });

  it('active state nodes get opacity 1.0', () => {
    const { nodes } = buildSceneData([makeNode({ state: 'active' })]);
    expect(nodes[0].opacity).toBe(1.0);
  });

  it('SceneNode always has an opacity field', () => {
    const { nodes } = buildSceneData([makeNode()]);
    expect(nodes[0]).toHaveProperty('opacity');
    expect(typeof nodes[0].opacity).toBe('number');
  });
});

describe('buildSceneData — edge distance', () => {
  it('computes distance on hierarchical edges', () => {
    const parent = makeNode({
      id: 'parent', state: 'domain',
      umap_x: 0, umap_y: 0, umap_z: 0,
    });
    const child = makeNode({
      id: 'child', parent_id: 'parent',
      umap_x: 0.3, umap_y: 0.4, umap_z: 0,
    });
    const { edges } = buildSceneData([parent, child]);
    const hier = edges.find(e => e.type === 'hierarchical')!;
    expect(hier.distance).toBeDefined();
    // UMAP_SCALE=10, so positions are (0,0,0) and (3,4,0), distance = 5
    expect(hier.distance).toBeCloseTo(5.0, 1);
  });

  it('does not set distance on similarity edges', () => {
    const a = makeNode({ id: 'a' });
    const b = makeNode({ id: 'b' });
    const simEdges = [{ from_id: 'a', to_id: 'b', similarity: 0.8 }];
    const { edges } = buildSceneData([a, b], simEdges);
    const sim = edges.find(e => e.type === 'similarity')!;
    expect(sim.distance).toBeUndefined();
  });
});

describe('buildSceneData — quality encoding', () => {
  beforeEach(() => {
    domainStore._reset();
    domainStore.domains = [
      { id: 'd1', label: 'backend', color_hex: '#b44aff', member_count: 0, avg_score: null, source: 'seed' },
    ];
  });

  it('populates coherence from ClusterNode, defaults to 0.5 when null', () => {
    const withCoherence = makeNode({ id: 'a', coherence: 0.9 });
    const withNull = makeNode({ id: 'b', coherence: null });
    const { nodes } = buildSceneData([withCoherence, withNull]);
    expect(nodes.find(n => n.id === 'a')!.coherence).toBe(0.9);
    expect(nodes.find(n => n.id === 'b')!.coherence).toBe(0.5);
  });

  it('populates avgScore from ClusterNode, preserves null', () => {
    const withScore = makeNode({ id: 'a', avg_score: 7.5 });
    const withNull = makeNode({ id: 'b', avg_score: null });
    const { nodes } = buildSceneData([withScore, withNull]);
    expect(nodes.find(n => n.id === 'a')!.avgScore).toBe(7.5);
    expect(nodes.find(n => n.id === 'b')!.avgScore).toBeNull();
  });
});

describe('computeHierarchicalOpacity', () => {
  // Base=1.0, CAP=10. Density is the primary opacity control; depth adds 25% reduction.
  it('returns full opacity for small domains (≤10 children)', () => {
    expect(computeHierarchicalOpacity(1)).toBeCloseTo(1.0);
    expect(computeHierarchicalOpacity(5)).toBeCloseTo(1.0);
    expect(computeHierarchicalOpacity(10)).toBeCloseTo(1.0);
  });

  it('reduces opacity for dense domains', () => {
    expect(computeHierarchicalOpacity(20)).toBeCloseTo(0.5);
    expect(computeHierarchicalOpacity(40)).toBeCloseTo(0.25);
  });

  it('handles zero/negative gracefully', () => {
    expect(computeHierarchicalOpacity(0)).toBeCloseTo(1.0);
    expect(computeHierarchicalOpacity(-1)).toBeCloseTo(1.0);
  });
});

describe('buildSceneData — readiness tier decoration', () => {
  function makeReadinessReport(overrides: Partial<DomainReadinessReport> = {}): DomainReadinessReport {
    return {
      domain_id: 'd1',
      domain_label: 'backend',
      member_count: 20,
      stability: {
        consistency: 0.3,
        dissolution_floor: 0.15,
        hysteresis_creation_threshold: 0.6,
        age_hours: 72,
        min_age_hours: 48,
        member_count: 20,
        member_ceiling: 5,
        sub_domain_count: 0,
        total_opts: 20,
        guards: {
          general_protected: false,
          has_sub_domain_anchor: false,
          age_eligible: true,
          above_member_ceiling: true,
          consistency_above_floor: true,
        },
        tier: 'guarded',
        dissolution_risk: 0.4,
        would_dissolve: false,
      },
      emergence: {
        threshold: 0.6,
        threshold_formula: 'max(0.40, 0.60 - 0.004 * members)',
        min_member_count: 5,
        total_opts: 20,
        top_candidate: null,
        gap_to_threshold: null,
        ready: false,
        blocked_reason: 'no_candidates',
        runner_ups: [],
        tier: 'inert',
      },
      computed_at: '2026-04-17T00:00:00Z',
      ...overrides,
    };
  }

  beforeEach(() => {
    readinessStore._reset();
    domainStore._reset();
    domainStore.domains = [
      { id: 'd1', label: 'backend', color_hex: '#b44aff', member_count: 0, avg_score: null, source: 'seed' },
    ];
  });

  it('sets readinessTier on domain SceneNode when a matching report exists', () => {
    readinessStore.reports = [makeReadinessReport({ domain_id: 'd1' })];
    readinessStore.loaded = true;

    const tree = [makeNode({ id: 'd1', state: 'domain', domain: 'backend' })];
    const { nodes } = buildSceneData(tree);
    const domainNode = nodes.find((n) => n.id === 'd1');
    expect(domainNode?.readinessTier).toBe('guarded');
  });

  it('leaves readinessTier undefined on non-domain nodes', () => {
    readinessStore.reports = [makeReadinessReport({ domain_id: 'd1' })];
    readinessStore.loaded = true;

    const tree = [makeNode({ id: 'd1', state: 'active', domain: 'backend' })];
    const { nodes } = buildSceneData(tree);
    expect(nodes.find((n) => n.id === 'd1')?.readinessTier).toBeUndefined();
  });

  it('leaves readinessTier undefined when no matching readiness report exists', () => {
    const tree = [makeNode({ id: 'd1', state: 'domain', domain: 'backend' })];
    const { nodes } = buildSceneData(tree);
    expect(nodes.find((n) => n.id === 'd1')?.readinessTier).toBeUndefined();
  });

  it('decorates sub-domain nodes (state=domain parented to another domain) when a report exists', () => {
    // Sub-domains are `state="domain"` nodes whose parent is also a domain.
    // The decoration gate is `state === 'domain'`, so sub-domains receive
    // a tier when the readiness store has a matching report — the ring
    // consumer in SemanticTopology applies the same gate.
    readinessStore.reports = [
      makeReadinessReport({ domain_id: 'd1' }),
      makeReadinessReport({ domain_id: 'sub1', domain_label: 'backend: auth' }),
    ];
    readinessStore.loaded = true;

    const tree = [
      makeNode({ id: 'd1', state: 'domain', domain: 'backend' }),
      makeNode({ id: 'sub1', state: 'domain', domain: 'backend', parent_id: 'd1' }),
    ];
    const { nodes } = buildSceneData(tree);
    const subDomain = nodes.find((n) => n.id === 'sub1');
    expect(subDomain?.isSubDomain).toBe(true);
    expect(subDomain?.readinessTier).toBe('guarded');
  });

  it('keys reports by domain node id, not by label or position', () => {
    // Guards against a regression where an unrelated report leaks onto a
    // different domain node. Only the node whose id matches report.domain_id
    // should receive the decoration.
    readinessStore.reports = [makeReadinessReport({ domain_id: 'other' })];
    readinessStore.loaded = true;

    const tree = [makeNode({ id: 'd1', state: 'domain', domain: 'backend' })];
    const { nodes } = buildSceneData(tree);
    expect(nodes.find((n) => n.id === 'd1')?.readinessTier).toBeUndefined();
  });

  it('leaves readinessTier undefined when the readiness store is unloaded', () => {
    // Explicit invariant: an unloaded store (loaded=false, reports=[]) must
    // not crash and must leave the tier absent — the UI degrades to the
    // pre-readiness visualization until the first snapshot arrives.
    expect(readinessStore.loaded).toBe(false);
    expect(readinessStore.reports).toHaveLength(0);

    const tree = [makeNode({ id: 'd1', state: 'domain', domain: 'backend' })];
    const { nodes } = buildSceneData(tree);
    expect(nodes.find((n) => n.id === 'd1')?.readinessTier).toBeUndefined();
  });

  it('updates SceneNode readinessTier when readinessStore reports change', () => {
    // SSE-driven re-decoration contract: when the readiness store mutates
    // between calls (e.g. a taxonomy_changed SSE event triggers a refresh
    // that upgrades a domain from guarded→ready), a subsequent buildSceneData
    // call must reflect the new tier without any extra wiring. The reactive
    // chain flows through readinessStore.reports → byId derived lookup →
    // buildSceneData decoration on each invocation.
    readinessStore.reports = [
      makeReadinessReport({
        domain_id: 'd1',
        stability: {
          consistency: 0.3,
          dissolution_floor: 0.15,
          hysteresis_creation_threshold: 0.6,
          age_hours: 72,
          min_age_hours: 48,
          member_count: 20,
          member_ceiling: 5,
          sub_domain_count: 0,
          total_opts: 20,
          guards: {
            general_protected: false,
            has_sub_domain_anchor: false,
            age_eligible: true,
            above_member_ceiling: true,
            consistency_above_floor: true,
          },
          tier: 'guarded',
          dissolution_risk: 0.4,
          would_dissolve: false,
        },
        emergence: {
          threshold: 0.6,
          threshold_formula: 'max(0.40, 0.60 - 0.004 * members)',
          min_member_count: 5,
          total_opts: 20,
          top_candidate: null,
          gap_to_threshold: null,
          ready: false,
          blocked_reason: 'no_candidates',
          runner_ups: [],
          tier: 'inert',
        },
      }),
    ];
    readinessStore.loaded = true;

    const tree = [makeNode({ id: 'd1', state: 'domain', domain: 'backend' })];
    let { nodes } = buildSceneData(tree);
    expect(nodes.find((n) => n.id === 'd1')?.readinessTier).toBe('guarded');

    // Mutate the store — simulates an SSE-driven refresh where the emergence
    // tier has flipped from inert → ready. A fresh buildSceneData call must
    // pick up the new tier.
    readinessStore.reports = [
      makeReadinessReport({
        domain_id: 'd1',
        stability: {
          consistency: 0.3,
          dissolution_floor: 0.15,
          hysteresis_creation_threshold: 0.6,
          age_hours: 72,
          min_age_hours: 48,
          member_count: 20,
          member_ceiling: 5,
          sub_domain_count: 0,
          total_opts: 20,
          guards: {
            general_protected: false,
            has_sub_domain_anchor: false,
            age_eligible: true,
            above_member_ceiling: true,
            consistency_above_floor: true,
          },
          tier: 'guarded',
          dissolution_risk: 0.4,
          would_dissolve: false,
        },
        emergence: {
          threshold: 0.6,
          threshold_formula: 'max(0.40, 0.60 - 0.004 * members)',
          min_member_count: 5,
          total_opts: 20,
          top_candidate: null,
          gap_to_threshold: null,
          ready: true,
          blocked_reason: null,
          runner_ups: [],
          tier: 'ready',
        },
      }),
    ];

    ({ nodes } = buildSceneData(tree));
    expect(nodes.find((n) => n.id === 'd1')?.readinessTier).toBe('ready');
  });

  it('drops readinessTier when the composed tier is not a known enum value (schema drift guard)', () => {
    // Defense against backend schema drift: if a future backend version
    // returns an unknown stability tier (e.g. a newly-added "unstable"
    // state), `composeReadinessTier()` passes it through unchanged. Without
    // a runtime guard, the topology ring renderer calls
    // `readinessTierColor(tier)` which returns `undefined` and Three.js
    // fails silently. The build step must drop the tier (leave undefined)
    // so the ring is omitted rather than drawn with a broken color.
    const malformed = makeReadinessReport({
      domain_id: 'd1',
      stability: {
        consistency: 0.3,
        dissolution_floor: 0.15,
        hysteresis_creation_threshold: 0.6,
        age_hours: 72,
        min_age_hours: 48,
        member_count: 20,
        member_ceiling: 5,
        sub_domain_count: 0,
        total_opts: 20,
        guards: {
          general_protected: false,
          has_sub_domain_anchor: false,
          age_eligible: true,
          above_member_ceiling: true,
          consistency_above_floor: true,
        },
        // Simulate schema drift: new tier the frontend enum doesn't know.
        tier: 'unstable' as unknown as 'healthy',
        dissolution_risk: 0.4,
        would_dissolve: false,
      },
    });
    readinessStore.reports = [malformed];
    readinessStore.loaded = true;

    const tree = [makeNode({ id: 'd1', state: 'domain', domain: 'backend' })];
    const { nodes } = buildSceneData(tree);
    expect(nodes.find((n) => n.id === 'd1')?.readinessTier).toBeUndefined();
  });
});
