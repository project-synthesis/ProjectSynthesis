<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import { getScoreColor } from '$lib/utils/colors';

  // Stage configuration
  const STAGE_COLORS: Record<string, string> = {
    explore:  '#00d4aa',
    analyze:  '#00e5ff',
    strategy: '#7b61ff',
    optimize: '#ff8c00',
    validate: '#22ff88'
  };

  const STAGE_LABELS: Record<string, string> = {
    explore:  'Explore',
    analyze:  'Analyze',
    strategy: 'Strategy',
    optimize: 'Optimize',
    validate: 'Validate'
  };

  // Expand state
  let expanded = $state<Record<string, boolean>>({});
  let rawEventsExpanded = $state(false);

  // Duration formatter
  function fmtMs(ms: number): string {
    if (!ms || ms <= 0) return '—';
    if (ms >= 60000) return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  // Token formatter
  function fmtTok(n: number): string {
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return `${n}`;
  }

  // Stage durations from stageResults (backend-measured)
  let stageDurations = $derived(
    Object.fromEntries(forge.stages.map(s => [s, forge.stageResults[s]?.duration ?? 0]))
  );

  // Total: prefer forge.totalDuration (backend sum), fallback to stage sum
  let totalDuration = $derived(
    forge.totalDuration ?? forge.stages.reduce((sum, s) => sum + (stageDurations[s] ?? 0), 0)
  );

  // Max stage duration — widest bar fills 100% of its cell
  let maxStageDuration = $derived(
    Math.max(...forge.visibleStages.map(s => stageDurations[s] ?? 0), 1)
  );

  // Total tokens: prefer store total, fallback to stage sum
  let totalTokens = $derived(
    forge.totalTokens ??
    forge.stages.reduce((sum, s) => sum + (forge.stageResults[s]?.tokenCount ?? 0), 0)
  );

  // Completion timestamp from pipeline events
  let completionTime = $derived(
    forge.pipelineEvents.length > 0
      ? new Date(
          (forge.pipelineEvents.find(e => e.type === 'forge_complete')
            ?? forge.pipelineEvents[forge.pipelineEvents.length - 1]).timestamp
        )
      : null
  );

  // Whether there's enough data to render the trace
  let hasData = $derived(
    Object.keys(forge.stageResults).length > 0 ||
    forge.visibleStages.some(s => forge.stageStatuses[s] !== 'idle')
  );

  // Whether any visible stage has timing data
  let hasTimingData = $derived(
    forge.visibleStages.some(s => (stageDurations[s] ?? 0) > 0)
  );

  function toggleExpanded(stage: string) {
    expanded = { ...expanded, [stage]: !expanded[stage] };
  }

  function getStatusIcon(status: string): string {
    switch (status) {
      case 'done':      return '✓';
      case 'error':     return '✗';
      case 'skipped':   return '⊘';
      case 'timed_out': return '⏱';
      case 'running':   return '◌';
      default:          return '·';
    }
  }

  function getStatusColor(status: string): string {
    switch (status) {
      case 'done':      return '#22ff88';
      case 'error':     return '#ff3366';
      case 'skipped':   return '#7a7a9e';
      case 'timed_out': return '#ff8c00';
      case 'running':   return '#00e5ff';
      default:          return '#7a7a9e';
    }
  }

  // One-line summary for each stage's collapsed row
  function getStageSummary(stage: string): string {
    const result = forge.stageResults[stage];
    if (!result) return '';
    const d = result.data;
    switch (stage) {
      case 'explore': {
        const parts: string[] = [];
        if (d.coverage_pct != null) parts.push(`${d.coverage_pct}%`);
        if (d.files_read_count != null) parts.push(`${d.files_read_count} files`);
        if (d.repo) parts.push(String(d.repo));
        return parts.join(' · ');
      }
      case 'analyze': {
        const parts: string[] = [];
        if (d.task_type) parts.push(String(d.task_type));
        if (d.complexity) parts.push(String(d.complexity));
        const wn = Array.isArray(d.weaknesses) ? d.weaknesses.length : 0;
        const sn = Array.isArray(d.strengths) ? d.strengths.length : 0;
        if (wn > 0 || sn > 0) parts.push(`${wn}w ${sn}s`);
        return parts.join(' · ');
      }
      case 'strategy':
        return d.primary_framework ? String(d.primary_framework) : '';
      case 'optimize': {
        const n = Array.isArray(d.changes_made) ? d.changes_made.length : 0;
        return n ? `${n} change${n !== 1 ? 's' : ''}` : '';
      }
      case 'validate': {
        const overall = d.overall_score ?? forge.overallScore;
        const score = overall != null ? `${overall}/10` : '';
        const n = Array.isArray(d.issues) ? d.issues.length : 0;
        return n > 0 ? `${score} · ${n} issue${n !== 1 ? 's' : ''}` : score;
      }
      default:
        return '';
    }
  }
</script>

{#if !hasData}
  <div class="flex items-center justify-center h-32">
    <p class="text-sm text-text-dim">Pipeline trace will appear during forging.</p>
  </div>
{:else}
  <div class="space-y-3 font-mono text-xs">

    <!-- ── Summary strip ────────────────────────────────────────── -->
    <div class="flex items-center gap-4 px-3 py-2 border border-border-subtle bg-bg-card">
      {#if completionTime}
        <span class="text-text-dim">
          {completionTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      {/if}
      {#if totalDuration}
        <span class="text-text-primary font-semibold">{fmtMs(totalDuration)}</span>
      {/if}
      {#if totalTokens}
        <span class="text-text-secondary">{fmtTok(totalTokens)} tok</span>
      {/if}
      {#if forge.overallScore != null}
        <span class="font-semibold" style="color: {getScoreColor(forge.overallScore)}">
          {forge.overallScore}/10
        </span>
      {/if}
    </div>

    <!-- ── Waterfall visualization ───────────────────────────────── -->
    {#if hasTimingData}
      <div class="px-3">
        <div class="text-[10px] text-text-dim mb-1.5 uppercase tracking-widest font-sans">
          Timeline
        </div>
        <div class="flex h-3.5 gap-px">
          {#each forge.visibleStages as stage}
            {@const dur = stageDurations[stage] ?? 0}
            {@const pct = totalDuration > 0 ? (dur / totalDuration * 100) : 0}
            {@const color = STAGE_COLORS[stage] ?? '#7a7a9e'}
            <div
              class="relative group h-full"
              style="width: max({pct}%, {dur > 0 ? '2px' : '0px'}); background: {color};"
              title="{STAGE_LABELS[stage]}: {fmtMs(dur)}"
            >
              <div
                class="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 px-2 py-0.5
                       bg-bg-card border border-border-subtle text-[10px] text-text-primary
                       whitespace-nowrap opacity-0 group-hover:opacity-100 pointer-events-none
                       z-10 transition-opacity duration-150"
              >
                {STAGE_LABELS[stage]}: {fmtMs(dur)}
              </div>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- ── Stage rows ────────────────────────────────────────────── -->
    <div class="border border-border-subtle divide-y divide-border-subtle">
      {#each forge.visibleStages as stage}
        {@const status    = forge.stageStatuses[stage]}
        {@const result    = forge.stageResults[stage]}
        {@const dur       = stageDurations[stage] ?? 0}
        {@const tok       = result?.tokenCount ?? 0}
        {@const color     = STAGE_COLORS[stage] ?? '#7a7a9e'}
        {@const statusIcon  = getStatusIcon(status)}
        {@const statusColor = getStatusColor(status)}
        {@const summary   = getStageSummary(stage)}
        {@const isExpanded = expanded[stage] ?? false}
        {@const barWidth  = maxStageDuration > 0 ? (dur / maxStageDuration * 100) : 0}
        {@const data      = result?.data ?? {}}

        <!-- Collapsed row -->
        <button
          class="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-bg-hover/40
                 transition-colors text-left"
          onclick={() => toggleExpanded(stage)}
          aria-expanded={isExpanded}
        >
          <!-- Stage dot / expand indicator -->
          <span
            class="shrink-0 w-3 text-center text-[10px] leading-none"
            style="color: {color};"
          >{isExpanded ? '▼' : '●'}</span>

          <!-- Stage name -->
          <span class="w-14 shrink-0 text-text-primary">{STAGE_LABELS[stage]}</span>

          <!-- Status icon -->
          <span
            class="shrink-0 w-4 text-center {status === 'running' ? 'status-active' : ''}"
            style="color: {statusColor};"
          >{statusIcon}</span>

          <!-- Proportional time bar -->
          <div class="relative w-20 h-1.5 shrink-0 bg-bg-primary overflow-hidden">
            {#if dur > 0}
              <div
                class="absolute inset-y-0 left-0 h-full"
                style="width: {barWidth}%; background: {color}; opacity: 0.85;"
              ></div>
            {/if}
          </div>

          <!-- Summary metadata -->
          <span class="flex-1 text-text-dim truncate text-[11px]">{summary}</span>

          <!-- Duration -->
          <span class="shrink-0 text-text-secondary w-16 text-right">
            {dur > 0 ? fmtMs(dur) : (status === 'running' ? '…' : '—')}
          </span>

          <!-- Token count -->
          <span class="shrink-0 text-text-dim w-16 text-right">
            {tok > 0 ? fmtTok(tok) + 'tok' : ''}
          </span>
        </button>

        <!-- Expanded details -->
        {#if isExpanded && result}
          <div class="px-6 py-3 bg-bg-input border-t border-border-subtle space-y-3 text-[11px]">

            {#if stage === 'explore'}
              {#if data.repo}
                <div class="flex items-center gap-3">
                  <span class="trace-label">repo</span>
                  <span class="font-mono text-[10px] text-neon-teal">
                    {data.repo}{data.branch ? ` @ ${data.branch}` : ''}
                  </span>
                </div>
              {/if}
              {#if data.coverage_pct != null || data.files_read_count != null}
                <div class="flex items-center gap-3">
                  <span class="trace-label">coverage</span>
                  <span class="font-mono text-[10px] text-text-secondary">
                    {data.coverage_pct != null ? `${data.coverage_pct}%` : ''}{data.files_read_count != null ? ` · ${data.files_read_count} files read` : ''}
                  </span>
                </div>
              {/if}
              {#if Array.isArray(data.tech_stack) && data.tech_stack.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">tech stack</span>
                  <div class="flex flex-wrap gap-1">
                    {#each data.tech_stack as lang}
                      <span class="trace-chip" style="--chip-color: #00d4aa;">{lang}</span>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if Array.isArray(data.key_files_read) && data.key_files_read.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">key files</span>
                  <div class="space-y-0.5">
                    {#each data.key_files_read as f}
                      <div class="font-mono text-[10px] text-text-secondary">{f}</div>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if Array.isArray(data.observations) && data.observations.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">observations</span>
                  <div class="space-y-2 flex-1">
                    {#each data.observations as obs}
                      <div class="trace-bullet" style="--bullet-color: {color};">
                        <span class="trace-bullet-marker">▸</span>
                        <span class="text-text-secondary leading-snug">{obs}</span>
                      </div>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if Array.isArray(data.grounding_notes) && data.grounding_notes.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">grounding</span>
                  <div class="space-y-2 flex-1">
                    {#each data.grounding_notes as note}
                      <div class="trace-bullet" style="--bullet-color: {color};">
                        <span class="trace-bullet-marker">▸</span>
                        <span class="text-text-secondary leading-snug">{note}</span>
                      </div>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if Array.isArray(data.relevant_snippets) && data.relevant_snippets.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">snippets</span>
                  <div class="space-y-2 flex-1">
                    {#each data.relevant_snippets as snip}
                      {@const s = snip as { file?: string; lines?: string; context?: string }}
                      <div class="border border-border-subtle/40 bg-bg-primary/50">
                        {#if s.file}
                          <div class="flex items-center gap-2 px-2 py-1 border-b border-border-subtle/30">
                            <span class="font-mono text-[9px] text-neon-teal/70 truncate">{s.file}</span>
                            {#if s.lines}
                              <span class="font-mono text-[9px] text-text-dim/50 shrink-0">:{s.lines}</span>
                            {/if}
                          </div>
                        {/if}
                        {#if s.context}
                          <pre class="px-2 py-1.5 text-[10px] text-text-secondary overflow-x-auto
                                      font-mono leading-relaxed whitespace-pre-wrap break-words">{s.context}</pre>
                        {/if}
                      </div>
                    {/each}
                  </div>
                </div>
              {/if}

            {:else if stage === 'analyze'}
              {#if Array.isArray(data.weaknesses) && data.weaknesses.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">weaknesses</span>
                  <div class="space-y-2 flex-1">
                    {#each data.weaknesses as w}
                      <div class="trace-bullet" style="--bullet-color: {color};">
                        <span class="trace-bullet-marker">▸</span>
                        <span class="text-text-secondary leading-snug">{w}</span>
                      </div>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if Array.isArray(data.strengths) && data.strengths.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">strengths</span>
                  <div class="space-y-2 flex-1">
                    {#each data.strengths as s}
                      <div class="trace-bullet" style="--bullet-color: {color};">
                        <span class="trace-bullet-marker">▸</span>
                        <span class="text-text-secondary leading-snug">{s}</span>
                      </div>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if Array.isArray(data.recommended_frameworks) && data.recommended_frameworks.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">frameworks</span>
                  <div class="flex flex-wrap gap-1">
                    {#each data.recommended_frameworks as fw}
                      <span class="trace-chip" style="--chip-color: #00e5ff;">{fw}</span>
                    {/each}
                  </div>
                </div>
              {/if}

            {:else if stage === 'strategy'}
              {#if data.rationale}
                <div class="flex items-start gap-3">
                  <span class="trace-label">rationale</span>
                  <div class="trace-prose flex-1" style="--prose-accent: {color};">
                    {data.rationale}
                  </div>
                </div>
              {/if}
              {#if Array.isArray(data.secondary_frameworks) && data.secondary_frameworks.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">secondary</span>
                  <div class="flex flex-wrap gap-1">
                    {#each data.secondary_frameworks as fw}
                      <span class="trace-chip" style="--chip-color: #7b61ff;">{fw}</span>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if data.approach_notes}
                <div class="flex items-start gap-3">
                  <span class="trace-label">approach</span>
                  <div class="trace-prose flex-1" style="--prose-accent: {color};">
                    {data.approach_notes}
                  </div>
                </div>
              {/if}

            {:else if stage === 'optimize'}
              {#if Array.isArray(data.changes_made) && data.changes_made.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">changes</span>
                  <div class="space-y-2 flex-1">
                    {#each data.changes_made as change}
                      <div class="trace-bullet" style="--bullet-color: {color};">
                        <span class="trace-bullet-marker">▸</span>
                        <span class="text-text-secondary leading-snug">{change}</span>
                      </div>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if data.optimization_notes}
                <div class="flex items-start gap-3">
                  <span class="trace-label">notes</span>
                  <div class="trace-prose flex-1" style="--prose-accent: {color};">
                    {data.optimization_notes}
                  </div>
                </div>
              {/if}

            {:else if stage === 'validate'}
              {#if data.scores && typeof data.scores === 'object'}
                {@const scores = data.scores as Record<string, number>}
                <div class="space-y-1.5">
                  {#each Object.entries(scores).filter(([k]) => k !== 'overall_score') as [key, val]}
                    {@const scoreVal = typeof val === 'number' ? val : 0}
                    <div class="flex items-center gap-2">
                      <span class="trace-label capitalize" style="width: 6rem;">
                        {key.replace(/_score$/, '').replace(/_/g, ' ')}
                      </span>
                      <div class="flex-1 h-1.5 bg-bg-primary overflow-hidden">
                        <div
                          class="h-full"
                          style="width: {scoreVal / 10 * 100}%; background: {getScoreColor(scoreVal)};"
                        ></div>
                      </div>
                      <span
                        class="w-5 text-right font-mono text-[10px]"
                        style="color: {getScoreColor(scoreVal)};"
                      >{scoreVal}</span>
                    </div>
                  {/each}
                </div>
              {/if}
              {#if Array.isArray(data.issues) && data.issues.length > 0}
                <div class="flex items-start gap-3">
                  <span class="trace-label">issues</span>
                  <div class="space-y-2 flex-1">
                    {#each data.issues as issue}
                      <div class="trace-bullet" style="--bullet-color: {color};">
                        <span class="trace-bullet-marker">▸</span>
                        <span class="text-text-secondary leading-snug">{issue}</span>
                      </div>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if data.verdict}
                <div class="flex items-start gap-3">
                  <span class="trace-label">verdict</span>
                  <div class="trace-prose flex-1 text-text-primary" style="--prose-accent: {color};">
                    {data.verdict}
                  </div>
                </div>
              {/if}
            {/if}

            <!-- Model string (all stages) -->
            {#if data.model}
              <div class="flex items-center gap-3 pt-1.5 border-t border-border-subtle/40">
                <span class="trace-label">model</span>
                <span class="font-mono text-[10px] text-text-dim/60">{data.model}</span>
              </div>
            {/if}
          </div>
        {/if}
      {/each}
    </div>

    <!-- ── Warning / error footer ──────────────────────────────── -->
    {#if forge.contextWarning}
      {@const w = forge.contextWarning}
      <div class="flex items-start gap-2 px-3 py-2 border border-neon-yellow/30 bg-neon-yellow/5 text-[11px]">
        <span class="text-neon-yellow shrink-0 mt-px">⚠</span>
        <span class="text-text-secondary leading-relaxed">
          Context limit reached —
          {#if w.dropped_files > 0}
            <span class="text-neon-yellow">{w.dropped_files} file{w.dropped_files !== 1 ? 's' : ''}</span> dropped
          {/if}
          {#if w.dropped_urls > 0}
            {w.dropped_files > 0 ? ', ' : ''}
            <span class="text-neon-yellow">{w.dropped_urls} URL{w.dropped_urls !== 1 ? 's' : ''}</span> dropped
          {/if}
          {#if w.dropped_instructions > 0}
            {w.dropped_files > 0 || w.dropped_urls > 0 ? ', ' : ''}
            <span class="text-neon-yellow">{w.dropped_instructions} instruction{w.dropped_instructions !== 1 ? 's' : ''}</span> dropped
          {/if}
        </span>
      </div>
    {/if}

    {#if forge.error}
      <div class="flex items-start gap-2 px-3 py-2 border border-neon-red/30 bg-neon-red/5 text-[11px]">
        <span class="text-neon-red shrink-0 mt-px">✗</span>
        <span class="text-text-secondary">{forge.error}</span>
      </div>
    {/if}

    <!-- ── Raw events (collapsible) ───────────────────────────── -->
    <div class="border border-border-subtle">
      {#if forge.pipelineEvents.length > 0}
        <button
          class="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-bg-hover/30
                 transition-colors text-left"
          onclick={() => { rawEventsExpanded = !rawEventsExpanded; }}
        >
          <span
            class="text-text-dim/50 text-[9px] transition-transform duration-150
                   {rawEventsExpanded ? 'rotate-90' : ''}"
          >▶</span>
          <span class="text-text-dim">Raw events ({forge.pipelineEvents.length})</span>
        </button>
        {#if rawEventsExpanded}
          <div class="divide-y divide-border-subtle/40 border-t border-border-subtle">
            {#each forge.pipelineEvents as ev, i}
              <div class="flex items-center gap-2 px-3 py-0.5 hover:bg-bg-hover/20">
                <span class="text-text-dim/40 w-5 text-right shrink-0">{i + 1}</span>
                <span class="text-text-dim/60 w-20 shrink-0">
                  {new Date(ev.timestamp).toLocaleTimeString()}
                </span>
                <span class="text-text-secondary">{ev.type}</span>
                {#if ev.stage}
                  <span
                    class="capitalize"
                    style="color: {STAGE_COLORS[ev.stage] ?? '#7a7a9e'};"
                  >{ev.stage}</span>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      {:else}
        <!-- History load: stageResults populated but no live events -->
        <div class="flex items-center gap-2 px-3 py-1.5">
          <span class="text-text-dim/30 text-[9px]">▶</span>
          <span class="text-text-dim/40">Raw events — not available for history loads</span>
        </div>
      {/if}
    </div>

  </div>
{/if}

<style>
  /* Opacity blink for 'running' status icon — opacity-in/out, no radiance */
  @keyframes status-blink {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.35; }
  }
  .status-active {
    animation: status-blink 1.4s ease-in-out infinite;
  }

  /* Shared label style — all expanded field labels */
  .trace-label {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    flex-shrink: 0;
    width: 5rem; /* 80px — fits longest label "weaknesses" */
    line-height: 1.6;
  }

  /* Chip/badge — rectangular, stage-colored border + text */
  .trace-chip {
    display: inline-flex;
    align-items: center;
    padding: 1px 6px;
    font-family: var(--font-mono);
    font-size: 10px;
    border: 1px solid color-mix(in srgb, var(--chip-color) 30%, transparent);
    color: var(--chip-color);
    line-height: 1.6;
  }

  /* Bullet list row — marker stays at top, text wraps with indent */
  .trace-bullet {
    display: flex;
    align-items: flex-start;
    gap: 6px;
  }

  .trace-bullet-marker {
    flex-shrink: 0;
    width: 10px;
    text-align: center;
    margin-top: 2px;
    font-size: 8px;
    color: color-mix(in srgb, var(--bullet-color) 50%, transparent);
    user-select: none;
    line-height: 1.4;
  }

  /* Prose block — left accent border, relaxed leading */
  .trace-prose {
    font-family: var(--font-sans);
    font-size: 11px;
    color: var(--color-text-secondary);
    line-height: 1.65;
    border-left: 1px solid color-mix(in srgb, var(--prose-accent) 25%, transparent);
    padding-left: 10px;
  }
</style>
