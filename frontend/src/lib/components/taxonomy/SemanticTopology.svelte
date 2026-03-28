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

  // Resolved at module level to avoid per-frame allocations
  const HIGHLIGHT_COLOR = parseInt(stateColor('template').replace('#', ''), 16);
  const EDGE_COLOR = parseInt(stateColor('archived').replace('#', ''), 16);

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

    // Build nodes — sharp neon contour aesthetic (brand: 1px borders, zero effects)
    // Dark fill + bright wireframe ring = the 3D equivalent of the UI's
    // `border: 1px solid var(--color-neon-*)` on dark `--color-bg-card` cards.
    const sphereGeo = new THREE.IcosahedronGeometry(1, 2);
    const wireGeo = new THREE.IcosahedronGeometry(1, 1);
    for (const node of data.nodes) {
      if (!node.visible) continue;

      const group = new THREE.Group();
      group.position.set(...node.position);

      // Fill: dark, slightly tinted with the domain color
      const fillMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(node.color).multiplyScalar(0.15),
        transparent: true,
        opacity: node.opacity * 0.9,
      });
      const fill = new THREE.Mesh(sphereGeo, fillMat);
      fill.scale.setScalar(node.size);
      group.add(fill);

      // Contour: sharp wireframe ring in full neon color
      const wireMat = new THREE.MeshBasicMaterial({
        color: node.color,
        wireframe: true,
        transparent: true,
        opacity: node.opacity * 0.85,
      });
      const wire = new THREE.Mesh(wireGeo, wireMat);
      wire.scale.setScalar(node.size);
      group.add(wire);

      renderer.scene.add(group);
      nodeMeshes.set(node.id, fill);
      interaction?.registerNode(node.id, fill, node);
    }

    // Build edges
    const edgePositions: number[] = [];
    const nodeMap = new Map(data.nodes.map(n => [n.id, n]));
    for (const edge of data.edges) {
      const from = nodeMap.get(edge.from);
      const to = nodeMap.get(edge.to);
      if (from?.visible && to?.visible) {
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
      renderer.scene.add(lines);
    }

    // Labels (near LOD only, but template labels always visible)
    if (labels) {
      const templateSprites: import('three').Sprite[] = [];
      for (const node of data.nodes) {
        if (!node.visible) continue;
        const sprite = labels.getOrCreate(node.id, node.label, node.color);
        sprite.position.set(node.position[0], node.position[1] + node.size + 0.5, node.position[2]);
        if (node.state === 'template') {
          templateSprites.push(sprite);
        }
      }
      labels.setVisible(lodTier === 'near');
      // Template nodes: labels always visible regardless of LOD
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
    const tree = clustersStore.taxonomyTree;
    if (tree.length > 0 && renderer) {
      untrack(() => {
        sceneData = buildSceneData(tree);
        assignLodVisibility(sceneData.nodes, lodTier);

        // Run force settling inline (Web Worker version for production)
        const positions = new Float32Array(sceneData.nodes.length * 3);
        const sizes = new Float32Array(sceneData.nodes.length);
        sceneData.nodes.forEach((n, i) => {
          positions[i * 3] = n.position[0];
          positions[i * 3 + 1] = n.position[1];
          positions[i * 3 + 2] = n.position[2];
          sizes[i] = n.size;
        });

        const settled = settleForces({ positions, sizes, iterations: 50 });
        sceneData.nodes.forEach((n, i) => {
          n.position = [settled.positions[i * 3], settled.positions[i * 3 + 1], settled.positions[i * 3 + 2]];
        });

        rebuildScene(sceneData);
      });
    }
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
