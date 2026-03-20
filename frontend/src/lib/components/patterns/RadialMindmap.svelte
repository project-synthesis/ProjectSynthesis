<script lang="ts">
  import { select, zoom, arc, type D3ZoomEvent } from 'd3';
  import { browser } from '$app/environment';
  import { untrack } from 'svelte';
  import { patternsStore } from '$lib/stores/patterns.svelte';
  import { DOMAIN_COLORS, domainColor } from '$lib/constants/patterns';
  import { formatScore } from '$lib/utils/formatting';
  import type { GraphFamily, GraphEdge } from '$lib/api/patterns';

  // Extract complex layout logic to pure module for testability
  import { calculateDomainAngles, calculateFamilyPositions, calculateEdgePathDistortion } from './utils/layout';

  // Design system tokens — must match :root in app.css.
  const BG_COLOR = '#06060c';       // --color-bg-primary
  const NEON_CYAN = '#00e5ff';      // --color-neon-cyan
  const TEXT_PRIMARY = '#e4e4f0';   // --color-text-primary
  const TEXT_DIM = '#7a7a9e';       // --color-text-dim
  const FONT_MONO = "'Geist Mono', 'JetBrains Mono', ui-monospace, monospace";

  let svgEl = $state<SVGSVGElement>(undefined!);
  let containerEl = $state<HTMLDivElement>(undefined!);
  let svgGroup: any = null; // store D3 group for selection updates

  // Load graph on mount and reload when invalidated
  $effect(() => {
    const gl = patternsStore.graphLoaded;
    if (!gl) {
      patternsStore.loadGraph();
    }
  });

  // Render D3 visualization when graph data changes
  $effect(() => {
    if (!browser) return;
    const graph = patternsStore.graph;
    if (!graph || !svgEl || !containerEl) return;
    
    // We untrack selectedFamilyId so graph data changes trigger a re-render,
    // but selection clicks do not recreate the entire SVG.
    untrack(() => {
      renderGraph(graph.families, graph.edges, graph.center);
    });
  });

  // Re-render on container resize
  $effect(() => {
    if (!browser || !containerEl) return;
    const observer = new ResizeObserver(() => {
      const graph = patternsStore.graph;
      if (graph && svgEl) {
        untrack(() => {
          renderGraph(graph.families, graph.edges, graph.center);
        });
      }
    });
    observer.observe(containerEl);
    return () => observer.disconnect();
  });

  // Dedicated effect for updating visual selection state to support cross-component reactivity
  $effect(() => {
    const selectedId = patternsStore.selectedFamilyId;
    if (!browser || !svgEl) return;
    
    const svg = select(svgEl);
    svg.selectAll('circle[data-family-id]').attr('stroke-width', 1);
    
    if (selectedId) {
      // Harden CSS selector against invalid characters that would cause D3 exceptions
      svg.select(`circle[data-family-id="${CSS.escape(selectedId)}"]`).attr('stroke-width', 2);
    }
  });

  function renderGraph(
    families: GraphFamily[],
    edges: GraphEdge[],
    center: { total_families: number; total_patterns: number; total_optimizations: number },
  ) {
    const svg = select(svgEl);
    svg.selectAll('*').remove();

    const rect = containerEl.getBoundingClientRect();
    const width = Math.max(100, rect.width || 800);
    const height = Math.max(100, rect.height || 600);
    const cx = width / 2;
    const cy = height / 2;

    svg.attr('width', width).attr('height', height).attr('viewBox', `0 0 ${width} ${height}`);

    // Zoom group
    const g = svg.append('g');
    svgGroup = g;
    // Harden pan and zoom limits so users cannot drag the visualization arbitrarily off-screen perpetually
    const zoomBehavior = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 5])
      .translateExtent([
        [-width * 0.5, -height * 0.5],
        [width * 1.5, height * 1.5]
      ])
      .on('zoom', (event: D3ZoomEvent<SVGSVGElement, unknown>) => {
        g.attr('transform', event.transform.toString());
      });
    svg.call(zoomBehavior);

    // Radii bounds protection: Math.max ensures we do not map negative geometries when resizing UI below 40px offsets
    const ringRadius1 = Math.max(20, Math.min(width, height) * 0.2); // domain ring
    const ringRadius2 = Math.max(40, Math.min(width, height) * 0.35); // family ring
    
    // Core Business Logic separation: Calculate geometric distributions
    const { domainMap, domains, domainAngles } = calculateDomainAngles(families);
    const familyPositions = calculateFamilyPositions(domains, domainMap, domainAngles, cx, cy, ringRadius2);

    // --- Ring 1: Domain arcs ---
    const domainArc = arc<{ startAngle: number; endAngle: number }>()
      .innerRadius(ringRadius1 - 18)
      .outerRadius(ringRadius1 + 2);

    for (const domain of domains) {
      const angles = domainAngles.get(domain)!;
      g.append('path')
        .attr('transform', `translate(${cx},${cy})`)
        .attr('d', domainArc({ startAngle: angles.start, endAngle: angles.end }))
        .attr('fill', 'none')
        .attr('stroke', domainColor(domain))
        .attr('stroke-width', 1)
        .attr('opacity', 0.7);

      // Domain label
      const labelAngle = angles.mid - Math.PI / 2;
      const labelR = ringRadius1 + 14;
      g.append('text')
        .attr('x', cx + Math.cos(labelAngle) * labelR)
        .attr('y', cy + Math.sin(labelAngle) * labelR)
        .attr('text-anchor', 'middle')
        .attr('dominant-baseline', 'central')
        .attr('fill', domainColor(domain))
        .attr('font-size', '9px')
        .attr('font-family', FONT_MONO)
        .attr('opacity', 0.8)
        .text(domain.toUpperCase());
    }

    // --- Ring 2: Family nodes ---
    // Prevent Maximum Call Stack Size Exceeded errors for excessively large data payloads
    // by using a safe aggregation pipeline instead of `...Math.max(...arrays)` destructors
    const maxUsage = families.reduce((max, f) => Math.max(max, f.usage_count || 1), 1);

    for (const domain of domains) {
      const fams = domainMap.get(domain)!;
      
      fams.forEach((f) => {
        const { x, y } = familyPositions.get(f.id)!;
        const nodeRadius = 4 + (f.usage_count / maxUsage) * 12;

        const isSelected = patternsStore.selectedFamilyId === f.id;

        // Node circle
        const node = g
          .append('circle')
          .attr('cx', x)
          .attr('cy', y)
          .attr('r', nodeRadius)
          .attr('fill', BG_COLOR)
          .attr('stroke', domainColor(f.domain))
          .attr('stroke-width', isSelected ? 2 : 1)
          .attr('cursor', 'pointer')
          .attr('data-family-id', f.id);

        // Hover / click
        node
          .on('mouseenter', function (this: SVGCircleElement) {
            select(this).attr('stroke-width', 2);
            showTooltip(f, x, y);
          })
          .on('mouseleave', function (this: SVGCircleElement) {
            if (patternsStore.selectedFamilyId !== f.id) {
              select(this).attr('stroke-width', 1);
            }
            hideTooltip();
          })
          .on('click', () => {
            const nextSelection = patternsStore.selectedFamilyId === f.id ? null : f.id;
            patternsStore.selectFamily(nextSelection);
            
            // Note: Dedicated Svelte effect handles highlighting automatically now.
          });

        const label = f.intent_label.length > 14 ? f.intent_label.slice(0, 14) + '..' : f.intent_label;
        g.append('text')
          .attr('x', x)
          .attr('y', y + nodeRadius + 10)
          .attr('text-anchor', 'middle')
          .attr('fill', TEXT_DIM)
          .attr('font-size', '8px')
          .attr('font-family', FONT_MONO)
          .attr('pointer-events', 'none')
          .text(label);
      });
    }

    // --- Edges: curved lines between related families ---
    for (const edge of edges) {
      const from = familyPositions.get(edge.from);
      const to = familyPositions.get(edge.to);
      if (!from || !to) continue;

      const pathData = calculateEdgePathDistortion(from, to, cx, cy, 0.3);
      if (!pathData) continue; // Skip rendering if vector math failed (NaN or missing coords)

      // Clamp weight metrics into safe CSS render bounds
      const safeWeight = Math.max(0, edge.weight || 0);

      g.append('path')
        .attr('d', pathData)
        .attr('fill', 'none')
        .attr('stroke', NEON_CYAN)
        .attr('stroke-width', 0.5 + safeWeight * 1.5)
        .attr('opacity', Math.min(1, 0.15 + safeWeight * 0.4))
        .attr('pointer-events', 'none');
    }

    // --- Center node ---
    const centerRadius = 30;
    g.append('circle')
      .attr('cx', cx)
      .attr('cy', cy)
      .attr('r', centerRadius)
      .attr('fill', BG_COLOR)
      .attr('stroke', NEON_CYAN)
      .attr('stroke-width', 1);

    g.append('text')
      .attr('x', cx)
      .attr('y', cy - 6)
      .attr('text-anchor', 'middle')
      .attr('fill', TEXT_PRIMARY)
      .attr('font-size', '14px')
      .attr('font-family', FONT_MONO)
      .attr('font-weight', 'bold')
      .text(String(center.total_families));

    g.append('text')
      .attr('x', cx)
      .attr('y', cy + 8)
      .attr('text-anchor', 'middle')
      .attr('fill', TEXT_DIM)
      .attr('font-size', '8px')
      .attr('font-family', FONT_MONO)
      .text('families');

    // --- Tooltip group (hidden by default) ---
    const tooltip = g.append('g').attr('class', 'mindmap-tooltip').attr('visibility', 'hidden');
    tooltip.append('rect').attr('fill', BG_COLOR).attr('stroke', NEON_CYAN).attr('stroke-width', 1);
    tooltip.append('text').attr('class', 'tt-title').attr('fill', TEXT_PRIMARY).attr('font-size', '9px').attr('font-family', FONT_MONO);
    tooltip.append('text').attr('class', 'tt-meta').attr('fill', TEXT_DIM).attr('font-size', '8px').attr('font-family', FONT_MONO);
    tooltip.append('text').attr('class', 'tt-patterns').attr('fill', NEON_CYAN).attr('font-size', '8px').attr('font-family', FONT_MONO);

    function showTooltip(f: GraphFamily, x: number, y: number) {
      const ttWidth = 200;
      const ttHeight = 58;
      const ttX = x - ttWidth / 2;
      const ttY = y - ttHeight - 20;

      tooltip.attr('visibility', 'visible');
      tooltip.select('rect').attr('x', ttX).attr('y', ttY).attr('width', ttWidth).attr('height', ttHeight);
      tooltip
        .select('.tt-title')
        .attr('x', ttX + 8)
        .attr('y', ttY + 14)
        .text(f.intent_label);
      tooltip
        .select('.tt-meta')
        .attr('x', ttX + 8)
        .attr('y', ttY + 28)
        .text(`${f.domain} | ${f.usage_count} uses | score: ${formatScore(f.avg_score)}`);

      const patternTexts = (f.meta_patterns || [])
        .slice(0, 2)
        .map((mp) => (mp.pattern_text || '').slice(0, 30))
        .join(', ');
      tooltip
        .select('.tt-patterns')
        .attr('x', ttX + 8)
        .attr('y', ttY + 42)
        .text(patternTexts || 'No meta-patterns');
    }

    function hideTooltip() {
      tooltip.attr('visibility', 'hidden');
    }
  }
</script>

<div class="mindmap-container" bind:this={containerEl}>
  {#if patternsStore.graphError}
    <div class="mindmap-empty">
      <span class="mindmap-empty-label">Error loading graph: {patternsStore.graphError}</span>
    </div>
  {:else if patternsStore.graph && patternsStore.graph.families.length === 0}
    <div class="mindmap-empty">
      <span class="mindmap-empty-label">No patterns yet. Optimize prompts to build your knowledge graph.</span>
    </div>
  {:else}
    <svg bind:this={svgEl}></svg>
  {/if}
</div>

<style>
  .mindmap-container {
    width: 100%;
    height: 100%;
    background: var(--color-bg-primary);
    overflow: hidden;
    position: relative;
  }

  .mindmap-container svg {
    display: block;
    width: 100%;
    height: 100%;
  }

  .mindmap-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 100%;
  }

  .mindmap-empty-label {
    font-size: 11px;
    color: var(--color-text-dim);
    font-family: var(--font-mono);
  }
</style>
