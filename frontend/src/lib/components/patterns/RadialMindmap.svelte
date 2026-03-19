<script lang="ts">
  import * as d3 from 'd3';
  import { patternsStore } from '$lib/stores/patterns.svelte';
  import { DOMAIN_COLORS, domainColor } from '$lib/constants/patterns';
  import type { GraphFamily, GraphEdge } from '$lib/api/patterns';

  // Design system tokens — must match :root in app.css.
  // D3 sets SVG attributes directly, so we need raw values here.
  const BG_COLOR = '#06060c';       // --color-bg-primary
  const NEON_CYAN = '#00e5ff';      // --color-neon-cyan
  const TEXT_PRIMARY = '#e4e4f0';   // --color-text-primary
  const TEXT_DIM = '#7a7a9e';       // --color-text-dim
  const FONT_MONO = "'Geist Mono', 'JetBrains Mono', ui-monospace, monospace";

  let svgEl = $state<SVGSVGElement>(undefined!);
  let containerEl = $state<HTMLDivElement>(undefined!);
  let selectedFamilyId = $state<string | null>(null);

  // Load graph on mount and reload when invalidated
  $effect(() => {
    // Track graphLoaded — when it transitions to false (invalidation), reload
    const gl = patternsStore.graphLoaded;
    if (!gl) {
      patternsStore.loadGraph();
    }
  });

  // Render D3 visualization when graph data changes
  $effect(() => {
    const graph = patternsStore.graph;
    if (!graph || !svgEl || !containerEl) return;
    renderGraph(graph.families, graph.edges, graph.center);
  });

  // Re-render on container resize
  $effect(() => {
    if (!containerEl) return;
    const observer = new ResizeObserver(() => {
      const graph = patternsStore.graph;
      if (graph && svgEl) {
        renderGraph(graph.families, graph.edges, graph.center);
      }
    });
    observer.observe(containerEl);
    return () => observer.disconnect();
  });

  function renderGraph(
    families: GraphFamily[],
    edges: GraphEdge[],
    center: { total_families: number; total_patterns: number; total_optimizations: number },
  ) {
    const svg = d3.select(svgEl);
    svg.selectAll('*').remove();

    const rect = containerEl.getBoundingClientRect();
    const width = rect.width || 800;
    const height = rect.height || 600;
    const cx = width / 2;
    const cy = height / 2;

    svg.attr('width', width).attr('height', height).attr('viewBox', `0 0 ${width} ${height}`);

    // Zoom group
    const g = svg.append('g');
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 4])
      .on('zoom', (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
        g.attr('transform', event.transform.toString());
      });
    svg.call(zoom);

    // Group families by domain
    const domainMap = new Map<string, GraphFamily[]>();
    for (const f of families) {
      const d = f.domain || 'general';
      if (!domainMap.has(d)) domainMap.set(d, []);
      domainMap.get(d)!.push(f);
    }
    const domains = Array.from(domainMap.keys()).sort();

    // Radii
    const ringRadius1 = Math.min(width, height) * 0.2; // domain ring
    const ringRadius2 = Math.min(width, height) * 0.35; // family ring

    // --- Ring 1: Domain arcs ---
    const domainArc = d3
      .arc<{ startAngle: number; endAngle: number }>()
      .innerRadius(ringRadius1 - 18)
      .outerRadius(ringRadius1 + 2);

    let angleOffset = 0;
    const totalFamilies = families.length || 1;
    const domainAngles = new Map<string, { start: number; end: number; mid: number }>();

    for (const domain of domains) {
      const count = domainMap.get(domain)!.length;
      const sweep = (count / totalFamilies) * Math.PI * 2;
      const start = angleOffset;
      const end = angleOffset + sweep;
      domainAngles.set(domain, { start, end, mid: (start + end) / 2 });

      g.append('path')
        .attr('transform', `translate(${cx},${cy})`)
        .attr('d', domainArc({ startAngle: start, endAngle: end }))
        .attr('fill', 'none')
        .attr('stroke', domainColor(domain))
        .attr('stroke-width', 1)
        .attr('opacity', 0.7);

      // Domain label
      const labelAngle = (start + end) / 2 - Math.PI / 2;
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

      angleOffset = end;
    }

    // --- Ring 2: Family nodes ---
    const familyPositions = new Map<string, { x: number; y: number }>();
    const maxUsage = Math.max(1, ...families.map((f) => f.usage_count));

    for (const domain of domains) {
      const fams = domainMap.get(domain)!;
      const angles = domainAngles.get(domain)!;
      const step = fams.length > 1 ? (angles.end - angles.start) / fams.length : 0;

      fams.forEach((f, i) => {
        const angle = angles.start + step * (i + 0.5) - Math.PI / 2;
        const x = cx + Math.cos(angle) * ringRadius2;
        const y = cy + Math.sin(angle) * ringRadius2;
        familyPositions.set(f.id, { x, y });

        const nodeRadius = 4 + (f.usage_count / maxUsage) * 12;

        // Node circle
        const node = g
          .append('circle')
          .attr('cx', x)
          .attr('cy', y)
          .attr('r', nodeRadius)
          .attr('fill', BG_COLOR)
          .attr('stroke', domainColor(f.domain))
          .attr('stroke-width', selectedFamilyId === f.id ? 2 : 1)
          .attr('cursor', 'pointer')
          .attr('data-family-id', f.id);

        // Hover / click
        node
          .on('mouseenter', function (this: SVGCircleElement) {
            d3.select(this).attr('stroke-width', 2);
            showTooltip(f, x, y);
          })
          .on('mouseleave', function (this: SVGCircleElement) {
            if (selectedFamilyId !== f.id) {
              d3.select(this).attr('stroke-width', 1);
            }
            hideTooltip();
          })
          .on('click', () => {
            selectedFamilyId = selectedFamilyId === f.id ? null : f.id;
            // Reset all strokes then highlight selected
            g.selectAll('circle[data-family-id]').attr('stroke-width', 1);
            if (selectedFamilyId) {
              g.select(`circle[data-family-id="${selectedFamilyId}"]`).attr('stroke-width', 2);
            }
            // Sync with patterns store so Inspector shows family detail
            patternsStore.selectFamily(selectedFamilyId);
          });

        // Label (intent_label, truncated)
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

      // Quadratic bezier through center-ish point
      const midX = (from.x + to.x) / 2 + (cx - (from.x + to.x) / 2) * 0.3;
      const midY = (from.y + to.y) / 2 + (cy - (from.y + to.y) / 2) * 0.3;

      g.append('path')
        .attr('d', `M${from.x},${from.y} Q${midX},${midY} ${to.x},${to.y}`)
        .attr('fill', 'none')
        .attr('stroke', NEON_CYAN)
        .attr('stroke-width', 0.5 + edge.weight * 1.5)
        .attr('opacity', 0.15 + edge.weight * 0.4)
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
        .text(`${f.domain} | ${f.usage_count} uses | score: ${f.avg_score?.toFixed(1) ?? 'N/A'}`);

      const patternTexts = f.meta_patterns
        .slice(0, 2)
        .map((mp) => mp.pattern_text.slice(0, 30))
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
