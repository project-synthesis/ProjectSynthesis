<script lang="ts">
  /**
   * Passthrough Protocol guide modal.
   *
   * Interactive stepper explaining the manual passthrough workflow with a
   * feature comparison matrix. Triggered from Navigator toggle and
   * PassthroughView help button.
   *
   * Copyright 2025-2026 Project Synthesis contributors.
   */
  import { passthroughGuide, STEP_COUNT } from '$lib/stores/passthrough-guide.svelte';

  // ---------------------------------------------------------------------------
  // Data-driven content
  // ---------------------------------------------------------------------------

  interface GuideStep {
    number: number;
    title: string;
    description: string;
    detail: string;
    accent: 'yellow' | 'cyan' | 'green';
  }

  const STEPS: GuideStep[] = [
    {
      number: 1,
      title: 'System assembles your prompt',
      description:
        'Strategy template, scoring rubric, workspace context, codebase context, applied patterns, and adaptation state are assembled into a single optimized instruction.',
      detail: 'All context enrichment happens server-side. The assembled prompt appears in the editor.',
      accent: 'yellow',
    },
    {
      number: 2,
      title: 'Copy the assembled prompt',
      description: 'Click COPY or select all text from the assembled prompt panel.',
      detail: 'The full prompt is designed to work with any instruction-following LLM.',
      accent: 'cyan',
    },
    {
      number: 3,
      title: 'Paste into your LLM',
      description:
        'Open ChatGPT, Claude.ai, Gemini, or any LLM interface and submit the assembled prompt.',
      detail: 'Strategy and rubric are embedded — the LLM receives full optimization instructions.',
      accent: 'cyan',
    },
    {
      number: 4,
      title: 'Copy the LLM response',
      description: "Copy the optimized prompt text from your LLM's output.",
      detail: "Only the optimized prompt text — not the LLM's preamble or commentary.",
      accent: 'cyan',
    },
    {
      number: 5,
      title: 'Paste result back',
      description: 'Paste into the OPTIMIZED RESULT textarea and click SAVE.',
      detail: 'Optional: add a changes summary to track what the LLM modified.',
      accent: 'cyan',
    },
    {
      number: 6,
      title: 'System scores and persists',
      description:
        'Heuristic scoring evaluates 5 dimensions. Result enters the taxonomy engine and history.',
      detail: 'Hybrid blending applies when historical data exists. Scores feed strategy adaptation.',
      accent: 'green',
    },
  ];

  interface ComparisonRow {
    feature: string;
    internal: string;
    sampling: string;
    passthrough: string;
    passthroughDim?: boolean; // true for cross-mark rows
  }

  const COMPARISON: ComparisonRow[] = [
    { feature: 'Analyze phase', internal: '\u2713', sampling: '\u2713', passthrough: 'Implicit' },
    { feature: 'Optimize phase', internal: '\u2713', sampling: '\u2713', passthrough: 'Single-shot' },
    { feature: 'Score phase', internal: 'LLM', sampling: 'LLM', passthrough: 'Heuristic / Hybrid' },
    { feature: 'Codebase explore', internal: '\u2713', sampling: '\u2713', passthrough: 'Roots + index' },
    { feature: 'Pattern injection', internal: '\u2713', sampling: '\u2713', passthrough: '\u2713' },
    {
      feature: 'Suggestions',
      internal: '\u2713',
      sampling: '\u2713',
      passthrough: '\u2717',
      passthroughDim: true,
    },
    {
      feature: 'Intent drift',
      internal: '\u2713',
      sampling: '\u2713',
      passthrough: '\u2717',
      passthroughDim: true,
    },
    { feature: 'Adaptation state', internal: '\u2713', sampling: '\u2713', passthrough: 'Injected' },
    { feature: 'Strategy template', internal: '\u2713', sampling: '\u2713', passthrough: 'Injected' },
    { feature: 'Cost', internal: 'API key', sampling: 'IDE LLM', passthrough: 'Zero' },
    { feature: 'Dependencies', internal: 'Provider', sampling: 'MCP client', passthrough: 'None' },
  ];

  // Dev-mode guard: STEPS array length must match store's STEP_COUNT
  if (import.meta.env.DEV && STEPS.length !== STEP_COUNT) {
    console.error(`PassthroughGuide: STEPS.length (${STEPS.length}) !== STEP_COUNT (${STEP_COUNT})`);
  }

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  let containerEl = $state<HTMLDivElement | null>(null);
  let previousFocus: HTMLElement | null = null; // not reactive — never read in template
  let dontShowAgain = $state(false);

  // Accent color CSS variable for each step type
  function accentVar(accent: GuideStep['accent']): string {
    switch (accent) {
      case 'yellow':
        return 'var(--color-neon-yellow)';
      case 'cyan':
        return 'var(--color-neon-cyan)';
      case 'green':
        return 'var(--color-neon-green)';
    }
  }

  // Tinted accent background for active step numbers (12% mix with transparent)
  function accentBg(accent: GuideStep['accent']): string {
    return `color-mix(in srgb, ${accentVar(accent)} 12%, transparent)`;
  }

  // ---------------------------------------------------------------------------
  // Focus management
  // ---------------------------------------------------------------------------

  $effect(() => {
    if (passthroughGuide.open) {
      previousFocus = document.activeElement as HTMLElement;
      dontShowAgain = false;
      setTimeout(() => containerEl?.focus(), 0);
    }
  });

  function close() {
    if (dontShowAgain) {
      passthroughGuide.dismiss();
    } else {
      passthroughGuide.close();
    }
    previousFocus?.focus();
  }

  // ---------------------------------------------------------------------------
  // Keyboard handling
  // ---------------------------------------------------------------------------

  function handleKeydown(e: KeyboardEvent) {
    if (!passthroughGuide.open) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
      return;
    }
    // Focus trap
    if (e.key === 'Tab' && containerEl) {
      const focusable = containerEl.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if passthroughGuide.open}
  <!-- Overlay backdrop -->
  <div
    class="overlay"
    role="dialog"
    aria-modal="true"
    aria-label="Passthrough workflow guide"
    tabindex="-1"
    onclick={(e) => {
      if (e.target === e.currentTarget) close();
    }}
    onkeydown={handleKeydown}
  >
    <!-- Guide container -->
    <div class="guide-container" bind:this={containerEl} tabindex="-1">
      <!-- Header -->
      <div class="guide-header">
        <span class="header-title">PASSTHROUGH PROTOCOL</span>
        <div class="header-spacer"></div>
        <button class="close-btn" onclick={close} aria-label="Close guide">
          &#x2715;
        </button>
      </div>

      <!-- Scrollable body -->
      <div class="guide-body">
        <!-- WHY section -->
        <div class="guide-section">
          <span class="section-label">WHY PASSTHROUGH</span>
          <p class="section-text">
            Zero-dependency fallback. No API key, no CLI, no MCP client required.
            The system assembles a rich optimization prompt — you run it through
            whatever LLM you have access to, then paste the result back. Scores,
            taxonomy, and adaptation all still work.
          </p>
        </div>

        <!-- Interactive stepper -->
        <div class="guide-section">
          <span class="section-label">WORKFLOW</span>
          <ol class="step-list">
            {#each STEPS as step, i (step.number)}
              {@const isActive = passthroughGuide.activeStep === i}
              {@const color = accentVar(step.accent)}
              <li
                class="step-item"
                class:step-item--active={isActive}
                style="animation-delay: {i * 50}ms"
              >
                <!-- Step row: always visible -->
                <div class="step-header">
                  <button
                    class="step-toggle"
                    onclick={() => passthroughGuide.setStep(i)}
                    aria-expanded={isActive}
                    aria-controls="step-content-{i}"
                  >
                    <span
                      class="step-number"
                      class:step-number--active={isActive}
                      style="
                        border-color: {isActive ? color : 'var(--color-border-subtle)'};
                        background: {isActive ? accentBg(step.accent) : 'transparent'};
                        color: {isActive ? color : 'var(--color-text-dim)'};
                      "
                    >
                      {step.number}
                    </span>
                    <span
                      class="step-title"
                      style="color: {isActive ? 'var(--color-text-primary)' : 'var(--color-text-dim)'};"
                    >
                      {step.title}
                    </span>
                  </button>
                  {#if isActive}
                    <div class="step-nav">
                      {#if i > 0}
                        <button
                          class="step-nav-btn"
                          onclick={() => passthroughGuide.prevStep()}
                          aria-label="Previous step"
                        >PREV</button>
                      {/if}
                      <button
                        class="step-nav-btn step-nav-btn--primary"
                        onclick={() => {
                          if (i === STEPS.length - 1) {
                            close();
                          } else {
                            passthroughGuide.nextStep();
                          }
                        }}
                        aria-label={i === STEPS.length - 1 ? 'Complete guide' : 'Next step'}
                      >{i === STEPS.length - 1 ? 'GOT IT' : 'NEXT'}</button>
                    </div>
                  {/if}
                </div>

                <!-- Expanded content -->
                {#if isActive}
                  <div class="step-content" id="step-content-{i}">
                    <p class="step-desc">{step.description}</p>
                    <div class="step-note" style="border-color: {color};">
                      {step.detail}
                    </div>
                  </div>
                {/if}

                <!-- Connector line (except last step) -->
                {#if i < STEPS.length - 1}
                  <div
                    class="step-connector"
                    style="background: {i < passthroughGuide.activeStep
                      ? accentVar(STEPS[i].accent)
                      : 'var(--color-border-subtle)'};"
                  ></div>
                {/if}
              </li>
            {/each}
          </ol>
        </div>

        <!-- Feature matrix -->
        <div class="guide-section">
          <span class="section-label">FEATURE MATRIX</span>
          <div class="table-wrap">
            <table class="comparison-table">
              <thead>
                <tr>
                  <th class="th-feature">Feature</th>
                  <th class="th-tier">Internal</th>
                  <th class="th-tier">Sampling</th>
                  <th class="th-tier th-passthrough">Passthrough</th>
                </tr>
              </thead>
              <tbody>
                {#each COMPARISON as row (row.feature)}
                  <tr>
                    <td class="td-feature">{row.feature}</td>
                    <td class="td-tier" class:td-tier--check={row.internal === '\u2713'}>{row.internal}</td>
                    <td class="td-tier" class:td-tier--check={row.sampling === '\u2713'}>{row.sampling}</td>
                    <td
                      class="td-passthrough"
                      class:td-passthrough--dim={row.passthroughDim}
                    >{row.passthrough}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="guide-footer">
        <label class="dismiss-label">
          <input
            type="checkbox"
            class="dismiss-checkbox"
            bind:checked={dontShowAgain}
          />
          <span class="dismiss-text">Don't show on toggle</span>
        </label>
        <div class="footer-spacer"></div>
        <button class="got-it-btn" onclick={close}>GOT IT</button>
      </div>
    </div>
  </div>
{/if}

<style>
  /* ------------------------------------------------------------------ */
  /* Overlay & container                                                 */
  /* ------------------------------------------------------------------ */

  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(6, 6, 12, 0.8);
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding-top: 60px;
    z-index: 9999;
  }

  .guide-container {
    width: 520px;
    max-height: min(80vh, 600px);
    display: flex;
    flex-direction: column;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    overflow: hidden;
    outline: none;
    animation: guide-in 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
  }

  @keyframes guide-in {
    from {
      opacity: 0;
      transform: scale(0.95) translateY(8px);
    }
    to {
      opacity: 1;
      transform: scale(1) translateY(0);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .guide-container {
      animation-duration: 0.01ms;
    }
    .step-item {
      animation-duration: 0.01ms !important;
    }
    .step-content {
      animation-duration: 0.01ms !important;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Header                                                              */
  /* ------------------------------------------------------------------ */

  .guide-header {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 28px;
    padding: 0 6px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-neon-yellow);
    flex-shrink: 0;
  }

  .header-title {
    font-size: 11px;
    font-family: var(--font-display);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-neon-yellow);
    user-select: none;
  }

  .header-spacer {
    flex: 1;
  }

  .close-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-size: 10px;
    cursor: pointer;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
    padding: 0;
    line-height: 1;
  }

  .close-btn:hover {
    color: var(--color-text-primary);
    border-color: var(--color-text-dim);
  }

  /* ------------------------------------------------------------------ */
  /* Body (scrollable)                                                   */
  /* ------------------------------------------------------------------ */

  .guide-body {
    flex: 1;
    overflow-y: auto;
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .guide-section {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .section-label {
    font-size: 10px;
    font-family: var(--font-display);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
    user-select: none;
  }

  .section-text {
    font-size: 11px;
    font-family: var(--font-sans);
    color: var(--color-text-secondary);
    line-height: 1.5;
    margin: 0;
    padding: 4px 6px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-subtle);
  }

  /* ------------------------------------------------------------------ */
  /* Interactive stepper                                                  */
  /* ------------------------------------------------------------------ */

  .step-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
  }

  .step-item {
    position: relative;
    display: flex;
    flex-direction: column;
    opacity: 0;
    animation: step-stagger 350ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
  }

  @keyframes step-stagger {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .step-header {
    display: flex;
    align-items: center;
    gap: 4px;
    width: 100%;
    padding: 0;
  }

  .step-toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1;
    min-width: 0;
    padding: 3px 4px;
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .step-toggle:hover {
    background: var(--color-bg-hover);
  }

  .step-number {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    flex-shrink: 0;
    font-size: 10px;
    font-family: var(--font-mono);
    font-weight: 700;
    border: 1px solid var(--color-border-subtle);
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .step-title {
    font-size: 11px;
    font-family: var(--font-sans);
    font-weight: 500;
    flex: 1;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .step-nav {
    display: flex;
    gap: 4px;
    flex-shrink: 0;
  }

  .step-nav-btn {
    font-family: var(--font-display);
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    padding: 0 6px;
    height: 20px;
    line-height: 18px;
    cursor: pointer;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
    white-space: nowrap;
  }

  .step-nav-btn:hover {
    color: var(--color-text-primary);
    border-color: var(--color-text-dim);
  }

  .step-nav-btn--primary {
    color: var(--color-neon-yellow);
    border-color: var(--color-neon-yellow);
  }

  .step-nav-btn--primary:hover {
    background: rgba(251, 191, 36, 0.06);
    color: var(--color-neon-yellow);
    border-color: var(--color-neon-yellow);
  }

  /* Expanded step content */
  .step-content {
    padding: 2px 4px 4px 26px; /* 16px number + 6px gap + 4px padding */
    animation: step-expand 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
  }

  @keyframes step-expand {
    from {
      opacity: 0;
      max-height: 0;
    }
    to {
      opacity: 1;
      max-height: 200px;
    }
  }

  .step-desc {
    font-size: 11px;
    font-family: var(--font-sans);
    color: var(--color-text-secondary);
    line-height: 1.5;
    margin: 0 0 4px;
  }

  .step-note {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    line-height: 1.4;
    margin: 0;
    padding: 2px 0 2px 8px;
    border-left: 1px solid;
    opacity: 0.7;
  }


  /* Connector line between steps */
  .step-connector {
    width: 2px;
    height: 10px;
    margin-left: 11px; /* center under 16px number: (16-2)/2 + 4px padding */
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  /* ------------------------------------------------------------------ */
  /* Feature matrix table                                                */
  /* ------------------------------------------------------------------ */

  .table-wrap {
    overflow-x: auto;
    border: 1px solid var(--color-border-subtle);
    background: var(--color-bg-secondary);
  }

  .comparison-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 10px;
    font-family: var(--font-mono);
  }

  .comparison-table thead {
    border-bottom: 1px solid var(--color-neon-yellow);
  }

  .comparison-table th {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-text-dim);
    text-align: left;
    padding: 4px 6px;
    white-space: nowrap;
  }

  .th-feature {
    width: auto;
  }

  .th-tier {
    width: 72px;
    text-align: center;
  }

  .th-passthrough {
    color: var(--color-neon-yellow);
  }

  .comparison-table td {
    padding: 3px 6px;
    border-bottom: 1px solid rgba(74, 74, 106, 0.08);
    white-space: nowrap;
  }

  .td-feature {
    font-family: var(--font-sans);
    font-size: 10px;
    color: var(--color-text-secondary);
  }

  .td-tier {
    text-align: center;
    color: var(--color-text-secondary);
  }

  .td-tier--check {
    color: var(--color-neon-green);
  }

  .td-passthrough {
    text-align: center;
    color: var(--color-neon-yellow);
  }

  .td-passthrough--dim {
    color: var(--color-text-dim);
    opacity: 0.4;
  }

  /* ------------------------------------------------------------------ */
  /* Footer                                                              */
  /* ------------------------------------------------------------------ */

  .guide-footer {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 28px;
    padding: 0 6px;
    background: var(--color-bg-secondary);
    border-top: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .dismiss-label {
    display: flex;
    align-items: center;
    gap: 4px;
    cursor: pointer;
  }

  .dismiss-checkbox {
    width: 12px;
    height: 12px;
    accent-color: var(--color-neon-yellow);
    cursor: pointer;
  }

  .dismiss-text {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
    user-select: none;
  }

  .footer-spacer {
    flex: 1;
  }

  .got-it-btn {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-neon-yellow);
    border: 1px solid var(--color-neon-yellow);
    background: transparent;
    padding: 0 8px;
    height: 20px;
    line-height: 18px;
    cursor: pointer;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
    white-space: nowrap;
  }

  .got-it-btn:hover {
    background: rgba(251, 191, 36, 0.06);
    transform: translateY(-1px);
  }

  .got-it-btn:active {
    transform: translateY(0);
  }

  /* ------------------------------------------------------------------ */
  /* Focus-visible (brand: cyan outline, offset 2px, additive)          */
  /* ------------------------------------------------------------------ */

  .guide-container :global(button:focus-visible),
  .dismiss-checkbox:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }
</style>
