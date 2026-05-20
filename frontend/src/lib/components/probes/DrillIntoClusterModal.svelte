<script lang="ts">
  import { onMount } from 'svelte';

  interface ClusterRef {
    id: string;
    label: string;
    domain: string;
    task_type: string;
  }

  interface Props {
    cluster: ClusterRef;
    onClose: () => void;
    onDrilled: (runId: string) => void;
  }

  let { cluster, onClose, onDrilled }: Props = $props();
  // Intentional: topic pre-fills from cluster.label on mount, then is freely editable.
  // svelte-ignore state_referenced_locally
  let topic = $state(cluster.label);
  let submitting = $state(false);
  let error = $state<string | null>(null);
  let titleEl: HTMLElement;
  let previouslyFocused: HTMLElement | null = null;

  onMount(() => {
    // Remember the element that had focus (the DrillButton trigger) so we
    // can return focus to it when the modal closes.
    previouslyFocused = document.activeElement as HTMLElement | null;
    titleEl?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      if (e.key !== 'Tab') return;
      // Focus trap: cycle focus within the modal panel.
      const panel = titleEl?.closest('[role="dialog"]') as HTMLElement | null;
      if (!panel) return;
      const focusables = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => !el.hasAttribute('disabled'));
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey);

    return () => {
      document.removeEventListener('keydown', onKey);
      // Return focus to the originating trigger on close.
      previouslyFocused?.focus();
    };
  });

  async function launch() {
    if (submitting) return;
    if (topic.trim().length < 3) return;
    submitting = true;
    error = null;
    try {
      const resp = await fetch(`/api/clusters/${cluster.id}/drill`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({ detail: 'unknown_error' }));
        error = body.detail ?? 'drill_failed';
        submitting = false;
        return;
      }
      const data = await resp.json();
      onDrilled(data.run_id);
    } catch (_e) {
      error = 'network_error';
      submitting = false;
    }
  }
</script>

<!-- Backdrop: z-50 (modal layer), glass at 92% via color-mix -->
<div
  class="fixed inset-0 z-50 flex items-center justify-center"
  style="background: color-mix(in srgb, var(--color-bg-secondary) 92%, transparent);"
  onclick={onClose}
  role="presentation"
>
  <!-- Panel: rounded-none (sharp), 1px border-subtle, p-1.5 max -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    role="dialog"
    aria-modal="true"
    aria-labelledby="drill-modal-title"
    tabindex="-1"
    class="rounded-none border border-border-subtle bg-bg-card p-1.5 space-y-1.5 min-w-[320px] max-w-[480px]"
    style="animation: dialog-in 300ms cubic-bezier(0.16, 1, 0.3, 1);"
    onclick={(e) => e.stopPropagation()}
  >
    <h2
      id="drill-modal-title"
      bind:this={titleEl}
      tabindex="-1"
      class="text-[11px] font-bold uppercase tracking-wider text-text-primary"
    >
      Drill into cluster
    </h2>

    <div class="flex gap-1 text-[10px]">
      <span class="chip">{cluster.label}</span>
      <span class="chip">{cluster.domain}</span>
      <span class="chip">{cluster.task_type}</span>
    </div>

    <label class="block text-[10px] text-text-secondary">
      Topic
      <input
        type="text"
        class="input-field block w-full"
        bind:value={topic}
        minlength="3"
        maxlength="500"
        required
        aria-describedby="topic-help"
      />
    </label>
    <p id="topic-help" class="text-[10px] text-text-dim">
      Pre-filled from cluster label. 3-500 characters.
    </p>

    {#if error}
      <p class="text-[10px] text-neon-red">Error: {error}</p>
    {/if}

    <div class="flex justify-end gap-1.5">
      <button type="button" class="btn-outline-secondary" onclick={onClose} disabled={submitting}>
        Cancel
      </button>
      <button
        type="button"
        class="btn-primary"
        onclick={launch}
        disabled={submitting || topic.trim().length < 3}
      >
        {submitting ? 'Launching…' : 'Launch'}
      </button>
    </div>
  </div>
</div>
