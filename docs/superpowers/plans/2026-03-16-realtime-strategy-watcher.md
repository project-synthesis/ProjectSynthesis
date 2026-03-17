# Real-Time Strategy File Watcher Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** File changes in `prompts/strategies/` propagate instantly to the UI with toast notifications — no manual refresh needed.

**Architecture:** Backend `watchfiles.awatch()` background task detects filesystem changes, publishes events to the existing event bus, which streams via SSE to the frontend. A toast store + component provides visual feedback. Double-notification suppression prevents redundant toasts when the user saves via the inline editor.

**Tech Stack:** watchfiles (already installed), asyncio background task, event_bus pub/sub, SSE EventSource, Svelte 5 runes stores.

**Spec:** `docs/superpowers/specs/2026-03-16-realtime-strategy-watcher.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/app/services/file_watcher.py` | Create | watchfiles.awatch() background task, change classification, event publishing |
| `backend/app/main.py` | Modify | Start/stop watcher in lifespan |
| `frontend/src/lib/stores/toast.svelte.ts` | Create | Toast queue store with addToast() API |
| `frontend/src/lib/components/shared/Toast.svelte` | Create | Toast notification visual component |
| `frontend/src/lib/api/client.ts` | Modify | Add strategy_changed to SSE event types |
| `frontend/src/routes/+page.svelte` | Modify | Dispatch strategy-changed window event |
| `frontend/src/lib/components/layout/Navigator.svelte` | Modify | Listen for changes, suppression, re-fetch, show toasts |
| `frontend/src/lib/components/editor/PromptEdit.svelte` | Modify | Listen for changes, refresh dropdown, reset deleted strategy |
| `frontend/src/routes/+layout.svelte` | Modify | Mount Toast container |

---

## Chunk 1: Backend

### Task 1: File Watcher Service

**Files:**
- Create: `backend/app/services/file_watcher.py`

- [ ] **Step 1: Create the file watcher**

```python
# backend/app/services/file_watcher.py
"""Background file watcher for strategy template hot-reload.

Uses watchfiles.awatch() for OS-native filesystem events (inotify/FSEvents).
Publishes strategy_changed events to the event bus on file add/modify/delete.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from watchfiles import Change, awatch

logger = logging.getLogger(__name__)


async def watch_strategy_files(strategies_dir: Path) -> None:
    """Watch strategies directory and publish changes to event bus.

    Runs as a long-lived background task. Cancellation-safe.
    Falls back to polling if native watching fails.
    """
    from app.services.event_bus import event_bus

    if not strategies_dir.is_dir():
        logger.info(
            "Strategies directory %s does not exist — file watcher not started",
            strategies_dir,
        )
        return

    logger.info("Strategy file watcher started: %s", strategies_dir)

    _ACTION_MAP = {
        Change.added: "created",
        Change.modified: "modified",
        Change.deleted: "deleted",
    }

    force_polling = False

    while True:
        try:
            async for changes in awatch(
                strategies_dir,
                debounce=500,
                force_polling=force_polling,
                poll_delay_ms=2000 if force_polling else 1600,
            ):
                for change_type, path_str in changes:
                    path = Path(path_str)
                    if path.suffix != ".md":
                        continue

                    action = _ACTION_MAP.get(change_type)
                    if not action:
                        continue

                    name = path.stem
                    logger.info(
                        "Strategy file %s: %s", action, name,
                    )

                    event_bus.publish("strategy_changed", {
                        "action": action,
                        "name": name,
                        "timestamp": time.time(),
                    })

        except asyncio.CancelledError:
            logger.info("Strategy file watcher stopped")
            return
        except Exception as exc:
            if not force_polling:
                logger.warning(
                    "Native file watching failed (%s), falling back to polling",
                    exc,
                )
                force_polling = True
            else:
                logger.error("Strategy file watcher error: %s", exc)
                await asyncio.sleep(5)
```

- [ ] **Step 2: Run ruff**

Run: `cd backend && source .venv/bin/activate && ruff check app/services/file_watcher.py`
Expected: All checks passed

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/file_watcher.py
git commit -m "feat: add strategy file watcher using watchfiles.awatch()"
```

---

### Task 2: Lifespan Integration

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add watcher start/stop to lifespan**

In `main.py`, import and add to the lifespan function. After existing startup code (before `yield`):

```python
import asyncio
from app.services.file_watcher import watch_strategy_files
from app.config import PROMPTS_DIR

# Start strategy file watcher
watcher_task = asyncio.create_task(
    watch_strategy_files(PROMPTS_DIR / "strategies")
)
app.state.watcher_task = watcher_task
```

After `yield` (in shutdown):

```python
# Stop strategy file watcher
if hasattr(app.state, "watcher_task"):
    app.state.watcher_task.cancel()
    try:
        await app.state.watcher_task
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 2: Run ruff + tests**

