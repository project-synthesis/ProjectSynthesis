<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';

  import { TopologyRenderer, type LODTier } from './TopologyRenderer';
  import { buildSceneData, assignLodVisibility, computeHierarchicalOpacity, type SceneData } from './TopologyData';
  import { TopologyInteraction } from './TopologyInteraction';
  import { TopologyLabels } from './TopologyLabels';
  import { settleForces } from './TopologyWorker';
  import TopologyControls from './TopologyControls.svelte';
  import ActivityPanel from './ActivityPanel.svelte';
  import SeedModal from './SeedModal.svelte';
  // Pattern Graph hint card is built into TopologyControls (inline, no separate component)
  import * as THREE from 'three';
  import { triggerRecluster } from '$lib/api/clusters';
  import type { TaxonomyActivityEvent } from '$lib/api/clusters';
  import { addToast } from '$lib/stores/toast.svelte';
  import { stateColor } from '$lib/utils/colors';
  import { parsePrimaryDomain } from '$lib/utils/formatting';
  import type { ClusterNode } from '$lib/api/clusters';
  import { BeamPool } from './BeamPool';
  import { ClusterPhysics } from './ClusterPhysics';
  import { createRippleUniforms, RIPPLE_VERTEX_SHADER, RIPPLE_FRAGMENT_SHADER } from './BeamShader';
  import { EDGE_DEPTH_VERTEX, EDGE_DEPTH_FRAGMENT, createEdgeDepthUniforms } from './EdgeShader';
  import { buildNodeMap } from './TopologyData';

  // Resolved at module level to avoid per-frame allocations
  const HIGHLIGHT_COLOR = parseInt(stateColor('template').replace('#', ''), 16);
  const EDGE_COLOR = parseInt(stateColor('archived').replace('#', ''), 16);
  const SIMILARITY_EDGE_COLOR = parseInt(stateColor('template').replace('#', ''), 16);
  const INJECTION_EDGE_COLOR = 0xff9500; // warm gold/amber

  /** Suppress hierarchical edges shorter than this (scene units).
   *  Spatial proximity already communicates the parent-child relationship. */
  const EDGE_PROXIMITY_THRESHOLD = 12.0;

  /** Segments per bezier curve for hierarchical edges.
   *  Used by buildCurvePositions and callers for index construction. */
  const CURVE_SEGMENTS = 12;

  /** Endpoint pair for hierarchical edge curve building. */
  interface HierEdge { from: [number, number, number]; to: [number, number, number] }

  // Edge groups — persisted across rebuilds for visibility toggle
  let similarityEdgeGroup: THREE.Group | null = null;
  let injectionEdgeGroup: THREE.Group | null = null;

  // Opacity lookup caches — rebuilt per rebuildScene call
  let _simScoreCache: Map<string, number> | null = null;
  let _injWeightCache: { map: Map<string, number>; max: number } | null = null;

  interface EdgeGroupOptions {
    type: 'similarity' | 'injection';
    color: number;
    dashed: boolean;
    tag: string;
    opacityFn: (edge: import('./TopologyData').SceneEdge) => number;
  }

  /** Build a THREE.Group of line segments for a given edge type. */
  function buildEdgeGroup(
    data: SceneData,
    nodeMap: Map<string, import('./TopologyData').SceneNode>,
    opts: EdgeGroupOptions,
  ): THREE.Group {
    const group = new THREE.Group();
    group.userData = { [opts.tag]: true };
    const edges = data.edges.filter(e => e.type === opts.type);
    for (const edge of edges) {
      const from = nodeMap.get(edge.from);
      const to = nodeMap.get(edge.to);
      if (!from || !to) continue;
      const opacity = opts.opacityFn(edge);
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(
        [...from.position, ...to.position], 3,
      ));
      const mat = opts.dashed
        ? new THREE.LineDashedMaterial({
            color: opts.color, transparent: true, opacity,
            dashSize: 0.3, gapSize: 0.2,
          })
        : new THREE.LineBasicMaterial({
            color: opts.color, transparent: true, opacity,
          });
      const line = new THREE.LineSegments(geo, mat);
      if (opts.dashed) line.computeLineDistances();
      line.userData = { [opts.tag]: true, baseOpacity: opacity };
      group.add(line);
    }
    return group;
  }

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

  // Flat node lookup for mid-LOD label logic and domain highlight
  let flatNodeMap: Map<string, ClusterNode> = new Map();

  // Beam pool + cluster physics state
  let beamPool: BeamPool | null = null;
  let clusterPhysics: ClusterPhysics | null = null;
  let _hasPlayedEntrance = false;
  let _beamNodeGroups: Map<string, THREE.Group> = new Map();
  let _sceneNodeMap: Map<string, import('./TopologyData').SceneNode> = new Map();
  let _prevNodeSizes: Map<string, number> = new Map();
  let _seedBatchActive = false;
  let _removeDomainRotation: (() => void) | null = null;
  let _removeFormationAnim: (() => void) | null = null;

  // Persisted edge grouping — shared between rebuildScene and formation rebuild
  let _edgesByParent: Map<string, HierEdge[]> = new Map();

  // External highlight tracking (for family selection sync)
  let _highlightedId: string | null = null;
  let _highlightedColor: number | null = null;

  /** Restore previous highlight color and apply neon cyan to a new node. */
  function applyHighlight(nodeId: string): void {
    // Restore previous
    if (_highlightedId && _highlightedId !== nodeId) {
      const prev = nodeMeshes.get(_highlightedId);
      if (prev && _highlightedColor !== null) {
        (prev.material as THREE.MeshBasicMaterial).color.setHex(_highlightedColor);
      }
    }
    const mesh = nodeMeshes.get(nodeId);
    if (!mesh) {
      _highlightedId = null;
      _highlightedColor = null;
      return;
    }
    _highlightedColor = (mesh.material as THREE.MeshBasicMaterial).color.getHex();
    _highlightedId = nodeId;
    (mesh.material as THREE.MeshBasicMaterial).color.setHex(HIGHLIGHT_COLOR);
  }

  /** Clear any active highlight, restoring the original color. */
  function clearHighlight(): void {
    if (_highlightedId) {
      const prev = nodeMeshes.get(_highlightedId);
      if (prev && _highlightedColor !== null) {
        (prev.material as THREE.MeshBasicMaterial).color.setHex(_highlightedColor);
      }
    }
    _highlightedId = null;
    _highlightedColor = null;
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
  function buildMergedCurveGeometry(edges: HierEdge[]): { positions: number[]; indices: number[] } {
    const positions: number[] = [];
    const indices: number[] = [];
    let offset = 0;
    for (let i = 0; i < edges.length; i++) {
      const cp = buildCurvePositions(edges[i].from, edges[i].to, i, edges.length);
      for (let j = 0; j < cp.length; j++) positions.push(cp[j]);
      for (let j = 0; j < CURVE_SEGMENTS; j++) indices.push(offset + j, offset + j + 1);
      offset += CURVE_SEGMENTS + 1;
    }
    return { positions, indices };
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

  function rebuildScene(data: SceneData): void {
    if (!renderer) return;

    // Temporarily remove beam pool from scene to protect it from disposal
    if (beamPool) {
      renderer.scene.remove(beamPool.group);
    }

    // Clear previous
    interaction?.clear();
    labels?.clear();  // disposes label sprites + textures
    nodeMeshes.clear();
    clearHighlight();
    _simScoreCache = null;
    _injWeightCache = null;

    // Dispose GPU resources before clearing scene.
    // Track disposed geometries to avoid duplicate dispose on shared instances.
    const disposedGeometries = new Set<THREE.BufferGeometry>();
    renderer.scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh || obj instanceof THREE.LineSegments) {
        if (!disposedGeometries.has(obj.geometry)) {
          obj.geometry.dispose();
          disposedGeometries.add(obj.geometry);
        }
        if (Array.isArray(obj.material)) {
          obj.material.forEach((m: THREE.Material) => m.dispose());
        } else {
          (obj.material as THREE.Material).dispose();
        }
      }
      // Note: Sprites are already disposed by labels.clear() above
    });

    // Remove old scene children
    while (renderer.scene.children.length > 0) {
      renderer.scene.remove(renderer.scene.children[0]);
    }

    // Build nodes — two distinct visual tiers:
    //
    // CLUSTERS: Icosahedron — dark fill + triangular wireframe contour.
    //   Standard neon-contour card aesthetic.
    //
    // DOMAIN NODES: Dodecahedron — dark fill + EdgesGeometry (clean pentagonal
    //   outlines only) + vertex anchor points + slow Y-axis rotation.
    //   Three complementary effects form one coherent "precision container" look:
    //   structural edges, bright vertex markers, mechanical motion.
    const clusterFillGeo = new THREE.IcosahedronGeometry(1, 2);
    const clusterWireGeo = new THREE.IcosahedronGeometry(1, 1);
    const domainFillGeo = new THREE.DodecahedronGeometry(1, 2);
    // EdgesGeometry on subdiv-0 dodecahedron: extracts only the 30 structural
    // edges (pentagonal outlines), ignoring subdivision diagonals.
    const domainEdgesBase = new THREE.DodecahedronGeometry(1, 0);
    const domainEdgesGeo = new THREE.EdgesGeometry(domainEdgesBase, 1);
    // Vertex points: extract the 20 unique vertices of the base dodecahedron
    const domainVertPositions = domainEdgesBase.getAttribute('position');
    const uniqueVerts = new Map<string, [number, number, number]>();
    for (let i = 0; i < domainVertPositions.count; i++) {
      const x = domainVertPositions.getX(i);
      const y = domainVertPositions.getY(i);
      const z = domainVertPositions.getZ(i);
      const key = `${x.toFixed(4)},${y.toFixed(4)},${z.toFixed(4)}`;
      if (!uniqueVerts.has(key)) uniqueVerts.set(key, [x, y, z]);
    }
    const vertArray = new Float32Array([...uniqueVerts.values()].flat());
    const domainPointsGeo = new THREE.BufferGeometry();
    domainPointsGeo.setAttribute('position', new THREE.Float32BufferAttribute(vertArray, 3));

    const domainGroups: THREE.Group[] = [];
    for (const node of data.nodes) {
      if (!node.visible) continue;

      const group = new THREE.Group();
      group.position.set(...node.position);
      const isStructural = node.state === 'domain' || node.state === 'project';
      group.userData = { isStructural };

      // Fill: dark tinted interior (structural nodes slightly darker = edge-dominant)
      // Non-structural nodes: modulate fill scalar by avgScore for saturation encoding
      let fillScalar = isStructural ? 0.08 : 0.15;
      if (!isStructural && node.avgScore != null) {
        fillScalar *= 0.7 + 0.3 * Math.min(1, Math.max(0, node.avgScore / 10));
      }
      const fillMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(node.color).multiplyScalar(fillScalar),
        transparent: true,
        opacity: node.opacity * 0.9,
      });
      const fill = new THREE.Mesh(isStructural ? domainFillGeo : clusterFillGeo, fillMat);
      fill.scale.setScalar(node.size);
      group.add(fill); // child 0: fill

      if (isStructural) {
        // Domain: EdgesGeometry — clean pentagonal structural outlines
        const edgeMat = new THREE.LineBasicMaterial({
          color: node.color,
          transparent: true,
          opacity: node.opacity * 0.9,
        });
        const edges = new THREE.LineSegments(domainEdgesGeo, edgeMat);
        edges.scale.setScalar(node.size);
        group.add(edges); // child 1: edges

        // Domain: vertex anchor points — bright dots at each structural corner
        const pointsMat = new THREE.PointsMaterial({
          color: node.color,
          size: 0.12,
          transparent: true,
          opacity: node.opacity * 0.95,
          sizeAttenuation: true,
        });
        const points = new THREE.Points(domainPointsGeo, pointsMat);
        points.scale.setScalar(node.size);
        group.add(points); // child 2: vertex points

        domainGroups.push(group);
      } else {
        // Cluster: dense triangular wireframe contour with ripple shader
        // Coherence maps [0,1] to opacity multiplier [0.5, 1.0]
        const wireUniforms = createRippleUniforms();
        wireUniforms.uColor.value = new THREE.Color(node.color);
        wireUniforms.uOpacity.value = node.opacity * (0.5 + 0.5 * node.coherence);
        const wireMat = new THREE.ShaderMaterial({
          uniforms: wireUniforms,
          vertexShader: RIPPLE_VERTEX_SHADER,
          fragmentShader: RIPPLE_FRAGMENT_SHADER,
          transparent: true,
          wireframe: true,
          depthWrite: false,
        });
        const wire = new THREE.Mesh(clusterWireGeo, wireMat);
        wire.scale.setScalar(node.size);
        group.add(wire); // child 1: wire
      }

      renderer.scene.add(group);
      nodeMeshes.set(node.id, fill);
      interaction?.registerNode(node.id, fill, node);
    }

    // Domain rotation: ~1 revolution per 50s at 60fps
    // Unsubscribe previous callback to prevent accumulation across rebuilds
    _removeDomainRotation?.();
    _removeDomainRotation = renderer.addAnimationCallback(() => {
      for (const g of domainGroups) {
        g.rotation.y += 0.002;
      }
    });

    // Build node map once — used for edge building, beam targeting, and edge group opacity
    _sceneNodeMap = buildNodeMap(data.nodes);

    // Group hierarchical edges by parent — proximity-suppressed edges excluded.
    // Persisted in _edgesByParent so the formation animation rebuild can
    // reconstruct curves from settled positions without re-scanning all edges.
    _edgesByParent = new Map<string, HierEdge[]>();
    for (const edge of data.edges) {
      if (edge.type !== 'hierarchical') continue;
      const from = _sceneNodeMap.get(edge.from);
      const to = _sceneNodeMap.get(edge.to);
      if (!from || !to) continue;
      if (edge.distance != null && edge.distance < EDGE_PROXIMITY_THRESHOLD) continue;
      let bucket = _edgesByParent.get(edge.from);
      if (!bucket) { bucket = []; _edgesByParent.set(edge.from, bucket); }
      bucket.push({ from: from.position, to: to.position });
    }

    // Count total children per parent (including proximity-suppressed ones)
    // so opacity scales by actual density, not by visible-edge count
    const childCountByParent = new Map<string, number>();
    for (const edge of data.edges) {
      if (edge.type !== 'hierarchical') continue;
      childCountByParent.set(edge.from, (childCountByParent.get(edge.from) ?? 0) + 1);
    }

    const hierarchicalGroup = new THREE.Group();
    hierarchicalGroup.userData = { isInterClusterEdgeGroup: true };
    for (const [parentId, edges] of _edgesByParent) {
      if (edges.length === 0) continue;
      const childCount = childCountByParent.get(parentId) ?? 1;
      const opacity = computeHierarchicalOpacity(childCount);
      const { positions: curvePositions, indices: curveIndices } = buildMergedCurveGeometry(edges);

      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(curvePositions, 3));
      geo.setIndex(curveIndices);

      const uniforms = createEdgeDepthUniforms(EDGE_COLOR, opacity);
      const mat = new THREE.ShaderMaterial({
        uniforms,
        vertexShader: EDGE_DEPTH_VERTEX,
        fragmentShader: EDGE_DEPTH_FRAGMENT,
        transparent: true,
        depthWrite: false,
      });
      const lines = new THREE.LineSegments(geo, mat);
      lines.userData = { isInterClusterEdge: true, baseOpacity: opacity, parentId };
      hierarchicalGroup.add(lines);
    }
    renderer.scene.add(hierarchicalGroup);

    // Similarity + injection edges — shared builder, separate groups with toggles
    similarityEdgeGroup = buildEdgeGroup(data, _sceneNodeMap, {
      type: 'similarity',
      color: SIMILARITY_EDGE_COLOR,
      dashed: true,
      tag: 'isSimilarityEdge',
      opacityFn: (edge) => {
        // Build lookup (bidirectional for undirected similarity edges)
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
    });
    similarityEdgeGroup.visible = clustersStore.showSimilarityEdges;
    renderer.scene.add(similarityEdgeGroup);

    injectionEdgeGroup = buildEdgeGroup(data, _sceneNodeMap, {
      type: 'injection',
      color: INJECTION_EDGE_COLOR,
      dashed: false,
      tag: 'isInjectionEdge',
      opacityFn: (edge) => {
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
    });
    injectionEdgeGroup.visible = clustersStore.showInjectionEdges;
    renderer.scene.add(injectionEdgeGroup);

    // Labels — always visible for small graphs (≤ 8 nodes), near = all, mid = large clusters
    if (labels) {
      const visibleNodes = data.nodes.filter(n => n.visible);
      const alwaysShowLabels = visibleNodes.length <= 8;
      const templateSprites: import('three').Sprite[] = [];
      // Mid-LOD: truncate labels to 14 chars for readability at distance
      const isMid = !alwaysShowLabels && lodTier === 'mid';
      const truncLabel = (text: string) =>
        isMid && text.length > 14 ? text.slice(0, 14).trimEnd() + '\u2026' : text;
      for (const node of data.nodes) {
        if (!node.visible) continue;
        const sprite = labels.getOrCreate(node.id, truncLabel(node.label), node.color);
        sprite.position.set(node.position[0], node.position[1] + node.size + 0.5, node.position[2]);
        if (node.state === 'template') {
          templateSprites.push(sprite);
        }
      }
      if (alwaysShowLabels || lodTier === 'near') {
        labels.setVisible(true);
      } else if (isMid) {
        // Show labels for clusters with 5+ members, domains, and templates at mid zoom
        const midLabelIds = new Set(
          visibleNodes
            .filter(n => n.state === 'template' || n.state === 'domain' || n.state === 'project' || (flatNodeMap.get(n.id)?.member_count ?? 0) >= 5)
            .map(n => n.id)
        );
        labels.setVisibleFor(midLabelIds);
      } else {
        labels.setVisible(false);
      }
      // Template nodes: labels always visible regardless of LOD or count
      for (const sprite of templateSprites) {
        sprite.visible = true;
      }
      renderer.scene.add(labels.group);
    }

    // Build beam targeting map (node groups for beam target objects)
    // _sceneNodeMap was already built above (before edges) — no need to rebuild
    _beamNodeGroups.clear();
    for (const [nodeId, mesh] of nodeMeshes) {
      if (mesh?.parent) {
        _beamNodeGroups.set(nodeId, mesh.parent as THREE.Group);
      }
    }

    // Reconcile physics state with actual data-driven node sizes.
    // Speculative accretion growth may have drifted from real member counts —
    // setBaseScale snaps physics to the authoritative data on each rebuild.
    if (clusterPhysics) {
      for (const node of data.nodes) {
        if (node.state !== 'domain' && node.state !== 'project') {
          clusterPhysics.setBaseScale(node.id, node.size);
        }
      }
    }

    // Re-add beam pool to scene (protected from disposal above)
    if (beamPool) {
      renderer.scene.add(beamPool.group);
    }
  }

  function handleLodChange(tier: LODTier): void {
    lodTier = tier;
    if (sceneData) {
      assignLodVisibility(sceneData.nodes, tier);
      rebuildScene(sceneData);
    }
  }

  function handleNodeClick(nodeId: string): void {
    focusedNodeId = nodeId;
    const node = sceneData?.nodes.find(n => n.id === nodeId);
    if (node) {
      renderer?.focusOn(new THREE.Vector3(...node.position));
    }
    // Select family in store for Inspector
    clustersStore.selectCluster(nodeId);
    // F10: Switch navigator to clusters tab so user sees the selection context
    window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'clusters' }));
  }

  function handleAscend(): void {
    if (focusedNodeId && sceneData) {
      const current = sceneData.nodes.find(n => n.id === focusedNodeId);
      if (current?.parentId) {
        handleNodeClick(current.parentId);
      } else {
        // Back to overview
        focusedNodeId = null;
        renderer?.focusOn(new THREE.Vector3(0, 0, 0), 80);
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
      applyHighlight(match.id);
      focusedNodeId = match.id;
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
    if (tree.length > 0 && renderer) {
      untrack(() => {
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

        const cacheKey = 'topology_settled_' + sceneData.nodes.map(n => n.id).sort().join('|');
        let settledPositions: Float32Array;
        
        try {
          const cached = localStorage.getItem(cacheKey);
          if (cached) {
            settledPositions = new Float32Array(JSON.parse(cached));
          } else {
            const settled = settleForces({
              positions, restPositions, sizes,
              parentIndices, domainGroups,
              iterations: 60,
            });
            settledPositions = settled.positions;
            try {
              // Remove stale topology cache entries before writing new one
              for (let k = localStorage.length - 1; k >= 0; k--) {
                const key = localStorage.key(k);
                if (key?.startsWith('topology_settled_') && key !== cacheKey) {
                  localStorage.removeItem(key);
                }
              }
              localStorage.setItem(cacheKey, JSON.stringify(Array.from(settledPositions)));
            } catch { /* quota exceeded — ignore */ }
          }
        } catch {
          const settled = settleForces({ positions, restPositions, sizes, parentIndices, domainGroups, iterations: 60 });
          settledPositions = settled.positions;
        }

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
        if (similarityEdgeGroup) similarityEdgeGroup.visible = false;
        if (injectionEdgeGroup) injectionEdgeGroup.visible = false;

        // Galaxy Formation Animation Loop (Lerp to Settled Positions)
        // Capture in const for TypeScript narrowing inside the closure
        const formSceneData = sceneData;
        let formProgress = 0.0;
        const formDuration = 90.0; // frames
        const initialPositions = new Float32Array(nodeCount * 3);
        sceneData.nodes.forEach((n, i) => {
           initialPositions[i*3] = n.position[0];
           initialPositions[i*3+1] = n.position[1];
           initialPositions[i*3+2] = n.position[2];
        });

        _removeFormationAnim?.();
        _removeFormationAnim = renderer?.addAnimationCallback(() => {
           formProgress += 1.0;
           // cubic ease-out
           const t = Math.min(formProgress / formDuration, 1.0);
           const easeT = 1 - Math.pow(1 - t, 3);

           formSceneData.nodes.forEach((n, i) => {
              n.position[0] = initialPositions[i*3] + (settledPositions[i*3] - initialPositions[i*3]) * easeT;
              n.position[1] = initialPositions[i*3+1] + (settledPositions[i*3+1] - initialPositions[i*3+1]) * easeT;
              n.position[2] = initialPositions[i*3+2] + (settledPositions[i*3+2] - initialPositions[i*3+2]) * easeT;

              const group = _beamNodeGroups.get(n.id);
              if (group) group.position.set(...n.position);

              if (labels) {
                 const sprite = labels.getOrCreate(n.id, n.label, n.color);
                 sprite.position.set(n.position[0], n.position[1] + n.size + 0.5, n.position[2]);
              }
           });

           if (t >= 1.0) {
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
                    const { positions, indices } = buildMergedCurveGeometry(edges);
                    ls.geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
                    ls.geometry.setIndex(indices);
                  }
                  obj.visible = true;
                }
              });
              if (similarityEdgeGroup) similarityEdgeGroup.visible = clustersStore.showSimilarityEdges;
              if (injectionEdgeGroup) injectionEdgeGroup.visible = clustersStore.showInjectionEdges;
           }
        }) ?? null;

        // Auto-focus on the largest domain cluster on initial load.
        if (!focusedNodeId && sceneData.nodes.length > 0) {
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
              if (!group || !beamPool || !renderer) return;
              
              // Scale aesthetics by node size dynamically
              const sizeFactor = Math.min(Math.max(node.size / 50, 0.5), 3.0);
              
              beamPool.acquire(group, {
                color: new THREE.Color(node.color), // Exact color of target node
                radius: node.size * 0.04 * sizeFactor,           // Scales dynamically with node size
                sustainMs: 1500 + (sizeFactor * 500), // Linger longer for bigger domains
              }, renderer.camera);
              
              // Ensure the cluster visually reacts/ripples to the materialization burst
              clusterPhysics?.onBeamImpact(node.id, node.size);
            }, i * 150);
          });
        }

        // Fire beams at clusters that grew (post-optimization or post-seed)
        const isSeedBatch = _seedBatchActive;
        if (_prevNodeSizes.size > 0 && beamPool && renderer) {
          let firedCount = 0;
          for (const node of sceneData.nodes) {
            if (node.state !== 'domain') continue; // Only for domain nodes
            
            const prevSize = _prevNodeSizes.get(node.id);
            if (prevSize !== undefined && node.size > prevSize) {
              const group = _beamNodeGroups.get(node.id);
              if (group) {
                setTimeout(() => {
                  if (!beamPool || !renderer) return;
                  
                  const sizeFactor = Math.min(Math.max(node.size / 50, 0.5), 3.0);
                  const nodeRadius = node.size * 0.04 * sizeFactor;
                  
                  beamPool.acquire(group, {
                    color: new THREE.Color(node.color),
                    radius: isSeedBatch ? nodeRadius * 2.0 : nodeRadius,
                    sustainMs: (isSeedBatch ? 3500 : 2500) + (sizeFactor * 500),
                  }, renderer.camera);
                  clusterPhysics?.onBeamImpact(node.id, node.size);
                }, isSeedBatch ? firedCount * 120 : 0);
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

  // Similarity edge visibility toggle
  $effect(() => {
    const show = clustersStore.showSimilarityEdges;
    if (similarityEdgeGroup) {
      similarityEdgeGroup.visible = show;
    }
  });

  // Injection edge visibility toggle
  $effect(() => {
    const show = clustersStore.showInjectionEdges;
    if (injectionEdgeGroup) {
      injectionEdgeGroup.visible = show;
    }
  });

  // Optimization event listener — snapshot member counts before tree rebuild
  $effect(() => {
    if (!beamPool || !renderer) return;
    function onOptimization(e: Event) {
      // Only snapshot on actual optimization completions — ignore feedback/failure
      const detail = (e as CustomEvent).detail;
      if (detail?.status !== 'completed') return;
      _prevNodeSizes.clear();
      for (const [id, node] of _sceneNodeMap) {
        _prevNodeSizes.set(id, node.size);
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

      const flatNode = flatNodeMap.get(node.id);
      const nodeDomain = parsePrimaryDomain(flatNode?.domain);
      const dimmed = highlightDomain != null && nodeDomain !== highlightDomain;
      const dimFactor = dimmed ? 0.15 : 1.0;

      // Apply dim factor to all materials in the group.
      // Cluster: fill (0.9) + wire (coherence-based). Domain: fill (0.9) + edges (0.9) + points (0.95).
      const isStructural = group.userData?.isStructural === true;
      for (let i = 0; i < group.children.length; i++) {
        const child = group.children[i];
        const mat = (child as THREE.Mesh | THREE.LineSegments | THREE.Points).material as
          THREE.MeshBasicMaterial | THREE.LineBasicMaterial | THREE.PointsMaterial;
        if (!mat) continue;
        let baseOpacity: number;
        if (i === 0) {
          baseOpacity = node.opacity * 0.9;              // fill (both types)
        } else if (isStructural) {
          baseOpacity = node.opacity * (i === 2 ? 0.95 : 0.9); // edges or points
        } else {
          baseOpacity = node.opacity * (0.5 + 0.5 * node.coherence); // cluster wire (coherence)
        }
        // Handle ripple ShaderMaterial (wireframe child)
        if ((mat as any).isShaderMaterial && (mat as any).uniforms?.uOpacity) {
          (mat as any).uniforms.uOpacity.value = baseOpacity * dimFactor;
          continue;
        }
        mat.opacity = baseOpacity * dimFactor;
      }
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

  // Sync external family selection → highlight node
  $effect(() => {
    const externalId = clustersStore.selectedClusterId;

    // Deselected — restore previous highlight
    if (!externalId) {
      clearHighlight();
      return;
    }

    if (!renderer || !sceneData) return;
    if (externalId === focusedNodeId) return; // already focused via click or search

    const node = sceneData.nodes.find(n => n.id === externalId);
    if (!node) return;

    applyHighlight(externalId);
    focusedNodeId = externalId;
    renderer.focusOn(new THREE.Vector3(...node.position));
  });

  onMount(() => {
    renderer = new TopologyRenderer(canvas);
    labels = new TopologyLabels();
    interaction = new TopologyInteraction(renderer, canvas, {
      onNodeClick: handleNodeClick,
      onNodeHover: (id) => { hoveredNodeId = id; },
      onAscend: handleAscend,
    });
    renderer.onLodChange(handleLodChange);
    renderer.start();

    // Initialize beam pool + cluster physics
    beamPool = new BeamPool();
    clusterPhysics = new ClusterPhysics();
    renderer.scene.add(beamPool.group);

    let lastTime = performance.now();
    const removeBeamUpdate = renderer.addAnimationCallback(() => {
      const now = performance.now();
      const delta = (now - lastTime) / 1000;
      lastTime = now;

      beamPool?.update(delta, renderer!.camera);

      clusterPhysics?.update(delta, (nodeId, scale, ripple) => {
        const group = _beamNodeGroups.get(nodeId);
        if (!group) return;
        for (const child of group.children) {
          child.scale.setScalar(scale);
        }
        const wire = group.children[1];
        if (wire && (wire as THREE.Mesh).material &&
            ((wire as THREE.Mesh).material as any).uniforms?.uRipple) {
          ((wire as THREE.Mesh).material as any).uniforms.uRipple.value = ripple;
        }
      });
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
      beamPool?.dispose();
      beamPool = null;
      clusterPhysics?.clear();
      clusterPhysics = null;
      removeBeamUpdate();
      _removeFormationAnim?.();
      _removeFormationAnim = null;
      _removeDomainRotation?.();
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
  {:else if !clustersStore.taxonomyError && clustersStore.filteredTaxonomyTree.length === 0}
    <div class="topology-empty">
      <span class="topology-empty-label">No clusters yet</span>
      <span class="topology-empty-hint">Forge a prompt to start building the taxonomy</span>
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
