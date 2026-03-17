# Event-Driven Reactivity Plan

> **For agentic workers:** Execute with subagent-driven development.

**Goal:** Wire all 6 SSE event types to actionable UI responses — toasts, status bar metrics, Inspector sync, refinement refresh, and auto-open.

**Architecture:** Frontend already receives all events via SSE → window CustomEvent. Each task adds a listener to the appropriate component. Toast store is reusable. No backend changes needed.

---

## Task 1: Failed optimization toast + error indicator

**Files:** `frontend/src/routes/+page.svelte`

Add `optimization_failed` to the window event dispatch:
```typescript
if (type === 'optimization_failed') {
  window.dispatchEvent(new CustomEvent('optimization-event', { detail: data }));
  // Import addToast dynamically to avoid circular dependency
  import('$lib/stores/toast.svelte').then(({ addToast }) => {
    addToast('deleted', data.error || 'Optimization failed');
  });
}
```

This reuses the red `-` toast for failure notifications.

---

## Task 2: Inspector feedback badge sync

**Files:** `frontend/src/lib/components/layout/Inspector.svelte`

Listen for `feedback-event` and update the displayed feedback state:
```typescript
import { forgeStore } from '$lib/stores/forge.svelte';

$effect(() => {
  const handler = (e: Event) => {
    const detail = (e as CustomEvent).detail;
    if (detail?.optimization_id === forgeStore.result?.id) {
      forgeStore.feedback = detail.rating;
    }
  };
  window.addEventListener('feedback-event', handler);
  return () => window.removeEventListener('feedback-event', handler);
});
```

When feedback is submitted from another source (MCP, another tab), the Inspector's thumbs up/down state updates.

---

## Task 3: Refinement timeline auto-refresh

**Files:** `frontend/src/lib/components/refinement/RefinementTimeline.svelte`

Listen for `optimization-event` where the detail contains a `refinement_turn` indicator:
```typescript
$effect(() => {
  const handler = (e: Event) => {
    const detail = (e as CustomEvent).detail;
    if (detail?.optimization_id === refinementStore.optimizationId) {
      refinementStore.refreshTurns();
    }
  };
  window.addEventListener('optimization-event', handler);
  return () => window.removeEventListener('optimization-event', handler);
});
```

This requires a `refreshTurns()` method on the refinement store. Check if one exists or add it.

---

## Task 4: New optimization toast with context

**Files:** `frontend/src/routes/+page.svelte`

Enhance the `optimization_created` / `optimization_analyzed` handler to show a toast when the optimization wasn't initiated from the current UI session:
```typescript
if (type === 'optimization_created' || type === 'optimization_analyzed') {
  window.dispatchEvent(new CustomEvent('optimization-event', { detail: data }));
  // Show toast if this wasn't from the current session
  if (data.trace_id && data.trace_id !== forgeStore.traceId) {
    const label = type === 'optimization_analyzed' ? 'analyzed' : 'optimized';
    import('$lib/stores/toast.svelte').then(({ addToast }) => {
      addToast('created', `Prompt ${label}`);
    });
  }
}
```

---

## Task 5: Status bar live metrics

**Files:** `frontend/src/lib/components/layout/StatusBar.svelte`

Add real-time pipeline status display by listening to `optimization-event`:
- Show last optimization score + strategy
- Show "Analyzing..." / "Optimizing..." / "Scoring..." during active pipeline (read from forgeStore.status)

The status bar should display: `[provider] | v0.1.0-dev | last: 8.2 chain-of-thought | Ctrl+K`

Listen to forgeStore.status for active phase display, and forgeStore.result for last score.

---

## Verification

1. Trigger optimization from MCP → toast appears in UI
2. Submit feedback → Inspector badge updates
3. File watcher creates strategy → toast appears
4. Failed optimization → red toast
5. Status bar shows last score and active phase