Run: `cd backend && source .venv/bin/activate && ruff check app/main.py && pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: start/stop strategy file watcher in FastAPI lifespan"
```

---

## Chunk 2: Frontend Toast System

### Task 3: Toast Store

**Files:**
- Create: `frontend/src/lib/stores/toast.svelte.ts`

- [ ] **Step 1: Create the toast store**

```typescript
// frontend/src/lib/stores/toast.svelte.ts

export interface ToastItem {
  id: string;
  symbol: string;
  message: string;
  color: string;
}

const ACTION_CONFIG: Record<string, { symbol: string; verb: string; color: string }> = {
  created: { symbol: '+', verb: 'detected', color: 'var(--color-neon-green)' },
  modified: { symbol: '~', verb: 'updated', color: 'var(--color-neon-yellow)' },
  deleted: { symbol: '-', verb: 'removed', color: 'var(--color-neon-red)' },
};

let _counter = 0;

class ToastStore {
  toasts = $state<ToastItem[]>([]);

  add(action: string, name: string): void {
    const config = ACTION_CONFIG[action] ?? ACTION_CONFIG.modified;
    const id = `toast-${Date.now()}-${_counter++}`;
    const item: ToastItem = {
      id,
      symbol: config.symbol,
      message: `${name} ${config.verb}`,
      color: config.color,
    };

    // Max 3 visible — dismiss oldest if needed
    if (this.toasts.length >= 3) {
      this.toasts = this.toasts.slice(-2);
    }
    this.toasts = [...this.toasts, item];

    // Auto-dismiss after 3 seconds
    setTimeout(() => this.dismiss(id), 3000);
  }

  dismiss(id: string): void {
    this.toasts = this.toasts.filter(t => t.id !== id);
  }
}

export const toastStore = new ToastStore();
export const addToast = (action: string, name: string) => toastStore.add(action, name);
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/stores/toast.svelte.ts
git commit -m "feat: add toast store with addToast() API"
```

---

### Task 4: Toast Component

**Files:**
- Create: `frontend/src/lib/components/shared/Toast.svelte`
- Modify: `frontend/src/routes/+layout.svelte`

- [ ] **Step 1: Create the Toast component**

```svelte
<!-- frontend/src/lib/components/shared/Toast.svelte -->
<script lang="ts">
  import { toastStore } from '$lib/stores/toast.svelte';
</script>

{#if toastStore.toasts.length > 0}
  <div class="toast-container" aria-live="polite">
    {#each toastStore.toasts as toast (toast.id)}
      <div class="toast-item">
        <span class="toast-symbol" style="color: {toast.color};">{toast.symbol}</span>
        <span class="toast-message">{toast.message}</span>
      </div>
    {/each}
  </div>
{/if}

<style>
  .toast-container {
    position: fixed;
    bottom: 28px; /* above status bar (22px) + 6px gap */
    right: 8px;
    z-index: 50;
    display: flex;
    flex-direction: column;
    gap: 4px;
    pointer-events: none;
  }

  .toast-item {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 22px;
    padding: 0 8px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    animation: toast-in 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
    pointer-events: auto;
  }

  .toast-symbol {
    font-weight: 700;
    flex-shrink: 0;
  }

  .toast-message {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  @keyframes toast-in {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .toast-item {
      animation-duration: 0.01ms;
    }
  }
</style>
```

- [ ] **Step 2: Mount Toast in +layout.svelte**

Add import and component in `frontend/src/routes/+layout.svelte`:

```typescript
import Toast from '$lib/components/shared/Toast.svelte';
```

Add `<Toast />` inside the workbench div (after `<CommandPalette />`):

```svelte
<CommandPalette />
<Toast />
```

- [ ] **Step 3: Run svelte-check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/shared/Toast.svelte frontend/src/routes/+layout.svelte
git commit -m "feat: add brand-compliant toast notification component"
```

---

## Chunk 3: SSE Wiring + Frontend Reactivity

### Task 5: SSE Event Transport

**Files:**
- Modify: `frontend/src/lib/api/client.ts`
- Modify: `frontend/src/routes/+page.svelte`

- [ ] **Step 1: Add strategy_changed to SSE event types**

In `frontend/src/lib/api/client.ts`, find the `eventTypes` array in `connectEventStream()` and add `'strategy_changed'`:

```typescript
const eventTypes = [
  'optimization_created', 'feedback_submitted',
  'refinement_turn', 'optimization_failed',
  'strategy_changed',  // <-- add this
];
```

- [ ] **Step 2: Dispatch window event in +page.svelte**

In `frontend/src/routes/+page.svelte`, find the `connectEventStream` callback and add a handler for `strategy_changed`:

```typescript
if (type === 'strategy_changed') {
  window.dispatchEvent(new CustomEvent('strategy-changed', { detail: data }));
}
```

- [ ] **Step 3: Run svelte-check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api/client.ts frontend/src/routes/+page.svelte
git commit -m "feat: wire strategy_changed SSE event to frontend"
```

