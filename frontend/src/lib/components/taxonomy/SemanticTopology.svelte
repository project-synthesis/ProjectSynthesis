<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { readinessStore } from '$lib/stores/readiness.svelte';
  import { topologyCache } from '$lib/stores/topology-cache.svelte';

  import { TopologyRenderer, type LODTier } from './TopologyRenderer';
  import { buildSceneData, assignLodVisibility, buildNodeMap, computeHierarchicalOpacity, type SceneData, type SceneNode } from './TopologyData';
  import { TopologyInteraction } from './TopologyInteraction';
  import { TopologyLabels } from './TopologyLabels';
  import { cleanupScene } from './scene-cleanup';
  import { settleForces } from './TopologyWorker';
  import TopologyControls from './TopologyControls.svelte';
  import ActivityPanel from './ActivityPanel.svelte';
  import SeedModal from './SeedModal.svelte';
  // Pattern Graph hint card is built into TopologyControls (inline, no separate component)
  import * as THREE from 'three';
  import { triggerRecluster } from '$lib/api/clusters';
  import { addToast } from '$lib/stores/toast.svelte';
  import { stateColor, HIGHLIGHT_COLOR_HEX, SIMILARITY_EDGE_COLOR_HEX } from '$lib/utils/colors';
  import { parsePrimaryDomain } from '$lib/utils/formatting';
  import type { ClusterNode } from '$lib/api/clusters';
  import { BeamPool } from './BeamPool';
  import { ClusterPhysics } from './ClusterPhysics';
  import { EnvelopePool } from './EnvelopePool';
  import { AnimationCoordinator } from './AnimationCoordinator';
  import { ImpactCoordinator } from './ImpactCoordinator';
  // Sub-project D — 5 single-responsibility builders + typed context.
  // Each builder implements SceneBuilder (build + dispose); shared per-
  // rebuild state flows through BuilderContext explicitly.
  import { ClusterBuilder } from './builders/ClusterBuilder';
  import { DomainBuilder } from './builders/DomainBuilder';
  import { EdgeBuilder, type EdgeOpacityResolvers } from './builders/EdgeBuilder';
  import { RingBuilder } from './builders/RingBuilder';
  import { DustBuilder } from './builders/DustBuilder';
  import { createBuilderContext } from './builders/BuilderContext';

  // ── Per-frame scratch table ──────────────────────────────────────
  // Per spec § 3.5: animation callbacks borrow these instances via
  // `.set()` / `.copy()` mutation instead of allocating fresh THREE
  // primitives every frame. The runtime invariant ("zero THREE allocs
  // per frame") is enforced by `perf-budget.test.ts`. The brand
  // reference at `.claude/skills/brand-guidelines/references/3d-visualization.md`
  // documents this pattern under "Per-Frame Allocation Budget".
  //
  // **Forward-defense status (cycle 3 follow-up)**: at present every
  // animation callback in this file (readiness billboard `lookAt`,
  // domain rotation `rotation.y +=`, formation lerp on
  // `n.position[0..2]`, `BeamPool.update` reusing `_origin` /
  // `_ndcOrigin`, ring LOD opacity sweep) operates entirely on existing
  // members or scalar math — no `new THREE.X()` per frame. The runtime
  // gate in `perf-budget.test.ts` proves this for `BeamPool.update` and
  // `ClusterPhysics.update`. The scratch table is therefore canonical
  // brand infrastructure that future per-frame code MUST borrow from
  // (rather than each file inventing its own scratch primitives).
  //
  // Borrow rules:
  //   - `_scratchVec3a` may be mutated by any callback as a transient.
  //     Treat the value as **invalid after the same callback returns**
  //     — no caching.
  //   - `Z_AXIS` is read-only. Never `.set()` or `.copy()` it. Used
  //     by `rotateOnAxis` callers that need a constant +Z axis.
  // Sub-project D — `_scratchQuat` + `_scratchColor` migrated into
  // builder-private scratch tables (the pre-extraction consumers are
  // gone from this file).
  const _scratchVec3a = new THREE.Vector3();
  const Z_AXIS = new THREE.Vector3(0, 0, 1);

  // Sub-project D — `TweenHandle` interface + `tweenRingColor` helper +
  // `prefersReducedMotion` + `_CUBIC` migrated to RingBuilder.ts. The
  // readiness-ring tier-tween RAF chain is now owned by RingBuilder.

  // Resolved at module level to avoid per-frame allocations
  const HIGHLIGHT_COLOR = parseInt(HIGHLIGHT_COLOR_HEX.replace('#', ''), 16);
  const EDGE_COLOR = parseInt(stateColor('archived').replace('#', ''), 16);
  const SIMILARITY_EDGE_COLOR = parseInt(SIMILARITY_EDGE_COLOR_HEX.replace('#', ''), 16);
  const INJECTION_EDGE_COLOR = 0xff9500; // warm gold/amber

  // Sub-project D — Ring radius/thickness/segments constants migrated
  // to RingBuilder.ts. `READINESS_RING_OPACITY_FACTOR` stays here because
  // the LOD-tier opacity callback (orchestrator-owned) reads it.
  const READINESS_RING_OPACITY_FACTOR = 0.9;

  /** LOD-tier → absolute ring opacity. At far camera distances the ring is a
   *  ghost (0.4), at mid it is half-weight (0.7), at near it is fully lit (1.0).
   *  Written each frame by the LOD animation callback registered in `onMount`,
   *  which is the final per-frame authority on ring opacity (supersedes the
   *  dim-sweep `$effect` that runs on rebuild / highlight change). Hoisted
   *  to module scope so the tier map and `READINESS_RING_OPACITY_FACTOR`
   *  sit side-by-side for future readers. */
  const READINESS_LOD_OPACITY: Record<LODTier, number> = {
    far: 0.4,
    mid: 0.7,
    near: 1.0,
  };

  /** Multiplicative opacity applied to nodes that do NOT match the currently
   *  highlighted domain (see `clustersStore.highlightedDomain`). Consumed by
   *  two sweeps in the same `$effect`: the per-domain-group dodecahedron
   *  materials loop, and the scene-root readiness-ring loop. Kept at module
   *  scope so the two sweeps cannot drift apart. */
  const DOMAIN_DIM_FACTOR = 0.15;

  // Sub-project D — Template ring pool migrated to RingBuilder. The pool
  // infrastructure (template-ring pool / free-list / id-map / set /
  // group, pool helpers create/ensure/acquire/release/sync) lives inside
  // RingBuilder.ts. The dev-only template-ring-pool hook is re-published
  // by RingBuilder.build.

  /** Predicate — shared between scene-build loop and DOM marker `{#each}`.
   *  Centralizes the "this node gets a readiness ring" rule so the two
   *  surfaces can never drift. */
  function hasReadinessRing(node: SceneNode): boolean {
    return node.state === 'domain' && node.readinessTier != null;
  }

  // Sub-project D — buildEdgeGroup helper + similarity/injection group
  // construction migrated to EdgeBuilder.ts (per-rebuild similarity +
  // injection groups acquired via EdgeBuilder.getSimilarityGroup() /
  // getInjectionGroup() accessors). The curve helpers
  // buildCurvePositions / buildMergedCurveGeometry STAY at module scope
  // because the formation animation rebuild loop (post-formation
  // completion) reuses them to repaint hierarchical edges at settled
  // positions. EdgeBuilder owns its own private copies for the initial
  // hierarchical build — duplication is acceptable; the formation rebuild
  // is a separate concern from per-rebuildScene edge construction.
  const EDGE_PROXIMITY_THRESHOLD = 5.0;
  const CURVE_SEGMENTS = 12;
  interface HierEdge {
    from: [number, number, number];
    to: [number, number, number];
    memberCount?: number;
  }

  // Opacity lookup caches — rebuilt per rebuildScene call. Read by the
  // EdgeOpacityResolvers injected into EdgeBuilder.
  let _simScoreCache: Map<string, number> | null = null;
  let _injWeightCache: { map: Map<string, number>; max: number } | null = null;

  let canvas: HTMLCanvasElement;
  let container: HTMLDivElement;
  let renderer: TopologyRenderer | null = null;
  let interaction: TopologyInteraction | null = null;
  let labels: TopologyLabels | null = null;
  let sceneData = $state<SceneData | null>(null);

  let lodTier = $state<LODTier>('far');
  let focusedNodeId = $state<string | null>(null);
  let hoveredNodeId = $state<string | null>(null);
  let seedModalOpen = $state(false);

  // Node meshes for raycasting
  let nodeMeshes: Map<string, THREE.Mesh> = new Map();

  // Sub-project D — Readiness ring registry migrated to RingBuilder.ts.
  // `ReadinessRingEntry` interface + `_readinessRings` map +
  // `_readinessRingGroup` + ring helpers (`buildRingGeometry`,
  // `updateRingFrameInputs`, `updateExistingRing`, `disposeRingEntry`)
  // all live in RingBuilder.ts. The per-frame billboard handle stays at
  // module scope because it's an orchestrator-owned canceller for the
  // per-frame readiness-billboard registration.
  let _removeReadinessBillboard: (() => void) | null = null;

  // ── Atmospheric layer state (canon F5/F8/F10) ────────────────────
  // Per-frame animation handles + accumulators for the cinematic layers
  // documented in `references/3d-visualization.md`:
  //   F5 — hierarchical edges pulse (uTime uniform driven by _removeEdgeAnim)
  //   F8 — organic breathing oscillation on every cluster mesh + ring
  //   F10 — Neural Dust ambient particle backdrop (3000-point galaxy)
  // All three callbacks are registered through the AnimationCoordinator
  // (phase 'ambient' for F5 + F10, phase 'breathing' for F8) in rebuildScene
  // and torn down by coordinator.dispose() in the unmount cleanup return
  // (see canon "Animation Tick Ordering").
  let _edgeUniforms: Record<string, THREE.IUniform>[] = [];
  // _removeEdgeAnim, _removeDustAnim, _breathingAnim — per-rebuild unsubscribe
  // handles for the AnimationCoordinator-registered atmospheric callbacks
  // (canon F5/F8/F10). rebuildScene cancels before re-registering so the
  // handler array doesn't grow across rebuilds. Cleanup happens through
  // coordinator.dispose() in the unmount return; these handles are NOT
  // re-invoked there (the test #6 source-grep would catch a duplicate
  // canceller).
  let _removeEdgeAnim: (() => void) | null = null;
  let _edgeTime = 0;

  // Sub-project D — `_dustPoints` migrated to DustBuilder.ts. Per-frame
  // dust drift reads `dustBuilder.dustPoints()` (rev-2 accessor).
  let _removeDustAnim: (() => void) | null = null;
  let _breathingAnim: (() => void) | null = null;
  let _breathingTime = 0;

  // T1.1 — Per-node phase offset for desynchronized organic breathing.
  // Seeded from a deterministic string hash so the same node always gets
  // the same phase (stable across rebuilds). Populated during rebuildScene
  // by ClusterBuilder + DomainBuilder (writes ctx.nodePhaseOffsets) +
  // synced to this module-level ref after all builders complete.
  let _nodePhaseOffsets: Map<string, number> = new Map();

  // Sub-project D — Per-cluster ShaderMaterial refs synced from
  // ctx.clusterShaderMaterials after ClusterBuilder.build(). Consumed by
  // the physics handler for ripple-uniform writes (replaces the prior
  // `group.children[1]` index walk — locks INT-6 architecture
  // improvement).
  let _clusterShaderMaterials: Map<string, THREE.ShaderMaterial> = new Map();

  // Sub-project D — Ring helpers + canon F2 glow texture migrated to
  // builders. RingBuilder.ts owns the readiness ring lifecycle +
  // `buildRingGeometry`/`updateExistingRing`/`disposeRingEntry`. The
  // `_glowTextureBuilt` sentinel is no longer needed — DomainBuilder
  // owns the lazy glow-texture construction.

  // Sub-project D — 5 scene builders constructed in onMount after
  // renderer + AnimationCoordinator + ImpactCoordinator + interaction.
  // Disposed in cleanup return between coordinator.dispose() + pool
  // disposes. ClusterBuilder + DomainBuilder receive the `interaction`
  // instance as a constructor dep so raycast-target registration
  // co-locates with mesh construction.
  let clusterBuilder: ClusterBuilder | null = null;
  let domainBuilder: DomainBuilder | null = null;
  let edgeBuilder: EdgeBuilder | null = null;
  let ringBuilder: RingBuilder | null = null;
  let dustBuilder: DustBuilder | null = null;

  // Flat node lookup for mid-LOD label logic and domain highlight
  let flatNodeMap: Map<string, ClusterNode> = new Map();

  // Beam pool + cluster physics state
  let beamPool: BeamPool | null = null;
  let clusterPhysics: ClusterPhysics | null = null;
  // AnimationCoordinator — owns all per-frame handler dispatch in 5 phases
  // (impact → physics → breathing → ambient → camera). Instantiated in
  // onMount after the renderer, disposed in cleanup return. Absorbs 8 of
  // the 10 canonical canceller symbols (envelope, flash, beam+physics,
  // ring-LOD, domain rotation, edge anim, dust anim, breathing). Two
  // conditional cancellers (formation + readiness billboard) remain as
  // module-scope `let` for self-unregister + per-rebuild lifecycle.
  let coordinator: AnimationCoordinator | null = null;
  // Envelope pool — plasma engulfment burst at beam impact (canon F19).
  // Acquired inside the ImpactCoordinator's `fire(...)` onImpact callback
  // (post-Sub-project-C) so the envelope ignites at the moment the beam
  // visually arrives at the target rather than synchronously with the
  // click. Disposed in the cleanup return below.
  let envelopePool: EnvelopePool | null = null;
  // Impact coordinator — Sub-project C single source for the canon
  // F7→F19→F9 impact chain. Owns the engulfment-marker set + the T3.4
  // idle ambient pulse. All 4 trigger sites (entrance, post-growth,
  // optimization, click) route through the coordinator's fire method;
  // the previous module-scope helper for the beam-impact router is
  // gone. Instantiated in onMount after beam/envelope/physics pools,
  // disposed FIRST in the cleanup return per spec acceptance #10.
  let impactCoordinator: ImpactCoordinator | null = null;

  // Emissive engulfment state — per-node MeshStandardMaterial.emissiveIntensity
  // burst at beam impact. Mirrors the 3-phase EnvelopePool lifecycle so the
  // inner glow (emissive) and outer plasma skin (envelope) rise, hold, and
  // dissolve as one continuous effect — the node is engulfed from both inside
  // and outside simultaneously.
  //
  // Timeline (matches EnvelopePool exactly):
  //   attack (120ms): cubic ease-out ramp baseline → peak (avoids jarring jump)
  //   hold   (580ms): sustained at peak — node glows while beam is connected
  //   decay  (680ms): cubic ease-out decay peak → baseline — dissolves with beam
  // Total: 1380ms (= ATTACK_MS + HOLD_MS + DECAY_MS from EnvelopePool.ts)
  const GLOW_ATTACK_MS = 120;
  const GLOW_HOLD_MS = 580;
  const GLOW_DECAY_MS = 680;
  const GLOW_TOTAL_MS = GLOW_ATTACK_MS + GLOW_HOLD_MS + GLOW_DECAY_MS; // 1380ms
  // Additive delta on top of the node's baseline emissive.
  // Additive (not multiplicative) to stay within the bloom pass's designed
  // headroom: a 4× multiplier on a high-score cluster (base ~1.4) would reach
  // 5.6 — deep into white-out territory. An absolute delta of +1.6 gives
  // a strong surge on domains (0.4→2.0) and a decisive punch on clusters
  // (0.6→2.2) without overdriving the bloom pass.
  const GLOW_PEAK_DELTA = 1.6;
  // Domain emissive color is stored per-glow so the burst always fires in the
  // node's brand color even if the highlight system has swapped mat.emissive to
  // HIGHLIGHT_COLOR (neon cyan). On completion, the correct emissive is decided
  // by a live _highlightedId check — NOT a prevEmissive snapshot, which would be
  // stale if the user switched nodes during the 1380ms glow window.
  const _flashStates = new Map<string, {
    startTime: number;
    baselineEmissive: number;
    // Attack-phase start value. Defaults to `max(currentEmissiveIntensity,
    // baselineEmissive)` so the flash ramp climbs UP from whatever the
    // current displayed value is (preventing the "dim flash" frame when
    // the idle pulse had elevated emissive above baseline). On flash
    // completion, the value lands at peak then decays back to baseline
    // — the asymmetry between attack-start and decay-end is intentional
    // and produces the operator-spec'd "star-forming" rise + fluid
    // dissipation.
    startIntensity: number;
    domainEmissive: THREE.Color;  // node brand color — applied to mat.emissive during burst
  }>();

  // T3.3 — Beam Impact Camera Micro-Shake
  let _cameraShake = 0;

  let _hasPlayedEntrance = false;
  // One-shot auto-focus guard (canon F17). The bird's-eye-view "frame the
  // largest domain" zoom must run EXACTLY ONCE for the component lifecycle.
  // Without this guard, every async stateFilter mutation triggers
  // rebuildScene which would re-trigger the auto-focus block, snapping the
  // camera back to distance 60 and overriding the user's current view.
  let _hasAutoFocused = false;
  let _beamNodeGroups: Map<string, THREE.Group> = new Map();
  // Previous-selection tracker so the selection $effect's engulfment-gate
  // clear is keyed off ACTUAL selection-change transitions, not every
  // reactive re-trigger of the effect (sceneData rebuilds, focusedNodeId
  // self-writes, etc.). Without this, mid-flight scene rebuilds would
  // clear the engulfment marker set between the beam impact and the next
  // idle-pulse frame — observed as "only one specific node gets the full
  // effect" because only nodes selected during quiescent scenes survived.
  // Post-Sub-project-C the engulfment-marker set itself lives on the
  // ImpactCoordinator (see `impactCoordinator.clearEngulfed()`); this
  // module-scope tracker stays per spec §4.3 (Sub-project E absorbs it later).
  let _prevSelectedId: string | null = null;
  let _sceneNodeMap: Map<string, import('./TopologyData').SceneNode> = new Map();
  let _prevNodeSizes: Map<string, number> = new Map();
  let _seedBatchActive = false;
  // _removeDomainRotation — per-rebuild unsubscribe handle for the
  // coordinator-registered ambient domain rotation. See _removeEdgeAnim
  // comment above for the rebuild-lifecycle rationale.
  let _removeDomainRotation: (() => void) | null = null;
  let _removeFormationAnim: (() => void) | null = null;

  // Persisted edge grouping — shared between rebuildScene and formation rebuild
  let _edgesByParent: Map<string, HierEdge[]> = new Map();

  // External highlight tracking (for family selection sync)
  let _highlightedId: string | null = null;
  let _highlightedColor: number | null = null;

  /** Restore previous highlight color and apply neon cyan to a new node.
   *  Cluster fill meshes use `MeshStandardMaterial` (per brand reference
   *  "Material Recipes"). The highlight swaps both `material.color` AND
   *  `material.emissive` — without the emissive flip, the matte surface
   *  would read cyan but the lit emission would still emit the original
   *  domain hex, producing a desaturated mismatch under the directional
   *  light. Spec § 3.2 (emissive setHex on highlight). */
  function applyHighlight(nodeId: string): void {
    // Restore previous
    if (_highlightedId && _highlightedId !== nodeId) {
      const prev = nodeMeshes.get(_highlightedId);
      if (prev && _highlightedColor !== null) {
        const m = prev.material as THREE.MeshStandardMaterial;
        m.color.setHex(_highlightedColor);
        m.emissive.setHex(_highlightedColor);
      }
    }
    const mesh = nodeMeshes.get(nodeId);
    if (!mesh) {
      _highlightedId = null;
      _highlightedColor = null;
      return;
    }
    const m = mesh.material as THREE.MeshStandardMaterial;
    _highlightedColor = m.color.getHex();
    _highlightedId = nodeId;
    m.color.setHex(HIGHLIGHT_COLOR);
    m.emissive.setHex(HIGHLIGHT_COLOR);
  }

  /** Clear any active highlight, restoring the original color + emission. */
  function clearHighlight(): void {
    if (_highlightedId) {
      const prev = nodeMeshes.get(_highlightedId);
      if (prev && _highlightedColor !== null) {
        const m = prev.material as THREE.MeshStandardMaterial;
        m.color.setHex(_highlightedColor);
        m.emissive.setHex(_highlightedColor);
      }
    }
    _highlightedId = null;
    _highlightedColor = null;
  }

  /**
   * Trigger emissive engulfment glow on a node at beam impact.
   *
   * `domainColor` is the node's brand color from the SceneNode data — always
   * the organic domain hue, never the highlight cyan. It is applied to
   * `mat.emissive` during the glow so the inner burst radiates the correct
   * brand color even when the highlight system has already swapped the emissive
   * to HIGHLIGHT_COLOR. The prior emissive is snapshot-ed and restored on
   * glow completion so the cyan highlight persists correctly afterwards.
   */
  function flashEmissive(nodeId: string, domainColor: THREE.Color): void {
    const mesh = nodeMeshes.get(nodeId);
    if (!mesh) return;
    const mat = mesh.material as THREE.MeshStandardMaterial;
    const existing = _flashStates.get(nodeId);
    // MUST use the true baseEmissive cached on the mesh, otherwise rapid refires
    // while the node is pulsing (idlePulse) will compound the pulse into the baseline.
    const trueBase = (mesh.userData.baseEmissive as number) ?? mat.emissiveIntensity;
    const baseline = existing ? existing.baselineEmissive : trueBase;
    // Anti-dip guard: capture the CURRENT displayed emissiveIntensity as
    // the attack-phase start. If the idle pulse (or a prior incomplete
    // flash) elevated the visible emissive above the data-driven
    // baseline, the attack ramp should start FROM that elevated value
    // and climb UP to the peak — NOT snap-cut back down to baseline
    // first. Pre-fix the `_tickFlashStates` attack formula was
    // `baselineEmissive + (peak - baselineEmissive) * ease` which dipped
    // emissive to `baselineEmissive` on impact, producing the operator-
    // observed "becomes flat when the beam hits" frame. New behavior:
    // start from `attackStart = max(currentIntensity, baselineEmissive)`,
    // ramp to peak. Reuses existing `startIntensity` field added to the
    // state record.
    const startIntensity = existing
      ? existing.startIntensity
      : Math.max(mat.emissiveIntensity, baseline);
    _flashStates.set(nodeId, {
      startTime: performance.now(),
      baselineEmissive: baseline,
      startIntensity,
      domainEmissive: domainColor.clone(),
    });
    // Apply domain color immediately so there is no one-frame cyan blip at attack onset.
    mat.emissive.copy(domainColor);
  }

  /**
   * Per-frame advance for every active emissive engulfment glow.
   * Mirrors the 3-phase EnvelopePool state machine (attack → hold → decay)
   * so the inner glow and outer envelope are always in phase.
   */
  function _tickFlashStates(now: number): void {
    if (_flashStates.size === 0) return;
    for (const [nodeId, state] of _flashStates) {
      const mesh = nodeMeshes.get(nodeId);
      if (!mesh) {
        _flashStates.delete(nodeId);
        continue;
      }
      const mat = mesh.material as THREE.MeshStandardMaterial;
      const elapsed = now - state.startTime;
      const peak = state.baselineEmissive + GLOW_PEAK_DELTA;

      if (elapsed >= GLOW_TOTAL_MS) {
        // Glow complete. Determine the correct emissive color by live-checking
        // the highlight state — NOT from a prevEmissive snapshot which may be
        // stale if the user clicked a different node during the 1380ms glow window.
        // applyHighlight restores the old node to domain color when switching nodes,
        // so a stale "cyan" snapshot would incorrectly re-apply highlight to a node
        // that is no longer selected.
        if (_highlightedId === nodeId) {
          mat.emissive.setHex(HIGHLIGHT_COLOR);
        } else {
          // Not selected: domain color is already correct (tick drives it every frame).
          mat.emissive.copy(state.domainEmissive);
        }
        mat.emissiveIntensity = state.baselineEmissive;
        _flashStates.delete(nodeId);
        continue;
      }

      // Keep domain color active throughout the glow so rapid re-fires or
      // concurrent highlight changes cannot re-inject cyan during the burst.
      mat.emissive.copy(state.domainEmissive);

      if (elapsed < GLOW_ATTACK_MS) {
        // Attack: cubic ease-out ramp `startIntensity` → peak. Ramping from
        // the current displayed value (captured at acquire time) — not the
        // data-driven baseline — eliminates the operator-reported "becomes
        // flat when the beam hits" dip when the idle pulse had already
        // lifted emissive above baseline before impact.
        const t = elapsed / GLOW_ATTACK_MS;
        const ease = 1 - Math.pow(1 - t, 3);
        mat.emissiveIntensity = state.startIntensity + (peak - state.startIntensity) * ease;
      } else if (elapsed < GLOW_ATTACK_MS + GLOW_HOLD_MS) {
        // Hold: node radiates at full engulfment peak while beam is connected.
        mat.emissiveIntensity = peak;
      } else {
        // Decay: cubic ease-out from peak → baseline, in sync with envelope dissolve.
        const t = (elapsed - GLOW_ATTACK_MS - GLOW_HOLD_MS) / GLOW_DECAY_MS;
        const ease = 1 - Math.pow(1 - t, 3);
        mat.emissiveIntensity = peak + (state.baselineEmissive - peak) * ease;
      }
    }
  }

  /** Set opacity on an edge material — handles both LineBasicMaterial and ShaderMaterial. */
  function setEdgeOpacity(obj: THREE.LineSegments, value: number): void {
    const mat = obj.material as any;
    if (mat.uniforms?.uBaseOpacity) {
      mat.uniforms.uBaseOpacity.value = value;
    } else {
      mat.opacity = value;
    }
  }

  /** Merge multiple curved edges into a single geometry's position + index arrays.
   *  Used by both initial rebuildScene and formation animation rebuild. */
  function buildMergedCurveGeometry(edges: HierEdge[]): { positions: number[]; indices: number[]; alphas: number[] } {
    const positions: number[] = [];
    const indices: number[] = [];
    const alphas: number[] = [];
    // T2.3 — Compute max memberCount across siblings for normalization.
    let maxMembers = 1;
    for (const e of edges) {
      if (e.memberCount != null && e.memberCount > maxMembers) maxMembers = e.memberCount;
    }
    let offset = 0;
    for (let i = 0; i < edges.length; i++) {
      const cp = buildCurvePositions(edges[i].from, edges[i].to, i, edges.length);
      // Per-vertex alpha: normalized member count → [0.3, 1.0].
      // Edges to heavier children render brighter; lighter children dimmer.
      // Floor of 0.3 prevents edges from vanishing entirely.
      const memberAlpha = edges[i].memberCount != null
        ? 0.3 + 0.7 * (edges[i].memberCount! / maxMembers)
        : 1.0;
      for (let j = 0; j < cp.length; j++) positions.push(cp[j]);
      // Each curve has CURVE_SEGMENTS + 1 vertices — fill alpha for each.
      for (let j = 0; j <= CURVE_SEGMENTS; j++) alphas.push(memberAlpha);
      for (let j = 0; j < CURVE_SEGMENTS; j++) indices.push(offset + j, offset + j + 1);
      offset += CURVE_SEGMENTS + 1;
    }
    return { positions, indices, alphas };
  }

  /** Build curved edge geometry from start→end with a perpendicular arc.
   *  The midpoint is offset perpendicular to the edge direction, creating
   *  a gentle arc. `arcIndex` and `arcTotal` spread siblings into a fan. */
  function buildCurvePositions(
    start: [number, number, number],
    end: [number, number, number],
    arcIndex: number,
    arcTotal: number,
  ): Float32Array {
    const positions = new Float32Array((CURVE_SEGMENTS + 1) * 3);

    // Midpoint
    const mx = (start[0] + end[0]) / 2;
    const my = (start[1] + end[1]) / 2;
    const mz = (start[2] + end[2]) / 2;

    // Edge direction
    const dx = end[0] - start[0];
    const dy = end[1] - start[1];
    const dz = end[2] - start[2];
    const len = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;

    // Perpendicular offset — cross product with up vector (0,1,0).
    // If edge is near-vertical, fallback to right vector (1,0,0).
    let px = -dz;   // dy*0 - dz*1
    let py = 0;     // dz*0 - dx*0
    let pz = dx;    // dx*1 - dy*0
    let pLen = Math.sqrt(px * px + py * py + pz * pz);
    if (pLen < 0.001) {
      // Edge is near-vertical — cross with right vector (1,0,0) instead
      // cross((dx,dy,dz), (1,0,0)) = (0, dz, -dy)
      px = 0;
      py = dz;
      pz = -dy;
      pLen = Math.sqrt(py * py + pz * pz) || 1;
    }
    px /= pLen; py /= pLen; pz /= pLen;

    // Fan offset: spread siblings apart. Center index = 0 offset.
    const spread = arcTotal > 1 ? (arcIndex - (arcTotal - 1) / 2) / arcTotal : 0;
    const arcMagnitude = len * 0.15 + spread * len * 0.2;

    const ctrlX = mx + px * arcMagnitude;
    const ctrlY = my + py * arcMagnitude;
    const ctrlZ = mz + pz * arcMagnitude;

    // Quadratic bezier: B(t) = (1-t)²·start + 2(1-t)t·ctrl + t²·end
    for (let i = 0; i <= CURVE_SEGMENTS; i++) {
      const t = i / CURVE_SEGMENTS;
      const t1 = 1 - t;
      positions[i * 3]     = t1 * t1 * start[0] + 2 * t1 * t * ctrlX + t * t * end[0];
      positions[i * 3 + 1] = t1 * t1 * start[1] + 2 * t1 * t * ctrlY + t * t * end[1];
      positions[i * 3 + 2] = t1 * t1 * start[2] + 2 * t1 * t * ctrlZ + t * t * end[2];
    }

    return positions;
  }

  /**
   * Per-frame breathing callback — registered through coordinator's
   * `breathing` phase. Composes:
   *   T3.3 — Beam impact camera micro-shake
   *   T1.1 — Per-node phase offset for desynchronized breathing
   *   T2.4 — Hover proximity field amplification
   *   T3.4 — Idle pulse engulfed-guard (Sub-project C §3.4 hazard)
   *   F4   — Readiness ring sync (via RingBuilder accessor)
   *   F3   — Template ring opacity + rotation (via RingBuilder accessor)
   *
   * Extracted from rebuildScene body so the orchestrator stays under the
   * 150 LOC ceiling per spec §6.1 #3.
   */
  function _advanceBreathing(): void {
    _breathingTime += 0.016;

    // T3.3 — Beam impact camera micro-shake.
    if (_cameraShake > 0.001) {
      if (renderer?.camera) {
        renderer.camera.rotation.x += (Math.random() - 0.5) * _cameraShake;
        renderer.camera.rotation.y += (Math.random() - 0.5) * _cameraShake;
      }
      _cameraShake *= 0.85;
    } else {
      _cameraShake = 0;
    }

    for (const [nodeId, mesh] of nodeMeshes) {
      const node = _sceneNodeMap.get(nodeId);
      if (!node) continue;

      // T1.1 — Per-node phase offset for desynchronized breathing.
      const phase = _nodePhaseOffsets.get(nodeId) ?? 0;
      const scaleBase = Math.sin((_breathingTime + phase) * 1.5) * 0.02 + 1.0;

      // T2.4 — Hover proximity field.
      const PROXIMITY_RADIUS = 8.0;
      const isHovered = (hoveredNodeId === nodeId);
      let proximityFactor = 0;
      if (!isHovered && hoveredNodeId) {
        const hoveredNode = _sceneNodeMap.get(hoveredNodeId);
        if (hoveredNode) {
          const dx = node.position[0] - hoveredNode.position[0];
          const dy = node.position[1] - hoveredNode.position[1];
          const dz = node.position[2] - hoveredNode.position[2];
          const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
          if (dist < PROXIMITY_RADIUS) {
            const t = 1 - dist / PROXIMITY_RADIUS;
            proximityFactor = t * t * 0.6;
          }
        }
      }
      const hoverAmplification = isHovered ? 1.1 : (1.0 + 0.1 * proximityFactor);
      const targetScaleMultiplier = scaleBase * hoverAmplification;
      const targetScale = node.size * targetScaleMultiplier;

      mesh.scale.lerp(_scratchVec3a.set(targetScale, targetScale, targetScale), 0.1);

      const parentGroup = mesh.parent;
      if (parentGroup) {
        for (const child of parentGroup.children) {
          if (child !== mesh && child.type !== 'Group') {
            child.scale.copy(mesh.scale);
          }
        }
      }

      // Readiness ring (canon F4) — read via RingBuilder accessor.
      const readyEntry = ringBuilder?.getReadinessRing(nodeId);
      if (readyEntry) {
        if (isHovered) {
          readyEntry.mesh.rotateOnAxis(Z_AXIS, 0.02);
        }
        readyEntry.mesh.scale.setScalar(targetScaleMultiplier);
      }

      // T3.4 — Idle Ambient Energy Pulse (canon)
      // Engulfed-guard preserved per Sub-project C §3.4 phase-ordering
      // hazard. The flash-state branch is a no-op (the engulfment-bug
      // regression guard); `_tickFlashStates` owns the full attack →
      // hold → decay ramp.
      const isSelected = clustersStore.selectedClusterId === nodeId;
      const mat = mesh.material as THREE.MeshStandardMaterial;
      const baseEmissive = (mesh.userData.baseEmissive as number) ?? mat.emissiveIntensity;
      if (_flashStates.has(nodeId)) {
        // No-op — `_tickFlashStates` owns the ramp.
      } else if (impactCoordinator?.isEngulfed(nodeId) && isSelected) {
        // No-op — ImpactCoordinator._tick owns the engulfed T3.4 pulse.
      } else {
        mat.emissiveIntensity = baseEmissive;
      }

      // Template ring (canon F3) — read via RingBuilder accessor.
      const templateRing = ringBuilder?.getTemplateRing(nodeId);
      if (templateRing) {
        const ringMat = templateRing.material as THREE.MeshBasicMaterial;
        if (isHovered) {
          templateRing.rotation.z += 0.02;
          ringMat.opacity = 0.35 + Math.sin(_breathingTime * 10.0) * 0.15;
        } else {
          ringMat.opacity = 0.35;
        }
        templateRing.scale.setScalar(targetScaleMultiplier);
      }
    }
  }

  function rebuildScene(data: SceneData): void {
    if (!renderer) return;
    if (!coordinator) return;
    if (!clusterBuilder || !domainBuilder || !edgeBuilder || !ringBuilder || !dustBuilder) return;

    // ── 1. Pre-cleanup state clearing ─────────────────────────────
    interaction?.clear();
    labels?.clear();
    nodeMeshes.clear();
    clearHighlight();
    _simScoreCache = null;
    _injWeightCache = null;
    // Unsubscribe the billboard FIRST so it cannot fire against a
    // half-disposed mesh (use-after-free guard).
    _removeReadinessBillboard?.();
    _removeReadinessBillboard = null;

    // ── 2. cleanupScene — Sub-project A flag-aware wipe ──────────
    // Readiness-ring pruning is absorbed into RingBuilder.build per
    // spec §3.6 step 2 NOTE + Cycle 4 GREEN (Task 17).
    cleanupScene(renderer.scene);

    // ── 3. Pre-populate ctx — sceneNodeMap must exist BEFORE any
    //       builder runs (EdgeBuilder + RingBuilder read it) ──────
    const ctx = createBuilderContext();
    for (const node of data.nodes) ctx.sceneNodeMap.set(node.id, node);

    // ── 4. Sub-project D — 5 builders in dependency order ────────
    clusterBuilder.build(data, renderer.scene, ctx);
    domainBuilder.build(data, renderer.scene, ctx);
    edgeBuilder.build(data, renderer.scene, ctx);
    ringBuilder.build(data, renderer.scene, ctx);
    dustBuilder.build(data, renderer.scene, ctx);

    // ── 5. Sync ctx → module-level for downstream consumers ──────
    nodeMeshes = ctx.nodeMeshes;
    _beamNodeGroups = ctx.beamNodeGroups;
    _edgeUniforms = ctx.edgeUniforms;
    _nodePhaseOffsets = ctx.nodePhaseOffsets;
    _clusterShaderMaterials = ctx.clusterShaderMaterials;

    // ── 6. _sceneNodeMap rebuild ─────────────────────────────────
    // Required by formation animation, hover-proximity field, beam
    // targeting — all of which outlive rebuildScene.
    _sceneNodeMap = buildNodeMap(data.nodes);

    // ── 7. _edgesByParent rebuild ────────────────────────────────
    // Required by formation animation post-completion. Reads bucketed
    // hierarchical edges by parent id. Persists across rebuilds.
    _edgesByParent = new Map<string, HierEdge[]>();
    for (const edge of data.edges) {
      if (edge.type !== 'hierarchical') continue;
      const from = _sceneNodeMap.get(edge.from);
      const to = _sceneNodeMap.get(edge.to);
      if (!from || !to) continue;
      if (edge.distance != null && edge.distance < EDGE_PROXIMITY_THRESHOLD) continue;
      let bucket = _edgesByParent.get(edge.from);
      if (!bucket) {
        bucket = [];
        _edgesByParent.set(edge.from, bucket);
      }
      bucket.push({ from: from.position, to: to.position, memberCount: to.memberCount });
    }

    // ── 8. Per-rebuild coordinator registrations ─────────────────
    // Five callbacks that outlive any single builder. Each unsubscribes
    // the prior registration before re-registering so handler arrays
    // don't accumulate across rebuilds.
    const camera = renderer.camera;
    if (ringBuilder.readinessRingCount() > 0) {
      _removeReadinessBillboard = coordinator.register('camera', () => {
        if (!camera?.position) return;
        for (const id of ringBuilder!.readinessRingIds()) {
          const entry = ringBuilder!.getReadinessRing(id);
          if (entry) entry.mesh.lookAt(camera.position);
        }
      });
    }
    _removeDomainRotation?.();
    _removeDomainRotation = coordinator.register('ambient', () => {
      for (const g of ctx.domainGroups) {
        g.rotation.y += 0.002;
      }
    });
    _removeEdgeAnim?.();
    _removeEdgeAnim = coordinator.register('ambient', () => {
      _edgeTime += 0.016;
      for (const u of _edgeUniforms) {
        if (u.uTime) u.uTime.value = _edgeTime;
      }
    });
    _removeDustAnim?.();
    _removeDustAnim = coordinator.register('ambient', () => {
      // DustBuilder.dustPoints() returns the live THREE.Points or null
      // (rev-2 accessor). Canon F10 ambient rotation — preserved verbatim
      // from the pre-extraction handler.
      const dust = dustBuilder!.dustPoints();
      if (dust) {
        dust.rotation.y += 0.0003;
        dust.rotation.x += 0.0001;
      }
    });
    _breathingAnim?.();
    _breathingAnim = coordinator.register('breathing', _advanceBreathing);

    // ── 9. Label visibility (LOD-driven) ─────────────────────────
    if (labels) {
      const visibleNodes = data.nodes.filter((n) => n.visible);
      const alwaysShowLabels = visibleNodes.length <= 8;
      const templateSprites: import('three').Sprite[] = [];
      const isMid = !alwaysShowLabels && lodTier === 'mid';
      const truncLabel = (text: string) =>
        isMid && text.length > 14 ? text.slice(0, 14).trimEnd() + '…' : text;
      for (const node of data.nodes) {
        if (!node.visible) continue;
        const sprite = labels.getOrCreate(node.id, truncLabel(node.label), node.color);
        sprite.position.set(node.position[0], node.position[1] + node.size + 0.5, node.position[2]);
        if (node.state === 'template') templateSprites.push(sprite);
      }
      if (alwaysShowLabels || lodTier === 'near') {
        labels.setVisible(true);
      } else if (isMid) {
        const midLabelIds = new Set(
          visibleNodes
            .filter(
              (n) =>
                n.state === 'template' ||
                n.state === 'domain' ||
                n.state === 'project' ||
                (flatNodeMap.get(n.id)?.member_count ?? 0) >= 5,
            )
            .map((n) => n.id),
        );
        labels.setVisibleFor(midLabelIds);
      } else {
        labels.setVisible(false);
      }
      for (const sprite of templateSprites) sprite.visible = true;
    }

    // ── 10. clusterPhysics base-scale reconciliation ─────────────
    // Speculative accretion growth may have drifted from real member
    // counts; setBaseScale snaps physics back to the authoritative data.
    if (clusterPhysics) {
      for (const node of data.nodes) {
        if (node.state !== 'domain' && node.state !== 'project') {
          clusterPhysics.setBaseScale(node.id, node.size);
        }
      }
    }

    // ── 11. Highlight survival (canon F16) ───────────────────────
    // Re-apply cyan highlight if a focusedNodeId exists so it survives
    // async rebuildScene mutations (stateFilter changes that re-fire
    // the $effect rebuild loop).
    if (focusedNodeId) {
      applyHighlight(focusedNodeId);
    }
  }

  function handleLodChange(tier: LODTier): void {
    lodTier = tier;
    if (!sceneData) return;
    assignLodVisibility(sceneData.nodes, tier);

    // Click-zoom bug fix: when the focus animation crosses an LOD tier
    // boundary (FAR→MID at distance 50, MID→NEAR at distance 15), this
    // callback fires mid-animation. The previous implementation called
    // `rebuildScene(sceneData)` here, which disposed every cluster mesh
    // and rebuilt them while the camera was lerping — visible to the
    // user as a "reset and zoom again" artifact.
    //
    // The lightweight path: just toggle each existing mesh group's
    // .visible flag. The per-frame readiness-ring LOD opacity callback
    // (registered against the coordinator's camera phase in onMount)
    // already reads renderer.lodTier each frame, so ring opacity
    // transitions automatically.
    //
    // Full rebuildScene is only needed when a node became visible in
    // the new tier but has no mesh in nodeMeshes (e.g., a candidate
    // that was filtered out at FAR tier becoming visible at MID).
    let needsFullRebuild = false;
    for (const node of sceneData.nodes) {
      const mesh = nodeMeshes.get(node.id);
      if (mesh?.parent) {
        (mesh.parent as THREE.Group).visible = node.visible;
      } else if (node.visible) {
        // Node is now visible but has no mesh — fall back to full rebuild
        // (rare; happens crossing into higher-LOD where new clusters
        // become visible).
        needsFullRebuild = true;
      }
    }
    if (needsFullRebuild) {
      rebuildScene(sceneData);
    }
  }

  function handleNodeClick(nodeId: string): void {
    // Canon F15: only call selectCluster — the $effect watching
    // clustersStore.selectedClusterId drives every visual update
    // (focusOn, applyHighlight, tactile feedback). Doing the work here
    // too duplicates effort and creates race conditions with the
    // async getClusterDetail fetch.
    clustersStore.selectCluster(nodeId);
    // F10: Switch navigator to clusters tab so user sees the selection context
    window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'clusters' }));
  }

  function handleAscend(): void {
    if (focusedNodeId && sceneData) {
      const current = sceneData.nodes.find(n => n.id === focusedNodeId);
      if (current?.parentId) {
        // Canon F15: route through selectCluster, let the $effect drive.
        clustersStore.selectCluster(current.parentId);
      } else {
        // Back to overview
        clustersStore.selectCluster(null);
      }
    }
  }

  function handleSearch(query: string): void {
    if (!sceneData) return;
    const lowerQuery = query.toLowerCase();
    const match = sceneData.nodes.find(n =>
      n.label.toLowerCase().includes(lowerQuery),
    );
    if (match) {
      interaction?.highlightNode(match.id);
      // Canon F15: selectCluster drives applyHighlight + focusOn + tactile
      // feedback via the $effect.
      clustersStore.selectCluster(match.id);
    }
  }

  async function handleRecluster(): Promise<void> {
    try {
      const result = await triggerRecluster();
      if (result.status === 'skipped') {
        addToast('modified', 'Recluster skipped — taxonomy cycle in progress');
        return;
      }
      if (result.status === 'rejected') {
        addToast('deleted', 'Recluster rejected — quality gate failed');
        return;
      }
      await clustersStore.loadTree();
      addToast('created', `Recluster complete — ${result.nodes_created ?? 0} created, ${result.nodes_updated ?? 0} updated`);
    } catch (err) {
      console.error('Recluster failed:', err);
      addToast('deleted', 'Recluster failed');
    }
  }

  // Watch for taxonomy tree changes — untrack the write to sceneData
  // to prevent effect_update_depth_exceeded (reads tree, writes sceneData).
  // Topology graph uses the FULL taxonomy tree. buildSceneData() excludes archived
  // nodes and dims non-matching nodes based on stateFilter (highlight+dim pattern).
  // Reading stateFilter here ensures the $effect re-runs when tabs switch.
  $effect(() => {
    const tree = clustersStore.taxonomyTree;
    const filter = clustersStore.stateFilter;
    // Touch readiness state so this effect re-runs when reports mutate.
    // `buildSceneData` reads `readinessStore.byDomain(...)` inside `untrack`
    // below, which hides the dependency from Svelte's tracker.
    void readinessStore.reports;
    void readinessStore.loaded;
    if (tree.length > 0 && renderer) {
      untrack(() => {
        // Coordinator narrowing for the bare coordinator-register literal
        // used below (formation lerp). Coordinator is unconditionally created
        // in onMount before the renderer guard above resolves to truthy, so
        // this guard is purely a TypeScript narrowing affordance.
        if (!coordinator) return;
        flatNodeMap = new Map(tree.map(n => [n.id, n]));
        sceneData = buildSceneData(tree, clustersStore.similarityEdges, clustersStore.injectionEdges, filter);
        assignLodVisibility(sceneData.nodes, lodTier);

        // Build semantic relationship data for the force simulation
        const nodeCount = sceneData.nodes.length;
        const positions = new Float32Array(nodeCount * 3);
        const sizes = new Float32Array(nodeCount);
        sceneData.nodes.forEach((n, i) => {
          positions[i * 3] = n.position[0];
          positions[i * 3 + 1] = n.position[1];
          positions[i * 3 + 2] = n.position[2];
          sizes[i] = n.size;
        });

        // Parent index array: maps each node to its parent's array index
        const nodeIndexMap = new Map(sceneData.nodes.map((n, i) => [n.id, i]));
        const parentIndices = new Int32Array(nodeCount);
        parentIndices.fill(-1);
        for (let i = 0; i < nodeCount; i++) {
          const pid = sceneData.nodes[i].parentId;
          if (pid) parentIndices[i] = nodeIndexMap.get(pid) ?? -1;
        }

        // Domain group array: same domain string → same integer ID
        const domainToGroup = new Map<string, number>();
        const domainGroups = new Int32Array(nodeCount);
        let nextGroup = 0;
        for (let i = 0; i < nodeCount; i++) {
          const fn = flatNodeMap.get(sceneData.nodes[i].id);
          const dom = fn?.domain ?? 'general';
          const primary = dom.includes(':') ? dom.split(':')[0].trim().toLowerCase() : dom.toLowerCase();
          if (!domainToGroup.has(primary)) domainToGroup.set(primary, nextGroup++);
          domainGroups[i] = domainToGroup.get(primary)!;
        }

        // UMAP rest positions (copy before force modification)
        const restPositions = new Float32Array(positions);

        const fingerprint = topologyCache.computeFingerprint(sceneData.nodes.map(n => n.id));
        let settledPositions: Float32Array;

        const cached = topologyCache.get(fingerprint);
        if (cached) {
          settledPositions = cached;
        } else {
          const settled = settleForces({
            positions, restPositions, sizes,
            parentIndices, domainGroups,
            iterations: 60,
          });
          settledPositions = settled.positions;
          topologyCache.set(fingerprint, settledPositions);
        }

        // Galaxy formation gate: cache-miss = first time seeing this node-set
        // (initial mount, or structural change via taxonomy_changed SSE that
        // added/removed clusters). Cache-hit = same node-set, only a scalar
        // property changed (stateFilter, readinessStore.reports). Subsequent
        // rebuilds MUST NOT re-run the formation animation — it disrupts the
        // user's view, and most concretely it produces the "click a node →
        // zoom in → LOD reset → zoom in second time" artifact when the
        // animation fires mid-focus (cross-filter clicks mutate stateFilter
        // in `_loadClusterDetail`, re-firing this $effect within the ~600ms
        // focus window).
        if (!cached) {
          // Start all nodes collapsed at origin for galaxy formation
          sceneData.nodes.forEach((n, i) => {
            const radius = Math.random() * 2.0;
            const theta = Math.random() * Math.PI * 2;
            const phi = Math.acos((Math.random() * 2) - 1);
            n.position = [
              radius * Math.sin(phi) * Math.cos(theta),
              radius * Math.sin(phi) * Math.sin(theta),
              radius * Math.cos(phi)
            ];
          });

          rebuildScene(sceneData);

          // Hide edges during formation to prevent visual clutter
          renderer?.scene.traverse((obj) => {
            if (obj.userData?.isInterClusterEdgeGroup) obj.visible = false;
          });
          // Sub-project D — similarity/injection groups accessed via
          // EdgeBuilder accessors instead of module-scope refs.
          const simGroup = edgeBuilder?.getSimilarityGroup();
          if (simGroup) simGroup.visible = false;
          const injGroup = edgeBuilder?.getInjectionGroup();
          if (injGroup) injGroup.visible = false;

          // Galaxy Formation Animation Loop (Lerp to Settled Positions)
          // Capture in const for TypeScript narrowing inside the closure
          const formSceneData = sceneData;
          let formProgress = 0.0;
          const formDuration = 90.0; // frames
          const initialPositions = new Float32Array(nodeCount * 3);
          const nodeDelays = new Float32Array(nodeCount);
          sceneData.nodes.forEach((n, i) => {
             initialPositions[i*3] = n.position[0];
             initialPositions[i*3+1] = n.position[1];
             initialPositions[i*3+2] = n.position[2];
             
             // T3.2 Stagger formation by depth
             const finalX = settledPositions[i*3];
             const finalY = settledPositions[i*3+1];
             const finalZ = settledPositions[i*3+2];
             const dist = Math.sqrt(finalX*finalX + finalY*finalY + finalZ*finalZ);
             // Further nodes start later (max delay ~40 frames)
             nodeDelays[i] = Math.min(dist * 0.5, 40.0);
          });

          _removeFormationAnim?.();
          // Formation lerp — runs in the `ambient` phase per spec §3.5.
          // Coordinator narrowing is established at the top of the untrack
          // arrow above; the bare coordinator-register literal here is
          // what the source-grep contract (cleanup-contract.test.ts #5) pins.
          // The local `removeFormation` const anchors the source-grep test
          // "galaxy formation animation is gated on cache miss" which
          // matches the bareword `removeFormation` (not the underscore
          // prefixed module-scope handle) followed by an ambient-phase
          // registration call. Both bindings hold the same unsubscribe
          // handle.
          const removeFormation = coordinator.register('ambient', () => {
             formProgress += 1.0;

             formSceneData.nodes.forEach((n, i) => {
                const delay = nodeDelays[i];
                const elapsed = Math.max(0, formProgress - delay);
                
                // cubic ease-out
                const t = Math.min(elapsed / (formDuration - delay), 1.0);
                const easeT = 1 - Math.pow(1 - t, 3);
                n.position[0] = initialPositions[i*3] + (settledPositions[i*3] - initialPositions[i*3]) * easeT;
                n.position[1] = initialPositions[i*3+1] + (settledPositions[i*3+1] - initialPositions[i*3+1]) * easeT;
                n.position[2] = initialPositions[i*3+2] + (settledPositions[i*3+2] - initialPositions[i*3+2]) * easeT;

                const group = _beamNodeGroups.get(n.id);
                if (group) group.position.set(...n.position);

                if (labels) {
                   const sprite = labels.getOrCreate(n.id, n.label, n.color);
                   sprite.position.set(n.position[0], n.position[1] + n.size + 0.5, n.position[2]);
                }

                // Pre-fix bug: template ring + readiness ring meshes were positioned ONCE
                // by ``rebuildScene`` which ran BEFORE this formation animation.
                // At that moment all nodes were origin-collapsed, so the rings
                // were placed at origin.  This callback then animated each
                // ``n.position`` toward its settled target without syncing the
                // decorations, leaving rings pinned at origin while clusters
                // flew outward.  Visible until LOD-change → ``rebuildScene``
                // re-sync (e.g. user zoom — which is exactly the symptom
                // observed: "I have to zoom in or out for the rings to
                // position themselves in the correct order and clusters").
                // Sub-project D — ring meshes accessed via RingBuilder
                // accessors instead of module-scope maps.
                const templateRing = ringBuilder?.getTemplateRing(n.id);
                if (templateRing) templateRing.position.set(n.position[0], n.position[1], n.position[2]);
                const ringEntry = ringBuilder?.getReadinessRing(n.id);
                if (ringEntry) {
                  ringEntry.mesh.position.set(n.position[0], n.position[1], n.position[2]);
                }
             });

             if (formProgress >= formDuration) {
                _removeFormationAnim?.();
                _removeFormationAnim = null;

                // Re-enable hierarchical edges — rebuild curves from settled positions.
                // Uses _edgesByParent (persisted from rebuildScene) to avoid re-scanning
                // all edges. Node positions were lerped to settled values above, so
                // _sceneNodeMap positions are already at their final locations.
                renderer?.scene.traverse((obj) => {
                  if (obj.userData?.isInterClusterEdgeGroup) {
                    for (const child of (obj as THREE.Group).children) {
                      const ls = child as THREE.LineSegments;
                      const parentId = ls.userData?.parentId as string | undefined;
                      if (!parentId) continue;
                      const edges = _edgesByParent.get(parentId);
                      if (!edges || edges.length === 0) continue;
                      const { positions, indices, alphas } = buildMergedCurveGeometry(edges);
                      ls.geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
                      ls.geometry.setAttribute('aAlpha', new THREE.Float32BufferAttribute(alphas, 1));
                      ls.geometry.setIndex(indices);
                    }
                    obj.visible = true;
                  }
                });
                // Sub-project D — similarity/injection groups accessed
                // via EdgeBuilder accessors.
                const simGroupAfter = edgeBuilder?.getSimilarityGroup();
                if (simGroupAfter) simGroupAfter.visible = clustersStore.showSimilarityEdges;
                const injGroupAfter = edgeBuilder?.getInjectionGroup();
                if (injGroupAfter) injGroupAfter.visible = clustersStore.showInjectionEdges;
             }
          });
          // Mirror the local const onto the module-scope handle so the
          // cleanup return + the self-unregister branch above (formation
          // completion / cache-hit cancel) can release the registration.
          _removeFormationAnim = removeFormation;
        } else {
          // Cache hit — same node-set as a prior build. Place each node
          // directly at its settled position and rebuild the scene without
          // animation. rebuildScene paints rings, labels, and beam groups
          // off `n.position`, so static placement here gives them correct
          // settled coordinates with no formation-animation post-processing.
          //
          // If a prior formation animation is still running (e.g., a fast
          // SSE re-fire during initial mount), cancel it — its closure-captured
          // `initialPositions` no longer matches the current node positions.
          _removeFormationAnim?.();
          _removeFormationAnim = null;

          sceneData.nodes.forEach((n, i) => {
            n.position = [
              settledPositions[i * 3],
              settledPositions[i * 3 + 1],
              settledPositions[i * 3 + 2],
            ];
          });

          rebuildScene(sceneData);
        }

        // Auto-focus on the largest domain cluster on initial load (canon F17).
        // _hasAutoFocused is a one-shot guard — every subsequent rebuildScene
        // (triggered by stateFilter mutation, taxonomy_changed, etc.) skips
        // this block and preserves the user's current camera position.
        if (!_hasAutoFocused && !focusedNodeId && sceneData.nodes.length > 0) {
          _hasAutoFocused = true;
          const domainSizes = new Map<string, { count: number; cx: number; cy: number; cz: number }>();
          for (const n of sceneData.nodes) {
            if (n.state === 'domain' || !n.visible) continue;
            const dom = (flatNodeMap.get(n.id)?.domain ?? 'general').split(':')[0].trim().toLowerCase();
            const entry = domainSizes.get(dom) ?? { count: 0, cx: 0, cy: 0, cz: 0 };
            entry.count++;
            // Focus on settled target, not start
            const idx = nodeIndexMap.get(n.id)!;
            entry.cx += settledPositions[idx * 3];
            entry.cy += settledPositions[idx * 3 + 1];
            entry.cz += settledPositions[idx * 3 + 2];
            domainSizes.set(dom, entry);
          }
          let bestDomain = '';
          let bestCount = 0;
          for (const [dom, entry] of domainSizes) {
            if (entry.count > bestCount) { bestCount = entry.count; bestDomain = dom; }
          }
          if (bestDomain && bestCount > 0) {
            const entry = domainSizes.get(bestDomain)!;
            const cx = entry.cx / entry.count;
            const cy = entry.cy / entry.count;
            const cz = entry.cz / entry.count;
            renderer?.focusOn(new THREE.Vector3(cx, cy, cz), 60, 1500); // Slower pan to match formation
          }
        }

        // Entrance beams — materialization burst on first mount
        if (!_hasPlayedEntrance && beamPool && sceneData.nodes.length > 0) {
          _hasPlayedEntrance = true;
          const sorted = [...sceneData.nodes]
            .filter(n => n.state === 'domain')
            .sort((a, b) => b.size - a.size);
            
          sorted.forEach((node, i) => {
            setTimeout(() => {
              const group = _beamNodeGroups.get(node.id);
              if (!group || !impactCoordinator) return;

              // Spec §5.3 M3 Row 1 — entrance burst routes through the
              // coordinator. TRIGGER_PRESETS.entrance owns sustainMs +
              // sizeFactor scaling + kineticDisplacement=false.
              impactCoordinator.fire({ trigger: 'entrance', node, group });
            }, i * 150);
          });
        }

        // Fire beams at clusters that grew (post-seed only)
        const isSeedBatch = _seedBatchActive;
        if (isSeedBatch && _prevNodeSizes.size > 0 && beamPool && renderer) {
          let firedCount = 0;
          for (const node of sceneData.nodes) {
            if (node.state !== 'domain') continue; // Only for domain nodes
            
            const prevSize = _prevNodeSizes.get(node.id);
            if (prevSize !== undefined && node.size > prevSize) {
              const group = _beamNodeGroups.get(node.id);
              if (group) {
                setTimeout(() => {
                  if (!impactCoordinator) return;
                  // Spec §5.3 M3 Row 1 — post-growth burst routes through the
                  // coordinator. TRIGGER_PRESETS['post-growth'] owns the
                  // thickness multiplier + extended sustain + kineticDisplacement=true.
                  impactCoordinator.fire({ trigger: 'post-growth', node, group });
                }, firedCount * 120);
                firedCount++;
              }
            }
          }
          _prevNodeSizes.clear();
        }
        // Always clear seed flag after tree rebuild — prevents contaminating
        // future optimize beams if seed batch had no detectable growth
        if (isSeedBatch) _seedBatchActive = false;
      });
    }
  });

  // Similarity edge visibility toggle (Sub-project D — accessor)
  $effect(() => {
    const show = clustersStore.showSimilarityEdges;
    const simGroup = edgeBuilder?.getSimilarityGroup();
    if (simGroup) simGroup.visible = show;
  });

  // Injection edge visibility toggle (Sub-project D — accessor)
  $effect(() => {
    const show = clustersStore.showInjectionEdges;
    const injGroup = edgeBuilder?.getInjectionGroup();
    if (injGroup) injGroup.visible = show;
  });

  // Optimization event listener — fire immediate beam to assigned cluster
  $effect(() => {
    if (!beamPool || !renderer) return;
    function onOptimization(e: Event) {
      // Only fire on actual optimization completions — ignore feedback/failure
      const detail = (e as CustomEvent).detail;
      if (detail?.status !== 'completed') return;
      
      // Real-time organic synthetization: fire beam immediately to domain
      const targetDomain = detail.domain ? parsePrimaryDomain(detail.domain) : 'general';
      const targetNode = sceneData?.nodes.find(n => n.state === 'domain' && n.domain === targetDomain);
      
      if (targetNode && impactCoordinator) {
        const group = _beamNodeGroups.get(targetNode.id);
        if (group) {
          // Spec §5.3 M3 Row 1 — optimization event routes through the
          // coordinator. TRIGGER_PRESETS.optimization owns the
          // fixed sustain + kineticDisplacement=true (Data-as-Matter spec).
          impactCoordinator.fire({ trigger: 'optimization', node: targetNode, group });
        }
      }
    }
    window.addEventListener('optimization-event', onOptimization);
    return () => window.removeEventListener('optimization-event', onOptimization);
  });

  // Seed batch tracking — flag active seed AND snapshot sizes on first event.
  // Individual optimization_created events may not fire during batch seeding
  // (bulk persist model), so we snapshot here.
  $effect(() => {
    if (!beamPool || !renderer) return;
    function onSeedProgress(e: Event) {
      const detail = (e as CustomEvent).detail;
      if (!detail) return;
      // Only snapshot once per batch (first event)
      if (!_seedBatchActive) {
        _prevNodeSizes.clear();
        for (const [id, node] of _sceneNodeMap) {
          _prevNodeSizes.set(id, node.size);
        }
      }
      _seedBatchActive = true;
    }
    window.addEventListener('seed-batch-progress', onSeedProgress);
    return () => window.removeEventListener('seed-batch-progress', onSeedProgress);
  });

  // Domain highlight dimming — when a domain is highlighted in the navigator,
  // dim all non-matching nodes and edges. Restores original opacities on clear.
  $effect(() => {
    const highlightDomain = clustersStore.highlightedDomain;
    if (!renderer || !sceneData) return;

    for (const node of sceneData.nodes) {
      if (!node.visible) continue;
      const mesh = nodeMeshes.get(node.id);
      if (!mesh) continue;
      const group = mesh.parent as THREE.Group | null;
      if (!group) continue;

      // `node.domain` on `SceneNode` is already `parsePrimaryDomain`-normalized
      // at build time (see TopologyData.ts) — no need to re-parse per sweep.
      const dimmed = highlightDomain != null && node.domain !== highlightDomain;
      const dimFactor = dimmed ? DOMAIN_DIM_FACTOR : 1.0;

      // Apply dim factor to all materials in the group.
      // Cluster: fill (0.9) + wire (coherence-based). Domain: fill (0.9) + edges (0.9) + points (0.95).
      const isStructural = group.userData?.isStructural === true;
      for (let i = 0; i < group.children.length; i++) {
        const child = group.children[i];
        // Fill meshes are `MeshStandardMaterial` (cluster + domain anchor) per
        // brand reference; points are PointsMaterial. Both cluster wireframe
        // and domain structural edges use ShaderMaterial (with uOpacity uniform)
        // — handled by the explicit `isShaderMaterial && uniforms?.uOpacity`
        // guard below, which `continue`s before the `mat.opacity =` assignment.
        // The cast widening here is safe because that downstream guard catches
        // the ShaderMaterial branch; the cast just keeps the `.opacity` access
        // type-checked for the PointsMaterial/Standard cases.
        const mat = (child as THREE.Mesh | THREE.LineSegments | THREE.Points).material as
          THREE.MeshStandardMaterial | THREE.LineBasicMaterial | THREE.PointsMaterial;
        if (!mat) continue;
        let baseOpacity: number;
        if (i === 0) {
          baseOpacity = node.opacity * 0.9;              // fill (both types)
        } else if (isStructural) {
          baseOpacity = node.opacity * (i === 2 ? 0.95 : 0.9); // edges or points
        } else {
          baseOpacity = node.opacity * (0.5 + 0.5 * node.coherence); // cluster wire (coherence)
        }
        // Handle ShaderMaterial (cluster wireframe ripple + domain edge heartbeat)
        if ((mat as any).isShaderMaterial && (mat as any).uniforms?.uOpacity) {
          (mat as any).uniforms.uOpacity.value = baseOpacity * dimFactor;
          continue;
        }
        mat.opacity = baseOpacity * dimFactor;
      }
    }

    // Readiness rings are parented to the SCENE ROOT, not the domain group,
    // so the per-group sweep above misses them. Mirror the dim semantics here
    // using the SAME match predicate as the dodecahedron sweep — comparing
    // the ring's owning node's primary domain to `highlightDomain`, not its
    // node id. `highlightedDomain` is set to a primary-domain string (e.g.
    // 'backend') by ClusterNavigator; id-matching would leave every ring
    // dimmed whenever any domain is highlighted, including its own. Iterating
    // `sceneData.nodes` with an O(1) `_readinessRings.get(id)` lookup mirrors
    // the dodecahedron sweep's structure and keeps the shared
    // `DOMAIN_DIM_FACTOR` as the single source of truth.
    for (const node of sceneData.nodes) {
      // Sub-project D — ring entries accessed via RingBuilder accessor.
      const ring = ringBuilder?.getReadinessRing(node.id);
      if (!ring) continue;
      const dimmed =
        highlightDomain != null && node.domain !== highlightDomain;
      const ringDimFactor = dimmed ? DOMAIN_DIM_FACTOR : 1.0;
      // First-frame paint: set opacity so the ring looks correct immediately
      // after rebuild/highlight-change, without waiting for the next animation
      // tick (~<16ms gap). The LOD animation callback registered in `onMount`
      // is the per-frame authority and uses the full composition formula:
      //   opacity = LOD_OPACITY[tier] * node.opacity
      //           * READINESS_RING_OPACITY_FACTOR * dimFactor
      // This $effect omits the LOD factor (treated as 1.0) because the
      // rebuild/highlight path doesn't know the current tier — the LOD tick
      // corrects it on the very next frame. Keep `entry.domain` and
      // `entry.nodeOpacity` fresh so the LOD callback sees the latest inputs
      // when a highlight change lands between rebuild and the next tick.
      ring.domain = node.domain;
      ring.nodeOpacity = node.opacity;
      ring.material.opacity =
        node.opacity * READINESS_RING_OPACITY_FACTOR * ringDimFactor;
    }

    // Dim all edge types (preserve domain node EdgesGeometry outlines)
    const dimActive = highlightDomain != null;
    renderer.scene.traverse((obj) => {
      if (!(obj instanceof THREE.LineSegments)) return;
      const ud = obj.userData;
      if (ud?.isInterClusterEdge) {
        const base = (ud.baseOpacity as number) ?? 0.4;
        setEdgeOpacity(obj, dimActive ? base * 0.25 : base);
      } else if (ud?.isSimilarityEdge || ud?.isInjectionEdge) {
        const mat = obj.material as THREE.LineBasicMaterial;
        const base = ud.baseOpacity as number;
        mat.opacity = dimActive ? base * 0.25 : base;
      }
    });
  });

  // Focus-reveal: on hover, brighten the hovered node's family edges,
  // dim everything else. On hover-clear, restore density-based opacities.
  $effect(() => {
    const hovered = hoveredNodeId;
    if (!renderer || !sceneData) return;

    // Find the hovered node's parent (for family matching)
    const hoveredNode = hovered ? sceneData.nodes.find(n => n.id === hovered) : null;
    const familyParentId = hoveredNode?.parentId ?? null;
    // If hovered node IS a domain/project, its "family" is itself as parent
    const isStructural = hoveredNode?.state === 'domain' || hoveredNode?.state === 'project';
    const activeParent = isStructural ? hovered : familyParentId;

    renderer.scene.traverse((obj) => {
      if (!(obj instanceof THREE.LineSegments)) return;
      const ud = obj.userData;
      if (!ud?.isInterClusterEdge) return;

      const base = (ud.baseOpacity as number) ?? 0.4;

      if (!hovered) {
        // No hover — restore to base opacity (or dimmed if domain highlight active)
        const dimActive = clustersStore.highlightedDomain != null;
        setEdgeOpacity(obj, dimActive ? base * 0.25 : base);
        return;
      }

      // Hover active — brighten family, dim the rest
      const edgeParent = ud.parentId as string | undefined;
      const isFamilyEdge = edgeParent != null && edgeParent === activeParent;
      setEdgeOpacity(obj, isFamilyEdge ? Math.min(base * 2.5, 0.6) : base * 0.15);
    });
  });

  // Sync external family selection → highlight node + tactile feedback (canon F18)
  $effect(() => {
    const externalId = clustersStore.selectedClusterId;

    // Clear the engulfment-gate flag ONLY when the selection actually
    // changes (deselect or switch to a different node). Pre-fix this
    // cleared on every `$effect` re-trigger — including triggers from
    // unrelated reactive deps like `sceneData` rebuilds during the
    // ~700ms beam-travel window. The result was the operator-reported
    // "only one specific node gets the full effect" symptom: that node
    // happened to be selected during a quiescent scene period and its
    // post-impact gate survived; other clicks landed mid-rebuild and
    // had their gate cleared between impact and the next idle-pulse
    // frame. `_prevSelectedId` makes the clear strictly transition-
    // triggered so re-fires of the same selection preserve the gate.
    if (externalId !== _prevSelectedId) {
      impactCoordinator?.clearEngulfed();
      _prevSelectedId = externalId;
    }

    // Deselected — restore previous highlight + return to overview camera.
    // Borrow `_scratchVec3a` for the focusOn target — `focusOn` clones the
    // target internally (TopologyRenderer.ts focusOn → endTarget.clone()),
    // so the scratch can be re-mutated immediately.
    if (!externalId) {
      clearHighlight();
      if (focusedNodeId) {
        focusedNodeId = null;
        renderer?.focusOn(_scratchVec3a.set(0, 0, 0), 80);
      }
      return;
    }

    if (!renderer || !sceneData) return;
    if (externalId === focusedNodeId) return; // already focused via click or search

    const node = sceneData.nodes.find(n => n.id === externalId);
    if (!node) return;

    applyHighlight(externalId);
    focusedNodeId = externalId;
    renderer.focusOn(
      _scratchVec3a.set(node.position[0], node.position[1], node.position[2]),
    );

    // Tactile feedback (canon F7 + F18 + F19). All impact reactions —
    // ClusterPhysics overshoot accretion, plasma envelopement burst,
    // emissive flash on the fill material — fire from the beam's
    // `onImpact` callback so they synchronize to the moment the beam
    // visually arrives at the target. Calling these synchronously with
    // `acquire()` was a pre-existing anti-causal-ordering bug: the beam
    // takes `FIRING_MS` (~700ms) to travel from FPS-weapon NDC origin to the cluster,
    // so the cluster used to ripple BEFORE being hit.
    if (impactCoordinator) {
      const group = _beamNodeGroups.get(node.id);
      if (group) {
        // Spec §5.3 M3 Row 1 — click selection routes through the
        // coordinator. TRIGGER_PRESETS.click owns the radius floor +
        // marksEngulfed=true (populates the coordinator's engulfment-marker
        // set for the T3.4 idle pulse).
        impactCoordinator.fire({ trigger: 'click', node, group });
      }
    }
  });

  onMount(() => {
    renderer = new TopologyRenderer(canvas);
    // Instantiate the AnimationCoordinator immediately after the renderer
    // and BEFORE the first per-frame registration (spec §3.2). The
    // coordinator subscribes to renderer's RAF loop via a single internal
    // addAnimationCallback subscription; its dispose() in the cleanup
    // return tears that subscription down.
    coordinator = new AnimationCoordinator(renderer);
    labels = new TopologyLabels();
    renderer.scene.add(labels.group);
    interaction = new TopologyInteraction(renderer, canvas, {
      onNodeClick: handleNodeClick,
      onNodeHover: (id) => { hoveredNodeId = id; },
      onAscend: handleAscend,
    });
    renderer.onLodChange(handleLodChange);
    renderer.start();

    // T3.3 — Beam Impact Camera Micro-Shake trigger
    const onBeamImpact = () => { _cameraShake = Math.min(_cameraShake + 0.04, 0.12); };
    window.addEventListener('beam-impact', onBeamImpact);

    // Initialize beam pool + cluster physics
    beamPool = new BeamPool();
    clusterPhysics = new ClusterPhysics();
    renderer.scene.add(beamPool.group);

    // Initialize plasma envelope pool (canon F19). Mounted as a sibling
    // of beamPool's group — both are owned by the renderer's scene and
    // disposed in the cleanup return. Per-frame update tracks node world
    // positions and advances the per-instance attack→hold→decay state
    // machine; missing-target detection ends an envelope gracefully if
    // its target node is removed mid-effect.
    envelopePool = new EnvelopePool();
    renderer.scene.add(envelopePool.group);

    // Instantiate the ImpactCoordinator AFTER all its dependencies are
    // constructed AND BEFORE the first impact-phase registration so the
    // coordinator can claim its slot in the impact phase before any other
    // handler. The coordinator owns the engulfment-marker set + the T3.4
    // idle pulse; all 4 trigger sites route through the coordinator's
    // fire method.
    // Spec §6.1 acceptance #3 + canon F7 source-level enforcement.
    impactCoordinator = new ImpactCoordinator({
      beamPool,
      envelopePool,
      clusterPhysics,
      flashEmissive,
      getSceneNode: (id) => _sceneNodeMap.get(id),
      getBeamGroup: (id) => _beamNodeGroups.get(id),
      getNodeMesh: (id) => nodeMeshes.get(id),
      getSelectedId: () => clustersStore.selectedClusterId,
      isFlashActive: (id) => _flashStates.has(id),
      renderer,
      animationCoordinator: coordinator,
    });

    // Sub-project D — 5 scene builders constructed AFTER renderer,
    // coordinator, impactCoordinator per spec §6.1 #8 + acceptance #12.
    // `interaction` is constructed earlier in onMount so ClusterBuilder +
    // DomainBuilder can inject it as a constructor dep — raycast-target
    // registration co-locates with mesh construction (rev-2 B2).
    clusterBuilder = new ClusterBuilder(interaction);
    domainBuilder = new DomainBuilder(interaction);
    // EdgeOpacityResolvers wire the clustersStore-backed opacity
    // computations + visibility flags into EdgeBuilder. Caches
    // (`_simScoreCache` / `_injWeightCache`) are nulled at rebuildScene
    // entry and lazily reconstructed per build (resolvers close over the
    // module-level refs, which are stable across rebuilds).
    const edgeResolvers: EdgeOpacityResolvers = {
      similarity: (edge) => {
        if (!_simScoreCache) {
          _simScoreCache = new Map<string, number>();
          for (const se of clustersStore.similarityEdges) {
            _simScoreCache.set(`${se.from_id}:${se.to_id}`, se.similarity);
            _simScoreCache.set(`${se.to_id}:${se.from_id}`, se.similarity);
          }
        }
        const sim = _simScoreCache.get(`${edge.from}:${edge.to}`) ?? 0.5;
        return Math.max(0.1, Math.min(0.4, 0.1 + (sim - 0.5) * 0.6));
      },
      injection: (edge) => {
        if (!_injWeightCache) {
          _injWeightCache = { map: new Map<string, number>(), max: 1 };
          for (const ie of clustersStore.injectionEdges) {
            const key = `${ie.source_id}:${ie.target_id}`;
            _injWeightCache.map.set(key, ie.weight);
            if (ie.weight > _injWeightCache.max) _injWeightCache.max = ie.weight;
          }
        }
        const w = _injWeightCache.map.get(`${edge.from}:${edge.to}`) ?? 1;
        return Math.max(0.15, Math.min(0.5, 0.15 + (w / _injWeightCache.max) * 0.35));
      },
      similarityVisible: () => clustersStore.showSimilarityEdges,
      injectionVisible: () => clustersStore.showInjectionEdges,
    };
    edgeBuilder = new EdgeBuilder(edgeResolvers);
    ringBuilder = new RingBuilder();
    dustBuilder = new DustBuilder();

    // CONSOLIDATED onMount animation registrations — strict source order
    // pins the impact-phase ordering required by spec §3.3 + cleanup-contract
    // test #4: beam.update → envelope.update → _tickFlashStates. Followed by
    // the physics-phase clusterPhysics handler (impact → physics per
    // PHASE_ORDER) and the camera-phase ring-LOD writer.
    // Beam pool tick — drives every active beam's instance offsets,
    // shader uniforms (uFade, uPulse), and impact dispatch. Fires
    // first in the impact phase so the beam's `onImpact` callback can
    // synchronize envelopes + emissive flashes downstream (spec §3.3).
    coordinator.register('impact', (delta) => {
      beamPool?.update(delta, renderer!.camera);
    });
    // Envelope pool tick — advances the per-instance plasma envelope
    // state machine (attack → hold → decay). Runs AFTER beam.update
    // so its sample of beam-impact arrival happens in the same frame
    // a beam terminates (causal-ordering invariant).
    coordinator.register('impact', (delta) => {
      envelopePool?.update(delta);
    });
    // Flash tick — per-node MeshStandardMaterial emissive ramp for
    // any active flash. Runs LAST in impact phase so its writes are
    // the final emissive values seen by the renderer this frame.
    coordinator.register('impact', () => {
      _tickFlashStates(performance.now());
    });
    coordinator.register('physics', (delta) => {
      clusterPhysics?.update(delta, (nodeId, scale, ripple) => {
        const group = _beamNodeGroups.get(nodeId);
        if (!group) return;
        for (const child of group.children) {
          child.scale.setScalar(scale);
        }
        // Sub-project D — read wire material via ctx-populated map (spec
        // §3.3 ClusterBuilder + INT-6) instead of group.children[1] index
        // walk. Locks the architecture improvement: the wire shader's
        // ripple uniform is resolved by node id from the ClusterBuilder-
        // populated map synced into module-level state by the orchestrator.
        const wireMat = _clusterShaderMaterials.get(nodeId);
        if (wireMat?.uniforms.uRipple) {
          wireMat.uniforms.uRipple.value = ripple;
        }
      });
    });

    // Task 9: LOD attenuation. The LOD callback is the FINAL opacity writer
    // per frame for readiness rings — it supersedes the dim-sweep `$effect`
    // on every tick based on `renderer.lodTier`. Registered in the `camera`
    // phase per spec §3.2 — camera phase runs after ambient so the LOD-driven
    // opacity is the last write before render. Iterating zero ring ids is
    // O(0) — safe when no rings exist (Sub-project D — RingBuilder accessor).
    coordinator.register('camera', () => {
      if (!ringBuilder) return;
      const lodFactor = READINESS_LOD_OPACITY[renderer!.lodTier];
      // NOT a reactive read: this callback runs inside requestAnimationFrame
      // via the AnimationCoordinator's single subscription to renderer's
      // tick loop, OUTSIDE any Svelte `$effect` or `$derived` tracking
      // scope.
      const highlighted = clustersStore.highlightedDomain;
      for (const id of ringBuilder.readinessRingIds()) {
        const entry = ringBuilder.getReadinessRing(id);
        if (!entry) continue;
        const dimFactor =
          highlighted != null && entry.domain !== highlighted
            ? DOMAIN_DIM_FACTOR
            : 1.0;
        entry.material.opacity =
          lodFactor * READINESS_RING_OPACITY_FACTOR * entry.nodeOpacity * dimFactor;
      }
    });

    // Taxonomy data loaded by +layout.svelte on app mount — no need to re-fetch here.
    // The $effect watching filteredTaxonomyTree (line 432) rebuilds the scene reactively.

    // Pattern Graph hint card auto-shows on first visit (handled by TopologyControls)

    // Resize observer
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      renderer?.resize(width, height);
    });
    ro.observe(container);

    return () => {
      // Sub-project C: impactCoordinator MUST dispose first so its
      // `_disposed` flag short-circuits any subsequent RAF-driven `_tick`
      // from the still-alive AnimationCoordinator. Reversed order is also
      // race-safe per Sub-project B's lenient-on-disposed contract, but
      // spec acceptance #10 (cleanup-contract.test.ts §Impact #10) pins
      // this explicit order for Sub-project E future-compatibility.
      impactCoordinator?.dispose();
      impactCoordinator = null;
      // Dispose the AnimationCoordinator next so every per-frame handler
      // it owns stops before any pool/material cleanup that follows. This
      // is the cancel-before-clear ordering invariant pinned by the source
      // grep tests (cleanup-contract test #6 + SemanticTopology tests
      // "cleanup return invokes coordinator?.dispose() before clearing
      // _flashStates" / "cleanup return calls coordinator?.dispose() +
      // envelopePool?.dispose() + nulls envelopePool"). The 8 absorbed
      // canceller calls (envelope, flash, beam+physics, ring-LOD, domain
      // rotation, edge anim, dust anim, breathing) are gone — coordinator
      // owns their lifetime via its single _removeTick subscription which
      // dispose() tears down.
      coordinator?.dispose();
      coordinator = null;

      beamPool?.dispose();
      beamPool = null;
      clusterPhysics?.clear();
      clusterPhysics = null;
      // Envelope pool — canon F19. The per-frame update was already
      // cancelled by coordinator.dispose() above; here we dispose the
      // pool itself (releases all instance materials + geometry).
      envelopePool?.dispose();
      envelopePool = null;
      // Emissive flash — restore each active flash's baseline emissive so
      // a remount doesn't inherit inflated values that would only normalize
      // on first paint (visible as a brief over-glow). The per-frame tick
      // that wrote these was cancelled by coordinator.dispose() above, so
      // _flashStates can be cleared without racing a late tick.
      for (const [nodeId, state] of _flashStates) {
        const mesh = nodeMeshes.get(nodeId);
        if (mesh) {
          const mat = mesh.material as THREE.MeshStandardMaterial;
          // Restore emissive color: live check ensures no stale cyan bleed on remount.
          if (_highlightedId === nodeId) {
            mat.emissive.setHex(HIGHLIGHT_COLOR);
          } else {
            mat.emissive.copy(state.domainEmissive);
          }
          mat.emissiveIntensity = state.baselineEmissive;
        }
      }
      _flashStates.clear();
      // Conditional cancellers — formation lerp and readiness billboard.
      // These remain as module-scope `let` because they have rebuild-scoped
      // lifecycles (formation self-unregisters on completion, billboard
      // unregisters at rebuildScene entry when ring set goes empty).
      // coordinator.dispose() above already cleared their handler arrays,
      // but invoking the captured canceller is idempotent (splice returns
      // -1 on a missing entry), keeping the pre/post-dispose-race surface
      // safe.
      _removeFormationAnim?.();
      _removeFormationAnim = null;
      _removeReadinessBillboard?.();
      _removeReadinessBillboard = null;
      // Per-rebuild handles for atmospheric (edge/dust/breathing) and
      // domain-rotation registrations were absorbed into coordinator;
      // null them here so a remount starts with fresh references.
      _removeDomainRotation = null;
      _removeEdgeAnim = null;
      _edgeUniforms = [];
      _removeDustAnim = null;
      _breathingAnim = null;

      // Canon F2 — dispose the radial-gradient CanvasTexture cache so a
      // future remount rebuilds it cleanly. DomainBuilder constructs the
      // texture on first build but the cleanup contract for the global
      // handle stays here (component-lifetime owner). Mirrors
      // cleanup-contract.test.ts canon F2 assertions.
      const glowTex = (globalThis as { __semTopGlowTexture?: THREE.CanvasTexture })
        .__semTopGlowTexture;
      if (glowTex && typeof glowTex.dispose === 'function') {
        glowTex.dispose();
      }
      (globalThis as { __semTopGlowTexture?: THREE.CanvasTexture })
        .__semTopGlowTexture = undefined;

      // Sub-project D — Builder disposes between coordinator.dispose()
      // (per-frame handlers stopped) + pool disposes (no races with beam
      // termination cleanup). RingBuilder.dispose() releases readiness +
      // template ring state (replaces the prior readiness-ring entry
      // loop + template-ring pool reset). DustBuilder.dispose() releases
      // the dust Points (replaces
      // the prior `_dustPoints` cleanup which relied on scene.traverse).
      // ClusterBuilder + DomainBuilder + EdgeBuilder dispose are mostly no-ops
      // (their groups are ephemeral; cleanupScene handles them on the next
      // rebuild) but pin the SceneBuilder contract.
      clusterBuilder?.dispose();
      clusterBuilder = null;
      domainBuilder?.dispose();
      domainBuilder = null;
      edgeBuilder?.dispose();
      edgeBuilder = null;
      ringBuilder?.dispose();
      ringBuilder = null;
      dustBuilder?.dispose();
      dustBuilder = null;
      _clusterShaderMaterials.clear();

      ro.disconnect();
      interaction?.dispose();
      labels?.dispose();
      renderer?.dispose();
    };
  });
