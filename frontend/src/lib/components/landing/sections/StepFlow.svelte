<script lang="ts">
  import type { StepFlowSection } from '$lib/content/types';

  interface Props {
    steps: StepFlowSection['steps'];
  }

  let { steps }: Props = $props();
</script>

<div class="step-flow">
  {#each steps as step, i}
    <div class="step" data-reveal style="--i:{i}">
      <div class="step__indicator">
        <div class="step__number">{i + 1}</div>
        {#if i < steps.length - 1}
          <div class="step__line" aria-hidden="true"></div>
        {/if}
      </div>
      <div class="step__content">
        <h3 class="step__title font-display">{step.title}</h3>
        <p class="step__desc">{step.description}</p>
      </div>
    </div>
  {/each}
</div>

<style>
  .step-flow {
    display: flex;
    flex-direction: column;
  }

  .step {
    display: flex;
    gap: 12px;
    align-items: stretch;
  }

  .step__indicator {
    display: flex;
    flex-direction: column;
    align-items: center;
    flex-shrink: 0;
  }

  .step__number {
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    color: var(--color-neon-cyan);
    border: 1px solid var(--color-neon-cyan);
    flex-shrink: 0;
  }

  .step__line {
    width: 1px;
    flex: 1;
    min-height: 12px;
    background: var(--color-border-subtle);
  }

  .step__content {
    padding-bottom: 20px;
  }

  .step__title {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary);
    margin: 2px 0 4px 0;
  }

  .step__desc {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin: 0;
    line-height: 1.5;
  }
</style>