---

### Task 6: Navigator + PromptEdit Reactivity

**Files:**
- Modify: `frontend/src/lib/components/layout/Navigator.svelte`
- Modify: `frontend/src/lib/components/editor/PromptEdit.svelte`

- [ ] **Step 1: Add strategy-changed listener to Navigator**

In `Navigator.svelte` `<script>`, add imports and listener:

```typescript
import { addToast } from '$lib/stores/toast.svelte';
```

Add a suppression set and listener effect:

```typescript
let suppressedNames = $state<Set<string>>(new Set());

$effect(() => {
  const handler = (e: Event) => {
    const detail = (e as CustomEvent).detail;
    if (!detail?.name) return;

    // Suppress toasts for names we just saved via UI
    if (suppressedNames.has(detail.name)) return;

    addToast(detail.action, detail.name);

    // Re-fetch strategies list
    strategiesLoaded = false;
    getStrategies()
      .then((list: any[]) => { strategiesList = list; })
      .catch(() => {});
  };
  window.addEventListener('strategy-changed', handler);
  return () => window.removeEventListener('strategy-changed', handler);
});
```

Update `saveStrategyEdit()` to add suppression:

```typescript
async function saveStrategyEdit() {
  if (!editingStrategy || !editDirty) return;
  editSaving = true;
  try {
    await updateStrategy(editingStrategy, editContent);
    editDirty = false;

    // Suppress watcher toast for this name (avoid double notification)
    suppressedNames.add(editingStrategy);
    setTimeout(() => suppressedNames.delete(editingStrategy!), 2000);

    // Refresh descriptions
    const list = await getStrategies();
    strategiesList = list;
  } catch { /* save failed */ }
  editSaving = false;
}
```

- [ ] **Step 2: Add strategy-changed listener to PromptEdit**

In `PromptEdit.svelte` `<script>`, add a listener that refreshes the dropdown and resets deleted strategies:

```typescript
import { addToast } from '$lib/stores/toast.svelte';

$effect(() => {
  const handler = () => {
    getStrategies().then((list: StrategyInfo[]) => {
      const auto = list.find(s => s.name === 'auto');
      const rest = list.filter(s => s.name !== 'auto');
      strategyOptions = [
        { value: '', label: auto ? 'auto' : 'auto' },
        ...rest.map(s => ({ value: s.name, label: s.name })),
      ];

      // Reset if active strategy was deleted
      if (forgeStore.strategy && !list.some(s => s.name === forgeStore.strategy)) {
        forgeStore.strategy = null;
      }
    }).catch(() => {});
  };
  window.addEventListener('strategy-changed', handler);
  return () => window.removeEventListener('strategy-changed', handler);
});
```

- [ ] **Step 3: Run svelte-check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/layout/Navigator.svelte frontend/src/lib/components/editor/PromptEdit.svelte
git commit -m "feat: real-time strategy reactivity with toast notifications and suppression"
```

---

## Chunk 4: Verification

### Task 7: Manual E2E Test + CLAUDE.md

- [ ] **Step 1: Restart services**

```bash
./init.sh restart
```

- [ ] **Step 2: Test file creation**

```bash
echo -e "---\ntagline: test\ndescription: Test strategy.\n---\n# Test\nA test strategy." > prompts/strategies/test-strategy.md
```

Expected: Toast `+ test-strategy detected` appears in UI. Sidebar and dropdown update.

- [ ] **Step 3: Test file modification**

```bash
echo -e "---\ntagline: test-v2\ndescription: Updated test.\n---\n# Test v2\nUpdated." > prompts/strategies/test-strategy.md
```

Expected: Toast `~ test-strategy updated` appears.

- [ ] **Step 4: Test file deletion**

```bash
rm prompts/strategies/test-strategy.md
```

Expected: Toast `- test-strategy removed` appears. Entry gone from sidebar + dropdown.

- [ ] **Step 5: Update CLAUDE.md**

Add to Key services section:
```
- `file_watcher.py` — background watchfiles.awatch() task for strategy file hot-reload with event bus integration
```

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document file watcher service in CLAUDE.md"
```
