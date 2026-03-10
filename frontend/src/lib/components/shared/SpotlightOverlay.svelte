<script lang="ts">
  import { onMount } from 'svelte';
  import { walkthrough } from '$lib/stores/walkthrough.svelte';
  import { user } from '$lib/stores/user.svelte';
  import { trackOnboardingEvent } from '$lib/api/client';

  let rect = $state<DOMRect | null>(null);
  let tooltipPos = $state<{ top: number; left: number }>({ top: 0, left: 0 });

  function updatePosition() {
    const step = walkthrough.step;
    if (!step) return;

    const el = document.querySelector(step.target) as HTMLElement | null;
    if (!el) {
      // Element not found — try next step or show centered tooltip
      rect = null;
      tooltipPos = { top: window.innerHeight / 2 - 80, left: window.innerWidth / 2 - 160 };
      return;
    }

    const r = el.getBoundingClientRect();
    rect = r;

    // Position tooltip relative to the target element
    const pad = 12;
    const tooltipW = 320;
    const tooltipH = 120;

    switch (step.position) {
      case 'right':
        tooltipPos = { top: r.top, left: r.right + pad };
        break;
      case 'left':
        tooltipPos = { top: r.top, left: r.left - tooltipW - pad };
        break;
      case 'bottom':
        tooltipPos = { top: r.bottom + pad, left: Math.max(pad, r.left) };
        break;
      case 'top':
        tooltipPos = { top: r.top - tooltipH - pad, left: Math.max(pad, r.left) };
        break;
    }

    // Clamp to viewport
    tooltipPos = {
      top: Math.max(8, Math.min(window.innerHeight - tooltipH - 8, tooltipPos.top)),
      left: Math.max(8, Math.min(window.innerWidth - tooltipW - 8, tooltipPos.left)),
    };
  }

  $effect(() => {
    // Re-run when step changes
    if (walkthrough.active && walkthrough.step) {
      // Execute step action if any
      walkthrough.step.action?.();
      // Delay slightly to let DOM updates settle
      requestAnimationFrame(() => updatePosition());
    }
  });

  function handleNext() {
    trackOnboardingEvent(`walkthrough_step_${walkthrough.currentStep + 1}`).catch(() => {});
    walkthrough.next();
    if (!walkthrough.active) {
      user.setWalkthroughCompleted();
      trackOnboardingEvent('walkthrough_completed').catch(() => {});
    }
  }

  function handleBack() {
    walkthrough.back();
  }

  function handleExit() {
    trackOnboardingEvent('walkthrough_exited', { step: walkthrough.currentStep }).catch(() => {});
    walkthrough.exit();
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') handleExit();
    else if (e.key === 'ArrowRight' || e.key === 'Enter') handleNext();
    else if (e.key === 'ArrowLeft') handleBack();
  }

  onMount(() => {
    updatePosition();
    const onResize = () => updatePosition();
    window.addEventListener('resize', onResize);
    document.addEventListener('keydown', handleKeydown);
    trackOnboardingEvent('walkthrough_started').catch(() => {});
    return () => {
      window.removeEventListener('resize', onResize);
      document.removeEventListener('keydown', handleKeydown);
    };
  });
</script>

{#if walkthrough.active && walkthrough.step}
  <!-- Full-screen overlay -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    class="fixed inset-0 z-[450]"
    onclick={handleExit}
  >
    <!-- Dimmed background with cutout -->
    <svg class="absolute inset-0 w-full h-full" style="pointer-events: none;">
      <defs>
        <mask id="spotlight-mask">
          <rect x="0" y="0" width="100%" height="100%" fill="white" />
          {#if rect}
            <rect
              x={rect.left - 4}
              y={rect.top - 4}
              width={rect.width + 8}
              height={rect.height + 8}
              fill="black"
            />
          {/if}
        </mask>
      </defs>
      <rect
        x="0" y="0" width="100%" height="100%"
        fill="rgba(10, 10, 18, 0.75)"
        mask="url(#spotlight-mask)"
        style="pointer-events: all;"
      />
    </svg>

    <!-- Highlight border around target -->
    {#if rect}
      <div
        class="absolute border border-neon-cyan/60 pointer-events-none"
        style="
          top: {rect.top - 4}px;
          left: {rect.left - 4}px;
          width: {rect.width + 8}px;
          height: {rect.height + 8}px;
        "
      ></div>
    {/if}

    <!-- Tooltip panel -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div
      class="absolute z-[460] w-80 bg-bg-card border border-border-subtle p-4 animate-fade-in"
      style="top: {tooltipPos.top}px; left: {tooltipPos.left}px;"
      onclick={(e) => e.stopPropagation()}
    >
      <!-- Step counter -->
      <div class="flex items-center justify-between mb-2">
        <span class="font-mono text-[8px] text-neon-cyan/60 uppercase tracking-[0.1em]">
          STEP {walkthrough.progress}
        </span>
        <button
          onclick={handleExit}
          class="font-mono text-[8px] text-text-dim/40 hover:text-text-dim uppercase tracking-[0.05em]"
        >EXIT TOUR</button>
      </div>

      <!-- Content -->
      <h3 class="font-display text-[11px] font-bold uppercase text-text-primary mb-1">
        {walkthrough.step.title}
      </h3>
      <p class="font-mono text-[9px] text-text-dim leading-relaxed mb-3">
        {walkthrough.step.description}
      </p>

      <!-- Navigation buttons -->
      <div class="flex items-center gap-2">
        {#if walkthrough.currentStep > 0}
          <button
            onclick={handleBack}
            class="px-3 py-1 border border-border-subtle font-mono text-[9px] text-text-dim uppercase tracking-[0.05em] hover:border-neon-cyan/30 hover:text-text-secondary transition-colors"
          >BACK</button>
        {/if}
        <button
          onclick={handleNext}
          class="px-3 py-1 bg-neon-cyan text-bg-primary border border-neon-cyan font-mono text-[9px] uppercase tracking-[0.07em] hover:bg-[#00cce6] transition-colors ml-auto"
        >{walkthrough.isLastStep ? 'FINISH' : 'NEXT'}</button>
      </div>
    </div>
  </div>
{/if}
