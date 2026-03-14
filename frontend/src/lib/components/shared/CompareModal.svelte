<script lang="ts">
  import { onMount } from 'svelte';
  import { slide } from 'svelte/transition';
  import {
    compareOptimizations,
    mergeOptimizations,
    acceptMerge,
    type CompareResponse,
  } from '$lib/api/client';
  import DiffView from '$lib/components/shared/DiffView.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { history } from '$lib/stores/history.svelte';
  import { toast } from '$lib/stores/toast.svelte';

  // Typed sub-object interfaces matching the backend Pydantic models
  interface Structural {
    a_input_words: number;
    b_input_words: number;
    a_output_words: number;
    b_output_words: number;
    a_expansion: number;
    b_expansion: number;
    a_complexity: string | null;
    b_complexity: string | null;
  }

  interface Efficiency {
    a_duration_ms: number | null;
    b_duration_ms: number | null;
    a_tokens: number | null;
    b_tokens: number | null;
    a_cost: number | null;
    b_cost: number | null;
    a_score_per_token: number | null;
    b_score_per_token: number | null;
  }

  interface Strategy {
    a_framework: string | null;
    a_source: string | null;
    a_rationale: string | null;
    a_guardrails: string[];
    b_framework: string | null;
    b_source: string | null;
    b_rationale: string | null;
    b_guardrails: string[];
  }

  interface Context {
    a_repo: string | null;
    b_repo: string | null;
    a_has_codebase: boolean;
    b_has_codebase: boolean;
    a_instruction_count: number;
    b_instruction_count: number;
  }

  interface Adaptation {
    feedbacks_between: number;
    weight_shifts: Record<string, number>;
    guardrails_added: string[];
  }

  let {
    idA,
    idB,
    onclose,
  }: {
    idA: string;
    idB: string;
    onclose: () => void;
  } = $props();

  // ---- Phase state ----
  type Phase = 'analyze' | 'merge' | 'commit';
  let phase = $state<Phase>('analyze');
  let compareData = $state<CompareResponse | null>(null);
  let loading = $state(true);
  let mergedText = $state('');
  let mergeError = $state(false);
  let mergeTokens = $state(0);
  let mergeModel = $state('');
  let mergeController = $state<AbortController | null>(null);
  let accepting = $state(false);

  // Accordion open state — all collapsed initially
  let openAccordions = $state<Record<string, boolean>>({
    structural: false,
    efficiency: false,
    strategy: false,
    context: false,
  });

  // Typed derived accessors for sub-objects
  let str = $derived(compareData?.structural as Structural | undefined);
  let eff = $derived(compareData?.efficiency as Efficiency | undefined);
  let strat = $derived(compareData?.strategy as Strategy | undefined);
  let ctx = $derived(compareData?.context as Context | undefined);
  let adpt = $derived(compareData?.adaptation as Adaptation | undefined);
  let aOpt = $derived(compareData?.a as Record<string, unknown> | undefined);
  let bOpt = $derived(compareData?.b as Record<string, unknown> | undefined);

  // ---- Fetch compare data on mount ----
  onMount(() => {
    loadCompare();
    return () => {
      mergeController?.abort();
    };
  });

  async function loadCompare() {
    loading = true;
    try {
      compareData = await compareOptimizations(idA, idB);
    } catch (err) {
      toast.error(`Compare failed: ${(err as Error).message}`);
      onclose();
    } finally {
      loading = false;
    }
  }

  // ---- Score helpers ----
  function deltaClass(d: number | null): string {
    if (d == null) return 'text-text-dim';
    if (d > 0) return 'text-neon-green';
    if (d < 0) return 'text-neon-red';
    return 'text-text-dim';
  }

  function deltaLabel(d: number | null): string {
    if (d == null) return '\u2014';
    if (d > 0) return `+${d.toFixed(1)}`;
    return d.toFixed(1);
  }

  function barWidth(score: number | null | undefined): string {
    if (score == null) return '0%';
    return `${Math.max(0, Math.min(100, score * 10))}%`;
  }

  // ---- Situation badge ----
  function situationBadgeClasses(sit: string): string {
    switch (sit) {
      case 'REFORGE':
        return 'font-mono text-[9px] font-medium px-1.5 py-0.5 border border-neon-green/35 text-neon-green bg-neon-green/5';
      case 'STRATEGY':
        return 'font-mono text-[9px] font-medium px-1.5 py-0.5 border border-neon-purple/35 text-neon-purple bg-neon-purple/5';
      case 'EVOLVED':
        return 'font-mono text-[9px] font-medium px-1.5 py-0.5 border border-neon-yellow/35 text-neon-yellow bg-neon-yellow/5';
      case 'CROSS':
        return 'font-mono text-[9px] font-medium px-1.5 py-0.5 border border-neon-blue/35 text-neon-blue bg-neon-blue/5';
      default:
        return 'font-mono text-[9px] font-medium px-1.5 py-0.5 border border-border-subtle text-text-dim';
    }
  }

  // ---- Accordion toggle ----
  function toggleAccordion(key: string) {
    openAccordions[key] = !openAccordions[key];
  }

  // ---- Merge phase ----
  async function startMerge() {
    phase = 'merge';
    mergedText = '';
    mergeError = false;
    mergeTokens = 0;
    // Collapse all accordions
    openAccordions = { structural: false, efficiency: false, strategy: false, context: false };

    mergeController = await mergeOptimizations(
      idA,
      idB,
      (chunk: string) => {
        mergedText += chunk;
        mergeTokens += chunk.split(/\s+/).filter(Boolean).length;
      },
      (err: Error) => {
        mergeError = true;
        toast.error(`Merge error: ${err.message}`);
      },
      () => {
        phase = 'commit';
      },
    );
  }

  function retryMerge() {
    mergeController?.abort();
    startMerge();
  }

  // ---- Commit phase ----
  async function handleAccept() {
    if (accepting) return;
    accepting = true;
    try {
      const result = await acceptMerge(idA, idB, mergedText);
      // Open the merged prompt as a new prompt tab — user forges it through the pipeline
      editor.openTab({
        id: `prompt-${result.optimization_id}`,
        label: 'Merged Prompt',
        type: 'prompt',
        promptText: mergedText,
        dirty: false,
      });
      // Refresh history
      await history.loadHistory();
      toast.success('Merge accepted — new optimization created');
      onclose();
    } catch (err) {
      toast.error(`Accept failed: ${(err as Error).message}`);
      // Stay on phase 3
    } finally {
      accepting = false;
    }
  }

  // ---- Phase 3 blocking ----
  function handleKeydown(e: KeyboardEvent) {
    if (phase === 'commit') {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
      }
    } else if (e.key === 'Escape') {
      onclose();
    }
  }

  function handleBackdropClick(e: MouseEvent) {
    if (phase === 'commit') return;
    if (e.target === e.currentTarget) onclose();
  }

  // Block beforeunload in commit phase
  function handleBeforeUnload(e: BeforeUnloadEvent) {
    if (phase === 'commit') {
      e.preventDefault();
    }
  }

  // ---- Efficiency helpers ----
  function fmtDuration(ms: number | null | undefined): string {
    if (ms == null) return '\u2014';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  function fmtTokens(t: number | null | undefined): string {
    if (t == null) return '\u2014';
    if (t >= 1000) return `${(t / 1000).toFixed(1)}k`;
    return `${t}`;
  }

  function fmtCost(c: number | null | undefined): string {
    if (c == null) return '\u2014';
    return `$${c.toFixed(4)}`;
  }

  function effBarWidth(val: number | null | undefined, max: number): string {
    if (val == null || max === 0) return '0%';
    return `${Math.max(0, Math.min(100, (val / max) * 100))}%`;
  }
</script>

<svelte:window onkeydown={handleKeydown} onbeforeunload={handleBeforeUnload} />

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="fixed inset-0 z-50 flex items-center justify-center p-4"
  style="background: rgba(12,12,22,0.7); backdrop-filter: blur(8px);"
  onclick={handleBackdropClick}
  role="dialog"
  aria-modal="true"
  aria-label="Optimization comparison"
  tabindex="-1"
>
  <div
    class="border border-border-subtle bg-bg-card w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col"
    onclick={(e) => e.stopPropagation()}
    role="presentation"
  >
    <!-- Header -->
    <div class="h-8 flex items-center justify-between px-2 border-b border-border-subtle shrink-0">
      <div class="flex items-center gap-2">
        <span class="font-display text-[11px] font-bold uppercase tracking-wider text-text-dim">
          Compare
        </span>

        {#if compareData && strat}
          <span class={situationBadgeClasses(compareData.situation)}>
            {compareData.situation}
          </span>

          <!-- Framework labels -->
          <span class="font-mono text-[10px] text-neon-purple/80 border border-neon-purple/20 px-1 py-0.5">
            {strat.a_framework ?? 'A'}
          </span>
          <span class="font-mono text-[9px] text-text-dim/40">vs</span>
          <span class="font-mono text-[10px] text-neon-blue/80 border border-neon-blue/20 px-1 py-0.5">
            {strat.b_framework ?? 'B'}
          </span>

          {#if compareData.a_is_trashed}
            <span class="font-mono text-[8px] text-neon-red/70 border border-neon-red/20 px-1 py-0.5">TRASHED</span>
          {/if}
          {#if compareData.b_is_trashed}
            <span class="font-mono text-[8px] text-neon-red/70 border border-neon-red/20 px-1 py-0.5">TRASHED</span>
          {/if}
        {/if}
      </div>

      {#if phase !== 'commit'}
        <button
          class="w-6 h-6 flex items-center justify-center text-text-dim hover:text-text-primary transition-colors duration-200"
          onclick={onclose}
          aria-label="Close comparison"
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"></path>
          </svg>
        </button>
      {/if}
    </div>

    <!-- Loading skeleton -->
    {#if loading}
      <div class="flex-1 flex items-center justify-center p-8">
        <div class="flex flex-col items-center gap-2">
          <div class="w-32 h-1 bg-border-subtle overflow-hidden">
            <div class="h-full bg-neon-cyan/40" style="width:60%;"></div>
          </div>
          <span class="font-mono text-[10px] text-text-dim">Analyzing optimizations...</span>
        </div>
      </div>

    {:else if compareData && str && eff && strat && ctx && adpt}
      <!-- Scrollable content -->
      <div class="overflow-y-auto flex-1 min-h-0">

        <!-- Insight strip -->
        <div class="px-2 py-1.5 border-b border-border-subtle">
          <div class="font-mono text-[10px] text-text-primary mb-1">
            {compareData.insight_headline}
          </div>
          {#if compareData.modifiers.length > 0}
            <div class="flex flex-wrap gap-1 mb-1">
              {#each compareData.modifiers as mod}
                <span class="font-mono text-[8px] px-1 py-0.5 border border-neon-teal/25 text-neon-teal uppercase tracking-wider">
                  {mod}
                </span>
              {/each}
            </div>
          {/if}
          {#each compareData.top_insights.slice(0, 3) as insight}
            <div class="font-mono text-[10px] text-text-secondary" style="font-variant-numeric: tabular-nums;">
              &#9656; {insight}
            </div>
          {/each}
        </div>

        <!-- Score table -->
        {#if compareData.scores.dimensions.length > 0}
          <div class="px-2 py-1.5 border-b border-border-subtle">
            <table class="w-full border-collapse">
              <thead>
                <tr class="border-b border-border-subtle">
                  <th class="text-left py-0.5 pr-2 font-mono text-[9px] font-medium uppercase tracking-wider text-text-dim">Dimension</th>
                  <th class="text-right py-0.5 px-2 font-mono text-[9px] font-medium text-neon-purple/70">A</th>
                  <th class="py-0.5 px-2 w-24"></th>
                  <th class="text-right py-0.5 px-2 font-mono text-[9px] font-medium text-neon-blue/70">B</th>
                  <th class="text-right py-0.5 pl-2 font-mono text-[9px] font-medium text-text-dim">Delta</th>
                </tr>
              </thead>
              <tbody>
                {#each compareData.scores.dimensions as dim}
                  {@const aScore = compareData.scores.a_scores[dim] ?? null}
                  {@const bScore = compareData.scores.b_scores[dim] ?? null}
                  {@const d = compareData.scores.deltas[dim] ?? null}
                  {@const winnerSide = d != null ? (d > 0 ? 'a' : d < 0 ? 'b' : null) : null}
                  <tr class="h-5 border-b border-border-subtle/30">
                    <td class="pr-2 font-mono text-[10px] text-text-secondary capitalize" style="font-variant-numeric: tabular-nums;">
                      <span class="flex items-center gap-1">
                        {#if winnerSide}
                          <span class="inline-block w-1 h-1 bg-neon-cyan shrink-0"></span>
                        {:else}
                          <span class="inline-block w-1 h-1 shrink-0"></span>
                        {/if}
                        {dim.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td class="px-2 text-right font-mono text-[10px] text-text-secondary" style="font-variant-numeric: tabular-nums;">
                      {aScore != null ? aScore.toFixed(1) : '\u2014'}
                    </td>
                    <td class="px-2">
                      <!-- Inline sparkline bars -->
                      <div class="flex items-center gap-px h-2">
                        <div class="flex-1 flex justify-end">
                          <div class="h-full bg-neon-purple/30" style="width: {barWidth(aScore)}"></div>
                        </div>
                        <div class="w-px h-3 bg-border-subtle shrink-0"></div>
                        <div class="flex-1">
                          <div class="h-full bg-neon-blue/30" style="width: {barWidth(bScore)}"></div>
                        </div>
                      </div>
                    </td>
                    <td class="px-2 text-right font-mono text-[10px] text-text-secondary" style="font-variant-numeric: tabular-nums;">
                      {bScore != null ? bScore.toFixed(1) : '\u2014'}
                    </td>
                    <td class="pl-2 text-right font-mono text-[10px] font-semibold {deltaClass(d)}" style="font-variant-numeric: tabular-nums;">
                      {deltaLabel(d)}
                    </td>
                  </tr>
                {/each}
                <!-- Overall row -->
                {#if compareData.scores.overall_delta != null}
                  <tr class="h-5 border-t border-border-subtle">
                    <td class="pr-2 font-mono text-[10px] text-text-primary font-semibold uppercase" style="font-variant-numeric: tabular-nums;">
                      Overall
                    </td>
                    <td class="px-2 text-right font-mono text-[10px] text-text-primary font-semibold" style="font-variant-numeric: tabular-nums;">
                      {aOpt?.overall_score != null ? Number(aOpt.overall_score).toFixed(1) : '\u2014'}
                    </td>
                    <td></td>
                    <td class="px-2 text-right font-mono text-[10px] text-text-primary font-semibold" style="font-variant-numeric: tabular-nums;">
                      {bOpt?.overall_score != null ? Number(bOpt.overall_score).toFixed(1) : '\u2014'}
                    </td>
                    <td class="pl-2 text-right font-mono text-[10px] font-bold {deltaClass(compareData.scores.overall_delta)}" style="font-variant-numeric: tabular-nums;">
                      {deltaLabel(compareData.scores.overall_delta)}
                    </td>
                  </tr>
                {/if}
              </tbody>
            </table>
          </div>
        {/if}

        <!-- Accordion: Structural -->
        <div class="border-b border-border-subtle">
          <button
            class="h-7 flex items-center justify-between px-2 cursor-pointer hover:bg-bg-hover/15 transition-colors duration-200 w-full text-left"
            onclick={() => toggleAccordion('structural')}
          >
            <span class="font-display text-[10px] font-bold uppercase tracking-wider text-text-dim">
              Structural
            </span>
            <span class="font-mono text-[9px] text-text-dim">
              {str.a_output_words}w / {str.b_output_words}w &middot; {str.a_expansion.toFixed(1)}x / {str.b_expansion.toFixed(1)}x
            </span>
          </button>
          {#if openAccordions.structural}
            <div transition:slide={{ duration: 200 }} class="px-2 pb-2">
              <div class="grid grid-cols-4 gap-1.5">
                <div class="border border-border-subtle p-1.5">
                  <div class="font-mono text-[8px] text-text-dim uppercase mb-0.5">Input Words</div>
                  <div class="flex justify-between font-mono text-[10px]" style="font-variant-numeric: tabular-nums;">
                    <span class="text-neon-purple/80">{str.a_input_words}</span>
                    <span class="text-neon-blue/80">{str.b_input_words}</span>
                  </div>
                </div>
                <div class="border border-border-subtle p-1.5">
                  <div class="font-mono text-[8px] text-text-dim uppercase mb-0.5">Output Words</div>
                  <div class="flex justify-between font-mono text-[10px]" style="font-variant-numeric: tabular-nums;">
                    <span class="text-neon-purple/80">{str.a_output_words}</span>
                    <span class="text-neon-blue/80">{str.b_output_words}</span>
                  </div>
                </div>
                <div class="border border-border-subtle p-1.5">
                  <div class="font-mono text-[8px] text-text-dim uppercase mb-0.5">Expansion</div>
                  <div class="flex justify-between font-mono text-[10px]" style="font-variant-numeric: tabular-nums;">
                    <span class="text-neon-purple/80">{str.a_expansion.toFixed(1)}x</span>
                    <span class="text-neon-blue/80">{str.b_expansion.toFixed(1)}x</span>
                  </div>
                </div>
                <div class="border border-border-subtle p-1.5">
                  <div class="font-mono text-[8px] text-text-dim uppercase mb-0.5">Complexity</div>
                  <div class="flex justify-between font-mono text-[10px]" style="font-variant-numeric: tabular-nums;">
                    <span class="text-neon-purple/80">{str.a_complexity ?? '\u2014'}</span>
                    <span class="text-neon-blue/80">{str.b_complexity ?? '\u2014'}</span>
                  </div>
                </div>
              </div>
            </div>
          {/if}
        </div>

        <!-- Accordion: Efficiency -->
        <div class="border-b border-border-subtle">
          <button
            class="h-7 flex items-center justify-between px-2 cursor-pointer hover:bg-bg-hover/15 transition-colors duration-200 w-full text-left"
            onclick={() => toggleAccordion('efficiency')}
          >
            <span class="font-display text-[10px] font-bold uppercase tracking-wider text-text-dim">
              Efficiency
            </span>
            <span class="font-mono text-[9px] text-text-dim">
              {fmtDuration(eff.a_duration_ms)} / {fmtDuration(eff.b_duration_ms)}
            </span>
          </button>
          {#if openAccordions.efficiency}
            <div transition:slide={{ duration: 200 }} class="px-2 pb-2">
              <!-- Duration bars -->
              <div class="mb-1.5">
                <div class="font-mono text-[8px] text-text-dim uppercase mb-0.5">Duration</div>
                <div class="flex items-center gap-2 h-5" style="font-variant-numeric: tabular-nums;">
                    <span class="font-mono text-[10px] text-neon-purple/80 w-12 text-right shrink-0">{fmtDuration(eff.a_duration_ms)}</span>
                    <div class="flex-1 h-2 bg-bg-secondary/40 overflow-hidden">
                      <div class="h-full bg-neon-purple/30" style="width: {effBarWidth(eff.a_duration_ms, Math.max(eff.a_duration_ms ?? 0, eff.b_duration_ms ?? 0))}"></div>
                    </div>
                  </div>
                  <div class="flex items-center gap-2 h-5" style="font-variant-numeric: tabular-nums;">
                    <span class="font-mono text-[10px] text-neon-blue/80 w-12 text-right shrink-0">{fmtDuration(eff.b_duration_ms)}</span>
                    <div class="flex-1 h-2 bg-bg-secondary/40 overflow-hidden">
                      <div class="h-full bg-neon-blue/30" style="width: {effBarWidth(eff.b_duration_ms, Math.max(eff.a_duration_ms ?? 0, eff.b_duration_ms ?? 0))}"></div>
                    </div>
                  </div>
              </div>
              <!-- Token bars -->
              <div class="mb-1.5">
                <div class="font-mono text-[8px] text-text-dim uppercase mb-0.5">Tokens</div>
                <div class="flex items-center gap-2 h-5" style="font-variant-numeric: tabular-nums;">
                  <span class="font-mono text-[10px] text-neon-purple/80 w-12 text-right shrink-0">{fmtTokens(eff.a_tokens)}</span>
                  <div class="flex-1 h-2 bg-bg-secondary/40 overflow-hidden">
                    <div class="h-full bg-neon-purple/30" style="width: {effBarWidth(eff.a_tokens, Math.max(eff.a_tokens ?? 0, eff.b_tokens ?? 0))}"></div>
                  </div>
                </div>
                <div class="flex items-center gap-2 h-5" style="font-variant-numeric: tabular-nums;">
                  <span class="font-mono text-[10px] text-neon-blue/80 w-12 text-right shrink-0">{fmtTokens(eff.b_tokens)}</span>
                  <div class="flex-1 h-2 bg-bg-secondary/40 overflow-hidden">
                    <div class="h-full bg-neon-blue/30" style="width: {effBarWidth(eff.b_tokens, Math.max(eff.a_tokens ?? 0, eff.b_tokens ?? 0))}"></div>
                  </div>
                </div>
              </div>
              <!-- Cost + Score/Token -->
              <div class="flex gap-4 font-mono text-[10px]" style="font-variant-numeric: tabular-nums;">
                <div>
                  <span class="text-text-dim text-[8px] uppercase">Cost: </span>
                  <span class="text-neon-purple/80">{fmtCost(eff.a_cost)}</span>
                  <span class="text-text-dim/40 mx-1">/</span>
                  <span class="text-neon-blue/80">{fmtCost(eff.b_cost)}</span>
                </div>
                <div>
                  <span class="text-text-dim text-[8px] uppercase">Score/Token: </span>
                  <span class="text-neon-purple/80">{eff.a_score_per_token?.toFixed(4) ?? '\u2014'}</span>
                  <span class="text-text-dim/40 mx-1">/</span>
                  <span class="text-neon-blue/80">{eff.b_score_per_token?.toFixed(4) ?? '\u2014'}</span>
                </div>
              </div>
            </div>
          {/if}
        </div>

        <!-- Accordion: Strategy -->
        <div class="border-b border-border-subtle">
          <button
            class="h-7 flex items-center justify-between px-2 cursor-pointer hover:bg-bg-hover/15 transition-colors duration-200 w-full text-left"
            onclick={() => toggleAccordion('strategy')}
          >
            <span class="font-display text-[10px] font-bold uppercase tracking-wider text-text-dim">
              Strategy
            </span>
            <span class="font-mono text-[9px] text-text-dim">
              {strat.a_framework ?? 'none'} / {strat.b_framework ?? 'none'}
            </span>
          </button>
          {#if openAccordions.strategy}
            <div transition:slide={{ duration: 200 }} class="px-2 pb-2">
              <div class="grid grid-cols-2 gap-1.5">
                <!-- A card -->
                <div class="border border-neon-purple/15 p-1.5">
                  <div class="font-mono text-[10px] text-neon-purple/80 font-semibold mb-0.5">{strat.a_framework ?? 'None'}</div>
                  <div class="font-mono text-[9px] text-text-dim mb-0.5">{strat.a_source ?? ''}</div>
                  <div class="font-mono text-[9px] text-text-secondary">{strat.a_rationale ?? ''}</div>
                  {#if strat.a_guardrails.length > 0}
                    <div class="flex flex-wrap gap-0.5 mt-1">
                      {#each strat.a_guardrails as g}
                        <span class="font-mono text-[8px] px-1 py-0.5 border border-border-subtle text-text-dim">{g}</span>
                      {/each}
                    </div>
                  {/if}
                </div>
                <!-- B card -->
                <div class="border border-neon-blue/15 p-1.5">
                  <div class="font-mono text-[10px] text-neon-blue/80 font-semibold mb-0.5">{strat.b_framework ?? 'None'}</div>
                  <div class="font-mono text-[9px] text-text-dim mb-0.5">{strat.b_source ?? ''}</div>
                  <div class="font-mono text-[9px] text-text-secondary">{strat.b_rationale ?? ''}</div>
                  {#if strat.b_guardrails.length > 0}
                    <div class="flex flex-wrap gap-0.5 mt-1">
                      {#each strat.b_guardrails as g}
                        <span class="font-mono text-[8px] px-1 py-0.5 border border-border-subtle text-text-dim">{g}</span>
                      {/each}
                    </div>
                  {/if}
                </div>
              </div>
            </div>
          {/if}
        </div>

        <!-- Accordion: Context & Adaptation -->
        <div class="border-b border-border-subtle">
          <button
            class="h-7 flex items-center justify-between px-2 cursor-pointer hover:bg-bg-hover/15 transition-colors duration-200 w-full text-left"
            onclick={() => toggleAccordion('context')}
          >
            <span class="font-display text-[10px] font-bold uppercase tracking-wider text-text-dim">
              Context &amp; Adaptation
            </span>
            <span class="font-mono text-[9px] text-text-dim">
              {adpt.feedbacks_between} feedback{adpt.feedbacks_between !== 1 ? 's' : ''} between
            </span>
          </button>
          {#if openAccordions.context}
            <div transition:slide={{ duration: 200 }} class="px-2 pb-2">
              <!-- Repo delta -->
              <div class="mb-1.5">
                <div class="font-mono text-[8px] text-text-dim uppercase mb-0.5">Repo Context</div>
                <div class="flex gap-4 font-mono text-[10px]" style="font-variant-numeric: tabular-nums;">
                  <span class="text-neon-purple/80">{ctx.a_repo ?? 'none'}{ctx.a_has_codebase ? ' (indexed)' : ''}</span>
                  <span class="text-neon-blue/80">{ctx.b_repo ?? 'none'}{ctx.b_has_codebase ? ' (indexed)' : ''}</span>
                </div>
              </div>
              <!-- Feedbacks & weight shifts -->
              <div class="mb-1.5">
                <div class="font-mono text-[8px] text-text-dim uppercase mb-0.5">Feedbacks Between</div>
                <div class="font-mono text-[10px] text-text-secondary" style="font-variant-numeric: tabular-nums;">
                  {adpt.feedbacks_between}
                </div>
              </div>
              {#if Object.keys(adpt.weight_shifts).length > 0}
                <div>
                  <div class="font-mono text-[8px] text-text-dim uppercase mb-0.5">Weight Shifts</div>
                  {#each Object.entries(adpt.weight_shifts) as [dim, shift]}
                    <div class="flex items-center justify-between h-5 font-mono text-[10px]" style="font-variant-numeric: tabular-nums;">
                      <span class="text-text-secondary capitalize">{dim.replace(/_/g, ' ')}</span>
                      <span class={deltaClass(shift)}>{deltaLabel(shift)}</span>
                    </div>
                  {/each}
                </div>
              {/if}
              {#if adpt.guardrails_added.length > 0}
                <div class="mt-1">
                  <div class="font-mono text-[8px] text-text-dim uppercase mb-0.5">Guardrails Added</div>
                  <div class="flex flex-wrap gap-0.5">
                    {#each adpt.guardrails_added as g}
                      <span class="font-mono text-[8px] px-1 py-0.5 border border-neon-yellow/20 text-neon-yellow/70">{g}</span>
                    {/each}
                  </div>
                </div>
              {/if}
            </div>
          {/if}
        </div>

        <!-- Guidance card -->
        {#if compareData.guidance && phase === 'analyze'}
          <div class="border-border-accent bg-neon-cyan/5 p-1.5 mx-2 my-1.5 border">
            <div class="font-display text-[10px] font-bold uppercase tracking-wider text-text-dim mb-1">
              Guidance
            </div>
            <div class="font-mono text-[10px] text-text-primary mb-1">
              {compareData.guidance.headline}
            </div>
            {#if compareData.guidance.merge_directives.length > 0}
              <div class="mb-1">
                {#each compareData.guidance.merge_directives as directive}
                  <div class="font-mono text-[10px] text-text-secondary" style="font-variant-numeric: tabular-nums;">
                    &#8594; {directive}
                  </div>
                {/each}
              </div>
            {/if}
            <div class="flex flex-wrap gap-1">
              {#each compareData.guidance.strengths_a as s}
                <span class="font-mono text-[8px] px-1 py-0.5 border border-neon-purple/25 text-neon-purple/70">A: {s}</span>
              {/each}
              {#each compareData.guidance.strengths_b as s}
                <span class="font-mono text-[8px] px-1 py-0.5 border border-neon-blue/25 text-neon-blue/70">B: {s}</span>
              {/each}
              {#each compareData.guidance.persistent_weaknesses as w}
                <span class="font-mono text-[8px] px-1 py-0.5 border border-neon-yellow/20 text-neon-yellow/70 capitalize">{w}</span>
              {/each}
            </div>
          </div>
        {/if}

        <!-- DiffView (Phase 1) -->
        {#if phase === 'analyze'}
          <div class="px-2 py-1.5 border-b border-border-subtle">
            <div class="font-display text-[10px] font-bold uppercase tracking-wider text-text-dim mb-1">
              Prompt Diff
            </div>
            <DiffView
              original={String(aOpt?.optimized_prompt ?? '')}
              modified={String(bOpt?.optimized_prompt ?? '')}
            />
          </div>
        {/if}

        <!-- Phase 2: Merge streaming preview -->
        {#if phase === 'merge' || phase === 'commit'}
          <div class="px-2 py-1.5">
            <div class="font-display text-[10px] font-bold uppercase tracking-wider text-text-dim mb-1">
              {phase === 'merge' ? 'Merging...' : 'Merged Prompt'}
            </div>
            <div
              class="bg-bg-input p-1.5 font-mono text-[10px] max-h-40 overflow-y-auto whitespace-pre-wrap break-words border {mergeError ? 'border-neon-red/40' : 'border-neon-teal/20'}"
            >
              {mergedText || (phase === 'merge' ? 'Waiting for stream...' : '')}
            </div>
            {#if phase === 'merge'}
              <div class="flex items-center justify-between mt-1">
                <span class="font-mono text-[9px] text-text-dim">
                  Streaming &middot; ~{mergeTokens} words
                </span>
                {#if mergeError}
                  <button
                    class="font-mono text-[10px] px-2 py-0.5 border border-neon-teal/40 text-neon-teal hover:bg-neon-teal/10 transition-colors duration-200"
                    onclick={retryMerge}
                  >
                    Retry
                  </button>
                {/if}
              </div>
            {/if}
          </div>
        {/if}

        <!-- Phase 3: Warning text -->
        {#if phase === 'commit'}
          <div class="px-2 py-1 border-t border-border-subtle">
            <div class="font-mono text-[8px] text-neon-yellow">
              WARNING: Accept will archive both parent optimizations and create a new merged entry. This action cannot be undone.
            </div>
          </div>
        {/if}

      </div>

      <!-- Pinned action bar -->
      <div class="h-8 flex items-center justify-between px-2 border-t border-border-subtle bg-bg-secondary/40 shrink-0">
        {#if phase === 'analyze'}
          <button
            class="font-mono text-[10px] px-2 py-1 text-text-dim hover:text-text-primary transition-colors duration-200"
            onclick={onclose}
          >
            Close
          </button>
          <button
            class="font-mono text-[10px] px-3 py-1 border border-neon-teal/40 text-neon-teal bg-neon-teal/5 hover:bg-neon-teal/10 transition-colors duration-200 uppercase tracking-wider disabled:opacity-40 disabled:cursor-not-allowed"
            onclick={startMerge}
            disabled={!compareData.guidance}
            title={compareData.guidance ? 'Synthesize best qualities from both prompts' : 'Merge unavailable — guidance generation failed'}
          >
            Merge Insights
          </button>
        {:else if phase === 'merge'}
          <span class="font-mono text-[10px] text-text-dim">
            Streaming merge...
          </span>
          <button
            class="font-mono text-[10px] px-2 py-1 text-text-dim hover:text-text-primary transition-colors duration-200"
            onclick={() => { mergeController?.abort(); phase = 'analyze'; mergedText = ''; }}
          >
            Cancel
          </button>
        {:else if phase === 'commit'}
          <div class="flex items-center gap-2 w-full">
            <button
              class="font-mono text-[10px] px-3 py-1.5 border border-neon-cyan/40 text-neon-cyan bg-neon-cyan/5 hover:bg-neon-cyan/15 transition-colors duration-200 flex-1 text-center uppercase disabled:opacity-40 disabled:cursor-not-allowed"
              onclick={handleAccept}
              disabled={accepting}
            >
              {accepting ? 'Accepting...' : 'Accept \u2014 Archive Parents'}
            </button>
            <button
              class="font-mono text-[10px] px-3 py-1.5 border border-neon-red/25 text-neon-red bg-neon-red/5 hover:bg-neon-red/10 transition-colors duration-200 flex-1 text-center uppercase"
              onclick={onclose}
            >
              Discard \u2014 Cancel Merge
            </button>
          </div>
        {/if}
      </div>
    {/if}
  </div>
</div>
