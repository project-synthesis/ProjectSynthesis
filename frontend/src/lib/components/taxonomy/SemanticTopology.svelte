<script lang="ts">
  import { onMount } from 'svelte';
  import { patternsStore } from '$lib/stores/patterns.svelte';
  import { TopologyRenderer, type LODTier } from './TopologyRenderer';
  import { buildSceneData, assignLodVisibility, type SceneData } from './TopologyData';
  import { TopologyInteraction } from './TopologyInteraction';
  import { TopologyLabels } from './TopologyLabels';
  import { settleForces } from './TopologyWorker';
  import TopologyControls from './TopologyControls.svelte';
  import * as THREE from 'three';
  import { triggerRecluster } from '$lib/api/taxonomy';
  import { addToast } from '$lib/stores/toast.svelte';

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

  function rebuildScene(data: SceneData): void {
    if (!renderer) return;

    // Clear previous
    interaction?.clear();
    labels?.clear();
    nodeMeshes.clear();

    // Dispose GPU resources before clearing scene
    renderer.scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        obj.geometry.dispose();
        if (Array.isArray(obj.material)) {
          obj.material.forEach((m) => m.dispose());
        } else {
          obj.material.dispose();
        }
      } else if (obj instanceof THREE.LineSegments) {
        obj.geometry.dispose();
        (obj.material as THREE.Material).dispose();
      }
    });

    // Remove old scene children
    while (renderer.scene.children.length > 0) {
      renderer.scene.remove(renderer.scene.children[0]);
    }

    // Build nodes as individual meshes
    const geometry = new THREE.IcosahedronGeometry(1, 1);
    for (const node of data.nodes) {
      if (!node.visible) continue;

      const material = new THREE.MeshBasicMaterial({ color: node.color });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(...node.position);
      mesh.scale.setScalar(node.size);
      renderer.scene.add(mesh);
      nodeMeshes.set(node.id, mesh);
      interaction?.registerNode(node.id, mesh, node);
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
        color: 0x2a2a3e,
        transparent: true,
        opacity: 0.4,
      });
      const lines = new THREE.LineSegments(edgeGeometry, edgeMaterial);
      renderer.scene.add(lines);
    }

    // Labels (near LOD only)
    if (labels) {
      for (const node of data.nodes) {
        if (!node.visible) continue;
        const sprite = labels.getOrCreate(node.id, node.label, node.color);
        sprite.position.set(node.position[0], node.position[1] + node.size + 0.5, node.position[2]);
      }
      labels.setVisible(lodTier === 'near');
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
    patternsStore.selectFamily(nodeId);
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
      focusedNodeId = match.id;
      patternsStore.selectFamily(match.id);
    }
  }

  async function handleRecluster(): Promise<void> {
    try {
      await triggerRecluster();
      await patternsStore.loadTree();
    } catch (err) {
      // Recluster failed — tree stays as-is
      console.error('Recluster failed:', err);
      addToast('deleted', 'Recluster failed');
    }
  }

  // Watch for taxonomy tree changes
  $effect(() => {
    const tree = patternsStore.taxonomyTree;
    if (tree.length > 0 && renderer) {
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
    }
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
    patternsStore.loadTree();

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
  {#if patternsStore.taxonomyLoading}
    <div class="topology-loading">Loading taxonomy...</div>
  {/if}
  {#if patternsStore.taxonomyError}
    <div class="topology-error" role="alert" aria-live="polite">{patternsStore.taxonomyError}</div>
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
    padding: 4px 8px;
    background: var(--color-surface);
    border: 1px solid var(--color-contour);
    color: var(--color-text);
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
