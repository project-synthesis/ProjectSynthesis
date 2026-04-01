<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { TopologyRenderer, type LODTier } from './TopologyRenderer';
  import { buildSceneData, assignLodVisibility, type SceneData } from './TopologyData';
  import { TopologyInteraction } from './TopologyInteraction';
  import { TopologyLabels } from './TopologyLabels';
  import { settleForces } from './TopologyWorker';
  import TopologyControls from './TopologyControls.svelte';
  import * as THREE from 'three';
  import { triggerRecluster } from '$lib/api/clusters';
  import { addToast } from '$lib/stores/toast.svelte';
  import { stateColor } from '$lib/utils/colors';
  import { parsePrimaryDomain } from '$lib/utils/formatting';
  import type { ClusterNode } from '$lib/api/clusters';

  // Resolved at module level to avoid per-frame allocations
  const HIGHLIGHT_COLOR = parseInt(stateColor('template').replace('#', ''), 16);
  const EDGE_COLOR = parseInt(stateColor('archived').replace('#', ''), 16);
  const SIMILARITY_EDGE_COLOR = parseInt(stateColor('template').replace('#', ''), 16);
  const INJECTION_EDGE_COLOR = 0xff9500; // warm gold/amber

  // Similarity edge group — persisted across rebuilds for visibility toggle
  let similarityEdgeGroup: THREE.Group | null = null;

  // Injection edge group — persisted across rebuilds for visibility toggle
  let injectionEdgeGroup: THREE.Group | null = null;

  let canvas: HTMLCanvasElement;
  let container: HTMLDivElement;
  let renderer: TopologyRenderer | null = null;
  let interaction: TopologyInteraction | null = null;
  let labels: TopologyLabels | null = null;
  let sceneData = $state<SceneData | null>(null);

  let lodTier = $state<LODTier>('far');
  let focusedNodeId = $state<string | null>(null);
  let hoveredNodeId = $state<string | null>(null);

  // Node meshes for raycasting
  let nodeMeshes: Map<string, THREE.Mesh> = new Map();

  // Flat node lookup for mid-LOD label logic and domain highlight
  let flatNodeMap: Map<string, ClusterNode> = new Map();

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

  function rebuildScene(data: SceneData): void {
    if (!renderer) return;

    // Clear previous
    interaction?.clear();
    labels?.clear();  // disposes label sprites + textures
    nodeMeshes.clear();
    clearHighlight();

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
      const isDomain = node.state === 'domain';
      group.userData = { isDomain };

      // Fill: dark tinted interior (domains slightly darker = edge-dominant)
      // Non-domain nodes: modulate fill scalar by avgScore for saturation encoding
      let fillScalar = isDomain ? 0.08 : 0.15;
      if (!isDomain && node.avgScore != null) {
        fillScalar *= 0.7 + 0.3 * Math.min(1, Math.max(0, node.avgScore / 10));
      }
      const fillMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(node.color).multiplyScalar(fillScalar),
        transparent: true,
        opacity: node.opacity * 0.9,
      });
      const fill = new THREE.Mesh(isDomain ? domainFillGeo : clusterFillGeo, fillMat);
      fill.scale.setScalar(node.size);
      group.add(fill); // child 0: fill

      if (isDomain) {
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
        // Cluster: dense triangular wireframe contour
        // Coherence maps [0,1] to opacity multiplier [0.5, 1.0]
        const wireMat = new THREE.MeshBasicMaterial({
          color: node.color,
          wireframe: true,
          transparent: true,
          opacity: node.opacity * (0.5 + 0.5 * node.coherence),
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
    renderer.onAnimate = () => {
      for (const g of domainGroups) {
        g.rotation.y += 0.002;
      }
    };

    // Build edges — hierarchical edges (parent→child) are always drawn
    // if both endpoints exist in the scene, regardless of LOD visibility.
    // This prevents child clusters from appearing "orphaned" when their
    // domain parent is at the edge of a visibility threshold.
    const edgePositions: number[] = [];
    const nodeMap = new Map(data.nodes.map(n => [n.id, n]));
    for (const edge of data.edges) {
      const from = nodeMap.get(edge.from);
      const to = nodeMap.get(edge.to);
      if (!from || !to) continue;
      const isHierarchical = edge.type === 'hierarchical';
      if (isHierarchical || (from.visible && to.visible)) {
        edgePositions.push(...from.position, ...to.position);
      }
    }

    if (edgePositions.length > 0) {
      const edgeGeometry = new THREE.BufferGeometry();
      edgeGeometry.setAttribute('position', new THREE.Float32BufferAttribute(edgePositions, 3));
      const edgeMaterial = new THREE.LineBasicMaterial({
        color: EDGE_COLOR,
        transparent: true,
        opacity: 0.4,
      });
      const lines = new THREE.LineSegments(edgeGeometry, edgeMaterial);
      lines.userData = { isInterClusterEdge: true };
      renderer.scene.add(lines);
    }

    // Similarity edges — separate group controlled by toggle
    similarityEdgeGroup = new THREE.Group();
    similarityEdgeGroup.userData = { isSimilarityEdge: true };
    similarityEdgeGroup.visible = clustersStore.showSimilarityEdges;

    const simEdges = data.edges.filter(e => e.type === 'similarity');
    if (simEdges.length > 0) {
      // Build a lookup from similarity edge data to get the score
      const simEdgeScores = new Map<string, number>();
      for (const se of clustersStore.similarityEdges) {
        simEdgeScores.set(`${se.from_id}:${se.to_id}`, se.similarity);
        simEdgeScores.set(`${se.to_id}:${se.from_id}`, se.similarity);
      }

      for (const edge of simEdges) {
        const from = nodeMap.get(edge.from);
        const to = nodeMap.get(edge.to);
        if (!from || !to) continue;

        const similarity = simEdgeScores.get(`${edge.from}:${edge.to}`) ?? 0.5;
        // Opacity proportional to similarity: 0.1 at threshold (0.5) to 0.4 at 1.0
        const opacity = 0.1 + (similarity - 0.5) * 0.6;

        const simGeo = new THREE.BufferGeometry();
        simGeo.setAttribute('position', new THREE.Float32BufferAttribute(
          [...from.position, ...to.position], 3,
        ));
        const simMat = new THREE.LineDashedMaterial({
          color: SIMILARITY_EDGE_COLOR,
          transparent: true,
          opacity: Math.max(0.1, Math.min(0.4, opacity)),
          dashSize: 0.3,
          gapSize: 0.2,
        });
        const simLine = new THREE.LineSegments(simGeo, simMat);
        simLine.computeLineDistances();
        simLine.userData = { isSimilarityEdge: true, baseOpacity: opacity };
        similarityEdgeGroup.add(simLine);
      }
    }
    renderer.scene.add(similarityEdgeGroup);

    // Injection provenance edges — directed, warm gold/amber, separate group
    injectionEdgeGroup = new THREE.Group();
    injectionEdgeGroup.userData = { isInjectionEdge: true };
    injectionEdgeGroup.visible = clustersStore.showInjectionEdges;

    // Build injection edge weight lookup from store data
    const injEdgeWeights = new Map<string, number>();
    let maxInjWeight = 1;
    for (const ie of clustersStore.injectionEdges) {
      const key = `${ie.source_id}:${ie.target_id}`;
      injEdgeWeights.set(key, ie.weight);
      if (ie.weight > maxInjWeight) maxInjWeight = ie.weight;
    }

    const injEdges = data.edges.filter(e => e.type === 'injection');
    if (injEdges.length > 0) {
      for (const edge of injEdges) {
        const from = nodeMap.get(edge.from);
        const to = nodeMap.get(edge.to);
        if (!from || !to) continue;

        const weight = injEdgeWeights.get(`${edge.from}:${edge.to}`) ?? 1;
        // Opacity proportional to weight: min 0.15, max 0.5
        const opacity = 0.15 + (weight / maxInjWeight) * 0.35;

        const injGeo = new THREE.BufferGeometry();
        injGeo.setAttribute('position', new THREE.Float32BufferAttribute(
          [...from.position, ...to.position], 3,
        ));
        const injMat = new THREE.LineBasicMaterial({
          color: INJECTION_EDGE_COLOR,
          transparent: true,
          opacity: Math.max(0.15, Math.min(0.5, opacity)),
        });
        const injLine = new THREE.LineSegments(injGeo, injMat);
        injLine.userData = { isInjectionEdge: true, baseOpacity: opacity };
        injectionEdgeGroup.add(injLine);
      }
    }
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
            .filter(n => n.state === 'template' || n.state === 'domain' || (flatNodeMap.get(n.id)?.member_count ?? 0) >= 5)
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
      await triggerRecluster();
      await clustersStore.loadTree();
    } catch (err) {
      // Recluster failed — tree stays as-is
      console.error('Recluster failed:', err);
      addToast('deleted', 'Recluster failed');
    }
  }

  // Watch for taxonomy tree changes — untrack the write to sceneData
  // to prevent effect_update_depth_exceeded (reads tree, writes sceneData).
  $effect(() => {
    const tree = clustersStore.filteredTaxonomyTree;
    if (tree.length > 0 && renderer) {
      untrack(() => {
        flatNodeMap = new Map(tree.map(n => [n.id, n]));
        sceneData = buildSceneData(tree, clustersStore.similarityEdges, clustersStore.injectionEdges);
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

        // Cache key: hash of node IDs + positions (invalidates when UMAP changes)
        const fingerprint = sceneData.nodes
          .map(n => `${n.id}:${n.position[0].toFixed(2)},${n.position[1].toFixed(2)},${n.position[2].toFixed(2)}`)
          .sort()
          .join('|');
        const cacheKey = 'topology_settled_' + fingerprint.split('').reduce((h, c) => ((h << 5) - h + c.charCodeAt(0)) | 0, 0).toString(36);

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
            // Cache for next load (cap at 200 entries to prevent localStorage bloat)
            try {
              // Clean old entries
              for (let k = 0; k < localStorage.length; k++) {
                const key = localStorage.key(k);
                if (key?.startsWith('topology_settled_') && key !== cacheKey) {
                  localStorage.removeItem(key);
                }
              }
              localStorage.setItem(cacheKey, JSON.stringify(Array.from(settledPositions)));
            } catch { /* quota exceeded — ignore */ }
          }
        } catch {
          // Fallback: always compute
          const settled = settleForces({
            positions, restPositions, sizes,
            parentIndices, domainGroups,
            iterations: 60,
          });
          settledPositions = settled.positions;
        }

        sceneData.nodes.forEach((n, i) => {
          n.position = [settledPositions[i * 3], settledPositions[i * 3 + 1], settledPositions[i * 3 + 2]];
        });

        rebuildScene(sceneData);

        // Auto-focus on the largest domain cluster on initial load.
        // Without this, the camera starts at origin (0,0,80) which may
        // be void space if the largest cluster's UMAP coords are elsewhere.
        if (!focusedNodeId && sceneData.nodes.length > 0) {
          // Find the domain with the most visible children
          const domainSizes = new Map<string, { count: number; cx: number; cy: number; cz: number }>();
          for (const n of sceneData.nodes) {
            if (n.state === 'domain' || !n.visible) continue;
            const dom = (flatNodeMap.get(n.id)?.domain ?? 'general').split(':')[0].trim().toLowerCase();
            const entry = domainSizes.get(dom) ?? { count: 0, cx: 0, cy: 0, cz: 0 };
            entry.count++;
            entry.cx += n.position[0];
            entry.cy += n.position[1];
            entry.cz += n.position[2];
            domainSizes.set(dom, entry);
          }
          let bestDomain = '';
          let bestCount = 0;
          for (const [dom, entry] of domainSizes) {
            if (entry.count > bestCount) {
              bestCount = entry.count;
              bestDomain = dom;
            }
          }
          if (bestDomain && bestCount > 0) {
            const entry = domainSizes.get(bestDomain)!;
            const cx = entry.cx / entry.count;
            const cy = entry.cy / entry.count;
            const cz = entry.cz / entry.count;
            renderer?.focusOn(new THREE.Vector3(cx, cy, cz), 40, 800);
          }
        }
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
      const isDomain = group.userData?.isDomain === true;
      for (let i = 0; i < group.children.length; i++) {
        const child = group.children[i];
        const mat = (child as THREE.Mesh | THREE.LineSegments | THREE.Points).material as
          THREE.MeshBasicMaterial | THREE.LineBasicMaterial | THREE.PointsMaterial;
        if (!mat || mat.opacity === undefined) continue;
        let baseOpacity: number;
        if (i === 0) {
          baseOpacity = node.opacity * 0.9;              // fill (both types)
        } else if (isDomain) {
          baseOpacity = node.opacity * (i === 2 ? 0.95 : 0.9); // edges or points
        } else {
          baseOpacity = node.opacity * (0.5 + 0.5 * node.coherence); // cluster wire (coherence)
        }
        mat.opacity = baseOpacity * dimFactor;
      }
    }

    // Dim inter-cluster edges and similarity edges (preserve domain node EdgesGeometry outlines)
    const dimActive = highlightDomain != null;
    renderer.scene.traverse((obj) => {
      if (obj instanceof THREE.LineSegments && obj.userData?.isInterClusterEdge) {
        const mat = obj.material as THREE.LineBasicMaterial;
        mat.opacity = dimActive ? 0.1 : 0.4;
      }
      if (obj instanceof THREE.LineSegments && obj.userData?.isSimilarityEdge) {
        const mat = obj.material as THREE.LineDashedMaterial;
        const baseOpacity = obj.userData.baseOpacity as number;
        mat.opacity = dimActive ? baseOpacity * 0.25 : baseOpacity;
      }
      if (obj instanceof THREE.LineSegments && obj.userData?.isInjectionEdge) {
        const mat = obj.material as THREE.LineBasicMaterial;
        const baseOpacity = obj.userData.baseOpacity as number;
        mat.opacity = dimActive ? baseOpacity * 0.25 : baseOpacity;
      }
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

    // Load taxonomy data
    clustersStore.loadTree();

    // Resize observer
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      renderer?.resize(width, height);
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      interaction?.dispose();
      labels?.dispose();
      renderer?.dispose();
    };
  });
</script>

<div class="topology-container" bind:this={container}>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <canvas
    bind:this={canvas}
    aria-label="Taxonomy topology visualization"
    tabindex="0"
  ></canvas>
  <TopologyControls
    {lodTier}
    onSearch={handleSearch}
    onRecluster={handleRecluster}
  />
  {#if hoveredNodeId}
    <div class="topology-tooltip" role="tooltip">
      {sceneData?.nodes.find(n => n.id === hoveredNodeId)?.label ?? ''}
    </div>
  {/if}
  {#if clustersStore.taxonomyLoading}
    <div class="topology-loading">Loading taxonomy...</div>
  {/if}
  {#if clustersStore.taxonomyError}
    <div class="topology-error" role="alert" aria-live="polite">{clustersStore.taxonomyError}</div>
  {/if}
</div>

<style>
  .topology-container {
    position: relative;
    width: 100%;
    height: 100%;
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
</style>