</script>

<div class="topology-outer" class:topology-has-activity={clustersStore.activityOpen}>
<div class="topology-container" bind:this={container}>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <canvas
    bind:this={canvas}
    aria-label="Taxonomy topology visualization"
    tabindex="0"
  ></canvas>
  <!--
    Readiness-ring DOM markers — one hidden `<span>` per domain node that
    owns a readiness ring in the Three.js scene. The WebGL ring itself
    isn't queryable from jsdom, so these markers provide a parallel DOM
    surface for tests (and a11y probes) to assert on. They mirror the
    scene-build predicate via the shared `hasReadinessRing` helper so the
    two surfaces cannot drift.
  -->
  {#each sceneData?.nodes.filter(hasReadinessRing) ?? [] as node (node.id)}
    <span
      data-readiness-ring={node.id}
      data-readiness-tier={node.readinessTier}
      aria-hidden="true"
      style="display:none"
    ></span>
  {/each}
  <TopologyControls
    {lodTier}
    showActivity={clustersStore.activityOpen}
    onSearch={handleSearch}
    onRecluster={handleRecluster}
    onToggleActivity={() => clustersStore.toggleActivity()}
    onSeed={() => { seedModalOpen = true; }}
  />
  {#if seedModalOpen}
    <SeedModal bind:open={seedModalOpen} onClose={() => { seedModalOpen = false; }} />
  {/if}
  <!-- Hint card is inline in TopologyControls -->
  {#if hoveredNodeId}
    {@const hn = sceneData?.nodes.find(n => n.id === hoveredNodeId)}
    {#if hn}
      <div class="topology-tooltip" role="tooltip">
        {#if hn.state === 'project'}
          {@const domainIds = new Set(sceneData?.nodes.filter(n => n.parentId === hn.id && n.state === 'domain').map(n => n.id) ?? [])}
          {@const domainCount = domainIds.size}
          {@const clusterCount = sceneData?.nodes.filter(n => n.parentId && domainIds.has(n.parentId) && n.state !== 'domain' && n.state !== 'project').length ?? 0}
          <span class="tt-label">{hn.label.includes('/') ? hn.label.split('/').pop() : hn.label}</span>
          <span class="tt-sep">&middot;</span>
          <span class="tt-meta">{domainCount} domains</span>
          <span class="tt-sep">&middot;</span>
          <span class="tt-meta">{clusterCount} clusters</span>
        {:else if hn.state === 'domain'}
          {@const childCount = sceneData?.nodes.filter(n => n.parentId === hn.id).length ?? 0}
          <span class="tt-label">{hn.label}</span>
          <span class="tt-sep">&middot;</span>
          <span class="tt-meta">{childCount} clusters</span>
          {#if hn.avgScore != null}
            <span class="tt-sep">&middot;</span>
            <span class="tt-score">{hn.avgScore.toFixed(1)}</span>
          {/if}
        {:else}
          <span class="tt-label">{hn.label}</span>
          <span class="tt-sep">&middot;</span>
          <span class="tt-domain">{hn.domain}</span>
          <span class="tt-sep">&middot;</span>
          <span class="tt-meta">{hn.memberCount}m</span>
          {#if hn.avgScore != null}
            <span class="tt-sep">&middot;</span>
            <span class="tt-score">{hn.avgScore.toFixed(1)}</span>
          {/if}
        {/if}
      </div>
    {/if}
  {/if}
  {#if clustersStore.taxonomyLoading}
    <div class="topology-loading">Loading taxonomy...</div>
  {:else if !clustersStore.taxonomyError && clustersStore.taxonomyTree.length === 0}
    <div class="topology-empty">
      <span class="topology-empty-label">No clusters yet</span>
      <span class="topology-empty-hint">Forge a prompt to start building the taxonomy</span>
    </div>
  {:else if !clustersStore.taxonomyError && clustersStore.filteredTaxonomyTree.length === 0 && clustersStore.stateFilter !== null}
    <div class="topology-empty">
      <span class="topology-empty-label">No {clustersStore.stateFilter} clusters</span>
      <span class="topology-empty-hint">Switch the state filter to view other clusters</span>
    </div>
  {/if}
  {#if clustersStore.taxonomyError}
    <div class="topology-error" role="alert" aria-live="polite">{clustersStore.taxonomyError}</div>
  {/if}
</div>
{#if clustersStore.activityOpen}
  <div class="topology-activity">
    <ActivityPanel />
  </div>
{/if}
</div>

<style>
  .topology-outer {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
  }

  .topology-container {
    position: relative;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  .topology-has-activity .topology-container {
    /* When activity panel is open, give canvas 65% of space */
    flex: 0 0 65%;
  }

  .topology-activity {
    flex: 0 0 35%;
    min-height: 0;
    overflow: hidden;
  }

  canvas {
    display: block;
    width: 100%;
    height: 100%;
  }

  .topology-tooltip {
    position: absolute;
    top: 8px;
    left: 8px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    padding: 4px 6px;
    font-size: 11px;
    font-family: var(--font-mono);
    pointer-events: none;
  }

  .topology-tooltip .tt-label {
    color: var(--color-text-primary);
  }

  .topology-tooltip .tt-sep {
    color: var(--color-text-dim);
    margin: 0 2px;
  }

  .topology-tooltip .tt-domain {
    color: var(--color-neon-cyan);
    text-transform: uppercase;
    font-size: 10px;
  }

  .topology-tooltip .tt-meta {
    color: var(--color-text-secondary);
  }

  .topology-tooltip .tt-score {
    color: var(--color-neon-green);
  }

  .topology-loading,
  .topology-error {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: var(--color-text-dim);
    font-size: 12px;
    font-family: var(--font-mono);
  }

  .topology-error {
    color: var(--color-neon-red);
  }

  .topology-empty {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    pointer-events: none;
  }

  .topology-empty-label {
    color: var(--color-text-dim);
    font-size: 12px;
    font-family: var(--font-mono);
  }

  .topology-empty-hint {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
  }
</style>
