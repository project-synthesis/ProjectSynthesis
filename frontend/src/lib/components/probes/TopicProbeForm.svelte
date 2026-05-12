<script lang="ts">
  // TopicProbeForm — POST /api/probes input surface.
  //
  // Per spec §6, the form gathers the user-driven topic, prompt count,
  // intent hint, and grounding mode. Submit is gated by topic length
  // (3-500 chars) AND the grounding mode being reachable: 'codebase'
  // requires a linked GitHub repo; 'topic_only' is always reachable.
  //
  // The component is presentation-only — it dispatches a typed payload
  // through the `onSubmit` callback prop OR (if absent) a no-op. The
  // parent (SeedModal's third tab or a standalone route) owns the
  // dispatch via api/probes.
  //
  // Brand canon: h-5 buttons, p-1.5 sidebars, font-mono numerics, font-
  // display section headings. Submit button uses `seed-btn-primary`-
  // style 1px contour with no glow.

  import { flushSync } from 'svelte';
  import { githubStore } from '$lib/stores/github.svelte';

  /** Canonical intent vocabulary. The dropdown surfaces 4 options; the
   *  contract requires at minimum explore/audit/refactor + one extra. */
  type Intent = 'explore' | 'audit' | 'refactor' | 'extend';
  type GroundingMode = 'codebase' | 'topic_only';

  export interface ProbeFormPayload {
    topic: string;
    n_prompts: number;
    intent: Intent;
    grounding_mode: GroundingMode;
  }

  interface Props {
    onSubmit?: (payload: ProbeFormPayload) => void;
  }

  const { onSubmit }: Props = $props();

  // ── State ────────────────────────────────────────────────────────────
  let topic = $state('');
  let nPrompts = $state(10);
  let intent = $state<Intent>('explore');
  // Default grounding mode is always 'codebase' — when no repo is linked
  // the codebase button is disabled AND groundingValid resolves false, so
  // the submit button stays disabled until the user explicitly clicks
  // 'topic_only'. This matches the test contract (spec §6): "Without repo
  // + 'codebase' default → submit disabled".
  let groundingMode = $state<GroundingMode>('codebase');

  // ── Derived gates ────────────────────────────────────────────────────
  const repoLinked = $derived(!!githubStore.linkedRepo);
  // Topic length contract: 3–500 chars after trim. The `<= 500` ceiling is
  // applied to the raw length (NOT trimmed) so a 500-character body
  // padded with spaces still counts as over-budget if it exceeds 500.
  const topicValid = $derived(topic.trim().length >= 3 && topic.length <= 500);
  // Codebase grounding requires a linked repo; topic_only is always valid.
  const groundingValid = $derived(
    groundingMode === 'topic_only' || (groundingMode === 'codebase' && repoLinked),
  );
  const canSubmit = $derived(topicValid && groundingValid);

  // ── Handlers ─────────────────────────────────────────────────────────
  function clampN(value: number): number {
    if (!Number.isFinite(value)) return 10;
    return Math.max(5, Math.min(25, Math.round(value)));
  }

  function handleNInput(e: Event) {
    const raw = Number((e.currentTarget as HTMLInputElement).value);
    nPrompts = clampN(raw);
    // Force the input element to reflect the clamped value when the
    // browser allowed out-of-range typing.
    (e.currentTarget as HTMLInputElement).value = String(nPrompts);
  }

  function handleSubmit(e: Event) {
    e.preventDefault();
    if (!canSubmit) return;
    onSubmit?.({
      topic: topic.trim(),
      n_prompts: nPrompts,
      intent,
      grounding_mode: groundingMode,
    });
  }
</script>

<form class="probe-form" onsubmit={handleSubmit}>
  <!-- Topic textarea ──────────────────────────────────────────────── -->
  <div class="probe-field">
    <label class="probe-label" for="probe-topic">TOPIC</label>
    <textarea
      id="probe-topic"
      class="probe-textarea"
      placeholder="e.g. embedding cache invalidation in EmbeddingIndex"
      value={topic}
      oninput={(e) => {
        // Use flushSync so the resulting derived state (`canSubmit`) and
        // the DOM `disabled` attribute of the submit button are updated
        // synchronously. Tests that fire `dispatchEvent(new Event('input'))`
        // directly assert against the submit-disabled state in the same
        // tick — without flushSync the Svelte microtask hasn't run yet.
        flushSync(() => {
          topic = (e.currentTarget as HTMLTextAreaElement).value;
        });
      }}
    ></textarea>
    <div class="probe-hint">
      <span class="probe-hint-count">{topic.length}/500</span>
      {#if topic.length > 0 && topic.trim().length < 3}
        <span class="probe-hint-error">min 3 characters</span>
      {/if}
    </div>
  </div>

  <!-- N prompts slider ───────────────────────────────────────────── -->
  <div class="probe-field">
    <label class="probe-label" for="probe-n">
      PROMPTS — <span class="probe-num">{nPrompts}</span>
    </label>
    <input
      id="probe-n"
      type="range"
      class="probe-slider"
      min="5"
      max="25"
      step="1"
      value={nPrompts}
      oninput={handleNInput}
    />
    <div class="probe-slider-marks">
      <span>5</span>
      <span>15</span>
      <span>25</span>
    </div>
  </div>

  <!-- Intent dropdown ───────────────────────────────────────────── -->
  <div class="probe-field">
    <label class="probe-label" for="probe-intent">INTENT</label>
    <select
      id="probe-intent"
      class="probe-select"
      bind:value={intent}
    >
      <option value="explore">Explore</option>
      <option value="audit">Audit</option>
      <option value="refactor">Refactor</option>
      <option value="extend">Extend</option>
    </select>
  </div>

  <!-- Grounding mode segmented control ───────────────────────────── -->
  <div class="probe-field" role="radiogroup" aria-label="Grounding mode">
    <span class="probe-label">GROUNDING</span>
    <div class="probe-segments">
      <button
        type="button"
        role="radio"
        class="probe-segment"
        class:probe-segment--active={groundingMode === 'codebase'}
        aria-checked={groundingMode === 'codebase'}
        disabled={!repoLinked}
        onclick={() => { if (repoLinked) groundingMode = 'codebase'; }}
      >
        Codebase
      </button>
      <button
        type="button"
        role="radio"
        class="probe-segment"
        class:probe-segment--active={groundingMode === 'topic_only'}
        aria-checked={groundingMode === 'topic_only'}
        onclick={() => { groundingMode = 'topic_only'; }}
      >
        Topic only
      </button>
    </div>
    {#if !repoLinked}
      <span class="probe-hint-error">
        Link a GitHub repo to enable codebase grounding.
      </span>
    {/if}
  </div>

  <!-- Submit ─────────────────────────────────────────────────────── -->
  <div class="probe-actions">
    <button
      type="submit"
      class="probe-submit"
      disabled={!canSubmit}
      aria-disabled={!canSubmit}
    >
      Run probe
    </button>
  </div>
</form>

<style>
  .probe-form {
    display: flex;
    flex-direction: column;
    gap: 14px;
    font-family: var(--font-mono);
  }

  .probe-field {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .probe-label {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--color-text-dim);
    text-transform: uppercase;
  }

  .probe-textarea {
    width: 100%;
    min-height: 64px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 8px;
    resize: vertical;
    box-sizing: border-box;
  }

  .probe-textarea:focus {
    outline: none;
    border-color: var(--color-neon-cyan);
  }

  .probe-hint {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 9px;
    color: var(--color-text-dim);
  }

  .probe-hint-count {
    font-family: var(--font-mono);
  }

  .probe-hint-error {
    color: var(--color-neon-yellow);
  }

  .probe-num {
    color: var(--color-neon-cyan);
    font-weight: 600;
    font-family: var(--font-mono);
  }

  .probe-slider {
    width: 100%;
    accent-color: var(--color-neon-cyan);
    cursor: pointer;
  }

  .probe-slider-marks {
    display: flex;
    justify-content: space-between;
    font-size: 9px;
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    margin-top: 2px;
  }

  /* Form input fields — h-5 (20px) per canon "Select/input fields | 20px"
     (SKILL.md line 139, spec §6 line 1102). text-[11px], line-height 18px
     to match the canonical button row. */
  .probe-select {
    width: 100%;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 0 4px;
    height: 20px;
    line-height: 18px;
    box-sizing: border-box;
    cursor: pointer;
    /* Atomic multi-property transition per axiom 5 + spec §6 line 1133-1140. */
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .probe-select:focus {
    outline: none;
    border-color: var(--color-neon-cyan);
  }

  .probe-segments {
    display: flex;
    gap: 0;
  }

  /* Segmented control — h-5 (20px) per spec §6 line 1104.
     text-[10px] line-height 18px matches canonical button row. */
  .probe-segment {
    flex: 1;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    font-size: 10px;
    padding: 0 8px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    height: 20px;
    line-height: 18px;
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .probe-segment + .probe-segment {
    border-left: none;
  }

  .probe-segment:hover:not(:disabled) {
    background: color-mix(in srgb, var(--color-text-primary) 3%, transparent);
    color: var(--color-text-primary);
  }

  .probe-segment--active {
    color: var(--color-neon-cyan);
    border-color: var(--color-neon-cyan);
  }

  .probe-segment:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .probe-actions {
    display: flex;
    justify-content: flex-end;
  }

  /* Run probe — Hero tier (cyan) per spec §6 line 1129.
     Resting `1px solid neon-cyan + 8% fill`; Hover `14% fill + translateY(-1px)`;
     Active `translateY(0)`. h-5 (20px) per spec §6 line 1104. */
  .probe-submit {
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
    border: 1px solid var(--color-neon-cyan);
    color: var(--color-neon-cyan);
    font-family: var(--font-mono);
    font-size: 10px;
    padding: 0 8px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    height: 20px;
    line-height: 18px;
    box-sizing: border-box;
    /* Atomic multi-property hover transition — spec §6 line 1133-1140. */
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                transform 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .probe-submit:hover:not(:disabled) {
    background: color-mix(in srgb, var(--color-neon-cyan) 14%, transparent);
    transform: translateY(-1px);
  }

  .probe-submit:active:not(:disabled) {
    transform: translateY(0);
    transition: background 150ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                transform 150ms cubic-bezier(0.16, 1, 0.3, 1),
                color 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .probe-submit:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }
</style>
